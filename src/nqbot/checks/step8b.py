"""Step-8b gate: level-mapping precision (SPEC §5.1, §12 step 8b, feature_spec F1.6). Reads stored data only.

Uses the step-7 Skylit sample days (development month 6) and their step-8 NQ/ES trades (the Databento plan pulls
those days first, so this can run after the first night):

- `precision`: at every applied, accepted board, |k * R_prev - k * R_cur| for each relevant node (F2.4: >= 25% of
  King to become relevant, kept until < 20%),
  per chain as median / p95 / p99 / max, by time of day and by trailing 15-minute realized-vol tercile, next to
  mapping_p95_max. A diagnostic since the owner's Q8b-4 answer (2026-10-08), not a gate condition. Also the F1.2
  rejection rate (frequent rejections: find the cause, never raise the threshold) and the stop floors per level
  source (`stop_buffer_floor`, §5.1).
- `spot-lag`: cross-correlation of 1-second `spot` changes with futures trade-price changes over +-120 s; a
  diagnostic (the alignment check replaces it in the gate). On a cached 1-minute day it answers §3.1 (is `asOf`
  the board's true time, or keep the +60 s?).
- `atlas`: for minutes the owner picks, the QQQ/SPY strikes the bot treats as relevant nodes (strike, % of King,
  mapped level and band) and the moments spot crossed each strike, with the NQ/ES price then next to the mapped
  level. The owner checks the same strikes in Atlas replay (the gate condition, owner 2026-10-08 Q8b-5); the
  share of crossings within atlas_match_tol is a regression check (alert below atlas_regression_min_share).
- `report`: <reports_dir>/step8b/mapping_gate.md, the gate evidence (Atlas pending until the owner confirms).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from nqbot import calendar as cal_mod
from nqbot import months as months_mod
from nqbot.checks.step7_analysis import quantile
from nqbot.clients.databento_client import TRADES
from nqbot.clients.databento_pull import plan_path
from nqbot.clients.databento_store import DatabentoStore, read_records
from nqbot.clients.skylit_pull import MAPS, sample_days
from nqbot.clients.skylit_store import SkylitStore
from nqbot.features import mapping as mp
from nqbot.features import nodes
from nqbot.features.bars import PRICE_SCALE
from nqbot.storage import Paths, atomic_write_json, atomic_write_text

ET = ZoneInfo("America/New_York")
RTH_OPEN, RTH_CLOSE = time(9, 30), time(16, 0)
CHAIN_ROOT = {"QQQ": "NQ", "NDXP": "NQ", "SPY": "ES", "SPX": "ES"}  # §5.1
ATLAS_MAPS = ("QQQ", "SPY")  # §5.1 Atlas match: the owner reads Atlas on QQQ and SPY
ATLAS_CROSS_WINDOW_MIN = 30  # crossings listed after each picked minute (step 8b; owner kept it 2026-10-06, §13)
TOD_BUCKET_MIN = 30  # time-of-day buckets of the precision report (step 8b; owner kept it 2026-10-06, §13)
SPOT_LAG_PASS_S = (0, 1)  # §12 step 8b: the peak at 0-1 s on the 1-second sample days
VOL_TERCILES = ("low", "mid", "high")
RAISE = "strike_window_raise"

log_default = logging.getLogger("nqbot.step8b")


class CheckRefused(Exception):
    pass


@dataclass
class Env:
    settings: dict[str, Any]
    features: dict[str, Any]
    paths: Paths
    log: logging.Logger = log_default
    now: Callable[[], datetime] = lambda: datetime.now(ET)
    echo: Callable[[str], None] = print
    cal: dict[str, Any] | None = None
    _sky: SkylitStore | None = field(default=None, repr=False)
    _db: DatabentoStore | None = field(default=None, repr=False)

    @property
    def sky(self) -> SkylitStore:
        if self._sky is None:
            self._sky = SkylitStore(self.paths)
        return self._sky

    @property
    def db(self) -> DatabentoStore:
        if self._db is None:
            self._db = DatabentoStore(self.paths)
        return self._db

    def close_of(self, d: date) -> time:
        return cal_mod.early_close_et(d, self.cal) or RTH_CLOSE


# --- reports --------------------------------------------------------------------------------------------

def reports_dir(paths: Paths) -> Path:
    return paths.reports_dir / "step8b"


def write_report(env: Env, name: str, report: dict[str, Any]) -> Path:
    path = reports_dir(env.paths) / f"{name}_{env.now().astimezone(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    atomic_write_json(path, {"check": name, "written": env.now().isoformat(), **report})
    env.echo(f"report: {path}")
    return path


def latest_report(paths: Paths, name: str) -> dict[str, Any] | None:
    files = sorted(reports_dir(paths).glob(f"{name}_*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


# --- inputs ---------------------------------------------------------------------------------------------

def days_to_check(env: Env, dates: list[date] | None) -> list[date]:
    days = sorted(dates) if dates else sorted(sample_days(env.paths))
    if not days:
        raise CheckRefused("no step-7 sample days yet (nqbot step7 sample-days writes them)")
    for d in days:
        months_mod.require_development(d, env.settings)  # §7 rule 2b
    return days


def skylit_base(env: Env, d: date) -> Path | None:
    """The boards to read: the strike_window_raise copy when skylit_max_strikes was raised after the sample days."""
    raised = env.sky.base(RAISE, pulled=f"max{env.settings['skylit_max_strikes']}")
    main, raise_ = env.sky.stored_windows(d), env.sky.stored_windows(d, raised)
    if not raise_:
        return None
    if main and set(main) != set(raise_):  # step 10 is moving that day's windows (replace_raised_sample_day)
        raise CheckRefused(f"{d}: the step-10 backfill is replacing this sample day's windows; run again after it")
    return raised


def front_symbols(env: Env) -> dict[str, dict[str, str]]:
    path = plan_path(env.paths)
    if not path.exists():
        raise CheckRefused("no Databento plan yet (nqbot databento plan): the session's front contracts are unknown")
    return json.loads(path.read_text(encoding="utf-8"))["front"]


@dataclass
class Futures:
    ts: np.ndarray  # ns, sorted by (ts_event, sequence)
    px: np.ndarray
    symbol: str


def load_futures(env: Env, root: str, d: date, front: dict[str, dict[str, str]]) -> Futures:
    symbol = (front.get(root) or {}).get(d.isoformat())
    path = env.db.raw_path(TRADES, symbol, d) if symbol else None
    if path is None or not path.exists():
        raise CheckRefused(f"{root} trades for {d} not pulled yet ({path or 'no front contract in the plan'}): "
                           "the Databento backfill pulls the sample days first")
    rec = read_records(path)
    order = np.lexsort((rec["sequence"], rec["ts_event"]))  # F0.1: same ts_event in exchange sequence order
    return Futures(np.asarray(rec["ts_event"], dtype=np.int64)[order],
                   np.asarray(rec["price"], dtype=np.float64)[order] / PRICE_SCALE, symbol)


@dataclass
class MapDay:
    """One map's boards of one session, in time order, with their strikes."""

    name: str
    as_of: np.ndarray  # ns
    tau: np.ndarray  # ns (F0.1 effective time)
    spot: np.ndarray
    resolution: list[str]
    r: np.ndarray  # F(tau) / spot (NaN: not applied)
    applied: np.ndarray  # passes the open rules and has a futures price at tau
    accepted: np.ndarray  # F1.2
    rejected: int
    shift_day: bool
    s_board: np.ndarray  # strike rows: board number
    s_strike: np.ndarray
    s_value: np.ndarray
    s_pct: np.ndarray  # % of King on its board


