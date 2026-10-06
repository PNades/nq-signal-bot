"""Pull Skylit RTH history session by session into the cache (SPEC §1.2, §3.1 Backfill, §12 steps 7 and 10).

A session is pulled as 15-minute /v1/historical/range windows (all four maps in one call, gamma,
maxExpirations=1, maxStrikes=skylit_max_strikes): 26 windows on a normal day (09:30-16:00 ET, `from` and
`to` both inclusive, so each window ends 1 ms before the next starts and the last one ends at 16:00:00).
Early-close days are pulled to early_close_et plus one probe window after it; on a calendar holiday
(a weekday) one probe window is pulled, and the rest of the day only if the probe returned boards.
Every window is stored the moment it arrives, so an interrupted session loses nothing and is never
re-pulled.

Skylit's 365-day reach is a moment in time, not a date (`from`: "Up to 365 days back", docs/skylit/openapi.json):
the oldest day's morning windows leave it while its afternoon is still there. The step-10 backfill pulls a window
only if its start is still after now - 365 days + skylit_reach_margin_min, checked just before the call; one that
isn't is "lost": logged and reported, never cached as empty and never an error.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from nqbot import calendar as cal_mod
from nqbot.clients import skylit_credits as credits
from nqbot.clients.skylit import AccessDenied, OutOfCredits, SkylitClient, SkylitError, iso_utc
from nqbot.clients.skylit_store import SkylitStore, range_to_tables
from nqbot.jobs.lock import SKYLIT
from nqbot.jobs.runner import Ctx, StopChunk
from nqbot.secrets import get_secret
from nqbot.storage import Paths, atomic_write_json

ET = ZoneInfo("America/New_York")
RTH_OPEN = time(9, 30)  # §1.2: Skylit history is pulled for RTH only
RTH_CLOSE = time(16, 0)
WINDOW = timedelta(minutes=15)  # /v1/historical/range: at most 15 minutes per call
INCLUSIVE_END = timedelta(milliseconds=1)  # `to` is inclusive: end 1 ms before the next window starts
MAPS = ("QQQ", "NDXP", "SPY", "SPX")  # §3.1 Symbols (decided); one call takes up to 5
NO_DATA_CODES = frozenset({"no_data", "NOT_FOUND", "not_found"})
PROBE_EMPTY_CODES = frozenset({"symbol_not_found"})  # a probe on a closed day may find none of the symbols
SKYLIT_HISTORY = timedelta(days=365)  # §3.1: /v1/historical/range reaches 365 days back
# §3.1 Backfill: a 400 (or a 404 that isn't no-data) for a window this close to the 365-day line is read as
# "out of reach" (lost),
# in case the API's own cut-off differs from ours by a timezone or a day boundary (presd_fixes issue 7). Further
# inside the reach the same answer is a real error and raises.
REACH_ANSWER_TOLERANCE = timedelta(days=1)
OUT_OF_REACH_STATUSES = frozenset({400, 404})


class WindowFailed(SkylitError):
    """A window whose paid answer couldn't be converted or stored. The body is kept in skylit/failed/ (SPEC §11:
    never paid for twice); `raw_path` says where, so it can be converted again later for free (presd_fixes D1)."""

    def __init__(self, code: str, message: str, raw_path: Path | None):
        super().__init__(200, code, message, charged=True)
        self.raw_path = raw_path


# Errors a window's caller never skips (they concern every window, not this one): stop instead.
NEVER_SKIPPED = (StopChunk, OutOfCredits, AccessDenied)


@dataclass(frozen=True)
class Window:
    day: date
    start: datetime  # ET, inclusive
    end: datetime  # ET, inclusive
    probe: bool = False

    @property
    def hhmm(self) -> str:
        return self.start.strftime("%H%M")


def _windows(day: date, open_t: time, close_t: time) -> list[Window]:
    out = []
    start = datetime.combine(day, open_t, tzinfo=ET)
    close = datetime.combine(day, close_t, tzinfo=ET)
    while start < close:
        nxt = min(start + WINDOW, close)
        end = nxt if nxt == close else nxt - INCLUSIVE_END
        out.append(Window(day, start, end))
        start = nxt
    return out


def session_windows(day: date, cal: dict[str, Any] | None = None) -> list[Window]:
    """The windows to pull for one ET date (empty on weekends). Probes are marked."""
    if day.weekday() >= 5:
        return []
    if not cal_mod.is_trading_day(day, cal):  # a weekday holiday: one probe window
        w = _windows(day, RTH_OPEN, RTH_CLOSE)[0]
        return [Window(day, w.start, w.end, probe=True)]
    early = cal_mod.early_close_et(day, cal)
    if early is None:
        return _windows(day, RTH_OPEN, RTH_CLOSE)
    windows = _windows(day, RTH_OPEN, early)
    probe_start = datetime.combine(day, early, tzinfo=ET) + INCLUSIVE_END
    return windows + [Window(day, probe_start, probe_start + WINDOW - INCLUSIVE_END, probe=True)]


def full_day_windows(day: date) -> list[Window]:
    return _windows(day, RTH_OPEN, RTH_CLOSE)


def reach(now: datetime, margin_min: float) -> datetime:
    """The oldest instant a window may start at and still be pulled now (§3.1, presd_fixes issue 7)."""
    return now.astimezone(timezone.utc) - SKYLIT_HISTORY + timedelta(minutes=margin_min)  # UTC: DST-safe


def window_reachable(w: Window, now: datetime, margin_min: float) -> bool:
    return w.start >= reach(now, margin_min)


def day_reachable(day: date, now: datetime, margin_min: float, cal: dict[str, Any] | None = None) -> bool:
    """True if at least the day's last window can still be pulled (weekends: False)."""
    windows = session_windows(day, cal)
    return bool(windows) and window_reachable(windows[-1], now, margin_min)


