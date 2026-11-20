"""Step 13c: the tuning on months 1-4, rows 1-14 (owner 2026-11-08, SPEC §13 Q-step13plan; 2026-11-14, Q-step13c;
§7.1 Tuning detector parameters, §7 rule 3b; development months 1-4 only; outside the build and labels keys).

One job, `step13_tune` (`nqbot step13 tune-plan`, then `nqbot run step13_tune --until HH:MM`, or queued for
`nqbot resume`): its chunks are static (`program.chunk_ids`), their content is resolved when they run from the winners
recorded so far (`<data_dir>/jobs/step13_tune/winners.json`), so each row runs at the earlier rows' winners:
- `r00:relabel` labels the baseline build under the current labeler; `r00:check` checks those labels equal the
  reference labels (8e9cdad94970: the Q13-vol code is off by default and must change no label); `r00:selftest` scores
  the baseline against itself (every event shared, difference 0, the model deterministic); `r00:volfloor` re-fits the
  volatility-scaled stop floor on months 1-4 (`tuning.volfloor`, for row 13);
- `<row>:b<n>`: the row's challenger builds, two at a time in build slots 1 and 2 (`parallel_builds`), each a child
  `nqbot run feature_build[_2] --until <the same time>` (a build already complete is skipped; a child stopped by the
  clock stops this job the same way, and the chunk re-runs);
- `<row>:labels`: the labels every comparison of the row needs, by build key, up to `LABEL_WORKERS` sessions at once
  (`step13 labels-day D --build-key K --label-key L` children);
- `<row>:score`: the comparison (`tuning.compare`), the pre-declared pick (`tuning.pick`), the row's report
  (`<reports_dir>/step13/tune/<row>_<name>.md|json`) and its winner;
- `end:summary`: `tune_summary.md`, every winner and its lead over the next-best grid point.
Nothing is written to features.yaml or settings.yaml: the owner reads the summary, then the winners are written with
`frozen_from: months 1-4`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time as _time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np

from nqbot import months as months_mod
from nqbot.checks import feature_build as fb
from nqbot.checks import feature_build_job as fbj
from nqbot.checks import step13b as s13b
from nqbot.checks.step8b import Env
from nqbot.features.session_tape import ET
from nqbot.storage import atomic_write_json, atomic_write_text
from nqbot.tuning import compare as cmp
from nqbot.tuning import pick as pk
from nqbot.tuning import program as prog
from nqbot.tuning import stats
from nqbot.tuning import volfloor as vf
from nqbot.tuning.data import Build, load_build, load_picks
from nqbot.tuning.model import group_weight, phold

JOB = "step13_tune"
MONTHS_14 = s13b.MONTHS_14
LABEL_WORKERS = 4         # labels-day children at once (each <= 2.5 GB, 23 s; 8 cores on the build PC)
DAY_WORKERS = 4           # vol-floor day children at once
EST_CHILD_GB = 2.5        # a labels-day / vol-floor day's peak (step13_labels: max 2.45 GB)
POLL_SEC = 10.0           # how often the builds are looked at
POOL_POLL_SEC = 1.0
MIN_MINUTES_LEFT = 10     # no child build starts closer than this to --until
REFERENCE_LABELS = "8e9cdad94970"   # the baseline's 13b labels (owner 2026-11-14): r00:check compares with them
CHILD_CODE = "import sys; from nqbot.cli import main; sys.exit(main(sys.argv[1:]))"


class TuneRefused(Exception):
    pass


# --- files ------------------------------------------------------------------------------------------------------------

def tune_dir(env: Env) -> Path:
    return env.paths.data_dir / "jobs" / JOB


def plan_file(env: Env) -> Path:
    return tune_dir(env) / "tune.json"


def winners_file(env: Env) -> Path:
    return tune_dir(env) / "winners.json"


def cache_dir(env: Env) -> Path:
    return tune_dir(env) / "cache"


def rep_dir(env: Env) -> Path:
    return env.paths.reports_dir / "step13" / "tune"


def vol_day_file(env: Env, day: date) -> Path:
    return rep_dir(env) / "volfloor" / "days" / f"{day.isoformat()}.npz"


def vol_fit_file(env: Env) -> Path:
    return rep_dir(env) / "volfloor" / "volfloor.json"


def _read_json(p: Path, default=None):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


# --- the plan ---------------------------------------------------------------------------------------------------------

def program(env: Env) -> list[prog.Row]:
    return prog.rows(env.features, env.settings)


def describe(r: prog.Row) -> dict[str, Any]:
    return {"id": r.id, "name": r.name, "kind": r.kind, "values": [prog.jsonable(v) for v in r.values],
            "start": prog.jsonable(r.start), "rule": r.rule, "mode": r.mode, "subset": r.subset, "keys": list(r.keys),
            "note": r.note}


def key_for(env: Env, overrides: dict[str, Any]) -> str:
    probe = Env(settings=env.settings, features=fb.apply_overrides(env.features, overrides), paths=env.paths,
                log=env.log, echo=env.echo, cal=env.cal)
    return fb.build_key(probe)


def tune_plan(env: Env, reference_labels: str | None = REFERENCE_LABELS) -> dict[str, Any]:
    """`nqbot step13 tune-plan`: checks the baseline (the committed configuration's months 1-4 build) is complete and
    `parallel_builds` is on, records the program and the job's chunks."""
    from nqbot.jobs.runner import record_plan

    if not fbj.parallel_on():
        raise TuneRefused("parallel_builds is off in config/local.yaml: the step-13c driver runs the challenger "
                          "builds two at a time and holds the data locks shared (owner 2026-11-14)")
    key = fb.build_key(env)
    doc = s13b.build_doc_for(env, key)
    days = s13b.sessions_14(env, doc)
    built = set(s13b.built_days(env, doc))
    if any(d not in built for d in days):
        raise TuneRefused(f"the baseline build {key} is not complete ({sum(d not in built for d in days)} sessions)")
    rows = program(env)
    ids = prog.chunk_ids(rows)
    plan = {"baseline_key": key, "reference_labels": reference_labels, "sessions": [d.isoformat() for d in days],
            "program": [describe(r) for r in rows], "chunks": ids, "written": datetime.now(ET).isoformat()}
    old = _read_json(plan_file(env))
    if old and old.get("baseline_key") != key and _read_json(winners_file(env)):
        raise TuneRefused(f"winners exist for baseline {old['baseline_key']}, not {key}: move "
                          f"{winners_file(env)} aside first (a new baseline restarts the tuning)")
    atomic_write_json(plan_file(env), plan)
    record_plan(env.paths, JOB, ids)
    n_builds = sum(len(r.challengers) for r in rows if r.kind in ("build", "logloss"))
    env.echo(f"{JOB}: baseline {key} ({len(days)} sessions), {len(rows)} rows, {n_builds} challenger builds, "
             f"{len(ids)} chunks; run: uv run nqbot run {JOB} --until HH:MM (or queue it for nqbot resume)")
    for r in rows:
        env.echo(f"  {r.id} {r.name}: start {r.start!r}, challengers {r.challengers}, rule {r.rule}, run {r.mode}"
                 + (f", subset {r.subset}" if r.subset else ""))
    return plan


def load_plan(env: Env) -> dict[str, Any]:
    plan = _read_json(plan_file(env))
    if plan is None:
        raise TuneRefused("no step-13c plan: uv run nqbot step13 tune-plan")
    now = [describe(r) for r in program(env)]
    if now != plan["program"]:
        raise TuneRefused("the tuning grids or start values changed since tune-plan: re-plan (nqbot step13 tune-plan)")
    if fb.build_key(env) != plan["baseline_key"]:
        raise TuneRefused(f"the committed configuration's build key is {fb.build_key(env)}, the plan's baseline "
                          f"{plan['baseline_key']}: re-plan")
    return plan


def sessions(plan: dict) -> list[date]:
    return [date.fromisoformat(d) for d in plan["sessions"]]


# --- winners ----------------------------------------------------------------------------------------------------------

def winners(env: Env) -> dict[str, Any]:
    return _read_json(winners_file(env), {})


def set_winner(env: Env, row_id: str, rec: dict[str, Any]) -> None:
    w = winners(env)
    w[row_id] = rec
    atomic_write_json(winners_file(env), w)


def _need(env: Env, rows: list[prog.Row]) -> dict[str, Any]:
    w = winners(env)
    missing = [r.id for r in rows if r.id not in w]
    if missing:
        raise TuneRefused(f"rows {', '.join(missing)} have no winner yet (the rows run in order)")
    return w


def build_over(env: Env, row_id: str) -> dict[str, Any]:
    """The features.yaml overrides of the winners of every event- or feature-changing row before `row_id`."""
    rows = [r for r in prog.before(program(env), row_id) if r.kind in ("build", "logloss")]
    w = _need(env, rows)
    out: dict[str, Any] = {}
    for r in rows:
        out.update(w[r.id].get("build_overrides") or {})
    return out


def label_over(env: Env, row_id: str) -> dict[str, Any]:
    rows = [r for r in prog.before(program(env), row_id) if r.kind in ("exec_ttl", "exec_stop")]
    w = _need(env, rows)
    out: dict[str, Any] = {}
    for r in rows:
        out.update(w[r.id].get("label_overrides") or {})
    return out


def order_types(env: Env, row_id: str) -> dict[str, str]:
    rows = [r for r in prog.before(program(env), row_id) if r.kind == "exec_ttl"]
    if rows:
        return dict(_need(env, rows)[rows[0].id]["order_types"])
    return {s: env.settings["entry_order_type"][s] for s in ("c", "d")}


def top_n_won(env: Env) -> bool:
    """Row 14 is scored as event-changing if `node_relevance_top_n` = 5 won row 9 (§7.1, Q-dryrun13 (4))."""
    rec = winners(env).get("r09")
    if rec is None:
        raise TuneRefused("row r09 has no winner yet")
    return rec.get("value") is not None


# --- child processes --------------------------------------------------------------------------------------------------

def _until_text(ctx) -> str:
    left = (ctx.deadline.timestamp() - _time.time()) / 60
    if left < MIN_MINUTES_LEFT:
        from nqbot.jobs.runner import StopChunk

        raise StopChunk(f"{left:.0f} min to --until: no build starts now", status="until")
    return ctx.deadline.astimezone(ET).strftime("%H:%M")


def run_jobs(env: Env, ctx, jobs: list[str]) -> dict[str, Any]:
    """`nqbot run <job> --until <ours>` for each job at once, waiting for all; a `nqbot stop step13_tune` is passed on
    to them (each stops cleanly). Each result: the RunResult of its summary line, else died / refused / locked."""
    from nqbot.cli import EXIT_LOCKED, RUN_SUMMARY
    from nqbot.jobs.runner import STOP_REASON, RunResult, stop_request_path

    until = _until_text(ctx)
    logs = env.paths.log_dir / JOB
    logs.mkdir(parents=True, exist_ok=True)
    procs = {}
    for job in jobs:
        out = logs / f"{job}_{datetime.now(ET):%Y%m%d-%H%M%S}_{os.getpid()}.txt"
        fh = open(out, "w", encoding="utf-8")
        procs[job] = (subprocess.Popen([sys.executable, "-c", CHILD_CODE, "run", job, "--until", until], stdout=fh,
                                       stderr=subprocess.STDOUT, env={**os.environ, "PYTHONUTF8": "1"}), fh, out)
        env.echo(f"{JOB}: started {job} (pid {procs[job][0].pid}) until {until}, output {out}")
    forwarded = False
    while any(p.poll() is None for p, _, _ in procs.values()):
        if not forwarded and ctx.should_stop() == STOP_REASON:
            for job in jobs:
                stop_request_path(env.paths, job).parent.mkdir(parents=True, exist_ok=True)
                stop_request_path(env.paths, job).write_text("stop (step13_tune)\n", encoding="utf-8")
            forwarded = True
        ctx.sleep(POLL_SEC)
    res: dict[str, Any] = {}
    for job, (p, fh, out) in procs.items():
        fh.close()
        text = out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""
        m = next((m for m in (RUN_SUMMARY.match(x.strip()) for x in reversed(text.splitlines())) if m
                  and m["job"] == job), None)
        if m:
            res[job] = RunResult(m["status"], int(m["done"]), int(m["total"]), message=m["msg"] or "")
        else:
            tail = " | ".join(text.strip().splitlines()[-3:])
            res[job] = RunResult("locked" if p.returncode == EXIT_LOCKED else "died", 0, 0,
                                 message=f"exit {p.returncode}: {tail}")
    return res


def _mem_ok(running: int) -> bool:
    from nqbot.checks.step8b_disloc import free_mb
    from nqbot.jobs.runner import RAM_RESERVE_GB

    mb = free_mb()
    return running == 0 or mb is None or mb / 1024 >= RAM_RESERVE_GB + EST_CHILD_GB


def run_pool(env: Env, ctx, tasks: list[tuple[str, list[str]]], workers: int, log_name: str = JOB) -> None:
    """Short `nqbot <argv>` children, up to `workers` at once as memory allows; a failed child stops the chunk
    (status failures) once the running ones end; a stop (clock, request, SSD) starts no new child. Child logs under
    `<log_dir>/<log_name>/pool` (another job using the pool keeps its own, pre-13d `step13_rescore`)."""
    from nqbot.jobs.runner import StopChunk

    logs = env.paths.log_dir / log_name / "pool"
    logs.mkdir(parents=True, exist_ok=True)
    pending = list(tasks)
    running: list[tuple[str, subprocess.Popen, Any, Path]] = []
    failed: list[str] = []
    stop = None
    while pending or running:
        stop = stop or ctx.should_stop()
        while not stop and not failed and pending and len(running) < workers and _mem_ok(len(running)):
            name, argv = pending.pop(0)
            out = logs / f"{name}.txt"
            fh = open(out, "w", encoding="utf-8")
            running.append((name, subprocess.Popen([sys.executable, "-c", CHILD_CODE, *argv], stdout=fh,
                                                   stderr=subprocess.STDOUT, env={**os.environ, "PYTHONUTF8": "1"}),
                            fh, out))
        still = []
        for name, p, fh, out in running:
            if p.poll() is None:
                still.append((name, p, fh, out))
                continue
            fh.close()
            if p.returncode != 0:
                tail = " | ".join(out.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:])
                failed.append(f"{name} (exit {p.returncode}: {tail})")
            else:
                out.unlink(missing_ok=True)
        running = still
        if (stop or failed) and not running:
            break
        ctx.sleep(POOL_POLL_SEC)
    if failed:
        raise StopChunk(f"{len(failed)} child(ren) failed: {'; '.join(failed[:3])}", status="failures")
    if pending:
        raise StopChunk(f"stopped with {len(pending)} left ({stop})")


