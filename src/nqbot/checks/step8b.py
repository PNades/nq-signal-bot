"""Step-8b gate: level-mapping precision (SPEC §5.1, §12 step 8b, feature_spec F1.6). Reads stored data only.

Uses the step-7 Skylit sample days (development month 6) and their step-8 NQ/ES trades (the Databento plan pulls
those days first, so this can run after the first night):

- `precision`: at every applied, accepted board, |k * R_prev - k * R_cur| for each relevant node (>= 25% of King),
  per chain as median / p95 / p99 / max, by time of day and by trailing 15-minute realized-vol tercile, next to
  mapping_p95_max. A diagnostic since the owner's Q8b-4 answer (2026-10-08), not a gate condition. Also the F1.2
  rejection rate (frequent rejections: find the cause, never raise the threshold) and the stop floors per level
  source (`stop_buffer_floor`, §5.1).
- `spot-lag`: cross-correlation of 1-second `spot` changes with futures trade-price changes over +-120 s; a
  diagnostic (the alignment check replaces it in the gate). On a cached 1-minute day it answers §3.1 (is `asOf`
  the board's true time, or keep the +60 s?).
- `atlas`: for minutes the owner picks, the QQQ/SPY strikes the bot treats as relevant nodes (strike, % of King,
  mapped level and band) and the moments spot crossed each strike, with the NQ/ES price then next to the mapped
  level (within atlas_match_tol). The owner checks the same strikes in Atlas replay.
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


def _day_inputs(env: Env, d: date, front: dict[str, dict[str, str]]) -> tuple[dict[str, MapDay], dict[str, Futures]]:
    boards, strikes = env.sky.read_range(d, skylit_base(env, d))
    if boards.num_rows == 0:
        raise CheckRefused(f"no Skylit boards stored for {d}")
    futs = {root: load_futures(env, root, d, front) for root in sorted(set(CHAIN_ROOT.values()))}
    return {m: map_day(env, d, m, boards, strikes, futs[CHAIN_ROOT[m]]) for m in MAPS}, futs


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
    min_pct = env.features["min_node_pct_of_king"]
    acc: dict[str, dict[str, list[Any]]] = {m: {"err": [], "tod": [], "rv": []} for m in MAPS}
    counts: dict[str, dict[str, Any]] = {m: {"boards": 0, "applied": 0, "rejected": 0, "shift_days": [],
                                             "resolutions": {}} for m in MAPS}
    for d in days:
        maps, futs = _day_inputs(env, d, front)
        start, end = _session_ns(env, d)
        for m, md in maps.items():
            relevant = md.s_pct >= min_pct
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

def check_atlas(env: Env, d: date, minutes: list[time]) -> dict[str, Any]:
    days_to_check(env, [d])
    front = front_symbols(env)
    maps, futs = _day_inputs(env, d, front)
    min_pct = env.features["min_node_pct_of_king"]
    tol = env.features["mapping"]["atlas_match_tol"]
    bands = env.features["node_band"]
    out: dict[str, Any] = {}
    lines = [f"# Atlas side-by-side, {d} (§5.1)", "",
             "For each minute: the board in force, the strikes the bot treats as relevant nodes (>= "
             f"{min_pct:.0%} of King), their mapped futures level and band, and the moments spot crossed each strike "
             f"in the next {ATLAS_CROSS_WINDOW_MIN} minutes with the futures price then. Check in Atlas replay that "
             "the same strikes show as nodes of similar size.", ""]
    for m in ATLAS_MAPS:
        md, fut, root = maps[m], futs[CHAIN_ROOT[m]], CHAIN_ROOT[m]
        prev = mp.prev_accepted(md.accepted)
        rows_m = []
        for minute in minutes:
            at = int(datetime.combine(d, minute, tzinfo=ET).timestamp()) * mp.NS
            i = int(np.searchsorted(md.tau, at, side="right")) - 1
            while i >= 0 and not md.accepted[i]:
                i -= 1
            if i < 0:
                rows_m.append({"minute": minute.strftime("%H:%M"), "board": None})
                continue
            rel = (md.s_board == i) & (md.s_pct >= min_pct)
            r_i = md.r[i]
            window_end = at + ATLAS_CROSS_WINDOW_MIN * 60 * mp.NS
            nodes = []
            for k, pct in sorted(zip(md.s_strike[rel], md.s_pct[rel])):
                level = k * r_i
                crossings = []
                j0 = int(np.searchsorted(md.tau, at, side="right"))
                for j in range(max(j0, 1), len(md.tau)):
                    if md.tau[j] > window_end:
                        break
                    a, b = md.spot[j - 1] - k, md.spot[j] - k
                    # a spot landing exactly on the strike is one touch, counted on its arrival (b == 0); the pair
                    # that leaves it (a == 0) is not a second crossing, and its R would come from the board whose
                    # own spot is the strike (k * F / k = F: a 0.00 diff that proves nothing)
                    if a * b > 0 or a == 0:
                        continue
                    # the moment spot traded at the strike, interpolated between the two boards
                    t_k = int(md.tau[j - 1] + (a / (a - b)) * (md.tau[j] - md.tau[j - 1])) if a != b else int(md.tau[j])
                    # the bot's level then: a board applies only after its tau, so the R in force is the last
                    # accepted one before board j
                    if prev[j] < 0:
                        continue
                    mapped = k * md.r[prev[j]]
                    f_then = float(mp.last_price_at_or_before(fut.ts, fut.px, np.array([t_k]))[0])
                    if not (np.isfinite(mapped) and np.isfinite(f_then)):
                        continue
                    gated = md.resolution[j] == "1s" and md.resolution[j - 1] == "1s"  # 1-minute: information only
                    crossings.append({"at": _et(t_k), "futures": round(f_then, 2), "mapped": round(mapped, 2),
                                      "diff": round(abs(f_then - mapped), 2), "gate": gated,
                                      "within_tol": bool(abs(f_then - mapped) <= tol[root])})
                nodes.append({"strike": float(k), "pct_of_king": round(float(pct), 3), "level": round(level, 2),
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
                head = f"| {n['strike']:g} | {n['pct_of_king']:.0%} | {n['level']:.2f} | {n['band'][0]:.2f}-{n['band'][1]:.2f}"
                if not n["crossings"]:
                    lines.append(head + " | - | - | - | - |")
                for c in n["crossings"]:
                    lines.append(head + f" | {c['at']} | {c['futures']:.2f} | {c['diff']:.2f} | "
                                        f"{'yes' if c['within_tol'] else 'NO'} |")
            lines.append("")
    crossings = [c for rows in out.values() for row in rows for n in row.get("nodes", []) for c in n["crossings"]]
    gated = [c for c in crossings if c["gate"]]
    report = {"date": d.isoformat(), "minutes": [t.strftime("%H:%M") for t in minutes], "maps": out,
              "crossings": len(crossings), "gated_crossings": len(gated),
              "all_within_tol": bool(gated) and all(c["within_tol"] for c in gated),
              "owner_check": "pending"}
    md_path = reports_dir(env.paths) / f"atlas_{d.isoformat()}.md"
    atomic_write_text(md_path, "\n".join(lines) + "\n")
    env.echo(f"  {len(crossings)} crossings, all within tolerance: {report['all_within_tol']}; table: {md_path}")
    write_report(env, "atlas", report)
    return report


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
    atlas_ok = bool(atlas_days) and all(d in atlas_by_date and atlas_by_date[d]["all_within_tol"] for d in atlas_days)
    lines += [f"- Atlas match (every sample day; crossings between 1-second boards within atlas_match_tol): "
              f"**{'PASS' if atlas_ok else 'FAIL' if all(d in atlas_by_date for d in atlas_days) else 'pending'}**; "
              "**owner's check in Atlas replay: pending**"]
    for d in atlas_days:
        a = atlas_by_date.get(d)
        if a is None:
            lines.append(f"  - {d}: **pending** (`nqbot step8b atlas --date {d} --minutes HH:MM,...`)")
            continue
        within = sum(c["within_tol"] for rows in a["maps"].values() for row in rows for n in row.get("nodes", [])
                     for c in n["crossings"] if c["gate"])
        lines.append(f"  - {d} ({', '.join(a['minutes'])}): {within} of {a['gated_crossings']} gated crossings within "
                     f"tolerance ({a['crossings']} in all); all within: {a['all_within_tol']} (table: atlas_{d}.md)")
    lines += ["- Gate conditions (a) bias (95% day-block interval contains 0 or is within one futures tick) and (b) the "
              "chosen estimator's crossing p95 within 10% of the best possible R: **no command yet**; evidence in "
              "`docs/step8b_findings.md` R2, R7, R8 (all chains pass; NDXP pending its method)",
              "- Alignment check (content time never after floor(asOf) + 1 s): **no command yet** (`step8b alignment`)"]
    lines += ["", f"**Gate 8b: {'Atlas tables ready for the owner' if atlas_ok else 'not passed yet'}; (a), (b) and the "
                  "alignment check still need their commands.** It must pass before step 11. If a chain fails, find the "
                  "cause (stale spot, wrong second, slow map cadence) before anything else; never raise a threshold."]
    path = reports_dir(env.paths) / "mapping_gate.md"
    atomic_write_text(path, "\n".join(lines) + "\n")
    env.echo(f"report: {path}")
    return path
