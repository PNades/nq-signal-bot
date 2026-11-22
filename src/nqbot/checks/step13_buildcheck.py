"""After-13c knock-on check (owner 2026-11-22, SPEC §13 Q-step13c (e) (4)): one 13c build row's start value against a
challenger, each a months 1-4 build made under the final (committed) settings, scored with 13c's method; read-only
on the builds, outside the build and labels keys; nothing is written to the configuration or to 13c's records.

Why: rows 4-13 were tuned with `zone_proximity` 8 and `sweep_max_depth` 25 in place, and the owner committed 5 and 10.
Row 4 (`sweep_reclaim_bars`) is re-checked: 2 (committed) against 3 (its 13c start value) on style (c) alone.

The method is 13c's for a build row (`step13c.score_build_row`, Q-step13c (5)): both builds labelled at the committed
labeler settings, the provisional model's out-of-fold P(held) per build, each fold's bar on the shared events, the
row's own run (row 4: style (c) only; (d) and combined reported for information), the committed order types, the
excess net R per month of the challenger over the start value with its 95% day-block interval; rule none decides,
fs for information. The row's rule (owner 2026-11-22, declared before any result): the best value outright on the
row's run: the challenger is better if its excess is above 0, else the start value is (the start counting 0, as in
13c).

Job `step13_buildcheck`: `nqbot step13 buildcheck-plan --row R --start K1 --challenger K2`, then
`nqbot run step13_buildcheck --until HH:MM` (chunks `labels`, `score`); by hand `nqbot step13 buildcheck-report`.
Report `<reports_dir>/step13/tune/buildcheck_<row>_<K1>_<K2>.md|json`.
"""

from __future__ import annotations

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

JOB = "step13_buildcheck"
MONTHS = s13b.MONTHS_14            # 13c's tuning months
ALL_MODES = s13c.ALL_MODES
STYLES = ("c", "d")


class BuildCheckRefused(Exception):
    pass


def job_dir(env: Env) -> Path:
    return env.paths.data_dir / "jobs" / JOB


def plan_file(env: Env) -> Path:
    return job_dir(env) / "plan.json"


def cache_dir(env: Env) -> Path:
    return job_dir(env) / "cache"     # its own: 13c's cache is never written


def report_base(env: Env, plan: dict) -> Path:
    return s13c.rep_dir(env) / f"buildcheck_{plan['row']['id']}_{plan['start']['key']}_{plan['challenger']['key']}"


def row_of(env: Env, row_id: str) -> dict[str, Any]:
    """The row as 13c ran it (its plan's program entry): a build row."""
    plan = s13c._read_json(s13c.plan_file(env))
    if plan is None:
        raise BuildCheckRefused(f"no step-13c plan ({s13c.plan_file(env)})")
    hit = [r for r in plan["program"] if r["id"] == row_id]
    if not hit:
        raise BuildCheckRefused(f"13c has no row {row_id}")
    if hit[0]["kind"] != "build":
        raise BuildCheckRefused(f"row {row_id} is a {hit[0]['kind']} row, not a build row")
    return hit[0]


def value_of(env: Env, doc: dict, keys: list[str]) -> dict[str, Any]:
    """The row's parameter(s) in build `doc`: its run-time override, else the committed value."""
    from nqbot.checks import feature_build as fb

    f = fb.apply_overrides(env.features, doc.get("overrides") or {})
    out = {}
    for k in keys:
        top, _, sub = k.partition(".")
        out[k] = f[top][sub] if sub else f[top]
    return out


def order_types(env: Env) -> dict[str, str]:
    return {s: env.settings["entry_order_type"][s] for s in STYLES}


def _days(env: Env, doc: dict) -> list[date]:
    days = s13b.sessions_14(env, doc)
    for d in days:
        months_mod.require_development(d, env.settings, MONTHS)
    return days


