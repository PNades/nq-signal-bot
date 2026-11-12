"""Step 13b checks, jobs and reports (owner 2026-11-10; SPEC §12 step 13, §13 Q-step13b, Q-flexfarm, Q-dryrun13 (9)
(14b), Q-flipretest, Q-fibvariants; development months 1-4 only; outside the build key).

- `events-day D`: the smoke run on one development day: the engine stage and the labeler on one session built from a
  carry-only warm-up, twice (determinism), with time, peak memory, decision points, pairs by style x order type x RR
  band, disk per folder and the labeler time, extrapolated to months 1-4 and to the 251-session window;
- `counts`: the reports the months 1-4 build makes possible before the labeler: style-(c) firings per event (more than
  `FIRINGS_ALERT` stops the labeler until the owner OKs it, Q-step13b (2)), the flip checkpoint (Q-flipretest), the
  golden-zone counts (Q-fibvariants / Q-gzsplit) and the 13d build counts (Q-dryrun13 (9));
- job `step13_slippage` (`slippage-plan`, `slippage-day D`, `slippage-report`): the `exit_slippage_ticks` measurement
  from `mbp-1` (§12 step 13) and the `be_offset_ticks` re-check (Q-dryrun13 (14b));
- job `step13_labels` (`labels-plan [--firings-ok] [--set k=v]`, `labels-day D`, `labels-report`): the labeler
  (`labeler/`), its own key, the low-RR extension's identity check on every session and the RR-band counts;
- `budget`: the step-13 budget re-estimated from the measured months 1-4 rebuild (Q-step12, Q-step13plan (7)).
"""

from __future__ import annotations

import json
import math
import os
import time as _time
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

from nqbot import months as months_mod
from nqbot.checks import feature_build as fb
from nqbot.checks import session_build as sb
from nqbot.checks.step8b import Env
from nqbot.events.common import GROUPS, GAMEPLAN, group_of_subtype, read_meta, write_table
from nqbot.features.session_tape import ET, NS, TICK
from nqbot.storage import atomic_write_json, atomic_write_text

JOB_SLIP, JOB_LABELS = "step13_slippage", "step13_labels"
REPORT_CHUNK = "report"
MONTHS_14 = (1, 2, 3, 4)
FIRINGS_ALERT = 5          # owner 2026-11-10 (Q-step13b (2)): more style-(c) firings than this on any event: tell the owner
SMALL_BUCKET = 100         # §7 rule 6: a bucket under ~100 events is flagged
FULL_WINDOW_SESSIONS = 251  # the 12-month window (feature build 857b36d5b175)
SLIP_SIZING_TICKS = (1, 2, 3, 4)   # the sensitivity of the measured slippage to the value used for sizing
SLIP_Q = 0.95              # §12 step 13: the 95th percentile


class Refused(Exception):
    pass


# --- the build ------------------------------------------------------------------------------------------------------

def build_doc(env: Env) -> dict[str, Any]:
    doc = fb.load_plan(env)
    if not set(MONTHS_14) <= set(doc["months"]):
        raise Refused(f"the feature-build plan covers ledger months {doc['months']}, not 1-4 "
                      "(`uv run nqbot features plan --months 1-4`)")
    return doc


def sessions_14(env: Env, doc: dict) -> list[date]:
    return [date.fromisoformat(d) for d in doc["sessions"]
            if months_mod.month_index(date.fromisoformat(d), env.settings) in MONTHS_14]


def out_dir(env: Env, doc: dict) -> Path:
    return fb.out_dir(env, doc["key"])


def report_dir(env: Env) -> Path:
    return env.paths.reports_dir / "step13"


def day_summary(env: Env, doc: dict, day: date) -> dict:
    p = fb.summary_file(env, doc["key"], day)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def built_days(env: Env, doc: dict) -> list[date]:
    base = out_dir(env, doc)
    return [d for d in sessions_14(env, doc) if "pass2" in day_summary(env, doc, d)
            and (base / "decisions" / f"{d.isoformat()}.parquet").exists()]


def _read(path: Path) -> list[dict]:
    return pq.read_table(path).to_pylist() if path.exists() else []


def _fmt(ns: int | None) -> str:
    return "" if ns is None else datetime.fromtimestamp(ns / NS, ET).strftime("%H:%M:%S")


# --- the labeler stage on one session -------------------------------------------------------------------------------

def label_one(env: Env, base: Path, day: date, p, identity: bool = True) -> dict[str, Any]:
    """Label one built session from `base` (decisions/ + its metadata); returns the outputs and their counts."""
    from nqbot.clients import mbp1
    from nqbot.features import session_tape as st
    from nqbot.labeler import labeler as lb

    iso = day.isoformat()
    dpath = base / "decisions" / f"{iso}.parquet"
    meta = read_meta(dpath)
    decisions = _read(dpath)
    t0 = _time.monotonic()
    tape = st.load_tape(env.db, sb.plan(env), "NQ", day)
    if tape is None or tape.symbol != meta.get("nq_symbol"):
        raise Refused(f"{day}: the stored NQ trades ({None if tape is None else tape.symbol}) are not the build's "
                      f"({meta.get('nq_symbol')})")
    book = mbp1.load(env.db.raw_path("mbp-1", tape.symbol, day))
    t_load = _time.monotonic() - t0
    out = lb.label_session(decisions, tape, book, meta, p)
    if identity:
        from dataclasses import replace

        t1 = _time.monotonic()
        off = lb.label_session(decisions, tape, book, meta, replace(p, extension=False))
        out["info"].update(lb.identity_check(out["pairs"], off["pairs"]),
                           identity_sec=round(_time.monotonic() - t1, 1))
    out["info"].update(load_sec=round(t_load, 1), book=book is not None, decisions=len(decisions))
    return out


# --- labels: the job -------------------------------------------------------------------------------------------------

def labels_plan_file(env: Env) -> Path:
    return env.paths.data_dir / "jobs" / JOB_LABELS / "plan.json"


def _label_params(env: Env, overrides: dict | None):
    from nqbot.labeler import params as lp

    try:
        return lp.params(env.settings, env.features, overrides)
    except KeyError as e:
        raise Refused(str(e)) from None


