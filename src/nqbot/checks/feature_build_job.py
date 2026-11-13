"""The feature build's scheduling (owner 2026-11-12, SPEC §13 Q-step13b (f)): the jobs `feature_build` and
`feature_build_2` (`nqbot run feature_build[_2] --until HH:MM`), their chunks, the child processes, the pass-2 pool,
two builds at once and the build report.

Outside the build key (not in `feature_build.CODE_PARTS`): nothing here changes what a session writes. Every session
is built in its own process by `nqbot features build-day D --pass N` (`feature_build.build_day_cmd`, inside the key)
from its checkpoint, so this module only decides when a session runs and how many run at once.

- `p0` the warm-up, `p1:<date>` pass 1 one session at a time in order (each starts from the previous one's state);
- pass 2 is one chunk (`p2:<first>~<last>`): a rolling pool of up to the plan's `batch` sessions at once (as memory
  allows), the next session starting as soon as any finishes, longest first by its pass-1 build time (the old fixed
  batches waited for each batch's slowest session). A stop request, `--until` or a failing session stops new starts;
  the sessions running then finish, and the chunk re-runs next time with only the sessions not done.

Two builds at once (owner 2026-11-12, Q-step13b (f) (2)): build slot 1 is the job `feature_build` with its plan in
`<data_dir>/jobs/feature_build/` (as always), slot 2 the job `feature_build_2` with its own plan, state and queue
entry (`nqbot features plan --months 1-4 --set k=v --slot 2`). Two slots never plan or run the same build key, so each
writes its own output folder. They run at the same time only while `parallel_builds: true` is in this machine's
config/local.yaml (switched on only with the owner's OK; read at the start of each run, never part of a build key;
off, the two run one after the other like any two jobs). Then:
- a build holds the Skylit and Databento locks shared (other builds may hold them too; a job that writes the stored
  data still takes them alone and waits for the builds), plus its own run lock (`<data_dir>/jobs/<job>/run.lock`: one
  run per job);
- one machine-wide budget: all builds' sessions together stay within `max_workers` (all cores but 2), the memory
  headroom counts what every build's running sessions may still grow by, and each build that wants sessions keeps its
  fair share (the cores ÷ the builds wanting) free of the others, so one build's pass 1 (one session at a time) isn't
  starved by the other's pass-2 pool. Each build publishes its running sessions in `<data_dir>/jobs/build_budget/`,
  and every start is decided under that folder's mutex;
- `nqbot resume` runs the first two queued builds at once, each in its own `nqbot run` process.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time as _time
from contextlib import contextmanager, nullcontext
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

from nqbot import calendar as cal_mod
from nqbot import months as months_mod
from nqbot.checks import feature_build as fb
from nqbot.checks.step8b import Env
from nqbot.storage import atomic_write_json, atomic_write_text

JOB = fb.JOB
SLOTS = (1, 2)           # owner 2026-11-12: at most two builds at once
PEAK_TAG = "CHILD_PEAK_GB"
EST_PEAK_GB = 4.5        # assumed per-session peak before one is measured (4.29 GB, `step11 build-day 2026-03-20`)
MIN_FREE_GB_EXTRA = 4.0  # headroom kept free besides the children (as runner.RAM_RESERVE_GB)
POLL_SEC = 2.0           # how often the pass-2 pool looks at its sessions
BUDGET_DIR = "build_budget"
BUDGET_MUTEX = ".mutex"
BUDGET_WAIT_SEC = 60.0   # longest wait for the budget mutex (held only while one start is decided) before a clean stop
BUDGET_RETRY_SEC = 0.05
GB = 2**30


# --- slots -------------------------------------------------------------------------------------------------------

def job_name(slot: int) -> str:
    if slot not in SLOTS:
        raise fb.BuildRefused(f"build slot {slot}: the slots are {', '.join(map(str, SLOTS))}")
    return JOB if slot == 1 else f"{JOB}_{slot}"


def slot_of(job: str) -> int | None:
    """The build slot a job name runs, None if it isn't a feature build."""
    for s in SLOTS:
        if job == job_name(s):
            return s
    return None