def _ns(col: pa.ChunkedArray) -> np.ndarray:
    return pc.cast(col, pa.timestamp("ns", "UTC")).cast(pa.int64()).to_numpy()


def map_day(env: Env, d: date, name: str, boards: pa.Table, strikes: pa.Table, fut: Futures) -> MapDay:
    cfg = env.features["mapping"]
    b = boards.filter(pc.equal(boards["symbol"], name))
    as_of = _ns(b["as_of"])
    order = np.argsort(as_of, kind="stable")
    as_of = as_of[order]
    spot = b["spot"].to_numpy()[order].astype(np.float64)
    res = [b["resolution"].to_pylist()[i] for i in order]
    tau = mp.board_tau_ns(as_of, np.array(res, dtype=object))
    open_ns, close_ns = _session_ns(env, d)
    f = mp.last_price_at_or_before(fut.ts, fut.px, tau)
    # the session's boards only: from the open (F0.1, F1.3) to the close (an early close's probe window is left out)
    applied = (mp.open_skip_mask(as_of, name, open_ns, cfg["ratio_open_skip_sec"]) & (as_of < close_ns)
               & np.isfinite(f) & (spot > 0))
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(applied, f / spot, np.nan)
    accepted, rejected, shifted = mp.accept_ratios(r, cfg["ratio_outlier_bps"], cfg["ratio_reaccept_n"])
    s = strikes.filter(pc.equal(strikes["symbol"], name))
    s_as_of = _ns(s["as_of"])
    if len(as_of):  # join strikes to their board on (symbol, as_of)
        idx = np.clip(np.searchsorted(as_of, s_as_of), 0, len(as_of) - 1)
        ok = as_of[idx] == s_as_of
    else:
        idx, ok = np.zeros(len(s_as_of), dtype=np.int64), np.zeros(len(s_as_of), dtype=bool)
    s_board = idx[ok]
    s_strike = s["strike"].to_numpy().astype(np.float64)[ok]
    s_value = s["value"].to_numpy().astype(np.float64)[ok]
    pct = mp.pct_of_king(s_board, s_value, len(as_of))
    return MapDay(name, as_of, tau, spot, res, r, applied, accepted, rejected, shifted, s_board, s_strike, s_value, pct)