def labels_plan(env: Env, firings_ok: bool = False, overrides: dict | None = None) -> dict[str, Any]:
    from nqbot.labeler import params as lp

    doc = build_doc(env)
    days = sessions_14(env, doc)
    missing = [d for d in days if d not in set(built_days(env, doc))]
    if missing:
        raise Refused(f"the months 1-4 build {doc['key']} is not complete ({len(missing)} sessions without pass 2, "
                      f"first {missing[0]}): finish `nqbot run feature_build` first")
    worst = max((day_summary(env, doc, d)["pass2"].get("firings_c_max", 0) for d in days), default=0)
    if worst > FIRINGS_ALERT and not firings_ok:
        raise Refused(f"an event has {worst} style-(c) firings (> {FIRINGS_ALERT}): the owner reads the examples in "
                      f"{report_dir(env) / 'counts.md'} (`nqbot step13 counts`) first, then `labels-plan --firings-ok` "
                      "(owner 2026-11-10, SPEC §13 Q-step13b (2))")
    p = _label_params(env, overrides)
    lkey = lp.label_key(doc["key"], p)
    plan = {"build_key": doc["key"], "label_key": lkey, "overrides": overrides or {}, "sessions": [d.isoformat()
            for d in days], "firings_ok": firings_ok, "params": lp.describe(p), "written": datetime.now(ET).isoformat()}
    atomic_write_json(labels_plan_file(env), plan)
    atomic_write_json(out_dir(env, doc) / "labels" / lkey / "label_plan.json", plan)
    return plan


def load_labels_plan(env: Env) -> tuple[dict, dict, Any]:
    from nqbot.labeler import params as lp

    path = labels_plan_file(env)
    if not path.exists():
        raise Refused("no labels plan: `nqbot step13 labels-plan` first")
    plan = json.loads(path.read_text(encoding="utf-8"))
    doc = build_doc(env)
    if doc["key"] != plan["build_key"]:
        raise Refused(f"the labels plan is for build {plan['build_key']}, the feature-build plan is now {doc['key']}: "
                      "re-plan the labels")
    p = _label_params(env, plan["overrides"])
    if lp.label_key(doc["key"], p) != plan["label_key"]:
        raise Refused("the labeler's settings or code changed since `labels-plan`: re-plan the labels")
    return plan, doc, p


def label_paths(env: Env, doc: dict, lkey: str, day: date) -> dict[str, Path]:
    base = out_dir(env, doc)
    iso = day.isoformat()
    return {"pairs": base / "pairs" / lkey / f"{iso}.parquet", "fills": base / "fills" / lkey / f"{iso}.parquet",
            "paths": base / "paths" / lkey / f"{iso}.parquet",
            "summary": base / "labels" / lkey / "days" / f"{iso}.json"}


def labels_day(env: Env, day: date) -> dict[str, Any]:
    plan, doc, p = load_labels_plan(env)
    months_mod.require_development(day, env.settings)
    if day.isoformat() not in plan["sessions"]:
        raise Refused(f"{day} is not a session of the labels plan")
    t0 = _time.monotonic()
    out = label_one(env, out_dir(env, doc), day, p, identity=True)
    lp_ = label_paths(env, doc, plan["label_key"], day)
    meta = {"day": day.isoformat(), "build_key": doc["key"], "label_key": plan["label_key"]}
    for k in ("pairs", "fills", "paths"):
        write_table(lp_[k], out[k], meta)
    info = {**out["info"], "total_sec": round(_time.monotonic() - t0, 1),
            "bytes": {k: lp_[k].stat().st_size for k in ("pairs", "fills", "paths")}}
    atomic_write_json(lp_["summary"], info)
    if not info.get("identity_ok", True):
        raise Refused(f"{day}: the low-RR extension changed the >= 0.8 pairs or their labels (Q-flexfarm (1))")
    env.echo(f"{day}: {info['pairs']} pairs, {info['label_sec']} s, identity {info.get('identity_ok')}")
    return info


def labels_chunk_ids(env: Env) -> list[str]:
    plan = json.loads(labels_plan_file(env).read_text(encoding="utf-8")) if labels_plan_file(env).exists() else None
    if plan is None:
        return []
    return [f"l:{d}:{plan['label_key']}" for d in plan["sessions"]] + [f"{REPORT_CHUNK}:{plan['label_key']}"]


def labels_report(env: Env) -> Path:
    plan, doc, _p = load_labels_plan(env)
    rows = []
    bands: Counter = Counter()
    for d in plan["sessions"]:
        s = label_paths(env, doc, plan["label_key"], date.fromisoformat(d))["summary"]
        if s.exists():
            x = json.loads(s.read_text(encoding="utf-8"))
            rows.append((d, x))
            for k, v in x.get("rr_bands", {}).items():
                bands[k] += v
    lines = [f"# Step 13b labels: build {doc['key']}, labels {plan['label_key']}", "",
             f"Sessions labelled {len(rows)} of {len(plan['sessions'])}; settings and overrides in "
             f"`label_plan.json` ({plan['overrides'] or 'no overrides'}).", "",
             "## Identity check of the low-RR extension (Q-flexfarm (1))", "",
             f"- Sessions checked: {sum(1 for _, x in rows if 'identity_ok' in x)}; all identical: "
             f"{all(x.get('identity_ok') for _, x in rows)}; base rows compared "
             f"{sum(x.get('identity_rows', 0) for _, x in rows):,}; extension rows {sum(x.get('ext_rows', 0) for _, x in rows):,}",
             "", "## Pairs by style, order type and RR band (every pair row; net RR at default costs)", "",
             "| style | order type | RR band | set | pairs |", "|---|---|---|---|---:|"]
    for k, v in sorted(bands.items()):
        lines.append("| " + " | ".join(k.split("|")) + f" | {v:,} |")
    lines += ["", "## The gap rule (owner 2026-11-10/11, Q-step13b (b) (vii), (d))", "",
              "A stop or breakeven exit fills at the worse of its price minus `exit_slippage_ticks` and the last print "
              "of the sweep that triggered it (every print at its trigger timestamp); a flat-by / news exit never "
              "better than the last print of the first sweep at or after its time. Counted over every breakeven rule's "
              "exits of filled pairs (one pair's exit counted once per rule); the mean extra cost is over the changed "
              "exits:", ""]
    gap: dict = {}
    for _, x in rows:
        for k, v in (x.get("gap_rule") or {}).items():
            a = gap.setdefault(k, [0, 0, 0.0])
            a[0] += v["exits"]
            a[1] += v["changed"]
            a[2] += v["mean_extra_ticks"] * v["changed"]
    lines += _table([(k, f"{a[0]:,}", f"{a[1]:,}", f"{a[1] / a[0]:.2%}" if a[0] else "",
                      f"{a[2] / a[1]:.2f}" if a[1] else "") for k, a in sorted(gap.items())],
                    ["exit", "exits", "changed", "share", "mean extra cost (ticks)"])
    tot = sum(x.get("pairs", 0) for _, x in rows)
    lines += ["", f"- Pairs {tot:,}; fills {sum(x.get('fills', 0) for _, x in rows):,}; labeler "
              f"{sum(x.get('label_sec', 0) for _, x in rows) / 3600:.2f} h (+ identity re-run "
              f"{sum(x.get('identity_sec', 0) for _, x in rows) / 3600:.2f} h); disk "
              f"{sum(sum(x.get('bytes', {}).values()) for _, x in rows) / 2**30:.2f} GB"]
    path = report_dir(env) / f"labels_{plan['label_key']}.md"
    atomic_write_text(path, "\n".join(lines) + "\n")
    return path