# --- labels -----------------------------------------------------------------------------------------------------------

def build_complete(env: Env, key: str, days: list[date]) -> bool:
    if not fb.build_plan_copy(env, key).exists():
        return False
    doc = {"key": key, "sessions": [d.isoformat() for d in days]}
    built = set(s13b.built_days(env, doc))
    return all(d in built for d in days)


def label_jobs(env: Env, targets: list[tuple[str, dict[str, Any]]], days: list[date]) -> list[tuple[str, list[str]]]:
    tasks = []
    for key, over in targets:
        lkey = s13b.labels_plan_for(env, key, over or None)["label_key"]
        for d in days:
            if not s13b.labelled(env, key, lkey, d):
                tasks.append((f"labels_{key}_{lkey}_{d.isoformat()}",
                              ["step13", "labels-day", d.isoformat(), "--build-key", key, "--label-key", lkey]))
    return tasks


def lkey_of(env: Env, key: str, over: dict[str, Any]) -> str:
    return s13b.labels_plan_for(env, key, over or None)["label_key"]


def ttl_over(row: prog.Row, ttl: Any) -> dict[str, Any]:
    return {} if ttl == row.start else {"entry_ttl_sec.c": ttl, "entry_ttl_sec.d": ttl}


def stop_over(env: Env, row: prog.Row, base: dict[str, Any], floor: str, buf: Any) -> dict[str, Any]:
    out = dict(base)
    if buf != row.start:
        out["stop_buffer"] = buf
    if floor == "vol":
        fit = _read_json(vol_fit_file(env))
        if fit is None:
            raise TuneRefused(f"no volatility-scaled floor fit ({vol_fit_file(env)}): chunk r00:volfloor first")
        out["stop_floor_vol"] = fit["override"]
    elif env.settings.get("stop_floor_vol") is not None:   # pre-13d: the floor is a setting; "fixed" forces it off
        out["stop_floor_vol"] = None
    return out


