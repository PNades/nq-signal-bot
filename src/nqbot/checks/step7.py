"""The step-7 checks, ready for SSD day (SPEC §12 step 7, §3.1). Run as `uv run nqbot step7 <check>`.

Each check prints its credit estimate before any chargeable call (CLAUDE.md rule 2), stores what it pulls in
the Skylit cache (skylit_store.py; every paid call reaches ledger.jsonl through the client's on_call), and
writes a JSON report to <paths.reports_dir>/step7/. `report` gathers the latest of each into the parameter
check shown to the owner before step 10 starts.

§7 rule 2b: dates outside the development months (live sessions, this week's prints, today's Tempest row)
appear in reports only as counts, timings and yes/no answers, never with prices or values.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from nqbot import calendar as cal_mod
from nqbot import months as months_mod
from nqbot.checks import step7_analysis as an
from nqbot.clients import skylit_credits as credits
from nqbot.clients.skylit import (DP_RANGE_MAX_DAYS, DarkPoolFailed, DarkPoolPull, OutOfCredits, SkylitClient,
                                  SkylitError)
from nqbot.clients.skylit_pull import (INCLUSIVE_END, MAPS, SessionResult, SkylitPullJob, Window, pull_window,
                                       session_windows, window_reachable)
from nqbot.clients.skylit_store import (
    SkylitStore, cached_or_mismatch, historical_to_table, prints_to_table, read_meta, stream_snapshots_to_tables,
    top_prints_to_table, vol_history_to_table, write_parquet,
)
from nqbot.clients.skylit_stream import SkylitStream, StreamStopped
from nqbot.features import darkpool as dp
from nqbot.storage import Paths, atomic_write_bytes, atomic_write_json, atomic_write_text
from nqbot.warmup import warmup_sessions

ET = ZoneInfo("America/New_York")
SAMPLE_DAYS = 5  # §12 step 7: pull 5 days from development month 6
SAMPLE_MONTH = 6
STRIKE_WINDOW_DEFAULT_START = time(9, 30)  # which 15-minute window the full-chain calls take (the open)
INSTANTS = 40  # §12 step 7: 40 /v1/historical instants over development months 1-6
INSTANT_SEED = 7  # fixed, so the 40 instants are reproducible
STREAM_MINUTES = 15  # §12 step 7: record 15 minutes of the stream
STREAM_RECONNECT_AT = 0.5  # force the one Last-Event-ID reconnect halfway through
STREAM_MAX_RECONNECTS = 3  # the forced one plus two spare; each costs 4 opens + 4 minutes (presd_fixes 15)
STREAM_GRACE_SEC = 60  # hard wall-clock end: minutes + this, even if no event ever arrives (presd_fixes 15)
LATE_PRINT_MORNINGS = 5  # §12 step 7: >= 5 mornings
LATE_PRINT_WAIT_SESSIONS = 5  # re-pulled >= 5 trading days later; also how long a date's prints count as provisional
TEMPEST_MIN_MORNINGS = 2  # §12 step 7: record the first appearance on two or more mornings
TEMPEST_POLL_MIN = 5  # minutes between free /v1/vol/symbols polls
TEMPEST_DEADLINE_ET = time(9, 0)  # §5.2: the previous session's row must reliably be there by 09:00 ET
TEMPEST_DEPTH_DAYS = 14  # calendar days pulled to confirm Tempest reaches back before month 1 (1 credit)
TEMPEST_SYMBOLS = ("QQQ", "SPY", "NDXP", "SPXW")  # Tempest keys the S&P complex on SPXW (Skylit docs)
VIX = "VIX"
RTH_OPEN, RTH_CLOSE = time(9, 30), time(16, 0)

log_default = logging.getLogger("nqbot.step7")


class CheckRefused(Exception):
    pass


def _default_offset(settings: dict[str, Any]) -> Callable[[], float | None]:
    def offset() -> float | None:
        from nqbot.clock import ntp_offset_ms

        try:
            return ntp_offset_ms(settings["ntp_server"])
        except OSError as e:
            log_default.warning("clock offset not measured (%s): latency is on the unchecked local clock", e)
            return None

    return offset


@dataclass
class Env:
    settings: dict[str, Any]
    features: dict[str, Any]
    paths: Paths
    log: logging.Logger = log_default
    client_factory: Callable[[SkylitStore], SkylitClient] | None = None
    stream_factory: Callable[..., SkylitStream] | None = None
    now: Callable[[], datetime] = lambda: datetime.now(ET)
    echo: Callable[[str], None] = print
    cal: dict[str, Any] | None = None
    clock_offset_ms: Callable[[], float | None] | None = None
    ledger_name: str = "ledger.jsonl"  # a check running alongside the backfill logs to its own file (decision 2)
    _store: SkylitStore | None = field(default=None, repr=False)
    _client: SkylitClient | None = field(default=None, repr=False)

    @property
    def store(self) -> SkylitStore:
        if self._store is None:
            self._store = SkylitStore(self.paths, self.ledger_name)
        return self._store

    @property
    def client(self) -> SkylitClient:
        if self._client is None:
            if self.client_factory is None:
                from nqbot.clients.skylit_pull import make_client

                self._client = make_client(self.settings, self.store, self.log)  # ledger wired via on_call
            else:
                self._client = self.client_factory(self.store)
        return self._client

    def trading_day(self, d: date) -> bool:
        return cal_mod.is_trading_day(d, self.cal)

    def close_of(self, d: date) -> time:
        return cal_mod.early_close_et(d, self.cal) or RTH_CLOSE

    def preopen(self) -> time:
        return time.fromisoformat(self.settings["preopen_start"])

    def development(self, d: date) -> bool:
        try:
            return months_mod.month_index(d, self.settings) in months_mod.DEVELOPMENT
        except months_mod.LedgerMissing:
            return False


# --- reports --------------------------------------------------------------------------------------------

def reports_dir(paths: Paths) -> Path:
    return paths.reports_dir / "step7"


def write_report(env: Env, name: str, report: dict[str, Any]) -> Path:
    path = reports_dir(env.paths) / f"{name}_{env.now().astimezone(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    atomic_write_json(path, {"check": name, "written": env.now().isoformat(), **report})
    env.echo(f"report: {path}")
    return path


def latest_report(paths: Paths, name: str) -> dict[str, Any] | None:
    files = sorted(reports_dir(paths).glob(f"{name}_*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def _gate(env: Env, est: credits.Estimate, approved: int | None) -> None:
    credits.check(est, balance=env.client.credits.remaining, approved=approved, echo=env.echo)


def _dev_days(env: Env, month_numbers: range | list[int]) -> list[date]:
    return [d for n in month_numbers for d in months_mod.days_of(n, env.settings) if env.trading_day(d)]


def _next_session(env: Env, d: date) -> date:
    d += timedelta(days=1)
    while not env.trading_day(d):
        d += timedelta(days=1)
    return d


def _sessions_between(env: Env, after: date, upto: date) -> int:
    """Trading sessions in (after, upto]."""
    return sum(1 for d in _weekdays(after + timedelta(days=1), upto) if env.trading_day(d))


# --- symbols --------------------------------------------------------------------------------------------

def check_symbols(env: Env) -> dict[str, Any]:
    """Free: account limits and balance, symbol coverage (QQQ, NDXP, SPY and the SPX roots), Tempest coverage."""
    acct = env.client.account()
    roots = set(env.settings["skylit_symbols"].values()) | {"SPX", "SPXW", "NDX", "NDXP"}
    syms = [s for s in env.client.symbols() if s["symbol"] in roots]
    vol = env.client.vol_symbols()
    vol_rows = (vol.get("data") or {}).get("symbols") or []
    wanted = set(TEMPEST_SYMBOLS) | {VIX}
    report = {
        "account": {"credits": acct.get("creditsBalance"), "limits": acct.get("limits"), "status": acct.get("status")},
        "symbols": syms,
        "missing_roots": sorted(set(env.settings["skylit_symbols"].values()) - {s["symbol"] for s in syms}),
        "tempest": [{k: r.get(k) for k in ("symbol", "historyThrough", "sessionDate", "stale")}
                    for r in vol_rows if r.get("symbol") in wanted],
        "tempest_missing": (vol.get("data") or {}).get("missing"),
        "tempest_vix_served": any(r.get("symbol") == VIX for r in vol_rows),
    }
    for s in syms:
        h = s.get("history") or {}
        env.echo(f"  {s['symbol']:5s} index={s.get('isIndex')} history {h.get('from')} .. {h.get('to')}")
    env.echo(f"  rate limit {report['account']['limits']}, balance {report['account']['credits']}")
    env.echo(f"  Tempest rows for {sorted(r['symbol'] for r in report['tempest'])}; VIX served: {report['tempest_vix_served']}")
    write_report(env, "symbols", report)
    return report


# --- strike window -------------------------------------------------------------------------------------

def _one_window(day: date, start: time) -> Window:
    s = datetime.combine(day, start, tzinfo=ET)
    return Window(day, s, s + timedelta(minutes=15) - timedelta(milliseconds=1))


# §12 step 7 (owner 2026-10-06): a full-chain call that times out is replaced by consecutive shorter calls that
# together cover the same full 15 minutes (3 x 5 minutes; a 5-minute piece that still times out, 5 x 1 minute), so the
# proposal rests on exactly the same boards. It is never measured on less than the full 15 minutes.
STRIKE_WINDOW_SPLITS = (3, 5)
STRIKE_WINDOW_SPLIT_CODES = frozenset({"gave_up", "dropped"})  # a 504 after the client's retries, or a timed-out read


def _minutes(w: Window) -> int:
    return round((w.end + INCLUSIVE_END - w.start).total_seconds() / 60)


def _split(w: Window, n: int) -> list[Window]:
    """n consecutive windows covering exactly w (each `to` 1 ms before the next `from`, the last ends with w)."""
    step = (w.end + INCLUSIVE_END - w.start) / n
    starts = [w.start + step * i for i in range(n)]
    return [Window(w.day, s, (starts[i + 1] - INCLUSIVE_END) if i + 1 < n else w.end) for i, s in enumerate(starts)]


def _piece_base(env: Env, minutes: int) -> Path:
    return env.store.fullchain / "pieces" / f"{minutes}m"


def _manifest_path(env: Env, w: Window) -> Path:
    return env.store.fullchain / "pieces" / f"{w.day.isoformat()}_{w.hhmm}.json"


def _covers(w: Window, pieces: list[Window]) -> bool:
    ps = sorted(pieces, key=lambda p: p.start)
    return bool(ps) and ps[0].start == w.start and ps[-1].end == w.end and all(
        b.start == a.end + INCLUSIVE_END for a, b in zip(ps, ps[1:]))


def _pull_full_chain(env: Env, w: Window, approved: int | None, depth: int = 0) -> list[Window]:
    """Pull w with maxStrikes=all; on a timeout, the consecutive pieces that cover it (recursively)."""
    base = env.store.fullchain if depth == 0 else _piece_base(env, _minutes(w))
    try:
        pull_window(env.client, env.store, w, max_strikes="all", result=SessionResult(w.day), log=env.log, base=base,
                    tree="fullchain")
        return [w]
    except OutOfCredits:
        raise
    except SkylitError as e:
        if e.code not in STRIKE_WINDOW_SPLIT_CODES or depth >= len(STRIKE_WINDOW_SPLITS):
            raise
        n = STRIKE_WINDOW_SPLITS[depth]
        pieces = _split(w, n)
        m = _minutes(pieces[0])
        todo = [p for p in pieces if p.hhmm not in env.store.stored_windows(p.day, _piece_base(env, m))]
        env.echo(f"  {w.day} {w.hhmm}: the full-chain call timed out ({e.code}): {n} consecutive {m}-minute calls "
                 f"cover the same {_minutes(w)} minutes instead")
        _gate(env, credits.range_estimate(f"strike window: {w.day} {w.hhmm} as {n} x {m}-minute full-chain calls",
                                          len(todo)), approved)
        return [x for p in pieces for x in _pull_full_chain(env, p, approved, depth + 1)]


def _strike_window_day(env: Env, d: date, start: time, approved: int | None) -> None:
    """The day's full 15 minutes, whole or in verified consecutive pieces (a manifest records the pieces)."""
    w = _one_window(d, start)
    if w.hhmm in env.store.stored_windows(d, env.store.fullchain) or _manifest_path(env, w).exists():
        return
    got = _pull_full_chain(env, w, approved)
    if got == [w]:
        return
    if not _covers(w, got):  # never measured on less than the full 15 minutes
        raise CheckRefused(f"{d} {w.hhmm}: the full-chain pieces don't cover the whole 15 minutes: ask Claude Code")
    atomic_write_json(_manifest_path(env, w), {"window": [w.start.isoformat(), w.end.isoformat()],
                                               "pieces": [{"minutes": _minutes(p), "hhmm": p.hhmm,
                                                           "from": p.start.isoformat(), "to": p.end.isoformat()}
                                                          for p in sorted(got, key=lambda p: p.start)]})


