"""Skylit REST client: Heatseeker history, Flowseeker dark pools, Tempest vol history (SPEC §3.1).

Built against docs/skylit/openapi.json, docs/skylit/flowseeker-openapi.yaml and
docs/skylit/api-streams-and-limits.md. Auth from SKYLIT_API_KEY (.env). Every call is paced by the
key's per-minute limit, retried only when the API says it is safe (failed calls are refunded), and
its X-Credits-Remaining is tracked so each call's charge is known. The live stream is in
skylit_stream.py; storage and the never-re-pull cache in skylit_store.py; the credit gate in
skylit_credits.py.

Map names (QQQ, NDXP, SPY, SPX) are the bot's names everywhere; `skylit_symbols` (settings.yaml)
maps them to the API's option roots (SPX -> SPXW, the 0DTE root).
"""

from __future__ import annotations

import logging
import math
import random
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterator

import httpx
import numpy as np

from nqbot.secrets import Secret

BASE_URL = "https://api.skylit.ai"  # serves Heatseeker, Flowseeker and Tempest with one key
DEFAULT_RPM = 120  # §3.1; replaced by /v1/account limits.requestsPerMinute when read
MAX_ATTEMPTS = 5  # Skylit docs, rate-limits-and-retries: give up after 5 attempts
BACKOFF_BASE_SEC = 1.0  # full jitter: random(0, min(cap, base * 2**attempt))
BACKOFF_CAP_SEC = 30.0
RESET_JITTER_SEC = 1.0  # added to a wait until X-RateLimit-Reset
RATE_LOW_REMAINING = 1  # sleep until X-RateLimit-Reset when this few requests are left in the window
RETRYABLE = frozenset({429, 500, 502, 503, 504})
TIMEOUT_SEC = 90.0  # client timeout; the server answers 504 after 20-25 s, a 15-min 1-s range body is large
DP_PAGE_LIMIT = 5000  # /v1/dark-pool/trades server cap per page
DP_RANGE_MAX_DAYS = 31  # Flowseeker: date_start..date_end spans at most 31 days per request (§3.1 Endpoints)
DP_MAX_PAGES = 200  # §3.1 Dark pools: fail loudly, never truncate silently (one ET date of $1M+ QQQ/SPY prints is ~1 page)

# presd_fixes D7: a connection dropped after the request was sent may already be charged (25 credits for a range
# call), so a large call is retried only this many times after such a drop; a connect-phase failure (never sent)
# keeps the normal retries.
LARGE_CALL_DROP_RETRIES = 1
NOT_SENT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)

log_default = logging.getLogger("nqbot.skylit")


class SkylitError(Exception):
    def __init__(self, status: int | None, code: str, message: str, raw: bytes | None = None, charged: bool = False):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message
        self.raw = raw  # the answer's body (a charged 200 that couldn't be decoded, or a refusal's error body)
        self.charged = charged  # True: the call was paid for (never re-pulled, rule 2); False: refunded


class OutOfCredits(SkylitError):
    """402: stop and tell the owner (never retried)."""


class AccessDenied(SkylitError):
    """401/403: bad key, suspended account or no access to this data (never retried)."""


@dataclass
class CallRecord:
    """One finished HTTP call (successful or not), for the ledger."""

    endpoint: str
    params: dict[str, Any]
    status: int | None
    attempts: int
    bytes: int
    credits_remaining: int | None
    charged: int | None  # previous known balance - this balance (None if unknown)
    at: str  # UTC ISO time the response arrived
    error: str | None = None
    context: dict[str, Any] = field(default_factory=dict)  # what the call was for (window, date, re-pull reason)


class CreditTracker:
    """Follows X-Credits-Remaining (and the stream's credits events); warns below skylit_credit_warn."""

    def __init__(self, warn_below: int | None, log: logging.Logger = log_default):
        self.remaining: int | None = None
        self.warn_below = warn_below
        self.log = log

    def update(self, remaining: int | None) -> int | None:
        if remaining is None:
            return None
        charged = self.remaining - remaining if self.remaining is not None else None
        if charged is not None and charged < 0:  # monthly reset or a top-up between calls: charge unknown
            self.log.info("Skylit balance rose from %d to %d (monthly reset or top-up)", self.remaining, remaining)
            charged = None
        self.remaining = remaining
        if self.warn_below is not None and remaining < self.warn_below:
            self.log.warning("Skylit credits low: %d left (warning below %d)", remaining, self.warn_below)
        return charged


