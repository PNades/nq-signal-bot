"""`nqbot step8b dislocations` (owner 2026-10-09, read-only, development months 1-6): how often the mapping ratio
moves fast, per chain (QQQ, SPY, SPX; NDXP's history spot is a futures price, so it is left out).

Per-board R = F(content time) / spot over the applied boards (the approved ingest rule, §5.1). A board is flagged at a
threshold when R's range over the trailing 2 minutes ((max - min) / median of the boards in (t - 120 s, t], within
one gap segment) exceeds it. Flagged boards less than 2 minutes apart form one event; its flagged span runs from the
first to the last flagged board (a single jump flags about 2 minutes). For each event: start time, minutes after the
open, peak range, and whether it starts inside a news blackout (§9) or the first minutes after the open.

One day at a time; each day's events are saved under `<reports_dir>/step8b/dislocations/<date>.json`, so a stopped
run resumes. The summary is `<reports_dir>/step8b/dislocations.md` (and a timestamped json).
"""

from __future__ import annotations

import gc
import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

import numpy as np

from nqbot import calendar as cal_mod
from nqbot import months as months_mod
from nqbot.checks import step8b as s8
from nqbot.features import mapping as mp
from nqbot.storage import atomic_write_json, atomic_write_text

DISLOC_MAPS = ("QQQ", "SPY", "SPX")
WINDOW_S = 120  # owner 2026-10-09: R moving more than the threshold within 2 minutes
THRESHOLDS = (0.0005, 0.001, 0.002)  # owner 2026-10-09: 0.05%, 0.1%, 0.2%
DETAIL_THRESHOLD = 0.001  # owner: time of day, news blackout and first minutes for the 0.1% events
FIRST_MINUTES = 15  # "the first minutes after the open"


def _dir(env: s8.Env) -> Path:
    return s8.reports_dir(env.paths) / "dislocations"


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


def day_dislocations(env: s8.Env, d: date, front: dict[str, dict[str, str]], cal: dict[str, Any]) -> dict[str, Any]:
    path = _dir(env) / f"{d.isoformat()}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        maps, futs = s8._day_inputs(env, d, front)
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
    out: dict[str, Any] = {"date": d.isoformat(), "era": era, "chains": {}}
    for m in DISLOC_MAPS:
        md, fut = maps[m], futs[s8.CHAIN_ROOT[m]]
        A = np.flatnonzero(md.applied)
        tc = mp.content_time_ns(md.as_of[A], mp.content_offset_ms(cfg, m, era))
        with np.errstate(divide="ignore", invalid="ignore"):
            r = mp.last_price_at_or_before(fut.ts, fut.px, tc) / md.spot[A]
        ok = np.isfinite(r)
        tc, r = tc[ok], r[ok]
        rng = trailing_range(tc, r, mp.gap_segments(tc, cfg["ratio_gap_reset_sec"]))
        chain: dict[str, Any] = {"boards": int(len(tc)), "resolution": sorted(set(md.resolution)), "events": {}}
        for thr in THRESHOLDS:
            chain["events"][str(thr)] = [
                {"start_et": _et(t0), "flagged_s": round((t1 - t0) / 1e9, 1), "peak_pct": round(p * 100, 3),
                 "min_after_open": round((t0 - open_ns) / 60e9, 1),
                 "first_minutes": bool(t0 - open_ns < FIRST_MINUTES * 60 * mp.NS),
                 "in_news_blackout": any(a <= t0 < b for a, b in spans)} for t0, t1, p in events(tc, rng, thr)]
        out["chains"][m] = chain
    atomic_write_json(path, out)
    del maps, futs
    gc.collect()
    return out


def development_days(env: s8.Env, cal: dict[str, Any]) -> list[date]:
    """Every trading day of ledger months 1-6."""
    return sorted(d for k in months_mod.DEVELOPMENT for d in months_mod.days_of(k, env.settings)
                  if cal_mod.is_trading_day(d, cal))


