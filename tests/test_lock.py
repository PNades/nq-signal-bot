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


def test_takeover_of_a_dead_lock_writes_our_pid_and_start(tmp_path):
    # Owner 2026-10-31 (resume drill): a run cut off by a restart left pid 5220's lock; the resumed run must hold it
    # under its own pid, process start time and start time, so `nqbot status` names the live holder.
    from nqbot.jobs.lock import _create_time, holder_alive

    path = tmp_path / "job.lock"
    dead = {"host": socket.gethostname(), "pid": 2**22 + 12345, "pid_created": 1.0, "job": "dummy_drill",
            "started_utc": "2026-10-31T06:34:14+00:00"}
    path.write_text(json.dumps(dead))
    with JobLock(path, "dummy_drill"):
        now = json.loads(path.read_text())
        assert now["pid"] == os.getpid() and now["pid_created"] == _create_time(os.getpid())
        assert now["started_utc"] > dead["started_utc"] and now["host"] == socket.gethostname()
        assert holder_alive(now)
    assert not path.exists()


def test_status_marks_a_dead_holder_stale(tmp_path):
    from types import SimpleNamespace

    from nqbot.jobs.lock import DATABENTO, SKYLIT, _create_time, lock_path
    from nqbot.jobs.status import lock_lines

    host = socket.gethostname()
    lock_path(tmp_path, SKYLIT).write_text(json.dumps(
        {"host": host, "pid": 2**22 + 12345, "pid_created": 1.0, "job": "dummy_drill", "started_utc": "t0"}))
    lock_path(tmp_path, DATABENTO).write_text(json.dumps(
        {"host": host, "pid": os.getpid(), "pid_created": _create_time(os.getpid()), "job": "x", "started_utc": "t1"}))
    sky, db = lock_lines(SimpleNamespace(root=tmp_path))
    assert "held by job 'dummy_drill'" in sky and "no longer running (stale" in sky
    assert "held by job 'x'" in db and "stale" not in db  # a live holder
    lock_path(tmp_path, SKYLIT).write_text(json.dumps({"host": "home-pc", "pid": 1, "job": "fold"}))
    assert "stale" not in lock_lines(SimpleNamespace(root=tmp_path))[0]  # another machine's lock is never judged


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
