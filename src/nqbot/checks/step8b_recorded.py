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

The report is `<reports_dir>/step8b/recorded_day_<date>_<stamp>.json`; each candidate's per-board errors are kept in
`<reports_dir>/step8b/recorded_days/<date>.npz` for `step8b ndxp-select` (owner 2026-10-08): leave one recorded
day out, pick the candidate with the lowest p95 on the others, score it on the held-out day, rotate; the candidate
with the best held-out p95 wins and that p95 becomes NDXP's `stop_buffer_floor`.
"""

from __future__ import annotations

from datetime import date, timedelta, timezone
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
from nqbot.clients.databento_contracts import cme_day_span
from nqbot.clients.databento_store import read_records
from nqbot.features import mapping as mp
from nqbot.features import ndxp
from nqbot.features.bars import PRICE_SCALE
from nqbot.jobs.lock import DATABENTO, job_locks
from nqbot.storage import atomic_write_bytes

ET = s8.ET
CONTINUOUS = {"NQ": "NQ.v.0", "ES": "ES.v.0"}  # §3.2: the session's front month
QQQ_K_SESSIONS = (5, 10, 20)  # owner 2026-10-08: QQQ x k candidates (k averaged over N previous sessions)
BASIS = "basis+carry"
SELECT_MIN_DAYS = 3  # owner 2026-10-08: leave-one-day-out across the three recordings


def previous_session(d: date, cal: dict[str, Any] | None) -> date:
    p = d - timedelta(days=1)
    while not cal_mod.is_trading_day(p, cal):
        p -= timedelta(days=1)
    return p


# --- 1. Databento ---------------------------------------------------------------------------------------

def pull_trades(env: s8.Env, d: date, with_prev: bool, client: DatabentoClient) -> dict[str, Any]:
    """The day's NQ/ES trades (and the previous session's NQ with with_prev), $0 only; front contracts."""
    start, end = cme_day_span(d)
    avail = client.dataset_end()
    env.echo(f"  Databento has published to {avail.isoformat()}")
    if avail < end.astimezone(timezone.utc):
        raise s8.CheckRefused(f"Databento has not published the whole CME day {d} yet (ends {end.isoformat()}); "
                              "run again later")
    prev = previous_session(d, env.cal)
    front, _ = client.resolve_front(CONTINUOUS, prev, d + timedelta(days=1))
    sym = {root: next(s for d0, d1, s in spans if d0 <= d < d1) for root, spans in front.items()}
    items = [(TRADES, sym["NQ"], d), (TRADES, sym["ES"], d)]
    prev_sym = next(s for d0, d1, s in front["NQ"] if d0 <= prev < d1)
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
    return {"front": sym, "previous_session": prev.isoformat(), "previous_front_nq": prev_sym}


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
    t = pa.concat_tables([pq.read_table(f, columns=["as_of", "symbol", "spot"]) for f in files])
    as_of = s8._ns(t["as_of"])
    sym = t["symbol"].to_pylist()
    seen, keep = set(), []
    for i in np.argsort(as_of, kind="stable"):
        key = (sym[i], int(as_of[i]))
        if key not in seen:
            seen.add(key)
            keep.append(int(i))
    return t.take(pa.array(keep, type=pa.int64()))


def _series(t: pa.Table, name: str, open_ns: int, close_ns: int) -> tuple[np.ndarray, np.ndarray]:
    x = t.filter(pc.equal(t["symbol"], name))
    a = s8._ns(x["as_of"])
    sp = x["spot"].to_numpy().astype(np.float64)
    k = (a >= open_ns) & (a < close_ns) & (sp > 0)
    o = np.argsort(a[k], kind="stable")
    return a[k][o], sp[k][o]


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


def _save_errors(env: s8.Env, d: date, errors: dict[str, np.ndarray]) -> None:
    import io

    buf = io.BytesIO()
    np.savez_compressed(buf, **{k.replace(" ", "_"): v for k, v in errors.items()})
    atomic_write_bytes(_errors_dir(env) / f"{d.isoformat()}.npz", buf.getvalue())


def _load_errors(path: Path) -> dict[str, np.ndarray]:
    z = np.load(path, allow_pickle=False)
    names = {c.replace(" ", "_"): c for c in candidates()}
    return {names[k]: z[k] for k in z.files if k in names}


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
                            "per_day_p95": {str(d): _r(_p95([days[d][c]])) for d in sorted(days)},
                            "picked_in_rotations": sum(r["picked"] == c for r in rotations)}
    winner = min(per_candidate, key=lambda c: per_candidate[c]["held_out_p95"])
    procedure = _p95([days[date.fromisoformat(r["held_out"])][r["picked"]] for r in rotations])
    report = {"days": [str(d) for d in sorted(days)], "candidates": per_candidate, "rotations": rotations,
              "winner": winner, "stop_floor_nq": per_candidate[winner]["held_out_p95"],
              "selection_procedure_held_out_p95": _r(procedure)}
    for c, v in per_candidate.items():
        env.echo(f"  {c}: held-out p95 {v['held_out_p95']} NQ, median {v['held_out_median']:+} "
                 f"(picked in {v['picked_in_rotations']} of {len(rotations)} rotations)")
    env.echo(f"  winner: {winner}; NDXP stop floor {report['stop_floor_nq']} NQ pts (the leave-one-day-out pick "
             f"itself: held-out p95 {report['selection_procedure_held_out_p95']})")
    s8.write_report(env, "ndxp_select", report)
    return report


def _r(x: float | None) -> float | None:
    return None if x is None else round(float(x), 3)


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
    an, nx = _series(boards, "NDXP", open_ns + cfg["ratio_open_skip_sec"] * mp.NS, close_ns)
    off = align.get("NDXP", {}).get("offset_ms")
    if len(an) == 0 or off is None:
        ndxp_out["skipped"] = "no NDXP boards (or no fitted content offset) in the recording"
    else:
        c_n = mp.content_time_ns(an, off)
        f_n = mp.last_price_at_or_before(nq[0], nq[1], c_n)
        methods: dict[str, Any] = {}
        errors: dict[str, np.ndarray] = {}
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
            methods[BASIS] = {**_err_stats(errors[BASIS]), "settle_prev": round(settle, 2),
                              "ndx_close_prev": closes["ndx"][prev]}
        aq, q = _series(boards, "QQQ", open_ns, close_ns)
        q_off = mp.content_offset_ms(cfg, "QQQ", mp.ERA_LIVE)
        if len(aq) == 0 or q_off is None:
            methods["QQQ x k"] = {"skipped": "no QQQ boards in the recording"}
        else:
            c_q = mp.content_time_ns(aq, q_off)
            r_q = mp.last_price_at_or_before(nq[0], nq[1], c_q) / q
            r_in = mp.ratio_in_force(c_q, r_q, cfg["ratio_window_sec"]["QQQ"], cfg["ratio_gap_reset_sec"],
                                     cfg["ratio_outlier_bps"], cfg["ratio_reaccept_n"])
            i = np.searchsorted(c_q, c_n, side="right") - 1  # the QQQ R in force at the NDXP board's content time
            r_at = np.where(i >= 0, r_in[np.clip(i, 0, None)], np.nan)
            for n in QQQ_K_SESSIONS:
                k = ndxp.qqq_k(d, closes["ndx"], closes["qqq"], closes["div"], n)
                key = qqq_key(n)
                if k is None:
                    methods[key] = {"skipped": f"fewer than {n} sessions of NDX and QQQ closes before {d}"}
                else:
                    errors[key] = nx * r_at / k - f_n
                    methods[key] = {**_err_stats(errors[key]), "k": round(k, 6)}
        ndxp_out["methods"] = methods
        for name, v in methods.items():
            env.echo(f"  NDXP {name}: " + (v["skipped"] if "skipped" in v else
                                          f"p95 {v['abs_p95']} NQ pts, median {v['signed_median']:+} (n {v['n']})"))
        _save_errors(env, d, errors)
    report = {"date": d.isoformat(), "databento": pulled, "alignment": align, "ndxp": ndxp_out,
              "alerts": [m for m, v in align.items() if v.get("pass") is False],
              "spy_live_offset_ms": align.get("SPY", {}).get("offset_ms")}
    s8.write_report(env, f"recorded_day_{d.isoformat()}", report)
    return report