def near_reach_edge(w: Window, now: datetime) -> bool:
    return w.start < now.astimezone(timezone.utc) - SKYLIT_HISTORY + REACH_ANSWER_TOLERANCE


@dataclass
class SessionResult:
    day: date
    pulled: list[str] = field(default_factory=list)
    cached: list[str] = field(default_factory=list)
    empty: list[str] = field(default_factory=list)
    lost: list[str] = field(default_factory=list)  # out of Skylit's 365-day reach: nothing called or cached
    failed: list[str] = field(default_factory=list)  # skipped by on_error (step-10 backfill: recorded, presd_fixes D1)
    charged: int = 0
    bytes: int = 0
    resolutions: dict[str, str] = field(default_factory=dict)  # hhmm -> meta.resolution

    def summary(self) -> str:
        res = sorted(set(self.resolutions.values()))
        lost = f", {len(self.lost)} LOST (beyond 365 days)" if self.lost else ""
        lost += f", {len(self.failed)} FAILED" if self.failed else ""
        return (f"{self.day}: {len(self.pulled)} pulled, {len(self.cached)} cached, {len(self.empty)} empty{lost}, "
                f"{self.charged} credits, {self.bytes / 1e6:.1f} MB, resolution {res or '-'}")


def range_params(client: SkylitClient, w: Window, max_strikes: int | str | None = None) -> dict[str, Any]:
    return {"symbols": client.roots(list(MAPS)), "from": iso_utc(w.start), "to": iso_utc(w.end),
            **client.board_params(max_strikes)}


def range_params_from_settings(settings: dict[str, Any], w: Window) -> dict[str, Any]:
    """The same parameters a client built from these settings sends (no client, no call: plans and estimates)."""
    return {"symbols": ",".join(settings["skylit_symbols"][m] for m in MAPS), "from": iso_utc(w.start),
            "to": iso_utc(w.end), "metric": settings["skylit_metric"],
            "maxExpirations": settings["skylit_max_expirations"], "maxStrikes": settings["skylit_max_strikes"]}