def parallel_on() -> bool:
    """`parallel_builds` in this machine's config/local.yaml (default false; never in the settings or a build key)."""
    from nqbot.config import parallel_builds

    return parallel_builds()


@contextmanager
def using_slot(slot: int) -> Iterator[None]:
    """Point `feature_build`'s plan file (its folder is named by `feature_build.JOB`) at this slot's for the block.
    Process-local: a process serves one slot at a time (each `nqbot run`, each child session and `features plan` is
    its own process; `nqbot resume` runs two builds at once in two processes)."""
    name = job_name(slot)
    old = fb.JOB
    fb.JOB = name
    try:
        yield
    finally:
        fb.JOB = old


def plan_file(paths, slot: int) -> Path:
    return paths.data_dir / "jobs" / job_name(slot) / "plan.json"


def load_plan(env: Env, slot: int = 1) -> dict[str, Any]:
    with using_slot(slot):
        return fb.load_plan(env)


def running_holder(paths, job: str) -> dict | None:
    """Who is running `job` now (a live process on this machine), else None: its run lock (a build with the shared
    locks), or a data lock it holds alone."""
    from nqbot.jobs import lock
    from nqbot.jobs.runner import run_lock_path

    h = lock.read_lock(run_lock_path(paths, job))
    if h is not None and lock.holder_alive(h):
        return h
    for kind in lock.ALL_LOCKS:
        h = lock.read_lock(lock.lock_path(paths.root, kind))
        if h is not None and h.get("job") == job and lock.holder_alive(h):
            return h
    return None


def _plan_key(paths, slot: int) -> str | None:
    p = plan_file(paths, slot)
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("key") if p.exists() else None
    except (OSError, ValueError):
        return None


def plan_slot(env: Env, months: list[int], batch: int | None = None, overrides: dict[str, Any] | None = None,
              slot: int = 1) -> dict[str, Any]:
    """`nqbot features plan ... [--slot N]`: `feature_build.make_plan` for that slot. Refused while that slot's build
    runs (its sessions read the plan), and when another slot's unfinished plan has the same build key (both would
    write the same folder)."""
    from nqbot.jobs.queue import is_complete
    from nqbot.jobs.runner import load_state, state_path

    name = job_name(slot)
    h = running_holder(env.paths, name)
    if h is not None:
        raise fb.BuildRefused(f"{name} is running (pid {h.get('pid')}): its sessions read its plan, so re-plan it only "
                              f"between runs (uv run nqbot stop {name})")
    probe = Env(settings=env.settings, features=fb.apply_overrides(env.features, dict(overrides or {})),
                paths=env.paths, log=env.log, echo=env.echo, cal=env.cal)
    key = fb.build_key(probe)
    for other in SLOTS:
        if other != slot and _plan_key(env.paths, other) == key \
                and not is_complete(load_state(state_path(env.paths, job_name(other)))):
            raise fb.BuildRefused(f"build slot {other} ({job_name(other)}) already plans build key {key} and isn't "
                                  "complete: two slots never build the same key")
    with using_slot(slot):
        return fb.make_plan(env, months, batch, overrides)


# --- chunks and children -------------------------------------------------------------------------------------------

def chunk_ids(doc: dict[str, Any]) -> list[str]:
    """`<pass>:<what>:<key>:<first session>`: the build key and the chain's first session (its warm-up root) name
    every chunk, and the pass-2 chunk names the plan's first and last session, so a re-plan (other months) never
    inherits a chunk finished for another chain or another set of days."""
    key = doc["key"]
    days = doc["sessions"]
    root = days[0]
    return ([f"p0:warmup:{key}:{root}"] + [f"p1:{d}:{key}:{root}" for d in days]
            + [f"p2:{days[0]}~{days[-1]}:{key}:{root}"])