class LabelsJob:
    """`nqbot run step13_labels --until HH:MM`: one session per chunk in its own process, then the report."""

    name = JOB_LABELS

    def __init__(self) -> None:
        from nqbot.jobs.lock import DATABENTO, SKYLIT

        self.locks = (SKYLIT, DATABENTO)

    def _env(self, ctx: Any) -> Env:
        return _job_env(ctx)

    def chunks(self, ctx: Any) -> list[str]:
        return labels_chunk_ids(self._env(ctx))

    def run_chunk(self, chunk_id: str, ctx: Any) -> dict[str, Any] | None:
        env = self._env(ctx)
        if chunk_id.startswith(REPORT_CHUNK + ":"):
            labels_report(env)
            return None
        _, d, _k = chunk_id.split(":")
        plan, doc, _p = load_labels_plan(env)
        if label_paths(env, doc, plan["label_key"], date.fromisoformat(d))["summary"].exists():
            return None
        return _child(ctx, ["step13", "labels-day", d], chunk_id)


def _job_env(ctx: Any) -> Env:
    from nqbot import calendar as cal_mod
    from nqbot.config import load_features

    return Env(settings=ctx.settings, features=load_features(), paths=ctx.paths, log=ctx.log, echo=ctx.log.info,
               cal=cal_mod.load())


def _child(ctx: Any, argv: list[str], chunk_id: str) -> dict[str, Any]:
    from nqbot.checks.step8b_disloc import MIN_FREE_MB, free_mb, run_cli_in_child
    from nqbot.jobs.runner import StopChunk

    mb = free_mb()
    if mb is not None and mb < MIN_FREE_MB:
        raise StopChunk(f"{mb} MB free (< {MIN_FREE_MB}) before {chunk_id}")
    peak = run_cli_in_child(argv)
    ctx.log.info("%s: process peak %s GB", chunk_id, peak)
    return {"peak_gb": peak}


# --- slippage (§12 step 13) ------------------------------------------------------------------------------------------

def slip_dir(env: Env, doc: dict) -> Path:
    return report_dir(env) / "slippage" / doc["key"] / "days"


def market_fill(tape, book, k: int, d_exit: int, size: float) -> float | None:
    """A stop that touched at trade k, sold (d_exit -1) or bought (+1) at market: the best bid / ask size at the touch,
    the rest at the following trades' prices in sequence (§12 step 13; no depth data)."""
    q = book.at(int(tape.ts[k]))
    if q is None:
        return None
    best, sz = (q[0], q[2]) if d_exit < 0 else (q[1], q[3])
    if best != best:
        return None
    left = float(size)
    take = min(left, max(sz, 0.0))
    cost = take * best
    left -= take
    j = k + 1
    while left > 1e-9 and j < len(tape):
        t = min(left, float(tape.size[j]) or 1.0)
        cost += t * float(tape.price[j])
        left -= t
        j += 1
    if left > 1e-9:
        cost += left * float(tape.price[-1])
    return cost / size


def slippage_day(env: Env, day: date) -> Path:
    from dataclasses import replace

    from nqbot.clients import mbp1
    from nqbot.features import session_tape as st
    from nqbot.labeler import candidates as cand
    from nqbot.labeler import fills as fl
    from nqbot.labeler import params as lp
    from nqbot.labeler.costs import Costs
    from nqbot.labeler.firsthit import PathIndex
    from nqbot.labeler.outcomes import sweep_end

    months_mod.require_development(day, env.settings)
    doc = build_doc(env)
    base = out_dir(env, doc)
    iso = day.isoformat()
    dpath = base / "decisions" / f"{iso}.parquet"
    meta = read_meta(dpath)
    out_path = slip_dir(env, doc) / f"{iso}.parquet"
    tape = st.load_tape(env.db, sb.plan(env), "NQ", day)
    book = mbp1.load(env.db.raw_path("mbp-1", tape.symbol, day)) if tape is not None else None
    rows: list[dict] = []
    if book is None:
        write_table(out_path, [], {"day": iso, "key": doc["key"], "skipped": "no mbp-1 (stored from 2025-10-07)"})
        return out_path
    p = lp.params(env.settings, env.features)
    path = PathIndex(tape.price)
    close_i = tape.idx_before(int(meta["close_ns"]))
    start_ot = env.settings["entry_order_type"]
    for dp in _read(dpath):
        d = int(dp["direction"])
        ot = start_ot[dp["style"]]
        ref, _ = fl.reference(ot, dp, book, p)
        if ref is None:
            continue
        ia = tape.idx_before(int(dp["decision_ts"]) + p.latency_ns)
        for stop, types in cand.stops(dp, ref, d, p):
            k = path.first_beyond(ia, stop, -d, close_i)
            if k is None or book.at(int(tape.ts[k])) is None:
                continue
            stop_pts = abs(ref - stop)
            r = {"decision_id": dp["decision_id"], "style": dp["style"], "order_type": ot, "tradeable": dp["tradeable"],
                 "stop_types": types, "stop": stop, "stop_pts": stop_pts, "direction": d, "t_touch": int(tape.ts[k]),
                 "first_px": float(tape.price[k]), "sweep_px": float(tape.price[sweep_end(tape, k)])}
            for es in SLIP_SIZING_TICKS:
                c = Costs(p.costs.commission_micro, float(es), p.costs.max_entry_slip_ticks, p.costs.risk_usd)
                _m, size, _w = c.size(stop_pts, ot)
                px = market_fill(tape, book, k, -d, float(size))
                r[f"size_es{es}"] = size
                r[f"slip_es{es}"] = None if px is None else d * (stop - px) / TICK
            rows.append(r)
    write_table(out_path, rows, {"day": iso, "key": doc["key"]})
    return out_path


def slip_chunk_ids(env: Env) -> list[str]:
    doc = build_doc(env)
    return [f"s:{d.isoformat()}:{doc['key']}" for d in sessions_14(env, doc)] + [f"{REPORT_CHUNK}:{doc['key']}"]


def _quantile(xs, q):
    xs = [x for x in xs if x is not None and x == x]
    return float(np.quantile(xs, q)) if xs else None


