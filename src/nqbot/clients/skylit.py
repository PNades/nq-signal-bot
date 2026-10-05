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
DP_MAX_PAGES = 200  # §3.1 Dark pools: fail loudly, never truncate silently (one ET date of $1M+ QQQ/SPY prints is ~1 page)

log_default = logging.getLogger("nqbot.skylit")


class SkylitError(Exception):
    def __init__(self, status: int | None, code: str, message: str, raw: bytes | None = None):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message
        self.raw = raw  # the body of a charged 200 that couldn't be decoded (saved by the caller)


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

    def get(self, path: str, params: dict[str, Any] | None = None) -> tuple[Any, CallRecord]:
        """GET with pacing, retries and credit tracking. Returns (parsed JSON, call record)."""
        params = {k: v for k, v in (params or {}).items() if v is not None}
        response: httpx.Response | None = None
        error = ""
        for attempt in range(MAX_ATTEMPTS):
            self.limiter.acquire()
            try:
                response = self._http.get(path, params=params)
            except httpx.TransportError as e:  # network error or timeout: safe to retry (GET, refunded)
                response, error = None, f"network: {type(e).__name__}"
                self.log.warning("Skylit %s: %s (attempt %d/%d)", path, error, attempt + 1, MAX_ATTEMPTS)
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
                        raise SkylitError(200, "undecodable", f"{path}: {error}", raw=response.content) from None
                    else:
                        return body, self._record(path, params, response, attempt + 1, charged, None)
                else:
                    code, message = _error_of(response)
                    error = f"{response.status_code} {code}: {message}"
                    self._record(path, params, response, attempt + 1, charged, error)
                    if response.status_code not in RETRYABLE:
                        exc_type = (OutOfCredits if response.status_code == 402
                                    else AccessDenied if response.status_code in (401, 403) else SkylitError)
                        raise exc_type(response.status_code, code, message)
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
                         max_strikes: int | str | None = None) -> tuple[dict[str, Any], CallRecord]:
        """Every snapshot in [start, end] (both inclusive, <= 15 min, <= 5 symbols). 25 credits."""
        params = {"symbols": self.roots(maps), "from": iso_utc(start), "to": iso_utc(end), **self.board_params(max_strikes)}
        return self.get("/v1/historical/range", params)

    def historical(self, maps: list[str], at: datetime,
                   max_strikes: int | str | None = None) -> tuple[dict[str, Any], CallRecord]:
        """The latest snapshot at or before `at` (carries Skylit's nodeType). 5 credits."""
        params = {"symbols": self.roots(maps), "at": iso_utc(at), **self.board_params(max_strikes)}
        return self.get("/v1/historical", params)

    # --- Flowseeker dark pools ---------------------------------------------------------------------

    def dark_pool_trades(self, day: date, tickers: list[str], min_notional: float) -> DarkPoolPull:
        """Every print of one ET trade date, oldest first, walked by meta.nextCursor. 5 credits a page.

        A short page (count < limit) ends the date. Out-of-order pages or a runaway page count fail loudly:
        a silently truncated day would change the top-3 list (§3.1 Dark pools).
        """
        base = {"tickers": ",".join(tickers), "date": day.isoformat(), "min_notional": min_notional,
                "limit": DP_PAGE_LIMIT, "order": "asc", "sort": "time"}
        prints: list[dict[str, Any]] = []
        records: list[CallRecord] = []
        cursor: str | None = None
        for page in range(1, DP_MAX_PAGES + 1):
            body, rec = self.get("/v1/dark-pool/trades", {**base, "cursor": cursor})
            records.append(rec)
            rows, meta = body.get("data") or [], body.get("meta") or {}
            times = [_ts_ns(r["timestamp"]) for r in rows]
            if prints and rows and times[0] < _ts_ns(prints[-1]["timestamp"]):
                raise SkylitError(None, "page_order", f"dark-pool page {page} for {day} starts before the last page ended")
            if any(a > b for a, b in zip(times, times[1:])):
                raise SkylitError(None, "page_order", f"dark-pool page {page} for {day} is not in time order")
            prints.extend(rows)
            limit = int(meta.get("limit", DP_PAGE_LIMIT))
            if len(rows) < limit:
                return DarkPoolPull(prints, page, records)
            cursor = meta.get("nextCursor")
            if not cursor:
                raise SkylitError(None, "no_cursor", f"dark-pool page {page} for {day} is full but has no nextCursor")
        raise SkylitError(None, "page_cap", f"dark-pool pull for {day} reached {DP_MAX_PAGES} pages")

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