def check_dislocations(env: s8.Env, dates: list[date] | None = None) -> dict[str, Any]:
    cal = env.cal if env.cal is not None else cal_mod.load()
    days = sorted(dates) if dates else development_days(env, cal)
    for d in days:
        months_mod.require_development(d, env.settings)  # §7 rule 2b
    front = s8.front_symbols(env)
    per_day = []
    for d in days:
        x = day_dislocations(env, d, front, cal)
        per_day.append(x)
        if "skip" in x:
            env.echo(f"  {d}: skipped ({x['skip']})")
        else:
            env.echo(f"  {d}: " + ", ".join(f"{m} {[len(c['events'][str(t)]) for t in THRESHOLDS]}"
                                            for m, c in x["chains"].items()))
    report = summarize(per_day)
    s8.write_report(env, "dislocations", report)
    atomic_write_text(s8.reports_dir(env.paths) / "dislocations.md", to_markdown(report))
    env.echo(f"summary: {s8.reports_dir(env.paths) / 'dislocations.md'}")
    return report


def summarize(per_day: list[dict[str, Any]]) -> dict[str, Any]:
    used = [x for x in per_day if "skip" not in x]
    out: dict[str, Any] = {"days": len(used), "skipped": {x["date"]: x["skip"] for x in per_day if "skip" in x},
                           "chains": {}}
    for m in DISLOC_MAPS:
        res: dict[str, Any] = {}
        for thr in THRESHOLDS:
            ev = [(x["date"], e) for x in used for e in x["chains"][m]["events"][str(thr)]]
            spans = np.array([e["flagged_s"] for _, e in ev]) if ev else np.zeros(0)
            res[str(thr)] = {"events": len(ev), "days_affected": len({d for d, _ in ev}),
                             "flagged_s_median": round(float(np.median(spans)), 1) if len(spans) else None,
                             "flagged_s_p90": round(float(np.quantile(spans, 0.9)), 1) if len(spans) else None}
            if thr == DETAIL_THRESHOLD:
                res[str(thr)]["list"] = [{"date": d, **e} for d, e in ev]
                res[str(thr)]["in_news_blackout"] = sum(e["in_news_blackout"] for _, e in ev)
                res[str(thr)]["first_minutes"] = sum(e["first_minutes"] for _, e in ev)
        out["chains"][m] = res
    return out


def to_markdown(rep: dict[str, Any]) -> str:
    lines = ["# Mapping dislocations, development months 1-6 (owner 2026-10-09)", "",
             f"{rep['days']} days ({len(rep['skipped'])} skipped). Per-board R moving more than the threshold within "
             f"{WINDOW_S // 60} minutes (trailing range); events merge flagged boards less than {WINDOW_S} s apart; the "
             "flagged span of a single jump is about 2 minutes.", "",
             "| Chain | Threshold | Events | Days affected | Flagged span median / p90 (s) |", "|---|---|---:|---:|---|"]
    for m, res in rep["chains"].items():
        for thr, r in res.items():
            lines.append(f"| {m} | {float(thr) * 100:g}% | {r['events']} | {r['days_affected']} | "
                         f"{r['flagged_s_median']} / {r['flagged_s_p90']} |")
    for m, res in rep["chains"].items():
        r = res[str(DETAIL_THRESHOLD)]
        lines += ["", f"## {m}: the {DETAIL_THRESHOLD * 100:g}% events ({r['events']}; {r['in_news_blackout']} in a news "
                      f"blackout, {r['first_minutes']} in the first {FIRST_MINUTES} minutes)", "",
                  "| Date | Start ET | Min after open | Flagged (s) | Peak range | News blackout | First minutes |",
                  "|---|---|---:|---:|---:|---|---|"]
        for e in r["list"]:
            lines.append(f"| {e['date']} | {e['start_et']} | {e['min_after_open']} | {e['flagged_s']} | "
                         f"{e['peak_pct']}% | {'yes' if e['in_news_blackout'] else ''} | "
                         f"{'yes' if e['first_minutes'] else ''} |")
    return "\n".join(lines) + "\n"
