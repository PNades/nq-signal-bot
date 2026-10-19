"""`nqbot step8b dislocations` (owner 2026-10-09, read-only, development months 1-6): how often the mapping ratio
moves fast, per chain (QQQ, SPY, SPX; NDXP's history spot is a futures price, so it is left out).

Per-board R = F(content time) / spot over the applied boards (the approved ingest rule, §5.1). A board is flagged at a
threshold when R's range over the trailing 2 minutes ((max - min) / median of the boards in (t - 120 s, t], within
one gap segment) exceeds it. Flagged boards less than 2 minutes apart form one event; its flagged span runs from the
first to the last flagged board (a single jump flags about 2 minutes). For each event: start time, minutes after the
open, peak range, and whether it starts inside a news blackout (§9) or the first minutes after the open.

One day at a time; each day's events are saved under `<reports_dir>/step8b/dislocations/<date>.json`, so a stopped
run resumes. The summary is `<reports_dir>/step8b/dislocations.md` (and a timestamped json).

The mapping-dislocation guard (owner 2026-10-14, F1.8, `features/disloc_guard.py`): every day also records the guard's
fires (trigger, size) and blocks per chain; the summary counts them, the blocks in which only the per-board trigger
fired (a column, not a verdict) and the **false fires** per week, warning above about one a week (owner 2026-10-14). A
false fire = a block during which the levels mapped with the bot's R stayed within the chain's tolerance of the levels
mapped with a hindsight R (the centred median of the per-board R over +-60 s; report only, never a feature), at every
relevant node of every board in the block: trading would have been safe without the block. Tolerance: QQQ its
`stop_buffer_floor` (4.0 NQ); SPY/SPX have none, so the closest existing setting, `atlas_match_tol` ES (0.78 ES, the
frozen SPY crossing p95). Run on the development months (default) or, with `--months walk-forward`, on months 7-9 (automated counts
only, §7 rule 2b; their files under `dislocations/walk_forward/`, summary `dislocations_walk_forward.md`). The top-up
and demo months join when they exist (steps 18/20 and 24).
"""

from __future__ import annotations

import gc
import json
import subprocess
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

import numpy as np

from nqbot import calendar as cal_mod
from nqbot import months as months_mod
from nqbot.checks import step8b as s8
from nqbot.features import disloc_guard as dg
from nqbot.features import mapping as mp
from nqbot.storage import atomic_write_json, atomic_write_text

DISLOC_MAPS = ("QQQ", "SPY", "SPX")
WINDOW_S = 120  # owner 2026-10-09: R moving more than the threshold within 2 minutes
THRESHOLDS = (0.0005, 0.001, 0.002)  # owner 2026-10-09: 0.05%, 0.1%, 0.2%
DETAIL_THRESHOLD = 0.001  # owner: time of day, news blackout and first minutes for the 0.1% events
FIRST_MINUTES = 15  # "the first minutes after the open"
MIN_FREE_MB = 2500  # owner 2026-10-10: stop before a day when less memory than this is free
DAY_VERSION = 5  # 2 (owner 2026-10-10): both R series, the median range and the day's futures range per day;
# 3 (owner 2026-10-14): the dislocation guard's fires and blocks per chain (F1.8); 4: false fires (hindsight R);
# 5 (owner 2026-10-17, §13 Q-dislguard-2 (b)(i)): the guard's windows reset only at the session start (re-run at the
# next gate run)
MONTH_SETS = {"development": months_mod.DEVELOPMENT, "walk-forward": months_mod.WALK_FORWARD}
FALSE_FIRES_PER_WEEK_MAX = 1.0  # owner 2026-10-14 (§13 Q-dislguard): revisit the guard above about one a week
TRADING_DAYS_PER_WEEK = 5
HINDSIGHT_HALF_WINDOW_S = 60  # owner 2026-10-14: hindsight R = centred median of the per-board R over +-60 s


