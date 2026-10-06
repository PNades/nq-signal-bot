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
import pyarrow.parquet as pq

from nqbot import calendar as cal_mod
from nqbot import months as months_mod
from nqbot.checks import step7_analysis as an
from nqbot.clients import skylit_credits as credits
from nqbot.clients.skylit import SkylitClient, SkylitError
from nqbot.clients.skylit_pull import MAPS, SessionResult, SkylitPullJob, Window, pull_window
from nqbot.clients.skylit_store import (
    SkylitStore, cached_or_mismatch, historical_to_table, prints_to_table, read_meta, stream_snapshots_to_tables,
    top_prints_to_table, vol_history_to_table, write_parquet,
)
from nqbot.clients.skylit_stream import SkylitStream
from nqbot.features import darkpool as dp
from nqbot.storage import Paths, atomic_write_json, atomic_write_text

ET = ZoneInfo("America/New_York")
SAMPLE_DAYS = 5  # §12 step 7: pull 5 days from development month 6
SAMPLE_MONTH = 6
STRIKE_WINDOW_DEFAULT_START = time(9, 30)  # which 15-minute window the full-chain calls take (the open)
INSTANTS = 40  # §12 step 7: 40 /v1/historical instants over development months 1-6
INSTANT_SEED = 7  # fixed, so the 40 instants are reproducible
STREAM_MINUTES = 15  # §12 step 7: record 15 minutes of the stream
STREAM_RECONNECT_AT = 0.5  # force the one Last-Event-ID reconnect halfway through
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
    _store: SkylitStore | None = field(default=None, repr=False)
    _client: SkylitClient | None = field(default=None, repr=False)

    @property
    def store(self) -> SkylitStore:
        if self._store is None:
            self._store = SkylitStore(self.paths)
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


def check_strike_window(env: Env, busy_day: date, sample_days: list[date], start: time = STRIKE_WINDOW_DEFAULT_START,
                        approved: int | None = None) -> dict[str, Any]:
    """Full-chain (maxStrikes=all) 15-minute calls -> the window skylit_max_strikes must hold (§12 step 7)."""
    months_mod.require_development(busy_day, env.settings)
    for d in sample_days:
        months_mod.require_development(d, env.settings, [SAMPLE_MONTH])
    days = [busy_day, *[d for d in sample_days if d != busy_day]]
    base = env.store.fullchain
    todo = [d for d in days if not env.store.stored_windows(d, base)]
    _gate(env, credits.range_estimate("strike window: full-chain 15-minute calls", len(todo)), approved)
    boards, strikes = [], []
    for d in days:
        pull_window(env.client, env.store, _one_window(d, start), max_strikes="all", result=SessionResult(d),
                    log=env.log, base=base, tree="fullchain")
        b, s = env.store.read_range(d, base)
        boards.append(b)
        strikes.append(s)
    res = an.strike_window(pa.concat_tables(boards), pa.concat_tables(strikes),
                           env.features["node_drop_pct"], env.settings["strike_edge_margin"])
    report = {"days": [d.isoformat() for d in days], "window_start_et": start.strftime("%H:%M"),
              "node_drop_pct": env.features["node_drop_pct"], "strike_edge_margin": env.settings["strike_edge_margin"],
              "per_symbol": res.per_symbol, "proposed_skylit_max_strikes": res.proposed,
              "current_skylit_max_strikes": env.settings["skylit_max_strikes"],
              "assumption": "the API's window = the N strikes nearest spot; checked on the sample-day pulls"}
    for sym, r in sorted(res.per_symbol.items()):
        env.echo(f"  {sym:5s} needs {r['need']} strikes (chain up to {r['chain_strikes_max']}, {r['boards']} boards)")
    env.echo(f"  proposed skylit_max_strikes = {res.proposed} (owner OKs it at the parameter check)")
    write_report(env, "strike_window", report)
    return report


# --- sample days ------------------------------------------------------------------------------------------

def plan_sample_days(env: Env, dates: list[date], approved: int | None, raised: bool = False) -> None:
    """Write the sample-day plan. raised=True re-pulls them with a raised skylit_max_strikes (rule-2 exception
    `strike_window_raise`) into their own folder; the first pull is never overwritten."""
    if len(dates) != SAMPLE_DAYS:
        raise CheckRefused(f"step 7 pulls {SAMPLE_DAYS} sample days, got {len(dates)}")
    for d in dates:
        months_mod.require_development(d, env.settings, [SAMPLE_MONTH])
        if not env.trading_day(d):
            raise CheckRefused(f"{d} is not a trading day")
    SkylitPullJob.write_plan(env.paths, "skylit_sample_days", dates, approved,
                             max_strikes=env.settings["skylit_max_strikes"] if raised else None,
                             repull="strike_window_raise" if raised else None)


