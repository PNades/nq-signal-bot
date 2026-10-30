"""`nqbot step8b recorded-day <date>` (owner 2026-10-08): the step after each NDXP session recording, in one command.

1. Databento (holds the Databento job lock): refuses until Databento has published the whole CME day; prints the
   `get_cost` price of the day's NQ and ES trades and pulls them only at $0 (owner: $0 for each recorded day; a
   cached file is never pulled again). The previous session's NQ trades (for the basis-method settlement) are pulled
   only with `--with-prev` (the owner's OK), else that method waits.
2. Free closes: NDX (FRED) and QQQ with ex-dividend dates (Nasdaq API), fetched once a day.
3. Alignment on the recording: every map's content offset fitted against the futures; alert if one falls after
   floor(asOf) + 1 s. SPY's is the live offset still pending in `board_content_offset_ms` (§5.1).
4. NDXP: both methods (findings R6/R9; `features/ndxp.py`) against the recording's real NDX spot. The error at a
   board is the NQ level the method gives the index's own price minus NQ at the board's content time, in NQ pts.
   Report only (owner 2026-10-11): the same methods' errors at relevant-node crossings (F2.4 on the NDXP board in
   force, the gate's crossing rule): strike x the method's NQ/NDX ratio at t* minus NQ at t*, with count and p95.

The report is `<reports_dir>/step8b/recorded_day_<date>_<stamp>.json`; each candidate's per-board errors are kept in
`<reports_dir>/step8b/recorded_days/<date>.npz` for `step8b ndxp-select` (owner 2026-10-08): leave one recorded
day out, pick the candidate with the lowest p95 on the others, score it on the held-out day, rotate; the candidate
with the best held-out p95 wins and that p95 becomes NDXP's `stop_buffer_floor`.
"""

from __future__ import annotations

import json

from datetime import date, datetime, timedelta, timezone
from datetime import time as time_cls
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from nqbot import calendar as cal_mod
from nqbot.checks import step8b as s8
from nqbot.checks import step8b_gate as gate
from nqbot.clients import ndx_sources
from nqbot.clients.databento_client import DATASET, TRADES, DatabentoClient
from nqbot.clients import databento_volume as vol_mod
from nqbot.clients.databento_contracts import cme_day_span, expiry, unexpired_quarterlies
from nqbot.clients.databento_pull import PlanRefused, decide_front
from nqbot.clients.databento_store import read_records
from nqbot.features import mapping as mp
from nqbot.features import ndxp, nodes
from nqbot.features.bars import PRICE_SCALE
from nqbot.jobs.lock import DATABENTO, job_locks
from nqbot.storage import atomic_write_bytes

ET = s8.ET
UNCOVERED_GAP_PRE_S = 2  # owner 2026-10-10: from just before the resume (its lastAsOf -> currentAsOf step)
QQQ_K_SESSIONS = (5, 10, 20)  # owner 2026-10-08: QQQ x k candidates (k averaged over N previous sessions)
BASIS = "basis+carry"
SELECT_MIN_DAYS = 3  # owner 2026-10-08: leave-one-day-out across the three recordings


def previous_session(d: date, cal: dict[str, Any] | None) -> date:
    p = d - timedelta(days=1)
    while not cal_mod.is_trading_day(p, cal):
        p -= timedelta(days=1)
    return p


# --- 1. Databento ---------------------------------------------------------------------------------------

def chain_days(d: date) -> list[date]:
    """The weekdays from the last quarterly expiry before `d` to `d`: the front rule's chain for a recorded day
    starts there, where the front is unambiguous (the expired contract is gone)."""
    last = max(e for r in unexpired_quarterlies("NQ", d - timedelta(days=100), 2) if (e := expiry(r, "NQ", d)) < d)
    n = (d - last).days
    return [x for x in (last + timedelta(days=i) for i in range(1, n + 1)) if x.weekday() < 5]


def front_contracts(env: s8.Env, client: DatabentoClient, d: date) -> dict[str, dict[date, str]]:
    """{root: {day: raw symbol}} up to `d` by the previous-session volume rule (§3.2, owner 2026-10-30): the same
    `front_by_volume` as the step-8 plan, from the candidates' hourly bars (pulled at $0 if missing, else refused)."""
    days = chain_days(d)
    try:
        vol_mod.pull_volumes(client, env.db, days, cal=env.cal, echo=lambda m: env.echo("  " + m))
        return decide_front(env.db, days, env.cal)[0]
    except (vol_mod.VolumesRefused, PlanRefused) as e:
        raise s8.CheckRefused(str(e)) from e


def pull_trades(env: s8.Env, d: date, with_prev: bool, client: DatabentoClient) -> dict[str, Any]:
    """The day's NQ/ES trades (and the previous session's NQ with with_prev), $0 only; front contracts."""
    start, end = cme_day_span(d)
    avail = client.dataset_end()
    env.echo(f"  Databento has published to {avail.isoformat()}")
    if avail < end.astimezone(timezone.utc):
        raise s8.CheckRefused(f"Databento has not published the whole CME day {d} yet (ends {end.isoformat()}); "
                              "run again later")
    prev = previous_session(d, env.cal)
    front = front_contracts(env, client, d)
    sym = {root: per[d] for root, per in front.items()}
    items = [(TRADES, sym["NQ"], d), (TRADES, sym["ES"], d)]
    prev_sym = front["NQ"][prev]
    if with_prev:
        items.append((TRADES, sym["NQ"], prev))  # the settlement of today's contract (also when it was not front)
    todo = []
    for schema, s, day in items:
        s0, s1 = cme_day_span(day)
        if env.db.cached(schema, s, day, s0, s1, DATASET):
            env.echo(f"  cached, not pulled again: {schema} {s} {day}")
            continue
        usd = client.cost(schema, [s], s0, s1)
        env.echo(f"  cost {schema} {s} {day}: ${usd:.4f}")
        if usd > 0:
            raise s8.CheckRefused(f"{schema} {s} {day} costs ${usd:.4f}; the owner approved $0 only (ask him)")
        todo.append((schema, s, day, s0, s1))
    for schema, s, day, s0, s1 in todo:
        size = client.download(schema, s, s0, s1, env.db.raw_path(schema, s, day), item="step8b_recorded_day",
                               payg=False, day=day.isoformat())
        env.echo(f"  pulled {schema} {s} {day}: {size:,} bytes")
    # every Databento pull records its days' dataset condition and flags a non-available one (owner 2026-10-09, §3.2)
    from nqbot.clients import databento_condition as cond

    first = min(day for _, _, day in items)
    res = cond.record_conditions(client, env.db, first - timedelta(days=1), d, today=env.now().date(),
                                 source=f"step8b recorded-day {d}", echo=lambda m: env.echo("  ALERT: " + m))
    condition = {"flagged": [{"date": r["date"][:10], "condition": r["condition"]} for r in res["flagged"]],
                 "upgrades": res["upgrades"]}
    return {"front": sym, "previous_session": prev.isoformat(), "previous_front_nq": prev_sym,
            "dataset_condition": condition}