def node_relevance(env: Env, md: MapDay) -> tuple[np.ndarray, np.ndarray]:
    """F2.4 (owner 2026-10-11, §5.2): (relevant, held by the 20% hold) per strike row, over the session's applied
    boards."""
    return nodes.relevance(md.s_board, md.s_strike, md.s_pct, md.applied, env.features["min_node_pct_of_king"],
                           env.features["node_drop_pct"])


def _day_inputs(env: Env, d: date, front: dict[str, dict[str, str]]) -> tuple[dict[str, MapDay], dict[str, Futures]]:
    boards, strikes = env.sky.read_range(d, skylit_base(env, d))
    if boards.num_rows == 0:
        raise CheckRefused(f"no Skylit boards stored for {d}")
    futs = {root: load_futures(env, root, d, front) for root in sorted(set(CHAIN_ROOT.values()))}
    return {m: map_day(env, d, m, boards, strikes, futs[CHAIN_ROOT[m]]) for m in MAPS}, futs


def day_futures(env: Env, d: date, front: dict[str, dict[str, str]]) -> dict[str, Futures]:
    """The session's NQ and ES trades, after checking boards are stored (the same refusals as `_day_inputs`)."""
    if not env.sky.stored_windows(d, skylit_base(env, d)):
        raise CheckRefused(f"no Skylit boards stored for {d}")
    return {root: load_futures(env, root, d, front) for root in sorted(set(CHAIN_ROOT.values()))}


def iter_day_maps(env: Env, d: date, names: list[str] | tuple[str, ...], futs: dict[str, Futures]):
    """Low memory (§11): one map's boards and strikes read and built at a time; the caller drops each before the next.
    The same MapDay as `_day_inputs` (map_day filters on the symbol either way)."""
    base = skylit_base(env, d)
    for m in names:
        boards, strikes = env.sky.read_range(d, base, symbols=[m])
        md = map_day(env, d, m, boards, strikes, futs[CHAIN_ROOT[m]])
        del boards, strikes
        yield m, md
        del md


def _session_ns(env: Env, d: date) -> tuple[int, int]:
    return (int(datetime.combine(d, RTH_OPEN, tzinfo=ET).timestamp()) * mp.NS,
            int(datetime.combine(d, env.close_of(d), tzinfo=ET).timestamp()) * mp.NS)


# --- precision ------------------------------------------------------------------------------------------

def stats(values: np.ndarray | list[float]) -> dict[str, Any]:
    v = np.asarray(values, dtype=np.float64)
    if len(v) == 0:
        return {"n": 0, "median": None, "p95": None, "p99": None, "max": None}
    return {"n": int(len(v)), "median": round(quantile(v, 0.5), 4), "p95": round(quantile(v, 0.95), 4),
            "p99": round(quantile(v, 0.99), 4), "max": round(float(v.max()), 4)}