def false_fire_tolerance(env: s8.Env, chain: str) -> tuple[float, str]:
    """The chain's tolerance for a false fire and where it comes from (owner 2026-10-14): its `stop_buffer_floor`,
    else (SPY/SPX, no NQ stop floor) `atlas_match_tol` of its futures, the closest existing setting."""
    floor = (env.settings.get("stop_buffer_floor") or {}).get(chain)
    if floor is not None:
        return float(floor), f"stop_buffer_floor[{chain}]"
    root = s8.CHAIN_ROOT[chain]
    return float(env.features["mapping"]["atlas_match_tol"][root]), f"atlas_match_tol[{root}]"


def hindsight_ratio(t_ns: np.ndarray, r: np.ndarray, idx: np.ndarray,
                    half_s: float = HINDSIGHT_HALF_WINDOW_S) -> np.ndarray:
    """Report only, never a feature: the median of the per-board r over [t - half, t + half] at the boards idx."""
    lo = np.searchsorted(t_ns, t_ns[idx] - int(half_s * mp.NS), side="left")
    hi = np.searchsorted(t_ns, t_ns[idx] + int(half_s * mp.NS), side="right")
    return np.array([np.median(r[a:b]) for a, b in zip(lo, hi)])
SERIES = ("board", "in_force")  # the per-board R, and the R the bot uses (F1.7) at its apply time


def _dir(env: s8.Env, month_set: str = "development") -> Path:
    base = s8.reports_dir(env.paths) / "dislocations"
    return base if month_set == "development" else base / month_set.replace("-", "_")


def _report_name(month_set: str) -> str:
    return "dislocations" if month_set == "development" else "dislocations_" + month_set.replace("-", "_")


def trailing_range(t_ns: np.ndarray, r: np.ndarray, seg: np.ndarray, window_s: float = WINDOW_S) -> np.ndarray:
    """(max - min) / median of r over (t - window, t] within each board's gap segment (trailing only)."""
    if len(t_ns) == 0:
        return np.zeros(0)
    starts = np.concatenate([[0], np.flatnonzero(np.diff(seg) != 0) + 1])
    seg_start = starts[np.searchsorted(starts, np.arange(len(t_ns)), side="right") - 1]
    lo = np.maximum(np.searchsorted(t_ns, t_ns - int(window_s * mp.NS), side="right"), seg_start)
    out = np.empty(len(t_ns))
    for i, a in enumerate(lo):
        w = r[a:i + 1]
        out[i] = (w.max() - w.min()) / np.median(w)
    return out


def events(t_ns: np.ndarray, rng: np.ndarray, thr: float, window_s: float = WINDOW_S) -> list[tuple[int, int, float]]:
    """(first flagged t, last flagged t, peak range) per event: flagged boards less than window_s apart join."""
    f = np.flatnonzero(rng > thr)
    if not len(f):
        return []
    groups = np.split(f, np.flatnonzero(np.diff(t_ns[f]) > int(window_s * mp.NS)) + 1)
    return [(int(t_ns[g[0]]), int(t_ns[g[-1]]), float(rng[g].max())) for g in groups]


def _et(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, timezone.utc).astimezone(s8.ET).strftime("%H:%M:%S")


def _mean_minute_range(fut: s8.Futures, start_ns: int, end_ns: int) -> float | None:
    """The session's mean 1-minute high-low range of the futures (descriptive, for the per-day table)."""
    k = (fut.ts >= start_ns) & (fut.ts < end_ns)
    if not k.any():
        return None
    m = (fut.ts[k] - start_ns) // (60 * mp.NS)
    n = int(m.max()) + 1
    hi, lo = np.full(n, -np.inf), np.full(n, np.inf)
    np.maximum.at(hi, m, fut.px[k])
    np.minimum.at(lo, m, fut.px[k])
    ok = np.isfinite(hi) & np.isfinite(lo)
    return round(float((hi[ok] - lo[ok]).mean()), 3)


