import json

import pytest

from nqbot import cli, storage
from nqbot.jobs.dummy import DummyJob
from nqbot.jobs.lock import LOCK_NAME
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
    (paths.root / LOCK_NAME).write_text(json.dumps({"host": "laptop-elsewhere", "pid": 1, "job": "dummy"}))
    assert cli.main(["run", "dummy"]) == cli.EXIT_LOCKED
    assert cli.main(["unlock"]) == cli.EXIT_LOCKED  # shows the holder, asks for --yes
    assert "laptop-elsewhere" in capsys.readouterr().out
    assert (paths.root / LOCK_NAME).exists()
    assert cli.main(["unlock", "--yes"]) == 0
    assert cli.main(["run", "dummy"]) == 0
    assert "complete (3/3" in capsys.readouterr().out
    assert sorted(json.loads(state_path(paths, "dummy").read_text())["runs"][-1]["chunks"]) == ["chunk-001", "chunk-002"]
