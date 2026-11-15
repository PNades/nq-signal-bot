"""Owner 2026-11-15: releasing a job lock while another process has the file open, and the build-budget mutex.

On 2026-11-15 the two-builds budget mutex stuck twice: Windows refuses to delete a file another process has open
(WinError 32), the waiting build opens it every 50 ms to read the holder, and `JobLock.release` swallowed the error,
so a live process kept the mutex and both builds stopped (recorded as `until`)."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from nqbot.checks import feature_build_job as fbj
from nqbot.jobs import lock
from nqbot.jobs.lock import JobLock, LockHeld, LockReleaseFailed
from nqbot.jobs.runner import StopChunk


def _hold_open(path: Path, sec: float) -> subprocess.Popen:
    """Another process with `path` open for reading (as a waiting starter has it), until `sec` have passed."""
    code = f"import sys,time; f=open({str(path)!r}); print('open', flush=True); time.sleep({sec})"
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "open"
    return p


@pytest.mark.skipif(os.name != "nt", reason="Windows refuses to delete a file another process has open")
def test_release_waits_for_a_reader_then_removes_the_lock(tmp_path):
    path = tmp_path / ".mutex"
    lk = JobLock(path, "build budget")
    lk.acquire()
    reader = _hold_open(path, 0.6)
    t0 = time.monotonic()
    try:
        lk.release()                       # refused while the reader has it open, retried, then removed
    finally:
        reader.wait()
    assert not path.exists() and time.monotonic() - t0 >= 0.3


def test_a_release_that_keeps_failing_is_logged_and_raised(tmp_path, monkeypatch, caplog):
    path = tmp_path / ".mutex"
    lk = JobLock(path, "build budget")
    lk.acquire()
    monkeypatch.setattr(lock, "RELEASE_WAIT_SEC", 0.2)
    real_unlink = Path.unlink

    def busy(self, *a, **k):
        if self == path:
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        return real_unlink(self, *a, **k)
    monkeypatch.setattr(Path, "unlink", busy)
    with pytest.raises(LockReleaseFailed, match="within"):
        lk.release()
    assert path.exists() and "could not remove our lock" in caplog.text
    monkeypatch.setattr(Path, "unlink", real_unlink)
    # the leftover is this live process's: only a reclaim_own lock (the budget mutex) takes it back
    with pytest.raises(LockHeld):
        JobLock(path, "build budget").acquire()
    again = JobLock(path, "build budget", reclaim_own=True)
    again.acquire()
    again.release()
    assert not path.exists()


def test_release_leaves_a_lock_that_is_no_longer_ours(tmp_path):
    path = tmp_path / ".mutex"
    lk = JobLock(path, "build budget")
    lk.acquire()
    other = {"host": socket.gethostname(), "pid": 4, "pid_created": 1.0, "job": "x", "started_utc": "t"}
    path.write_text(json.dumps(other), encoding="utf-8")
    lk.release()
    assert json.loads(path.read_text(encoding="utf-8")) == other


def test_reclaim_own_never_takes_a_lock_this_process_really_holds(tmp_path):
    path = tmp_path / ".mutex"
    held = JobLock(path, "build budget", reclaim_own=True)
    held.acquire()
    try:
        with pytest.raises(LockHeld):
            JobLock(path, "build budget", reclaim_own=True).acquire()
    finally:
        held.release()
    assert not path.exists()


def test_a_dead_processs_leftover_mutex_is_taken_over(paths):
    """The 2026-11-15 case: the mutex left on the SSD by a build that has since ended (pid 7784)."""
    path = fbj.budget_dir(paths) / fbj.BUDGET_MUTEX
    path.parent.mkdir(parents=True, exist_ok=True)
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    path.write_text(json.dumps({"host": socket.gethostname(), "pid": gone.pid, "pid_created": 1.0,
                                "job": "build budget", "started_utc": "2026-11-15T07:09:48+00:00"}), encoding="utf-8")
    with fbj.budget_mutex(paths):
        assert json.loads(path.read_text(encoding="utf-8"))["pid"] == os.getpid()
    assert not path.exists()


def test_budget_mutex_release_failure_stops_cleanly_and_the_next_start_takes_it_back(paths, monkeypatch):
    path = fbj.budget_dir(paths) / fbj.BUDGET_MUTEX
    monkeypatch.setattr(lock, "RELEASE_WAIT_SEC", 0.1)
    real_unlink = Path.unlink

    def busy(self, *a, **k):
        if self == path:
            raise PermissionError(32, "in use")
        return real_unlink(self, *a, **k)
    monkeypatch.setattr(Path, "unlink", busy)
    with pytest.raises(StopChunk) as e:
        with fbj.budget_mutex(paths):
            pass
    assert e.value.status == fbj.BUDGET_STALL and path.exists()
    monkeypatch.setattr(Path, "unlink", real_unlink)
    with fbj.budget_mutex(paths):          # this process's next start takes its own leftover back at once
        pass
    assert not path.exists()


def test_a_budget_mutex_held_by_another_live_process_stalls_with_its_own_status(paths, monkeypatch):
    path = fbj.budget_dir(paths) / fbj.BUDGET_MUTEX
    path.parent.mkdir(parents=True, exist_ok=True)
    import psutil

    live = os.getppid()                    # the other build: a live process that is not this one
    path.write_text(json.dumps({"host": socket.gethostname(), "pid": live,
                                "pid_created": psutil.Process(live).create_time(), "job": "build budget",
                                "started_utc": "t"}), encoding="utf-8")
    monkeypatch.setattr(fbj, "BUDGET_WAIT_SEC", 0.1)
    with pytest.raises(StopChunk, match="stayed locked") as e:
        with fbj.budget_mutex(paths):
            pass
    assert e.value.status == fbj.BUDGET_STALL