def _guard_day(chain: str, params: dg.GuardParams, t_apply: np.ndarray, r: np.ndarray, r_in: np.ndarray,
               open_ns: int, spans: list[tuple[int, int]], top_strike: np.ndarray | None = None,
               tol: float | None = None) -> dict[str, Any]:
    """The guard's fires and blocks for one chain and session (the same ChainGuard labels and live use).
    top_strike: per board, the largest relevant strike (nan: none); with tol, each block gets its largest level gap
    between the bot's R and the hindsight R (futures points) and whether it was a false fire (gap <= tol)."""
    fires = dg.run_session(chain, params, t_apply, r, r_in)

    def where(t0: int) -> dict[str, Any]:
        return {"min_after_open": round((t0 - open_ns) / 60e9, 1), "in_news_blackout": any(a <= t0 < b for a, b in spans)}

    blocks = []
    for a, b in dg.block_spans(fires):
        inside = [f for f in fires if a <= f.start_ns <= b]
        trig = sorted({f.trigger for f in inside})
        gap = None
        if top_strike is not None:
            k = np.flatnonzero((t_apply >= a) & (t_apply <= b) & np.isfinite(r_in) & np.isfinite(top_strike))
            if len(k):
                gap = float(np.max(top_strike[k] * np.abs(r_in[k] - hindsight_ratio(t_apply, r, k))))
        blocks.append({"start_et": _et(a), "end_et": _et(b), "s": round((b - a) / 1e9, 1), "triggers": trig,
                       "peak_pct": round(max(f.peak for f in inside) * 100, 3),
                       "per_board_only": trig == [dg.TRIGGER_BOARD],
                       "max_level_gap": None if gap is None else round(gap, 3),
                       "false_fire": bool(tol is not None and gap is not None and gap <= tol), **where(a)})
    return {"fires": [{"trigger": f.trigger, "start_et": _et(f.start_ns), "end_et": _et(f.end_ns),
                       "size_pct": round(f.size * 100, 3), "peak_pct": round(f.peak * 100, 3), **where(f.start_ns)}
                      for f in fires], "blocks": blocks}


