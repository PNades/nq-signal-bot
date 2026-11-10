"""Step 13a: the reaction-distance report (SPEC §5.4b, §12 step 13; feature_spec F5.14; owner 2026-11-09, SPEC §13
Q-dryrun13 (3), (14) (d)).

Reads the level timelines of the months 1-4 feature build (`levels/<date>.parquet`, `features.level_timeline`) and the
stored trades; writes nothing the build reads. Development months only (§7 rule 2b).

**The measurement (F5.14), per level, on every trade (in exchange order):**
- the line: the band's centre (a chart level's band is line ± its start half-width; a node's band is its mapped strike
  ± `node_band`, so the centre is the mapped strike, F1.4); a single print: the near edge of its unfilled run;
- **approach:** after a re-arm (price ≥ cap + `zone_proximity` + `rearm_dist` from the line on one side), the first trade
  within cap + `zone_proximity` of it from that side. The cap: a chart level's `chart_level_half_width_cap`; a node's
  `node_band` half-width mapped with R in force at the trade (NDXP at ±20, the widest tested). ES (SPY / SPX nodes):
  `zone_proximity`, `rearm_dist` and `react_pts` × ES ÷ NQ (F0.11);
- **reaction:** within `react_minutes` of the approach, price moves ≥ `react_pts` back from the approach's extreme
  (the running deepest point toward and through the line);
- **reaction distance:** max(0, the deepest point past the line before the reaction), reacted approaches only;
- **highs/lows** (the sweep-eligible keys): an approach whose penetration resolves as a break by the sweep rule (F3.9)
  judged against the line, before the reaction, is not a reacted approach ("penetration beyond the sweep window
  excluded"; reading approved by the owner 2026-11-10, SPEC §13 Q-step13a);
- the level is measured only while its band exists and it isn't frozen (a node while not relevant, a profile level while
  away); every stretch is measured on its own (a re-arm inside it first). Its active spans (crossed or not) don't limit
  it: a cross at the start width would cap the measured penetration at the width being measured.
- **half-width:** the `react_width_q` quantile of a type's reaction distances, clipped to its cap, to the tick; every
  CME hour counts, the RTH-only width is shown beside and flagged when it differs (`RTH_DIFF_SHARE`, owner 2026-11-10);
- **NDXP band:** the smallest of ±10 / 15 / 20 NDXP $ covering that quantile of the NDXP distances in NDXP $.

**Job** `step13a_reactions` (`nqbot step13 reactions-plan`, then `nqbot run step13a_reactions --until HH:MM`): one
session per chunk in its own process (`nqbot step13 reactions-day D`), then the report (`nqbot step13
reactions-report`): `<reports_dir>/step13/reactions.md`, `reactions_widths.png`, `reactions_nodes.png`,
`proposed_widths.json`.
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from nqbot import months as months_mod
from nqbot.checks import feature_build as fb
from nqbot.checks import session_build as sb
from nqbot.checks.step8b import Env
from nqbot.features import level_timeline as lt
from nqbot.features import session_tape as st
from nqbot.features.sweep import HIGHS_LOWS
from nqbot.storage import atomic_write_bytes, atomic_write_json, atomic_write_text

JOB = "step13a_reactions"
REPORT_CHUNK = "report"
WIDTH_MONTHS = (1, 2, 3, 4)       # §12 step 13: the widths are frozen on development months 1-4
MIN_REACTED = 100                 # a type with fewer reacted approaches is flagged (the step-13 count reports' floor)
NDXP_CHOICES = (10, 15, 20)       # §5.4b: the NDXP band tested (NDXP $)
MINUTE_NS = 60 * 10**9
NODE_CHAINS = {"NQ": ("QQQ", "NDXP"), "ES": ("SPY", "SPX")}
GROUP_OF_CHAIN = {"QQQ": "qqq_node", "NDXP": "ndxp_node", "SPY": "spy_node", "SPX": "spx_node"}
DAY_SCHEMA = 1
RTH_DIFF_SHARE = 0.20             # owner 2026-11-10 (§13 Q-step13a): flag a width whose RTH-only figure differs from
                                  # the all-hours one by more than this share of it (or rounds to another width)


class Refused(Exception):
    pass


# --- the measurement (pure, on arrays) ------------------------------------------------------------------------------

def measure(ts: np.ndarray, price: np.ndarray, top: np.ndarray, bot: np.ndarray, cap: np.ndarray, prox: np.ndarray,
            rearm: np.ndarray, react: np.ndarray, react_ns: int, sweep: dict | None = None) -> list[dict[str, Any]]:
    """F5.14 on one level's trades. `top` / `bot` = the line (equal for a line, the run's edges for a single print;
    NaN where the level isn't measured); `cap`, `prox`, `rearm`, `react` per trade (the ES side scales them).
    `sweep` = {"depth": array, "bars": int} for highs/lows. Returns one dict per approach: i0 (trade index), side (+1
    from above, -1 from below), reacted, excluded ("sweep_break" or None), dist (pts; reacted only)."""
    n = len(ts)
    out: list[dict[str, Any]] = []
    if not n:
        return out
    valid = np.isfinite(top) & np.isfinite(bot) & np.isfinite(cap)
    if not valid.any():
        return out
    u = price - top               # ≥ 0: above the line (the run's top)
    w = bot - price               # ≥ 0: below it
    near = cap + prox
    arm = near + rearm
    edges = np.flatnonzero(np.diff(np.r_[False, valid, False].astype(np.int8)))
    for s, e in zip(edges[::2], edges[1::2]):
        arm_hi = s + np.flatnonzero(u[s:e] >= arm[s:e])
        arm_lo = s + np.flatnonzero(w[s:e] >= arm[s:e])
        near_hi = s + np.flatnonzero(u[s:e] <= near[s:e])
        near_lo = s + np.flatnonzero(w[s:e] <= near[s:e])
        pos = s
        while True:
            a = _next(arm_hi, pos)
            b = _next(arm_lo, pos)
            if a is None and b is None:
                break
            side = 1 if b is None or (a is not None and a < b) else -1
            i0 = _next(near_hi if side == 1 else near_lo, (a if side == 1 else b) + 1)
            if i0 is None:
                break
            out.append(_approach(ts, price, u if side == 1 else w, react, react_ns, i0, e, side, sweep))
            pos = i0 + 1
    return out


def _next(idx: np.ndarray, pos: int) -> int | None:
    k = int(np.searchsorted(idx, pos))
    return int(idx[k]) if k < len(idx) else None


def _approach(ts, price, x, react, react_ns, i0, e, side, sweep) -> dict[str, Any]:
    """One approach from trade i0: x = the distance on the approach side (negative = past the line)."""
    j1 = min(e, int(np.searchsorted(ts, ts[i0] + react_ns, side="left")))
    xs = x[i0:j1]
    m = np.minimum.accumulate(xs)
    hit = np.flatnonzero(xs - m >= react[i0:j1])
    rec = {"i0": int(i0), "side": side, "reacted": False, "excluded": None, "dist": None}
    if not len(hit):
        return rec
    h = int(hit[0])
    if sweep is not None and _sweep_broke(ts, x, i0, i0 + h, e, sweep):
        rec["excluded"] = "sweep_break"
        return rec
    rec["reacted"] = True
    rec["dist"] = float(max(0.0, -m[h]))
    return rec


def _sweep_broke(ts, x, i0, ih, e, sweep) -> bool:
    """F3.9 against the line, for a high/low that reacted at trade ih: the first unresolved break before the reaction
    (from the first trade past the line) is judged. A break if price goes more than `sweep_max_depth` past before a
    1-minute bar closes not past the line, or if no bar through the `sweep_reclaim_bars`-th full bar after the break
    bar does (the break bar's own close can reclaim); otherwise swept. A bar whose close the measured stretch doesn't
    reach (the level stopped or froze inside it) can't reclaim."""
    past = np.flatnonzero(x[i0:ih + 1] < 0)
    if not len(past):
        return False
    b = i0 + int(past[0])
    bars = int(sweep["bars"])
    m0 = int(ts[b] // MINUTE_NS)
    j_end = min(e, int(np.searchsorted(ts, (m0 + bars + 1) * MINUTE_NS, side="left")))
    seg_ts, seg_x = ts[b:j_end], x[b:j_end]
    minute = seg_ts // MINUTE_NS
    last = np.flatnonzero(np.r_[minute[1:] != minute[:-1], True])     # each bar's closing trade
    if j_end == e and e < len(ts) and len(last) and ts[e] // MINUTE_NS == minute[last[-1]]:
        last = last[:-1]
    reclaim = last[seg_x[last] >= 0]
    deep = np.flatnonzero(seg_x < -sweep["depth"][b:j_end])
    i_rec = int(reclaim[0]) if len(reclaim) else None
    i_deep = int(deep[0]) if len(deep) else None
    if i_deep is not None and (i_rec is None or i_deep < i_rec):
        return True
    return i_rec is None


# --- one session ----------------------------------------------------------------------------------------------------

def params(f: dict) -> dict[str, Any]:
    return {"zone_proximity": float(f["zone_proximity"]), "rearm_dist": float(f["rearm_dist"]),
            "react_pts": float(f["react_pts"]), "react_minutes": float(f["react_minutes"]),
            "react_width_q": float(f["react_width_q"]), "cap": dict(f["chart_level_half_width_cap"]),
            "sweep_max_depth": float(f["sweep_max_depth"]), "sweep_reclaim_bars": int(f["sweep_reclaim_bars"]),
            "node_band": dict(f["node_band"]), "va_edge_sweep": bool(f["va_edge_sweep"]),
            "measure": "f5.14-v1"}


def params_digest(p: dict) -> str:
    return hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()[:10]


def build_doc(env: Env) -> dict[str, Any]:
    """The feature build the report reads: the job's current plan (its key checked against the code and config)."""
    doc = fb.load_plan(env)
    if not set(WIDTH_MONTHS) <= set(doc["months"]):
        raise Refused(f"the feature-build plan covers ledger months {doc['months']}, not 1-4: plan and run the months 1-4 "
                      "build first (`uv run nqbot features plan --months 1-4`)")
    return doc


def day_dir(env: Env, key: str, p: dict) -> Path:
    return env.paths.reports_dir / "step13" / "reactions" / f"{key}_{params_digest(p)}" / "days"


def day_file(env: Env, key: str, p: dict, day: date) -> Path:
    return day_dir(env, key, p) / f"{day.isoformat()}.parquet"


def report_dir(env: Env) -> Path:
    return env.paths.reports_dir / "step13"


def reactions_day(env: Env, day: date) -> Path:
    """`nqbot step13 reactions-day D`: the session's approaches, cached by build key and parameters."""
    months_mod.require_development(day, env.settings)
    doc = build_doc(env)
    if day.isoformat() not in doc["sessions"]:
        raise Refused(f"{day} is not a session of the feature-build plan")
    key = doc["key"]
    f = env.features
    p = params(f)
    path = fb.out_dir(env, key) / "levels" / f"{day.isoformat()}.parquet"
    if not path.exists():
        raise Refused(f"{day}: the build has not written its level timelines yet ({path}; pass 2)")
    table, meta = fb.read_levels(path)
    if meta.get("schema") != lt.SCHEMA:
        raise Refused(f"{day}: level timeline schema {meta.get('schema')} (this code reads {lt.SCHEMA})")
    pl = sb.plan(env)
    damage = sb.load_damage(env).get(day)
    gaps = [] if damage is None else [(int(a.timestamp() * 1e9), int(b.timestamp() * 1e9))
                                      for a, b in damage.rth_gap_minutes]
    rows = table.to_pylist()
    out: list[dict[str, Any]] = []
    for root in ("NQ", "ES"):
        tape = st.load_tape(env.db, pl, root, day)
        if tape is None:
            raise Refused(f"{root} {day}: no stored trades")
        mine = [r for r in rows if r["instrument"] == root]
        out += _measure_root(tape, mine, meta, p, root, day, gaps)
        del tape
    write_day(day_file(env, key, p, day), out, {"day": day.isoformat(), "key": key, "params": p,
                                                 "schema": DAY_SCHEMA})
    env.echo(f"{day}: {len(out)} approaches, {sum(1 for r in out if r['reacted'])} reacted")
    return day_file(env, key, p, day)


def _measure_root(tape, rows: list[dict], meta: dict, p: dict, root: str, day: date, gaps: list) -> list[dict]:
    ts, price = tape.ts, tape.price
    n = len(ts)
    if root == "ES":
        ratio = np.where(ts >= int(meta["es_switch_ns"]), float(meta["es_ratio_today"]), float(meta["es_ratio_prev"]))
    else:
        ratio = np.ones(n)
    react_ns = int(p["react_minutes"] * MINUTE_NS)
    hw_sp = float(meta["chart_level_half_width"].get("single_print", 0.0))
    em = meta["em_nq"] if root == "NQ" else meta["em_es"]
    out = []
    for r in rows:
        node = r["rtype"] == "gex_node"
        if node:
            if r["chain"] not in NODE_CHAINS[root]:
                continue
            group = GROUP_OF_CHAIN[r["chain"]]
        else:
            if root != "NQ" or r["key"] not in p["cap"]:
                continue        # ES chart levels (their widths follow NQ's, F5.14); containers; dark pools
            group = r["key"]
        times = np.asarray(r["times"], dtype=np.int64)
        if not len(times):
            continue
        i_a = int(np.searchsorted(ts, times[0], side="left"))
        i_z = n if r["end_ns"] is None else int(np.searchsorted(ts, int(r["end_ns"]), side="left"))
        if i_z <= i_a:
            continue
        t = ts[i_a:i_z]
        k = np.searchsorted(times, t, side="right") - 1
        lo = np.asarray(r["lo"], float)[k]
        hi = np.asarray(r["hi"], float)[k]
        for a, z in r["freezes"]:
            lo[(t >= a) & (t < z)] = np.nan
        if r["key"] == "single_print":
            top, bot = hi - hw_sp, lo + hw_sp
        else:
            top = bot = (lo + hi) / 2
        rr = ratio[i_a:i_z]
        if node:
            cap = (hi - lo) / 2
        else:
            cap = np.full(len(t), float(p["cap"][r["key"]]))
        sweep = None
        if r["key"] in HIGHS_LOWS or (r["key"] == "va_edge" and p["va_edge_sweep"]):
            sweep = {"depth": np.full(len(t), p["sweep_max_depth"]), "bars": p["sweep_reclaim_bars"]}
        res = measure(t, price[i_a:i_z], top, bot, cap, p["zone_proximity"] * rr, p["rearm_dist"] * rr,
                      p["react_pts"] * rr, react_ns, sweep)
        for x in res:
            j = x["i0"]
            t0 = int(t[j])
            if any(g0 < t0 + react_ns and g1 > t0 for g0, g1 in gaps):
                continue        # a damaged stretch inside the reaction window (§3.2): not measured
            per_unit = None
            if node:
                band = float(p["node_band"][r["chain"]])
                per_unit = band / float(cap[j]) if cap[j] > 0 else None    # underlying $ per futures point
            out.append({"day": day.isoformat(), "uid": r["uid"], "level_id": r["level_id"], "sub_type": r["sub_type"],
                        "group": group, "instrument": root, "side": x["side"], "t0": t0,
                        "rth": bool(int(meta["open_ns"]) <= t0 < int(meta["close_ns"])), "reacted": x["reacted"],
                        "excluded": x["excluded"], "dist_pts": x["dist"], "cap_pts": float(cap[j]),
                        "underlying_per_pt": per_unit, "em_pts": em})
    return out


def write_day(path: Path, rows: list[dict], meta: dict) -> None:
    cols = ["day", "uid", "level_id", "sub_type", "group", "instrument", "side", "t0", "rth", "reacted", "excluded",
            "dist_pts", "cap_pts", "underlying_per_pt", "em_pts"]
    schema = pa.schema([("day", pa.string()), ("uid", pa.string()), ("level_id", pa.string()),
                        ("sub_type", pa.string()), ("group", pa.string()), ("instrument", pa.string()),
                        ("side", pa.int8()), ("t0", pa.int64()), ("rth", pa.bool_()), ("reacted", pa.bool_()),
                        ("excluded", pa.string()), ("dist_pts", pa.float64()), ("cap_pts", pa.float64()),
                        ("underlying_per_pt", pa.float64()), ("em_pts", pa.float64())])
    table = pa.Table.from_pylist([{c: r.get(c) for c in cols} for r in rows], schema=schema)
    table = table.replace_schema_metadata({b"nqbot": json.dumps(meta, sort_keys=True, default=str).encode()})
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd")
    atomic_write_bytes(path, buf.getvalue())


# --- the report -----------------------------------------------------------------------------------------------------

def tick_round(x: float) -> float:
    return round(x / st.TICK) * st.TICK


def width_of(dists: np.ndarray, q: float, cap: float) -> float | None:
    """F5.14: the q quantile of the reaction distances, clipped to the cap, to the tick."""
    if not len(dists):
        return None
    return float(min(cap, tick_round(float(np.quantile(dists, q)))))


def ndxp_choice(q_ndxp_usd: float | None) -> tuple[int | None, bool]:
    """Q-dryrun13 (14) (d), proposed: the smallest of ±10 / 15 / 20 NDXP $ that covers the `react_width_q` quantile of
    the NDXP reaction distances (in NDXP $); above 20 → 20 and flagged."""
    if q_ndxp_usd is None:
        return None, False
    for c in NDXP_CHOICES:
        if q_ndxp_usd <= c:
            return c, False
    return NDXP_CHOICES[-1], True


def rth_flag(q_all: float | None, q_rth: float | None, w_all: float | None, w_rth: float | None,
             cap: float) -> str | None:
    """Owner 2026-11-10 (§13 Q-step13a): the owner decides which to freeze when the RTH-only figure differs from the
    all-hours one by more than `RTH_DIFF_SHARE` of it, or rounds to a different width. Both quantiles are clipped to the
    cap first (beyond it both freeze to the cap). None = no flag; else the reason."""
    if q_all is None:
        return None
    if q_rth is None:
        return "no RTH reactions"
    a, r = min(q_all, cap), min(q_rth, cap)
    if w_all != w_rth:
        return f"RTH width {_fmt(w_rth)} vs {_fmt(w_all)}"
    if abs(r - a) > RTH_DIFF_SHARE * a:
        return f"RTH q {r:.2f} vs {a:.2f}" + (f" ({100 * (r - a) / a:+.0f}%)" if a > 0 else "")
    return None


def chart_row(t: pa.Table, k: str, q: float, cap: float) -> dict[str, Any]:
    """One chart-level type: its summary, the proposed width (all hours), the RTH-only width and the RTH flag."""
    s = summarize(t, k, q)
    qk = f"{q:g}"
    s["proposed"] = width_of(s["dist"], q, cap)
    s["proposed_rth"] = width_of(s["dist"][s["rth"]], q, cap)
    s["rth_flag"] = rth_flag(s["q"][qk], s["q_rth"][qk], s["proposed"], s["proposed_rth"], cap)
    return s


def ndxp_row(s: dict[str, Any], q: float) -> dict[str, Any]:
    """The NDXP band choice from all hours and from RTH only (NDXP $), with the RTH flag."""
    qk = f"{q:g}"
    q_all = None if s["q_under"] is None else s["q_under"][qk]
    q_rth = None if s["q_under_rth"] is None else s["q_under_rth"][qk]
    pick, above = ndxp_choice(q_all)
    pick_rth, above_rth = ndxp_choice(q_rth)
    return {"q": q_all, "q_rth": q_rth, "pick": pick, "above": above, "pick_rth": pick_rth, "above_rth": above_rth,
            "rth_flag": rth_flag(q_all, q_rth, pick, pick_rth, float(NDXP_CHOICES[-1]))}


def load_days(env: Env, key: str, p: dict, days: list[date]) -> pa.Table:
    paths = [day_file(env, key, p, d) for d in days]
    missing = [d for d, x in zip(days, paths) if not x.exists()]
    if missing:
        raise Refused(f"{len(missing)} sessions not measured yet (first {missing[0]}): run the job first")
    return pa.concat_tables([pq.read_table(x).replace_schema_metadata(None) for x in paths])


def summarize(t: pa.Table, group: str, q: float) -> dict[str, Any]:
    g = t.filter(pa.compute.equal(t.column("group"), group))
    reacted = np.asarray(g.column("reacted").to_pylist(), bool)
    dist = np.asarray([x for x, r in zip(g.column("dist_pts").to_pylist(), reacted) if r], float)
    rth = np.asarray([x for x, r in zip(g.column("rth").to_pylist(), reacted) if r], bool)
    excl = sum(1 for x in g.column("excluded").to_pylist() if x)
    em = np.asarray([x for x, r in zip(g.column("em_pts").to_pylist(), reacted) if r], float)
    per = np.asarray([x if x is not None else np.nan for x, r in zip(g.column("underlying_per_pt").to_pylist(), reacted)
                      if r], float)
    qs = (0.5, q, 0.9)
    quant = lambda a: {f"{x:g}": (None if not len(a) else float(np.quantile(a, x))) for x in qs}
    under = dist * per
    ok = np.isfinite(under)
    return {"approaches": g.num_rows, "reacted": int(reacted.sum()), "sweep_breaks": excl,
            "share": (float(reacted.mean()) if len(reacted) else None), "dist": dist, "rth": rth,
            "q": quant(dist), "q_rth": quant(dist[rth]), "q_em": quant(dist / em) if len(em) else quant(em),
            "under": under[ok], "q_under": quant(under[ok]) if ok.any() else None,
            "q_under_rth": quant(under[ok & rth]) if (ok & rth).any() else None,
            "zero_share": (float((dist == 0).mean()) if len(dist) else None),
            "sessions": len(set(g.column("day").to_pylist()))}


def report(env: Env) -> Path:
    """`nqbot step13 reactions-report`: the widths proposal, charts and tables (§5.4b; the owner confirms)."""
    doc = build_doc(env)
    key = doc["key"]
    f = env.features
    p = params(f)
    q = p["react_width_q"]
    days = [date.fromisoformat(x) for x in doc["sessions"]]
    days = [d for d in days if months_mod.month_index(d, env.settings) in WIDTH_MONTHS]
    t = load_days(env, key, p, days)
    start = dict(f["chart_level_half_width"])
    rows = {k: chart_row(t, k, q, float(p["cap"][k])) for k in start}
    proposed = {k: s["proposed"] for k, s in rows.items()}
    nodes = {g: summarize(t, g, q) for g in ("qqq_node", "ndxp_node", "spy_node", "spx_node")}
    nd = ndxp_row(nodes["ndxp_node"], q)
    out_dir = report_dir(env)
    png_w = out_dir / "reactions_widths.png"
    png_n = out_dir / "reactions_nodes.png"
    _plot_widths(png_w, rows, start, proposed, p, q)
    _plot_nodes(png_n, nodes, p, q, nd["pick"])
    flags = {k: s["rth_flag"] for k, s in rows.items() if s["rth_flag"]}
    if nd["rth_flag"]:
        flags["ndxp_node"] = nd["rth_flag"]
    prop = {"build_key": key, "features_version": f["features_version"], "months": list(WIDTH_MONTHS),
            "sessions": len(days), "params": p, "chart_level_half_width": proposed,
            "chart_level_half_width_rth": {k: s["proposed_rth"] for k, s in rows.items()}, "rth_flags": flags,
            "ndxp_node_band": nd["pick"], "ndxp_q_usd": nd["q"], "ndxp_above_20": nd["above"],
            "ndxp_node_band_rth": nd["pick_rth"], "ndxp_q_usd_rth": nd["q_rth"],
            "counts": {k: {"approaches": v["approaches"], "reacted": v["reacted"]} for k, v in {**rows, **nodes}.items()}}
    atomic_write_json(out_dir / "proposed_widths.json", prop)
    md = _markdown(rows, nodes, start, p, q, nd, key, days, png_w, png_n, f)
    path = out_dir / "reactions.md"
    atomic_write_text(path, md)
    env.echo(f"report {path}")
    return path


def _fmt(x, d=2) -> str:
    return "-" if x is None else f"{x:.{d}f}"


def _markdown(rows, nodes, start, p, q, nd, key, days, png_w, png_n, f) -> str:
    qk = f"{q:g}"
    lines = [f"# Step 13a: reaction distances and the chart-level zone widths (months 1-4)", "",
             f"Build `{key}` (features_version {f['features_version']}), {len(days)} sessions of development months "
             f"1-4 ({days[0]} to {days[-1]}), the corrected levels of step 13-0 at the start widths. SPEC §5.4b, "
             "feature_spec F5.14. Every trade checked against every level line.", "",
             f"Parameters: `zone_proximity` {p['zone_proximity']:g}, `rearm_dist` {p['rearm_dist']:g}, `react_pts` "
             f"{p['react_pts']:g}, `react_minutes` {p['react_minutes']:g}, `react_width_q` {q:g}; highs/lows: "
             f"`sweep_max_depth` {p['sweep_max_depth']:g}, `sweep_reclaim_bars` {p['sweep_reclaim_bars']}.", "",
             "## Chart levels (NQ points)", "",
             f"Proposed half-width = the {qk} quantile of the reaction distances over all CME hours, clipped to the "
             f"cap, to the tick; the RTH-only width beside it. Flags: `few` = fewer than {MIN_REACTED} reacted "
             f"approaches; `RTH` = the RTH-only figure differs from the all-hours one by more than "
             f"{100 * RTH_DIFF_SHARE:.0f}% (both clipped to the cap) or rounds to a different width: the owner decides "
             "which to freeze (owner 2026-11-10).", "",
             f"| Type | Approaches | Reacted | Reacted % | Sweep breaks | At 0 | q0.5 | q{qk} | q0.9 | q{qk} RTH only | "
             f"q{qk} in EM | Cap | Start | **Proposed** | RTH-only width | Flag |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, s in rows.items():
        flags = (["few"] if s["reacted"] < MIN_REACTED else []) + ([f"RTH: {s['rth_flag']}"] if s["rth_flag"] else [])
        share = None if s["share"] is None else 100 * s["share"]
        zero = None if s["zero_share"] is None else 100 * s["zero_share"]
        lines.append(f"| `{k}` | {s['approaches']:,} | {s['reacted']:,} | {_fmt(share, 0)} | {s['sweep_breaks']:,} | "
                     f"{_fmt(zero, 0)}% | {_fmt(s['q']['0.5'])} | {_fmt(s['q'][qk])} | {_fmt(s['q']['0.9'])} | "
                     f"{_fmt(s['q_rth'][qk])} | {_fmt(s['q_em'][qk], 3)} | {p['cap'][k]:g} | {start[k]:g} | "
                     f"**{_fmt(s['proposed'])}** | {_fmt(s['proposed_rth'])} | {'; '.join(flags)} |")
    lines += ["", f"![widths]({png_w.name})", "", "## Skylit nodes (report only except NDXP)", "",
              "The line = the node's mapped strike (R in force at each trade), the cap = its `node_band` half-width "
              "mapped the same way (NDXP at ±20). QQQ / NDXP on NQ, SPY / SPX on ES (ES points; the NQ-point parameters "
              "× ES ÷ NQ). QQQ / SPY / SPX bands stay fixed (nothing frozen from these rows).", "",
              f"| Map | Approaches | Reacted | Reacted % | q0.5 pts | q{qk} pts | q0.9 pts | q{qk} underlying $ | "
              f"q{qk} in EM | Band (underlying) |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for g, s in nodes.items():
        chain = {"qqq_node": "QQQ", "ndxp_node": "NDXP", "spy_node": "SPY", "spx_node": "SPX"}[g]
        uq = None if s["q_under"] is None else s["q_under"][qk]
        lines.append(f"| {chain} | {s['approaches']:,} | {s['reacted']:,} | "
                     f"{_fmt(100 * s['share'] if s['share'] is not None else None, 0)} | {_fmt(s['q']['0.5'])} | "
                     f"{_fmt(s['q'][qk])} | {_fmt(s['q']['0.9'])} | {_fmt(uq)} | {_fmt(s['q_em'][qk], 3)} | "
                     f"±{p['node_band'][chain]:g} |")
    lines += ["", f"![nodes]({png_n.name})", "",
              "## NDXP band (owner 2026-11-10, §13 Q-step13a)", "",
              f"The smallest of ±{' / '.join(str(c) for c in NDXP_CHOICES)} NDXP $ that covers the q{qk} of the NDXP "
              f"reaction distances in NDXP $ (above 20: 20, flagged). All hours: q{qk} = {_fmt(nd['q'])} NDXP $ → "
              f"**±{nd['pick']}**" + (" (above 20: flagged)" if nd["above"] else "") + f"; RTH only: q{qk} = "
              f"{_fmt(nd['q_rth'])} → ±{nd['pick_rth']}" + (" (above 20)" if nd["above_rth"] else "") + "."
              + (f" **Flag RTH:** {nd['rth_flag']}: the owner decides which to freeze." if nd["rth_flag"] else ""), "",
              "## Readings (approved by the owner 2026-11-10, §13 Q-step13a)", "",
              "- Highs/lows, \"penetration beyond the sweep window excluded\": an approach whose penetration resolves "
              "as a break by the sweep rule judged against the line (more than `sweep_max_depth` past before a "
              "1-minute close back, or no close back by the `sweep_reclaim_bars`-th full bar) is not a reacted "
              "approach (counted under Sweep breaks).",
              "- The level is measured over its band's whole existence (not only its active spans) except while frozen; "
              "a cross at the start width would otherwise cap the penetration at the width being measured.",
              "- Every hour of the CME day counts (events exist overnight); the RTH-only figure is shown beside, and a "
              f"difference of more than {100 * RTH_DIFF_SHARE:.0f}% or a different width is flagged.",
              "- QQQ / SPY node rows cover months 1-4 here; months 5-6 come with the step-13d full-window build (report "
              "only, nothing frozen from them).",
              "- The VWAP lines and bands are read once a second (the line after each second's last trade).",
              "- An approach whose reaction window overlaps a damaged RTH stretch (§3.2) is left out.", ""]
    return "\n".join(lines) + "\n"


def _plot_widths(path: Path, rows, start, proposed, p, q) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    keys = list(rows)
    ncol = 4
    nrow = (len(keys) + ncol - 1) // ncol
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.2 * ncol, 2.9 * nrow), squeeze=False)
    for ax, k in zip(axes.flat, keys):
        cap = float(p["cap"][k])
        d = rows[k]["dist"]
        top = cap * 2
        bins = np.arange(0, top + 0.5, 0.5)
        ax.hist(np.clip(d, 0, top), bins=bins, color="#5b8def", edgecolor="white", linewidth=0.3)
        ax.axvline(start[k], color="#888888", linestyle="--", linewidth=1.2, label=f"start {start[k]:g}")
        if proposed[k] is not None:
            ax.axvline(proposed[k], color="#e8590c", linewidth=2, label=f"proposed {proposed[k]:g}")
        ax.axvline(cap, color="#c92a2a", linestyle=":", linewidth=1.5, label=f"cap {cap:g}")
        ax.set_title(f"{k}  (reacted {rows[k]['reacted']:,})", fontsize=10)
        ax.set_xlabel("reaction distance past the line (NQ pts)", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.legend(fontsize=7, frameon=False)
    for ax in list(axes.flat)[len(keys):]:
        ax.axis("off")
    fig.suptitle(f"Reaction distances by chart-level type, months 1-4 (proposed = q{q:g}, clipped to the cap; "
                 f"the last bar holds everything ≥ 2 × cap)", fontsize=11)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    atomic_write_bytes(path, buf.getvalue())


def _plot_nodes(path: Path, nodes, p, q, nd_pick) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 4, figsize=(17, 3.4))
    for ax, (g, chain) in zip(axes, (("qqq_node", "QQQ"), ("ndxp_node", "NDXP"), ("spy_node", "SPY"),
                                     ("spx_node", "SPX"))):
        s = nodes[g]
        band = float(p["node_band"][chain])
        vals = s["under"]          # in the underlying's own units
        unit = chain + " $"
        top = band * 2
        ax.hist(np.clip(vals, 0, top), bins=40, range=(0, top), color="#5b8def", edgecolor="white", linewidth=0.3)
        qq = float(np.quantile(vals, q)) if len(vals) else None
        if qq is not None:
            ax.axvline(qq, color="#e8590c", linewidth=2, label=f"q{q:g} {qq:.2f}")
        ax.axvline(band, color="#c92a2a", linestyle=":", linewidth=1.5, label=f"band ±{band:g}")
        if chain == "NDXP":
            for c in NDXP_CHOICES[:-1]:
                ax.axvline(c, color="#888888", linestyle="--", linewidth=1, label="±10 / ±15" if c == 10 else None)
        ax.set_title(f"{chain} nodes (reacted {s['reacted']:,})" + (f": proposed ±{nd_pick}" if chain == "NDXP" else ""),
                     fontsize=10)
        ax.set_xlabel(f"reaction distance past the mapped strike ({unit})", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.legend(fontsize=7, frameon=False)
    fig.suptitle("Skylit node reaction distances, months 1-4 (the last bar holds everything ≥ 2 × band)", fontsize=11)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    atomic_write_bytes(path, buf.getvalue())


# --- the job --------------------------------------------------------------------------------------------------------

def chunk_ids(env: Env) -> list[str]:
    doc = build_doc(env)
    days = [d for d in doc["sessions"] if months_mod.month_index(date.fromisoformat(d), env.settings) in WIDTH_MONTHS]
    tag = f"{doc['key']}_{params_digest(params(env.features))}"
    return [f"r:{d}:{tag}" for d in days] + [f"{REPORT_CHUNK}:{tag}"]


class ReactionsJob:
    """`nqbot run step13a_reactions --until HH:MM`: one session per chunk in its own process, then the report. Reads
    the build's level timelines and the stored trades only; holds both job locks so no pull writes the data it reads."""

    name = JOB

    def __init__(self) -> None:
        from nqbot.jobs.lock import DATABENTO, SKYLIT

        self.locks = (SKYLIT, DATABENTO)

    def _env(self, ctx: Any) -> Env:
        from nqbot import calendar as cal_mod
        from nqbot.config import load_features

        return Env(settings=ctx.settings, features=load_features(), paths=ctx.paths, log=ctx.log, echo=ctx.log.info,
                   cal=cal_mod.load())

    def chunks(self, ctx: Any) -> list[str]:
        return chunk_ids(self._env(ctx))

    def run_chunk(self, chunk_id: str, ctx: Any) -> dict[str, Any] | None:
        from nqbot.checks.step8b_disloc import MIN_FREE_MB, free_mb, run_cli_in_child
        from nqbot.jobs.runner import StopChunk

        env = self._env(ctx)
        if chunk_id.startswith(REPORT_CHUNK + ":"):
            report(env)
            return None
        _, d, _tag = chunk_id.split(":")
        if day_file(env, build_doc(env)["key"], params(env.features), date.fromisoformat(d)).exists():
            return None
        mb = free_mb()
        if mb is not None and mb < MIN_FREE_MB:
            raise StopChunk(f"{mb} MB free (< {MIN_FREE_MB}) before {chunk_id}")
        peak = run_cli_in_child(["step13", "reactions-day", d])
        ctx.log.info("%s: %s process peak %s GB", self.name, chunk_id, peak)
        return {"peak_gb": peak}