def row_label_targets(env: Env, row: prog.Row) -> list[tuple[str, dict[str, Any]]]:
    """(build key, labeler overrides) of every labels set the row's score reads."""
    base = build_over(env, row.id)
    if row.kind == "build" or (row.kind == "logloss" and top_n_won(env)):
        lov = label_over(env, row.id)
        keys = [key_for(env, base)] + [key_for(env, {**base, **row.build_overrides(v)}) for v in row.challengers]
        return [(k, lov) for k in keys]
    if row.kind == "logloss":
        return []
    key = key_for(env, base)
    if row.kind == "exec_ttl":
        return [(key, {**label_over(env, row.id), **ttl_over(row, t)}) for t in row.values]
    lov = label_over(env, row.id)
    return [(key, stop_over(env, row, lov, f, b)) for f in row.extra["floors"] for b in row.values]


# --- scoring ----------------------------------------------------------------------------------------------------------

class Loader:
    """Builds, predictions and picks, each loaded once per process."""

    def __init__(self, env: Env, plan: dict):
        self.env, self.plan = env, plan
        self.days = sessions(plan)
        self.month_of = {d.isoformat(): months_mod.month_index(d, env.settings) for d in self.days}
        self._b: dict[str, Build] = {}
        self._p: dict[str, dict] = {}
        self.threads = max(1, (os.cpu_count() or 1) - 2)

    def build(self, key: str) -> Build:
        if key not in self._b:
            if not build_complete(self.env, key, self.days):
                raise TuneRefused(f"build {key} is not complete")
            self._b[key] = load_build(self.env, key, self.days, self.month_of)
        return self._b[key]

    def p(self, key: str) -> dict[str, float]:
        if key not in self._p:
            cache_dir(self.env).mkdir(parents=True, exist_ok=True)
            self._p[key], _ = phold(self.build(key), self.env.settings, self.threads, cache_dir(self.env))
        return self._p[key]

    def picks(self, key: str, lkey: str) -> list[dict]:
        for d in self.days:
            if not s13b.labelled(self.env, key, lkey, d):
                raise TuneRefused(f"labels {lkey} of build {key} are not complete ({d})")
        return load_picks(self.env, key, lkey, self.days, cache_dir(self.env) / f"picks_{key}_{lkey}.jsonl")

    @property
    def n_months(self) -> float:
        return float(len(set(self.month_of.values())))


def _reps(env: Env) -> int:
    return int(env.settings["tune_13"]["bootstrap_replicates"])


def _distance(row: prog.Row):
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in row.values):
        return pk.numeric_distance
    return pk.index_distance(list(row.values))


def _pick(row: prog.Row, pts: list[dict]) -> Any:
    return pk.best_value(row.start, pts, _distance(row)) if row.rule == "best" else pk.keep_current(row.start, pts)


DETECTOR_ROWS = ("r01", "r02", "r03", "r05a", "r05b", "r05c")   # owner 2026-11-14: the (c)/(d) disagreement flag
TRADE_CUT_FLAG = 0.10    # owner 2026-11-14: flag a winner with more than 10% fewer trades than the start value
ALL_MODES = ("combined", "c", "d")


def _lead_of(win: Any, start: Any, scores: dict[Any, dict], reps: int, n_months: float) -> dict[str, Any]:
    """The winner's lead over the next-best grid point on the excess net R (owner 2026-11-14): points ranked by their
    excess against the start value (the start counting 0). `scores`: {value: a `compare.score` dict} (start excluded)."""
    pts = [(start, 0.0)] + [(v, s["diff"]) for v, s in scores.items()]
    others = sorted([x for x in pts if x[0] != win], key=lambda x: -x[1])
    if not others:
        return {}
    nxt = others[0][0]
    w = None if win == start else scores[win]
    r = None if nxt == start else scores[nxt]
    d = cmp.lead(w, r, n_months, reps)
    return {"next_best": prog.jsonable(nxt), "lead": d[0], "lo": d[1], "hi": d[2]}


def _trade_cut_flag(win: Any, start: Any, sc: dict | None, what: str = "") -> list[str]:
    if win == start or sc is None or sc.get("trades_change_pct") is None:
        return []
    if sc["trades_change_pct"] < -TRADE_CUT_FLAG:
        return [f"{what}the winner {win!r} has {-sc['trades_change_pct']:.1%} fewer trades than the start value "
                f"({sc['trades_change']:+.0f})"]
    return []


def _rule_key(row: prog.Row, mode: str, rule: str = "none") -> str:
    return rule if mode == row.mode else f"{mode}:{rule}"


def _fmt(x: float | None, nd: int = 2) -> str:
    return "" if x is None else f"{x:+.{nd}f}"