def sample_days_report(env: Env, dates: list[date]) -> dict[str, Any]:
    """After the pull: credits and payload per day, resolution, 0DTE, edge check, the strike-window prediction."""
    ledger = [r for r in env.store.ledger() if r["endpoint"] == "/v1/historical/range"
              and r.get("tree") != "fullchain" and not r.get("repull")]
    n = env.settings["skylit_max_strikes"]
    per_day = {}
    all_boards = []
    for d in dates:
        b, s = env.store.read_range(d)
        all_boards.append(b)
        calls = [r for r in ledger if str(r.get("window", "")).startswith(d.isoformat())]
        fb, fs = env.store.read_range(d, env.store.fullchain)
        per_day[d.isoformat()] = {
            "windows": len(env.store.stored_windows(d)),
            "credits": sum(r.get("charged") or 0 for r in calls),
            "mb": round(sum(r.get("bytes") or 0 for r in calls) / 1e6, 1),
            "resolution": sorted(set(b["resolution"].to_pylist())),
            "edge_violations": an.edge_violations(b, s, env.features["node_drop_pct"], env.settings["strike_edge_margin"]),
            "window_prediction_mismatches": an.window_prediction_mismatches(fb, fs, s, n) if fb.num_rows else None,
        }
    boards = pa.concat_tables(all_boards) if all_boards else None
    report = {"dates": [d.isoformat() for d in dates], "skylit_max_strikes": n, "per_day": per_day,
              "zero_dte_misses": an.zero_dte_misses(boards) if boards is not None else []}
    for d, r in per_day.items():
        env.echo(f"  {d}: {r['windows']} windows, {r['credits']} credits, {r['mb']} MB, {r['resolution']}")
    if report["zero_dte_misses"]:
        env.echo(f"  WARNING: boards whose expiration isn't the session date: {report['zero_dte_misses'][:5]}")
    write_report(env, "sample_days", report)
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

