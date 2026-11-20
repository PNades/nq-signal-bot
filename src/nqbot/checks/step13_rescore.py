"""Pre-13d item (8) (owner 2026-11-20, `docs/design/pre13d_after_13c.md` section 8): 13c's execution winners against
their runners-up on the corrected build (13d's, `features_version` 40), months 1-4 only, no rebuild; outside the build
and labels keys; nothing is written to the configuration.

The compared points come from 13c's records (`<data_dir>/jobs/step13_tune/winners.json` and row 13's report):
- row 12, per style: the winner (order type, `entry_ttl_sec`) against its next-best point;
- row 13: the winner (floor, `stop_buffer`) against the next-best buffer on the same floor, and against the other
  floor at its best buffer (the floor decision).
Every point is written as absolute execution settings (`entry_ttl_sec.c`, `entry_ttl_sec.d`, `stop_buffer`,
`stop_floor_vol`); a label set's overrides are the keys that differ from the committed configuration, and equal sets
are labelled once (`step13 labels-day D --build-key K --label-key L`, as 13c does).

The score is 13c's (Q-step13c (5)): the events frozen (one bar per fold over all events, the provisional model's
out-of-fold P(held)), single-style policies, the excess net R per month with its 95% day-block interval; here the
runner-up's excess over the winner directly (the winner is the reference run). Rule none decides, fs for information.

Job `step13_rescore`: `nqbot step13 rescore-plan --build-key K`, then `nqbot run step13_rescore --until HH:MM`
(chunks `labels:<K>`, `score:<K>`); by hand `nqbot step13 rescore-report`. Report
`<reports_dir>/step13/tune/rescore_exec_<K>.md|json`.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

from nqbot import months as months_mod
from nqbot.checks import step13b as s13b
from nqbot.checks import step13c as s13c
from nqbot.checks.step8b import Env
from nqbot.features.session_tape import ET
from nqbot.storage import atomic_write_json, atomic_write_text

JOB = "step13_rescore"
MONTHS = s13b.MONTHS_14            # 13c's tuning months
EXEC_KEYS = ("entry_ttl_sec.c", "entry_ttl_sec.d", "stop_buffer", "stop_floor_vol")
STYLES = ("c", "d")


class RescoreRefused(Exception):
    pass


def job_dir(env: Env) -> Path:
    return env.paths.data_dir / "jobs" / JOB


def plan_file(env: Env) -> Path:
    return job_dir(env) / "plan.json"


def cache_dir(env: Env) -> Path:
    return job_dir(env) / "cache"     # its own: 13c's cache is never written


def report_base(env: Env, key: str) -> Path:
    return s13c.rep_dir(env) / f"rescore_exec_{key}"


# --- the points --------------------------------------------------------------------------------------------------------

def committed(env: Env) -> dict[str, Any]:
    """The committed configuration's execution settings, the floor included (`stop_floor_vol`: null = fixed, a
    setting the labeler reads since owner 2026-11-20)."""
    s = env.settings
    return {"entry_ttl_sec.c": s["entry_ttl_sec"]["c"], "entry_ttl_sec.d": s["entry_ttl_sec"]["d"],
            "stop_buffer": s["stop_buffer"], "stop_floor_vol": s["stop_floor_vol"]}


def overrides_of(env: Env, point: dict[str, Any]) -> dict[str, Any]:
    now = committed(env)
    return {k: point[k] for k in EXEC_KEYS if point[k] != now[k]}


def _r13_report(env: Env) -> dict[str, Any]:
    hits = sorted(s13c.rep_dir(env).glob("r13_*.json"))
    if len(hits) != 1:
        raise RescoreRefused(f"row 13's report: expected one r13_*.json in {s13c.rep_dir(env)}, found {len(hits)} "
                             "(13c must have scored row 13)")
    return json.loads(hits[0].read_text(encoding="utf-8"))


def _floor_setting(env: Env, floor: str) -> dict | None:
    if floor == "fixed":
        return None
    fit = s13c._read_json(s13c.vol_fit_file(env))
    if fit is None:
        raise RescoreRefused(f"no volatility-scaled floor fit ({s13c.vol_fit_file(env)})")
    return fit["override"]


def comparisons(env: Env) -> tuple[dict[str, dict], list[dict[str, Any]]]:
    """({set name: absolute point}, [comparison]) from 13c's winners and row 13's report."""
    w = s13c.winners(env)
    missing = [r for r in ("r12", "r13") if r not in w]
    if missing:
        raise RescoreRefused(f"13c has no winner for {', '.join(missing)} yet ({s13c.winners_file(env)})")
    w12, w13 = w["r12"], w["r13"]
    ot_win = {s: w12["value"][s][0] for s in STYLES}
    floor, buf = w13["value"]["floor"], w13["value"]["stop_buffer"]
    win = {"entry_ttl_sec.c": w12["value"]["c"][1], "entry_ttl_sec.d": w12["value"]["d"][1], "stop_buffer": buf,
           "stop_floor_vol": _floor_setting(env, floor)}
    run12 = {s: w12["lead"][s]["next_best"] for s in STYLES}      # [order type, ttl] per style
    sets: dict[str, dict] = {"winners": win,
                             "r12_runners_up": {**win, **{f"entry_ttl_sec.{s}": run12[s][1] for s in STYLES}}}
    rep = _r13_report(env)
    other = "vol" if floor == "fixed" else "fixed"
    vv = rep["vol_vs_fixed"]
    sets["r13_buffer_runner_up"] = {**win, "stop_buffer": rep["floors"][floor]["lead"]["next_best"]}
    sets["r13_floor_runner_up"] = {**win, "stop_buffer": vv[f"{other}_buffer"],
                                   "stop_floor_vol": _floor_setting(env, other)}
    comps: list[dict[str, Any]] = []
    for s in STYLES:
        comps.append({"id": f"r12_{s}", "row": "r12", "styles": [s],
                      "winner": {"set": "winners", "order_types": {s: ot_win[s]}, "label": list(w12["value"][s])},
                      "runner": {"set": "r12_runners_up", "order_types": {s: run12[s][0]}, "label": list(run12[s])},
                      "lead_13c": w12["lead"][s]})
    comps.append({"id": "r13_buffer", "row": "r13", "styles": list(STYLES),
                  "winner": {"set": "winners", "order_types": ot_win, "label": [floor, buf]},
                  "runner": {"set": "r13_buffer_runner_up", "order_types": ot_win,
                             "label": [floor, sets["r13_buffer_runner_up"]["stop_buffer"]]},
                  "lead_13c": rep["floors"][floor]["lead"]})
    comps.append({"id": "r13_floor", "row": "r13", "styles": list(STYLES),
                  "winner": {"set": "winners", "order_types": ot_win, "label": [floor, buf]},
                  "runner": {"set": "r13_floor_runner_up", "order_types": ot_win,
                             "label": [other, sets["r13_floor_runner_up"]["stop_buffer"]]},
                  "lead_13c": {"vol_minus_fixed": vv.get("diff"), "lo": vv.get("lo"), "hi": vv.get("hi")}})
    # equal points are one label set (e.g. a runner-up that differs from the winner only by its order type)
    canon: dict[str, str] = {}
    uniq: dict[str, dict] = {}
    for name, pt in sets.items():
        same = next((n for n, q in uniq.items() if q == pt), None)
        canon[name] = same or name
        if same is None:
            uniq[name] = pt
    for c in comps:
        for side in ("winner", "runner"):
            c[side]["set"] = canon[c[side]["set"]]
    return uniq, comps


# --- the plan ----------------------------------------------------------------------------------------------------------

def _days(env: Env, doc: dict) -> list[date]:
    days = s13b.sessions_14(env, doc)
    for d in days:
        months_mod.require_development(d, env.settings, MONTHS)
    return days


def rescore_plan(env: Env, key: str) -> dict[str, Any]:
    """Checks build `key` is this code's and complete on months 1-4, writes the label sets and comparisons."""
    from nqbot.jobs.runner import record_plan

    doc = s13b.build_doc_for(env, key)
    days = _days(env, doc)
    if not s13c.build_complete(env, key, days):
        raise RescoreRefused(f"build {key} is not complete on months 1-4")
    sets, comps = comparisons(env)
    out_sets = {}
    for name, pt in sets.items():
        over = overrides_of(env, pt)
        out_sets[name] = {"point": pt, "overrides": over,
                          "label_key": s13b.labels_plan_for(env, key, over or None)["label_key"]}
    plan = {"build_key": key, "sessions": [d.isoformat() for d in days], "sets": out_sets, "comparisons": comps,
            "winners_file": str(s13c.winners_file(env)), "written": datetime.now(ET).isoformat()}
    atomic_write_json(plan_file(env), plan)
    record_plan(env.paths, JOB, chunk_ids(plan))
    return plan