def score_build_row(env: Env, plan: dict, row: prog.Row, ld: Loader | None = None,
                    event_changing_logloss: bool = False) -> dict[str, Any]:
    """Rows 1-11 (and row 14 when event-changing): each challenger build against the start value's build."""
    ld = ld or Loader(env, plan)
    reps = _reps(env)
    base = build_over(env, row.id)
    lov = label_over(env, row.id)
    ot = order_types(env, row.id)
    k0 = key_for(env, base)
    l0 = lkey_of(env, k0, lov)
    b0, p0, x0 = ld.build(k0), ld.p(k0), ld.picks(k0, l0)
    res: dict[Any, dict] = {}
    keys: dict[Any, str] = {}
    for v in row.challengers:
        kv = key_for(env, {**base, **row.build_overrides(v)})
        lv = lkey_of(env, kv, lov)
        keys[v] = kv
        res[v] = cmp.compare(b0, ld.build(kv), p0, ld.p(kv), x0, ld.picks(kv, lv), ot, row.mode, row.subset, reps,
                             extra_modes=tuple(m for m in ALL_MODES if m != row.mode))
    out: dict[str, Any] = {"row": describe(row), "start_key": k0, "start_labels": l0, "order_types": ot,
                           "label_overrides": lov, "build_overrides_before": base, "keys": {}, "results": {},
                           "score": "excess net R per month (owner 2026-11-14, SPEC §13 Q-step13c (5))"}

    def pts_of(key: str) -> list[dict]:
        return [{"value": v, "diff": r["rules"][key]["diff"], "lo": r["rules"][key]["lo"], "hi": r["rules"][key]["hi"]}
                for v, r in res.items()]

    win = _pick(row, pts_of("none"))
    win_fs = _pick(row, pts_of("fs"))
    flags = []
    if win_fs != win:
        flags.append(f"breakeven rule fs would pick {win_fs!r} (none picks {win!r}); decided on none (owner 2026-11-14)")
    flags += _trade_cut_flag(win, row.start, res.get(win, {}).get("rules", {}).get("none"))
    if row.mode == "c" and win != row.start:
        d = res[win]["rules"]["d:none"]
        if d["hi"] < 0:
            flags.append(f"the (c) winner {win!r} makes style (d) clearly worse than the start value: (d)-only excess "
                         f"{d['diff']:+.2f} R/month [{d['lo']:+.2f}, {d['hi']:+.2f}] (information only)")
    if row.id in DETECTOR_ROWS:
        wc, wd = _pick(row, pts_of(_rule_key(row, "c"))), _pick(row, pts_of(_rule_key(row, "d")))
        if wc != wd:
            flags.append(f"the (c)-only excess would pick {wc!r}, the (d)-only excess {wd!r} (information only)")
    out["winner"] = prog.jsonable(win)
    out["winner_fs"] = prog.jsonable(win_fs)
    out["lead"] = _lead_of(win, row.start, {v: r["rules"]["none"] for v, r in res.items()}, reps, ld.n_months)
    out["flags"] = flags
    for v, r in res.items():
        out["keys"][json.dumps(prog.jsonable(v))] = keys[v]
        out["results"][json.dumps(prog.jsonable(v))] = r
    rec = {"value": prog.jsonable(win), "build_overrides": row.build_overrides(win),
           "key": keys.get(win, k0), "lead": out["lead"], "flags": flags}
    _write_row_report(env, row, out, _build_row_lines(row, out, res, ld.n_months))
    set_winner(env, row.id, rec)
    return out


def _build_row_lines(row: prog.Row, out: dict, res: dict, n_months: float) -> list[str]:
    lines = [f"# Step 13c row {row.id}: {row.name}", "",
             f"Start value {row.start!r} (build {out['start_key']}, labels {out['start_labels']}); rule "
             f"{'best value (Q-step13plan (8))' if row.rule == 'best' else 'keep-current (95% interval excludes 0)'}; "
             f"scored run: {row.mode}" + (f"; subset: {row.subset}" if row.subset else "")
             + f"; order types {out['order_types']}; breakeven none decides, fs for information.", "",
             "**Score (decides):** the excess net R per month over the start value, (R_value - R_start) - the start "
             "run's mean net R per trade x (N_value - N_start), with its 95% day-block interval (owner 2026-11-14, "
             "SPEC §13 Q-step13c (5)). Beside it, for information: the total net R difference, net R per trade, the "
             "trade-count change and the (c)-only and (d)-only excess.", ""]
    if row.note:
        lines += [row.note, ""]
    head = ("| value | build | events (shared) | excess R/mo [95%] | total R/mo diff [95%] | R/trade start -> value | "
            "trades start -> value | (c)-only excess [95%] | (d)-only excess [95%] |")
    lines += [head + (" subset excess [95%] | subset trades start / value |" if row.subset else ""),
              "|---|---|---:|---|---|---|---|---|---|" + ("---|---|" if row.subset else "")]

    def ci(x) -> str:
        return f"{x[0]:+.2f} [{x[1]:+.2f}, {x[2]:+.2f}]"

    for v, r in res.items():
        a = r["rules"]["none"]
        c_, d_ = r["rules"][_rule_key(row, "c")], r["rules"][_rule_key(row, "d")]
        pct = "" if a["trades_change_pct"] is None else f" ({a['trades_change_pct']:+.1%})"
        pts = ("" if a["per_trade_start"] is None or a["per_trade_value"] is None else
               f"{a['per_trade_start']:+.3f} -> {a['per_trade_value']:+.3f}")
        line = (f"| {v!r} | {out['keys'][json.dumps(prog.jsonable(v))]} | {r['events']['challenger']:,} "
                f"({r['events']['shared']:,}) | {ci([a['diff'], a['lo'], a['hi']])} | {ci(a['total'])} | {pts} | "
                f"{a['start']['trades']} -> {a['challenger']['trades']}{pct} | {ci([c_['diff'], c_['lo'], c_['hi']])} | "
                f"{ci([d_['diff'], d_['lo'], d_['hi']])} |")
        if row.subset:
            line += (f" {ci(a['subset_excess'])} | {a['start']['subset']['trades']} / "
                     f"{a['challenger']['subset']['trades']} |")
        lines.append(line)
    lines += ["", "With breakeven rule fs (information, excess): "
              + "; ".join(f"{v!r} {r['rules']['fs']['diff']:+.2f} [{r['rules']['fs']['lo']:+.2f}, "
                          f"{r['rules']['fs']['hi']:+.2f}]" for v, r in res.items())]
    if row.mode == "c":
        lines.append("Combined (c)+(d) run (information, excess): " + "; ".join(
            f"{v!r} {r['rules']['combined:none']['diff']:+.2f} [{r['rules']['combined:none']['lo']:+.2f}, "
            f"{r['rules']['combined:none']['hi']:+.2f}]" for v, r in res.items()))
    ld = out["lead"]
    lines += ["", f"**Winner: {out['winner']!r}**" + (" (the start value stays)" if out["winner"] == prog.jsonable(row.start)
                                                       else "")]
    if ld:
        lines.append(f"Lead over the next-best grid point ({ld['next_best']!r}): {ld['lead']:+.2f} R/month excess "
                     f"[{ld['lo']:+.2f}, {ld['hi']:+.2f}]")
    lines += [f"- FLAG: {f}" for f in out["flags"]]
    return lines


def _write_row_report(env: Env, row: prog.Row, out: dict, lines: list[str]) -> None:
    name = f"{row.id}_{row.name.replace(' ', '_').replace('x', '')}".replace("__", "_")
    rep_dir(env).mkdir(parents=True, exist_ok=True)
    atomic_write_json(rep_dir(env) / f"{name}.json", out)
    atomic_write_text(rep_dir(env) / f"{name}.md", "\n".join(lines) + "\n")