def fill_touch_prices(env: Env, day: date, rows: list[dict]) -> None:
    """Rows written before `first_px` / `sweep_px` were stored: the first print at or through the stop at `t_touch`
    and the last print under that timestamp, from the stored trades (the same prices `slippage_day` now records)."""
    from nqbot.features import session_tape as st
    from nqbot.labeler.outcomes import sweep_end

    todo = [r for r in rows if r.get("sweep_px") is None]
    if not todo:
        return
    tape = st.load_tape(env.db, sb.plan(env), "NQ", day)
    for r in todo:
        k0 = int(np.searchsorted(tape.ts, r["t_touch"], side="left"))
        k1 = int(np.searchsorted(tape.ts, r["t_touch"], side="right"))
        seg = tape.price[k0:k1]
        s = float(r["stop"])
        hit = np.flatnonzero(seg <= s + 1e-9) if int(r["direction"]) > 0 else np.flatnonzero(seg >= s - 1e-9)
        if len(hit):
            k = k0 + int(hit[0])
            r["first_px"], r["sweep_px"] = float(tape.price[k]), float(tape.price[sweep_end(tape, k)])


def sweep_rule_proposal(rows: list[dict], es: int) -> tuple[int | None, dict[int, tuple[float, float, float]]]:
    """The gap rule (owner 2026-11-11, Q-step13b (d)) charges a stop max(`exit_slippage_ticks`, the gap from the stop
    to the triggering sweep's last print): the proposal is the smallest whole tick at which that charge covers the
    measured fill on >= 95% of the stops (`SLIP_Q`). Also {S: (share covered, mean charge, the same share under the
    first-print rule)} for S = 1..10."""
    xs = [(r[f"slip_es{es}"], int(r["direction"]) * (float(r["stop"]) - r["sweep_px"]) / TICK,
           int(r["direction"]) * (float(r["stop"]) - r["first_px"]) / TICK)
          for r in rows if r.get(f"slip_es{es}") is not None and r.get("sweep_px") is not None]
    if not xs:
        return None, {}
    a = np.array(xs)
    cover = lambda s, g: float(np.mean(np.maximum(s, a[:, g]) >= a[:, 0] - 1e-9))
    table = {s: (cover(s, 1), float(np.maximum(s, a[:, 1]).mean()), cover(s, 2)) for s in range(1, 11)}
    prop = next((s for s in range(0, 101) if cover(s, 1) >= SLIP_Q - 1e-12), None)
    return prop, table


def slippage_report(env: Env) -> Path:
    from nqbot.labeler.costs import MNQ_USD_TICK

    doc = build_doc(env)
    rows, skipped = [], []
    for d in sessions_14(env, doc):
        f = slip_dir(env, doc) / f"{d.isoformat()}.parquet"
        if not f.exists():
            continue
        m = read_meta(f)
        if m.get("skipped"):
            skipped.append(d.isoformat())
        day_rows = _read(f)
        fill_touch_prices(env, d, [r for r in day_rows if r["tradeable"]])
        rows += day_rows
    cur = float(env.settings["exit_slippage_ticks"])
    trad = [r for r in rows if r["tradeable"]]
    es = int(cur) if cur in SLIP_SIZING_TICKS else SLIP_SIZING_TICKS[0]
    p95 = _quantile([r[f"slip_es{es}"] for r in trad], SLIP_Q)
    plain = None if p95 is None else max(0, math.ceil(p95 - 1e-9))
    proposal, cov = sweep_rule_proposal(trad, es)
    comm = float(env.settings["default_commission_per_micro_rt"])
    split = [(r[f"slip_es{es}"], int(r["direction"]) * (float(r["stop"]) - r["first_px"]) / TICK,
              int(r["direction"]) * (float(r["stop"]) - r["sweep_px"]) / TICK)
             for r in trad if r.get(f"slip_es{es}") is not None and r.get("sweep_px") is not None]
    lines = [f"# Step 13b: stop slippage on months 1-4 (build {doc['key']})", "",
             "For every stop candidate (F11.1, at the start `stop_buffer`) of every decision point that price traded "
             "through after `decision_ts` + `order_latency_sec` before the RTH close: a stop order of the default size "
             "(NQ-equivalent contracts at `default_risk_usd`, from the start order type's reference price) filled "
             "against the best bid / ask size at the first touch (the book after that trade's timestamp), the rest at "
             "the following trades in sequence; slippage = (stop - fill) against the trade, ticks (SPEC §12 step 13).",
             "",
             f"- Sessions without `mbp-1` (skipped, Q-dryrun13 (14b)): {', '.join(skipped) or 'none'}",
             f"- Stops measured: {len(rows):,} ({len(trad):,} at tradeable decision points, used for the proposal)",
             f"- Plain p95 at tradeable decision points, sized with `exit_slippage_ticks` {cur:g}: "
             f"{p95 if p95 is None else round(p95, 2)} ticks (rounded up: {plain})",
             f"- **Proposal under the gap rule: `exit_slippage_ticks` = {proposal}**: the smallest whole tick at which "
             "the labeler's charge, max(`exit_slippage_ticks`, the gap from the stop to the triggering sweep's last "
             f"print), covers the measured fill on at least {SLIP_Q:.0%} of the tradeable stops",
             f"- Settings now: `exit_slippage_ticks` {cur:g}, `be_offset_ticks` {env.settings['be_offset_ticks']:g}. "
             "Owner 2026-11-11 (SPEC §13 Q-step13b (d), option B): the gap rule compares against the triggering "
             "sweep's last print (every print at the stop's trigger timestamp), not the first trade; both values "
             "frozen at 4 from months 1-4", "",
             "## Where the slippage comes from (tradeable stops)", "",
             "A stop is hit by one aggressor's sweep: its first print is at the stop, its later prints (same "
             "`ts_event`) go through, and the stop fills against the book the sweep left.", "",
             "| part | mean | median | p90 | p95 | p99 |", "|---|---:|---:|---:|---:|---:|"]
    if split:
        a = np.array(split)
        for name, v in (("measured slippage", a[:, 0]), ("stop to the first print at or through it", a[:, 1]),
                        ("stop to the sweep's last print", a[:, 2]), ("measured beyond the sweep's last print",
                                                                     a[:, 0] - a[:, 2])):
            lines.append(f"| {name} | {v.mean():.2f} | {np.quantile(v, .5):.2f} | {np.quantile(v, .9):.2f} | "
                         f"{np.quantile(v, .95):.2f} | {np.quantile(v, .99):.2f} |")
    lines += ["", "Share of stops whose measured fill the labeler's charge covers, by `exit_slippage_ticks`:", "",
              "| exit_slippage_ticks | sweep's last print (the rule) | mean charge (ticks) | first print (as first "
              "built) |", "|---:|---:|---:|---:|"]
    for s, (c_sw, m_sw, c_fi) in cov.items():
        lines.append(f"| {s} | {c_sw:.1%} | {m_sw:.2f} | {c_fi:.1%} |")
    lines += ["", "## Sensitivity to the value used for sizing (sizes grow as the value falls)", "",
             "| sizing value | p95 (ticks) | p99 | mean | share > 1 tick |", "|---:|---:|---:|---:|---:|"]
    for e in SLIP_SIZING_TICKS:
        xs = [r[f"slip_es{e}"] for r in trad if r[f"slip_es{e}"] is not None]
        if xs:
            lines.append(f"| {e} | {_quantile(xs, 0.95):.2f} | {_quantile(xs, 0.99):.2f} | {np.mean(xs):.2f} | "
                         f"{np.mean([x > 1 for x in xs]):.1%} |")
    lines += ["", "## By stop type, size and hour (tradeable, current sizing)", "",
              "| group | n | p95 | mean |", "|---|---:|---:|---:|"]
    groups: dict[str, list] = defaultdict(list)
    for r in trad:
        x = r[f"slip_es{es}"]
        if x is None:
            continue
        groups[f"stop {r['stop_types'].split(',')[0]}"].append(x)
        groups[f"size {r[f'size_es{es}']} NQ"].append(x)
        groups[f"hour {_fmt(r['t_touch'])[:2]}"].append(x)
        groups[f"style {r['style']}"].append(x)
    for g in sorted(groups):
        xs = groups[g]
        lines.append(f"| {g} | {len(xs):,} | {_quantile(xs, 0.95):.2f} | {np.mean(xs):.2f} |")
    # be_offset_ticks re-check (§6.6, §3.5 test 2): a breakeven exit nets (ceil(c / $0.50) + offset - s) ticks x $0.50 - c
    lines += ["", "## `be_offset_ticks` re-check (Q-dryrun13 (14b))", ""]
    base_ticks = math.ceil(comm / MNQ_USD_TICK - 1e-9)
    be_now = float(env.settings["be_offset_ticks"])
    need_of = lambda s: next(off for off in range(0, 101) if (base_ticks + off - s) * MNQ_USD_TICK - comm >= -1e-9)
    for s in sorted({x for x in (cur, proposal, plain) if x is not None}):
        need = need_of(s)
        lines.append(f"- slippage {s:g}: a breakeven exit nets ${(base_ticks + be_now - s) * MNQ_USD_TICK - comm:+.2f} "
                     f"per micro at the offset now ({be_now:g}); the smallest offset at or above $0 is {need} "
                     f"(${(base_ticks + need - s) * MNQ_USD_TICK - comm:+.2f})")
    if proposal is not None:
        lines.append(f"- **Smallest offset that keeps a breakeven exit at or above $0 after costs at slippage "
                     f"{proposal}: {need_of(proposal)}** (now {be_now:g})")
    path = report_dir(env) / "slippage.md"
    atomic_write_text(path, "\n".join(lines) + "\n")
    atomic_write_json(report_dir(env) / "slippage.json",
                      {"build_key": doc["key"], "p95": p95, "plain_proposal": plain, "proposal": proposal,
                       "be_offset_proposal": None if proposal is None else need_of(proposal),
                       "coverage": {str(s): v[0] for s, v in cov.items()}, "rows": len(rows),
                       "tradeable_rows": len(trad), "skipped": skipped})
    return path


