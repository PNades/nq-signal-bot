"""Step-8b gate conditions (owner 2026-10-08, SPEC §5.1, §12 step 8b, §13 Q8b-4). Reads stored data only.

Scored on strike crossings at event moments (findings R1): spot crosses a relevant node's strike k (>= 25% of
King on the board before the crossing pair) between two consecutive applied boards; the crossing time t* is
interpolated between their content times, and the error is F(t*) - k * R with the R in force after the board
before the pair (known before the crossing: not circular).

- `bias` (a): each chain's median signed error, with a 95% day-block bootstrap interval; pass = the interval
  contains 0 or lies within one futures tick.
- `estimator` (b): the chosen estimator's (F1.7) p95 |error| within 10% of the best possible R's, a centred
  median of the board ratios over +-60 s around t* (evaluation only: it uses later boards on purpose).
- `alignment`: each map's content offset fitted against the futures (cross-correlating board-to-board spot changes
  with the futures price at floor(asOf) + offset); pass = the fitted content time is never after floor(asOf) + 1 s.

NDXP is left out (its history `spot` is an NQ futures price; method pending, §13 Q8b-1). Each day's crossings are
kept on disk, so a stopped run loses nothing.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

import numpy as np

from nqbot.checks import step8b as s8
from nqbot.checks.step7_analysis import quantile
from nqbot.features import mapping as mp
from nqbot.storage import atomic_write_bytes

GATE_MAPS = ("QQQ", "SPY", "SPX")  # NDXP pending its method (§13 Q8b-1)
FUTURES_TICK = 0.25  # F0.3: NQ and ES tick
BIAS_CONF = 0.95  # §5.1 gate (a): 95% day-block bootstrap interval
BOOT_RESAMPLES = 2000  # findings R1: day-block bootstrap resamples
BOOT_SEED = 7
ESTIMATOR_MARGIN = 0.10  # §5.1 gate (b): within 10% of the best possible R
BEST_R_HALF_S = 60  # findings R1: the best possible R = centred median of the board ratios over +-60 s
ALIGN_SEARCH_MS = 3000  # alignment: offsets searched from -3 s to +3 s around floor(asOf); a peak at the edge fails
ALIGN_STEP_MS = 20
ALIGN_MAX_MS = 1000  # F0.1: content never after floor(asOf) + 1 s (τ)
ALIGN_MIN_PAIRS = 10  # fewer board-to-board changes than this: no fit
CACHE_VERSION = 1  # bump when the crossing maths change


# --- crossings ------------------------------------------------------------------------------------------

def _cfg_key(env: s8.Env) -> str:
    m = env.features["mapping"]
    key = {k: m[k] for k in ("board_content_offset_ms", "ratio_window_sec", "ratio_gap_reset_sec", "ratio_outlier_bps",
                             "ratio_reaccept_n", "ratio_open_skip_sec")}
    key.update(min_pct=env.features["min_node_pct_of_king"], stamp=str(m["skylit_stamp_change_date"]),
               half=BEST_R_HALF_S, v=CACHE_VERSION)
    return hashlib.sha256(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()[:12]


def chain_crossings(env: s8.Env, d: date, md: s8.MapDay, fut: s8.Futures) -> dict[str, np.ndarray]:
    """Signed errors (futures - k * R) at the event-moment crossings of one map and session: the chosen estimator
    (F1.7) and the best possible R, with each crossing's time t* (ns) and strike."""
    cfg = env.features["mapping"]
    era = mp.board_era(d, cfg["skylit_stamp_change_date"])
    off = mp.content_offset_ms(cfg, md.name, era)
    window = cfg["ratio_window_sec"][md.name]
    if off is None or window is None:
        raise s8.CheckRefused(f"{md.name}: board_content_offset_ms[{era}] or ratio_window_sec not set (pending)")
    A = np.flatnonzero(md.applied)
    tc = mp.content_time_ns(md.as_of[A], off)
    sp = md.spot[A]
    f = mp.last_price_at_or_before(fut.ts, fut.px, tc)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = f / sp
    gap = cfg["ratio_gap_reset_sec"]
    r_in = mp.ratio_in_force(tc, r, window, gap, cfg["ratio_outlier_bps"], cfg["ratio_reaccept_n"])
    seg = mp.gap_segments(tc, gap)
    pos = np.full(len(md.as_of), -1)
    pos[A] = np.arange(len(A))
    rel = (md.s_pct >= env.features["min_node_pct_of_king"]) & (pos[md.s_board] >= 0)
    rb, rk = pos[md.s_board[rel]], md.s_strike[rel]
    o = np.lexsort((rk, rb))
    rb, rk = rb[o], rk[o]
    lo_s, hi_s = np.searchsorted(rb, np.arange(len(A))), np.searchsorted(rb, np.arange(len(A)), side="right")
    ct, ck, cb = [], [], []
    for p in range(1, len(A) - 1):
        if seg[p - 1] != seg[p] or seg[p] != seg[p + 1]:
            continue
        a, b = sp[p], sp[p + 1]
        if a == b:
            continue
        ks = rk[lo_s[p - 1]:hi_s[p - 1]]  # relevant nodes on the board before the pair
        ks = ks[(ks > a) & (ks <= b)] if b > a else ks[(ks >= b) & (ks < a)]
        for k in ks:
            ct.append(tc[p] + (k - a) / (b - a) * (tc[p + 1] - tc[p]))
            ck.append(k)
            cb.append(p - 1)
    if not ct:
        return {"chosen": np.zeros(0), "best": np.zeros(0), "t": np.zeros(0, dtype=np.int64), "k": np.zeros(0)}
    ct_ = np.asarray(ct, dtype=np.int64)
    ck_, cb_ = np.asarray(ck), np.asarray(cb)
    f_gt = mp.last_price_at_or_before(fut.ts, fut.px, ct_)
    lo = np.searchsorted(tc, ct_ - BEST_R_HALF_S * mp.NS)
    hi = np.searchsorted(tc, ct_ + BEST_R_HALF_S * mp.NS, side="right")
    best = np.array([np.nanmedian(r[i:j]) if np.isfinite(r[i:j]).any() else np.nan for i, j in zip(lo, hi)])
    return {"chosen": f_gt - ck_ * r_in[cb_], "best": f_gt - ck_ * best, "t": ct_, "k": ck_}