def score_exec_ttl(env: Env, plan: dict, row: prog.Row, ld: Loader | None = None) -> dict[str, Any]:
    """Row 12: per style, (order type, entry_ttl_sec) on its own single-style policy, the events frozen (every event
    shared, so one bar per fold for all points)."""
    ld = ld or Loader(env, plan)
    reps = _reps(env)
    key = key_for(env, build_over(env, row.id))
    lov = label_over(env, row.id)
    b, p = ld.build(key), ld.p(key)
    bar = cmp.bars(b, p, [e["event_id"] for e in b.events])
    picks = {t: ld.picks(key, lkey_of(env, key, {**lov, **ttl_over(row, t)})) for t in row.values}
    start_order = row.extra["start_order"]
    out: dict[str, Any] = {"row": describe(row), "key": key, "styles": {}}
    rec_ot, rec_ttl, flags = {}, {}, []
    lines = [f"# Step 13c row {row.id}: {row.name}", "", f"Frozen events: build {key}; the start point per style "
             f"(start order type, entry_ttl_sec {row.start}); each style on its own single-style policy; score: the "
             "excess net R per month over the start point (owner 2026-11-14, Q-step13c (5)); rule best value (start "
             "counts 0); ties: fewer things changed (only the ttl, then only the order type, then both), then "
             "|ttl - start| in seconds, then the larger lower bound, then the start stays (Q-step13c (7)).", ""]
    for s in ("c", "d"):
        start_pt = (start_order[s], row.start)
        per = {}
        for rule in ("none", "fs"):
            rs, ns = cmp.fixed_events_series(b, picks[row.start], p, bar, {s: start_order[s]}, (s,), rule)[s]
            sc: dict[Any, dict] = {}
            for ot in row.extra["order_types"][s]:
                for t in row.values:
                    if (ot, t) == start_pt:
                        continue
                    rc, nc = cmp.fixed_events_series(b, picks[t], p, bar, {s: ot}, (s,), rule)[s]
                    sc[(ot, t)] = {**cmp.score(rc, nc, rs, ns, ld.n_months, reps),
                                   "r_per_month": float(rc.sum() / ld.n_months), "trades": int(nc.sum())}
            per[rule] = (sc, float(rs.sum() / ld.n_months), int(ns.sum()))
        sc, base_r, base_n = per["none"]
        pts = [{"value": v, "diff": x["diff"], "lo": x["lo"], "hi": x["hi"]} for v, x in sc.items()]
        win = pk.best_value(start_pt, pts, pk.exec_distance)
        win_fs = pk.best_value(start_pt, [{"value": v, "diff": x["diff"], "lo": x["lo"], "hi": x["hi"]}
                                          for v, x in per["fs"][0].items()], pk.exec_distance)
        if win_fs != win:
            flags.append(f"style {s}: breakeven rule fs would pick {win_fs} (none: {win})")
        flags += _trade_cut_flag(win, start_pt, sc.get(win), f"style {s}: ")
        lead = _lead_of(win, start_pt, sc, reps, ld.n_months)
        rec_ot[s], rec_ttl[s] = win[0], win[1]
        out["styles"][s] = {"start": list(start_pt), "start_r_per_month": base_r, "start_trades": base_n,
                            "winner": list(win), "lead": lead,
                            "points": [{**{k: v for k, v in x.items() if k != "series"}, "value": list(v)}
                                       for v, x in sc.items()]}
        lines += [f"## Style ({s})", "", f"Start {start_pt}: {base_r:.2f} R/month, {base_n} trades", "",
                  "| order type | ttl | excess R/mo [95%] | total R/mo diff [95%] | R/trade | trades (change) |",
                  "|---|---:|---|---|---:|---|"]
        for (ot_, t_), x in sc.items():
            pct = "" if x["trades_change_pct"] is None else f" ({x['trades_change_pct']:+.1%})"
            lines.append(f"| {ot_} | {t_} | {x['diff']:+.2f} [{x['lo']:+.2f}, {x['hi']:+.2f}] | {x['total'][0]:+.2f} "
                         f"[{x['total'][1]:+.2f}, {x['total'][2]:+.2f}] | "
                         f"{'' if x['per_trade_value'] is None else format(x['per_trade_value'], '+.3f')} | "
                         f"{x['trades']}{pct} |")
        lines += ["", f"**Winner ({s}): {win}**" + (" (the start point stays)" if win == start_pt else ""),
                  f"Lead over the next-best point ({lead['next_best']}): {lead['lead']:+.2f} R/month excess "
                  f"[{lead['lo']:+.2f}, {lead['hi']:+.2f}]", ""]
    lines += [f"- FLAG: {f}" for f in flags]
    lov_out = {f"entry_ttl_sec.{s}": rec_ttl[s] for s in ("c", "d") if rec_ttl[s] != row.start}
    rec = {"value": {s: [rec_ot[s], rec_ttl[s]] for s in ("c", "d")}, "order_types": rec_ot,
           "label_overrides": lov_out, "flags": flags,
           "lead": {s: out["styles"][s]["lead"] for s in ("c", "d")}}
    out["flags"] = flags
    _write_row_report(env, row, out, lines)
    set_winner(env, row.id, rec)
    return out