class SlippageJob:
    name = JOB_SLIP

    def __init__(self) -> None:
        from nqbot.jobs.lock import DATABENTO, SKYLIT

        self.locks = (SKYLIT, DATABENTO)

    def chunks(self, ctx: Any) -> list[str]:
        return slip_chunk_ids(_job_env(ctx))

    def run_chunk(self, chunk_id: str, ctx: Any) -> dict[str, Any] | None:
        env = _job_env(ctx)
        if chunk_id.startswith(REPORT_CHUNK + ":"):
            slippage_report(env)
            return None
        _, d, _k = chunk_id.split(":")
        doc = build_doc(env)
        if (slip_dir(env, doc) / f"{d}.parquet").exists():
            return None
        if not (out_dir(env, doc) / "decisions" / f"{d}.parquet").exists():
            from nqbot.jobs.runner import StopChunk
            raise StopChunk(f"{d}: the build's decisions/ file is missing (finish the feature build first)",
                            status="failures")
        return _child(ctx, ["step13", "slippage-day", d], chunk_id)


# --- counts (before the labeler) -------------------------------------------------------------------------------------

def _table(rows, header) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "|".join("---" if i == 0 else "---:" for i in range(len(header))) + "|"] \
        + ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]


def _hold(evs: list[dict]) -> tuple[int, str, str, str]:
    n = len(evs)
    c = Counter(e["label"] for e in evs)
    f = (lambda k: f"{c[k] / n:.1%}") if n else (lambda k: "")
    return n, f("held"), f("broke"), f("timeout")