async def _record(stream: SkylitStream, minutes: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    snaps, events = [], []
    start = None
    reconnected = False
    try:
        async for ev in stream.events():
            start = start or ev.arrival_ns
            elapsed_min = (ev.arrival_ns - start) / 6e10
            events.append({"kind": ev.kind, "arrival_ns": ev.arrival_ns, "event_id": ev.event_id,
                           "connection": ev.connection, "data": None if ev.kind in ("snapshot", "velocity") else ev.data,
                           "symbol": (ev.data or {}).get("symbol") if ev.kind in ("snapshot", "velocity") else None})
            if ev.kind == "snapshot" and ev.data:
                snaps.append({"payload": ev.data, "arrival_ns": ev.arrival_ns, "event_id": ev.event_id,
                              "connection": ev.connection})
            if not reconnected and elapsed_min >= minutes * STREAM_RECONNECT_AT:
                stream.request_reconnect()
                reconnected = True
            if elapsed_min >= minutes:
                break
    finally:
        await stream.aclose()
    return snaps, events


def check_stream_record(env: Env, minutes: float = STREAM_MINUTES, approved: int | None = None,
                        force: bool = False) -> dict[str, Any]:
    now = env.now()
    if not force and not (env.trading_day(now.date()) and RTH_OPEN <= now.time() < env.close_of(now.date())):
        raise CheckRefused("record the stream during a live session (09:30-16:00 ET on a trading day)")
    _gate(env, credits.stream_estimate(f"stream recording, {minutes:g} min, 4 maps", len(MAPS), minutes, reconnects=1), approved)
    offset = (env.clock_offset_ms or _default_offset(env.settings))()
    if env.stream_factory is None:
        from nqbot.secrets import get_secret

        stream = SkylitStream.from_settings(env.settings, get_secret("SKYLIT_API_KEY"), list(MAPS),
                                            credits=env.client.credits, log=env.log)
    else:
        stream = env.stream_factory()
    snaps, events = asyncio.run(_record(stream, minutes))
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
            "clock_offset_ms": [offset, offset_after]}
    write_parquet(folder / f"{tag}_boards.parquet", boards, meta)
    write_parquet(folder / f"{tag}_strikes.parquet", strikes, meta)
    atomic_write_text(folder / f"{tag}_events.jsonl", "".join(json.dumps(e, default=str) + "\n" for e in events))
    summary = an.event_report(events)
    stale = env.settings["skylit_stale_sec"]
    report = {"folder": str(folder), "tag": tag, "boards": boards.num_rows, "events": len(events),
              "resent_boards_dropped": duplicates,
              "clock_offset_ms": [offset, offset_after],
              "latency": an.latency_report(boards), "events_summary": summary,
              "stream_shape": an.stream_shape(boards, strikes),
              "requested_max_strikes": stream.params["maxStrikes"],
              "skylit_stale_sec": stale,
              "ping_gap_within_stale": summary["ping_gap_max"] is not None and summary["ping_gap_max"] < stale}
    env.echo(f"  {boards.num_rows} boards, proposed map_latency_sec (99.9th pct) "
             f"{report['latency']['proposed_map_latency_sec']}, clock offset {offset} ms, "
             f"credits used {summary['credits_used']}, max ping gap {summary['ping_gap_max']} s")
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
        rows, pages, charged = pull.prints, pull.pages, sum(r.charged or 0 for r in pull.records)
    except SkylitError as e:
        if e.status != 404 or e.code not in DP_NO_DATA_CODES:
            raise
        rows, pages, charged = [], 0, 0
    if not rows and env.trading_day(d) and not keep_empty_trading_day:
        env.log.warning("dark pools: no prints on trading day %s: no coverage (not cached)", d)
        return None
    if not rows and env.trading_day(d) and _recent(env, d):
        raise CheckRefused(f"no dark-pool prints for {d} yet (a recent trading day): not cached, try again later")
    if not rows and env.trading_day(d):
        env.log.warning("dark pools: no prints at all on trading day %s (cached as empty)", d)
    write_parquet(path, prints_to_table(rows), {"params": params, "pages": pages, "repull": repull, "charged": charged,
                                                "pulled_at": env.now().isoformat(), "provisional": _recent(env, d)})
    return path


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
    months_mod.require_development(day, env.settings)
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
    probe_todo = int(probe is not None and probe not in dates and not env.store.dp_trades_path(probe).exists())
    tickers = env.features["dp_tickers"]
    tops_todo = sum(1 for x in as_ofs for t in tickers if not env.store.dp_top_path(t, x).exists())
    _gate(env, credits.dark_pool_estimate(f"dark pools: {len(todo)} dates + top-prints cross-check"
                                          + (" + best-effort depth probe" if probe_todo else ""), len(todo) + probe_todo,
                                          _pages_per_date(env), top_prints=tops_todo), approved)
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
    depth = depth_probe_result(env, probe) if probe is not None else None  # last, best-effort (presd_fixes issue 9)
    ledger = [r for r in env.store.ledger() if r["endpoint"] == "/v1/dark-pool/trades" and r.get("charged") is not None]
    with_prints = sorted({p["timestamp"].astimezone(ET).date() for p in prints})
    report = {
        "date": day.isoformat(), "check_dates": [d.isoformat() for d in check_dates], "dates_pulled": len(dates),
        "prints": len(prints), "earliest_print_date": with_prints[0].isoformat() if with_prints else None,
        "depth_probe": depth,
        "credits_per_page": sorted({r["charged"] for r in ledger}), "pages_per_date": round(_pages_per_date(env), 2),
        "top_prints_checks": checks, "all_match": all(c["match"] for c in checks),
        "f4_top": {t: [_fmt(p) for p in v.prints] for t, v in f4.items()},
    }
    env.echo(f"  {len(prints)} prints over {len(dates)} dates; top-prints matches: "
             f"{sum(c['match'] for c in checks)}/{len(checks)}; depth probe {report['depth_probe']}")
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


def check_late_prints_morning(env: Env, approved: int | None = None, force: bool = False) -> dict[str, Any]:
    """Before 09:30: pull the previous session's prints (kept apart) and save today's computed top-3 lists.
    The lists stay in the data tree; the report holds counts only (§7 rule 2b)."""
    now = env.now()
    today = now.date()
    if not force and not (env.trading_day(today) and now.time() < RTH_OPEN):
        raise CheckRefused("run the late-print morning pull before 09:30 ET on a trading day")
    prev = dp.previous_session(today, 1, env.trading_day)
    hist = _history_dates(env, prev)
    todo = [d for d in hist if not env.store.dp_trades_path(d).exists()]
    _gate(env, credits.dark_pool_estimate("late prints: this morning's pull + missing lookback dates", len(todo) + 1,
                                          _pages_per_date(env)), approved)
    files = [pull_dp_date(env, d) for d in hist]
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
        hist = read_prints([env.store.dp_trades_path(d) for d in _history_dates(env, prev)])
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


