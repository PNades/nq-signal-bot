"""Pull Skylit RTH history session by session into the cache (SPEC §1.2, §3.1 Backfill, §12 steps 7 and 10).

A session is pulled as 15-minute /v1/historical/range windows (all four maps in one call, gamma,
maxExpirations=1, maxStrikes=skylit_max_strikes): 26 windows on a normal day (09:30-16:00 ET, `from` and
`to` both inclusive, so each window ends 1 ms before the next starts and the last one ends at 16:00:00).
Early-close days are pulled to early_close_et plus one probe window after it; on a calendar holiday
(a weekday) one probe window is pulled, and the rest of the day only if the probe returned boards.
Every window is stored the moment it arrives, so an interrupted session loses nothing and is never
re-pulled.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from nqbot import calendar as cal_mod
from nqbot.clients import skylit_credits as credits
from nqbot.clients.skylit import SkylitClient, SkylitError, iso_utc
from nqbot.clients.skylit_store import SkylitStore, range_to_tables
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


@dataclass
class SessionResult:
    day: date
    pulled: list[str] = field(default_factory=list)
    cached: list[str] = field(default_factory=list)
    empty: list[str] = field(default_factory=list)
    charged: int = 0
    bytes: int = 0
    resolutions: dict[str, str] = field(default_factory=dict)  # hhmm -> meta.resolution

    def summary(self) -> str:
        res = sorted(set(self.resolutions.values()))
        return (f"{self.day}: {len(self.pulled)} pulled, {len(self.cached)} cached, {len(self.empty)} empty, "
                f"{self.charged} credits, {self.bytes / 1e6:.1f} MB, resolution {res or '-'}")


def range_params(client: SkylitClient, w: Window, max_strikes: int | str | None = None) -> dict[str, Any]:
    return {"symbols": client.roots(list(MAPS)), "from": iso_utc(w.start), "to": iso_utc(w.end),
            **client.board_params(max_strikes)}


def pull_window(client: SkylitClient, store: SkylitStore, w: Window, *, max_strikes: int | str | None = None,
                result: SessionResult, log: logging.Logger, base: Path | None = None,
                repull: str | None = None, tree: str | None = None) -> bool:
    """Pull one window unless cached under `base` (default: the normal tree). Returns True if it holds boards
    (a cached window answers from its stored row count, so a stored empty probe stays empty)."""
    params = range_params(client, w, max_strikes)
    if store.range_cached(w.day, w.hhmm, params, base):
        result.cached.append(w.hhmm)
        return store.range_n_boards(w.day, w.hhmm, base) > 0
    reason = repull
    if store.range_partial(w.day, w.hhmm, base):
        moved = store.quarantine_partial(w.day, w.hhmm, base)
        log.warning("%s %s: interrupted write (one of two files); orphan moved to %s, pulling the window again",
                    w.day, w.hhmm, moved)
        reason = reason or "partial_write"
    label = f"{w.day} {w.hhmm}"
    try:
        with client.tagged(window=label, repull=reason, tree=tree):
            body, rec = client.historical_range(list(MAPS), w.start, w.end, max_strikes)
    except SkylitError as e:
        if e.status == 404 and (e.code in NO_DATA_CODES or (w.probe and e.code in PROBE_EMPTY_CODES)):
            result.empty.append(w.hhmm)  # refunded: nothing stored, nothing charged
            return False
        if e.raw is not None:
            log.error("%s: undecodable answer saved to %s", label, store.save_failed(e.raw, f"range_{w.day}_{w.hhmm}"))
        raise
    try:
        boards, strikes = range_to_tables(body, client.root_to_map, w.start, w.end)
    except (ValueError, KeyError) as e:
        path = store.save_failed(json.dumps(body).encode(), f"range_{w.day}_{w.hhmm}")
        raise ValueError(f"{label}: answer couldn't be converted ({e}); kept in {path}") from e
    if boards.num_rows == 0:
        result.empty.append(w.hhmm)
    meta = {"params": params, "meta": body.get("meta"), "pulled_at": rec.at, "credits_remaining": rec.credits_remaining,
            "charged": rec.charged, "bytes": rec.bytes, "probe": w.probe, "repull": reason, "n_boards": boards.num_rows}
    store.write_range(w.day, w.hhmm, boards, strikes, meta, base)
    result.pulled.append(w.hhmm)
    result.charged += rec.charged or 0
    result.bytes += rec.bytes
    result.resolutions[w.hhmm] = body["meta"]["resolution"]
    return boards.num_rows > 0


def pull_session(client: SkylitClient, store: SkylitStore, day: date, *, max_strikes: int | str | None = None,
                 should_stop: Callable[[], str | None] = lambda: None, log: logging.Logger,
                 cal: dict[str, Any] | None = None, base: Path | None = None, repull: str | None = None) -> SessionResult:
    """Pull one session into `base` (default: the normal tree; a re-pull reason sends it to repull/<reason>/)."""
    result = SessionResult(day)
    kw = {"max_strikes": max_strikes, "result": result, "log": log, "base": base, "repull": repull}
    windows = session_windows(day, cal)
    for w in windows:
        reason = should_stop()
        if reason:
            raise StopChunk(reason)
        has_boards = pull_window(client, store, w, **kw)
        if w.probe and not cal_mod.is_trading_day(day, cal) and has_boards:
            log.warning("%s is a calendar holiday but the probe returned boards: pulling the whole day", day)
            for extra in full_day_windows(day)[1:]:
                reason = should_stop()
                if reason:
                    raise StopChunk(reason)
                pull_window(client, store, extra, **kw)
    log.info("Skylit %s", result.summary())
    return result


def uncached_windows(store: SkylitStore, client: SkylitClient, days: list[date],
                     max_strikes: int | str | None = None, cal: dict[str, Any] | None = None,
                     base: Path | None = None) -> int:
    return sum(1 for d in days for w in session_windows(d, cal)
               if not store.range_cached(d, w.hhmm, range_params(client, w, max_strikes), base))


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
        return list(plan["dates"])

    def run_chunk(self, chunk_id: str, ctx: Ctx) -> None:
        plan = self._plan(ctx)
        store = SkylitStore(ctx.paths)
        pull_session(self._client_for(ctx, store), store, date.fromisoformat(chunk_id),
                     max_strikes=plan.get("max_strikes"), should_stop=ctx.should_stop, log=ctx.log,
                     base=self._base(store, plan), repull=plan.get("repull"))