def read_fullchain(env: Env, d: date) -> tuple[pa.Table, pa.Table]:
    """The day's full-chain boards and strikes: the whole window, or the pieces its manifest lists."""
    if env.store.stored_windows(d, env.store.fullchain):
        return env.store.read_range(d, env.store.fullchain)
    manifests = sorted((env.store.fullchain / "pieces").glob(f"{d.isoformat()}_*.json"))
    if not manifests:
        return env.store.read_range(d, env.store.fullchain)  # empty tables
    pieces = json.loads(manifests[-1].read_text(encoding="utf-8"))["pieces"]
    parts = [env.store.read_range(d, _piece_base(env, m)) for m in sorted({p["minutes"] for p in pieces})]
    return (pa.concat_tables([b for b, _ in parts]), pa.concat_tables([s for _, s in parts]))


def check_strike_window(env: Env, busy_day: date, sample_days: list[date], start: time = STRIKE_WINDOW_DEFAULT_START,
                        approved: int | None = None) -> dict[str, Any]:
    """Full-chain (maxStrikes=all) 15-minute calls -> the window skylit_max_strikes must hold (§12 step 7)."""
    months_mod.require_development(busy_day, env.settings)
    for d in sample_days:
        months_mod.require_development(d, env.settings, [SAMPLE_MONTH])
    days = [busy_day, *[d for d in sample_days if d != busy_day]]
    todo = [d for d in days if not env.store.stored_windows(d, env.store.fullchain)
            and not _manifest_path(env, _one_window(d, start)).exists()]
    _gate(env, credits.range_estimate("strike window: full-chain 15-minute calls", len(todo)), approved)
    boards, strikes = [], []
    for d in days:
        _strike_window_day(env, d, start, approved)
        b, s = read_fullchain(env, d)
        boards.append(b)
        strikes.append(s)
    res = an.strike_window(pa.concat_tables(boards), pa.concat_tables(strikes),
                           env.features["node_drop_pct"], env.settings["strike_edge_margin"])
    report = {"days": [d.isoformat() for d in days], "window_start_et": start.strftime("%H:%M"),
              "node_drop_pct": env.features["node_drop_pct"], "strike_edge_margin": env.settings["strike_edge_margin"],
              "per_symbol": res.per_symbol, "proposed_skylit_max_strikes": res.proposed,
              "drivers": res.drivers, "boards_setting_proposal": res.n_drivers, "next_smaller": res.next_smaller,
              "current_skylit_max_strikes": env.settings["skylit_max_strikes"],
              "assumption": "the API's window = the N strikes nearest spot; checked on the sample-day pulls"}
    for sym, r in sorted(res.per_symbol.items()):
        env.echo(f"  {sym:5s} needs {r['need']} strikes (chain up to {r['chain_strikes_max']}, {r['boards']} boards)")
    env.echo(f"  proposed skylit_max_strikes = {res.proposed} (owner OKs it at the parameter check)")
    for line in _driver_lines(report):
        env.echo("  " + line.lstrip("- "))
    write_report(env, "strike_window", report)
    return report


# --- sample days ------------------------------------------------------------------------------------------

SAMPLE_JOB = "skylit_sample_days"
SAMPLE_RAISE_JOB = "skylit_sample_days_raise"  # its own job: the first pull's state.json never marks it done (D4)
RAISE = "strike_window_raise"


def sample_job_name(raised: bool) -> str:
    return SAMPLE_RAISE_JOB if raised else SAMPLE_JOB


def plan_sample_days(env: Env, dates: list[date], approved: int | None, raised: bool = False) -> None:
    """Write the sample-day plan. raised=True re-pulls them with a raised skylit_max_strikes (rule-2 exception
    `strike_window_raise`) into their own folder and their own job; the first pull is never overwritten."""
    if len(dates) != SAMPLE_DAYS:
        raise CheckRefused(f"step 7 pulls {SAMPLE_DAYS} sample days, got {len(dates)}")
    for d in dates:
        months_mod.require_development(d, env.settings, [SAMPLE_MONTH])
        if not env.trading_day(d):
            raise CheckRefused(f"{d} is not a trading day")
    SkylitPullJob.write_plan(env.paths, sample_job_name(raised), dates, approved,
                             max_strikes=env.settings["skylit_max_strikes"] if raised else None,
                             repull=RAISE if raised else None)


def sample_days_report(env: Env, dates: list[date], raised: bool = False) -> dict[str, Any]:
    """After the pull: credits and payload per day, resolution, 0DTE, edge check, the strike-window prediction.
    raised=True reads the `strike_window_raise` copies pulled with the current skylit_max_strikes (D4)."""
    n = env.settings["skylit_max_strikes"]
    base = env.store.base(RAISE, pulled=f"max{n}") if raised else None
    ledger = [r for r in env.store.ledger() if r["endpoint"] == "/v1/historical/range"
              and r.get("tree") != "fullchain" and r.get("repull") == (RAISE if raised else None)]
    per_day = {}
    all_boards = []
    for d in dates:
        b, s = env.store.read_range(d, base)
        all_boards.append(b)
        calls = [r for r in ledger if str(r.get("window", "")).startswith(d.isoformat())]
        fb, fs = read_fullchain(env, d)
        per_day[d.isoformat()] = {
            "windows": len(env.store.stored_windows(d, base)),
            "credits": sum(r.get("charged") or 0 for r in calls),
            "mb": round(sum(r.get("bytes") or 0 for r in calls) / 1e6, 1),
            "resolution": sorted(set(b["resolution"].to_pylist())),
            "edge_violations": an.edge_violations(b, s, env.features["node_drop_pct"], env.settings["strike_edge_margin"]),
            "window_prediction_mismatches": an.window_prediction_mismatches(fb, fs, s, n) if fb.num_rows else None,
        }
    boards = pa.concat_tables(all_boards) if all_boards else None
    report = {"dates": [d.isoformat() for d in dates], "skylit_max_strikes": n, "raised": raised, "per_day": per_day,
              "zero_dte_misses": an.zero_dte_misses(boards) if boards is not None else []}
    for d, r in per_day.items():
        env.echo(f"  {d}: {r['windows']} windows, {r['credits']} credits, {r['mb']} MB, {r['resolution']}")
    if report["zero_dte_misses"]:
        env.echo(f"  WARNING: boards whose expiration isn't the session date: {report['zero_dte_misses'][:5]}")
    write_report(env, "sample_days", report)
    return report


# --- the oldest window still in reach (presd_fixes D1) ---------------------------------------------------------

OLDEST_WINDOW_SAFETY = timedelta(hours=1)  # §12 step 7: the test window starts skylit_reach_margin_min + 1 h inside the reach


def oldest_window(env: Env, now: datetime) -> Window | None:
    """The first non-probe window of the oldest trading day of the ledger window that the backfill will pull, still
    safely inside Skylit's 365-day reach (the backfill pulls it first, possibly at 1-minute resolution)."""
    first, end = months_mod.span(env.settings)
    margin = float(env.settings["skylit_reach_margin_min"])
    for d in _weekdays(first, min(end, now.date() - timedelta(days=1))):
        if not env.trading_day(d):
            continue
        for w in session_windows(d, env.cal):
            if not w.probe and window_reachable(w, now + OLDEST_WINDOW_SAFETY, margin):
                return w
    return None