def pull_window(client: SkylitClient, store: SkylitStore, w: Window, *, max_strikes: int | str | None = None,
                result: SessionResult, log: logging.Logger, base: Path | None = None,
                repull: str | None = None, tree: str | None = None,
                gate: Callable[[Window], bool] | None = None, now: Callable[[], datetime] | None = None,
                drop_retries: int | None = None) -> bool:
    """Pull one window unless cached under `base` (default: the normal tree). Returns True if it holds boards
    (a cached window answers from its stored row count, so a stored empty probe stays empty).

    `gate(w)` runs after the cache check, just before the call: False = the window is out of reach (recorded as
    lost, nothing called or stored); it may raise StopChunk (e.g. the credit budget can't cover the call).
    With `now`, an out-of-reach answer for a window near the 365-day line is recorded as lost too."""
    params = range_params(client, w, max_strikes)
    if store.range_cached(w.day, w.hhmm, params, base):
        result.cached.append(w.hhmm)
        return store.range_n_boards(w.day, w.hhmm, base) > 0
    if gate is not None and not gate(w):
        result.lost.append(w.hhmm)
        log.warning("%s %s: beyond Skylit's 365-day reach, lost (not pulled, not cached)", w.day, w.hhmm)
        return False
    reason = repull
    if store.range_partial(w.day, w.hhmm, base):
        moved = store.quarantine_partial(w.day, w.hhmm, base)
        log.warning("%s %s: interrupted write (one of two files); orphan moved to %s, pulling the window again",
                    w.day, w.hhmm, moved)
        reason = reason or "partial_write"
    label = f"{w.day} {w.hhmm}"
    try:
        with client.tagged(window=label, repull=reason, tree=tree):
            body, rec = (client.historical_range(list(MAPS), w.start, w.end, max_strikes) if drop_retries is None
                         else client.historical_range(list(MAPS), w.start, w.end, max_strikes,
                                                      drop_retries=drop_retries))
    except SkylitError as e:
        if e.status == 404 and (e.code in NO_DATA_CODES or (w.probe and e.code in PROBE_EMPTY_CODES)):
            result.empty.append(w.hhmm)  # refunded: nothing stored, nothing charged
            return False
        if now is not None and e.status in OUT_OF_REACH_STATUSES and near_reach_edge(w, now()):
            result.lost.append(w.hhmm)  # refunded; never cached as empty
            log.warning("%s: answered %s %s at the 365-day line: lost (not cached)", label, e.status, e.code)
            return False
        if e.raw is not None:  # a charged undecodable 200, or a refusal's error body (kept for the diagnosis)
            path = store.save_failed(e.raw, f"range_{w.day}_{w.hhmm}" + ("" if e.charged else "_refused"))
            e.raw_path = path
            log.error("%s: %s answer saved to %s", label, "undecodable" if e.charged else "refused", path)
        raise
    return store_window(store, w, body, rec, params, client.root_to_map, result=result, base=base, reason=reason)


def store_window(store: SkylitStore, w: Window, body: dict[str, Any], rec: Any, params: dict[str, Any],
                 root_to_map: dict[str, str], *, result: SessionResult, base: Path | None = None,
                 reason: str | None = None, raw_path: Path | None = None) -> bool:
    """Convert and store one paid answer. Any failure keeps the body in failed/ first and raises WindowFailed
    (presd_fixes D1). `raw_path`: the body is already kept there (a free re-conversion of a failed window)."""
    label = f"{w.day} {w.hhmm}"

    def keep() -> Path | None:
        if raw_path is not None:
            return raw_path
        try:
            return store.save_failed(json.dumps(body).encode(), f"range_{w.day}_{w.hhmm}")
        except Exception:  # noqa: BLE001 - e.g. the SSD is gone: the original error is the one to report
            return None

    try:
        boards, strikes = range_to_tables(body, root_to_map, w.start, w.end)
        resolution = body["meta"]["resolution"]
    except Exception as e:  # noqa: BLE001 - any conversion failure: the paid body is kept, never pulled again
        path = keep()
        raise WindowFailed("convert_failed", f"{label}: answer couldn't be converted ({type(e).__name__}: {e}); "
                           f"kept in {path}", path) from e
    if boards.num_rows == 0:
        result.empty.append(w.hhmm)
    meta = {"params": params, "meta": body.get("meta"), "pulled_at": rec.at, "credits_remaining": rec.credits_remaining,
            "charged": rec.charged, "bytes": rec.bytes, "probe": w.probe, "repull": reason, "n_boards": boards.num_rows}
    if raw_path is not None:
        meta["converted_from"] = raw_path.name
    try:
        store.write_range(w.day, w.hhmm, boards, strikes, meta, base)
    except Exception as e:  # noqa: BLE001 - a failed write must not lose the paid answer (presd_fixes D1, D5)
        if not store.paths.mounted():
            raise  # the SSD is gone: the runner stops cleanly (ssd_missing) and the window is pulled next time
        path = keep()
        raise WindowFailed("write_failed", f"{label}: couldn't be stored ({type(e).__name__}: {e}); answer kept "
                           f"in {path}", path) from e
    result.pulled.append(w.hhmm)
    result.charged += rec.charged or 0
    result.bytes += rec.bytes or 0
    result.resolutions[w.hhmm] = resolution
    return boards.num_rows > 0