def buildcheck_plan(env: Env, row_id: str, start_key: str, chal_key: str) -> dict[str, Any]:
    """Checks both builds are this code's and complete on months 1-4 and differ only in the row's parameter(s), writes
    each build's labels plan at the committed labeler settings and the job's plan."""
    from nqbot.jobs.runner import record_plan

    if start_key == chal_key:
        raise BuildCheckRefused("the start and challenger builds are the same build")
    row = row_of(env, row_id)
    keys = list(row["keys"])
    sides: dict[str, dict] = {}
    days_of: dict[str, list[date]] = {}
    for side, key in (("start", start_key), ("challenger", chal_key)):
        doc = s13b.build_doc_for(env, key)
        other = {k: v for k, v in (doc.get("overrides") or {}).items() if k.partition(".")[0] not in
                 {x.partition(".")[0] for x in keys}}
        if other:
            raise BuildCheckRefused(f"build {key} overrides {other}: only the row's {keys} may differ from the "
                                    "committed configuration")
        days = _days(env, doc)
        if not s13c.build_complete(env, key, days):
            raise BuildCheckRefused(f"build {key} is not complete on months 1-4")
        days_of[side] = days
        sides[side] = {"key": key, "value": value_of(env, doc, keys), "overrides": doc.get("overrides") or {},
                       "label_key": s13b.labels_plan_for(env, key, None)["label_key"]}
    if days_of["start"] != days_of["challenger"]:
        raise BuildCheckRefused("the two builds cover different months 1-4 sessions")
    if sides["start"]["value"] == sides["challenger"]["value"]:
        raise BuildCheckRefused(f"both builds have {keys} = {sides['start']['value']}")
    plan = {"row": row, "start": sides["start"], "challenger": sides["challenger"], "order_types": order_types(env),
            "sessions": [d.isoformat() for d in days_of["start"]], "written": datetime.now(ET).isoformat()}
    atomic_write_json(plan_file(env), plan)
    record_plan(env.paths, JOB, chunk_ids())
    return plan


def chunk_ids() -> list[str]:
    return ["labels", "score"]


def load_plan(env: Env) -> dict[str, Any]:
    plan = s13c._read_json(plan_file(env))
    if plan is None:
        raise BuildCheckRefused("no build-check plan: uv run nqbot step13 buildcheck-plan --row R --start K1 "
                                "--challenger K2")
    for side in ("start", "challenger"):
        x = plan[side]
        s13b.build_doc_for(env, x["key"])          # still this code's build
        if s13b.labels_plan_for(env, x["key"], None)["label_key"] != x["label_key"]:
            raise BuildCheckRefused(f"{side} build {x['key']}: the labeler's settings or code changed since the plan")
    if plan["order_types"] != order_types(env):
        raise BuildCheckRefused("the committed order types changed since the plan")
    return plan


def label_targets(plan: dict) -> list[tuple[str, dict[str, Any]]]:
    return [(plan[side]["key"], {}) for side in ("start", "challenger")]


# --- the score ---------------------------------------------------------------------------------------------------------

def verdict(sc: dict[str, Any]) -> str:
    """The row's rule (owner 2026-11-22): the best value outright; the start value counts 0."""
    return "challenger" if sc["diff"] > 0 else "start"


def _ci(x: dict) -> str:
    return f"{x['diff']:+.2f} [{x['lo']:+.2f}, {x['hi']:+.2f}]"