def check_oldest_window(env: Env, approved: int | None = None) -> dict[str, Any]:
    """One 25-credit window on the oldest day still in reach: does its answer convert? (Step 7's other pulls are
    month-6 days; the backfill starts with the oldest.) Stored in the normal tree, so the backfill reuses it; run
    only once the strike-window check passes, so its maxStrikes is final."""
    ok, bad = strike_window_check(env)
    if not ok:
        raise CheckRefused("run oldest-window once the strike-window check passes (its window is kept for the "
                           "backfill, so its maxStrikes must be final): " + "; ".join(bad))
    now = env.now()
    w = oldest_window(env, now)
    if w is None:
        raise CheckRefused("no trading day of the ledger window is still inside Skylit's 365-day reach")
    cached = w.hhmm in env.store.stored_windows(w.day)
    _gate(env, credits.range_estimate(f"oldest-window test: {w.day} {w.hhmm} (the oldest day still in reach)",
                                      0 if cached else 1), approved)
    report: dict[str, Any] = {"day": w.day.isoformat(), "window": w.hhmm, "converted": False, "resolution": None,
                              "boards": None, "error": None, "kept_in": None}
    try:
        pull_window(env.client, env.store, w, result=SessionResult(w.day), log=env.log, now=env.now)
    except OutOfCredits:
        raise
    except SkylitError as e:  # reported, not raised: the owner asks Claude Code before step 10
        report["error"] = str(e)
        kept = getattr(e, "raw_path", None)
        report["kept_in"] = str(kept) if kept else None
    else:
        if w.hhmm in env.store.stored_windows(w.day):
            meta = read_meta(env.store.range_paths(w.day, w.hhmm)[0])
            report.update(converted=True, resolution=(meta.get("meta") or {}).get("resolution"),
                          boards=env.store.range_n_boards(w.day, w.hhmm))
        else:
            report["error"] = "no boards stored (no data or out of reach): pick nothing, ask Claude Code"
    env.echo(f"  oldest window {w.day} {w.hhmm}: " + (f"converted, {report['boards']} boards at {report['resolution']}"
                                                      if report["converted"] else f"FAILED ({report['error']})"))
    write_report(env, "oldest_window", report)
    return report


# --- historical instants -------------------------------------------------------------------------------

def check_historical_instants(env: Env, approved: int | None = None) -> dict[str, Any]:
    """40 /v1/historical instants (they carry Skylit's nodeType) over development months 1-6 (§5.2)."""
    instants = an.pick_instants(_dev_days(env, months_mod.DEVELOPMENT), INSTANTS, INSTANT_SEED, close_of=env.close_of)
    todo = [t for t in instants if not env.store.historical_path(t).exists()]
    _gate(env, credits.historical_estimate(f"{len(todo)} of {len(instants)} historical instants", len(todo)), approved)
    types: dict[str, int] = {}
    for t in instants:
        path = env.store.historical_path(t)
        params = {"symbols": env.client.roots(list(MAPS)), "at": t.astimezone(timezone.utc).isoformat(),
                  **env.client.board_params()}
        if not cached_or_mismatch(path, params):
            with env.client.tagged(instant=t.isoformat()):
                body, rec = env.client.historical(list(MAPS), t)
            write_parquet(path, historical_to_table(body, env.client.root_to_map, t),
                          {"params": params, "meta": body.get("meta"), "pulled_at": rec.at})
        for nt in pq.read_table(path, columns=["node_type"])["node_type"].to_pylist():
            types[nt] = types.get(nt, 0) + 1
    report = {"instants": [t.isoformat() for t in instants], "node_types": types}
    env.echo(f"  {len(instants)} instants stored; node types {types}")
    write_report(env, "historical_instants", report)
    return report


# --- stream recording ---------------------------------------------------------------------------------