def day_crossings(env: s8.Env, d: date, front: dict[str, dict[str, str]]) -> dict[str, Any]:
    """One session's crossings for GATE_MAPS, cached on disk per config (a stopped run resumes)."""
    path = s8.reports_dir(env.paths) / "gate_days" / f"{d.isoformat()}_{_cfg_key(env)}.npz"
    if path.exists():
        z = np.load(path, allow_pickle=False)
        if "skip" in z.files:
            return {"skip": str(z["skip"])}
        return {m: {"chosen": z[f"{m}_chosen"], "best": z[f"{m}_best"]} for m in GATE_MAPS}
    maps, futs = s8._day_inputs(env, d, front)
    res = {r for m in GATE_MAPS for r in maps[m].resolution}
    out: dict[str, Any] = {}
    arrays: dict[str, np.ndarray] = {}
    if res != {"1s"}:
        out = {"skip": f"not all 1-second boards ({sorted(res)})"}
        arrays["skip"] = np.array(out["skip"])
    else:
        for m in GATE_MAPS:
            out[m] = chain_crossings(env, d, maps[m], futs[s8.CHAIN_ROOT[m]])
            arrays[f"{m}_chosen"], arrays[f"{m}_best"] = out[m]["chosen"], out[m]["best"]
    import io

    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    atomic_write_bytes(path, buf.getvalue())
    return out


def _collect(env: s8.Env, dates: list[date] | None) -> tuple[list[date], dict[str, list[tuple[date, dict]]], dict]:
    days = s8.days_to_check(env, dates)
    front = s8.front_symbols(env)
    per: dict[str, list[tuple[date, dict]]] = {m: [] for m in GATE_MAPS}
    skipped: dict[str, str] = {}
    for d in days:
        out = day_crossings(env, d, front)
        if "skip" in out:
            skipped[d.isoformat()] = out["skip"]
            env.echo(f"  {d}: skipped ({out['skip']})")
            continue
        for m in GATE_MAPS:
            per[m].append((d, out[m]))
        env.echo(f"  {d}: " + ", ".join(f"{m} {len(out[m]['chosen'])}" for m in GATE_MAPS) + " event crossings")
    return days, per, skipped