def counts(env: Env) -> Path:
    doc = build_doc(env)
    days = built_days(env, doc)
    base = out_dir(env, doc)
    events, levels, decisions_c = [], [], []
    summaries = {}
    for d in days:
        iso = d.isoformat()
        events += _read(base / "events" / f"{iso}.parquet")
        levels += _read(base / "level_events" / f"{iso}.parquet")
        decisions_c += [x for x in _read(base / "decisions" / f"{iso}.parquet") if x["style"] == "c"]
        summaries[iso] = day_summary(env, doc, d).get("pass2", {})
    lines = [f"# Step 13b counts on months 1-4 (build {doc['key']}, {len(days)} sessions)", "",
             "Counts and structure-label hold rates only (no pair outcome, no model): development months 1-4 (§7 rule "
             "2b).", ""]
    # 1. firings
    fc = Counter(e["n_firings_c"] for e in events if not e["damage_drop"])
    worst = sorted((e for e in events if e["n_firings_c"] > FIRINGS_ALERT), key=lambda e: -e["n_firings_c"])
    lines += ["## 1. Style-(c) firings per event (owner 2026-11-10, Q-step13b (2))", "",
              f"Every completed audit or sweep reclaim before re-arm and before `t_pair_end` is a decision point. Events "
              f"with more than {FIRINGS_ALERT}: **{len(worst)}** (the labeler waits for the owner's OK: "
              "`labels-plan --firings-ok`).", ""]
    lines += _table([(k, f"{v:,}", f"{v / max(1, sum(fc.values())):.1%}") for k, v in sorted(fc.items())],
                    ["firings", "events", "share"])
    if worst:
        by_ev = defaultdict(list)
        for x in decisions_c:
            by_ev[x["event_id"]].append(x)
        lines += ["", f"Examples (the {min(10, len(worst))} with the most firings):", ""]
        ex = []
        for e in worst[:10]:
            fs = sorted(by_ev[e["event_id"]], key=lambda x: x["decision_ts"])
            ex.append((e["event_id"], e["event_type"], e["direction"], f"{e['label']} @ {_fmt(e['t_resolved'])}",
                       f"{_fmt(e['ts'])}", e["n_firings_c"],
                       " ".join(f"{_fmt(x['decision_ts'])[:5]}({x['trigger_kind'][0]})" for x in fs),
                       f"{fs[0]['zone_lo']:.2f}-{fs[0]['zone_hi']:.2f}" if fs else ""))
        lines += _table(ex, ["event", "type", "dir", "label", "touch", "firings", "decision times (a audit, s sweep)",
                             "zone at the first"])
    # 2. flip checkpoint
    lines += ["", "## 2. Flip checkpoint (owner 2026-10-23, Q-flipretest; events only, never outcomes)", "",
              "Level transitions per level-type group (`level_events/`), and post-cross retest events before vs after "
              "a 5-minute flip (the primary crossed member at the touch: in its provisional role = before a flip; "
              f"flipped = after). Buckets under {SMALL_BUCKET} flagged.", ""]
    names = ("cross", "cross_back", "reclaim", "flip", "swept", "resume")
    lv_c = Counter((x["group"], x["name"]) for x in levels)
    rows = []
    groups = list(GROUPS) + [GAMEPLAN]
    for g in groups:
        rows.append((g, *(f"{lv_c[(g, n)]:,}" for n in names)))
    lines += _table(rows, ["group", *names])
    pc = Counter()
    for e in events:
        g = group_of_subtype(e.get("primary_member_type")) if e["event_type"] == "level" else (
            GAMEPLAN if e["event_type"] == "gameplan" else None)
        if g is None:
            continue
        if e["post_cross_retest"]:
            pc[(g, "before")] += 1
        elif (e.get("primary_flip_count") or 0) >= 1:
            pc[(g, "after")] += 1
    rows = []
    for g in groups:
        b, a = pc[(g, "before")], pc[(g, "after")]
        rows.append((g, f"{b:,}" + (" ⚑" if b < SMALL_BUCKET else ""), f"{a:,}" + (" ⚑" if a < SMALL_BUCKET else "")))
    lines += ["", "Post-cross retest events:", ""] + _table(rows, ["group", "before a flip", "after a flip"])
    # 3. golden zone
    gold = [e for e in events if e["event_type"] == "golden"]
    lines += ["", "## 3. Golden-zone counts (owner 2026-10-26/27, Q-fibvariants, Q-gzsplit; current design: flip on, "
              "redraw on)", "", f"Structure-label shares at `hold_reaction_pts`; buckets under {SMALL_BUCKET} flagged "
              "(⚑).", ""]
    rows = []
    for title, key in (("fib flips", lambda e: "0" if not e["or_fib_flips"] else ">=1"),
                       ("drawing", lambda e: e["or_fib_drawing"]), ("zone", lambda e: e["golden_zone_part"])):
        bk = defaultdict(list)
        for e in gold:
            bk[key(e)].append(e)
        for k in sorted(bk, key=str):
            for which, evs in (("all", bk[k]), ("tradeable", [e for e in bk[k] if e["tradeable"]])):
                n, h, b, t = _hold(evs)
                rows.append((title, k, which, f"{n:,}" + (" ⚑" if n < SMALL_BUCKET else ""), h, b, t))
    lines += _table(rows, ["split", "bucket", "events", "n", "held", "broke", "timeout"])
    # 4. the 13d build counts
    lines += ["", "## 4. Build counts (coded for 13d, Q-dryrun13 (9); months 1-4 here, months 1-6 from the 13d build)",
              ""]
    zd = [d for d, s in summaries.items() if s.get("zone_diff_any")]
    lines.append(f"- Sessions with any zone difference from the candidate-time fix (Q-step12a (1)): {len(zd)} of "
                 f"{len(summaries)}" + (f" ({', '.join(zd[:12])}{' ...' if len(zd) > 12 else ''})" if zd else ""))
    lj: Counter = Counter()
    for s in summaries.values():
        for j in s.get("level_jumps") or []:
            lj[(j.get("root"), j.get("sub_type"), "return" if j.get("returned_to") else "new level")] += 1
    if lj:
        lines += ["", "Level jumps by instrument and type (Q-step12b (a); a jump back to a band an earlier piece held "
                  "returns to that piece, Q-step13-0 (e1)):", ""]
        lines += _table([(r, t, w, f"{v:,}") for (r, t, w), v in sorted(lj.items(), key=str)],
                        ["root", "type", "outcome", "jumps"])
        lines.append("")
    for which, evs in (("all events", events), ("tradeable", [e for e in events if e["tradeable"]])):
        n30 = Counter(e["label"] for e in evs)
        n20 = Counter(e["pair_end_label"] for e in evs)
        n = max(1, len(evs))
        lines.append(f"- {which} ({len(evs):,}): at 30 pts held {n30['held'] / n:.1%} / broke {n30['broke'] / n:.1%} / "
                     f"timeout {n30['timeout'] / n:.1%}; at 20 pts held {n20['held'] / n:.1%} / broke "
                     f"{n20['broke'] / n:.1%} / timeout {n20['timeout'] / n:.1%}")
    lines.append(f"- Events dropped for damage (§3.2 rule 3): {sum(1 for e in events if e['damage_drop']):,}")
    path = report_dir(env) / "counts.md"
    atomic_write_text(path, "\n".join(lines) + "\n")
    return path


# --- budget ---------------------------------------------------------------------------------------------------------

def union_seconds(intervals: list[tuple[float, float]]) -> float:
    """Total length of the union of [start, end] intervals."""
    tot, cur = 0.0, None
    for a, b in sorted(intervals):
        if cur is None or a > cur[1]:
            if cur is not None:
                tot += cur[1] - cur[0]
            cur = [a, b]
        else:
            cur[1] = max(cur[1], b)
    return tot + (cur[1] - cur[0] if cur is not None else 0.0)


def pass2_wall(env: Env, doc: dict, days: list[date]) -> float:
    """Pass 2's wall seconds from the sessions' own times: per chunk, the union of its sessions' intervals (the pass-2
    summary entry is written last, so the summary file's write time is the session's end; its start = end -
    `total_sec`)."""
    tot = 0.0
    built = set(days)
    for c in fb.chunk_ids(doc):
        if not c.startswith("p2"):
            continue
        a, b = (date.fromisoformat(x) for x in c.split(":")[1].split("~"))
        iv = []
        for d in built:
            if a <= d <= b:
                end = fb.summary_file(env, doc["key"], d).stat().st_mtime
                iv.append((end - float(day_summary(env, doc, d)["pass2"].get("total_sec", 0)), end))
        tot += union_seconds(iv)
    return tot