async def _record(stream: SkylitStream, minutes: float,
                  grace_sec: float = STREAM_GRACE_SEC) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Record until `minutes` after the first event, or the wall-clock end (minutes + grace from now), or the
    stream stops (e.g. the reconnect cap). Returns what was received and why it ended."""
    snaps: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    start = None
    reconnected = False
    ended = "deadline"
    try:
        async with asyncio.timeout(minutes * 60 + grace_sec):
            async for ev in stream.events():
                start = start or ev.arrival_ns
                elapsed_min = (ev.arrival_ns - start) / 6e10
                events.append({"kind": ev.kind, "arrival_ns": ev.arrival_ns, "event_id": ev.event_id,
                               "connection": ev.connection,
                               "data": None if ev.kind in ("snapshot", "velocity") else ev.data,
                               "symbol": (ev.data or {}).get("symbol") if ev.kind in ("snapshot", "velocity") else None})
                if ev.kind == "snapshot" and ev.data:
                    snaps.append({"payload": ev.data, "arrival_ns": ev.arrival_ns, "event_id": ev.event_id,
                                  "connection": ev.connection})
                if not reconnected and elapsed_min >= minutes * STREAM_RECONNECT_AT:
                    stream.request_reconnect()
                    reconnected = True
                if elapsed_min >= minutes:
                    ended = "minutes"
                    break
    except TimeoutError:
        ended = "deadline"
        if not snaps:
            raise CheckRefused(f"no board in {minutes:g} minutes + {grace_sec:g} s: nothing recorded") from None
    except StreamStopped as e:
        if not events:
            raise CheckRefused(f"stream stopped before any event: {e}") from None
        ended = f"stopped: {e}"
    finally:
        await stream.aclose()
    return snaps, events, ended


def check_stream_record(env: Env, minutes: float = STREAM_MINUTES, approved: int | None = None,
                        force: bool = False) -> dict[str, Any]:
    now = env.now()
    if not force:
        close = env.close_of(now.date())
        if not (env.trading_day(now.date()) and RTH_OPEN <= now.time() < close):
            raise CheckRefused("record the stream during a live session (09:30-16:00 ET on a trading day)")
        longest = timedelta(minutes=minutes, seconds=STREAM_GRACE_SEC)  # the wall-clock end included
        if now + longest > datetime.combine(now.date(), close, tzinfo=ET):
            raise CheckRefused(f"{minutes:g} minutes from {now:%H:%M} could run past today's close ({close:%H:%M} ET): "
                               f"start by {(datetime.combine(now.date(), close) - longest):%H:%M}")
    # worst case: open until the wall-clock end, with every reconnect the cap allows
    _gate(env, credits.stream_estimate(f"stream recording, {minutes:g} min, 4 maps", len(MAPS),
                                       minutes + STREAM_GRACE_SEC / 60, reconnects=STREAM_MAX_RECONNECTS), approved)
    offset = (env.clock_offset_ms or _default_offset(env.settings))()
    if env.stream_factory is None:
        from nqbot.secrets import get_secret

        stream = SkylitStream.from_settings(env.settings, get_secret("SKYLIT_API_KEY"), list(MAPS),
                                            credits=env.client.credits, log=env.log,
                                            max_reconnects=STREAM_MAX_RECONNECTS)
    else:
        stream = env.stream_factory()
    snaps, events, ended = asyncio.run(_record(stream, minutes))
    # A resume re-sends each symbol's latest board even if it was already received: keep its first arrival.
    seen: set[tuple[str, str]] = set()
    unique = []
    for s in snaps:
        key = (s["payload"]["symbol"], s["payload"]["asOf"])
        if key not in seen:
            seen.add(key)
            unique.append(s)
    duplicates, snaps = len(snaps) - len(unique), unique
    offset_after = (env.clock_offset_ms or _default_offset(env.settings))()
    boards, strikes = stream_snapshots_to_tables(snaps, stream.root_to_map)
    folder = env.store.stream_dir(now.date())
    tag = now.strftime("%H%M%S")
    meta = {"params": stream.params, "minutes": minutes, "recorded_at": now.isoformat(), "lagged": stream.lagged_count,
            "clock_offset_ms": [offset, offset_after], "ended": ended}
    write_parquet(folder / f"{tag}_boards.parquet", boards, meta)
    write_parquet(folder / f"{tag}_strikes.parquet", strikes, meta)
    atomic_write_text(folder / f"{tag}_events.jsonl", "".join(json.dumps(e, default=str) + "\n" for e in events))
    summary = an.event_report(events)
    stale = env.settings["skylit_stale_sec"]
    report = {"folder": str(folder), "tag": tag, "boards": boards.num_rows, "events": len(events),
              "ended": ended, "complete": ended == "minutes",  # not complete: record again (parity needs all of it)
              "resent_boards_dropped": duplicates,
              "clock_offset_ms": [offset, offset_after],
              "latency": an.latency_report(boards), "events_summary": summary,
              "stream_shape": an.stream_shape(boards, strikes),
              "requested_max_strikes": stream.params["maxStrikes"],
              "skylit_stale_sec": stale,
              "ping_gap_within_stale": summary["ping_gap_max"] is not None and summary["ping_gap_max"] < stale}
    env.echo(f"  {boards.num_rows} boards, proposed map_latency_sec (99.9th pct) "
             f"{report['latency']['proposed_map_latency_sec']}, clock offset {offset} ms, "
             f"credits used {summary['credits_used']}, max ping gap {summary['ping_gap_max']} s"
             + ("" if report["complete"] else f"; INCOMPLETE ({ended}): record again"))
    write_report(env, "stream_record", report)
    return report


# --- parity ---------------------------------------------------------------------------------------------

def check_parity(env: Env, session: date, tag: str | None = None, approved: int | None = None) -> dict[str, Any]:
    """Pull the recorded 15 minutes from history (at least one session later) and compare board by board."""
    ready = datetime.combine(_next_session(env, session), env.preopen(), tzinfo=ET)
    if env.now() < ready:
        raise CheckRefused(f"wait until {ready:%Y-%m-%d %H:%M} ET (the next session's pre-open) to pull the history")
    folder = env.store.stream_dir(session)
    tags = sorted(p.name.split("_")[0] for p in folder.glob("*_boards.parquet"))
    if not tags:
        raise CheckRefused(f"no stream recording in {folder}")
    tag = tag or tags[-1]
    sb, ss = pq.read_table(folder / f"{tag}_boards.parquet"), pq.read_table(folder / f"{tag}_strikes.parquet")
    rec_meta = json.loads((sb.schema.metadata or {})[b"nqbot"])
    events = [json.loads(line) for line in (folder / f"{tag}_events.jsonl").read_text(encoding="utf-8").splitlines() if line]
    first_ns = min(e["arrival_ns"] for e in events)
    start = datetime.fromtimestamp(first_ns // 1_000_000_000, timezone.utc).astimezone(ET)
    end = min(start + timedelta(minutes=15) - timedelta(milliseconds=1),
              datetime.fromtimestamp(max(e["arrival_ns"] for e in events) / 1e9, timezone.utc).astimezone(ET))
    base = env.store.base("parity", pulled=f"{session.isoformat()}_{tag}")
    w = Window(session, start, end)
    params_cached = env.store.stored_windows(session, base)
    _gate(env, credits.range_estimate("parity: the recorded minutes from /v1/historical/range", 0 if params_cached else 1),
          approved)
    pull_window(env.client, env.store, w, max_strikes=rec_meta["params"]["maxStrikes"], result=SessionResult(session),
                log=env.log, base=base, repull="parity")
    hb, hs = env.store.read_range(session, base)
    sbt = sb.replace_schema_metadata(None)
    res = an.compare_boards(sbt, ss.replace_schema_metadata(None), hb, hs, start, end)
    report = {
        "session": session.isoformat(), "tag": tag, "window": [start.isoformat(), end.isoformat()],
        "matched": res.matched, "only_stream": res.only_stream[:50], "n_only_stream": len(res.only_stream),
        "only_history": res.only_history[:50], "n_only_history": len(res.only_history),
        "revised": res.revised[:50], "n_revised": len(res.revised), "STOP_ask_owner": res.stop,
        "extra_history": an.extra_history_boards(sbt, hb, start, end),
        "latency": an.latency_report(sbt), "events_summary": an.event_report(events),
        "stream_max_strikes": rec_meta["params"]["maxStrikes"], "clock_offset_ms": rec_meta.get("clock_offset_ms"),
    }
    env.echo(f"  matched {res.matched}, only in stream {len(res.only_stream)}, only in history {len(res.only_history)}, "
             f"revised {len(res.revised)}")
    if res.stop:
        env.echo("  STOP: history differs from what the stream sent (revised boards). Ask the owner before step 10 (§3.1 Ingest).")
    write_report(env, "parity", report)
    return report


# --- dark pools -----------------------------------------------------------------------------------------

DARKPOOL_MIN_MONTH = 3  # §3.1 Dark pools / §12 step 7 (presd_fixes D8): an earlier --date's lookback may be out of reach
DP_NO_DATA_CODES = frozenset({"NOT_FOUND", "not_found", "no_data"})  # Flowseeker: no prints for the window (free)


def _weekdays(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1) if (start + timedelta(days=i)).weekday() < 5]


def _dp_params(env: Env, d: date) -> dict[str, Any]:
    return {"tickers": ",".join(env.features["dp_tickers"]), "date": d.isoformat(),
            "min_notional": env.features["dp_min_notional"]}


def _recent(env: Env, d: date) -> bool:
    """Prints of a trade date pulled within LATE_PRINT_WAIT_SESSIONS sessions may still grow (late reports)."""
    return _sessions_between(env, d, env.now().date()) < LATE_PRINT_WAIT_SESSIONS


def pull_dp_date(env: Env, d: date, base: Path | None = None, repull: str | None = None, *,
                 keep_empty_trading_day: bool = True, before_page: Callable[[], None] | None = None) -> Path | None:
    """One ET trade date of prints, cached. A no-data answer (free) is cached only for a non-trading day or a
    settled date; on a recent trading day it is refused, never cached as a permanent empty day. Dates pulled
    while recent are marked `provisional` (first received live: the later top-ups may re-pull them, rule 2).

    keep_empty_trading_day=False (the depth probe, the step-10 backfill): an empty trading day is not cached at
    all and None is returned ("no coverage": history may simply not reach that far back)."""
    path = env.store.dp_trades_path(d, base)
    params = _dp_params(env, d)
    if cached_or_mismatch(path, params):
        return path
    try:
        with env.client.tagged(dp_date=d.isoformat(), repull=repull):
            pull = env.client.dark_pool_trades(d, env.features["dp_tickers"], env.features["dp_min_notional"],
                                               before_page=before_page)
    except DarkPoolFailed as e:
        keep_dp_failure(env, e)
        raise
    except SkylitError as e:
        if e.status != 404 or e.code not in DP_NO_DATA_CODES:
            raise
        pull = DarkPoolPull([], 0, span={"date": d.isoformat()})
    return store_dp_date(env, d, pull, base=base, repull=repull, keep_empty_trading_day=keep_empty_trading_day)


def store_dp_date(env: Env, d: date, pull: DarkPoolPull, *, base: Path | None = None, repull: str | None = None,
                  keep_empty_trading_day: bool = True, raw_path: Path | None = None) -> Path | None:
    """Store one date's answer (pull_dp_date's rules). `raw_path`: a kept failed answer converted again for free."""
    path = env.store.dp_trades_path(d, base)
    rows, pages, charged = pull.prints, pull.pages, sum(r.charged or 0 for r in pull.records)
    if not rows and env.trading_day(d) and not keep_empty_trading_day:
        env.log.warning("dark pools: no prints on trading day %s: no coverage (not cached)", d)
        return None
    if not rows and env.trading_day(d) and _recent(env, d):
        raise CheckRefused(f"no dark-pool prints for {d} yet (a recent trading day): not cached, try again later")
    if not rows and env.trading_day(d):
        env.log.warning("dark pools: no prints at all on trading day %s (cached as empty)", d)
    meta = {"params": _dp_params(env, d), "pages": pages, "repull": repull, "charged": charged,
            "pulled_at": env.now().isoformat(), "provisional": _recent(env, d)}
    _write_dp(env, path, pull, meta, str(d), raw_path)
    return path


def keep_dp_failure(env: Env, e: DarkPoolFailed, raw_path: Path | None = None) -> DarkPoolFailed:
    """Keep a paid dark-pool answer that couldn't be used in skylit/failed/, every page as answered, so it is never
    paid for twice (rule 2, presd_fixes D1). `raw_path`: it is already kept there (a free re-conversion)."""
    if raw_path is not None:
        e.raw_path = raw_path
        return e
    doc = {"kind": "dark_pool_trades", "span": e.span, "complete": e.complete, "code": e.code, "message": e.message,
           "pages": e.bodies, "unreadable": e.raw.decode("utf-8", "replace") if e.raw else None}
    label = "dp_" + "_".join(e.span.values()) + ("" if e.complete else "_incomplete")
    try:
        e.raw_path = env.store.save_failed(json.dumps(doc).encode(), label)
    except Exception:  # noqa: BLE001 - e.g. the SSD is gone: the original error is the one to report
        e.raw_path = None
    env.log.error("dark pools %s: %s; answer kept in %s", "..".join(e.span.values()), e, e.raw_path)
    return e


def read_kept_dp(raw_path: Path) -> dict[str, Any] | None:
    """A kept failed answer, or None if the copy itself can't be read (corrupt)."""
    try:
        doc = json.loads(raw_path.read_bytes())
        return doc if isinstance(doc, dict) and isinstance(doc.get("pages"), list) else None
    except (OSError, ValueError):
        return None


def _write_dp(env: Env, path: Path, pull: DarkPoolPull, meta: dict[str, Any], label: str,
              raw_path: Path | None) -> None:
    """Convert and write a paid answer; any failure keeps every page first and raises DarkPoolFailed (D1)."""
    try:
        table = prints_to_table(pull.prints)
    except Exception as e:  # noqa: BLE001 - any conversion failure: the paid pages are kept, converted again later
        raise keep_dp_failure(env, DarkPoolFailed("convert_failed", f"dark pools {label}: answer couldn't be "
                                                  f"converted ({type(e).__name__}: {e})", pull.bodies, True,
                                                  pull.span), raw_path) from e
    if raw_path is not None:
        meta = {**meta, "converted_from": raw_path.name}
    try:
        write_parquet(path, table, meta)
    except Exception as e:  # noqa: BLE001 - a failed write must not lose the paid answer (D1)
        if not env.paths.mounted():
            raise  # the SSD is gone: the runner stops cleanly and nothing was lost that a re-run can't fetch
        raise keep_dp_failure(env, DarkPoolFailed("write_failed", f"dark pools {label}: couldn't be stored "
                                                  f"({type(e).__name__}: {e})", pull.bodies, True, pull.span),
                              raw_path) from e


def dp_trade_date(p: dict[str, Any]) -> date:
    """A print's ET trade date (prints come as dicts with a UTC `timestamp`, from the API or from read_prints)."""
    t = p["timestamp"]
    return (t if isinstance(t, datetime) else _parse_ts(t)).astimezone(ET).date()


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def dp_range_path(env: Env, start: date, end: date) -> Path:
    return env.store.root / "darkpool" / "range" / f"{start.isoformat()}_{end.isoformat()}.parquet"


