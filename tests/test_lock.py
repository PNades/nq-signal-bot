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


# --- two job locks (presd_fixes issue 3, §11 One machine) ----------------------------------------------------

def test_job_locks_take_only_the_named_ones(tmp_path):
    from nqbot.jobs.lock import ALL_LOCKS, DATABENTO, SKYLIT, job_locks, lock_path

    with job_locks(tmp_path, (SKYLIT,), "skylit_backfill"):
        with job_locks(tmp_path, (DATABENTO,), "databento_backfill"):  # the two backfills run side by side
            assert all(lock_path(tmp_path, k).exists() for k in ALL_LOCKS)
            with pytest.raises(LockHeld, match="unlock --skylit"):
                with job_locks(tmp_path, ALL_LOCKS, "dummy"):
                    pass
        with pytest.raises(LockHeld, match="unlock --skylit"):
            with job_locks(tmp_path, (SKYLIT,), "step7 symbols"):
                pass
        assert not lock_path(tmp_path, DATABENTO).exists()


def test_job_locks_keep_nothing_when_the_second_is_held(tmp_path):
    from nqbot.jobs.lock import DATABENTO, SKYLIT, job_locks, lock_path

    lock_path(tmp_path, DATABENTO).write_text(json.dumps({"host": "home-pc", "pid": 1, "job": "databento_backfill"}))
    with pytest.raises(LockHeld, match="unlock --databento"):
        with job_locks(tmp_path, (SKYLIT, DATABENTO), "dummy"):
            pass
    assert not lock_path(tmp_path, SKYLIT).exists()


def test_holder_alive():
    from nqbot.jobs.lock import _create_time, holder_alive

    me = {"host": socket.gethostname(), "pid": os.getpid(), "pid_created": _create_time(os.getpid())}
    assert holder_alive(me)
    assert not holder_alive({**me, "pid_created": 1.0})  # pid reused
    assert not holder_alive({**me, "host": "home-pc"})
    assert not holder_alive({"host": socket.gethostname(), "pid": 2**22 + 12345})
    assert not holder_alive(None)