def score(env: Env, plan: dict[str, Any], threads: int | None = None) -> Path:
    from nqbot.tuning import compare as cmp
    from nqbot.tuning.data import load_build, load_picks
    from nqbot.tuning.model import phold

    days = [date.fromisoformat(d) for d in plan["sessions"]]
    for d in days:
        months_mod.require_development(d, env.settings, MONTHS)
    month_of = {d.isoformat(): months_mod.month_index(d, env.settings) for d in days}
    reps = int(env.settings["tune_13"]["bootstrap_replicates"])
    threads = threads or max(1, (os.cpu_count() or 1) - 2)
    cache_dir(env).mkdir(parents=True, exist_ok=True)
    loaded = {}
    for side in ("start", "challenger"):
        x = plan[side]
        for d in days:
            if not s13b.labelled(env, x["key"], x["label_key"], d):
                raise BuildCheckRefused(f"labels {x['label_key']} of build {x['key']} are not complete ({d})")
        b = load_build(env, x["key"], days, month_of)
        p, _ = phold(b, env.settings, threads, cache_dir(env))
        picks = load_picks(env, x["key"], x["label_key"], days,
                           cache_dir(env) / f"picks_{x['key']}_{x['label_key']}.jsonl")
        loaded[side] = (b, p, picks)
    row = plan["row"]
    (b0, p0, x0), (b1, p1, x1) = loaded["start"], loaded["challenger"]
    res = cmp.compare(b0, b1, p0, p1, x0, x1, plan["order_types"], row["mode"], row["subset"], reps,
                      extra_modes=tuple(m for m in ALL_MODES if m != row["mode"]))
    dec = res["rules"]["none"]
    win = verdict(dec)
    sv, cv = plan["start"]["value"], plan["challenger"]["value"]
    keep = cv if win == "challenger" else sv
    out = {"plan": plan, "result": res, "winner": win, "winner_value": keep,
           "lead": {k: dec[k] for k in ("diff", "lo", "hi")}}
    ev = res["events"]
    st, ch = dec["start"], dec["challenger"]
    lines = [f"# Knock-on check of {row['id']} `{row['name']}` under the final settings (months 1-4)", "",
             "Owner 2026-11-22 (SPEC §13 Q-step13c (e) (4)): the start value's build against the challenger's, both "
             "under the committed configuration; 13c's method (Q-step13c (5)): each fold's bar on the shared events, "
             f"the row's run ({row['mode']}), the committed order types {plan['order_types']}, the challenger's excess "
             "net R per month over the start value with its 95% day-block interval; rule none decides, fs for "
             "information. The row's rule (declared before any result): the best value outright on the row's run; the "
             "start value counts 0. Nothing is written to the configuration.", "",
             f"- start: {sv} (build {plan['start']['key']}, labels {plan['start']['label_key']})",
             f"- challenger: {cv} (build {plan['challenger']['key']}, labels {plan['challenger']['label_key']})",
             f"- events: start {ev['start']:,}, challenger {ev['challenger']:,}, shared {ev['shared']:,} "
             f"(model-ok {ev['shared_model']:,})", "",
             "| run | rule | start R/mo (trades) | challenger R/mo (trades) | challenger excess [95%] | total R/mo diff [95%] |",
             "|---|---|---|---|---|---|"]
    for m in (row["mode"],) + tuple(x for x in ALL_MODES if x != row["mode"]):
        for rule in ("none", "fs"):
            r = res["rules"][rule if m == row["mode"] else f"{m}:{rule}"]
            t = r["total"]
            lines.append(f"| {m}{' (decides)' if m == row['mode'] and rule == 'none' else ''} | {rule} | "
                         f"{r['start']['r_per_month']:+.2f} ({r['start']['trades']}) | "
                         f"{r['challenger']['r_per_month']:+.2f} ({r['challenger']['trades']}) | {_ci(r)} | "
                         f"{t[0]:+.2f} [{t[1]:+.2f}, {t[2]:+.2f}] |")
    lines += ["", f"**Result:** the challenger's lead {_ci(dec)} R/month on {row['mode']} "
              f"({st['trades']} -> {ch['trades']} trades): **{keep}** "
              f"({'the challenger' if win == 'challenger' else 'the start value'} is better by the row's rule)."]
    base = report_base(env, plan)
    base.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(base.with_suffix(".json"), out)
    atomic_write_text(base.with_suffix(".md"), "\n".join(lines) + "\n")
    return base.with_suffix(".md")


# --- the job -----------------------------------------------------------------------------------------------------------

class BuildCheckJob:
    """`nqbot run step13_buildcheck --until HH:MM`: both builds' labels (a pool of labels-day children), then the
    score."""

    name = JOB

    def __init__(self) -> None:
        from nqbot.jobs.lock import DATABENTO, SKYLIT

        self.locks = (SKYLIT, DATABENTO)

    def _env(self, ctx) -> Env:
        return s13b._job_env(ctx)

    def chunks(self, ctx) -> list[str]:
        load_plan(self._env(ctx))
        return chunk_ids()

    def run_chunk(self, chunk_id: str, ctx) -> dict | None:
        from nqbot.jobs.runner import StopChunk

        env = self._env(ctx)
        try:
            plan = load_plan(env)
            days = [date.fromisoformat(d) for d in plan["sessions"]]
            if chunk_id == "labels":
                s13c.run_pool(env, ctx, s13c.label_jobs(env, label_targets(plan), days), s13c.LABEL_WORKERS,
                              log_name=JOB)
            elif chunk_id == "score":
                env.echo(f"{JOB}: report {score(env, plan)}")
            else:
                raise BuildCheckRefused(f"unknown chunk {chunk_id}")
            return None
        except (BuildCheckRefused, s13b.Refused, s13c.TuneRefused) as e:
            ctx.log.error("%s: %s: %s", JOB, chunk_id, e)
            raise StopChunk(f"{chunk_id}: {e}", status="failures") from None


def describe_plan(plan: dict) -> list[str]:
    return [f"  start {plan['start']['value']}: build {plan['start']['key']}, labels {plan['start']['label_key']}",
            f"  challenger {plan['challenger']['value']}: build {plan['challenger']['key']}, labels "
            f"{plan['challenger']['label_key']}",
            f"{JOB}: {plan['row']['id']} on {plan['row']['mode']}, {len(plan['sessions'])} sessions; run: uv run nqbot "
            f"run {JOB} --until HH:MM"]