def _trades(env: s8.Env, symbol: str, d: date) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    path = env.db.raw_path(TRADES, symbol, d)
    if not path.exists():
        return None
    rec = read_records(path)
    o = np.lexsort((rec["sequence"], rec["ts_event"]))
    return (np.asarray(rec["ts_event"], dtype=np.int64)[o], np.asarray(rec["price"], dtype=np.float64)[o] / PRICE_SCALE,
            np.asarray(rec["size"], dtype=np.float64)[o])


# --- 3. the recording -----------------------------------------------------------------------------------

def recording_boards(env: s8.Env, d: date) -> pa.Table:
    """Every recorded board of the session (stream-record and stream-session layouts), one row per (symbol, asOf)."""
    files = sorted(Path(env.sky.stream_dir(d)).rglob("*_boards.parquet"))
    if not files:
        raise s8.CheckRefused(f"no stream recording for {d} under {env.sky.stream_dir(d)}")
    tables = []
    for f in files:
        x = pq.read_table(f)
        if "arrival" not in x.column_names:  # a board without its arrival time: applied at τ + map_latency_sec
            x = x.append_column("arrival", pa.nulls(x.num_rows, pa.timestamp("ns", "UTC")))
        tables.append(x.select(["as_of", "symbol", "spot", "arrival"]).cast(
            pa.schema([("as_of", pa.timestamp("ms", "UTC")), ("symbol", pa.string()), ("spot", pa.float64()),
                       ("arrival", pa.timestamp("ns", "UTC"))])))
    t = pa.concat_tables(tables)
    as_of = s8._ns(t["as_of"])
    sym = t["symbol"].to_pylist()
    seen, keep = set(), []
    for i in np.argsort(as_of, kind="stable"):
        key = (sym[i], int(as_of[i]))
        if key not in seen:
            seen.add(key)
            keep.append(int(i))
    return t.take(pa.array(keep, type=pa.int64()))


def _series(t: pa.Table, name: str, open_ns: int, close_ns: int, with_arrival: bool = False):
    x = t.filter(pc.equal(t["symbol"], name))
    a = s8._ns(x["as_of"])
    sp = x["spot"].to_numpy().astype(np.float64)
    k = (a >= open_ns) & (a < close_ns) & (sp > 0)
    o = np.argsort(a[k], kind="stable")
    if not with_arrival:
        return a[k][o], sp[k][o]
    arr = pc.cast(pc.fill_null(x["arrival"], pa.scalar(0, pa.timestamp("ns", "UTC"))), pa.int64()).to_numpy()
    return a[k][o], sp[k][o], arr[k][o]  # 0 = no arrival recorded (never later than the apply time)


def _et_ns(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, timezone.utc).astimezone(s8.ET).strftime("%H:%M:%S")


def _err_stats(e: np.ndarray) -> dict[str, Any]:
    e = e[np.isfinite(e)]
    if not len(e):
        return {"n": 0}
    a = np.abs(e)
    return {"n": int(len(e)), "signed_median": round(float(np.median(e)), 3), "abs_p50": round(float(np.median(a)), 3),
            "abs_p95": round(float(np.quantile(a, 0.95)), 3), "abs_max": round(float(a.max()), 3)}


def qqq_key(n: int) -> str:
    return f"QQQ x k (N={n})"


def candidates() -> list[str]:
    return [BASIS] + [qqq_key(n) for n in QQQ_K_SESSIONS]


def _errors_dir(env: s8.Env) -> Path:
    return s8.reports_dir(env.paths) / "recorded_days"


CROSS_PREFIX = "x__"  # crossing errors in the same file (report only, owner 2026-10-11)


TIMES_KEY = "t_ns"  # content time of each scored board (owner 2026-10-18: errors by time of day)


def _save_errors(env: s8.Env, d: date, errors: dict[str, np.ndarray],
                 crossings: dict[str, np.ndarray] | None = None, t_ns: np.ndarray | None = None,
                 x_t_ns: np.ndarray | None = None) -> None:
    import io

    buf = io.BytesIO()
    arrays = {k.replace(" ", "_"): v for k, v in errors.items()}
    arrays.update({CROSS_PREFIX + k.replace(" ", "_"): v for k, v in (crossings or {}).items()})
    if t_ns is not None:
        arrays[TIMES_KEY] = np.asarray(t_ns, dtype=np.int64)
    if x_t_ns is not None:
        arrays[CROSS_PREFIX + TIMES_KEY] = np.asarray(x_t_ns, dtype=np.int64)
    np.savez_compressed(buf, **arrays)
    atomic_write_bytes(_errors_dir(env) / f"{d.isoformat()}.npz", buf.getvalue())


def _load_errors(path: Path, crossings: bool = False) -> dict[str, np.ndarray]:
    """Every-board errors per candidate, or (crossings=True) the relevant-node crossing errors (report only)."""
    z = np.load(path, allow_pickle=False)
    names = {(CROSS_PREFIX if crossings else "") + c.replace(" ", "_"): c for c in candidates()}
    return {names[k]: z[k] for k in z.files if k in names}


def _load_times(path: Path, crossings: bool = False) -> np.ndarray | None:
    """The content times saved with the errors (same order), or None for a file saved before 2026-10-18."""
    z = np.load(path, allow_pickle=False)
    key = (CROSS_PREFIX if crossings else "") + TIMES_KEY
    return z[key] if key in z.files else None


