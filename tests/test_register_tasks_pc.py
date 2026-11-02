"""scripts/register_tasks_pc.ps1 finds uv itself (owner 2026-10-30): -Uv overrides, else (Get-Command uv).Source,
the path printed first, a clear stop if there is none. Its triggers, and register_tasks.ps1's, are local wall times
(owner 2026-11-02). Only -ResolveUvOnly and -ShowTriggersOnly run here (no admin, no tasks)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "register_tasks_pc.ps1"
pytestmark = pytest.mark.skipif(shutil.which("powershell") is None, reason="Windows PowerShell only")


def _resolve(path_dirs: list[Path], *args: str) -> subprocess.CompletedProcess:
    windir = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    system = [windir / "System32", windir / "System32" / "WindowsPowerShell" / "v1.0"]
    env = {**os.environ, "PATH": os.pathsep.join(str(p) for p in [*path_dirs, *system])}
    return subprocess.run([str(system[1] / "powershell.exe"), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(SCRIPT), "-ResolveUvOnly", *args], capture_output=True, text=True, env=env, timeout=120)


def test_uv_is_found_on_path_and_printed(tmp_path):
    bin_dir = tmp_path / "with space" / "Links"
    bin_dir.mkdir(parents=True)
    (bin_dir / "uv.exe").write_bytes(b"")  # Get-Command only needs the file
    r = _resolve([bin_dir])
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"uv: {bin_dir / 'uv.exe'} (found on PATH; pass -Uv <path> to use another)"


def test_uv_override_wins_and_a_wrong_one_stops(tmp_path):
    on_path, chosen = tmp_path / "a", tmp_path / "b"
    for d in (on_path, chosen):
        d.mkdir()
        (d / "uv.exe").write_bytes(b"")
    r = _resolve([on_path], "-Uv", str(chosen / "uv.exe"))
    assert r.returncode == 0 and r.stdout.strip() == f"uv: {chosen / 'uv.exe'} (from -Uv)"
    r = _resolve([on_path], "-Uv", str(tmp_path / "nope" / "uv.exe"))
    assert r.returncode != 0 and "uv not found at -Uv" in r.stderr


def test_no_uv_stops_with_a_clear_message(tmp_path):
    r = _resolve([tmp_path])
    assert r.returncode != 0 and "uv not found on this PowerShell's PATH" in r.stderr and "-Uv" in r.stderr


# --- trigger times (owner 2026-11-02) -------------------------------------------------------------------------------
# New-ScheduledTaskTrigger -At alone stores a UTC instant ("...Z"), so a daily 07:35 registered in EDT fired at 06:35
# after the clocks went back (seen on the PC 2026-11-02). Both scripts store local wall time: no "Z", no offset.

def _show(script: str, *args: str) -> list[list[str]]:
    windir = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    ps = windir / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    r = subprocess.run([str(ps), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT.parent / script),
                        "-ShowTriggersOnly", *args], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return [line.split("|") for line in r.stdout.splitlines() if "|" in line]


def _local(start: str, hhmm: str | None = None) -> bool:
    from datetime import datetime
    ok = len(start) == 19 and datetime.fromisoformat(start).tzinfo is None   # "YYYY-MM-DDTHH:MM:SS", no Z / offset
    return ok and (hhmm is None or start[11:16] == hhmm)


def test_pc_tasks_keep_their_et_wall_times(tmp_path):
    uv = tmp_path / "uv.exe"
    uv.write_bytes(b"")
    rows = _show("register_tasks_pc.ps1", "-Uv", str(uv))
    by = {(name, kind): (start, rep) for name, kind, start, rep in rows}
    assert by[("nqbot resume", "MSFT_TaskBootTrigger")] == ("", "")              # at startup: no clock time
    assert _local(by[("nqbot resume", "MSFT_TaskDailyTrigger")][0], "07:35")
    assert _local(by[("nqbot preflight", "MSFT_TaskDailyTrigger")][0], "07:40")
    hourly = [(start, rep) for name, kind, start, rep in rows if kind == "MSFT_TaskTimeTrigger"]
    assert len(hourly) == 2 and all(_local(s) and rep == "PT1H" for s, rep in hourly)
    assert sorted(s[14:16] for s, _ in hourly) == ["05", "15"]                   # resume at :05, status push at :15


def test_laptop_tasks_keep_their_et_wall_times():
    rows = _show("register_tasks.ps1")
    starts = {name: [s for n, _, s in rows if n == name] for name, _, _ in rows}
    assert [_local(s, "08:20") for s in starts["nqbot preflight"]] == [True]
    assert starts["nqbot recording 10-19"] == ["2026-10-19T09:27:00"]
    assert sorted(s[11:16] for s in starts["nqbot sigma check"]) == ["09:05"] * 3 + ["12:00"] * 3
    assert all(_local(s) for _, _, s in rows)