def _unit(m: str) -> str:
    return f"{s8.CHAIN_ROOT[m]} pts"


# --- (a) bias -------------------------------------------------------------------------------------------

def bootstrap_median(per_day: list[np.ndarray], resamples: int = BOOT_RESAMPLES, seed: int = BOOT_SEED,
                     conf: float = BIAS_CONF) -> tuple[float | None, float | None, float | None]:
    """Median of the pooled values and its day-block bootstrap interval (days resampled with replacement)."""
    days = [v[np.isfinite(v)] for v in per_day]
    days = [v for v in days if len(v)]
    if not days:
        return None, None, None
    rng = np.random.default_rng(seed)
    meds = np.empty(resamples)
    for i in range(resamples):
        idx = rng.integers(0, len(days), len(days))
        meds[i] = np.median(np.concatenate([days[j] for j in idx]))
    a = (1 - conf) / 2
    return (float(np.median(np.concatenate(days))), float(np.quantile(meds, a)), float(np.quantile(meds, 1 - a)))


def bias_pass(lo: float | None, hi: float | None, tick: float = FUTURES_TICK) -> bool:
    """§5.1 gate (a): the interval contains 0, or lies within one futures tick of it."""
    if lo is None or hi is None:
        return False
    return (lo <= 0 <= hi) or (abs(lo) <= tick and abs(hi) <= tick)


def check_bias(env: s8.Env, dates: list[date] | None = None) -> dict[str, Any]:
    days, per, skipped = _collect(env, dates)
    stamp = env.features["mapping"]["skylit_stamp_change_date"]
    per_chain: dict[str, Any] = {}
    for m in GATE_MAPS:
        med, lo, hi = bootstrap_median([x["chosen"] for _, x in per[m]])
        eras = {}
        for era in (mp.ERA_BEFORE, mp.ERA_FROM):
            sel = [x["chosen"] for d, x in per[m] if mp.board_era(d, stamp) == era]
            e_med, e_lo, e_hi = bootstrap_median(sel)
            eras[era] = {"days": len(sel), "median": _r(e_med), "interval": [_r(e_lo), _r(e_hi)]}
        per_chain[m] = {"unit": _unit(m), "days": len(per[m]), "n": int(sum(len(x["chosen"]) for _, x in per[m])),
                        "median": _r(med), "interval": [_r(lo), _r(hi)], "by_era": eras,
                        "pass": bias_pass(lo, hi)}
        env.echo(f"  {m}: bias {_r(med)} [{_r(lo)}, {_r(hi)}] {_unit(m)} -> {'PASS' if per_chain[m]['pass'] else 'FAIL'}")
    report = {"days": [d.isoformat() for d in days], "skipped": skipped, "per_chain": per_chain,
              "tick": FUTURES_TICK, "resamples": BOOT_RESAMPLES, "pass": all(c["pass"] for c in per_chain.values())}
    s8.write_report(env, "bias", report)
    return report


# --- (b) estimator --------------------------------------------------------------------------------------

def check_estimator(env: s8.Env, dates: list[date] | None = None) -> dict[str, Any]:
    days, per, skipped = _collect(env, dates)
    per_chain: dict[str, Any] = {}
    for m in GATE_MAPS:
        chosen = np.abs(np.concatenate([x["chosen"] for _, x in per[m]] or [np.zeros(0)]))
        best = np.abs(np.concatenate([x["best"] for _, x in per[m]] or [np.zeros(0)]))
        chosen, best = chosen[np.isfinite(chosen)], best[np.isfinite(best)]
        p_c = quantile(chosen, 0.95) if len(chosen) else None
        p_b = quantile(best, 0.95) if len(best) else None
        ok = p_c is not None and p_b is not None and p_c <= (1 + ESTIMATOR_MARGIN) * p_b
        window = env.features["mapping"]["ratio_window_sec"][m]
        per_chain[m] = {"unit": _unit(m), "estimator": f"trailing {window} s median" if window else "per board (F1.2)",
                        "n": int(len(chosen)), "chosen_p95": _r(p_c), "best_p95": _r(p_b),
                        "ratio": _r(p_c / p_b) if p_c is not None and p_b else None, "pass": bool(ok)}
        env.echo(f"  {m}: {per_chain[m]['estimator']} p95 {_r(p_c)} vs best possible {_r(p_b)} {_unit(m)} -> "
                 f"{'PASS' if ok else 'FAIL'}")
    report = {"days": [d.isoformat() for d in days], "skipped": skipped, "per_chain": per_chain,
              "margin": ESTIMATOR_MARGIN, "best_r_half_s": BEST_R_HALF_S,
              "pass": all(c["pass"] for c in per_chain.values())}
    s8.write_report(env, "estimator", report)
    return report