def pull_dp_range_raw(env: Env, start: date, end: date,
                      before_page: Callable[[], None] | None = None) -> Path:
    """One date_start..date_end call (at most 31 days, §3.1), kept whole in darkpool/range/ before anything else is
    done with it, so a paid answer is never paid for twice (rule 2) even if splitting it fails. A no-data 404 (free)
    is kept as an empty answer."""
    path = dp_range_path(env, start, end)
    if cached_or_mismatch(path, _dp_range_params(env, start, end)):
        return path
    try:
        with env.client.tagged(dp_range=f"{start.isoformat()}..{end.isoformat()}"):
            pull = env.client.dark_pool_trades(start, env.features["dp_tickers"], env.features["dp_min_notional"],
                                               before_page=before_page, end=end)
    except DarkPoolFailed as e:
        keep_dp_failure(env, e)
        raise
    except SkylitError as e:
        if e.status != 404 or e.code not in DP_NO_DATA_CODES:
            raise
        pull = DarkPoolPull([], 0, span={"date_start": start.isoformat(), "date_end": end.isoformat()})
    return store_dp_range_raw(env, start, end, pull)


def _dp_range_params(env: Env, start: date, end: date) -> dict[str, Any]:
    return {"tickers": ",".join(env.features["dp_tickers"]), "date_start": start.isoformat(),
            "date_end": end.isoformat(), "min_notional": env.features["dp_min_notional"]}


def store_dp_range_raw(env: Env, start: date, end: date, pull: DarkPoolPull, raw_path: Path | None = None) -> Path:
    """Keep a 31-day answer whole in darkpool/range/ (`raw_path`: a kept failed answer converted again for free)."""
    path = dp_range_path(env, start, end)
    meta = {"params": _dp_range_params(env, start, end), "pages": pull.pages,
            "charged": sum(r.charged or 0 for r in pull.records), "pulled_at": env.now().isoformat()}
    _write_dp(env, path, pull, meta, f"{start}..{end}", raw_path)
    return path


def _et_dates(table: pa.Table) -> list[date]:
    return [t.astimezone(ET).date() for t in table.column("timestamp").to_pylist()]


def pull_dp_range(env: Env, start: date, end: date, dates: list[date], *,
                  before_page: Callable[[], None] | None = None) -> dict[date, Path | None]:
    """A 31-day answer split by ET trade date and cached per date in the same files and with the same parameters as
    one-date calls, so either kind of cache serves both (owner 2026-10-05: 31-day calls once step 7 has shown they
    return the same prints). Only `dates` are written (never an already cached date). The step-10 rules of
    pull_dp_date(keep_empty_trading_day=False) apply per date: an empty trading day is not cached (None, "no
    coverage"), an empty non-trading day is cached empty, a recent date is marked provisional."""
    todo = [d for d in dates if start <= d <= end and not cached_or_mismatch(env.store.dp_trades_path(d), _dp_params(env, d))]
    if not todo:
        return {}
    raw = pull_dp_range_raw(env, start, end, before_page)
    table = pq.read_table(raw).replace_schema_metadata(None)
    meta = read_meta(raw)
    days = _et_dates(table)
    outside = sorted({d for d in days if not start <= d <= end})
    if outside:  # kept in darkpool/range/: re-running never pays again; ask before trusting this answer
        raise SkylitError(None, "range_date", f"dark-pool range {start}..{end} returned prints dated {outside[:3]}; "
                          f"the answer is kept in {raw} (never paid for again): ask Claude Code before going on")
    day_col = pa.array(days, type=pa.date32())
    out: dict[date, Path | None] = {}
    for d in todo:
        rows = table.filter(pc.equal(day_col, pa.scalar(d, pa.date32())))
        if rows.num_rows == 0 and env.trading_day(d):
            env.log.warning("dark pools: no prints on trading day %s in range %s..%s: no coverage (not cached)",
                            d, start, end)
            out[d] = None
            continue
        path = env.store.dp_trades_path(d)
        write_parquet(path, rows, {
            "params": _dp_params(env, d), "pages": None, "repull": None, "charged": None,
            "via_range": {"start": start.isoformat(), "end": end.isoformat(), "pages": meta.get("pages"),
                          "charged": meta.get("charged")},
            "pulled_at": env.now().isoformat(), "provisional": _recent(env, d)})
        out[d] = path
    return out


def _print_key(p: dict[str, Any]) -> tuple[Any, ...]:
    t = p["timestamp"]
    ts = (t if isinstance(t, datetime) else _parse_ts(t)).astimezone(timezone.utc)
    return (ts.isoformat(), p["ticker"], float(p["price"]), int(p["size"]), float(p["notional"]), p.get("venue"))


def compare_range_with_dates(env: Env, start: date, end: date) -> dict[str, Any]:
    """Step 7: do 31-day calls return exactly the prints of one-date calls? Pulls start..end once (its answer is
    kept in darkpool/range/, never paid twice) and compares, print by print, with the cached one-date files.
    The step-10 backfill uses range calls only if this matched (owner 2026-10-05)."""
    path = pull_dp_range_raw(env, start, end)
    ranged: dict[date, list[tuple[Any, ...]]] = {}
    for p in read_prints([path]):
        ranged.setdefault(dp_trade_date(p), []).append(_print_key(p))
    mismatches = []
    for d in _weekdays(start, end):
        single = env.store.dp_trades_path(d)
        one = sorted(_print_key(p) for p in read_prints([single])) if single.exists() else None
        got = sorted(ranged.get(d, []))
        if one is None:
            if got:
                mismatches.append({"date": d.isoformat(), "single": None, "range": len(got)})
            continue
        if one != got:
            mismatches.append({"date": d.isoformat(), "single": len(one), "range": len(got),
                               "only_single": len(set(one) - set(got)), "only_range": len(set(got) - set(one))})
    extra = sorted(d.isoformat() for d in ranged if d.weekday() >= 5)
    return {"start": start.isoformat(), "end": end.isoformat(), "range_prints": sum(len(v) for v in ranged.values()),
            "mismatches": mismatches, "weekend_dates": extra, "matches": not mismatches and not extra}


def dp_range_ok(paths_: Paths) -> bool:
    """The latest step-7 darkpool report showed 31-day calls return the same prints (step 10 then uses them)."""
    rep = latest_report(paths_, "darkpool")
    return bool(rep and (rep.get("range_check") or {}).get("matches"))


def dp_prints_per_date(paths_: Paths) -> float | None:
    """Prints per pulled trading day in the latest step-7 darkpool report (sizes the step-10 range-call estimate)."""
    rep = latest_report(paths_, "darkpool")
    n = (rep or {}).get("trading_dates_pulled") or (rep or {}).get("dates_pulled")
    return rep["prints"] / n if n else None


def read_prints(paths_: list[Path]) -> list[dict[str, Any]]:
    out = []
    for p in paths_:
        if not p.exists():
            raise FileNotFoundError(f"dark-pool file missing: {p}")
        out += pq.read_table(p).replace_schema_metadata(None).to_pylist()
    return out


def dp_out_of_reach(e: SkylitError) -> bool:
    """A 400 (or a 404 that isn't no-data) for an old date: the history doesn't reach it (§3.1, presd_fixes 9)."""
    return e.status == 400 or (e.status == 404 and e.code not in DP_NO_DATA_CODES)


def depth_probe_result(env: Env, probe: date) -> dict[str, Any]:
    """Best-effort: does Flowseeker reach the first date month 1's lookback needs? Never stops the check, never
    caches an empty or refused date as a trading day (presd_fixes issue 9). An out-of-reach or no-coverage answer
    is kept as a probe record (darkpool/probe/<date>.json), so the probe is not paid for twice (rule 2)."""
    record = env.store.root / "darkpool" / "probe" / f"{probe.isoformat()}.json"
    if record.exists() and not env.store.dp_trades_path(probe).exists():
        return {**json.loads(record.read_text(encoding="utf-8")), "recorded_before": True}
    out: dict[str, Any] = {"date": probe.isoformat(), "probed_at": env.now().isoformat(), "has_prints": False}
    try:
        path = pull_dp_date(env, probe, keep_empty_trading_day=False)
    except SkylitError as e:
        if not dp_out_of_reach(e):
            env.log.warning("dark pools: depth probe %s failed (%s): reported, not cached", probe, e)
            return {**out, "status": "error", "error": str(e)}  # not recorded: may be tried again
        env.log.warning("dark pools: depth probe %s answered %s %s: out of reach", probe, e.status, e.code)
        out["status"] = "out_of_reach"
    else:
        if path is not None:
            has = bool(read_prints([path]))
            return {**out, "status": "has_prints" if has else "no_coverage", "has_prints": has}
        out["status"] = "no_coverage"
    atomic_write_json(record, out)
    return out


def _pages_per_date(env: Env) -> float:
    calls = [r for r in env.store.ledger() if r["endpoint"] == "/v1/dark-pool/trades" and r.get("dp_date")]
    dates = {r["dp_date"] for r in calls}
    return len(calls) / len(dates) if dates else 1.0


def _f4_lower(env: Env, x: date) -> date:
    """First trade date the F4 list at session x can reach (cutoff of the previous session - lookback)."""
    prev = dp.previous_session(x, env.features["dp_lag_sessions"], env.trading_day)
    return (datetime.combine(prev, dp.CUTOFF_ET, tzinfo=ET) - timedelta(days=env.features["dp_lookback_days"])).date()