def day_dislocations(env: s8.Env, d: date, front: dict[str, dict[str, str]], cal: dict[str, Any],
                     month_set: str = "development") -> dict[str, Any]:
    path = _dir(env, month_set) / f"{d.isoformat()}.json"
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if "skip" in old or old.get("v") == DAY_VERSION:
            return old  # an older day file is recomputed (it has the per-board series only)
    try:
        futs = s8.day_futures(env, d, front)
    except s8.CheckRefused as e:
        out = {"date": d.isoformat(), "skip": str(e)}
        atomic_write_json(path, out)
        return out
    cfg = env.features["mapping"]
    era = mp.board_era(d, cfg["skylit_stamp_change_date"])
    st = env.settings
    blackouts = cal_mod.blackout_windows(cal, d, before_min=st["blackout_before_min"], after_min=st["blackout_after_min"],
                                         lead_sec=st["news_flatten_lead_sec"])
    spans = [(int(a.timestamp()) * mp.NS, int(b.timestamp()) * mp.NS) for a, b in blackouts]
    open_ns = int(datetime.combine(d, time(9, 30), tzinfo=s8.ET).timestamp()) * mp.NS
    out: dict[str, Any] = {"date": d.isoformat(), "era": era, "v": DAY_VERSION, "chains": {},
                           "futures_mean_1min_range": {root: _mean_minute_range(f, open_ns, s8._session_ns(env, d)[1])
                                                       for root, f in futs.items()}}
    gap = cfg["ratio_gap_reset_sec"]
    gparams = dg.GuardParams.from_features(env.features)
    for m, md in s8.iter_day_maps(env, d, DISLOC_MAPS, futs):  # low memory: one chain in memory at a time (§11)
        fut = futs[s8.CHAIN_ROOT[m]]
        A = np.flatnonzero(md.applied)
        tc = mp.content_time_ns(md.as_of[A], mp.content_offset_ms(cfg, m, era))
        t_apply = mp.apply_times_ns(md.as_of[A], np.array(md.resolution, dtype=object)[A], st.get("map_latency_sec"))
        with np.errstate(divide="ignore", invalid="ignore"):
            r = mp.last_price_at_or_before(fut.ts, fut.px, tc) / md.spot[A]
        ok = np.isfinite(r)
        tc, t_apply, r = tc[ok], t_apply[ok], r[ok]
        r_in = mp.ratio_in_force(tc, r, cfg["ratio_window_sec"][m], gap, cfg["ratio_outlier_bps"], cfg["ratio_reaccept_n"])
        chain: dict[str, Any] = {"boards": int(len(tc)), "resolution": sorted(set(md.resolution))}
        # per board at its content time; the R the bot uses at its apply time (owner 2026-10-10)
        for name, t_s, r_s in (("board", tc, r), ("in_force", t_apply, r_in)):
            rng = trailing_range(t_s, r_s, mp.gap_segments(t_s, gap))
            chain[name] = {"median_range_bps": round(float(np.median(rng)) * 1e4, 3) if len(rng) else None, "events": {}}
            for thr in THRESHOLDS:
                chain[name]["events"][str(thr)] = [
                    {"start_et": _et(t0), "flagged_s": round((t1 - t0) / 1e9, 1), "peak_pct": round(p * 100, 3),
                     "min_after_open": round((t0 - open_ns) / 60e9, 1),
                     "first_minutes": bool(t0 - open_ns < FIRST_MINUTES * 60 * mp.NS),
                     "in_news_blackout": any(a <= t0 < b for a, b in spans)} for t0, t1, p in events(t_s, rng, thr)]
        if m in cfg["disl_guard_maps"]:
            rel = s8.node_relevance(env, md)[0]
            top = np.full(len(md.as_of), np.nan)
            np.fmax.at(top, md.s_board[rel], md.s_strike[rel])
            tol, src = false_fire_tolerance(env, m)
            chain["guard"] = _guard_day(m, gparams, t_apply, r, r_in, open_ns, spans, top[A][ok], tol)
            chain["guard"]["tolerance"] = {"value": tol, "setting": src}
        out["chains"][m] = chain
        del md
        gc.collect()
    atomic_write_json(path, out)
    del futs
    gc.collect()
    return out


def development_days(env: s8.Env, cal: dict[str, Any], month_set: str = "development") -> list[date]:
    """Every trading day of ledger months 1-6 (or of the walk-forward months 7-9)."""
    return sorted(d for k in MONTH_SETS[month_set] for d in months_mod.days_of(k, env.settings)
                  if cal_mod.is_trading_day(d, cal))