def budget(env: Env) -> Path:
    """The step-13 budget from the measured months 1-4 rebuild (Q-step12, Q-step13plan (7))."""
    from nqbot.jobs.runner import load_state, state_path

    doc = build_doc(env)
    st = load_state(state_path(env.paths, fb.JOB))
    done = st.get("done") or {}
    recorded = Counter()
    for c in fb.chunk_ids(doc):
        x = done.get(c)
        if x:
            recorded[c.split(":")[0]] += float(x.get("sec") or 0)
    days = built_days(env, doc)
    p1 = [day_summary(env, doc, d).get("pass1", {}).get("build_sec", 0) for d in days]
    p2 = [day_summary(env, doc, d).get("pass2", {}) for d in days]
    # wall time from the sessions' own times (owner 2026-11-11): pass 1 runs one session at a time; pass 2 = the union
    # of each chunk's session intervals, so a machine asleep between sessions (2026-11-11: about 13.7 h inside one
    # chunk) is not counted
    sec = Counter({"p0": recorded["p0"], "p1": float(sum(p1)), "p2": pass2_wall(env, doc, days)})
    wall = sum(sec.values())
    left_out = sum(recorded.values()) - wall
    ev_s = [x.get("events_stage_sec", 0) for x in p2]
    zc_s = [x.get("zone_compare_sec", 0) for x in p2]
    lab_s, slip_s = [], 0.0
    lp_file = labels_plan_file(env)
    if lp_file.exists():
        plan = json.loads(lp_file.read_text(encoding="utf-8"))
        for d in days:
            s = label_paths(env, doc, plan["label_key"], d)["summary"]
            if s.exists():
                x = json.loads(s.read_text(encoding="utf-8"))
                lab_s.append(x.get("total_sec", 0))
    sst = load_state(state_path(env.paths, JOB_SLIP)).get("done") or {}
    slip_s = sum(float(v.get("sec") or 0) for k, v in sst.items() if doc["key"] in k)
    n = max(1, len(days))
    rebuild_h = wall / 3600
    label_h = sum(lab_s) / 3600 if lab_s else None
    per_session = wall / n
    lines = [f"# Step 13 budget re-estimate from the months 1-4 rebuild (build {doc['key']}, {len(days)} sessions)", "",
             "Measured on the build PC, around the clock (Q-step13plan (7)). Wall time from the sessions' own times "
             "(owner 2026-11-11): pass 1 = the sum of the sessions' build times (one at a time); pass 2 = the union of "
             "each chunk's session intervals (each session's summary write time minus its `total_sec`; pass 2 runs "
             "sessions in parallel); the warm-up = its chunk time. The job's recorded chunk times add "
             f"{left_out / 3600:.1f} h more (time the PC slept or the parent waited between sessions), left out.", "",
             f"- Pass 2 process time: {sum(x.get('total_sec', 0) for x in p2) / 3600:.2f} h over {len(p2)} sessions "
             f"(median {np.median([x.get('total_sec', 0) for x in p2]) if p2 else 0:.0f} s, max "
             f"{max((x.get('total_sec', 0) for x in p2), default=0):.0f} s)",
             f"- Months 1-4 rebuild: **{rebuild_h:.1f} h** wall (warm-up {sec['p0'] / 3600:.2f} h, pass 1 "
             f"{sec['p1'] / 3600:.2f} h, pass 2 {sec['p2'] / 3600:.2f} h); {per_session / 60:.1f} min a session; "
             f"pass 1 = {sec['p1'] / max(wall, 1):.0%} of it (the share a pass-1 checkpoint reuse could save on variants "
             "that don't change carried state, Q-step13plan (7))",
             f"- Engine stage inside pass 2 (events, decisions, decision rows, level events): mean "
             f"{np.mean(ev_s) if ev_s else 0:.0f} s a session, of which the 13d zone comparison "
             f"{np.mean(zc_s) if zc_s else 0:.0f} s",
             f"- Pass-1 build: mean {np.mean(p1) if p1 else 0:.0f} s a session",
             f"- Labeler (with the identity re-run): {'' if label_h is None else f'{label_h:.2f} h'} "
             f"({'' if not lab_s else f'{np.mean(lab_s):.0f} s a session, one at a time'})",
             f"- Slippage measurement: {slip_s / 3600:.2f} h", "",
             "## Re-estimate (provisional numbers of Q-step13plan (7) replaced by the measured ones)", ""]
    lab = label_h or 0.0
    items = [("13c rows 1-11: 28 event-changing months 1-4 rebuilds, each with its labels", 28 * (rebuild_h + lab)),
             ("13c rows 12-13: 40 execution re-simulations (labeler re-runs, no rebuild)", 40 * lab),
             ("13c row 14: roll / King switch windows (engine runs, Q-dryrun13 (4))", 24.0),
             ("13d: the full-window build (251 sessions) with the winners and its labels",
              (rebuild_h + lab) * FULL_WINDOW_SESSIONS / n)]
    for what, h in items:
        lines.append(f"- {what}: {h:.0f} h ({h / 24:.1f} days)")
    tot = sum(h for _, h in items)
    lines += ["", f"**Total ≈ {tot:.0f} h ≈ {tot / 24:.1f} days of PC time** (plus the provisional model's "
              "cross-validation per row, measured at 13c). No variant is dropped or shortened for time without the "
              "owner (Q-step12)."]
    path = report_dir(env) / "budget.md"
    atomic_write_text(path, "\n".join(lines) + "\n")
    return path


# --- the smoke run --------------------------------------------------------------------------------------------------