def _tod_label(tau_ns: np.ndarray, d: date) -> np.ndarray:
    open_ns = int(datetime.combine(d, RTH_OPEN, tzinfo=ET).timestamp()) * mp.NS
    bucket = np.maximum(0, (tau_ns - open_ns) // (TOD_BUCKET_MIN * 60 * mp.NS))
    start = datetime.combine(d, RTH_OPEN)
    return np.array([(start + timedelta(minutes=int(k) * TOD_BUCKET_MIN)).strftime("%H:%M") for k in bucket],
                    dtype=object)


def check_precision(env: Env, dates: list[date] | None = None) -> dict[str, Any]:
    days = days_to_check(env, dates)
    front = front_symbols(env)
    acc: dict[str, dict[str, list[Any]]] = {m: {"err": [], "tod": [], "rv": []} for m in MAPS}
    counts: dict[str, dict[str, Any]] = {m: {"boards": 0, "applied": 0, "rejected": 0, "shift_days": [],
                                             "resolutions": {}} for m in MAPS}
    for d in days:
        maps, futs = _day_inputs(env, d, front)
        start, end = _session_ns(env, d)
        for m, md in maps.items():
            relevant = node_relevance(env, md)[0]
            err, rows = mp.step_errors(md.r, md.accepted, md.applied, md.s_board, md.s_strike, relevant)
            fut = futs[CHAIN_ROOT[m]]
            rv = mp.trailing_rv(fut.ts, fut.px, md.tau[rows], start, end)
            acc[m]["err"].append(err)
            acc[m]["tod"].append(_tod_label(md.tau[rows], d))
            acc[m]["rv"].append(rv)
            c = counts[m]
            c["boards"] += len(md.as_of)
            c["applied"] += int(md.applied.sum())
            c["rejected"] += md.rejected
            if md.shift_day:
                c["shift_days"].append(d.isoformat())
            for res in md.resolution:
                c["resolutions"][res] = c["resolutions"].get(res, 0) + 1
    p95_max = env.features["mapping"]["mapping_p95_max"]
    per_chain: dict[str, Any] = {}
    for m in MAPS:
        err = np.concatenate(acc[m]["err"]) if acc[m]["err"] else np.array([])
        tod = np.concatenate(acc[m]["tod"]) if acc[m]["tod"] else np.array([], dtype=object)
        rv = np.concatenate(acc[m]["rv"]) if acc[m]["rv"] else np.array([])
        by_vol: dict[str, Any] = {}
        finite = np.isfinite(rv)
        if finite.sum() >= len(VOL_TERCILES):
            cuts = np.quantile(rv[finite], [1 / 3, 2 / 3])
            label = np.where(~finite, "n/a", np.where(rv <= cuts[0], "low", np.where(rv <= cuts[1], "mid", "high")))
            by_vol = {t: stats(err[label == t]) for t in (*VOL_TERCILES, "n/a") if (label == t).any()}
            by_vol["cuts"] = [float(c) for c in cuts]
        overall = stats(err)
        c = counts[m]
        per_chain[m] = {
            "unit": f"{CHAIN_ROOT[m]} pts", "overall": overall,
            "by_time": {t: stats(err[tod == t]) for t in sorted(set(tod.tolist()))}, "by_vol": by_vol,
            "boards": c["boards"], "applied": c["applied"], "rejected": c["rejected"],
            "rejection_rate": round(c["rejected"] / c["applied"], 5) if c["applied"] else None,
            "ratio_shift_days": c["shift_days"], "resolutions": c["resolutions"],
            "p95_max": p95_max[m],
            "within_p95_max": overall["p95"] is not None and overall["p95"] <= p95_max[m],
        }
    floors = env.settings["stop_buffer_floor"]
    report = {"days": [d.isoformat() for d in days], "per_chain": per_chain, "diagnostic": True,
              "all_within_p95_max": all(c["within_p95_max"] for c in per_chain.values()),
              "stop_buffer_floor": floors, "stop_buffer_setting": env.settings["stop_buffer"]}
    env.echo("  F1.6 step change (diagnostic, not a gate condition):")
    for m in MAPS:
        o = per_chain[m]
        env.echo(f"  {m}: p95 {o['overall']['p95']} {o['unit']} (mapping_p95_max {o['p95_max']}: "
                 f"{'within' if o['within_p95_max'] else 'above'}); rejected {o['rejected']}/{o['applied']} updates")
    env.echo(f"  stop floors per level source (stop_buffer_floor, NQ pts): {_floors_text(floors)}")
    write_report(env, "precision", report)
    return report


# --- spot lag -------------------------------------------------------------------------------------------

def check_spot_lag(env: Env, dates: list[date] | None = None) -> dict[str, Any]:
    days = days_to_check(env, dates)
    front = front_symbols(env)
    per_day: dict[str, Any] = {}
    for d in days:
        maps, futs = _day_inputs(env, d, front)
        start, end = _session_ns(env, d)
        out = {}
        for m, md in maps.items():
            fut = futs[CHAIN_ROOT[m]]
            res = sorted(set(md.resolution))
            lag = mp.spot_lag(md.as_of, md.spot, fut.ts, fut.px, start, end)  # asOf itself: is it the true time?
            one_second = res == ["1s"]
            peak = lag["peak_lag_s"]
            out[m] = {**lag, "resolution": res, "gate": one_second,
                      "pass": (SPOT_LAG_PASS_S[0] <= peak <= SPOT_LAG_PASS_S[1]) if one_second and peak is not None
                      else None}
            if not one_second and peak is not None:  # §3.1: the 1-minute repeat only decides the +60 s
                out[m]["recommendation"] = ("asOf is the board's true time: clear the +60 s"
                                            if SPOT_LAG_PASS_S[0] <= peak <= SPOT_LAG_PASS_S[1]
                                            else "keep tau = asOf + 60 s")
        per_day[d.isoformat()] = out
        env.echo(f"  {d}: " + ", ".join(f"{m} peak {o['peak_lag_s']} s" for m, o in out.items()))
    gated = [o["pass"] for o in (x for day in per_day.values() for x in day.values()) if o["gate"]]
    report = {"days": [d.isoformat() for d in days], "per_day": per_day, "max_lag_s": mp.SPOT_LAG_MAX_S,
              "pass_window_s": list(SPOT_LAG_PASS_S), "pass": bool(gated) and all(gated)}
    write_report(env, "spot_lag", report)
    return report


# --- Atlas side by side ---------------------------------------------------------------------------------

def map_ratio_in_force(env: Env, d: date, md: MapDay, fut: Futures) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The approved rule (owner 2026-10-08, §5.1, F0.1, F1.1, F1.7) for one map and session: each board's content
    time, its apply time floor(asOf) + 1 s + map_latency_sec (0 until set), and the R in force after it (NaN where
    not applied)."""
    cfg = env.features["mapping"]
    era = mp.board_era(d, cfg["skylit_stamp_change_date"])
    off, window = mp.content_offset_ms(cfg, md.name, era), cfg["ratio_window_sec"][md.name]
    if off is None or window is None:
        raise CheckRefused(f"{md.name}: board_content_offset_ms[{era}] or ratio_window_sec not set (pending)")
    tc = mp.content_time_ns(md.as_of, off)
    t_apply = mp.apply_times_ns(md.as_of, np.array(md.resolution, dtype=object), env.settings.get("map_latency_sec"))
    A = np.flatnonzero(md.applied)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = mp.last_price_at_or_before(fut.ts, fut.px, tc[A]) / md.spot[A]
    r_in = np.full(len(md.as_of), np.nan)
    r_in[A] = mp.ratio_in_force(tc[A], r, window, cfg["ratio_gap_reset_sec"], cfg["ratio_outlier_bps"],
                                cfg["ratio_reaccept_n"])
    return tc, t_apply, r_in


def check_atlas(env: Env, d: date, minutes: list[time]) -> dict[str, Any]:
    days_to_check(env, [d])
    front = front_symbols(env)
    maps, futs = _day_inputs(env, d, front)
    min_pct, drop_pct = env.features["min_node_pct_of_king"], env.features["node_drop_pct"]
    tol = env.features["mapping"]["atlas_match_tol"]
    bands = env.features["node_band"]
    out: dict[str, Any] = {}
    lines = [f"# Atlas side-by-side, {d} (§5.1)", "",
             "For each minute: the board in force, the strikes the bot treats as relevant nodes (F2.4, §5.2: a "
             f"node becomes relevant at >= {min_pct:.0%} of its board's King and stays relevant until it drops below "
             f"{drop_pct:.0%}, fresh each session), their mapped futures level and band, and the moments spot crossed "
             f"each strike in the next {ATLAS_CROSS_WINDOW_MIN} minutes with the futures price then (a crossing counts "
             "only while the node is still relevant on the board in force then). Check in Atlas replay that the same "
             f"strikes show as nodes of similar size. **held** = listed only because of the {drop_pct:.0%} hold (now "
             f"under {min_pct:.0%} of King).", ""]
    latency = env.settings.get("map_latency_sec")
    lines += [f"Approved ingest rule and R estimators (owner 2026-10-08, §5.1, F1.7): a board's futures price is read at "
              "its content time, the board applies at floor(asOf) + 1 s + map_latency_sec "
              f"({latency if latency is not None else 'not set yet: 0 used'} s), and the R in force is the map's "
              "estimator (QQQ/SPY trailing 60-s median, SPX per board). At each crossing the mapped level uses the "
              "R the bot had then (the last board applied before it).", ""]
    for m in ATLAS_MAPS:
        md, fut, root = maps[m], futs[CHAIN_ROOT[m]], CHAIN_ROOT[m]
        tc, t_apply, r_in = map_ratio_in_force(env, d, md, fut)
        A = np.flatnonzero(md.applied)
        rel_rows, held_rows = node_relevance(env, md)
        ro = np.flatnonzero(rel_rows)
        ro = ro[np.lexsort((md.s_strike[ro], md.s_board[ro]))]
        rb_sorted = md.s_board[ro]

        def relevant_on(board: int, k: float) -> bool:  # k is a relevant node on that board (F2.4)
            lo, hi = np.searchsorted(rb_sorted, board), np.searchsorted(rb_sorted, board, side="right")
            return bool(np.any(md.s_strike[ro[lo:hi]] == k))

        rows_m = []
        for minute in minutes:
            at = int(datetime.combine(d, minute, tzinfo=ET).timestamp()) * mp.NS
            pa_ = int(np.searchsorted(t_apply[A], at, side="right")) - 1  # the last board applied by then
            if pa_ < 0:
                rows_m.append({"minute": minute.strftime("%H:%M"), "board": None})
                continue
            i = int(A[pa_])
            rel = (md.s_board == i) & rel_rows
            r_i = r_in[i]
            window_end = at + ATLAS_CROSS_WINDOW_MIN * 60 * mp.NS
            nodes = []
            for k, pct, held in sorted(zip(md.s_strike[rel], md.s_pct[rel], held_rows[rel])):
                level = k * r_i
                crossings = []
                q0 = int(np.searchsorted(tc[A], at, side="right"))
                for q in range(max(q0, 1), len(A)):
                    j0, j = int(A[q - 1]), int(A[q])
                    if tc[j] > window_end:
                        break
                    a, b = md.spot[j0] - k, md.spot[j] - k
                    # a spot landing exactly on the strike is one touch, counted on its arrival (b == 0); the pair
                    # that leaves it (a == 0) is not a second crossing
                    if a * b > 0 or a == 0:
                        continue
                    # the moment spot traded at the strike, interpolated between the two boards' content times
                    t_k = int(tc[j0] + (a / (a - b)) * (tc[j] - tc[j0])) if a != b else int(tc[j])
                    # the bot's level then: the R in force from the last board applied before t_k
                    pk = int(np.searchsorted(t_apply[A], t_k, side="right")) - 1
                    if pk < 0 or not relevant_on(int(A[pk]), k):
                        continue  # no map yet, or no longer a relevant node on the map in force (as in the gate)
                    mapped = k * r_in[A[pk]]
                    f_then = float(mp.last_price_at_or_before(fut.ts, fut.px, np.array([t_k]))[0])
                    if not (np.isfinite(mapped) and np.isfinite(f_then)):
                        continue
                    gated = md.resolution[j] == "1s" and md.resolution[j0] == "1s"  # 1-minute: information only
                    crossings.append({"at": _et(t_k), "futures": round(f_then, 2), "mapped": round(mapped, 2),
                                      "diff": round(abs(f_then - mapped), 2), "gate": gated,
                                      "within_tol": bool(abs(f_then - mapped) <= tol[root])})
                # a held node is under 25% of King by definition: its % is rounded down, so 24.999% never reads as 25%
                pct_r = float(np.floor(pct * 1e4) / 1e4) if held else round(float(pct), 4)
                nodes.append({"strike": float(k), "pct_of_king": pct_r, "held": bool(held),
                              "level": round(level, 2),
                              "band": [round((k - bands[m]) * r_i, 2), round((k + bands[m]) * r_i, 2)],
                              "crossings": crossings})
            rows_m.append({"minute": minute.strftime("%H:%M"), "board": _et(md.as_of[i]), "spot": float(md.spot[i]),
                           "r": round(float(r_i), 6), "nodes": nodes})
        out[m] = rows_m
        lines += [f"## {m} -> {root} (tolerance {tol[root]} {root} pts)", ""]
        for row in rows_m:
            if row["board"] is None:
                lines += [f"### {row['minute']}: no accepted board yet", ""]
                continue
            lines += [f"### {row['minute']} (board {row['board']}, spot {row['spot']}, R {row['r']})", "",
                      f"| Strike | % of King | Mapped {root} | Band | Crossed at | {root} then | Diff | OK |",
                      "|---:|---:|---:|---|---|---:|---:|---|"]
            for n in row["nodes"]:
                pct_txt = (f"{np.floor(n['pct_of_king'] * 1000) / 10:.1f}% held" if n.get("held")
                           else f"{n['pct_of_king']:.0%}")
                head = f"| {n['strike']:g} | {pct_txt} | {n['level']:.2f} | {n['band'][0]:.2f}-{n['band'][1]:.2f}"
                if not n["crossings"]:
                    lines.append(head + " | - | - | - | - |")
                for c in n["crossings"]:
                    lines.append(head + f" | {c['at']} | {c['futures']:.2f} | {c['diff']:.2f} | "
                                        f"{'yes' if c['within_tol'] else 'NO'} |")
            lines.append("")
    crossings = [c for rows in out.values() for row in rows for n in row.get("nodes", []) for c in n["crossings"]]
    gated = [c for c in crossings if c["gate"]]
    counts, share = {}, {}
    for m, rows in out.items():
        g = [c for row in rows for n in row.get("nodes", []) for c in n["crossings"] if c["gate"]]
        counts[m] = [int(sum(c["within_tol"] for c in g)), len(g)]
        share[m] = round(counts[m][0] / len(g), 4) if g else None  # information only (judged pooled, owner Q8b-5)
    report = {"date": d.isoformat(), "minutes": [t.strftime("%H:%M") for t in minutes], "maps": out,
              "crossings": len(crossings), "gated_crossings": len(gated), "tolerance": dict(tol),
              "within_tol_counts": counts, "share_within_tol": share, "owner_check": "pending"}
    md_path = reports_dir(env.paths) / f"atlas_{d.isoformat()}.md"
    atomic_write_text(md_path, "\n".join(lines) + "\n")
    env.echo(f"  {len(crossings)} crossings; share within atlas_match_tol (information; the regression check pools "
             "every sample day in `step8b report`) "
             + ", ".join(f"{m} {v:.1%}" if v is not None else f"{m} n/a" for m, v in share.items())
             + f"; table: {md_path}")
    write_report(env, "atlas", report)
    return report


def atlas_pooled_share(reports: list[dict[str, Any]]) -> dict[str, tuple[int, int]]:
    """Owner Q8b-5 (2026-10-08): crossings within atlas_match_tol and gated crossings per map, pooled over days."""
    out: dict[str, tuple[int, int]] = {}
    for rep in reports:
        for m, (w, n) in rep["within_tol_counts"].items():
            a, b = out.get(m, (0, 0))
            out[m] = (a + w, b + n)
    return out


def tol_text(env: Env, root: str) -> str:
    return f"{env.features['mapping']['atlas_match_tol'][root]} {root}"


def _et(ns: int) -> str:
    return datetime.fromtimestamp(int(ns) / mp.NS, tz=timezone.utc).astimezone(ET).strftime("%H:%M:%S")


# --- gate report ----------------------------------------------------------------------------------------

def latest_atlas_by_date(paths: Paths) -> dict[str, dict[str, Any]]:
    """The latest atlas report of each date (a later run of the same date replaces the earlier one)."""
    out: dict[str, dict[str, Any]] = {}
    for f in sorted(reports_dir(paths).glob("atlas_*.json")):
        rep = json.loads(f.read_text(encoding="utf-8"))
        out[rep["date"]] = rep
    return out


def _floors_text(floors: dict[str, Any]) -> str:
    return ", ".join(f"{k} {'pending' if v is None else v}" for k, v in floors.items())


def check_report(env: Env) -> Path:
    prec, lag = (latest_report(env.paths, n) for n in ("precision", "spot_lag"))
    try:  # §12 step 8b: every sample day has its own Atlas table
        atlas_days = [d.isoformat() for d in days_to_check(env, None)]
    except CheckRefused:
        atlas_days = []
    atlas_by_date = latest_atlas_by_date(env.paths)
    lines = ["# Step-8b gate: level-mapping precision (§5.1, §12 step 8b)", ""]
    if prec is None:
        lines += ["- Precision: **pending** (`nqbot step8b precision`)"]
    elif "diagnostic" not in prec:  # written before the owner's Q8b-4 answer (2026-10-08)
        lines += ["- Precision: **out of date** (written before the Q8b-4 answer): run `nqbot step8b precision` again"]
    else:
        lines += [f"- F1.6 step change ({', '.join(prec['days'])}): diagnostic, not a gate condition (owner, Q8b-4)", "",
                  "| Chain | p95 | mapping_p95_max | median | p99 | max | rejected / applied | within |",
                  "|---|---:|---:|---:|---:|---:|---|---|"]
        for m, c in prec["per_chain"].items():
            o = c["overall"]
            lines.append(f"| {m} ({c['unit']}) | {o['p95']} | {c['p95_max']} | {o['median']} | {o['p99']} | {o['max']} | "
                         f"{c['rejected']} / {c['applied']} | {'yes' if c['within_p95_max'] else 'no'} |")
        lines += ["", f"- Stop floors per level source (`stop_buffer_floor`, NQ pts, §5.1): "
                      f"{_floors_text(prec['stop_buffer_floor'])} (`stop_buffer` {prec['stop_buffer_setting']})"]
    if lag is None:
        lines += ["- Spot lag: **pending** (`nqbot step8b spot-lag`)"]
    else:
        lines += [f"- Spot lag (diagnostic; the alignment check replaces it in the gate): peak within "
                  f"{lag['pass_window_s'][0]}-{lag['pass_window_s'][1]} s on the 1-second days: {lag['pass']}"]
        for day, per in lag["per_day"].items():
            lines.append(f"  - {day}: " + ", ".join(f"{m} {o['peak_lag_s']} s" + ("" if o["gate"] else f" (1-minute: "
                                                                                     f"{o.get('recommendation')})")
                                                    for m, o in per.items()))
    tables = bool(atlas_days) and all("within_tol_counts" in atlas_by_date.get(d, {}) for d in atlas_days)
    min_share = env.features["mapping"]["atlas_regression_min_share"]
    lines += ["- **Atlas (gate condition): the owner's check in Atlas replay: pending** (owner 2026-10-08, Q8b-5: the "
              "same strikes show as nodes of similar size)"]
    if tables:
        pooled = atlas_pooled_share([atlas_by_date[d] for d in atlas_days])
        alert = [m for m, (w, n) in pooled.items() if n and w / n < min_share]
        lines.append("- Atlas regression check (information only; owner Q8b-5): share of crossings within the frozen "
                     f"atlas_match_tol (QQQ {tol_text(env, 'NQ')}, SPY {tol_text(env, 'ES')}), pooled over every "
                     f"sample day, alert below {min_share:.0%}: "
                     + ", ".join(f"{m} {w / n:.1%} of {n}" if n else f"{m} n/a" for m, (w, n) in pooled.items())
                     + (f": **REGRESSION ALERT** ({', '.join(alert)})" if alert else ": ok"))
    else:
        lines.append("- Atlas regression check (information only): pooled over every sample day once each has a table")
    for d in atlas_days:
        a = atlas_by_date.get(d)
        if a is None or "within_tol_counts" not in a:
            lines.append(f"  - {d}: **pending** (`nqbot step8b atlas --date {d} --minutes HH:MM,...`)")
            continue
        shares = ", ".join(f"{m} {v:.1%}" if v is not None else f"{m} n/a" for m, v in a["share_within_tol"].items())
        lines.append(f"  - {d} ({', '.join(a['minutes'])}): {shares} of {a['gated_crossings']} gated crossings "
                     f"(this day, information only) (table: atlas_{d}.md)")
    conditions = {}
    for name, title, cmd in (("bias", "(a) bias: 95% day-block interval contains 0 or lies within one futures tick",
                              "bias"),
                             ("estimator", "(b) the chosen estimator's crossing p95 within 10% of the best possible R",
                              "estimator"),
                             ("alignment", "alignment: fitted content time never after floor(asOf) + 1 s", "alignment")):
        rep = latest_report(env.paths, name)
        conditions[name] = bool(rep and rep["pass"])
        if rep is None:
            lines.append(f"- {title}: **pending** (`nqbot step8b {cmd} --dates ...`)")
            continue
        lines.append(f"- {title} ({len(rep['days'])} days): **{'PASS' if rep['pass'] else 'FAIL'}** "
                     "(NDXP pending its method, §13 Q8b-1)")
        for m, c in rep["per_chain"].items():
            if name == "bias":
                detail = f"median {c['median']} [{c['interval'][0]}, {c['interval'][1]}] {c['unit']}, n {c['n']}"
            elif name == "estimator":
                detail = f"{c['estimator']} p95 {c['chosen_p95']} vs best {c['best_p95']} {c['unit']}"
            else:
                detail = f"latest content {c['max_offset_ms']} ms after floor(asOf); alerts {c['alerts'] or 'none'}"
            lines.append(f"  - {m}: {detail}: {'pass' if c['pass'] else 'FAIL'}")
    gate = all(conditions.values()) and tables
    lines += ["", f"**Gate 8b: {'conditions met; the owner checks Atlas replay' if gate else 'not passed yet'}.** It "
                  "must pass before step 11. If a chain fails, find the cause (stale spot, wrong second, slow map "
                  "cadence) before anything else; never raise a threshold."]
    path = reports_dir(env.paths) / "mapping_gate.md"
    atomic_write_text(path, "\n".join(lines) + "\n")
    env.echo(f"report: {path}")
    return path