def check_darkpool(env: Env, day: date, check_dates: list[date], approved: int | None = None,
                   depth_probe: bool = True) -> dict[str, Any]:
    if months_mod.require_development(day, env.settings) < DARKPOOL_MIN_MONTH:
        env.echo(f"  WARNING: {day} is in development month {months_mod.month_index(day, env.settings)}: its 45-day "
                 f"lookback may reach past Skylit's 365-day history; a --date in month {DARKPOOL_MIN_MONTH} or later "
                 "is safer (presd_fixes D8)")
    for d in check_dates:
        months_mod.require_development(d, env.settings)
    lookback, top_n = env.features["dp_lookback_days"], env.features["dp_top_n"]
    as_ofs = sorted({day, *check_dates})
    first = min(min(_f4_lower(env, x), x - timedelta(days=lookback)) for x in as_ofs)
    dates = _weekdays(first, as_ofs[-1])
    probe = None
    if depth_probe:  # the earliest trading day month 1's lookback needs (§3.1: ideally >= 45 days before month 1)
        m1 = months_mod.days_of(1, env.settings)[0]
        probe = next(d for d in _weekdays(_f4_lower(env, m1), m1) if env.trading_day(d))
    todo = [d for d in dates if not env.store.dp_trades_path(d).exists()]
    # the range comparison: the last <= 31 days of the span, one call (priced like one page per date: an upper bound)
    r_end = as_ofs[-1]
    r_start = max(first, r_end - timedelta(days=DP_RANGE_MAX_DAYS - 1))
    range_todo = 0 if dp_range_path(env, r_start, r_end).exists() else len(_weekdays(r_start, r_end))
    probe_todo = int(probe is not None and probe not in dates and not env.store.dp_trades_path(probe).exists())
    tickers = env.features["dp_tickers"]
    tops_todo = sum(1 for x in as_ofs for t in tickers if not env.store.dp_top_path(t, x).exists())
    _gate(env, credits.dark_pool_estimate(f"dark pools: {len(todo)} dates + top-prints cross-check"
                                          + (f" + one {r_start}..{r_end} range call (<= {range_todo} pages)"
                                             if range_todo else "")
                                          + (" + best-effort depth probe" if probe_todo else ""),
                                          len(todo) + probe_todo + range_todo, _pages_per_date(env),
                                          top_prints=tops_todo), approved)
    prints = read_prints([pull_dp_date(env, d) for d in dates])
    checks = []
    for x in as_ofs:
        for t in tickers:
            path = env.store.dp_top_path(t, x)
            params = {"ticker": t, "top_n": top_n, "lookback_days": lookback, "as_of_date": x.isoformat()}
            if not cached_or_mismatch(path, params):
                with env.client.tagged(top_prints=f"{t} {x}"):
                    rows, _ = env.client.top_prints(t, x, top_n, lookback)
                write_parquet(path, top_prints_to_table(t, rows), {"params": params, "pulled_at": env.now().isoformat()})
            api = pq.read_table(path).to_pylist()
            bot = dp.top_prints_api_window(prints, t, x, top_n=top_n, lookback_days=lookback)
            checks.append({"as_of": x.isoformat(), "ticker": t, "match": an.top_matches_api(bot, api),
                           "bot": [_fmt(p) for p in bot], "api": [_fmt(p) for p in api]})
    f4 = {t: dp.top_prints(prints, t, day, top_n=top_n, lookback_days=lookback, lag_sessions=env.features["dp_lag_sessions"],
                           is_trading_day=env.trading_day) for t in tickers}
    try:  # best-effort: a refused range call only keeps step 10 on one date per call
        range_check = compare_range_with_dates(env, r_start, r_end)
    except OutOfCredits:
        raise
    except SkylitError as e:
        env.log.warning("dark pools: the 31-day comparison call failed (%s): step 10 stays one date per call", e)
        range_check = {"start": r_start.isoformat(), "end": r_end.isoformat(), "matches": False, "error": str(e)}
    depth = depth_probe_result(env, probe) if probe is not None else None  # last, best-effort (presd_fixes issue 9)
    ledger = [r for r in env.store.ledger() if r["endpoint"] == "/v1/dark-pool/trades" and r.get("charged") is not None]
    with_prints = sorted({p["timestamp"].astimezone(ET).date() for p in prints})
    report = {
        "date": day.isoformat(), "check_dates": [d.isoformat() for d in check_dates], "dates_pulled": len(dates),
        "trading_dates_pulled": sum(env.trading_day(d) for d in dates),
        "prints": len(prints), "earliest_print_date": with_prints[0].isoformat() if with_prints else None,
        "depth_probe": depth, "range_check": range_check,
        "credits_per_page": sorted({r["charged"] for r in ledger}), "pages_per_date": round(_pages_per_date(env), 2),
        "top_prints_checks": checks, "all_match": all(c["match"] for c in checks),
        "f4_top": {t: [_fmt(p) for p in v.prints] for t, v in f4.items()},
    }
    env.echo(f"  {len(prints)} prints over {len(dates)} dates; top-prints matches: "
             f"{sum(c['match'] for c in checks)}/{len(checks)}; 31-day call matches one-date calls: "
             f"{range_check['matches']}; depth probe {report['depth_probe']}")
    write_report(env, "darkpool", report)
    return report


def _fmt(p: dict[str, Any]) -> dict[str, Any]:
    return {"timestamp": p["timestamp"].isoformat(), "price": p["price"], "size": p["size"], "notional": p["notional"]}


def _f4_lists(env: Env, prints: list[dict[str, Any]], session: date) -> dict[str, list[dict[str, Any]]]:
    return {t: dp.top_prints(prints, t, session, top_n=env.features["dp_top_n"], lookback_days=env.features["dp_lookback_days"],
                             lag_sessions=1, is_trading_day=env.trading_day).prints for t in env.features["dp_tickers"]}


def _history_dates(env: Env, prev: date) -> list[date]:
    lower = datetime.combine(prev, dp.CUTOFF_ET, tzinfo=ET) - timedelta(days=env.features["dp_lookback_days"])
    return [d for d in _weekdays(lower.date(), prev) if d != prev]


def _late_dir(env: Env, kind: str, session: date) -> Path:
    return env.store.base("late_print", pulled=f"{kind}-{session.isoformat()}")


def _lookback_base(env: Env) -> Path:
    """The late-print check's own copies of lookback dates the main cache doesn't hold yet: it runs alongside the
    step-10 backfill without its lock, so it never writes darkpool/trades/ (decision 2, presd_fixes D3)."""
    return env.store.base("late_print", pulled="lookback")


def _lookback_path(env: Env, d: date) -> Path:
    main = env.store.dp_trades_path(d)
    return main if main.exists() else env.store.dp_trades_path(d, _lookback_base(env))


def _lookback_file(env: Env, d: date) -> Path:
    """The main cache's file if it has the date, else the check's own copy (pulled into it once)."""
    if env.store.dp_trades_path(d).exists():
        return pull_dp_date(env, d)  # parameter check only (cached)
    return pull_dp_date(env, d, _lookback_base(env), "late_print")


def has_lookback_copy(env: Env, d: date) -> bool:
    """The late-print check holds this date (step 10 copies it for free; never part of a paid range call)."""
    return env.store.dp_trades_path(d, _lookback_base(env)).exists()


def adopt_lookback_copy(env: Env, d: date) -> bool:
    """Step 10: a date the late-print check already pulled into its own folder is copied into the main cache for
    free instead of being paid for again (rule 2). Parameters are checked; returns False if there is no copy."""
    src = env.store.dp_trades_path(d, _lookback_base(env))
    if not src.exists() or env.store.dp_trades_path(d).exists():
        return False
    cached_or_mismatch(src, _dp_params(env, d))  # CacheMismatch if pulled with other parameters
    atomic_write_bytes(env.store.dp_trades_path(d), src.read_bytes())
    return True


def check_late_prints_morning(env: Env, approved: int | None = None, force: bool = False) -> dict[str, Any]:
    """Before 09:30: pull the previous session's prints (kept apart) and save today's computed top-3 lists.
    The lists stay in the data tree; the report holds counts only (§7 rule 2b)."""
    now = env.now()
    today = now.date()
    if not force and not (env.trading_day(today) and now.time() < RTH_OPEN):
        raise CheckRefused("run the late-print morning pull before 09:30 ET on a trading day")
    prev = dp.previous_session(today, 1, env.trading_day)
    hist = _history_dates(env, prev)
    todo = [d for d in hist if not _lookback_path(env, d).exists()]
    _gate(env, credits.dark_pool_estimate("late prints: this morning's pull + missing lookback dates", len(todo) + 1,
                                          _pages_per_date(env)), approved)
    files = [_lookback_file(env, d) for d in hist]
    folder = _late_dir(env, "morning", today)
    morning = pull_dp_date(env, prev, folder, "late_print")
    lists = _f4_lists(env, read_prints(files + [morning]), today)
    atomic_write_json(folder / "top3.json", {t: [_fmt(p) for p in v] for t, v in lists.items()})
    report = {"session": today.isoformat(), "prev_session": prev.isoformat(), "pulled_at": now.isoformat(),
              "morning_prints": len(read_prints([morning])), "top_sizes": {t: len(v) for t, v in lists.items()}}
    write_report(env, f"late_prints_morning_{today.isoformat()}", report)
    return report


