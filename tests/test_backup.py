"""nqbot backup (presd_fixes 13, SPEC §11 Storage): rclone copy + check of raw/skylit/, never a real rclone here."""

from datetime import timedelta

import pytest

from nqbot import backup, cli
from nqbot.backup import BackupRefused, SkylitBackupJob
from nqbot.jobs.runner import run_job


class FakeRclone:
    def __init__(self, remotes="gdrive:\n", installed=True, fail=None, stop_on=None):
        self.remotes, self.installed, self.fail, self.stop_on = remotes, installed, fail or {}, stop_on
        self.calls: list[list[str]] = []

    def quick(self, args):
        if not self.installed:
            return -1, "[WinError 2] The system cannot find the file specified"
        return (0, "rclone v1.68.0\n- os/version: windows") if args == ["version"] else (0, self.remotes)

    def run(self, args, should_stop):
        self.calls.append(args)
        if self.stop_on == args[0]:
            return None
        return self.fail.get((args[0], args[2].rsplit("/", 1)[-1]), 0)


def _skylit(paths):
    root = paths.raw_dir / "skylit"
    for rel in ("range/2025-10-06/0930_boards.parquet", "range/2025-10-07/0930_boards.parquet",
                "range/2025-11-03/0930_boards.parquet", "darkpool/trades/2025-10-06.parquet", "ledger.jsonl"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"x" * 10)
    (root / "range" / "2025-10-06" / ".0945_boards.parquet.abc.tmp").write_bytes(b"half")  # a temp: never copied
    return root


def _run(paths, settings, log, clock, fake):
    return run_job(SkylitBackupJob(lambda exe: fake), settings, paths, until=clock.now() + timedelta(hours=8), log=log,
                   now=clock.now, sleep=clock.sleep, is_trading_day=lambda d: True)


def test_settings_name_the_owners_remote_and_folder(settings):
    assert backup.settings_of(settings) == {"remote": "gdrive", "folder": "NQ bot backup", "exe": "rclone"}


def test_copies_and_checks_every_folder_with_checksums(paths, settings, log, clock):
    root = _skylit(paths)
    fake = FakeRclone()
    result = _run(paths, settings, log, clock, fake)
    assert result.status == "complete" and result.done == 4  # range/2025-10, range/2025-11, darkpool, _files
    assert [c[0] for c in fake.calls] == ["copy", "check"] * 4
    assert [r.split("@")[0] for r in result.ran] == ["range/2025-10", "range/2025-11", "darkpool", "_files"]
    copy_oct = next(c for c in fake.calls if c[0] == "copy" and "+ /2025-10-*/**" in c)
    assert copy_oct[1] == str(root / "range") and copy_oct[2] == "gdrive:NQ bot backup/raw/skylit/range"  # one arg
    assert "--checksum" in copy_oct and copy_oct[copy_oct.index("--filter") + 1] == "- .*.tmp"
    check_root = next(c for c in fake.calls if c[0] == "check" and c[2] == "gdrive:NQ bot backup/raw/skylit")
    assert "--one-way" in check_root and "+ /*" in check_root
    report = (paths.reports_dir / "backup" / "backup.md").read_text(encoding="utf-8")
    assert "verified: 4" in report and "| range/2025-10 | 2 |" in report  # the temp file isn't counted


def test_a_second_run_copies_only_folders_that_changed(paths, settings, log, clock):
    root = _skylit(paths)
    _run(paths, settings, log, clock, FakeRclone())
    fake = FakeRclone()
    assert _run(paths, settings, log, clock, fake).ran == []  # nothing changed: nothing re-sent
    (root / "range" / "2025-11-04").mkdir()
    (root / "range" / "2025-11-04" / "0930_boards.parquet").write_bytes(b"new")
    result = _run(paths, settings, log, clock, fake)
    assert [r.split("@")[0] for r in result.ran] == ["range/2025-11"]


def test_a_failed_check_stops_loudly_and_reruns(paths, settings, log, clock):
    _skylit(paths)
    fake = FakeRclone(fail={("check", "darkpool"): 1})
    result = _run(paths, settings, log, clock, fake)
    assert result.status == "backup_failed" and "differ from or are missing on Drive" in result.message
    report = (paths.reports_dir / "backup" / "backup.md").read_text(encoding="utf-8")
    assert "check_failed" in report
    result = _run(paths, settings, log, clock, FakeRclone())
    assert result.status == "complete" and any(r.startswith("darkpool@") for r in result.ran)


def test_a_stop_mid_copy_leaves_the_chunk_to_rerun(paths, settings, log, clock):
    _skylit(paths)
    result = _run(paths, settings, log, clock, FakeRclone(stop_on="copy"))
    assert result.status == "until" and result.done == 0 and "re-runs next time" in result.message


@pytest.mark.parametrize("fake, match", [(FakeRclone(installed=False), "isn't installed"),
                                         (FakeRclone(remotes="onedrive:\n"), "no remote named 'gdrive'")])
def test_refuses_without_rclone_or_the_remote(paths, settings, log, clock, fake, match):
    _skylit(paths)
    with pytest.raises(BackupRefused, match=match):
        _run(paths, settings, log, clock, fake)
    assert fake.calls == []


def test_refuses_with_nothing_to_back_up(paths, settings, log, clock):
    (paths.raw_dir / "skylit").mkdir(parents=True)
    with pytest.raises(BackupRefused, match="nothing to back up"):
        _run(paths, settings, log, clock, FakeRclone())


def test_cli_backup_refuses_cleanly(settings, drive, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr("nqbot.jobs.registry.JOBS", {"skylit_backup": SkylitBackupJob})
    assert cli.main(["backup"]) == cli.EXIT_REFUSED
    assert "nothing to back up" in capsys.readouterr().err


def test_real_rclone_wrapper_reports_a_missing_program():
    code, out = backup.SubprocessRclone("nqbot-no-such-rclone").quick(["version"])
    assert code == -1 and out


def test_real_rclone_wrapper_passes_exit_codes_and_stops(monkeypatch):
    """SubprocessRclone.run on Windows: a stand-in program (python) for the exit code and for a stop mid-run."""
    import sys
    import time

    monkeypatch.setattr(backup, "POLL_SEC", 0.05)
    rc = backup.SubprocessRclone(sys.executable)
    assert rc.run(["-c", "import sys; sys.exit(3)"], lambda: None) == 3
    t0 = time.monotonic()
    assert rc.run(["-c", "import time; time.sleep(60)"], lambda: "reached 07:30") is None  # terminated
    assert time.monotonic() - t0 < 30
    code, out = rc.quick(["-c", "print('gdrive:')"])
    assert code == 0 and out.split() == ["gdrive:"]