def recording_strikes(env: s8.Env, d: date, name: str, as_of_ns: np.ndarray
                      ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """One map's recorded strike rows joined to its boards (as_of_ns sorted, unique): (board, strike, value), each
    (asOf, strike) once (a board written twice around a resume has its strikes twice)."""
    a_l, k_l, v_l = [], [], []
    for f in sorted(Path(env.sky.stream_dir(d)).rglob("*_strikes.parquet")):
        x = pq.read_table(f, columns=["as_of", "symbol", "strike", "value"])
        x = x.filter(pc.equal(x["symbol"], name))
        a_l.append(s8._ns(x["as_of"]))
        k_l.append(x["strike"].to_numpy().astype(np.float64))
        v_l.append(x["value"].to_numpy().astype(np.float64))
    if not a_l or not len(as_of_ns):
        return np.zeros(0, dtype=np.int64), np.zeros(0), np.zeros(0)
    a, k, v = np.concatenate(a_l), np.concatenate(k_l), np.concatenate(v_l)
    o = np.lexsort((k, a))
    a, k, v = a[o], k[o], v[o]
    first = np.concatenate([[True], (a[1:] != a[:-1]) | (k[1:] != k[:-1])]) if len(a) else np.zeros(0, dtype=bool)
    a, k, v = a[first], k[first], v[first]
    idx = np.clip(np.searchsorted(as_of_ns, a), 0, len(as_of_ns) - 1)
    ok = as_of_ns[idx] == a
    return idx[ok], k[ok], v[ok]


def ndxp_crossings(env: s8.Env, d: date, a_n: np.ndarray, spot: np.ndarray, arrival: np.ndarray, c_n: np.ndarray
                   ) -> tuple[np.ndarray, np.ndarray]:
    """NDXP's relevant-node crossings in a recording (report only, owner 2026-10-11): F2.4 over the session's NDXP
    boards, the gate's crossing rule on the board in force (applied at max(arrival, floor(asOf) + 1 s +
    map_latency_sec)). Returns (t*, strike). Owner 2026-10-14 (live stream drops): a crossing is interpolated only
    between two NDXP boards no more than `skylit_map_stale_sec` apart (a board is never carried across a gap)."""
    cfg = env.features["mapping"]
    bi, k, v = recording_strikes(env, d, "NDXP", a_n)
    pct = mp.pct_of_king(bi, v, len(a_n))
    rel, _ = nodes.relevance(bi, k, pct, np.ones(len(a_n), dtype=bool), env.features["min_node_pct_of_king"],
                             env.features["node_drop_pct"])
    t_apply = mp.apply_times_ns(a_n, "1s", env.settings.get("map_latency_sec"), arrival)
    t, strike, _ = gate.event_crossings(c_n, spot, mp.gap_segments(c_n, _no_carry_s(env)), t_apply, bi[rel], k[rel])
    return t, strike


def _no_carry_s(env: s8.Env) -> float:
    """The longest a board is used after it applied in the recorded-day scores (owner 2026-10-14): the live per-map
    freshness limit `skylit_map_stale_sec` (a map older than that blocks entries live), never more than the R reset."""
    gap = float(env.features["mapping"]["ratio_gap_reset_sec"])
    stale = env.settings.get("skylit_map_stale_sec")
    return min(gap, float(stale)) if stale is not None else gap


# --- leave-one-day-out choice (owner 2026-10-08) --------------------------------------------------------

def _p95(e: list[np.ndarray]) -> float | None:
    x = np.abs(np.concatenate(e)) if e else np.zeros(0)
    x = x[np.isfinite(x)]
    return float(np.quantile(x, 0.95)) if len(x) else None


def check_ndxp_select(env: s8.Env) -> dict[str, Any]:
    """Hold one recorded day out, pick the candidate with the lowest p95 on the others, score the pick on the held-out
    day, rotate. Each candidate's held-out p95 and median pool its errors on each day while that day is held out;
    the best held-out p95 wins and becomes NDXP's stop floor."""
    days = {date.fromisoformat(f.stem): _load_errors(f) for f in sorted(_errors_dir(env).glob("*.npz"))}
    if len(days) < SELECT_MIN_DAYS:
        raise s8.CheckRefused(f"{len(days)} recorded day(s) scored ({', '.join(map(str, days)) or 'none'}); "
                              f"the choice needs {SELECT_MIN_DAYS} (`step8b recorded-day D` after each recording)")
    missing = {str(d): [c for c in candidates() if c not in e] for d, e in days.items()}
    missing = {d: m for d, m in missing.items() if m}
    if missing:
        raise s8.CheckRefused(f"candidates not scored on every day: {missing} (the basis method needs the previous "
                              "session's NQ trades: recorded-day --with-prev, after the owner's OK)")
    rotations = []
    for held in sorted(days):
        p_train = {c: _p95([days[d][c] for d in days if d != held]) for c in candidates()}
        pick = min(p_train, key=lambda c: p_train[c])
        rotations.append({"held_out": held.isoformat(), "train_p95": {c: _r(v) for c, v in p_train.items()},
                          "picked": pick, "held_out_p95": _r(_p95([days[held][pick]]))})
    per_candidate = {}
    for c in candidates():
        e = np.concatenate([days[d][c] for d in sorted(days)])
        e = e[np.isfinite(e)]
        per_candidate[c] = {"held_out_p95": _r(_p95([e])), "held_out_median": _r(float(np.median(e))),
                            "held_out_abs_median": _r(float(np.median(np.abs(e)))),
                            "per_day_p95": {str(d): _r(_p95([days[d][c]])) for d in sorted(days)},
                            "picked_in_rotations": sum(r["picked"] == c for r in rotations)}
    # report only (owner 2026-10-11): each candidate's p95 at relevant-node crossings, pooled over the days
    cross = {date.fromisoformat(f.stem): _load_errors(f, crossings=True) for f in sorted(_errors_dir(env).glob("*.npz"))}
    no_cross = [str(d) for d in sorted(days) if set(cross.get(d, {})) != set(candidates())]
    for c in candidates():
        e = [cross[d][c] for d in sorted(days) if c in cross.get(d, {})]
        per_candidate[c]["crossing_p95"] = None if no_cross else _r(_p95(e))
        xe = np.abs(np.concatenate(e)) if e else np.zeros(0)
        xe = xe[np.isfinite(xe)]
        per_candidate[c]["crossing_abs_median"] = None if no_cross or not len(xe) else _r(float(np.median(xe)))
        per_candidate[c]["crossings_n"] = int(sum(np.isfinite(x).sum() for x in e))
    winner = min(per_candidate, key=lambda c: per_candidate[c]["held_out_p95"])
    procedure = _p95([days[date.fromisoformat(r["held_out"])][r["picked"]] for r in rotations])
    w = per_candidate[winner]
    # owner 2026-10-11: propose the larger of the two, so stops are never tighter than crossings show; owner decides
    proposal = None if w["crossing_p95"] is None else max(w["held_out_p95"], w["crossing_p95"])
    report = {"days": [str(d) for d in sorted(days)], "candidates": per_candidate, "rotations": rotations,
              "winner": winner, "every_board_held_out_p95": w["held_out_p95"], "crossing_p95": w["crossing_p95"],
              "proposed_stop_floor_nq": proposal, "days_without_crossings": no_cross,
              "selection_procedure_held_out_p95": _r(procedure)}
    for c, v in per_candidate.items():
        env.echo(f"  {c}: held-out p95 {v['held_out_p95']} NQ, median {v['held_out_median']:+} "
                 f"(picked in {v['picked_in_rotations']} of {len(rotations)} rotations); at relevant-node crossings "
                 f"p95 {v['crossing_p95']} (n {v['crossings_n']}, report only)")
    if no_cross:
        env.echo(f"  no crossing errors saved for {', '.join(no_cross)}: re-run `step8b recorded-day` for them")
    env.echo(f"  winner: {winner}; proposed NDXP stop floor {proposal} NQ pts = the larger of every-board "
             f"{w['held_out_p95']} and crossings {w['crossing_p95']} (owner decides; the leave-one-day-out pick "
             f"itself: held-out p95 {report['selection_procedure_held_out_p95']})")
    s8.write_report(env, "ndxp_select", report)
    return report


def _r(x: float | None) -> float | None:
    return None if x is None else round(float(x), 3)


ATLAS_RECORDED_MINUTES = 10  # owner 2026-10-19: about 10 times per map per day, spread across the recorded session
ATLAS_RECORDED_EDGE_MIN = 10  # the first time 10 minutes after the open, the last 10 before the close or the end


def atlas_minutes(start: datetime, end: datetime, n: int = ATLAS_RECORDED_MINUTES) -> list[time_cls]:
    """n whole minutes spread evenly from start + 10 min to end - 10 min (fixed by the clock only, never chosen from
    prices: §7 rule 2b's step-8b exception, owner 2026-10-19)."""
    a = start + timedelta(minutes=ATLAS_RECORDED_EDGE_MIN)
    b = end - timedelta(minutes=ATLAS_RECORDED_EDGE_MIN)
    step = (b - a) / (n - 1)
    return [(a + step * i).replace(second=0, microsecond=0).timetz().replace(tzinfo=None) for i in range(n)]


def check_atlas_recorded(env: s8.Env, d: date, minutes: list[time_cls] | None = None) -> dict[str, Any]:
    """The Atlas tables (QQQ, SPY) on a recorded session day (owner 2026-10-19, §7 rule 2b's step-8b exception,
    §13 Q-atlas-oct): the recorded boards with their arrival times and the live content offsets, the futures from
    `recorded-day`'s $0 trades. Levels, times and mapping errors only. Default: 10 minutes spread over the recorded
    part of the session. Written as `atlas_recorded_<date>.md` / report `atlas_recorded_<date>`, apart from the
    sample days' regression check."""
    reports = sorted(s8.reports_dir(env.paths).glob(f"recorded_day_{d.isoformat()}_*.json"))
    if not reports:
        raise s8.CheckRefused(f"run `step8b recorded-day {d}` first (it pulls the day's trades and fits the offsets)")
    front = json.loads(reports[-1].read_text(encoding="utf-8"))["databento"]["front"]
    open_ns, close_ns = s8._session_ns(env, d)
    boards = recording_boards(env, d)
    arr_all = pc.cast(pc.fill_null(boards["arrival"], pa.scalar(0, pa.timestamp("ns", "UTC"))), pa.int64()).to_numpy()
    last = int(arr_all[(arr_all > 0) & (arr_all < close_ns)].max()) if (arr_all > 0).any() else close_ns
    if minutes is None:
        tz = s8.ET
        minutes = atlas_minutes(datetime.fromtimestamp(open_ns / 1e9, tz), datetime.fromtimestamp(min(last, close_ns) / 1e9, tz))
    boards = boards.append_column("resolution", pa.array(["1s"] * boards.num_rows))
    maps, futs, arrivals = {}, {}, {}
    for m in s8.ATLAS_MAPS:
        root = s8.CHAIN_ROOT[m]
        tr = _trades(env, front[root], d)
        if tr is None:
            raise s8.CheckRefused(f"{root} trades for {d} missing: run `step8b recorded-day {d}`")
        futs[root] = s8.Futures(tr[0], tr[1], front[root])
        a, k, v = _recording_strike_rows(env, d, m)
        strikes = pa.table({"as_of": pa.array(a // 1_000_000, pa.timestamp("ms", "UTC")), "symbol": [m] * len(a),
                            "strike": pa.array(k, pa.float32()), "value": pa.array(v, pa.float32())})
        maps[m] = s8.map_day(env, d, m, boards, strikes, futs[root])
        x = boards.filter(pc.equal(boards["symbol"], m))
        o = np.argsort(s8._ns(x["as_of"]), kind="stable")
        arrivals[m] = pc.cast(pc.fill_null(x["arrival"], pa.scalar(0, pa.timestamp("ns", "UTC"))), pa.int64()).to_numpy()[o]
    note = (f"**Recorded session (owner 2026-10-19, §7 rule 2b step-8b exception):** the live stream's boards as "
            f"recorded (applied at max(arrival, floor(asOf) + 1 s + map_latency_sec)), the live content offsets, the "
            f"day's Databento futures; the recording ends {_et_ns(last)} ET. Minutes fixed by the clock, not by prices.")
    # owner 2026-10-22: NDXP and SPX as a light spot check, levels and times only
    tz = s8.ET
    spot_minutes = atlas_minutes(datetime.fromtimestamp(open_ns / 1e9, tz),
                                 datetime.fromtimestamp(min(last, close_ns) / 1e9, tz), ATLAS_SPOT_MINUTES)
    ndxp_rows = ndxp_spot_rows(env, d, boards, futs["NQ"], maps["QQQ"], arrivals["QQQ"], spot_minutes)
    spx_rows, spx_source = spx_spot_rows(env, d, boards, futs["ES"], spot_minutes)
    spot = {"NDXP": ndxp_rows, "SPX": spx_rows}
    extra_lines = spot_check_lines(env, spot, spx_source)
    path = s8.reports_dir(env.paths) / f"atlas_recorded_{d.isoformat()}.md"
    return s8.atlas_tables(env, d, minutes, maps, futs, era=mp.ERA_LIVE, arrivals=arrivals,
                           name=f"atlas_recorded_{d.isoformat()}", md_path=path, note=note,
                           extra_lines=extra_lines, extra={"spot_checks": spot, "spx_source": spx_source})


ATLAS_SPOT_MINUTES = 3  # owner 2026-10-22: NDXP and SPX spot checks at the open, midday and near the end


def _spot_nodes(env: s8.Env, md: s8.MapDay, i: int, r: float) -> list[dict[str, Any]]:
    """Board i's relevant nodes (F2.4) with their mapped level and band at R r (levels only, no crossings)."""
    rel_rows, held_rows = s8.node_relevance(env, md)
    rel = (md.s_board == i) & rel_rows
    band = env.features["node_band"][md.name]
    out = []
    for k, pct, held in sorted(zip(md.s_strike[rel], md.s_pct[rel], held_rows[rel])):
        pct_r = float(np.floor(pct * 1e4) / 1e4) if held else round(float(pct), 4)
        out.append({"strike": float(k), "pct_of_king": pct_r, "held": bool(held), "level": round(k * r, 2),
                    "band": [round((k - band) * r, 2), round((k + band) * r, 2)]})
    return out


def _at_ns(d: date, minute: time_cls) -> int:
    return int(datetime.combine(d, minute, tzinfo=s8.ET).timestamp()) * mp.NS


def _last_applied(t_apply: np.ndarray, applied: np.ndarray, at: int) -> int | None:
    A = np.flatnonzero(applied)
    p = int(np.searchsorted(t_apply[A], at, side="right")) - 1
    return None if p < 0 else int(A[p])


def ndxp_spot_rows(env: s8.Env, d: date, boards: pa.Table, fut: s8.Futures, qqq: s8.MapDay, qqq_arrival: np.ndarray,
                   minutes: list[time_cls]) -> list[dict[str, Any]]:
    """NDXP at each minute: the recorded board in force and its relevant nodes mapped with the frozen method
    (QQQ x k, §13 Q8b-frozen): R_NDXP = the QQQ R in force then / k, k from `recorded-day`'s report."""
    cfg = env.features["mapping"]
    n = cfg["ndxp_k_sessions"]
    rep = json.loads(sorted(s8.reports_dir(env.paths).glob(f"recorded_day_{d.isoformat()}_*.json"))[-1]
                     .read_text(encoding="utf-8"))
    k = rep.get("ndxp", {}).get("methods", {}).get(qqq_key(n), {}).get("k")
    if k is None:
        return [{"minute": m.strftime("%H:%M"), "board": None, "why": f"no k (N = {n}) in recorded-day's report"}
                for m in minutes]
    a, kk, v = _recording_strike_rows(env, d, "NDXP")
    strikes = pa.table({"as_of": pa.array(a // 1_000_000, pa.timestamp("ms", "UTC")), "symbol": ["NDXP"] * len(a),
                        "strike": pa.array(kk, pa.float32()), "value": pa.array(v, pa.float32())})
    md = s8.map_day(env, d, "NDXP", boards, strikes, fut)
    x = boards.filter(pc.equal(boards["symbol"], "NDXP"))
    o = np.argsort(s8._ns(x["as_of"]), kind="stable")
    arr = pc.cast(pc.fill_null(x["arrival"], pa.scalar(0, pa.timestamp("ns", "UTC"))), pa.int64()).to_numpy()[o]
    latency = env.settings.get("map_latency_sec")
    t_apply = mp.apply_times_ns(md.as_of, np.array(md.resolution, dtype=object), latency, arr)
    _, q_apply, q_r = s8.map_ratio_in_force(env, d, qqq, fut, mp.ERA_LIVE, qqq_arrival)
    no_carry = int(_no_carry_s(env) * mp.NS)
    rows = []
    for m in minutes:
        at = _at_ns(d, m)
        i, j = _last_applied(t_apply, md.applied, at), _last_applied(q_apply, qqq.applied, at)
        if i is None or j is None or at - t_apply[i] > no_carry or at - q_apply[j] > no_carry:
            rows.append({"minute": m.strftime("%H:%M"), "board": None,
                         "why": "no NDXP or QQQ board applied in the last "
                                f"{_no_carry_s(env):g} s (a connection stretch)"})
            continue
        r = float(q_r[j]) / k
        rows.append({"minute": m.strftime("%H:%M"), "board": s8._et(md.as_of[i]), "spot": float(md.spot[i]),
                     "r": round(r, 6), "r_basis": f"QQQ board {s8._et(qqq.as_of[j])} R {q_r[j]:.6f} / k {k}",
                     "nodes": _spot_nodes(env, md, i, r)})
    return rows


def spx_spot_rows(env: s8.Env, d: date, boards: pa.Table, fut: s8.Futures, minutes: list[time_cls]
                  ) -> tuple[list[dict[str, Any]], str]:
    """SPX at each minute (per-board R, F1.2). The session recordings carry NDXP, QQQ and SPY only, so SPX comes
    from the recording if it has SPX boards, else from the day's stored history windows (the step-7 parity /
    stream-drops pulls, 15 minutes each): one minute per window, 10 minutes into it (fixed by the clock)."""
    if pc.any(pc.equal(boards["symbol"], "SPX")).as_py():
        a, kk, v = _recording_strike_rows(env, d, "SPX")
        strikes = pa.table({"as_of": pa.array(a // 1_000_000, pa.timestamp("ms", "UTC")), "symbol": ["SPX"] * len(a),
                            "strike": pa.array(kk, pa.float32()), "value": pa.array(v, pa.float32())})
        x = boards.filter(pc.equal(boards["symbol"], "SPX"))
        o = np.argsort(s8._ns(x["as_of"]), kind="stable")
        arr = pc.cast(pc.fill_null(x["arrival"], pa.scalar(0, pa.timestamp("ns", "UTC"))), pa.int64()).to_numpy()[o]
        sets = [(boards, strikes, mp.ERA_LIVE, arr, minutes)]
        source = "the session recording"
    else:
        sets = []
        for base in sorted((env.sky.root / "repull" / "parity").glob(f"{d.isoformat()}_*")):
            b, s = env.sky.read_range(d, base, symbols=["SPX"])
            if b.num_rows == 0:
                continue
            first = datetime.fromtimestamp(int(s8._ns(b["as_of"]).min()) / 1e9, s8.ET).replace(second=0, microsecond=0)
            m = (first + timedelta(minutes=ATLAS_RECORDED_EDGE_MIN)).time()
            sets.append((b, s, None, None, [m]))
        source = ("the day's stored history windows (step-7 parity / stream-drops pulls; SPX is not in the "
                  "recordings)" if sets else "none: SPX is not in the recordings and no history window is stored")
    rows = []
    for b, s, era, arr, mins in sets:
        md = s8.map_day(env, d, "SPX", b, s, fut)
        _, t_apply, r_in = s8.map_ratio_in_force(env, d, md, fut, era, arr)
        no_carry = int(_no_carry_s(env) * mp.NS)
        for m in mins:
            at = _at_ns(d, m)
            i = _last_applied(t_apply, md.applied, at)
            if i is None or at - t_apply[i] > no_carry or not np.isfinite(r_in[i]):
                rows.append({"minute": m.strftime("%H:%M"), "board": None,
                             "why": f"no SPX board applied in the last {_no_carry_s(env):g} s"})
                continue
            rows.append({"minute": m.strftime("%H:%M"), "board": s8._et(md.as_of[i]), "spot": float(md.spot[i]),
                         "r": round(float(r_in[i]), 6), "nodes": _spot_nodes(env, md, i, float(r_in[i]))})
    return rows, source


def spot_check_lines(env: s8.Env, spot: dict[str, list[dict[str, Any]]], spx_source: str) -> list[str]:
    lines = ["## Spot checks: NDXP and SPX (owner 2026-10-22; levels and times only)", "",
             "A few minutes per map to compare in Atlas: the board in force, its relevant nodes (F2.4) and their "
             "mapped futures level and band. No crossings and no tolerance: these maps are not scored here. NDXP: "
             "the recorded boards, R = the QQQ R in force / k (QQQ x k, N = "
             f"{env.features['mapping']['ndxp_k_sessions']}, frozen); SPX: per-board R (F1.2), from {spx_source}.", ""]
    for m, rows in spot.items():
        root = s8.CHAIN_ROOT[m]
        lines += [f"## {m} -> {root} (spot check)", ""]
        if not rows:
            lines += ["No minute available.", ""]
        for row in rows:
            if row["board"] is None:
                lines += [f"### {row['minute']}: {row['why']}", ""]
                continue
            lines += [f"### {row['minute']} (board {row['board']}, spot {row['spot']}, R {row['r']})", ""]
            if row.get("r_basis"):
                lines += [f"R from the {row['r_basis']}.", ""]
            lines += [f"| Strike | % of King | Mapped {root} | Band |", "|---:|---:|---:|---|"]
            for n in row["nodes"]:
                pct_txt = (f"{np.floor(n['pct_of_king'] * 1000) / 10:.1f}% held" if n["held"]
                           else f"{n['pct_of_king']:.0%}")
                lines.append(f"| {n['strike']:g} | {pct_txt} | {n['level']:.2f} | {n['band'][0]:.2f}-{n['band'][1]:.2f} |")
            lines.append("")
    return lines


def _recording_strike_rows(env: s8.Env, d: date, name: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """One map's recorded strike rows (asOf ns, strike, value), each (asOf, strike) once."""
    a_l, k_l, v_l = [], [], []
    for f in sorted(Path(env.sky.stream_dir(d)).rglob("*_strikes.parquet")):
        x = pq.read_table(f, columns=["as_of", "symbol", "strike", "value"], filters=[("symbol", "=", name)])
        a_l.append(s8._ns(x["as_of"]))
        k_l.append(x["strike"].to_numpy().astype(np.float64))
        v_l.append(x["value"].to_numpy().astype(np.float64))
    if not a_l:
        return np.zeros(0, dtype=np.int64), np.zeros(0), np.zeros(0)
    a, k, v = np.concatenate(a_l), np.concatenate(k_l), np.concatenate(v_l)
    o = np.lexsort((k, a))
    a, k, v = a[o], k[o], v[o]
    first = np.concatenate([[True], (a[1:] != a[:-1]) | (k[1:] != k[:-1])]) if len(a) else np.zeros(0, dtype=bool)
    return a[first], k[first], v[first]


def uncovered_gaps(env: s8.Env, d: date) -> list[dict[str, Any]]:
    """Every resume the stream reported with gapCovered=False (its events files), with the symbols it advanced."""
    out = []
    for f in sorted(Path(env.sky.stream_dir(d)).rglob("*_events.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            e = json.loads(line)
            data = e.get("data")
            if e.get("kind") == "resumed" and isinstance(data, dict) and data.get("gapCovered") is False:
                out.append({"arrival_ns": int(e["arrival_ns"]),
                            "advanced": [x["symbol"] for x in data.get("symbols", [])
                                         if isinstance(x, dict) and x.get("status") != "unchanged"]})
    return out


def gap_mask(t_ns: np.ndarray, gaps: list[dict[str, Any]], post_s: float) -> np.ndarray:
    """True for boards to keep: outside [resume - UNCOVERED_GAP_PRE_S, resume + post_s] of every uncovered gap."""
    keep = np.ones(len(t_ns), dtype=bool)
    for g in gaps:
        keep &= ~((t_ns >= g["arrival_ns"] - UNCOVERED_GAP_PRE_S * mp.NS) & (t_ns <= g["arrival_ns"] + int(post_s * mp.NS)))
    return keep


def connection_stretches(boards: pa.Table, open_ns: int, close_ns: int, stale_s: float,
                         to_close: bool = False) -> list[dict[str, int]]:
    """Owner 2026-10-18 (10-16: the owner's Wi-Fi went out): stretches in the session when no recorded map's board
    arrived for more than `skylit_map_stale_sec`, i.e. the connection, not one map, went quiet. Each runs from the
    last arrival before it to the first after it. A single map's drops (Q-mapstale) are not stretches.
    to_close (owner 2026-10-19: 10-19's connection went quiet at 15:45 and never came back before the close): the
    recording ran to the close, so silence from the last arrival to the close is a stretch too (a recording that
    stopped early ends at its last board instead)."""
    if "arrival" not in boards.column_names:
        return []
    arr = pc.cast(pc.fill_null(boards["arrival"], pa.scalar(0, pa.timestamp("ns", "UTC"))), pa.int64()).to_numpy()
    arr = np.unique(arr[(arr >= open_ns) & (arr < close_ns)])
    if len(arr) == 0:
        return []
    i = np.where(np.diff(arr) > int(stale_s * mp.NS))[0]
    out = [{"from_ns": int(arr[j]), "to_ns": int(arr[j + 1])} for j in i]
    if to_close and close_ns - int(arr[-1]) > int(stale_s * mp.NS):
        out.append({"from_ns": int(arr[-1]), "to_ns": int(close_ns)})
    return out


def ran_to_close(env: s8.Env, d: date) -> bool:
    """True if the session log says the recording ended at the close (`session ended ... (close, ...)`)."""
    log = env.paths.log_dir / f"stream_session_{d:%Y%m%d}.txt"
    if not log.exists():
        return False
    return any(line.startswith("session ended ") and "(close" in line
               for line in log.read_text(encoding="utf-8", errors="replace").splitlines())


def stretch_mask(t_ns: np.ndarray, stretches: list[dict[str, int]], post_s: float) -> np.ndarray:
    """True for boards to keep: outside [stretch start - UNCOVERED_GAP_PRE_S, stretch end + post_s] of every
    connection stretch, the uncovered-gap window around the whole stretch (owner 2026-10-18)."""
    keep = np.ones(len(t_ns), dtype=bool)
    for g in stretches:
        keep &= ~((t_ns >= g["from_ns"] - UNCOVERED_GAP_PRE_S * mp.NS) & (t_ns <= g["to_ns"] + int(post_s * mp.NS)))
    return keep


def reconnects(env: s8.Env, d: date) -> list[dict[str, Any]]:
    """Every reconnect of the recording (owner 2026-10-18): Skylit's `reconnect` / `resumed` events, and the
    connection attempts that failed (from the session log, `stream connection N failed: ...`)."""
    out = []
    for f in sorted(Path(env.sky.stream_dir(d)).rglob("*_events.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            e = json.loads(line)
            data = e.get("data") if isinstance(e.get("data"), dict) else {}
            if e.get("kind") == "reconnect":
                out.append({"at_et": _et_ns(int(e["arrival_ns"])), "kind": "reconnect", "reason": data.get("reason")})
            elif e.get("kind") == "resumed":
                out.append({"at_et": _et_ns(int(e["arrival_ns"])), "kind": "resumed",
                            "gap_covered": data.get("gapCovered")})
    log = env.paths.log_dir / f"stream_session_{d:%Y%m%d}.txt"
    if log.exists():
        import re

        pat = re.compile(r"^(\S+Z) .*stream connection (\d+) failed: (.*)$")
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            m = pat.match(line)
            if m:
                t = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
                out.append({"at_et": t.astimezone(s8.ET).strftime("%H:%M:%S"), "kind": "failed",
                            "connection": int(m.group(2)), "error": m.group(3).strip()})
    return sorted(out, key=lambda x: x["at_et"])


# --- the command ----------------------------------------------------------------------------------------

def check_recorded_day(env: s8.Env, d: date, *, with_prev: bool = False,
                       client_factory: Callable[[], DatabentoClient] | None = None,
                       fetch: ndx_sources.Fetch = ndx_sources.http_get) -> dict[str, Any]:
    if not cal_mod.is_trading_day(d, env.cal):
        raise s8.CheckRefused(f"{d} is not a trading day")
    boards = recording_boards(env, d)
    with job_locks(env.paths.root, (DATABENTO,), "step8b recorded-day"):
        client = client_factory() if client_factory else DatabentoClient.from_env(on_call=env.db.log_call)
        pulled = pull_trades(env, d, with_prev, client)
    nq, es = _trades(env, pulled["front"]["NQ"], d), _trades(env, pulled["front"]["ES"], d)
    if nq is None or es is None:
        raise s8.CheckRefused(f"{d}: NQ/ES trades missing after the pull")
    fut = {"NQ": nq, "ES": es}
    open_ns, close_ns = s8._session_ns(env, d)
    cfg = env.features["mapping"]

    # alignment on the recording
    align: dict[str, Any] = {}
    for m in s8.MAPS:
        a, sp = _series(boards, m, open_ns, close_ns)
        if len(a) < gate.ALIGN_MIN_PAIRS:
            align[m] = {"boards": int(len(a)), "offset_ms": None, "pass": None}
            continue
        ts, px, _ = fut[s8.CHAIN_ROOT[m]]
        align[m] = {**gate.fit_content_offset(a, sp, ts, px), "configured_live_ms": mp.content_offset_ms(cfg, m, mp.ERA_LIVE)}
        flag = "" if align[m]["pass"] else "  ALERT: content after floor(asOf) + 1 s"
        env.echo(f"  {m}: content offset {align[m]['offset_ms']} ms (configured live "
                 f"{align[m]['configured_live_ms']}){flag}")

    # NDXP: both methods against the real NDX
    closes = ndx_sources.fetch_all(env.paths, env.now().date(), fetch)
    prev = date.fromisoformat(pulled["previous_session"])
    sym = pulled["front"]["NQ"]
    ndxp_out: dict[str, Any] = {"previous_session": prev.isoformat(), "contract": sym, "sources": closes["folder"]}
    an, nx, arr_n = _series(boards, "NDXP", open_ns + cfg["ratio_open_skip_sec"] * mp.NS, close_ns,
                            with_arrival=True)
    off = align.get("NDXP", {}).get("offset_ms")
    if len(an) == 0 or off is None:
        ndxp_out["skipped"] = "no NDXP boards (or no fitted content offset) in the recording"
    else:
        c_n = mp.content_time_ns(an, off)
        f_n = mp.last_price_at_or_before(nq[0], nq[1], c_n)
        # owner 2026-10-10: boards near a resume with gapCovered=False are left out: from 2 s before it to the
        # longest trailing R window after it (a missed board would sit in QQQ's 60-s median that long)
        gaps = uncovered_gaps(env, d)
        post_s = max(v for v in cfg["ratio_window_sec"].values() if v is not None)
        keep_gap = gap_mask(c_n, gaps, post_s)
        # owner 2026-10-18: the connection's silent stretches (Wi-Fi) are left out the same way
        stretches = connection_stretches(boards, open_ns, close_ns, float(env.settings["skylit_map_stale_sec"]),
                                         to_close=ran_to_close(env, d))
        keep_str = stretch_mask(c_n, stretches, post_s)
        keep = keep_gap & keep_str
        f_n = np.where(keep, f_n, np.nan)
        ndxp_out["uncovered_gaps"] = {"resumes": [{"at_et": _et_ns(g["arrival_ns"]), "advanced": g["advanced"]} for g in gaps],
                                      "window_s": [-UNCOVERED_GAP_PRE_S, post_s], "boards_left_out": int((~keep_gap).sum())}
        ndxp_out["connection_stretches"] = {
            "stretches": [{"from_et": _et_ns(g["from_ns"]), "to_et": _et_ns(g["to_ns"]),
                           "length_s": round((g["to_ns"] - g["from_ns"]) / mp.NS, 1)} for g in stretches],
            "window_s": [-UNCOVERED_GAP_PRE_S, post_s], "boards_left_out": int((keep_gap & ~keep_str).sum())}
        methods: dict[str, Any] = {}
        errors: dict[str, np.ndarray] = {}
        ratio_at: dict[str, Callable[[np.ndarray], np.ndarray]] = {}  # each method's NQ/NDX ratio at any instant
        prev_trades = _trades(env, sym, prev)
        prev_close_ns = s8._session_ns(env, prev)[1]
        settle = (ndxp.settlement_vwap(*prev_trades, prev_close_ns) if prev_trades is not None else None)
        if prev not in closes["ndx"]:
            methods[BASIS] = {"skipped": f"FRED has no NDX close for {prev} yet"}
        elif settle is None:
            methods[BASIS] = {"skipped": f"no {sym} trades for {prev} (pull with --with-prev after the owner's OK)"}
        else:
            rho = ndxp.basis_ratio(c_n, sym, settle, closes["ndx"][prev], prev_close_ns)
            errors[BASIS] = nx * rho - f_n
            ratio_at[BASIS] = lambda t, s=settle, c=closes["ndx"][prev]: ndxp.basis_ratio(t, sym, s, c, prev_close_ns)
            methods[BASIS] = {**_err_stats(errors[BASIS]), "settle_prev": round(settle, 2),
                              "ndx_close_prev": closes["ndx"][prev]}
        aq, q, arr_q = _series(boards, "QQQ", open_ns, close_ns, with_arrival=True)
        q_off = mp.content_offset_ms(cfg, "QQQ", mp.ERA_LIVE)
        if len(aq) == 0 or q_off is None:
            methods["QQQ x k"] = {"skipped": "no QQQ boards in the recording"}
        else:
            c_q = mp.content_time_ns(aq, q_off)
            r_q = mp.last_price_at_or_before(nq[0], nq[1], c_q) / q
            r_in = mp.ratio_in_force(c_q, r_q, cfg["ratio_window_sec"]["QQQ"], cfg["ratio_gap_reset_sec"],
                                     cfg["ratio_outlier_bps"], cfg["ratio_reaccept_n"])
            # the QQQ R the bot had at the NDXP board's content time: the last QQQ board applied by then,
            # max(arrival, floor(asOf) + 1 s + map_latency_sec) (F0.1, owner 2026-10-09: same rule as the gate)
            t_apply_q = mp.apply_times_ns(aq, "1s", env.settings.get("map_latency_sec"), arr_q)
            no_carry_ns = int(_no_carry_s(env) * mp.NS)

            def qqq_r_at(t: np.ndarray) -> np.ndarray:  # the QQQ R in force at t; none once its board is too old
                j = mp.in_force_index(t_apply_q, t)
                jj = np.clip(j, 0, None)
                ok = (j >= 0) & (np.asarray(t, dtype=np.int64) - t_apply_q[jj] <= no_carry_ns)
                return np.where(ok, r_in[jj], np.nan)
            r_at = qqq_r_at(c_n)
            for n in QQQ_K_SESSIONS:
                k = ndxp.qqq_k(d, closes["ndx"], closes["qqq"], closes["div"], n)
                key = qqq_key(n)
                if k is None:
                    methods[key] = {"skipped": f"fewer than {n} sessions of NDX and QQQ closes before {d}"}
                else:
                    errors[key] = nx * r_at / k - f_n
                    ratio_at[key] = lambda t, k=k: qqq_r_at(t) / k
                    methods[key] = {**_err_stats(errors[key]), "k": round(k, 6)}
        # report only (owner 2026-10-11): the same methods at relevant-node crossings
        t_x, k_x = ndxp_crossings(env, d, an, nx, arr_n, c_n)
        keep_x = gap_mask(t_x, gaps, post_s) & stretch_mask(t_x, stretches, post_s)
        t_x, k_x = t_x[keep_x], k_x[keep_x]
        f_x = mp.last_price_at_or_before(nq[0], nq[1], t_x)
        cross_err = {c: k_x * fn(t_x) - f_x for c, fn in ratio_at.items()}
        for c, e in cross_err.items():
            methods[c]["crossings"] = _err_stats(e)
        ndxp_out["crossings"] = {"n": int(len(t_x)), "left_out_near_gaps": int((~keep_x).sum()), "report_only": True}
        ndxp_out["boards"] = {"recorded": int(len(an)), "no_carry_s": _no_carry_s(env),
                              "scored": {c: int(np.isfinite(e).sum()) for c, e in errors.items()}}
        ndxp_out["methods"] = methods
        for name, v in methods.items():
            x = v.get("crossings", {})
            env.echo(f"  NDXP {name}: " + (v["skipped"] if "skipped" in v else
                                          f"p95 {v['abs_p95']} NQ pts, median {v['signed_median']:+} (n {v['n']}); "
                                          f"at relevant-node crossings p95 {x.get('abs_p95')} (n {x.get('n', 0)}, "
                                          "report only)"))
        _save_errors(env, d, errors, cross_err, c_n, t_x)
    events = reconnects(env, d)
    for e in events:
        env.echo("  reconnect log: " + ", ".join(f"{k} {v}" for k, v in e.items()))
    for g in ndxp_out.get("connection_stretches", {}).get("stretches", []):
        env.echo(f"  connection stretch left out: {g['from_et']} -> {g['to_et']} ({g['length_s']} s)")
    report = {"date": d.isoformat(), "databento": pulled, "alignment": align, "ndxp": ndxp_out, "reconnects": events,
              "alerts": [m for m, v in align.items() if v.get("pass") is False],
              "spy_live_offset_ms": align.get("SPY", {}).get("offset_ms")}
    s8.write_report(env, f"recorded_day_{d.isoformat()}", report)
    return report