def pull_session(client: SkylitClient, store: SkylitStore, day: date, *, max_strikes: int | str | None = None,
                 should_stop: Callable[[], str | None] = lambda: None, log: logging.Logger,
                 cal: dict[str, Any] | None = None, base: Path | None = None, repull: str | None = None,
                 gate: Callable[[Window], bool] | None = None,
                 now: Callable[[], datetime] | None = None,
                 skip: Callable[[Window], bool] | None = None,
                 on_error: Callable[[Window, Exception, int], bool] | None = None,
                 on_success: Callable[[Window], None] | None = None) -> SessionResult:
    """Pull one session into `base` (default: the normal tree; a re-pull reason sends it to repull/<reason>/).
    `gate` and `now`: see pull_window (the step-10 backfill's reach and credit checks).

    `skip(w)`: True = leave the window alone (the backfill's recorded failures: never pulled again in the main pass).
    `on_error(w, exc, attempt)`: called for a window's error other than NEVER_SKIPPED; True = try the window again
    now, False = it was recorded, go on with the next window (result.failed); it may raise to stop. Without it,
    errors raise (step 7). `on_success(w)`: a call for w was answered (boards or no data), not a cache hit."""
    result = SessionResult(day)
    kw = {"max_strikes": max_strikes, "result": result, "log": log, "base": base, "repull": repull, "gate": gate,
          "now": now}

    def one(w: Window) -> bool:
        reason = should_stop()
        if reason:
            raise StopChunk(reason)
        if skip is not None and skip(w):
            result.failed.append(w.hhmm)
            return False
        attempt = 0
        while True:
            answered = len(result.pulled) + len(result.empty)
            try:
                has_boards = pull_window(client, store, w, **kw)
                if on_success is not None and len(result.pulled) + len(result.empty) > answered:
                    on_success(w)
                return has_boards
            except NEVER_SKIPPED:
                raise
            except Exception as e:  # noqa: BLE001 - on_error decides (it re-raises what it can't handle)
                if on_error is None or not store.paths.mounted():
                    raise
                if not on_error(w, e, attempt):
                    result.failed.append(w.hhmm)
                    return False
                attempt += 1

    for w in session_windows(day, cal):
        has_boards = one(w)
        if w.probe and not cal_mod.is_trading_day(day, cal) and has_boards:
            log.warning("%s is a calendar holiday but the probe returned boards: pulling the whole day", day)
            for extra in full_day_windows(day)[1:]:
                one(extra)
    log.info("Skylit %s", result.summary())
    return result


def uncached_windows(store: SkylitStore, client: SkylitClient, days: list[date],
                     max_strikes: int | str | None = None, cal: dict[str, Any] | None = None,
                     base: Path | None = None, now: datetime | None = None, margin_min: float = 0) -> int:
    """Windows still to pull. With `now`: only those still inside Skylit's reach (lost ones cost nothing)."""
    return sum(1 for d in days for w in session_windows(d, cal)
               if (now is None or window_reachable(w, now, margin_min))
               and not store.range_cached(d, w.hhmm, range_params(client, w, max_strikes), base))


def make_client(settings: dict[str, Any], store: SkylitStore, log: logging.Logger, **kw: Any) -> SkylitClient:
    """The real client: key from .env, every call written to the ledger. Reads /v1/account (free) for limits."""
    kw.setdefault("on_call", store.log_call)
    client = SkylitClient.from_settings(settings, get_secret("SKYLIT_API_KEY"), log=log, **kw)
    client.account()
    return client


