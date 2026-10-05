import json
import os
import socket

import pytest

from nqbot.jobs.lock import JobLock, LockHeld, force_unlock


def test_exclusive(tmp_path):
    path = tmp_path / "job.lock"
    with JobLock(path, "a"):
        with pytest.raises(LockHeld, match="'a'"):
            JobLock(path, "b").acquire()
    assert not path.exists()


def test_stale_same_host_reclaimed(tmp_path):
    path = tmp_path / "job.lock"
    path.write_text(json.dumps({"host": socket.gethostname(), "pid": 2**22 + 12345, "job": "old"}))
    with JobLock(path, "new"):
        assert json.loads(path.read_text())["job"] == "new"


def test_other_host_never_broken(tmp_path):
    path = tmp_path / "job.lock"
    path.write_text(json.dumps({"host": "home-pc", "pid": 1, "job": "fold", "started_utc": "x"}))
    with pytest.raises(LockHeld, match="home-pc"):
        JobLock(path, "new").acquire()
    assert json.loads(path.read_text())["host"] == "home-pc"


def test_live_pid_same_host_held(tmp_path):
    path = tmp_path / "job.lock"
    path.write_text(json.dumps({"host": socket.gethostname(), "pid": os.getpid(), "job": "busy"}))
    with pytest.raises(LockHeld):
        JobLock(path, "new").acquire()


def test_reused_pid_is_stale(tmp_path):
    # Windows reuses pids: our own (live) pid with a different start time is a crashed job's old lock.
    path = tmp_path / "job.lock"
    path.write_text(json.dumps({"host": socket.gethostname(), "pid": os.getpid(), "pid_created": 1.0, "job": "old"}))
    with JobLock(path, "new"):
        assert json.loads(path.read_text())["job"] == "new"


def test_lock_held_message_points_to_unlock(tmp_path):
    path = tmp_path / "job.lock"
    path.write_text(json.dumps({"host": "home-pc", "pid": 1, "job": "fold"}))
    with pytest.raises(LockHeld, match="nqbot unlock"):
        JobLock(path, "new").acquire()


def test_force_unlock(tmp_path):
    path = tmp_path / "job.lock"
    path.write_text(json.dumps({"host": "home-pc", "pid": 1, "job": "fold"}))
    assert force_unlock(path)["host"] == "home-pc"
    assert not path.exists()
    assert force_unlock(path) is None
