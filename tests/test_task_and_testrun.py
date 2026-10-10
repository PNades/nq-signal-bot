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