def events_day(env: Env, day: date) -> Path:
    """`nqbot step13 events-day D` (owner 2026-11-10): the engine stage and the labeler on one development session."""
    import shutil

    import psutil

    from nqbot.events import build as ev_build
    from nqbot.labeler import params as lp

    months_mod.require_development(day, env.settings)
    proc = psutil.Process(os.getpid())
    t0 = _time.monotonic()
    pl = sb.plan(env)
    planned = sb.planned_sessions(pl, env.cal)
    damage = sb.load_damage(env)
    state = sb.warmup_state(env, pl, planned, day, damage, full=False)
    t_warm = _time.monotonic() - t0
    built = sb.build_day(env, day, state, pl, planned, damage)
    t_build = _time.monotonic() - t0 - t_warm
    tmp = env.paths.tmp_dir / f"events_day_{day.isoformat()}"
    shutil.rmtree(tmp, ignore_errors=True)
    t1 = _time.monotonic()
    rows, _eod, info = fb.write_day_files(tmp, built.sf, day, "smoke", env.features, settings=env.settings,
                                          damage_day=damage.get(day), events=True)
    t_files = _time.monotonic() - t1
    p = lp.params(env.settings, env.features)
    out = label_one(env, tmp, day, p, identity=True)
    lpaths = {k: tmp / k / "smoke" / f"{day.isoformat()}.parquet" for k in ("pairs", "fills", "paths")}
    for k, path in lpaths.items():
        write_table(path, out[k], {"day": day.isoformat()})
    peak1 = getattr(proc.memory_info(), "peak_wset", 0) / 2**30 or proc.memory_info().rss / 2**30
    again = sb.build_day(env, day, state, pl, planned, damage)
    out2 = ev_build.session_outputs(again.sf, again.sf.touch_rows(), damage.get(day), env.settings)
    same_dec = _strip(out2["decisions"]) == _strip(_read(tmp / "decisions" / f"{day.isoformat()}.parquet"))
    same_ev = [e["event_id"] for e in out2["events"]] == [e["event_id"] for e in
                                                          _read(tmp / "events" / f"{day.isoformat()}.parquet")]
    sizes = {k: (tmp / k / f"{day.isoformat()}.parquet").stat().st_size for k in
             ("touches", "outcomes", "daytype", "levels", "events", "decisions", "decision_rows", "level_events")}
    sizes.update({k: v.stat().st_size for k, v in lpaths.items()})
    shutil.rmtree(tmp, ignore_errors=True)
    peak = getattr(proc.memory_info(), "peak_wset", 0) / 2**30 or peak1
    li = out["info"]
    try:
        n14 = len(sessions_14(env, build_doc(env)))
    except (fb.BuildRefused, Refused, OSError):
        n14 = 84   # the months 1-4 sessions of the ledger (build 58b311aa6985)
    lab_sec = li["label_sec"] + li.get("load_sec", 0)
    bands: Counter = Counter()
    for r in out["pairs"]:
        bands[(r["style"], r["order_type"], r["rr_band"], "ext" if r["rr_ext"] else "base")] += 1
    total_bytes = sum(sizes.values())
    lab_bytes = sum(sizes[k] for k in ("pairs", "fills", "paths"))
    full_gb = total_bytes * FULL_WINDOW_SESSIONS / 2**30
    eng_sec = t_build + info.get("events_stage_sec", 0)
    alarms = []
    if full_gb > 50:
        alarms.append(f"disk {full_gb:.0f} GB for the full window (> about 50 GB)")
    if lab_sec + li.get("identity_sec", 0) > t_build:
        alarms.append(f"the labeler ({lab_sec + li.get('identity_sec', 0):.0f} s with the identity re-run) slower than "
                      f"the build ({t_build:.0f} s)")
    if info.get("firings_c_max", 0) > FIRINGS_ALERT:
        alarms.append(f"an event with {info['firings_c_max']} style-(c) firings (> {FIRINGS_ALERT})")
    lines = [f"# Step 13b events-day smoke run: {day}", "",
             "Stored data only (\\$0, no lock); a carry-only warm-up, then the feature build's pass-2 files with the "
             "engine stage (events/, decisions/, decision_rows/, level_events/) and the labeler (pairs/, fills/, "
             "paths/), written to a temporary folder, measured and removed.", "",
             f"- **Stop-and-show conditions (owner 2026-11-10): {'; '.join(alarms) if alarms else 'none'}**",
             f"- Wall time: warm-up {t_warm:.0f} s, build {t_build:.0f} s, files + engine stage {t_files:.0f} s "
             f"(engine stage {info.get('events_stage_sec')} s, of it style (c) {info.get('style_c_sec')} s and the zone "
             f"comparison {info.get('zone_compare_sec')} s), labeler {li['label_sec']} s (+ trades / book load "
             f"{li.get('load_sec')} s, identity re-run {li.get('identity_sec')} s); peak {peak1:.2f} GB "
             f"({peak:.2f} GB after the second build)",
             f"- Built twice from the same state: decision points identical {same_dec}, events identical {same_ev}",
             f"- Events {len(rows)}; decision points {info['decisions']} ((d) {info['decisions_d']}, (c) "
             f"{info['decisions_c']}); style-(c) firings per event {info['firings_c']} (max {info['firings_c_max']}); "
             f"damage-dropped {info['damage_dropped']}; level transitions {info['level_events']}; zone difference from "
             f"the candidate-time fix: {info['zone_diff_trades']:,} trades",
             f"- Pairs {li['pairs']:,}, fills {li['fills']:,}; identity check (Q-flexfarm (1)): "
             f"{li.get('identity_ok')} ({li.get('identity_rows', 0):,} base rows, {li.get('ext_rows', 0):,} extension "
             "rows)",
             "- Gap rule (Q-step13b (b) (vii)): " + "; ".join(
                 f"{k} exits {v['exits']:,}, changed {v['changed']:,}, mean extra {v['mean_extra_ticks']:.2f} ticks"
                 for k, v in (li.get("gap_rule") or {}).items()),
             "", "## Pairs by style, order type and RR band", ""]
    lines += _table([(*k, f"{v:,}") for k, v in sorted(bands.items())], ["style", "order type", "RR band", "set",
                                                                          "pairs"])
    lines += ["", "## Disk (this session) and extrapolation", ""]
    lines += _table([(k, f"{v / 2**20:.2f} MB") for k, v in sizes.items()], ["folder", "size"])
    lines += ["", f"- This session {total_bytes / 2**20:.1f} MB (labeler {lab_bytes / 2**20:.1f} MB); months 1-4 "
              f"({n14} sessions) ≈ {total_bytes * n14 / 2**30:.1f} GB; full window ({FULL_WINDOW_SESSIONS} sessions) "
              f"≈ {full_gb:.1f} GB", f"- Pairs: months 1-4 ≈ {li['pairs'] * n14 / 1e6:.1f} M, full window ≈ "
              f"{li['pairs'] * FULL_WINDOW_SESSIONS / 1e6:.1f} M rows",
              f"- Time: engine (build + stage) {eng_sec:.0f} s, labeler {lab_sec:.0f} s "
              f"(+ {li.get('identity_sec', 0):.0f} s identity) a session; months 1-4 labeler ≈ "
              f"{(lab_sec + li.get('identity_sec', 0)) * n14 / 3600:.1f} h one at a time",
              "", "This day is busier than the median session (it was the step-7 busy day), so the extrapolations are on "
              "the high side."]
    path = report_dir(env) / f"events_day_{day.isoformat()}.md"
    atomic_write_text(path, "\n".join(lines) + "\n")
    return path


def _strip(rows: list[dict]) -> list[dict]:
    """Rows as parquet gives them back (floats compared to 1e-9)."""
    out = []
    for r in rows:
        x = {}
        for k, v in r.items():
            if isinstance(v, float):
                v = None if v != v else round(v, 9)
            elif isinstance(v, list):
                v = [round(y, 9) if isinstance(y, float) else y for y in v]
            x[k] = v
        out.append(x)
    return out
