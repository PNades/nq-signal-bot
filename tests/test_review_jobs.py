"""Owner 2026-11-13: the independent review of the job side before 13c (two builds at once, the pass-2 pool, stop and
resume, the build pin). One test or more per finding; all of it is outside every build key."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import psutil
import pytest

from nqbot.checks import feature_build as fb
from nqbot.checks import feature_build_job as fbj
from nqbot.jobs import queue
from nqbot.jobs.lock import ALL_LOCKS, LockHeld, JobLock, job_locks, lock_path, take_stale, SKYLIT
from nqbot.jobs.runner import ET, StopChunk, pool_progress_path, run_lock_path, state_path
from nqbot.storage import atomic_write_json
from tests.test_feature_build import _no_engine_stage, _pool_setup, _SlowProc, env  # noqa: F401  (fixtures)

DEAD_PID = 2**22 + 12345


def _sleeper(code: str = "import time; time.sleep(60)") -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", code])


def _gone(pid: int, wait: float = 10.0) -> bool:
    t0 = time.monotonic()
    while time.monotonic() - t0 < wait:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.1)
    return False


# --- 1. nqbot stop finds a build holding only shared data locks and its run lock ------------------------------------

def test_stop_finds_a_parallel_build(paths):
    from nqbot import cli
    from nqbot.jobs.runner import stop_request_path

    paths.root.mkdir(parents=True, exist_ok=True)
    args = SimpleNamespace(job="feature_build")
    assert cli._stop(args, paths) == cli.EXIT_REFUSED                         # not running
    run_lock_path(paths, "feature_build").parent.mkdir(parents=True, exist_ok=True)
    with JobLock(run_lock_path(paths, "feature_build"), "feature_build"), \
            job_locks(paths.root, ALL_LOCKS, "feature_build", shared=True):
        assert not lock_path(paths.root, SKYLIT).exists()                    # no exclusive lock at all
        assert cli._stop(args, paths) == 0
        assert stop_request_path(paths, "feature_build").exists()


# --- 2. the memory check reads the session, not the venv launcher ----------------------------------------------------

def test_grown_gb_reads_the_process_tree():
    p = _sleeper("x = bytearray(300 * 2**20); import time; time.sleep(30)")
    try:
        t0 = time.monotonic()
        while fbj._grown_gb(p) < 0.25 and time.monotonic() - t0 < 20:
            time.sleep(0.2)
        assert fbj._grown_gb(p) >= 0.25   # the 300 MB live in the interpreter (a launcher's child on a uv venv)
    finally:
        fbj.kill_tree(p.pid)
    assert _gone(p.pid)
    assert fbj._grown_gb(SimpleNamespace(pid=DEAD_PID)) == 0.0
    fbj.kill_tree(None)          # never this process
    fbj.kill_tree(os.getpid())


# --- 3. sessions are ended when the pool fails, and a dead run's sessions are ended first ----------------------------

def test_pool_ends_its_sessions_on_an_exception(env, monkeypatch):
    doc, plan_days, job, ctx, ids, secs = _pool_setup(env, monkeypatch, batch=2)
    started = []

    def child(argv, log):
        log.parent.mkdir(parents=True, exist_ok=True)
        p = _sleeper()
        started.append(p.pid)
        return p
    monkeypatch.setattr(fbj, "_child", child)
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        if calls["n"] > 2:
            raise RuntimeError("SSD gone mid-write")
        return None
    with pytest.raises(RuntimeError):
        job._pool(env, doc["key"], plan_days, 2, 2, SimpleNamespace(**{**vars(ctx), "should_stop": boom}))
    assert len(started) == 2 and all(_gone(pid) for pid in started)


def test_kill_orphans_ends_only_this_roots_and_slots_sessions(tmp_path):
    mine = fbj.session_tag(tmp_path / "a", 1)
    other_root, other_slot = fbj.session_tag(tmp_path / "b", 1), fbj.session_tag(tmp_path / "a", 2)
    ps = {t: _sleeper(f"import time; time.sleep(60)  # {t}") for t in (mine, other_root, other_slot)}
    try:
        time.sleep(0.5)
        ended = fbj.kill_orphans(tmp_path / "a", 1)
        assert ps[mine].pid in ended and _gone(ps[mine].pid)
        assert ps[other_root].poll() is None and ps[other_slot].poll() is None
    finally:
        for p in ps.values():
            fbj.kill_tree(p.pid)


def test_children_carry_the_pools_tag(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(fbj.subprocess, "Popen", lambda argv, **kw: seen.setdefault("argv", argv))
    fbj._TAG["tag"] = fbj.session_tag(tmp_path, 2)
    try:
        fbj._child(["features", "build-day", "2025-10-01", "--pass", "2"], tmp_path / "x.txt")
    finally:
        fbj._TAG["tag"] = ""
    assert seen["argv"][2].endswith(f"  # {fbj.session_tag(tmp_path, 2)}")


# --- 4. a failure with the SSD gone resumes by itself ----------------------------------------------------------------

def test_pass2_failure_with_the_ssd_gone_is_ssd_missing(env, monkeypatch):
    doc, plan_days, job, ctx, ids, secs = _pool_setup(env, monkeypatch, batch=2)
    seen = {"running": set(), "started": [], "ended": [], "max": 0}
    bad = set(plan_days)
    monkeypatch.setattr(fbj, "_child", lambda argv, log: _SlowProc(env, argv, log, 1, seen, bad))
    gone = SimpleNamespace(**{**vars(ctx), "paths": SimpleNamespace(**{**vars(env.paths), "mounted": lambda: False})})
    with pytest.raises(StopChunk) as ex:
        job._pool(env, doc["key"], plan_days, 2, 2, gone)
    assert ex.value.status == "ssd_missing"


# --- 5. a long pass 2 cut by restarts while sessions finish isn't held as crashing -----------------------------------

def test_pool_sessions_count_as_progress_for_resume(paths):
    job = "feature_build"
    atomic_write_json(state_path(paths, job), {"done": {"p1": {"sec": 1}}, "total": 3, "chunks": ["p0", "p1", "p2"],
                                               "runs": [{"start": "x"}]})
    rs = {"attempts": {job: {"done_before": 1, "crashes": 2, "outcome": None}}, "held": {}}
    assert queue.crash_count(job, queue.job_state(paths, job), rs) == 3            # no progress: a crash
    atomic_write_json(pool_progress_path(paths, job), {"finished_total": 4})
    assert queue.crash_count(job, queue.job_state(paths, job), rs) == 0            # sessions finished: progress
    assert queue.progress(queue.job_state(paths, job)) == 5


def test_pool_records_its_progress(env, monkeypatch):
    doc, plan_days, job, ctx, ids, secs = _pool_setup(env, monkeypatch, batch=3)
    seen = {"running": set(), "started": [], "ended": [], "max": 0}
    monkeypatch.setattr(fbj, "_child", lambda argv, log: _SlowProc(env, argv, log, 2, seen))
    before = fbj.read_progress(env.paths, job.name).get("finished_total") or 0
    job.run_chunk(ids[-1], ctx)
    pr = fbj.read_progress(env.paths, job.name)
    assert pr["pass"] == 2 and pr["pass_done"] == pr["pass_total"] == len(plan_days)
    assert pr["finished_total"] == before + len(plan_days) and len(pr["session_sec"]) == len(plan_days)


# --- 6. the queue shows a live run as running whatever its previous run's end -----------------------------------------

def test_queue_shows_running_even_when_the_last_recorded_run_ended(paths, settings):
    paths.root.mkdir(parents=True, exist_ok=True)
    queue.write_queue(paths, ["dummy"])
    atomic_write_json(state_path(paths, "dummy"), {"done": {}, "total": 5, "runs": [{"start": "x", "status": "until"}]})
    with job_locks(paths.root, ALL_LOCKS, "dummy"):
        line = next(x for x in queue.queue_lines(paths, settings) if x.startswith("  dummy:"))
        assert line.startswith("  dummy: running now (pid ")
    assert "dummy: run (last run ended 'until')" in "\n".join(queue.queue_lines(paths, settings))


# --- 7. a pass-2 session starts only if its p90 time fits before --until -----------------------------------------------

def test_pass2_session_starts_only_if_it_fits_before_until(env, monkeypatch):
    doc, plan_days, job, ctx, ids, secs = _pool_setup(env, monkeypatch, batch=2)
    atomic_write_json(pool_progress_path(env.paths, job.name), {"finished_total": 0, "session_sec": [600.0]})
    seen = {"running": set(), "started": [], "ended": [], "max": 0}
    monkeypatch.setattr(fbj, "_child", lambda argv, log: _SlowProc(env, argv, log, 1, seen))
    soon = SimpleNamespace(**{**vars(ctx), "deadline": datetime.now(ET) + timedelta(seconds=60)})
    with pytest.raises(StopChunk, match="would not finish before"):
        job.run_chunk(ids[-1], soon)
    assert seen["started"] == []
    later = SimpleNamespace(**{**vars(ctx), "deadline": datetime.now(ET) + timedelta(hours=2)})
    job.run_chunk(ids[-1], later)
    assert len(seen["started"]) == len(plan_days)


# --- the budget mutex timing out drains the running sessions (no orphans) ----------------------------------------------

def test_budget_timeout_lets_running_sessions_finish(env, monkeypatch):
    from contextlib import contextmanager

    doc, plan_days, job, ctx, ids, secs = _pool_setup(env, monkeypatch, batch=2)
    job.parallel = True
    seen = {"running": set(), "started": [], "ended": [], "max": 0}
    monkeypatch.setattr(fbj, "_child", lambda argv, log: _SlowProc(env, argv, log, 3, seen))
    monkeypatch.setattr(fbj, "machine_cap", lambda s: 2)
    calls = {"n": 0}

    @contextmanager
    def mutex(paths):
        calls["n"] += 1
        if calls["n"] > 1:
            raise StopChunk("the build budget stayed locked for 60 s", status=fbj.BUDGET_STALL)
        yield
    monkeypatch.setattr(fbj, "budget_mutex", mutex)
    with pytest.raises(StopChunk, match="budget stayed locked") as ex:
        job.run_chunk(ids[-1], ctx)
    # owner 2026-11-15: the stall keeps its own status through the pool (it was turned into a plain `until`)
    assert ex.value.status == fbj.BUDGET_STALL                 # a clean stop: resumed by itself
    assert len(seen["started"]) == 1 and seen["ended"] == seen["started"]


# --- 12. with two builds, pass 1 waits for memory the other build's pass 2 holds ---------------------------------------

def test_pass1_waits_for_the_other_builds_memory(env, monkeypatch):
    from nqbot.checks import step8b_disloc

    job = fbj.FeatureBuildJob(parallel=True)
    free = iter([1024, 1024, 64 * 1024])
    monkeypatch.setattr(step8b_disloc, "free_mb", lambda: next(free))
    monkeypatch.setattr(fbj, "others", lambda paths, name: [{"children": [123]}])
    ctx = SimpleNamespace(paths=env.paths, should_stop=lambda: None, sleep=lambda s: None)
    job._wait_for_memory(ctx, "2025-10-01 (pass 1)")                    # waited twice, then went on
    monkeypatch.setattr(step8b_disloc, "free_mb", lambda: 1024)
    monkeypatch.setattr(fbj, "others", lambda paths, name: [])
    with pytest.raises(StopChunk, match="MB free before"):              # nothing elsewhere to wait for
        job._wait_for_memory(ctx, "2025-10-01 (pass 1)")
    monkeypatch.setattr(fbj, "others", lambda paths, name: [{"children": [123]}])
    with pytest.raises(StopChunk, match="07:30"):                       # waiting still honours --until / a stop
        job._wait_for_memory(SimpleNamespace(**{**vars(ctx), "should_stop": lambda: "reached 07:30"}), "x")


# --- 9. a dead lock is taken over by one process only ----------------------------------------------------------------

def test_take_stale_never_removes_a_lock_someone_just_took(tmp_path):
    path = tmp_path / ".nqbot_skylit.lock"
    dead = {"host": socket.gethostname(), "pid": DEAD_PID, "pid_created": 1.0, "job": "x", "started_utc": "t0"}
    path.write_text(json.dumps(dead), encoding="utf-8")
    take_stale(path, dead)
    assert not path.exists()
    # B read the dead holder before A took the lock over: B's take-over must leave A's new lock alone
    live = {"host": socket.gethostname(), "pid": os.getpid(), "pid_created": 2.0, "job": "a", "started_utc": "t1"}
    path.write_text(json.dumps(live), encoding="utf-8")
    take_stale(path, dead)
    assert json.loads(path.read_text(encoding="utf-8")) == live
    assert [p.name for p in tmp_path.iterdir()] == [path.name]          # nothing left behind


def test_two_acquirers_of_a_dead_lock_one_wins(tmp_path):
    path = tmp_path / "run.lock"
    path.write_text(json.dumps({"host": socket.gethostname(), "pid": DEAD_PID, "pid_created": 1.0, "job": "x"}),
                    encoding="utf-8")
    a = JobLock(path, "feature_build")
    a.acquire()
    try:
        with pytest.raises(LockHeld):
            JobLock(path, "feature_build").acquire()
    finally:
        a.release()


# --- 10. no job starts while the pin moves ---------------------------------------------------------------------------

def test_pin_move_blocks_jobs_until_it_ends(paths):
    from nqbot import build_pin

    paths.root.mkdir(parents=True, exist_ok=True)
    (paths.data_dir / "jobs").mkdir(parents=True, exist_ok=True)
    build_pin.begin_move(paths, os.getpid(), echo=lambda *_: None)
    v = build_pin.check(paths)
    assert not v.ok and "being moved" in v.message
    build_pin.end_move(paths)
    assert build_pin.check(paths).ok
    # a dead script's record is ignored
    atomic_write_json(build_pin.move_path(paths), {"host": socket.gethostname(), "pid": DEAD_PID, "pid_created": 1.0})
    assert build_pin.moving(paths) is None and build_pin.check(paths).ok
    build_pin.end_move(paths)
    # a running job refuses the move and leaves no record
    with job_locks(paths.root, ALL_LOCKS, "dummy"):
        with pytest.raises(build_pin.PinRefused, match="a job is running"):
            build_pin.begin_move(paths, os.getpid(), echo=lambda *_: None)
    assert not build_pin.move_path(paths).exists()


def test_resume_checks_the_pin_before_each_job(paths, monkeypatch):
    from nqbot import build_pin, cli

    (paths.data_dir / "jobs").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cli, "_head_or_none", lambda: "b" * 40)
    cli._resume_may_start(paths, "b" * 40)                                # same commit, no move: fine
    with pytest.raises(LockHeld, match="pin moved"):
        cli._resume_may_start(paths, "a" * 40)                            # the worktree moved under this resume
    build_pin.begin_move(paths, os.getpid(), echo=lambda *_: None)
    try:
        with pytest.raises(LockHeld):
            cli._resume_may_start(paths, "b" * 40)
    finally:
        build_pin.end_move(paths)


def test_worktree_script_holds_the_move_to_the_end():
    from pathlib import Path

    s = (Path(__file__).resolve().parents[1] / "scripts" / "build_worktree.ps1").read_text(encoding="utf-8")
    i_begin, i_co, i_set, i_end = (s.index(x) for x in ("build-pin move-begin --pid $PID", "checkout --detach $sha }",
                                                       "build-pin set }", "build-pin move-end"))
    assert i_begin < i_co < i_set < i_end and "can-move" not in s.split("$DryRun")[-1]


# --- 11. resume's child output on the SSD ------------------------------------------------------------------------------

def test_run_many_for_resume_writes_child_output_under_logs(log, tmp_path):
    from nqbot import cli

    class FakeP:
        def __init__(self, argv, stdout, stderr, env):
            stdout.write("feature_build: complete (3/3 chunks done)\n")
            self.returncode, self.pid = 0, 1

        def poll(self):
            return self.returncode
    res = cli._run_many_for_resume(["feature_build"], "07:30", log, popen=FakeP, poll_sec=0, out_dir=tmp_path / "logs")
    assert res["feature_build"].status == "complete"
    assert [p.name.startswith("resume_feature_build_") for p in (tmp_path / "logs").iterdir()] == [True]


# --- 8 and the pass-2 progress in nqbot status ---------------------------------------------------------------------------

def test_status_shows_pass2_sessions_and_a_differing_worktree_switch(paths, monkeypatch, tmp_path):
    from nqbot import build_pin, config
    from nqbot.jobs.status import job_lines, switch_lines

    atomic_write_json(state_path(paths, "feature_build"), {"done": {"p0": {"sec": 10}}, "total": 2,
                                                           "chunks": ["p0", "p2"], "runs": []})
    atomic_write_json(pool_progress_path(paths, "feature_build"), {"pass": 2, "pass_done": 17, "pass_total": 23})
    assert "pass 2: 17/23 sessions done" in job_lines(paths)[0]
    wt = tmp_path / "wt"
    (wt / "config").mkdir(parents=True)
    (wt / "config" / "local.yaml").write_text("parallel_builds: true\n", encoding="utf-8")
    atomic_write_json(build_pin.pin_path(paths), {"path": str(wt), "commit": "c" * 40, "host": socket.gethostname()})
    lines = switch_lines(paths)                                  # this folder reads off (the test fixture)
    assert lines[0].endswith(": off") and "WARNING: builds read the build worktree's copy" in lines[1]
    assert lines[1].rstrip().endswith("on (moving the pin copies this folder's over it)")
    monkeypatch.setattr(config, "parallel_builds", lambda config_dir=None: True)
    assert len(switch_lines(paths)) == 1                         # the same value: no warning