def free_mb() -> int | None:
    """Free physical memory (MB) on Windows; None elsewhere (no guard)."""
    if sys.platform != "win32":
        return None
    import ctypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    st = MemoryStatus()
    st.dwLength = ctypes.sizeof(MemoryStatus)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
    return int(st.ullAvailPhys // (1024 * 1024))


def _day_done(env: s8.Env, d: date, month_set: str = "development") -> bool:
    path = _dir(env, month_set) / f"{d.isoformat()}.json"
    if not path.exists():
        return False
    x = json.loads(path.read_text(encoding="utf-8"))
    return "skip" in x or x.get("v") == DAY_VERSION


PEAK_TAG = "CHILD_PEAK_GB"


def _run_day_in_child(d: date, month_set: str = "development") -> float | None:
    """One day in a fresh Python process (owner 2026-10-10: a long run in one process ran the laptop out of memory),
    so nothing stays in memory between days. Returns the day process's own peak memory (GB; the job records it,
    owner 2026-10-16: the parent's 0.1 GB hid the 2.5 GB a day used)."""
    return run_cli_in_child(["step8b", "dislocations", "--dates", d.isoformat(), "--months", month_set])


def run_cli_in_child(argv: list[str]) -> float | None:
    """`nqbot <argv>` in a fresh Python process; returns its peak memory (GB). Shared by the step-8b jobs."""
    code = ("import sys, psutil; from nqbot.cli import main; rc = main(sys.argv[1:]); i = psutil.Process().memory_info(); "
            f"print('{PEAK_TAG}', getattr(i, 'peak_wset', 0) / 2**30 or i.rss / 2**30); sys.exit(rc)")
    r = subprocess.run([sys.executable, "-c", code, *argv], capture_output=True, text=True)
    if r.returncode != 0:
        raise s8.CheckRefused(f"{' '.join(argv)}: the day's process failed ({r.returncode}): "
                              f"{(r.stderr or r.stdout)[-400:]}")
    return child_peak_gb(r.stdout)


def child_peak_gb(stdout: str) -> float | None:
    for line in reversed((stdout or "").splitlines()):
        if line.startswith(PEAK_TAG):
            return round(float(line.split()[1]), 3)
    return None


def check_dislocations(env: s8.Env, dates: list[date] | None = None, isolate: bool = False,
                       min_free_mb: int = MIN_FREE_MB, month_set: str = "development") -> dict[str, Any]:
    """isolate: each day not yet saved runs in its own process, after checking free memory (the CLI does this).
    month_set: "development" (months 1-6) or "walk-forward" (months 7-9, automated counts only, §7 rule 2b)."""
    if month_set not in MONTH_SETS:
        raise s8.CheckRefused(f"--months must be one of {list(MONTH_SETS)} (the top-up and demo months join when they "
                              "exist, steps 18/20 and 24)")
    cal = env.cal if env.cal is not None else cal_mod.load()
    days = sorted(dates) if dates else development_days(env, cal, month_set)
    for d in days:
        months_mod.require_development(d, env.settings, MONTH_SETS[month_set])  # §7 rule 2b
    front = s8.front_symbols(env)
    if isolate and len(days) > 1:
        for d in days:
            if _day_done(env, d, month_set):
                continue
            mb = free_mb()
            if mb is not None and mb < min_free_mb:
                raise s8.CheckRefused(f"stopped before {d}: {mb} MB free (< {min_free_mb}); every finished day is saved, "
                                      "run the same command again to resume")
            _run_day_in_child(d, month_set)
    for d in days:
        x = day_dislocations(env, d, front, cal, month_set)
        if "skip" in x:
            env.echo(f"  {d}: skipped ({x['skip']})")
        else:
            env.echo(f"  {d}: " + ", ".join(f"{m} {[len(c['board']['events'][str(t)]) for t in THRESHOLDS]} -> "
                                            f"{[len(c['in_force']['events'][str(t)]) for t in THRESHOLDS]}"
                                            for m, c in x["chains"].items()))
    # the summary always covers every saved day file of months 1-6, whatever dates this run was given (owner
    # 2026-10-11: each day's own process used to overwrite it with that one day)
    saved, pending = [], []
    for d in development_days(env, cal, month_set):
        if _day_done(env, d, month_set):
            saved.append(json.loads((_dir(env, month_set) / f"{d.isoformat()}.json").read_text(encoding="utf-8")))
        else:
            pending.append(d.isoformat())
    report = summarize(saved)
    report["pending"] = pending
    report["months"] = month_set
    if pending:
        env.echo(f"  {len(pending)} {month_set} days not counted yet (first {pending[0]}): the summary leaves them out")
    s8.write_report(env, _report_name(month_set), report)
    md = s8.reports_dir(env.paths) / f"{_report_name(month_set)}.md"
    atomic_write_text(md, to_markdown(report))
    g = report["guard"]
    env.echo("guard: " + ", ".join(f"{m} {c['blocks']} blocks ({c['per_board_only']} per-board only, "
                                    f"{c['false_fires']} false, {c['false_fires_per_week']}/week)"
                                    for m, c in g["chains"].items())
             + (" -- ABOVE about one a week: revisit (owner)" if g["over_limit"] else ""))
    env.echo(f"summary: {md}")
    return report


def summarize(per_day: list[dict[str, Any]]) -> dict[str, Any]:
    used = [x for x in per_day if "skip" not in x]
    out: dict[str, Any] = {"days": len(used), "skipped": {x["date"]: x["skip"] for x in per_day if "skip" in x},
                           "chains": {}}
    for m in DISLOC_MAPS:
        out["chains"][m] = {name: _series_summary(used, m, name) for name in SERIES}
    out["guard"] = _guard_summary(used)
    out["per_day"] = [{"date": x["date"], "era": x["era"], "futures_mean_1min_range": x["futures_mean_1min_range"],
                       **{f"{m}_{name}": {"events": [len(x["chains"][m][name]["events"][str(t)]) for t in THRESHOLDS],
                                          "median_range_bps": x["chains"][m][name]["median_range_bps"]}
                          for m in DISLOC_MAPS for name in SERIES}} for x in used]
    return out


def _guard_summary(used: list[dict[str, Any]]) -> dict[str, Any]:
    """Fires per trigger, blocks, minutes blocked, the blocks where only the per-board trigger fired (a column) and the
    false fires per chain, with the false fires' weekly rate against about one a week (owner 2026-10-14)."""
    weeks = len(used) / TRADING_DAYS_PER_WEEK
    chains: dict[str, Any] = {}
    for m in DISLOC_MAPS:
        g = [(x["date"], x["chains"][m]["guard"]) for x in used if "guard" in x["chains"].get(m, {})]
        if not g:
            continue
        blocks = [{"date": d, **b} for d, gd in g for b in gd["blocks"]]
        false = sum(b["false_fire"] for b in blocks)
        chains[m] = {"fires": {t: sum(f["trigger"] == t for _, gd in g for f in gd["fires"]) for t in dg.TRIGGERS},
                     "blocks": len(blocks), "days_affected": len({b["date"] for b in blocks}),
                     "minutes_blocked": round(sum(b["s"] for b in blocks) / 60, 1),
                     "per_board_only": sum(b["per_board_only"] for b in blocks), "false_fires": false,
                     "false_fires_per_week": round(false / weeks, 3) if weeks else None,
                     "tolerance": g[0][1].get("tolerance"), "list": blocks}
    total_false = sum(c["false_fires"] for c in chains.values())
    per_week = round(total_false / weeks, 3) if weeks else None
    return {"chains": chains, "false_fires_per_week_all": per_week,
            "over_limit": bool(per_week is not None and per_week > FALSE_FIRES_PER_WEEK_MAX)}


def _series_summary(used: list[dict[str, Any]], m: str, name: str) -> dict[str, Any]:
    res: dict[str, Any] = {}
    for thr in THRESHOLDS:
        ev = [(x["date"], e) for x in used for e in x["chains"][m][name]["events"][str(thr)]]
        spans = np.array([e["flagged_s"] for _, e in ev]) if ev else np.zeros(0)
        res[str(thr)] = {"events": len(ev), "days_affected": len({d for d, _ in ev}),
                         "flagged_s_median": round(float(np.median(spans)), 1) if len(spans) else None,
                         "flagged_s_p90": round(float(np.quantile(spans, 0.9)), 1) if len(spans) else None}
        if thr == DETAIL_THRESHOLD:
            res[str(thr)]["list"] = [{"date": d, **e} for d, e in ev]
            res[str(thr)]["in_news_blackout"] = sum(e["in_news_blackout"] for _, e in ev)
            res[str(thr)]["first_minutes"] = sum(e["first_minutes"] for _, e in ev)
    return res


def to_markdown(rep: dict[str, Any]) -> str:
    label = {"board": "per-board R", "in_force": "R the bot uses"}
    title = ("development months 1-6" if rep.get("months", "development") == "development"
             else "walk-forward months 7-9")
    lines = [f"# Mapping dislocations, {title} (owner 2026-10-09/10)", "",
             f"{rep['days']} days ({len(rep['skipped'])} skipped: {', '.join(rep['skipped']) or 'none'}; "
             f"{len(rep.get('pending', []))} not counted yet{': ' + ', '.join(rep['pending']) if rep.get('pending') else ''}"
             "). R moving more "
             f"than the threshold within {WINDOW_S // 60} minutes (trailing range); events merge flagged boards less than "
             f"{WINDOW_S} s apart (a single jump flags about 2 minutes). Two series: the per-board R (each board's own "
             "ratio at its content time) and the R the bot uses (F1.7: QQQ/SPY trailing 60-s median, SPX per board with "
             "F1.2) at each board's apply time.", "",
             "| Chain | Threshold | Events (per-board / bot's R) | Days affected | Flagged span median / p90 (s), bot's R |",
             "|---|---|---|---|---|"]
    for m, res in rep["chains"].items():
        for thr in res["board"]:
            b, f = res["board"][thr], res["in_force"][thr]
            lines.append(f"| {m} | {float(thr) * 100:g}% | {b['events']} / {f['events']} | {b['days_affected']} / "
                         f"{f['days_affected']} | {f['flagged_s_median']} / {f['flagged_s_p90']} |")
    for m, res in rep["chains"].items():
        r = res["in_force"][str(DETAIL_THRESHOLD)]
        lines += ["", f"## {m}: the {DETAIL_THRESHOLD * 100:g}% events on the {label['in_force']} ({r['events']}; "
                      f"{r['in_news_blackout']} in a news blackout, {r['first_minutes']} in the first {FIRST_MINUTES} "
                      "minutes)", "",
                  "| Date | Start ET | Min after open | Flagged (s) | Peak range | News blackout | First minutes |",
                  "|---|---|---:|---:|---:|---|---|"]
        for e in r["list"]:
            lines.append(f"| {e['date']} | {e['start_et']} | {e['min_after_open']} | {e['flagged_s']} | "
                         f"{e['peak_pct']}% | {'yes' if e['in_news_blackout'] else ''} | "
                         f"{'yes' if e['first_minutes'] else ''} |")
    g = rep.get("guard", {"chains": {}})
    lines += ["", "## Dislocation guard (owner 2026-10-14, F1.8)", "",
              "Blocks start on either trigger (per-board R with both ends held by two boards within 10 s, or the bot's "
              "R), each lasting until 60 s after its last flagged board, extended by any later flag. Per-board only = a "
              "block in which only the per-board trigger fired (not a verdict). False fire = a block during which the "
              "levels mapped with the bot's R stayed within the chain's tolerance of the levels mapped with a hindsight "
              "R (centred median of the per-board R over +-60 s, report only) at every relevant node: trading would "
              "have been safe without it. Revisit if false fires exceed about one a week: "
              f"{g.get('false_fires_per_week_all')} a week over all chains"
              f"{' -- ABOVE: revisit' if g.get('over_limit') else ''}.", "",
              "| Chain | Fires per-board / bot's R | Blocks | Days | Minutes blocked | Per-board only | False fires "
              "(per week) | Tolerance |", "|---|---|---:|---:|---:|---:|---|---|"]
    for m, c in g["chains"].items():
        tol = c.get("tolerance") or {}
        lines.append(f"| {m} | {c['fires'][dg.TRIGGER_BOARD]} / {c['fires'][dg.TRIGGER_BOT]} | {c['blocks']} | "
                     f"{c['days_affected']} | {c['minutes_blocked']} | {c['per_board_only']} | {c['false_fires']} "
                     f"({c['false_fires_per_week']}) | {tol.get('value')} ({tol.get('setting')}) |")
    blocks = [(m, b) for m, c in g["chains"].items() for b in c["list"]]
    if blocks:
        lines += ["", "| Date | Chain | Block ET | Seconds | Triggers | Peak range | Largest level gap (pts) | "
                      "Min after open | News blackout | Per-board only | False fire |",
                  "|---|---|---|---:|---|---:|---:|---:|---|---|---|"]
        for m, b in sorted(blocks, key=lambda x: (x[1]["date"], x[1]["start_et"], x[0])):
            lines.append(f"| {b['date']} | {m} | {b['start_et']}-{b['end_et']} | {b['s']} | {' + '.join(b['triggers'])} "
                         f"| {b['peak_pct']}% | {b['max_level_gap']} | {b['min_after_open']} | "
                         f"{'yes' if b['in_news_blackout'] else ''} | {'yes' if b['per_board_only'] else ''} | "
                         f"{'yes' if b['false_fire'] else ''} |")
    lines += ["", "## Per day", "",
              "| Date | Era | NQ / ES mean 1-min range | " + " | ".join(
                  f"{m} events 0.05/0.1/0.2% (board → bot's R)" for m in DISLOC_MAPS) + " | SPX median 2-min range bps (board / bot's R) |",
              "|---|---|---|" + "---|" * len(DISLOC_MAPS) + "---|"]
    for p_ in rep.get("per_day", []):
        fr = p_["futures_mean_1min_range"]
        cells = [f"{'/'.join(map(str, p_[f'{m}_board']['events']))} → {'/'.join(map(str, p_[f'{m}_in_force']['events']))}"
                 for m in DISLOC_MAPS]
        lines.append(f"| {p_['date']} | {p_['era'].replace('_stamp_change', '')} | {fr.get('NQ')} / {fr.get('ES')} | "
                     + " | ".join(cells) + f" | {p_['SPX_board']['median_range_bps']} / {p_['SPX_in_force']['median_range_bps']} |")
    return "\n".join(lines) + "\n"


class DislocationsJob:
    """`nqbot run step8b_dislocations --until HH:MM` (owner 2026-10-14): the months 1-6 dislocation count as a resumable
    job, one trading day per chunk, each day in its own process after the free-memory check (a day without enough
    memory stops the run cleanly; it re-runs next time). Reads stored data only; holds the Databento lock so no pull
    writes the trades it reads. The summary (`dislocations.md`) is rewritten after every day."""

    name = "step8b_dislocations"

    def __init__(self, month_set: str = "development"):
        from nqbot.jobs.lock import DATABENTO

        self.month_set = month_set
        self.locks = (DATABENTO,)

    def _env(self, ctx: Any) -> s8.Env:
        from nqbot.config import load_features

        return s8.Env(settings=ctx.settings, features=load_features(), paths=ctx.paths, log=ctx.log, echo=ctx.log.info)

    def chunks(self, ctx: Any) -> list[str]:
        """One chunk per day, named with the day-file version (owner 2026-10-19: a new version is a new chunk, so the
        runner, which remembers finished chunks, re-runs every day after a version change)."""
        env = self._env(ctx)
        return [f"{d.isoformat()}:v{DAY_VERSION}" for d in development_days(env, env.cal or cal_mod.load(), self.month_set)]

    def run_chunk(self, chunk_id: str, ctx: Any) -> dict[str, Any] | None:
        from nqbot.jobs.runner import StopChunk

        d = date.fromisoformat(chunk_id.split(":")[0])
        if _day_done(self._env(ctx), d, self.month_set):
            return None
        mb = free_mb()
        if mb is not None and mb < MIN_FREE_MB:
            raise StopChunk(f"{mb} MB free (< {MIN_FREE_MB}) before {d}")
        peak = _run_day_in_child(d, self.month_set)
        ctx.log.info("%s: %s day process peak %s GB, %s MB free before it", self.name, d, peak, mb)
        return {"peak_gb": peak}