# --- alignment ------------------------------------------------------------------------------------------

def fit_content_offset(as_of_ns: np.ndarray, spot: np.ndarray, fut_ts: np.ndarray, fut_px: np.ndarray
                       ) -> dict[str, Any]:
    """The offset (ms from floor(asOf)) at which the futures price best tracks the board's spot changes."""
    fl = mp.floor_second_ns(as_of_ns)
    dsp = np.diff(np.asarray(spot, dtype=np.float64))
    best = (None, -np.inf)
    for o in range(-ALIGN_SEARCH_MS, ALIGN_SEARCH_MS + 1, ALIGN_STEP_MS):
        df = np.diff(mp.last_price_at_or_before(fut_ts, fut_px, fl + o * 1_000_000))
        ok = np.isfinite(df) & np.isfinite(dsp)
        if ok.sum() < ALIGN_MIN_PAIRS or dsp[ok].std() == 0 or df[ok].std() == 0:
            continue
        c = float(np.corrcoef(dsp[ok], df[ok])[0, 1])
        if c > best[1]:
            best = (o, c)
    off, corr = best
    edge = off is not None and abs(off) >= ALIGN_SEARCH_MS  # the true peak may lie beyond the search
    return {"offset_ms": off, "corr": None if off is None else round(corr, 4), "boards": int(len(fl)),
            "at_search_edge": edge, "pass": off is not None and off <= ALIGN_MAX_MS and not edge}


def check_alignment(env: s8.Env, dates: list[date] | None = None) -> dict[str, Any]:
    days = s8.days_to_check(env, dates)
    front = s8.front_symbols(env)
    cfg = env.features["mapping"]
    per_day: dict[str, Any] = {}
    for d in days:
        maps, futs = s8._day_inputs(env, d, front)
        era = mp.board_era(d, cfg["skylit_stamp_change_date"])
        out = {}
        for m in GATE_MAPS:
            md, fut = maps[m], futs[s8.CHAIN_ROOT[m]]
            fit = fit_content_offset(md.as_of[md.applied], md.spot[md.applied], fut.ts, fut.px)
            out[m] = {**fit, "configured_ms": mp.content_offset_ms(cfg, m, era), "era": era}
        per_day[d.isoformat()] = out
        env.echo(f"  {d}: " + ", ".join(f"{m} {o['offset_ms']} ms{'' if o['pass'] else ' ALERT'}" for m, o in out.items()))
    per_chain = {}
    for m in GATE_MAPS:
        fits = [day[m] for day in per_day.values()]
        offs = [f["offset_ms"] for f in fits if f["offset_ms"] is not None]
        per_chain[m] = {"days": len(fits), "max_offset_ms": max(offs) if offs else None,
                        "alerts": [d for d, day in per_day.items() if not day[m]["pass"]],
                        "pass": bool(fits) and all(f["pass"] for f in fits)}
        env.echo(f"  {m}: latest content {per_chain[m]['max_offset_ms']} ms after floor(asOf) (max {ALIGN_MAX_MS}) -> "
                 f"{'PASS' if per_chain[m]['pass'] else 'FAIL'}")
    report = {"days": [d.isoformat() for d in days], "per_day": per_day, "per_chain": per_chain,
              "max_ms": ALIGN_MAX_MS, "pass": all(c["pass"] for c in per_chain.values())}
    s8.write_report(env, "alignment", report)
    return report


def _r(x: float | None, nd: int = 4) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), nd)