def chunk_ids(plan: dict) -> list[str]:
    return [f"labels:{plan['build_key']}", f"score:{plan['build_key']}"]


def load_plan(env: Env) -> dict[str, Any]:
    plan = s13c._read_json(plan_file(env))
    if plan is None:
        raise RescoreRefused("no re-score plan: uv run nqbot step13 rescore-plan --build-key K")
    s13b.build_doc_for(env, plan["build_key"])          # still this code's build
    for name, x in plan["sets"].items():
        if s13b.labels_plan_for(env, plan["build_key"], x["overrides"] or None)["label_key"] != x["label_key"]:
            raise RescoreRefused(f"label set {name}: the labeler's settings or code changed since rescore-plan")
    return plan


def label_targets(plan: dict) -> list[tuple[str, dict[str, Any]]]:
    return [(plan["build_key"], x["overrides"]) for x in plan["sets"].values()]


# --- the score ---------------------------------------------------------------------------------------------------------

def _verdict(sc: dict[str, Any]) -> str:
    if sc["lo"] > 0:
        return "runner-up better (interval above 0)"
    if sc["hi"] < 0:
        return "winner better (interval below 0)"
    return "no clear difference (interval contains 0)"


def score(env: Env, plan: dict[str, Any], threads: int | None = None) -> Path:
    from nqbot.tuning import compare as cmp
    from nqbot.tuning.data import load_build, load_picks
    from nqbot.tuning.model import phold

    key = plan["build_key"]
    days = [date.fromisoformat(d) for d in plan["sessions"]]
    for d in days:
        months_mod.require_development(d, env.settings, MONTHS)
    month_of = {d.isoformat(): months_mod.month_index(d, env.settings) for d in days}
    n_months = float(len(set(month_of.values())))
    reps = int(env.settings["tune_13"]["bootstrap_replicates"])
    cache_dir(env).mkdir(parents=True, exist_ok=True)
    b = load_build(env, key, days, month_of)
    p, _ = phold(b, env.settings, threads or max(1, (os.cpu_count() or 1) - 2), cache_dir(env))
    bar = cmp.bars(b, p, [e["event_id"] for e in b.events])
    picks = {}
    for name, x in plan["sets"].items():
        for d in days:
            if not s13b.labelled(env, key, x["label_key"], d):
                raise RescoreRefused(f"label set {name} ({x['label_key']}) is not complete ({d})")
        picks[name] = load_picks(env, key, x["label_key"], days, cache_dir(env) / f"picks_{key}_{x['label_key']}.jsonl")
    results = []
    lines = [f"# Re-score of 13c's execution winners on build {key} (months 1-4)", "",
             f"Pre-13d item (8) (owner 2026-11-20). Frozen events: build {key}, {len(days)} sessions, one bar per fold "
             "over all its events (the provisional model's out-of-fold P(held)); single-style policies; score: the "
             "runner-up's excess net R per month over the winner with its 95% day-block interval (Q-step13c (5); the "
             "winner is the reference run). Rule none decides, fs for information. Nothing is written to the "
             "configuration.", "", "| comparison | winner | runner-up | winner R/mo (trades) | runner-up R/mo (trades) "
             "| runner-up excess over the winner [95%] | verdict | fs: excess [95%] | 13c lead [95%] |",
             "|---|---|---|---|---|---|---|---|---|"]
    for c in plan["comparisons"]:
        styles = tuple(c["styles"])
        row = {"id": c["id"]}
        for rule in ("none", "fs"):
            rw, nw = cmp.fixed_events_series(b, picks[c["winner"]["set"]], p, bar, c["winner"]["order_types"], styles,
                                             rule)["sum"]
            rr, nr = cmp.fixed_events_series(b, picks[c["runner"]["set"]], p, bar, c["runner"]["order_types"], styles,
                                             rule)["sum"]
            sc = cmp.score(rr, nr, rw, nw, n_months, reps)
            row[rule] = {"winner_r_per_month": float(rw.sum() / n_months), "winner_trades": int(nw.sum()),
                         "runner_r_per_month": float(rr.sum() / n_months), "runner_trades": int(nr.sum()),
                         **{k: v for k, v in sc.items() if k != "series"}}
        row["verdict"] = _verdict(row["none"])
        results.append(row)
        n, f = row["none"], row["fs"]
        l13 = c.get("lead_13c") or {}
        lead = l13.get("lead", l13.get("vol_minus_fixed"))
        l13_txt = "" if lead is None else f"{lead:+.2f} [{l13.get('lo', 0):+.2f}, {l13.get('hi', 0):+.2f}]"
        lines.append(f"| {c['id']} | {c['winner']['label']} | {c['runner']['label']} | "
                     f"{n['winner_r_per_month']:+.2f} ({n['winner_trades']}) | {n['runner_r_per_month']:+.2f} "
                     f"({n['runner_trades']}) | {n['diff']:+.2f} [{n['lo']:+.2f}, {n['hi']:+.2f}] | {row['verdict']} | "
                     f"{f['diff']:+.2f} [{f['lo']:+.2f}, {f['hi']:+.2f}] | {l13_txt} |")
    lines += ["", "13c lead: the winner's lead over this runner-up as 13c reported it (on build bb10b8f5585e's events, "
              "before the age fix); for r13_floor, vol minus fixed. A runner-up better with its interval above 0 is "
              "for the owner to decide on; nothing changes by itself.", "", "Label sets:", ""]
    for name, x in plan["sets"].items():
        lines.append(f"- {name}: labels {x['label_key']}, overrides {x['overrides'] or 'none (the committed settings)'}")
    base = report_base(env, key)
    base.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(base.with_suffix(".json"), {"plan": plan, "results": results})
    atomic_write_text(base.with_suffix(".md"), "\n".join(lines) + "\n")
    return base.with_suffix(".md")