class RateLimiter:
    """Token bucket at the key's requests per minute, plus the server's own X-RateLimit-* headers."""

    def __init__(self, rpm: int, *, mono: Callable[[], float], wall: Callable[[], float],
                 sleep: Callable[[float], None]):
        self.mono, self.wall, self.sleep = mono, wall, sleep
        self.set_rpm(rpm)

    def set_rpm(self, rpm: int) -> None:
        self.rpm = max(1, int(rpm))
        self.rate = self.rpm / 60.0
        self.tokens = float(self.rpm)
        self.last = self.mono()

    def _refill(self) -> None:
        now = self.mono()
        self.tokens = min(float(self.rpm), self.tokens + (now - self.last) * self.rate)
        self.last = now

    def acquire(self) -> None:
        self._refill()
        if self.tokens < 1:
            self.sleep((1 - self.tokens) / self.rate)
            self._refill()
        self.tokens -= 1

    def observe(self, headers: httpx.Headers) -> None:
        remaining, reset = _int(headers.get("X-RateLimit-Remaining")), _int(headers.get("X-RateLimit-Reset"))
        if remaining is not None and remaining <= RATE_LOW_REMAINING and reset is not None:
            wait = reset - self.wall()
            if wait > 0:
                self.sleep(wait + RESET_JITTER_SEC)


def _int(value: str | None) -> int | None:
    try:
        return int(float(value)) if value is not None else None
    except ValueError:
        return None


def iso_utc(t: datetime) -> str:
    if t.tzinfo is None:
        raise ValueError("timestamps sent to Skylit must be timezone-aware")
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@dataclass
class DarkPoolPull:
    prints: list[dict[str, Any]]
    pages: int
    records: list[CallRecord] = field(default_factory=list)
    bodies: list[dict[str, Any]] = field(default_factory=list)  # every page as answered (kept if storing fails)
    span: dict[str, str] = field(default_factory=dict)  # {"date": ...} or {"date_start": ..., "date_end": ...}


class DarkPoolFailed(SkylitError):
    """Paid dark-pool pages that couldn't be used (presd_fixes D1). `bodies`: every page answered, kept in
    skylit/failed/ by the caller (never paid for twice, rule 2). `complete`: the walk reached its last page and the
    pages are in order, so the kept copy can be converted again for free; False: the answer was cut off, out of
    order or unreadable, and the date may get one paid re-pull while in reach (owner 2026-10-06)."""

    def __init__(self, code: str, message: str, bodies: list[dict[str, Any]], complete: bool, span: dict[str, str],
                 status: int | None = 200, unreadable: bytes | None = None):
        super().__init__(status, code, message, raw=unreadable, charged=True)
        self.bodies, self.complete, self.span = bodies, complete, span
        self.raw_path: Any = None  # set once the caller has kept the pages
        self.group: list[date] = []  # step 10: the other dates of a failed 31-day call


def dark_pool_prints(bodies: list[dict[str, Any]], label: str, span: dict[str, str]) -> list[dict[str, Any]]:
    """The prints of a whole walk, checked (the same check for a fresh answer and for a kept copy converted again).
    Out-of-order pages fail loudly (a silently wrong day would change the top-3 list, §3.1 Dark pools)."""
    prints: list[dict[str, Any]] = []
    last: int | None = None
    for page, body in enumerate(bodies, 1):
        rows = body.get("data") or []
        try:
            times = [_ts_ns(r["timestamp"]) for r in rows]
        except Exception as e:  # noqa: BLE001 - any malformed print: the whole answer is kept, converted again later
            raise DarkPoolFailed("convert_failed", f"dark-pool page {page} for {label}: a print couldn't be read "
                                 f"({type(e).__name__}: {e})", bodies, True, span) from e
        if last is not None and rows and times[0] < last:
            raise DarkPoolFailed("page_order", f"dark-pool page {page} for {label} starts before the last page ended",
                                 bodies, False, span)
        if any(a > b for a, b in zip(times, times[1:])):
            raise DarkPoolFailed("page_order", f"dark-pool page {page} for {label} is not in time order", bodies,
                                 False, span)
        last = times[-1] if times else last
        prints.extend(rows)
    return prints


