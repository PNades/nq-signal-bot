import json

import pytest

from nqbot import cli, storage
from nqbot.jobs.dummy import DummyJob
from nqbot.jobs.lock import ALL_LOCKS, DATABENTO, SKYLIT, lock_path
from nqbot.jobs.runner import state_path


def test_run_and_status_end_to_end(settings, drive, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr("nqbot.jobs.registry.JOBS", {"dummy": lambda: DummyJob(3, 0)})
    assert cli.main(["run", "dummy", "--until", "07:30"]) == 0
    assert "complete (3/3" in capsys.readouterr().out
    paths = storage.resolve_paths(settings)
    assert state_path(paths, "dummy").exists()
    assert any(paths.log_dir.glob("nqbot_*.log"))
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "dummy: 3/3 chunks done (complete)" in out and "GB free" in out


def test_refuses_before_ssd_day(settings, monkeypatch, capsys):
    settings["paths"]["root"] = None  # as committed until step 4b
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(storage, "candidate_drives", lambda: [])
    assert cli.main(["run", "dummy"]) == cli.EXIT_REFUSED
    assert "step 4b" in capsys.readouterr().err
    assert cli.main(["status"]) == cli.EXIT_REFUSED


def test_refuses_without_marker(settings, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(storage, "candidate_drives", lambda: [])
    assert cli.main(["status"]) == cli.EXIT_REFUSED
    assert ".nqbot_drive" in capsys.readouterr().err


def test_bad_arguments_rejected_before_ssd_lookup():
    for argv in (["run", "dummy", "--until", "7pm"], ["run", "dummy", "--until", "25:00"], ["run", "nosuch"]):
        with pytest.raises(SystemExit) as e:
            cli.main(argv)
        assert e.value.code == 2


def test_unlock_lets_the_other_machine_resume(settings, drive, monkeypatch, capsys):
    """§11: a job interrupted by unplugging the SSD resumes on the other machine from its last chunk."""
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr("nqbot.jobs.registry.JOBS", {"dummy": lambda: DummyJob(3, 0)})
    paths = storage.resolve_paths(settings)
    # The laptop finished one chunk, then the SSD was unplugged: its lock was left behind.
    state_path(paths, "dummy").parent.mkdir(parents=True)
    state_path(paths, "dummy").write_text(json.dumps({"done": {"chunk-000": {"sec": 1}}, "runs": [], "total": 3}))
    for kind in ALL_LOCKS:
        lock_path(paths.root, kind).write_text(json.dumps({"host": "laptop-elsewhere", "pid": 1, "job": "dummy"}))
    assert cli.main(["run", "dummy"]) == cli.EXIT_LOCKED
    assert cli.main(["unlock"]) == cli.EXIT_LOCKED  # shows the holders, asks for --yes
    out = capsys.readouterr().out
    assert "skylit job lock held by" in out and "databento job lock held by" in out
    assert all(lock_path(paths.root, k).exists() for k in ALL_LOCKS)
    assert cli.main(["unlock", "--yes"]) == 0
    assert cli.main(["run", "dummy"]) == 0
    assert "complete (3/3" in capsys.readouterr().out
    assert sorted(json.loads(state_path(paths, "dummy").read_text())["runs"][-1]["chunks"]) == ["chunk-001", "chunk-002"]


def test_unlock_selector_and_live_holder(settings, drive, monkeypatch, capsys):
    import os
    import socket

    from nqbot.jobs.lock import _create_time

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    paths = storage.resolve_paths(settings)
    paths.root.mkdir(parents=True)
    other = {"host": "home-pc", "pid": 1, "job": "skylit_backfill"}
    lock_path(paths.root, SKYLIT).write_text(json.dumps(other))
    lock_path(paths.root, DATABENTO).write_text(json.dumps(other | {"job": "databento_backfill"}))
    assert cli.main(["unlock", "--databento", "--yes"]) == 0
    assert lock_path(paths.root, SKYLIT).exists() and not lock_path(paths.root, DATABENTO).exists()
    # A lock whose holder is still running on this machine is never removed by hand.
    me = {"host": socket.gethostname(), "pid": os.getpid(), "pid_created": _create_time(os.getpid()), "job": "x"}
    lock_path(paths.root, DATABENTO).write_text(json.dumps(me))
    capsys.readouterr()
    assert cli.main(["unlock", "--yes"]) == cli.EXIT_LOCKED
    assert "still running on this machine" in capsys.readouterr().out
    assert all(lock_path(paths.root, k).exists() for k in ALL_LOCKS)
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "skylit job lock: held by job 'skylit_backfill'" in out and "databento job lock: held by job 'x'" in out
    with pytest.raises(SystemExit):
        cli.main(["unlock", "--skylit", "--gameplan"])  # one selector at a time


def test_run_prints_power_warnings(settings, drive, monkeypatch, capsys):
    from nqbot import power

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr("nqbot.jobs.registry.JOBS", {"dummy": lambda: DummyJob(1, 0)})
    monkeypatch.setattr(power.os, "name", "nt")
    monkeypatch.setattr(power, "powercfg_query", lambda sub, setting: "Current AC Power Setting Index: 0x00000e10")
    assert cli.main(["run", "dummy"]) == 0
    err = capsys.readouterr().err
    assert "sleep after (on AC) could stop an overnight job" in err and "standby-timeout-ac 0" in err