def score_exec_stop(env: Env, plan: dict, row: prog.Row, ld: Loader | None = None) -> dict[str, Any]:
    """Row 13: stop_buffer x {fixed, vol-scaled} floor (owner 2026-11-14: the full grid), at the row-12 winners, scored
    on the (c) and (d) single-style policies summed; the best buffer per floor (best value), then the vol-scaled floor
    replaces the fixed one only by the keep-current rule."""
    ld = ld or Loader(env, plan)
    reps = _reps(env)
    key = key_for(env, build_over(env, row.id))
    lov = label_over(env, row.id)
    ot = order_types(env, row.id)
    b, p = ld.build(key), ld.p(key)
    bar = cmp.bars(b, p, [e["event_id"] for e in b.events])
    ser: dict[str, dict[tuple, dict]] = {"none": {}, "fs": {}}
    for f in row.extra["floors"]:
        for buf in row.values:
            picks = ld.picks(key, lkey_of(env, key, stop_over(env, row, lov, f, buf)))
            for rule in ("none", "fs"):
                ser[rule][(f, buf)] = cmp.fixed_events_series(b, picks, p, bar, ot, ("c", "d"), rule)
    out: dict[str, Any] = {"row": describe(row), "key": key, "order_types": ot, "label_overrides_before": lov,
                           "floors": {}, "vol_fit": _read_json(vol_fit_file(env))}
    lines = [f"# Step 13c row {row.id}: {row.name}", "", f"Frozen events: build {key}; order types {ot}; "
             f"labels at {lov or 'the start values'}; (c) and (d) single-style policies summed. Score: the excess net "
             f"R per month (owner 2026-11-14, Q-step13c (5)). The best stop_buffer per floor (best value, start "
             f"{row.start}), then the vol-scaled floor (QQQ stops only; NDXP keeps 6.50) against the fixed one "
             "(keep-current).", ""]

    def sc_of(rule: str, a: tuple, base: tuple, part: str = "sum") -> dict:
        rc, nc = ser[rule][a][part]
        rs, ns = ser[rule][base][part]
        return cmp.score(rc, nc, rs, ns, ld.n_months, reps)

    best: dict[str, dict[str, Any]] = {}
    for rule in ("none", "fs"):
        best[rule] = {}
        for f in row.extra["floors"]:
            scs = {buf: sc_of(rule, (f, buf), (f, row.start)) for buf in row.values if buf != row.start}
            pts = [{"value": v, "diff": x["diff"], "lo": x["lo"], "hi": x["hi"]} for v, x in scs.items()]
            best[rule][f] = (pk.best_value(row.start, pts, pk.numeric_distance), scs)
    for f in row.extra["floors"]:
        w, scs = best["none"][f]
        rs0, ns0 = ser["none"][(f, row.start)]["sum"]
        lead = _lead_of(w, row.start, scs, reps, ld.n_months)
        out["floors"][f] = {"winner": w, "start_r_per_month": float(rs0.sum() / ld.n_months),
                            "start_trades": int(ns0.sum()), "lead": lead,
                            "points": [{**{k: v for k, v in x.items() if k != "series"}, "value": v,
                                        "c_excess": sc_of("none", (f, v), (f, row.start), "c")["diff"],
                                        "d_excess": sc_of("none", (f, v), (f, row.start), "d")["diff"]}
                                       for v, x in scs.items()]}
        lines += [f"## Floor: {f}", "", f"stop_buffer {row.start}: {rs0.sum() / ld.n_months:.2f} R/month, "
                  f"{int(ns0.sum())} trades", "",
                  "| stop_buffer | excess R/mo [95%] | total R/mo diff [95%] | R/trade | trades (change) | (c) excess | "
                  "(d) excess |", "|---:|---|---|---:|---|---:|---:|"]
        for x in out["floors"][f]["points"]:
            pct = "" if x["trades_change_pct"] is None else f" ({x['trades_change_pct']:+.1%})"
            lines.append(f"| {x['value']} | {x['diff']:+.2f} [{x['lo']:+.2f}, {x['hi']:+.2f}] | {x['total'][0]:+.2f} "
                         f"[{x['total'][1]:+.2f}, {x['total'][2]:+.2f}] | "
                         f"{'' if x['per_trade_value'] is None else format(x['per_trade_value'], '+.3f')} | "
                         f"{x['trades_change']:+.0f}{pct} | {x['c_excess']:+.2f} | {x['d_excess']:+.2f} |")
        lines += ["", f"Best stop_buffer ({f}): **{w}**; lead over the next-best ({lead['next_best']}): "
                      f"{lead['lead']:+.2f} R/month excess [{lead['lo']:+.2f}, {lead['hi']:+.2f}]", ""]
    bf, bv = best["none"]["fixed"][0], best["none"]["vol"][0]
    vs = sc_of("none", ("vol", bv), ("fixed", bf))
    floor = pk.keep_current("fixed", [{"value": "vol", "diff": vs["diff"], "lo": vs["lo"], "hi": vs["hi"]}])
    win_buf = bv if floor == "vol" else bf
    flags = []
    fvs = sc_of("fs", ("vol", best["fs"]["vol"][0]), ("fixed", best["fs"]["fixed"][0]))
    fs_floor = pk.keep_current("fixed", [{"value": "vol", "diff": fvs["diff"], "lo": fvs["lo"], "hi": fvs["hi"]}])
    fs_buf = best["fs"]["vol"][0] if fs_floor == "vol" else best["fs"]["fixed"][0]
    if (fs_floor, fs_buf) != (floor, win_buf):
        flags.append(f"breakeven rule fs would pick {fs_floor} floor, stop_buffer {fs_buf} (none: {floor}, {win_buf})")
    if (floor, win_buf) != ("fixed", row.start):
        flags += _trade_cut_flag((floor, win_buf), ("fixed", row.start),
                                 sc_of("none", (floor, win_buf), ("fixed", row.start)))
    out.update({"vol_vs_fixed": {k: v for k, v in vs.items() if k != "series"} | {"fixed_buffer": bf,
                                                                                   "vol_buffer": bv},
                "winner": {"floor": floor, "stop_buffer": win_buf}, "flags": flags})
    lines += ["## Vol-scaled against fixed (keep-current)", "",
              f"vol @ {bv} - fixed @ {bf}: excess {vs['diff']:+.2f} R/month [{vs['lo']:+.2f}, {vs['hi']:+.2f}]; total "
              f"{vs['total'][0]:+.2f} [{vs['total'][1]:+.2f}, {vs['total'][2]:+.2f}]; trades {vs['trades_change']:+.0f}",
              "", f"**Winner: {floor} floor, stop_buffer {win_buf}**"] + [f"- FLAG: {x}" for x in flags]
    label_out = dict(stop_over(env, row, {}, floor, win_buf))
    rec = {"value": {"floor": floor, "stop_buffer": win_buf}, "label_overrides": label_out, "flags": flags,
           "lead": out["floors"][floor]["lead"]}
    _write_row_report(env, row, out, lines)
    set_winner(env, row.id, rec)
    return out


def score_logloss(env: Env, plan: dict, row: prog.Row, ld: Loader | None = None) -> dict[str, Any]:
    """Row 14: the switch window by the provisional model's day-blocked CV log loss on the fixed events (a value
    replaces the start only if lower with the interval excluding 0); event-changing if `node_relevance_top_n` = 5 won."""
    if top_n_won(env):
        return score_build_row(env, plan, row, ld)
    ld = ld or Loader(env, plan)
    reps = _reps(env)
    base = build_over(env, row.id)
    k0 = key_for(env, base)
    b0, p0 = ld.build(k0), ld.p(k0)
    w = group_weight(b0)
    n0, d0 = cmp.logloss_by_day(b0, p0, lambda e: w[e["event_id"]])
    pts, keys, info = [], {}, {}
    for v in row.challengers:
        kv = key_for(env, {**base, **row.build_overrides(v)})
        bv = ld.build(kv)
        if not cmp.same_set(b0, bv):
            raise TuneRefused(f"row {row.id} {row.name}={v}: build {kv}'s events or structure labels differ from the "
                              f"start build {k0}'s; the row expects the feature rows only to change (owner decides)")
        nv, dv = cmp.logloss_by_day(bv, ld.p(kv), lambda e: w[e["event_id"]])
        diff, lo, hi = stats.ratio_diff_ci(nv, dv, n0, d0, reps)
        pts.append({"value": v, "diff": diff, "lo": lo, "hi": hi, "logloss": float(nv.sum() / dv.sum())})
        keys[v] = kv
    win = pk.keep_current_lower(row.start, pts)
    ll0 = float(n0.sum() / d0.sum())
    others = sorted([(row.start, 0.0)] + [(x["value"], x["diff"]) for x in pts], key=lambda x: x[1])
    nxt = [x for x in others if x[0] != win][0]
    win_diff = 0.0 if win == row.start else next(x["diff"] for x in pts if x["value"] == win)
    out = {"row": describe(row), "start_key": k0, "start_logloss": ll0, "points": pts,
           "keys": {str(k): v for k, v in keys.items()}, "winner": win,
           "lead": {"next_best": nxt[0], "lead": nxt[1] - win_diff}}
    lines = [f"# Step 13c row {row.id}: {row.name}", "", f"Day-blocked CV log loss of the provisional structure model "
             f"on the fixed events (start {row.start}: {ll0:.5f}, build {k0}); a value replaces the start only if "
             "lower with the 95% interval excluding 0.", "", "| value | build | log loss | diff [95%] |",
             "|---:|---|---:|---|"]
    lines += [f"| {x['value']} | {keys[x['value']]} | {x['logloss']:.5f} | {x['diff']:+.5f} [{x['lo']:+.5f}, "
              f"{x['hi']:+.5f}] |" for x in pts]
    lines += ["", f"**Winner: {win}**" + (" (the start value stays)" if win == row.start else ""),
              f"Lead over the next-best ({nxt[0]}): {out['lead']['lead']:+.5f} log loss (lower is better)"]
    rec = {"value": win, "build_overrides": row.build_overrides(win), "key": keys.get(win, k0), "lead": out["lead"]}
    _write_row_report(env, row, out, lines)
    set_winner(env, row.id, rec)
    return out