def _child(argv: list[str], log: Path) -> subprocess.Popen:
    """One session in its own process; its whole output goes to `log` (a file, not a pipe: several children run at
    once and a full pipe buffer would stall one until it is read)."""
    code = ("import sys, psutil; from nqbot.cli import main; rc = main(sys.argv[1:]); i = psutil.Process().memory_info(); "
            f"print('{PEAK_TAG}', getattr(i, 'peak_wset', 0) / 2**30 or i.rss / 2**30, flush=True); sys.exit(rc)")
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w", encoding="utf-8") as fh:   # the child keeps its own handle; ours closes at once
        return subprocess.Popen([sys.executable, "-c", code, *argv], stdout=fh, stderr=subprocess.STDOUT,
                                env={**os.environ, "PYTHONUTF8": "1"})


def _out(log: Path) -> str:
    return log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""


def _peak(stdout: str) -> float | None:
    for line in reversed((stdout or "").splitlines()):
        if line.startswith(PEAK_TAG):
            return round(float(line.split()[1]), 3)
    return None


def _grown_gb(p) -> float:
    """How far a running session has already grown (its peak working set so far, GB); 0 if it can't be read."""
    import psutil

    try:
        i = psutil.Process(p.pid).memory_info()
    except (AttributeError, TypeError, ValueError, psutil.Error, OSError):
        return 0.0
    return (getattr(i, "peak_wset", 0) or i.rss) / GB


def headroom_gb(free_mb: float | None, running: list, per: float, others: list | tuple = ()) -> float | None:
    """Memory left for one more session: free memory, less the reserve, less what each running session may still
    grow by to its expected peak `per` (a session starts small and reaches its peak later); `others` = the other
    builds' running sessions as (session, that build's expected peak). None = unknown."""
    if free_mb is None:
        return None
    grow = sum(max(0.0, per - _grown_gb(p)) for p in running) + sum(max(0.0, po - _grown_gb(q)) for q, po in others)
    return free_mb / 1024 - MIN_FREE_GB_EXTRA - grow


def machine_cap(settings: dict[str, Any]) -> int:
    """Sessions at once on this machine, every build together: `max_workers` (config/local.yaml), else all cores but
    2 (SPEC §11; memory is checked at every start)."""
    from nqbot.jobs.runner import SPARE_CORES

    return int(settings.get("max_workers") or max(1, (os.cpu_count() or 1) - SPARE_CORES))


def may_start(mine: int, others: list[dict], machine: int) -> bool:
    """One more session for this build, machine-wide (owner 2026-11-12): every build's sessions together stay below
    `machine`, and each other build that wants sessions keeps its fair share (`machine` ÷ the builds wanting, this one
    included) free of this one. With fewer places than builds the share is 0: first come, first served."""
    k = 1 + sum(1 for o in others if int(o.get("want") or 0) > 0)
    share = machine // k
    used = mine + sum(len(o.get("children") or []) for o in others)
    reserve = sum(max(0, min(int(o.get("want") or 0), share) - len(o.get("children") or [])) for o in others)
    return used + reserve < machine


def _measured_peak(env: Env, key: str, job: str = JOB) -> float | None:
    from nqbot.jobs.runner import load_state, state_path

    st = load_state(state_path(env.paths, job))
    vals = [v.get("child_peak_gb") or v.get("peak_gb") for c, v in (st.get("done") or {}).items()
            if f":{key}:" in c and c.startswith("p2")]
    vals = [v for v in vals if v]
    return max(vals) if vals else None


