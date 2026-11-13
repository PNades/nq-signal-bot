"""Owner 2026-11-13: `nqbot status` while two builds run. A shared lock shows as shared with its holders, never "free";
a run still going shows as running, not "died" ("died" only when its process is gone without an end state); the
`parallel_builds` switch is shown. Display only: outside every build key."""

from __future__ import annotations

import json
import os
import socket

from nqbot import config
from nqbot.jobs import queue
from nqbot.jobs.lock import ALL_LOCKS, DATABENTO, SKYLIT, JobLock, job_locks, lock_path, shared_path
from nqbot.jobs.runner import run_lock_path, state_path
from nqbot.jobs.status import lock_lines, status_lines, switch_lines
from nqbot.storage import atomic_write_json

DEAD_PID = 2**22 + 12345


def _running_state(paths, job):
    atomic_write_json(state_path(paths, job), {"done": {}, "total": 5, "chunks": [f"c{i}" for i in range(5)],
                                               "runs": [{"start": "2026-11-13T05:07:31-05:00"}]})   # no end yet


def _queue_line(paths, settings, job):
    return next(x for x in queue.queue_lines(paths, settings) if x.startswith(f"  {job}:"))


def test_shared_locks_show_their_holders_never_free(paths):
    paths.root.mkdir(parents=True, exist_ok=True)
    with job_locks(paths.root, ALL_LOCKS, "feature_build", shared=True), \
            job_locks(paths.root, ALL_LOCKS, "feature_build_2", shared=True):
        lines = lock_lines(paths)
        assert not any("free" in x for x in lines)
        assert [x.split(" (pid")[0] for x in lines] == [
            "skylit job lock: shared by job 'feature_build'", "skylit job lock: shared by job 'feature_build_2'",
            "databento job lock: shared by job 'feature_build'", "databento job lock: shared by job 'feature_build_2'"]
        assert all(f"pid {os.getpid()} on {socket.gethostname()}" in x and "stale" not in x for x in lines)
    assert lock_lines(paths) == ["skylit job lock: free", "databento job lock: free"]


def test_exclusive_and_stale_shared_holders(paths):
    paths.root.mkdir(parents=True, exist_ok=True)
    with job_locks(paths.root, (SKYLIT,), "skylit_backfill"):
        sky, db = lock_lines(paths)
        assert sky.startswith("skylit job lock: held by job 'skylit_backfill'") and db == "databento job lock: free"
    dead = shared_path(paths.root, DATABENTO, "feature_build_2")
    dead.write_text(json.dumps({"host": socket.gethostname(), "pid": DEAD_PID, "pid_created": 1.0,
                                "job": "feature_build_2", "started_utc": "t0"}), encoding="utf-8")
    sky, db = lock_lines(paths)
    assert sky == "skylit job lock: free"
    assert db.startswith("databento job lock: shared by job 'feature_build_2'")
    assert db.endswith("no longer running (stale; the next job run removes it)")


def test_a_running_build_shows_running_not_died(paths, settings):
    paths.root.mkdir(parents=True, exist_ok=True)
    queue.write_queue(paths, ["feature_build", "feature_build_2"])
    for job in ("feature_build", "feature_build_2"):
        _running_state(paths, job)
        run_lock_path(paths, job).parent.mkdir(parents=True, exist_ok=True)
    with JobLock(run_lock_path(paths, "feature_build"), "feature_build"), \
            job_locks(paths.root, ALL_LOCKS, "feature_build", shared=True), \
            JobLock(run_lock_path(paths, "feature_build_2"), "feature_build_2"), \
            job_locks(paths.root, ALL_LOCKS, "feature_build_2", shared=True):
        for job in ("feature_build", "feature_build_2"):
            line = _queue_line(paths, settings, job)
            assert line.startswith(f"  {job}: running now (pid {os.getpid()} on {socket.gethostname()}, since ")
            assert "died" not in line and "judged by its lock" not in line
    # the processes are gone and no end was recorded: died
    for job in ("feature_build", "feature_build_2"):
        assert _queue_line(paths, settings, job) == f"  {job}: run (last run ended 'died')"