# --- the job -----------------------------------------------------------------------------------------------------------

class RescoreJob:
    """`nqbot run step13_rescore --until HH:MM`: the label sets (a pool of labels-day children), then the score."""

    name = JOB

    def __init__(self) -> None:
        from nqbot.jobs.lock import DATABENTO, SKYLIT

        self.locks = (SKYLIT, DATABENTO)

    def _env(self, ctx) -> Env:
        return s13b._job_env(ctx)

    def chunks(self, ctx) -> list[str]:
        return chunk_ids(load_plan(self._env(ctx)))

    def run_chunk(self, chunk_id: str, ctx) -> dict | None:
        from nqbot.jobs.runner import StopChunk

        env = self._env(ctx)
        try:
            plan = load_plan(env)
            what, key = chunk_id.split(":")
            if key != plan["build_key"]:
                raise RescoreRefused(f"chunk {chunk_id} is not for the plan's build {plan['build_key']}")
            days = [date.fromisoformat(d) for d in plan["sessions"]]
            if what == "labels":
                s13c.run_pool(env, ctx, s13c.label_jobs(env, label_targets(plan), days), s13c.LABEL_WORKERS,
                              log_name=JOB)
            else:
                env.echo(f"{JOB}: report {score(env, plan)}")
            return None
        except (RescoreRefused, s13b.Refused, s13c.TuneRefused) as e:
            ctx.log.error("%s: %s: %s", JOB, chunk_id, e)
            raise StopChunk(f"{chunk_id}: {e}", status="failures") from None