class SkylitClient:
    def __init__(
        self,
        api_key: Secret,
        *,
        symbols_map: dict[str, str],
        metric: str = "gamma",
        max_expirations: int = 1,
        max_strikes: int | str = 200,
        credit_warn: int | None = None,
        base_url: str = BASE_URL,
        transport: httpx.BaseTransport | None = None,
        log: logging.Logger = log_default,
        mono: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] = random.random,
        on_call: Callable[[CallRecord], None] | None = None,
    ):
        self.symbols_map = dict(symbols_map)
        self.root_to_map = {v: k for k, v in self.symbols_map.items()}
        self.metric, self.max_expirations, self.max_strikes = metric, max_expirations, max_strikes
        self.log, self.wall, self.sleep, self.rng = log, wall, sleep, rng
        self.credits = CreditTracker(credit_warn, log)
        self.limiter = RateLimiter(DEFAULT_RPM, mono=mono, wall=wall, sleep=sleep)
        self.on_call = on_call  # every finished call (paid or failed) -> the ledger
        self.context: dict[str, Any] = {}
        self._http = httpx.Client(
            base_url=base_url,
            transport=transport,
            timeout=TIMEOUT_SEC,
            headers={"Authorization": f"Bearer {api_key.reveal()}", "Accept-Encoding": "gzip"},
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> SkylitClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @classmethod
    def from_settings(cls, settings: dict[str, Any], api_key: Secret, **kw: Any) -> SkylitClient:
        return cls(
            api_key,
            symbols_map=settings["skylit_symbols"],
            metric=settings["skylit_metric"],
            max_expirations=settings["skylit_max_expirations"],
            max_strikes=settings["skylit_max_strikes"],
            credit_warn=settings.get("skylit_credit_warn"),
            **kw,
        )

    # --- transport ---------------------------------------------------------------------------------

    def _backoff(self, attempt: int, response: httpx.Response | None) -> float:
        wait = self.rng() * min(BACKOFF_CAP_SEC, BACKOFF_BASE_SEC * 2**attempt)
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            reset = _int(response.headers.get("X-RateLimit-Reset"))
            if retry_after is not None:
                wait = max(wait, float(retry_after))
            elif response.status_code == 429 and reset is not None:
                wait = max(wait, reset - self.wall() + self.rng() * RESET_JITTER_SEC)
        return max(wait, 0.0)

    def get(self, path: str, params: dict[str, Any] | None = None,
            drop_retries: int | None = None) -> tuple[Any, CallRecord]:
        """GET with pacing, retries and credit tracking. Returns (parsed JSON, call record).

        drop_retries: how often a connection dropped after the request was sent (possibly charged) is retried
        before giving up with code `dropped` (None: like any network error, up to MAX_ATTEMPTS)."""
        params = {k: v for k, v in (params or {}).items() if v is not None}
        response: httpx.Response | None = None
        error = ""
        drops = 0
        for attempt in range(MAX_ATTEMPTS):
            self.limiter.acquire()
            try:
                response = self._http.get(path, params=params)
            except httpx.TransportError as e:  # network error or timeout: retried (GET; a failed call is refunded)
                response, error = None, f"network: {type(e).__name__}"
                self.log.warning("Skylit %s: %s (attempt %d/%d)", path, error, attempt + 1, MAX_ATTEMPTS)
                if drop_retries is not None and not isinstance(e, NOT_SENT_ERRORS):
                    drops += 1
                    if drops > drop_retries:
                        raise SkylitError(None, "dropped", f"{path}: connection dropped after the request was sent "
                                          f"({error}), {drops} times: not retried again (it may have been "
                                          "charged)") from None
            if response is not None:
                self.limiter.observe(response.headers)
                charged = self.credits.update(_int(response.headers.get("X-Credits-Remaining")))
                if response.status_code < 400:
                    try:
                        body = response.json()
                    except ValueError:
                        # charged already: never retried (that would pay again); the caller saves the raw body
                        error = "undecodable 200 body (charged)"
                        self.log.error("Skylit %s: %s", path, error)
                        self._record(path, params, response, attempt + 1, charged, error)
                        raise SkylitError(200, "undecodable", f"{path}: {error}", raw=response.content,
                                          charged=True) from None
                    else:
                        return body, self._record(path, params, response, attempt + 1, charged, None)
                else:
                    code, message = _error_of(response)
                    error = f"{response.status_code} {code}: {message}"
                    self._record(path, params, response, attempt + 1, charged, error)
                    if response.status_code not in RETRYABLE:
                        exc_type = (OutOfCredits if response.status_code == 402
                                    else AccessDenied if response.status_code in (401, 403) else SkylitError)
                        raise exc_type(response.status_code, code, message, raw=response.content)
                    self.log.warning("Skylit %s: %s (attempt %d/%d)", path, error, attempt + 1, MAX_ATTEMPTS)
            if attempt < MAX_ATTEMPTS - 1:
                self.sleep(self._backoff(attempt, response))
        status = response.status_code if response is not None else None
        raise SkylitError(status, "gave_up", f"{path} failed after {MAX_ATTEMPTS} attempts ({error})")

    def _record(self, path: str, params: dict[str, Any], response: httpx.Response, attempts: int,
                charged: int | None, error: str | None) -> CallRecord:
        rec = CallRecord(
            endpoint=path,
            params=dict(params),
            status=response.status_code,
            attempts=attempts,
            bytes=len(response.content),
            credits_remaining=self.credits.remaining,
            charged=charged,
            at=datetime.fromtimestamp(self.wall(), timezone.utc).isoformat(),
            error=error,
            context=dict(self.context),
        )
        if self.on_call is not None:
            self.on_call(rec)
        return rec

    @contextmanager
    def tagged(self, **context: Any) -> Iterator[None]:
        """Calls made inside carry this context into their ledger lines (window, date, re-pull reason)."""
        saved = self.context
        self.context = {**saved, **{k: v for k, v in context.items() if v is not None}}
        try:
            yield
        finally:
            self.context = saved

    # --- free endpoints ----------------------------------------------------------------------------

    def account(self) -> dict[str, Any]:
        """Balance and limits (free). Also sets the rate limit and the credit balance."""
        body, _ = self.get("/v1/account")
        data = body.get("data", {})
        rpm = (data.get("limits") or {}).get("requestsPerMinute")
        if rpm:
            self.limiter.set_rpm(rpm)
        if data.get("creditsBalance") is not None:
            self.credits.update(int(data["creditsBalance"]))
        return data

    def symbols(self) -> list[dict[str, Any]]:
        body, _ = self.get("/v1/symbols")
        return body["data"]["symbols"]

    def vol_symbols(self) -> dict[str, Any]:
        body, _ = self.get("/v1/vol/symbols")
        return body

    def vol_status(self) -> dict[str, Any]:
        body, _ = self.get("/v1/vol/status")
        return body

    # --- Heatseeker --------------------------------------------------------------------------------

    def roots(self, maps: list[str]) -> str:
        return ",".join(self.symbols_map[m] for m in maps)

    def map_name(self, api_symbol: str) -> str:
        try:
            return self.root_to_map[api_symbol]
        except KeyError:
            raise SkylitError(None, "unexpected_symbol", f"response symbol {api_symbol!r} not requested") from None

    def board_params(self, max_strikes: int | str | None = None) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "maxExpirations": self.max_expirations,
            "maxStrikes": max_strikes if max_strikes is not None else self.max_strikes,
        }

    def historical_range(self, maps: list[str], start: datetime, end: datetime,
                         max_strikes: int | str | None = None,
                         drop_retries: int = LARGE_CALL_DROP_RETRIES) -> tuple[dict[str, Any], CallRecord]:
        """Every snapshot in [start, end] (both inclusive, <= 15 min, <= 5 symbols). 25 credits."""
        params = {"symbols": self.roots(maps), "from": iso_utc(start), "to": iso_utc(end), **self.board_params(max_strikes)}
        return self.get("/v1/historical/range", params, drop_retries=drop_retries)

    def historical(self, maps: list[str], at: datetime,
                   max_strikes: int | str | None = None) -> tuple[dict[str, Any], CallRecord]:
        """The latest snapshot at or before `at` (carries Skylit's nodeType). 5 credits."""
        params = {"symbols": self.roots(maps), "at": iso_utc(at), **self.board_params(max_strikes)}
        return self.get("/v1/historical", params)

    # --- Flowseeker dark pools ---------------------------------------------------------------------

    def dark_pool_trades(self, day: date, tickers: list[str], min_notional: float,
                         before_page: Callable[[], None] | None = None, end: date | None = None) -> DarkPoolPull:
        """Every print of one ET trade date (or of the trade dates day..end, at most DP_RANGE_MAX_DAYS), oldest
        first, walked by meta.nextCursor. 5 credits a page.

        A short page (count < limit) ends the pull. Out-of-order pages or a runaway page count fail loudly:
        a silently truncated day would change the top-3 list (§3.1 Dark pools). `before_page` runs before every
        page's call (the step-10 credit budget; it may raise to stop). Once a page has been paid for, any failure
        raises DarkPoolFailed carrying every page answered, so the caller keeps them (presd_fixes D1).
        """
        span = {"date": day.isoformat()}
        label = str(day)
        if end is not None and end != day:
            if not day < end <= day + timedelta(days=DP_RANGE_MAX_DAYS - 1):
                raise ValueError(f"dark-pool range {day}..{end}: at most {DP_RANGE_MAX_DAYS} days, oldest first")
            span, label = {"date_start": day.isoformat(), "date_end": end.isoformat()}, f"{day}..{end}"
        base = {"tickers": ",".join(tickers), **span, "min_notional": min_notional,
                "limit": DP_PAGE_LIMIT, "order": "asc", "sort": "time"}
        bodies: list[dict[str, Any]] = []
        records: list[CallRecord] = []
        cursor: str | None = None
        last: int | None = None  # the last print time seen
        readable = True
        try:
            for page in range(1, DP_MAX_PAGES + 1):
                if before_page is not None:
                    before_page()
                body, rec = self.get("/v1/dark-pool/trades", {**base, "cursor": cursor})
                records.append(rec)
                bodies.append(body)
                try:
                    rows, meta = body.get("data") or [], body.get("meta") or {}
                    limit = int(meta.get("limit", DP_PAGE_LIMIT))
                except (AttributeError, TypeError, ValueError) as e:  # a page of another shape: what was paid is kept
                    raise DarkPoolFailed("cut_off", f"dark-pool page {page} for {label} can't be read "
                                         f"({type(e).__name__}: {e})", bodies, False, span) from e
                # out of order: stop at once (the answer can't be trusted); a print that can't be read is a
                # conversion matter: the walk goes on and dark_pool_prints checks the whole answer at the end
                try:
                    times = [_ts_ns(r["timestamp"]) for r in rows] if readable else []
                except Exception:  # noqa: BLE001
                    readable, times = False, []
                if (last is not None and times and times[0] < last) or any(a > b for a, b in zip(times, times[1:])):
                    dark_pool_prints(bodies, label, span)  # raises page_order with the page named
                last = times[-1] if times else last
                if len(rows) < limit:
                    break
                cursor = meta.get("nextCursor")
                if not cursor:
                    raise DarkPoolFailed("no_cursor", f"dark-pool page {page} for {label} is full but has no "
                                         "nextCursor", bodies, False, span)
            else:
                raise DarkPoolFailed("page_cap", f"dark-pool pull for {label} reached {DP_MAX_PAGES} pages", bodies,
                                     False, span)
        except (DarkPoolFailed, OutOfCredits, AccessDenied):
            raise
        except SkylitError as e:  # a later page failed (or an answer was unreadable): the paid pages are kept
            if not bodies and e.code != "undecodable":
                raise
            raise DarkPoolFailed("cut_off", f"dark-pool pull for {label} cut off at page {len(bodies) + 1} ({e})",
                                 bodies, False, span, status=e.status, unreadable=e.raw if e.charged else None) from e
        return DarkPoolPull(dark_pool_prints(bodies, label, span), len(bodies), records, bodies, span)

    def top_prints(self, ticker: str, as_of: date, top_n: int, lookback_days: int) -> tuple[list[dict[str, Any]], CallRecord]:
        """Skylit's own top-N over [as_of - lookback_days, as_of] (cross-check only). 3 credits."""
        body, rec = self.get(f"/v1/dark-pool/top-prints/{ticker}",
                             {"top_n": top_n, "lookback_days": lookback_days, "as_of_date": as_of.isoformat()})
        return body.get("data") or [], rec

    # --- Tempest -----------------------------------------------------------------------------------

    def vol_history(self, symbols: list[str], start: date, end: date) -> tuple[dict[str, Any], CallRecord]:
        """Daily rows at each session's 15:59 ET close. ceil(symbol-weekdays / 10) credits, min 1."""
        return self.get("/v1/vol/history", {"symbols": ",".join(symbols), "from": start.isoformat(),
                                            "to": end.isoformat()})


def _ts_ns(ts: str) -> int:
    """RFC3339 -> ns since the epoch (exact whatever the sub-second precision)."""
    return int(np.datetime64(ts.replace("Z", "").replace("+00:00", ""), "ns").astype(np.int64))


def _error_of(response: httpx.Response) -> tuple[str, str]:
    try:
        err = response.json().get("error")
    except ValueError:
        return "unknown", response.text[:200]
    if isinstance(err, dict):
        return str(err.get("code", "unknown")), str(err.get("message", ""))
    return "unknown", str(err)[:200]


def vol_history_cost(n_symbols: int, start: date, end: date) -> int:
    """Credits for /v1/vol/history: 1 per 10 symbol-weekdays, rounded up, minimum 1 (holidays not discounted)."""
    weekdays = sum(1 for i in range((end - start).days + 1) if (start + timedelta(days=i)).weekday() < 5)
    return max(1, math.ceil(n_symbols * weekdays / 10))