def check_late_prints_compare(env: Env, approved: int | None = None) -> dict[str, Any]:
    """Re-pull each saved morning's previous session >= 5 trading days later and compare prints and top-3 lists."""
    today = env.now().date()
    by_session: dict[str, dict[str, Any]] = {}
    for f in sorted(reports_dir(env.paths).glob("late_prints_morning_*.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        by_session[r["session"]] = r  # one morning per session, however often it was run
    due = [r for r in by_session.values()
           if _sessions_between(env, date.fromisoformat(r["prev_session"]), today) >= LATE_PRINT_WAIT_SESSIONS]
    todo = [r for r in due if not env.store.dp_trades_path(date.fromisoformat(r["prev_session"]),
                                                            _late_dir(env, "recheck", date.fromisoformat(r["session"]))).exists()]
    _gate(env, credits.dark_pool_estimate("late prints: re-pull of each due morning", len(todo), _pages_per_date(env)), approved)
    results = []
    for r in due:
        session, prev = date.fromisoformat(r["session"]), date.fromisoformat(r["prev_session"])
        recheck = pull_dp_date(env, prev, _late_dir(env, "recheck", session), "late_print")
        first = read_prints([env.store.dp_trades_path(prev, _late_dir(env, "morning", session))])
        second = read_prints([recheck])
        hist = read_prints([_lookback_path(env, d) for d in _history_dates(env, prev)])
        a, b = _f4_lists(env, hist + first, session), _f4_lists(env, hist + second, session)
        diff = an.compare_print_sets(first, second)
        results.append({"session": session.isoformat(), "added": len(diff["added"]), "removed": len(diff["removed"]),
                        "changed": len(diff["changed"]),
                        "top_changed": {t: not an.same_top(a[t], b[t]) for t in a}})
    any_top = any(any(x["top_changed"].values()) for x in results)
    enough = len(results) >= LATE_PRINT_MORNINGS
    report = {"mornings_saved": len(by_session), "compared": results, "enough": enough,
              "any_top_list_changed": any_top,
              "recommend_dp_lag_sessions": 2 if any_top else (1 if enough else None)}
    env.echo(f"  {len(results)} mornings compared (need {LATE_PRINT_MORNINGS}); top list changed: {any_top}; "
             f"recommend dp_lag_sessions = {report['recommend_dp_lag_sessions']}")
    write_report(env, "late_prints_compare", report)
    return report


# --- Tempest --------------------------------------------------------------------------------------------

def _history_through(vol_symbols: dict[str, Any]) -> tuple[dict[str, str | None], str]:
    """Each symbol's last stored daily session. The docs name it `historyThrough`; if the rows lack it,
    `sessionDate` (the session of the latest reading) is used instead and the report says so."""
    rows = (vol_symbols.get("data") or {}).get("symbols") or []
    if any("historyThrough" in r for r in rows):
        return {r.get("symbol"): r.get("historyThrough") for r in rows}, "historyThrough"
    return {r.get("symbol"): r.get("sessionDate") for r in rows}, "sessionDate"


def check_tempest_poll(env: Env, until: time = RTH_OPEN, approved: int | None = None,
                       sleep: Callable[[float], None] | None = None) -> dict[str, Any]:
    """Poll the free /v1/vol/symbols until each symbol's last stored session reaches the one just closed;
    record when it first appears (on time = by 09:00 ET of the session that uses it, §5.2)."""
    import time as _time

    sleep = sleep or _time.sleep
    now = env.now()
    # The row awaited: today's session if started after today's close, else the previous session's.
    if env.trading_day(now.date()) and now.time() >= env.close_of(now.date()):
        prev = now.date()
    else:
        prev = dp.previous_session(now.date(), 1, env.trading_day)
    uses = _next_session(env, prev)  # the session that will use the row
    through, field_used = _history_through(env.client.vol_symbols())
    targets = [s for s in (*TEMPEST_SYMBOLS, VIX) if s in through]
    present_at_start = {s: bool(through.get(s) and through[s] >= prev.isoformat()) for s in targets}
    first_seen: dict[str, str | None] = {s: None for s in targets}
    deadline = datetime.combine(uses, until, tzinfo=ET)
    while True:
        stamp = env.now()
        for s in targets:
            if first_seen[s] is None and through.get(s) and through[s] >= prev.isoformat():
                first_seen[s] = stamp.isoformat()
        if all(first_seen.values()) or stamp >= deadline:
            break
        sleep(TEMPEST_POLL_MIN * 60)
        through, field_used = _history_through(env.client.vol_symbols())
    seen = [s for s in targets if first_seen[s]]
    stored = None
    already = env.store.vol_files(prev, prev)
    if seen and not already:  # pull only the new row, once (later rows are re-pulled only by tempest-compare)
        _gate(env, credits.vol_history_estimate("Tempest row just published", len(seen), prev, prev), approved)
        with env.client.tagged(tempest="new_row"):
            body, rec = env.client.vol_history(seen, prev, prev)
        pulled = env.now().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        stored = env.store.vol_path(prev, prev, pulled)
        write_parquet(stored, vol_history_to_table(body),
                      {"params": {"symbols": seen, "from": prev.isoformat(), "to": prev.isoformat()},
                       "pulled_at": env.now().isoformat()})
    deadline_09 = datetime.combine(uses, TEMPEST_DEADLINE_ET, tzinfo=ET)
    on_time = {s: t is not None and datetime.fromisoformat(t) <= deadline_09 for s, t in first_seen.items()}
    report = {"session_using_row": uses.isoformat(), "prev_session": prev.isoformat(), "first_seen": first_seen,
              "present_at_start": present_at_start, "by_09_00": on_time, "field_used": field_used,
              "vix_served": VIX in targets, "stored": str(stored) if stored else None}
    env.echo(f"  previous-session rows first seen: {first_seen} (present at start: {present_at_start})")
    write_report(env, f"tempest_poll_{uses.isoformat()}", report)
    return report


def check_tempest_compare(env: Env, approved: int | None = None) -> dict[str, Any]:
    """Re-pull the oldest stored Tempest row not yet re-checked (at least one session after it was pulled) and
    list every changed cell (re-pull `tempest_recheck`; no values in the report, §7 rule 2b)."""
    today = env.now().date()
    candidates = []
    for f in env.store.vol_files():
        meta = read_meta(f)
        pulled = datetime.fromisoformat(meta["pulled_at"]).astimezone(ET).date()
        done = (env.store.root / "repull" / "tempest_recheck" / f.stem)
        if _sessions_between(env, pulled, today) >= 1 and not done.exists():
            candidates.append((f, meta, done))
    if not candidates:
        raise CheckRefused("no Tempest pull ready to re-check (needs one pulled at least one session ago, not yet re-checked)")
    first, meta, folder = candidates[0]
    p = meta["params"]
    start, end = date.fromisoformat(p["from"]), date.fromisoformat(p["to"])
    _gate(env, credits.vol_history_estimate("Tempest re-pull check", len(p["symbols"]), start, end), approved)
    with env.client.tagged(repull="tempest_recheck"):
        body, rec = env.client.vol_history(p["symbols"], start, end)
    second = vol_history_to_table(body)
    write_parquet(folder / first.name, second, {"params": p, "pulled_at": env.now().isoformat(), "repull": "tempest_recheck"})
    diffs = an.compare_vol_tables(pq.read_table(first).replace_schema_metadata(None), second)
    report = {"first": first.name, "changed_cells": len(diffs), "diffs": diffs[:100]}
    env.echo(f"  {len(diffs)} Tempest cells changed between pulls")
    write_report(env, "tempest_compare", report)
    return report


def _tempest_start(env: Env) -> date:
    """§12 step 10: Tempest from warmup_sessions + 2 sessions before month 1."""
    d = months_mod.days_of(1, env.settings)[0]
    for _ in range(warmup_sessions(env.features) + 2):
        d = dp.previous_session(d, 1, env.trading_day)
    return d


def check_tempest_depth(env: Env, approved: int | None = None) -> dict[str, Any]:
    """Does /v1/vol/history reach the first date step 10 needs (and VIX too, if served)? One small pull."""
    start = _tempest_start(env)
    end = start + timedelta(days=TEMPEST_DEPTH_DAYS)
    through, _ = _history_through(env.client.vol_symbols())
    symbols = ["QQQ"] + ([VIX] if VIX in through else [])
    _gate(env, credits.vol_history_estimate("Tempest depth check", len(symbols), start, end), approved)
    with env.client.tagged(tempest="depth"):
        body, rec = env.client.vol_history(symbols, start, end)
    table = vol_history_to_table(body)
    pulled = env.now().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    write_parquet(env.store.vol_path(start, end, pulled), table,
                  {"params": {"symbols": symbols, "from": start.isoformat(), "to": end.isoformat()},
                   "pulled_at": env.now().isoformat()})
    first = {s: min((d for sym, d in zip(table["symbol"].to_pylist(), table["date"].to_pylist()) if sym == s), default=None)
             for s in symbols}
    report = {"needed_from": start.isoformat(), "first_row": {s: str(d) if d else None for s, d in first.items()},
              "covered": {s: d is not None and _sessions_between(env, start, d) <= 1 for s, d in first.items()},
              "missing": (body.get("data") or {}).get("missing")}
    env.echo(f"  Tempest needed from {start}; first rows {report['first_row']}; covered {report['covered']}")
    write_report(env, "tempest_depth", report)
    return report


def tempest_lag_recommendation(paths: Paths) -> dict[str, Any]:
    """§5.2: tempest_lag_sessions = 1 only if the previous session's row was there by 09:00 on every recorded
    morning (at least two); otherwise 2. Fewer than two mornings: not decided yet."""
    polls = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(reports_dir(paths).glob("tempest_poll_*.json"))]
    mornings = {p["session_using_row"]: p for p in polls}
    if len(mornings) < TEMPEST_MIN_MORNINGS:
        return {"mornings": len(mornings), "recommend_tempest_lag_sessions": None}
    ok = all(all(p["by_09_00"].values()) and p["by_09_00"] for p in mornings.values())
    return {"mornings": len(mornings), "all_by_09_00": ok, "recommend_tempest_lag_sessions": 1 if ok else 2}


# --- parameter check and backfill estimate ------------------------------------------------------------------

def _balance_and_symbols(env: Env) -> tuple[int | None, str, list[str]]:
    """The live balance and the Tempest symbols served (both free calls); without a key or network, the latest
    `symbols` report's values, labelled with its date."""
    from nqbot.clients import skylit_backfill as bf

    try:
        through, _ = _history_through(env.client.vol_symbols())
        return env.client.credits.remaining, "live (/v1/account)", bf.tempest_symbols(through)
    except Exception as e:  # noqa: BLE001 - the report must still be written (no key, no network)
        sym = latest_report(env.paths, "symbols")
        if sym is None:
            return None, f"unknown ({type(e).__name__}: {e}; no symbols report yet)", bf.tempest_symbols(None)
        served = {r.get("symbol"): True for r in sym.get("tempest") or []}
        bal = (sym.get("account") or {}).get("credits")
        since = datetime.fromisoformat(sym["written"]).astimezone(timezone.utc)
        spent = sum(max(r.get("charged") or 0, bf.nominal_cost(r)) for r in env.store.ledger()
                    if (r.get("status") or 999) < 400 and r.get("at") and datetime.fromisoformat(r["at"]) > since)
        return (None if bal is None else bal - spent,
                f"the symbols report of {sym.get('written')} minus {spent:,} credits spent since",
                bf.tempest_symbols(served))


def backfill_estimate(env: Env) -> dict[str, Any]:
    """Step 10 as a plan made now would pull it: reachable past windows only, lost days listed, the shortfall and
    the cheapest covering pack (§3.1, decision 2 in docs/presd_fixes.md)."""
    from nqbot.clients import skylit_backfill as bf

    balance, source, symbols = _balance_and_symbols(env)
    out = bf.report_estimate(env, balance, symbols)
    out["balance_source"] = source
    return out


REPORT_NAMES = ["symbols", "strike_window", "sample_days", "oldest_window", "historical_instants", "stream_record", "parity", "darkpool",
                "late_prints_compare", "tempest_depth", "tempest_compare"]
# SSD-day order (decision 1 in docs/presd_fixes.md, SPEC §12 step 7): what the owner's step-10 OK needs, what runs
# before the report, and what runs alongside step 10 (never holds it up; needed before step 11).
NEEDED_FOR_STEP10 = ("symbols", "strike_window", "sample_days", "oldest_window")
BEFORE_REPORT = ("historical_instants", "darkpool", "tempest_depth")
ALONGSIDE = ("stream_record", "parity", "late_prints_compare", "tempest_compare")


def _strikes_value(v: Any) -> float:
    return float("inf") if v == "all" else float(v)


def strike_window_check(env: Env) -> tuple[bool, list[str]]:
    """Decision 3 (presd_fixes D2): the backfill may start only when skylit_max_strikes is at least the window the
    strike-window check proposed, and the latest sample-days pull, made with that value, shows no edge violation.
    Returns (passed, what is wrong). There is no override: the fix is to raise skylit_max_strikes and re-check."""
    sw, sd = latest_report(env.paths, "strike_window"), latest_report(env.paths, "sample_days")
    current = env.settings["skylit_max_strikes"]
    bad = []
    if sw is None:
        bad.append("run nqbot step7 strike-window first")
    elif _strikes_value(current) < _strikes_value(sw["proposed_skylit_max_strikes"]):
        bad.append(f"skylit_max_strikes is {current}, below the {sw['proposed_skylit_max_strikes']} the strike-window "
                   "check proposed: set it in config/settings.yaml (owner's OK), then run nqbot step7 sample-days "
                   "--raised")
    if sd is None:
        bad.append("run nqbot step7 sample-days first")
    elif sd.get("skylit_max_strikes") != current:
        bad.append(f"the sample days were pulled with {sd.get('skylit_max_strikes')}, not {current}: run nqbot step7 "
                   "sample-days --dates ... --raised")
    elif current != "all":  # with the whole chain the edge is the ladder's real end, not a cut
        edges = {d: {s: f"{v['violations']} board(s), first {v.get('first')}"
                     for s, v in (r.get("edge_violations") or {}).items() if v.get("violations")}
                 for d, r in (sd.get("per_day") or {}).items()}
        edges = {d: v for d, v in edges.items() if v}
        if edges:
            bad.append(f"strong strikes sit at the window's edge on the sample days ({edges}): raise skylit_max_strikes "
                       "(owner's OK), then run nqbot step7 sample-days --raised")
    return not bad, bad


def _driver_lines(sw: dict[str, Any]) -> list[str]:
    """Which boards set the proposal and what the next smaller window would be (owner 2026-10-06: zero tolerance
    stays; the owner decides if one unusual board drives the window bigger). Range calls cost the same whatever
    maxStrikes is (Skylit API: /v1/historical/range is a fixed 25 credits), so the difference is data size."""
    drivers, nxt, n = sw.get("drivers") or [], sw.get("next_smaller"), sw.get("proposed_skylit_max_strikes")
    if not drivers:
        return []
    shown = "; ".join(f"{d['symbol']} {d['as_of']} (strike {d['strike']})" for d in drivers[:3])
    lines = [f"- the proposal {n} is set by {sw.get('boards_setting_proposal', len(drivers))} board(s): {shown}"]
    if nxt:
        lines.append(f"- without them every other board fits in {nxt} strikes. Credits: {credits.RANGE_CALL} per "
                     f"15-minute window either way (the price doesn't depend on maxStrikes); the data stored per window "
                     f"is about {n / nxt:.2f}x larger with {n}. The owner decides; the gate itself has zero tolerance")
    return lines


def _strike_check(env: Env, latest: dict[str, Any]) -> list[str]:
    sw, sd = latest.get("strike_window"), latest.get("sample_days")
    current = env.settings["skylit_max_strikes"]
    lines = [f"- skylit_max_strikes now: {current}"
             + (f"; proposed by strike-window: {sw.get('proposed_skylit_max_strikes')}" if sw else "")]
    lines += _driver_lines(sw) if sw else []
    if sd is not None:
        lines.append(f"- sample days pulled with {sd.get('skylit_max_strikes')}"
                     + (" (the raised copies)" if sd.get("raised") else ""))
    ok, bad = strike_window_check(env)
    lines.append("- strike-window check: passed" if ok else "- **strike-window check: NOT READY** (nqbot skylit plan "
                 "refuses until it passes):" + "".join(f"\n  - {b}" for b in bad))
    return lines


def check_report(env: Env) -> str:
    from nqbot.clients import skylit_backfill as bf_mod

    latest = {n: latest_report(env.paths, n) for n in REPORT_NAMES}
    missing = [n for n in NEEDED_FOR_STEP10 if latest[n] is None]
    strikes_ok, _ = strike_window_check(env)
    ow = latest["oldest_window"]
    oldest_ok = ow is not None and ow.get("converted")
    why = ([f"missing: {', '.join(missing)}"] if missing else []) + ([] if strikes_ok else ["strike-window check"]) \
        + ([] if oldest_ok or ow is None else ["the oldest-window test failed"])
    lines = ["# Step 7 parameter check", "", f"Written {env.now():%Y-%m-%d %H:%M} ET.", "",
             f"**Ready for the owner's step-10 OK: {'yes' if not why else 'NOT READY'}**"
             + (f" ({'; '.join(why)})" if why else ""), ""]
    lines += ["## Strike window", *_strike_check(env, latest), ""]
    sections = [("Needed for the step-10 OK", NEEDED_FOR_STEP10, "MISSING: run it before the OK"),
                ("Before the report (SSD-day order)", BEFORE_REPORT, "not run yet"),
                ("Alongside step 10 (never holds it up; needed before step 11)", ALONGSIDE,
                 "pending (runs alongside)")]
    for title, names, absent in sections:
        lines += [f"# {title}", ""]
        for n in names:
            r = latest[n]
            lines.append(f"## {n}")
            lines.append(absent if r is None else "```json\n" + json.dumps(_brief(n, r), indent=1, default=str) + "\n```")
            lines.append("")
        if names is ALONGSIDE:
            rec = tempest_lag_recommendation(env.paths)
            lines += ["## Tempest timing (tempest_lag_sessions)",
                      "pending (runs alongside)" if rec["recommend_tempest_lag_sessions"] is None else "",
                      "```json", json.dumps(rec, indent=1), "```", ""]
    try:
        bf = backfill_estimate(env)
    except (months_mod.LedgerMissing, bf_mod.BackfillRefused) as e:
        lines += ["# Step-10 backfill estimate", f"not available: {e}"]
    else:
        lines += ["# Step-10 backfill estimate", "```", bf["text"], "```",
                  f"- window {bf['window'][0]}..{bf['window'][1]}: {bf['weekdays']} weekdays still in reach, "
                  f"{bf['dark_pool_dates']} dark-pool dates, Tempest from {bf['tempest']['from']} "
                  f"({', '.join(bf['tempest']['symbols'])})",
                  f"- already beyond the 365-day reach: {len(bf['lost_days'])} weekdays"
                  + (f" ({bf['lost_days'][0]}..{bf['lost_days'][-1]})" if bf["lost_days"] else ""),
                  f"- with a {bf['margin']:.0%} margin: {bf['with_margin']:,} credits; balance {bf['balance']} "
                  f"({bf['balance_source']})"]
        if bf["shortfall"] is None:
            lines.append("- shortfall: unknown (no balance)")
        elif bf["shortfall"] == 0:
            lines.append("- shortfall: none, the balance covers the whole backfill")
        else:
            packs = bf["packs"]
            buy = " + ".join(f"{b['count']} x {b['credits']:,} (${b['usd']})" for b in packs["buy"])
            lines.append(f"- **shortfall {bf['shortfall']:,} credits: buy {buy} = ${packs['usd']:g} "
                         f"({packs['credits']:,} credits)**")
        lines.append(f"- then: `uv run nqbot skylit plan --approve-credits {bf['with_margin']}` (the estimate plus the margin) and "
                     "`uv run nqbot run skylit_backfill --until 07:30`")
    text = "\n".join(lines) + "\n"
    path = reports_dir(env.paths) / "parameter_check.md"
    atomic_write_text(path, text)
    env.echo(f"report: {path}")
    return text


def _brief(name: str, r: dict[str, Any]) -> dict[str, Any]:
    keep = {
        "symbols": ["account", "missing_roots", "tempest_vix_served"],
        "strike_window": ["per_symbol", "proposed_skylit_max_strikes", "current_skylit_max_strikes",
                          "boards_setting_proposal", "drivers", "next_smaller"],
        "sample_days": ["skylit_max_strikes", "raised", "per_day", "zero_dte_misses"],
        "oldest_window": ["day", "window", "converted", "resolution", "boards", "error", "kept_in"],
        "historical_instants": ["node_types"],
        "stream_record": ["complete", "ended", "boards", "clock_offset_ms", "latency", "events_summary", "stream_shape",
                          "ping_gap_within_stale"],
        "parity": ["matched", "n_only_stream", "n_only_history", "n_revised", "STOP_ask_owner", "extra_history", "latency"],
        "darkpool": ["prints", "earliest_print_date", "depth_probe", "credits_per_page", "pages_per_date", "all_match",
                     "range_check"],
        "late_prints_compare": ["compared", "enough", "any_top_list_changed", "recommend_dp_lag_sessions"],
        "tempest_depth": ["needed_from", "first_row", "covered"],
        "tempest_compare": ["changed_cells"],
    }[name]
    return {k: r.get(k) for k in keep}
