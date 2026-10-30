"""Owner 2026-10-10: `nqbot test` keeps the whole pytest output and its exit code; the scheduled-task wrapper ends
with the program's own exit code, whatever the program wrote to stderr."""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from nqbot import testrun

WRAPPER = Path(__file__).resolve().parents[1] / "scripts" / "nqbot_task.ps1"


def _stand_in(tmp_path: Path, code: int) -> list[str]:
    """A program that writes to stdout and stderr (like a log line) and exits with `code`."""
    script = tmp_path / f"stand_in_{code}.py"
    script.write_text("import sys\nprint('3 passed')\nprint('INFO nqbot: a log line', file=sys.stderr)\n"
                      f"sys.exit({code})\n", encoding="utf-8")
    return [sys.executable, str(script)]


@pytest.mark.parametrize("code", [0, 1])
def test_nqbot_test_saves_the_whole_output_and_returns_pytests_exit_code(tmp_path, code):
    out = io.BytesIO()
    got, path = testrun.run_tests(tmp_path / "logs", cmd=_stand_in(tmp_path, code), now=datetime(2026, 10, 10, 11, 5, 9),
                                  out=out)
    assert got == code and path.name == "pytest_20261010-110509.txt"
    text = path.read_text(encoding="utf-8")
    assert "3 passed" in text and "a log line" in text  # stderr kept too
    assert text.rstrip().splitlines()[-1] == f"PYTEST EXIT CODE: {code}"
    assert f"PYTEST EXIT CODE: {code}" in out.getvalue().decode()


def _marker_stand_in(tmp_path: Path) -> list[str]:
    """A stand-in that leaves a marker file when it runs (it must not, when the start is refused)."""
    script = tmp_path / "marker.py"
    script.write_text(f"open({str(tmp_path / 'ran')!r}, 'w').close()\n", encoding="utf-8")
    return [sys.executable, str(script)]


def test_nqbot_test_stops_at_the_start_when_elevated_or_the_basetemp_is_locked(tmp_path, monkeypatch):
    """Owner 2026-10-30: an admin run, or a basetemp that exists but can't be written, stops before pytest with one
    message and a not-run exit line (C:\\nqbot_pytest locked by an admin run made every test error)."""
    monkeypatch.setattr(testrun, "is_elevated", lambda: True)
    out = io.BytesIO()
    got, path = testrun.run_tests(tmp_path / "logs", cmd=_marker_stand_in(tmp_path), now=datetime(2026, 10, 30, 9),
                                  out=out)
    text = path.read_text(encoding="utf-8")
    assert got == testrun.NOT_RUN_CODE != 0 and not (tmp_path / "ran").exists()
    assert "running elevated (admin)" in text and "PYTEST EXIT CODE: 4 (no test ran)" in out.getvalue().decode()

    monkeypatch.setattr(testrun, "is_elevated", lambda: False)
    base = tmp_path / "pt"
    cmd = [*_marker_stand_in(tmp_path), "--basetemp", str(base)]
    assert testrun.start_problem(cmd) is None  # not there yet: pytest makes it
    base.mkdir()
    assert testrun.start_problem(cmd) is None and not (base / testrun.PROBE_NAME).exists()  # writable, probe gone
    real_scandir = testrun.os.scandir

    def denied(p):
        if Path(p) == base:
            raise PermissionError(13, "Access is denied")
        return real_scandir(p)

    monkeypatch.setattr(testrun.os, "scandir", denied)
    got, path = testrun.run_tests(tmp_path / "logs", cmd=cmd, now=datetime(2026, 10, 30, 9, 1), out=io.BytesIO())
    text = path.read_text(encoding="utf-8")
    assert got == testrun.NOT_RUN_CODE and not (tmp_path / "ran").exists()
    assert "isn't writable (Access is denied)" in text and "takeown /f" in text and str(base) in text
    monkeypatch.setattr(testrun.os, "scandir", real_scandir)
    base.rmdir()
    base.write_text("")  # a file in the folder's place
    assert "is not a folder" in testrun.start_problem(cmd)
    assert testrun.basetemp_of(["pytest", "--basetemp", "a", "--basetemp=b"]) == Path("b")


def test_nqbot_test_command_is_wired(monkeypatch):
    from nqbot import cli

    seen = {}
    monkeypatch.setattr(testrun, "run_tests", lambda log_dir, extra: (seen.update(extra=extra), (5, log_dir))[1])
    monkeypatch.setattr(cli, "resolve_paths", lambda s: type("P", (), {"log_dir": Path("logs")})())
    monkeypatch.setattr(cli, "setup_logging", lambda d: None)
    assert cli.main(["test", "-k", "x"]) == 5 and seen["extra"] == ["-k", "x"]


@pytest.mark.skipif(shutil.which("powershell") is None, reason="Windows PowerShell only")
@pytest.mark.parametrize("code", [0, 4])
def test_task_wrapper_exit_code_is_the_programs_own(tmp_path, code):
    """A program that logs to stderr and exits 0 gives exit code 0 (the 10-09 task ended with 1); 4 stays 4."""
    exe = " ".join(f'"{x}"' if " " in x else x for x in _stand_in(tmp_path, code))
    logs = tmp_path / "logs"
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(WRAPPER),
                        "-LogDir", str(logs), "-LogName", "stream_session", "-NqbotArgs", "--maps NDXP", "-Exe", exe],
                       capture_output=True, timeout=120)
    assert r.returncode == code, r.stderr.decode(errors="replace")
    (log,) = logs.glob("stream_session_*.txt")
    lines = log.read_text(encoding="utf-8").splitlines()  # UTF-8, not UTF-16
    assert lines[0].startswith("TASK START") and lines[0].endswith("--maps NDXP")
    assert "3 passed" in lines and "INFO nqbot: a log line" in lines
    assert lines[-1] == f"TASK EXIT CODE: {code}"


def test_nqbot_test_keeps_the_log_when_the_ssd_drops_mid_run(tmp_path, monkeypatch):
    """Owner 2026-10-11: the 22:27 run died with an empty log when the SSD dropped; the output is kept in memory and
    the whole log written at the end."""
    import errno
    import pathlib

    real_open = pathlib.Path.open

    def flaky_open(self, *a, **k):
        if self.name.startswith("pytest_"):
            raise OSError(errno.EIO, "device gone")
        return real_open(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "open", flaky_open)
    got, path = testrun.run_tests(tmp_path / "logs", cmd=_stand_in(tmp_path, 0), now=datetime(2026, 10, 11, 22, 27, 41),
                                  out=io.BytesIO())
    monkeypatch.setattr(pathlib.Path, "open", real_open)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert got == 0 and "3 passed" in lines and lines[-1] == "PYTEST EXIT CODE: 0"