class SkylitPullJob:
    """`nqbot run <name>`: pull the sessions listed in <job dir>/dates.json, one session per chunk.

    dates.json = {"dates": [...], "approved_credits": N | null, "max_strikes": ..., "repull": reason | null}
    (repull = "strike_window_raise": the sample days again with a raised skylit_max_strikes, CLAUDE.md rule 2,
    stored under repull/strike_window_raise/max<N>/ so the first pull is never overwritten). It is written by the
    command that starts the pull (e.g. `nqbot step7 sample-days`) after the credit estimate was printed;
    on every run the remaining (uncached) windows are re-estimated and refused over 5,000 credits unless
    approved_credits covers them (CLAUDE.md rule 2).
    """

    locks = (SKYLIT,)  # the Skylit lock only: the Databento backfill may run alongside (§11 One machine)

    def __init__(self, name: str = "skylit_sample_days",
                 client_factory: Callable[..., SkylitClient] = make_client):
        self.name = name
        self.client_factory = client_factory
        self._client: SkylitClient | None = None

    @staticmethod
    def plan_path(paths: Paths, name: str):
        return paths.data_dir / "jobs" / name / "dates.json"

    @staticmethod
    def write_plan(paths: Paths, name: str, dates: list[date], approved: int | None,
                   max_strikes: int | str | None = None, repull: str | None = None) -> None:
        atomic_write_json(SkylitPullJob.plan_path(paths, name),
                          {"dates": [d.isoformat() for d in dates], "approved_credits": approved, "max_strikes": max_strikes,
                           "repull": repull})

    @staticmethod
    def _base(store: SkylitStore, plan: dict[str, Any]) -> Path | None:
        return store.base(plan["repull"], pulled=f"max{plan.get('max_strikes')}") if plan.get("repull") else None

    def _plan(self, ctx: Ctx) -> dict[str, Any]:
        path = self.plan_path(ctx.paths, self.name)
        if not path.exists():
            raise SystemExit(f"{self.name}: no {path}; start it with its nqbot step7 command first")
        return json.loads(path.read_text(encoding="utf-8"))

    def _client_for(self, ctx: Ctx, store: SkylitStore) -> SkylitClient:
        if self._client is None:
            self._client = self.client_factory(ctx.settings, store, ctx.log)
        return self._client

    def chunks(self, ctx: Ctx) -> list[str]:
        plan = self._plan(ctx)
        store = SkylitStore(ctx.paths)
        client = self._client_for(ctx, store)
        days = [date.fromisoformat(d) for d in plan["dates"]]
        est = credits.range_estimate(f"{self.name}: {len(days)} sessions, uncached windows only",
                                     uncached_windows(store, client, days, plan.get("max_strikes"),
                                                      base=self._base(store, plan)))
        credits.check(est, balance=client.credits.remaining, approved=plan.get("approved_credits"), echo=ctx.log.info)
        # A re-pull's chunk ids carry its maxStrikes: a second raise is never marked done by the first (D4).
        suffix = f"_max{plan.get('max_strikes')}" if plan.get("repull") else ""
        return [f"{d}{suffix}" for d in plan["dates"]]

    def run_chunk(self, chunk_id: str, ctx: Ctx) -> None:
        plan = self._plan(ctx)
        store = SkylitStore(ctx.paths)
        try:
            pull_session(self._client_for(ctx, store), store, date.fromisoformat(chunk_id.split("_")[0]),
                         max_strikes=plan.get("max_strikes"), should_stop=ctx.should_stop, log=ctx.log,
                         base=self._base(store, plan), repull=plan.get("repull"))
        except OutOfCredits as e:  # a clean stop, like the step-10 backfill (presd_fixes 17)
            raise StopChunk(f"Skylit answered 402 out of credits ({e.message}): top up, then re-run nqbot run "
                            f"{self.name}", status="credits") from None


def sample_days(paths: Paths) -> set[date]:
    """The step-7 Skylit sample days (nqbot step7 sample-days wrote them); empty before step 7."""
    path = SkylitPullJob.plan_path(paths, "skylit_sample_days")
    if not path.exists():
        return set()
    return {date.fromisoformat(d) for d in json.loads(path.read_text(encoding="utf-8"))["dates"]}