def score_row(env: Env, plan: dict, row: prog.Row, ld: Loader | None = None) -> dict[str, Any]:
    if row.kind == "build":
        return score_build_row(env, plan, row, ld)
    if row.kind == "exec_ttl":
        return score_exec_ttl(env, plan, row, ld)
    if row.kind == "exec_stop":
        return score_exec_stop(env, plan, row, ld)
    return score_logloss(env, plan, row, ld)


# --- r00 --------------------------------------------------------------------------------------------------------------

def check_reference(env: Env, plan: dict) -> dict[str, Any]:
    """r00:check: the baseline's labels under the current labeler (Q13-vol off) equal the reference labels."""
    import pyarrow.parquet as pq

    ref = plan.get("reference_labels")
    key = plan["baseline_key"]
    new = lkey_of(env, key, {})
    out = {"baseline": key, "labels": new, "reference": ref, "days": 0, "different": []}
    if not ref or ref == new:
        out["note"] = "no reference labels, or the same key: nothing to compare"
        return out
    for d in sessions(plan):
        for kind in ("pairs", "fills", "paths"):
            a = s13b.label_paths(env, {"key": key}, ref, d)[kind]
            b = s13b.label_paths(env, {"key": key}, new, d)[kind]
            ta = pq.read_table(a)
            tb = pq.read_table(b)
            sort = "pair_id" if "pair_id" in ta.column_names else "fill_id"
            ta, tb = ta.sort_by(sort), tb.sort_by(sort)
            if ta.column_names != tb.column_names or not ta.equals(tb):
                out["different"].append(f"{d} {kind}")
        out["days"] += 1
    atomic_write_json(rep_dir(env) / "r00_check.json", out)
    if out["different"]:
        raise TuneRefused(f"the baseline's labels {new} differ from the reference {ref} on {len(out['different'])} "
                          f"file(s), first {out['different'][0]}: the Q13-vol code must change no label when off")
    return out


def selftest(env: Env, plan: dict) -> dict[str, Any]:
    """r00:selftest: the baseline against itself: every event shared, difference exactly 0, the model deterministic
    (two fits give the same predictions)."""
    ld = Loader(env, plan)
    key = plan["baseline_key"]
    lkey = lkey_of(env, key, {})
    b = ld.build(key)
    p1, _ = phold(b, env.settings, ld.threads, None)
    p2 = ld.p(key)
    same_p = all(abs(p1[k] - p2[k]) == 0 for k in p1)
    x = ld.picks(key, lkey)
    res = cmp.compare(b, b, p1, p1, x, x, order_types(env, "r01"), "combined", None, _reps(env))
    a = res["rules"]["none"]
    out = {"key": key, "labels": lkey, "events": len(b.events), "shared": res["events"]["shared"],
           "diff": a["diff"], "lo": a["lo"], "hi": a["hi"], "trades": a["start"]["trades"],
           "r_per_month": a["start"]["r_per_month"], "deterministic_model": same_p,
           "bars": res["bars"]["start"]}
    lines = ["# Step 13c self-test: the baseline against itself", "",
             f"- Build {key}, labels {lkey}: {len(b.events):,} events, {out['shared']:,} shared",
             f"- Model fitted twice: predictions identical {same_p}",
             f"- Baseline run: {out['trades']} trades, {out['r_per_month']:.2f} net R per month (combined (c)+(d), "
             f"breakeven none); difference {a['diff']:+.4f} [{a['lo']:+.4f}, {a['hi']:+.4f}]",
             f"- Fold bars (median P(held) of the shared events): {out['bars']}"]
    atomic_write_text(rep_dir(env) / "r00_selftest.md", "\n".join(lines) + "\n")
    atomic_write_json(rep_dir(env) / "r00_selftest.json", out)
    if out["shared"] != len(b.events) or a["diff"] != 0 or a["lo"] != 0 or a["hi"] != 0 or not same_p:
        raise TuneRefused(f"the self-test failed (see {rep_dir(env) / 'r00_selftest.md'})")
    return out