def warmup_sessions(features: dict[str, Any]) -> int:
    """§3.2: max(20, the longest session lookback in features.yaml)."""
    keys = ("atr_period", "rv_lookback_sessions", "big_trade_lookback_sessions", "ib_avg_sessions", "retest_lookback_sessions")
    return max(20, *(int(features[k]) for k in keys if features.get(k) is not None))


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
    except Exception as e:  # noqa: BLE001 - the report must still be written
        sym = latest_report(env.paths, "symbols")
        if sym is None:
            return None, f"unknown ({type(e).__name__}: {e}; no symbols report yet)", bf.tempest_symbols(None)
        served = {r.get("symbol"): True for r in sym.get("tempest") or []}
        return ((sym.get("account") or {}).get("credits"), f"from the symbols report of {sym.get('written')}",
                bf.tempest_symbols(served))


def backfill_estimate(env: Env) -> dict[str, Any]:
    """Step 10 as a plan made now would pull it: reachable past windows only, lost days listed, the shortfall and
    the cheapest covering pack (§3.1, decision 2 in docs/presd_fixes.md)."""
    from nqbot.clients import skylit_backfill as bf

    balance, source, symbols = _balance_and_symbols(env)
    out = bf.report_estimate(env, balance, symbols)
    out["balance_source"] = source
    return out


REPORT_NAMES = ["symbols", "strike_window", "sample_days", "historical_instants", "stream_record", "parity", "darkpool",
                "late_prints_compare", "tempest_depth", "tempest_compare"]
# SSD-day order (decision 1 in docs/presd_fixes.md, SPEC §12 step 7): what the owner's step-10 OK needs, what runs
# before the report, and what runs alongside step 10 (never holds it up; needed before step 11).
NEEDED_FOR_STEP10 = ("symbols", "strike_window", "sample_days")
BEFORE_REPORT = ("historical_instants", "darkpool", "tempest_depth")
ALONGSIDE = ("stream_record", "parity", "late_prints_compare", "tempest_compare")


def _strike_check(env: Env, latest: dict[str, Any]) -> list[str]:
    sw, sd = latest.get("strike_window"), latest.get("sample_days")
    current = env.settings["skylit_max_strikes"]
    lines = [f"- skylit_max_strikes now: {current}"
             + (f"; proposed by strike-window: {sw.get('proposed_skylit_max_strikes')}" if sw else "")]
    if sd is not None:
        pulled = sd.get("skylit_max_strikes")
        if pulled == current:
            lines.append(f"- sample days pulled with {pulled}: matches")
        else:
            lines.append(f"- sample days pulled with {pulled}, not {current}: run `nqbot step7 sample-days --raised`, "
                         "or the backfill pulls those days again (rule-2 exception)")
    return lines


def check_report(env: Env) -> str:
    from nqbot.clients import skylit_backfill as bf_mod

    latest = {n: latest_report(env.paths, n) for n in REPORT_NAMES}
    missing = [n for n in NEEDED_FOR_STEP10 if latest[n] is None]
    lines = ["# Step 7 parameter check", "", f"Written {env.now():%Y-%m-%d %H:%M} ET.", "",
             f"**Ready for the owner's step-10 OK: {'yes' if not missing else 'no'}**"
             + (f" (missing: {', '.join(missing)})" if missing else ""), ""]
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
        "strike_window": ["per_symbol", "proposed_skylit_max_strikes", "current_skylit_max_strikes"],
        "sample_days": ["per_day", "zero_dte_misses"],
        "historical_instants": ["node_types"],
        "stream_record": ["boards", "clock_offset_ms", "latency", "events_summary", "stream_shape", "ping_gap_within_stale"],
        "parity": ["matched", "n_only_stream", "n_only_history", "n_revised", "STOP_ask_owner", "extra_history", "latency"],
        "darkpool": ["prints", "earliest_print_date", "depth_probe", "credits_per_page", "pages_per_date", "all_match"],
        "late_prints_compare": ["compared", "enough", "any_top_list_changed", "recommend_dp_lag_sessions"],
        "tempest_depth": ["needed_from", "first_row", "covered"],
        "tempest_compare": ["changed_cells"],
    }[name]
    return {k: r.get(k) for k in keep}
