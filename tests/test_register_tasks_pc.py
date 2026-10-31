"""scripts/register_tasks_pc.ps1 finds uv itself (owner 2026-10-30): -Uv overrides, else (Get-Command uv).Source,
the path printed first, a clear stop if there is none. Only the -ResolveUvOnly part runs here (no admin, no tasks)."""

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