def test_a_single_job_holding_the_exclusive_lock_shows_running(paths, settings):
    paths.root.mkdir(parents=True, exist_ok=True)
    queue.write_queue(paths, ["dummy"])
    _running_state(paths, "dummy")
    with job_locks(paths.root, ALL_LOCKS, "dummy"):
        assert _queue_line(paths, settings, "dummy").startswith("  dummy: running now (pid ")
    assert _queue_line(paths, settings, "dummy") == "  dummy: run (last run ended 'died')"


def test_a_dead_holder_or_another_jobs_lock_is_not_running(paths, settings):
    paths.root.mkdir(parents=True, exist_ok=True)
    queue.write_queue(paths, ["dummy"])
    _running_state(paths, "dummy")
    lock_path(paths.root, SKYLIT).write_text(json.dumps(
        {"host": socket.gethostname(), "pid": DEAD_PID, "pid_created": 1.0, "job": "dummy", "started_utc": "t0"}),
        encoding="utf-8")
    assert _queue_line(paths, settings, "dummy") == "  dummy: run (last run ended 'died')"   # process gone
    lock_path(paths.root, SKYLIT).unlink()
    with job_locks(paths.root, ALL_LOCKS, "step13_labels"):
        assert _queue_line(paths, settings, "dummy") == "  dummy: run (last run ended 'died')"   # someone else's


def test_another_machines_holder_is_taken_at_its_word(paths, settings):
    paths.root.mkdir(parents=True, exist_ok=True)
    queue.write_queue(paths, ["dummy"])
    _running_state(paths, "dummy")
    lock_path(paths.root, DATABENTO).write_text(json.dumps(
        {"host": "other-pc", "pid": 77, "job": "dummy", "started_utc": "t0"}), encoding="utf-8")
    assert _queue_line(paths, settings, "dummy") == (
        "  dummy: running now (pid 77 on other-pc, since t0): judged by its lock only (another machine's process "
        "is never checked from here)")


def test_an_ended_run_keeps_the_decision_line(paths, settings):
    """Only a run with no recorded end is checked for a live holder; an ended one shows what resume would do."""
    paths.root.mkdir(parents=True, exist_ok=True)
    queue.write_queue(paths, ["dummy"])
    atomic_write_json(state_path(paths, "dummy"), {"done": {}, "total": 5, "runs": [{"start": "x", "status": "until"}]})
    assert _queue_line(paths, settings, "dummy") == "  dummy: run (last run ended 'until')"


def test_status_shows_the_parallel_builds_switch(paths, monkeypatch):
    paths.root.mkdir(parents=True, exist_ok=True)
    line = "two builds at once (parallel_builds in config/local.yaml): "
    assert switch_lines() == [line + "off"]   # the test fixture reads this machine's switch as off
    assert line + "off" in status_lines(paths)
    monkeypatch.setattr(config, "parallel_builds", lambda config_dir=None: True)
    assert line + "on" in status_lines(paths)

    def bad(config_dir=None):
        raise config.ConfigError("parallel_builds must be true or false, found 'yes'")
    monkeypatch.setattr(config, "parallel_builds", bad)
    assert switch_lines()[0].startswith(line + "unreadable (")


def test_reads_the_real_switch_from_a_config_folder(tmp_path):
    (tmp_path / "local.yaml").write_text("parallel_builds: true\n", encoding="utf-8")
    assert config.parallel_builds(tmp_path) is True


def test_the_display_code_is_outside_the_build_key():
    """The labels key's code digest reads only labeler/*.py and clients/mbp1.py (labeler/params.py)."""
    from nqbot.checks import feature_build

    for f in ("jobs/status.py", "jobs/queue.py"):
        assert not any(f == p or f.startswith(p.rstrip("/") + "/") for p in feature_build.CODE_PARTS)