def _pass1_sec(env: Env, key: str, day: date) -> float:
    try:
        x = json.loads(fb.summary_file(env, key, day).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0.0
    return float((x.get("pass1") or {}).get("build_sec") or 0.0)


# --- the machine-wide budget (two builds at once) -------------------------------------------------------------------

def budget_dir(paths) -> Path:
    return paths.data_dir / "jobs" / BUDGET_DIR


def publish(paths, job: str, running: list, per: float, want: int) -> None:
    """This build's running sessions (pids), their expected peak and how many it wants (running + waiting)."""
    atomic_write_json(budget_dir(paths) / f"{job}.json",
                      {"host": socket.gethostname(), "pid": os.getpid(), "job": job, "per": per, "want": int(want),
                       "children": [getattr(p, "pid", None) for p in running]})


def others(paths, job: str) -> list[dict]:
    """The other builds running now on this machine: a published file whose process still holds that build's run
    lock (a finished run's file is ignored; it is rewritten by that build's next run)."""
    from nqbot.jobs import lock
    from nqbot.jobs.runner import run_lock_path

    d = budget_dir(paths)
    out = []
    for f in sorted(d.glob("*.json")) if d.exists() else []:
        if f.stem == job:
            continue
        try:
            x = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        h = lock.read_lock(run_lock_path(paths, x.get("job") or f.stem))
        if x.get("host") == socket.gethostname() and h and lock.holder_alive(h) and h.get("pid") == x.get("pid"):
            out.append(x)
    return out


@contextmanager
def budget_mutex(paths) -> Iterator[None]:
    """Held only while one start is decided and made, so two builds never both take the last free place."""
    from nqbot.jobs.lock import JobLock, LockHeld
    from nqbot.jobs.runner import StopChunk

    path = budget_dir(paths) / BUDGET_MUTEX
    path.parent.mkdir(parents=True, exist_ok=True)
    lk = JobLock(path, "build budget")
    t0 = _time.monotonic()
    while True:
        try:
            lk.acquire()
            break
        except LockHeld as e:
            if _time.monotonic() - t0 > BUDGET_WAIT_SEC:
                raise StopChunk(f"the build budget stayed locked for {BUDGET_WAIT_SEC:.0f} s: {e}") from None
            _time.sleep(BUDGET_RETRY_SEC)
    try:
        yield
    finally:
        lk.release()


# --- the job -------------------------------------------------------------------------------------------------------

class FeatureBuildJob:
    """`nqbot run feature_build[_2] --until HH:MM`: the two passes, one session per process (low memory)."""

    def __init__(self, slot: int = 1, parallel: bool | None = None):
        from nqbot.jobs.lock import DATABENTO, SKYLIT

        self.slot = slot
        self.name = job_name(slot)
        self.locks = (SKYLIT, DATABENTO)   # nothing may write the stored trades or boards it reads
        # two builds at once (owner 2026-11-12): read once per run, so switching it between runs changes no key
        self.parallel = parallel_on() if parallel is None else bool(parallel)
        self.shared_locks = self.parallel  # the runner then also takes this job's own run lock

    def _env(self, ctx) -> Env:
        from nqbot.config import load_features

        return Env(settings=ctx.settings, features=load_features(), paths=ctx.paths, log=ctx.log, echo=ctx.log.info,
                   cal=cal_mod.load())

    def _argv(self, argv: list[str]) -> list[str]:
        return argv + (["--slot", str(self.slot)] if self.slot != 1 else [])

    def chunks(self, ctx) -> list[str]:
        from nqbot.jobs.lock import LockHeld
        from nqbot.jobs.runner import run_lock_path

        doc = load_plan(self._env(ctx), self.slot)
        for other in SLOTS:   # never two runs of one key at once (`plan_slot` refuses it; this also covers a hand edit)
            if other == self.slot or _plan_key(ctx.paths, other) != doc["key"]:
                continue
            h = running_holder(ctx.paths, job_name(other))
            if h is not None:
                raise LockHeld(run_lock_path(ctx.paths, job_name(other)), h)
        return chunk_ids(doc)

    def run_chunk(self, chunk_id: str, ctx) -> dict | None:
        from nqbot.checks.step8b_disloc import free_mb
        from nqbot.jobs.runner import StopChunk

        env = self._env(ctx)
        doc = load_plan(env, self.slot)
        kind, which, key, _root = chunk_id.split(":")
        days = [date.fromisoformat(x) for x in doc["sessions"]]
        if kind == "p0":
            if fb.warmup_file(env, key, days[0]).exists():
                return None
            mb = free_mb()
            if mb is not None and mb < (EST_PEAK_GB + MIN_FREE_GB_EXTRA) * 1024:
                raise StopChunk(f"{mb} MB free before the warm-up")
            return {"peak_gb": self._pool(env, key, ["warmup"], 0, 1, ctx)}
        if kind == "p1":
            d = date.fromisoformat(which)
            if fb.pass1_done(env, key, days, d):
                return None
            mb = free_mb()
            if mb is not None and mb < (EST_PEAK_GB + MIN_FREE_GB_EXTRA) * 1024:
                raise StopChunk(f"{mb} MB free before {d} (pass 1)")
            return {"peak_gb": self._pool(env, key, [d], 1, 1, ctx)}
        d0, d1 = (date.fromisoformat(x) for x in which.split("~"))
        todo = [d for d in days if d0 <= d <= d1 and not fb.pass2_done(env, key, d, days)]
        if not todo:
            return None
        todo.sort(key=lambda d: (-_pass1_sec(env, key, d), d))   # longest first: the slowest never starts last
        return {"peak_gb": self._pool(env, key, todo, 2, int(doc["batch"]), ctx)}

    def _start(self, env: Env, key: str, d, pass_no: int):
        if pass_no == 0:
            log = fb.log_file(env, key, "warmup")
            return log, _child(self._argv(["features", "build-warmup"]), log)
        log = fb.log_file(env, key, f"{d.isoformat()}_p{pass_no}")
        return log, _child(self._argv(["features", "build-day", d.isoformat(), "--pass", str(pass_no)]), log)

    def _pool(self, env: Env, key: str, todo: list, pass_no: int, n: int, ctx) -> float | None:
        """Run `todo` (in this order; pass 0 = the warm-up), up to `n` sessions at once as memory allows (with
        `parallel_builds`: as the machine-wide budget allows), starting the next as soon as one finishes. A stop or a
        failure stops new starts; the running sessions finish first."""
        from nqbot.checks.step8b_disloc import free_mb
        from nqbot.jobs.runner import StopChunk

        sleep = getattr(ctx, "sleep", None) or _time.sleep
        par = self.parallel
        machine = machine_cap(ctx.settings) if par else 0
        cap = max(1, min(n, machine if par else (ctx.max_workers or n)))
        what = "the warm-up" if pass_no == 0 else f"one pass-{pass_no} session"
        queue, running = list(todo), {}
        peaks: list[float] = []
        failed, stop = [], None

        def per_gb() -> float:
            return max(EST_PEAK_GB, _measured_peak(env, key, self.name) or 0.0, max(peaks, default=0.0))

        def tell() -> None:
            if par:
                publish(env.paths, self.name, [p for _, p in running.values()], per_gb(), len(running) + len(queue))

        while queue or running:
            for d, (log, p) in list(running.items()):
                if p.poll() is None:
                    continue
                del running[d]
                out = _out(log)
                pk = _peak(out)
                if pk is not None:
                    peaks.append(pk)
                if p.returncode != 0:
                    failed.append((d, p.returncode, log, out[-600:]))
                elif pass_no == 0:
                    ctx.log.info("%s: warm-up done, process peak %s GB", self.name, pk)
                else:
                    ctx.log.info("%s: %s pass %d done, process peak %s GB", self.name, d, pass_no, pk)
            if queue and not failed and stop is None:
                stop = ctx.should_stop()
            if failed or stop is not None:
                queue = []
            tell()
            while queue and len(running) < cap:
                with budget_mutex(env.paths) if par else nullcontext():
                    rest = others(env.paths, self.name) if par else []
                    if rest and not may_start(len(running), rest, machine):
                        break   # the cores left are another build's fair share: look again at the next poll
                    per = per_gb()
                    theirs = [(SimpleNamespace(pid=c), float(o.get("per") or EST_PEAK_GB))
                              for o in rest for c in (o.get("children") or [])]
                    room = headroom_gb(free_mb(), [p for _, p in running.values()], per, theirs)
                    if room is not None and room < per:
                        if not running and not theirs:
                            raise StopChunk(f"{room + MIN_FREE_GB_EXTRA:.1f} GB free: not enough for {what} "
                                            f"({per:.1f} GB)")
                        break   # the next starts when a session finishes (this build's or another's)
                    d = queue.pop(0)
                    running[d] = self._start(env, key, d, pass_no)
                    tell()
            if running or queue:
                sleep(POLL_SEC)
            elif failed or stop is not None:
                break
        if par:   # pass 0/1: the next session follows at once, so keep one place; pass 2 is the last chunk
            publish(env.paths, self.name, [], per_gb(), 1 if pass_no < 2 and not failed and stop is None else 0)
        if failed:
            for d, rc, log, msg in failed:
                if pass_no == 0:
                    ctx.log.error("%s: warm-up failed (exit %s), log %s: %s", self.name, rc, log, msg)
                else:
                    ctx.log.error("%s: %s pass %d failed (exit %s), log %s: %s", self.name, d, pass_no, rc, log, msg)
            if pass_no == 0:
                _, rc, log, _ = failed[0]
                raise StopChunk(f"the warm-up failed (exit {rc}; log {log})", status="failures")
            raise StopChunk(f"{len(failed)} session(s) failed in pass {pass_no}: "
                            f"{', '.join(str(d) for d, *_ in failed)} (see the log)", status="failures")
        if stop is not None:
            raise StopChunk(stop)
        return max(peaks) if peaks else None

    def at_end(self, ctx) -> None:
        env = self._env(ctx)
        path = write_report(env, load_plan(env, self.slot), self.slot)
        ctx.log.info("%s: report %s", self.name, path)


# --- the report (§7 rule 2b: months 7-12 as counts only) --------------------------------------------------------

def report_file(env: Env, slot: int = 1) -> Path:
    return env.paths.reports_dir / "step11" / ("feature_build.md" if slot == 1 else f"feature_build_{slot}.md")


def write_report(env: Env, doc: dict[str, Any], slot: int = 1) -> Path:
    key = doc["key"]
    days = [date.fromisoformat(x) for x in doc["sessions"]]
    per_month: dict[int, dict] = {}
    dev_rows = []
    for d in days:
        k = months_mod.month_index(d, env.settings)
        m = per_month.setdefault(k, {"sessions": 0, "p1": 0, "p2": 0, "events": 0, "rows": 0})
        m["sessions"] += 1
        s = fb.summary_file(env, key, d)
        x = json.loads(s.read_text(encoding="utf-8")) if s.exists() else {}
        if fb.state_file(env, key, d).exists():
            m["p1"] += 1
        p2 = x.get("pass2")
        if p2:
            m["p2"] += 1
            m["events"] += p2.get("events", 0)
            m["rows"] += p2.get("rows", 0)
            if k in months_mod.DEVELOPMENT:
                dev_rows.append((d, p2))
    lines = [f"# Feature build (step 11 part 3), key {key}", "",
             f"features_version {doc['features_version']}, build version {doc['build_version']}; outputs under "
             f"`{fb.out_dir(env, key)}`. Months 7-12 are counts only (§7 rule 2b: walk-forward and sealed months appear "
             "in no report until they are scored).", "",
             "| ledger month | sessions | pass 1 done | pass 2 done | events | rows |", "|---|---|---|---|---|---|"]
    for k in sorted(per_month):
        m = per_month[k]
        lines.append(f"| {k} | {m['sessions']} | {m['p1']} | {m['p2']} | {m['events']} | {m['rows']} |")
    lines += ["", "## Development months (1-6), by session", "",
              "| session | events | tradeable | held | broke | timeout | day type | build s | total s | Skylit |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for d, p in dev_rows:
        lab = p.get("labels") or {}
        lines.append(f"| {d} | {p.get('events')} | {p.get('tradeable')} | {lab.get('held')} | {lab.get('broke')} | "
                     f"{lab.get('timeout')} | {p.get('day_type')} | {p.get('build_sec')} | {p.get('total_sec')} | "
                     f"{p.get('skylit')} |")
    path = report_file(env, slot)
    atomic_write_text(path, "\n".join(lines) + "\n")
    return path