def volfloor_day(env: Env, day: date) -> Path:
    """`nqbot step13 vol-floor-day D`: one session's QQQ crossings on NQ with the trailing volatility (read-only)."""
    from nqbot.checks import step8b as s8
    from nqbot.checks import step8b_gate as g
    from nqbot.features import session_tape as st
    from nqbot.labeler import candidates as cand

    months_mod.require_development(day, env.settings, MONTHS_14)
    path = vol_day_file(env, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    import io

    buf = io.BytesIO()
    saved = False
    try:
        front = s8.front_symbols(env)
        futs = s8.day_futures(env, day, front)
        for m, md in s8.iter_day_maps(env, day, [vf.SOURCE], futs):
            if set(md.resolution) != {"1s"}:
                np.savez_compressed(buf, skip=np.array(f"not all 1-second boards ({sorted(set(md.resolution))})"))
            else:
                x = g.chain_crossings(env, day, md, futs[s8.CHAIN_ROOT[m]])
                fut = futs[s8.CHAIN_ROOT[m]]
                bars = st.bars(st.Tape.from_arrays(day, fut.ts, fut.px, symbol=fut.symbol), 1)
                vol = np.array([np.nan if (r := cand.trailing_range(bars, int(t), vf.WINDOW_MIN)) is None else r
                                for t in x["t"]])
                np.savez_compressed(buf, err=x["chosen"], t=x["t"], vol=vol)
            saved = True
    except s8.CheckRefused as e:   # e.g. no Skylit boards (2025-10-01 to 10-06, before the history's reach)
        buf = io.BytesIO()
        np.savez_compressed(buf, skip=np.array(str(e)))
        saved = True
    if not saved:
        np.savez_compressed(buf, skip=np.array("no QQQ map"))
    from nqbot.storage import atomic_write_bytes

    atomic_write_bytes(path, buf.getvalue())
    return path


def volfloor_fit(env: Env, plan: dict) -> dict[str, Any]:
    errs, vols, used, skipped = [], [], 0, []
    for d in sessions(plan):
        z = np.load(vol_day_file(env, d), allow_pickle=False)
        if "skip" in z.files:
            skipped.append(f"{d}: {z['skip']}")
            continue
        errs.append(z["err"])
        vols.append(z["vol"])
        used += 1
    err, vol = np.concatenate(errs), np.concatenate(vols)
    res = vf.fit(err, vol)
    fixed = float(env.settings["stop_buffer_floor"][vf.SOURCE])
    out = {**res, "override": vf.override(res), "sessions": used, "skipped": skipped, "fixed_floor": fixed,
           "coverage_fixed": vf.coverage_fixed(err, vol, fixed)}
    atomic_write_json(vol_fit_file(env), out)
    lines = ["# Step 13c: the volatility-scaled stop floor re-fitted on months 1-4 (Q13-vol)", "",
             f"QQQ crossings on NQ over {used} sessions ({len(skipped)} skipped), {res['n']:,} crossings with a known "
             f"trailing {vf.WINDOW_MIN}-minute volatility.", "",
             f"- Floor = max({res['min']}, {res['k']} x vol15) NQ pts: covers {res['coverage']:.1%}; the fixed "
             f"{fixed} covers {out['coverage_fixed']:.1%}", "", "| vol15 quartile | crossings | covered (scaled) | "
             "mean floor |", "|---|---:|---:|---:|"]
    lines += [f"| Q{q['q']} | {q['n']:,} | {q['coverage_scaled']:.1%} | {q['mean_floor']:.2f} |" for q in res["quartiles"]
              if q["n"]]
    lines += ["", "NDXP keeps its fixed floor 6.50 (owner 2026-11-14, Q-step13c (8): its crossing error barely grows "
                  "with volatility, Spearman 0.06 on the five October recordings, and a fit would rest on those 5 days); "
                  "chart levels 0. Used only by row 13 (labels with `stop_floor_vol`)."]
    atomic_write_text(rep_dir(env) / "volfloor" / "volfloor.md", "\n".join(lines) + "\n")
    return out


# --- the summary ------------------------------------------------------------------------------------------------------

def summary(env: Env, plan: dict) -> Path:
    w = winners(env)
    rows = program(env)
    lines = ["# Step 13c tuning: winners (months 1-4)", "",
             f"Baseline build {plan['baseline_key']}; combined (c)+(d) run for rows 1-11 (row 4: (c) only), the "
             "bootstrap policy (stop 2, nearest target >= 1:1), breakeven none (fs reported), each fold's bar on the "
             "shared events; score = the excess net R per month over the start value (Q-step13c (5)); lead = the "
             "winner's excess over the next-best grid point's, with its 95% day-block interval.", "",
             "| row | parameter | start | winner | lead over the next-best | flags |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        rec = w.get(r.id)
        if rec is None:
            lines.append(f"| {r.id} | {r.name} | {r.start!r} | (not run yet) | | |")
            continue
        lead = rec.get("lead") or {}
        if r.kind == "exec_ttl":
            lt = "; ".join(f"{s}: {v['lead']:+.2f} [{v['lo']:+.2f}, {v['hi']:+.2f}] vs {v['next_best']}"
                           for s, v in lead.items())
        elif r.kind == "logloss" and "lo" not in lead:
            lt = f"{lead.get('lead', 0):+.5f} log loss vs {lead.get('next_best')!r}" if lead else ""
        else:
            lt = (f"{lead['lead']:+.2f} R/mo excess [{lead['lo']:+.2f}, {lead['hi']:+.2f}] vs {lead['next_best']!r}"
                  if lead else "")
        lines.append(f"| {r.id} | {r.name} | {r.start!r} | {rec['value']!r} | {lt} | "
                     f"{'; '.join(rec.get('flags') or [])} |")
    lines += ["", "Nothing is written to the configuration until the owner reads this; the winners then go into "
                  "features.yaml / settings.yaml marked `frozen_from: months 1-4`."]
    path = rep_dir(env) / "tune_summary.md"
    atomic_write_text(path, "\n".join(lines) + "\n")
    return path


# --- the job ----------------------------------------------------------------------------------------------------------

class TuneJob:
    """`nqbot run step13_tune --until HH:MM`: the step-13c program (rows 1-14), resumable chunk by chunk."""

    name = JOB
    p90_fit = False          # its chunks run child jobs that stop themselves at the same --until
    shared_locks = True      # like the builds it runs (parallel_builds): the data locks shared + its own run lock

    def __init__(self) -> None:
        from nqbot.jobs.lock import DATABENTO, SKYLIT

        self.locks = (SKYLIT, DATABENTO)

    def _env(self, ctx) -> Env:
        return s13b._job_env(ctx)

    def chunks(self, ctx) -> list[str]:
        env = self._env(ctx)
        if not fbj.parallel_on():
            raise TuneRefused("parallel_builds is off in config/local.yaml (the driver needs it, owner 2026-11-14)")
        return load_plan(env)["chunks"]

    def run_chunk(self, chunk_id: str, ctx) -> dict | None:
        from nqbot.jobs.runner import StopChunk

        env = self._env(ctx)
        plan = load_plan(env)
        days = sessions(plan)
        row_id, what = chunk_id.split(":")
        try:
            if row_id == "r00":
                return self._r00(env, ctx, plan, what, days)
            if row_id == "end":
                summary(env, plan)
                return None
            row = prog.row_of(program(env), row_id)
            if what.startswith("b"):
                self._builds(env, ctx, row, int(what[1:]), days)
            elif what == "labels":
                run_pool(env, ctx, label_jobs(env, row_label_targets(env, row), days), LABEL_WORKERS)
            elif what == "score":
                score_row(env, plan, row)
            return None
        except (TuneRefused, s13b.Refused, fb.BuildRefused) as e:
            ctx.log.error("%s: %s: %s", JOB, chunk_id, e)
            raise StopChunk(f"{chunk_id}: {e}", status="failures") from None

    def _r00(self, env: Env, ctx, plan: dict, what: str, days: list[date]) -> None:
        if what == "relabel":
            run_pool(env, ctx, label_jobs(env, [(plan["baseline_key"], {})], days), LABEL_WORKERS)
        elif what == "check":
            check_reference(env, plan)
        elif what == "selftest":
            selftest(env, plan)
        elif what == "volfloor":
            tasks = [(f"volfloor_{d.isoformat()}", ["step13", "vol-floor-day", d.isoformat()]) for d in days
                     if not vol_day_file(env, d).exists()]
            run_pool(env, ctx, tasks, DAY_WORKERS)
            volfloor_fit(env, plan)

    def _builds(self, env: Env, ctx, row: prog.Row, n: int, days: list[date]) -> None:
        from nqbot.jobs.runner import StopChunk

        base = build_over(env, row.id)
        todo = []
        for v in row.batches()[n - 1]:
            over = {**base, **row.build_overrides(v)}
            key = key_for(env, over)
            if not build_complete(env, key, days):
                todo.append((over, key))
        if not todo:
            return
        for slot, (over, key) in enumerate(todo, start=1):
            e = Env(settings=env.settings, features=dict(env.features), paths=env.paths, log=env.log, echo=env.echo,
                    cal=env.cal)
            fbj.plan_slot(e, list(MONTHS_14), None, over, slot)
            env.echo(f"{JOB}: {row.id} {row.name}: build {key} in slot {slot} ({over or 'no overrides'})")
        res = run_jobs(env, ctx, [fbj.job_name(s) for s in range(1, len(todo) + 1)])
        bad = {j: r for j, r in res.items() if r.status != "complete"}
        if bad:
            statuses = {r.status for r in bad.values()}
            status = next((s for s in ("stopped", "ssd_missing", "low_space", "budget_stall", "until", "lock_window")
                           if s in statuses),
                          "failures")
            raise StopChunk("; ".join(f"{j}: {r.status} {r.message}" for j, r in bad.items()),
                            status=None if status == "until" else status)
        for _, key in todo:
            if not build_complete(env, key, days):
                raise StopChunk(f"build {key} reported complete but has sessions without pass 2", status="failures")
