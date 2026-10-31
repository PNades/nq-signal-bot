"""scripts/fix_time_sync.ps1 (owner 2026-10-31): w32tm settings for the PC and the laptop. Only the read-only parts run
here: -ShowOnly, and the refusal without admin (nqbot test never runs elevated), so nothing on this machine changes."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fix_time_sync.ps1"
pytestmark = pytest.mark.skipif(shutil.which("powershell") is None, reason="Windows PowerShell only")


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), *args],
                          capture_output=True, text=True, timeout=120)


def test_script_is_ascii():
    """PowerShell 5.1 reads a file without a BOM in the ANSI code page: any non-ASCII character would be mangled."""
    SCRIPT.read_bytes().decode("ascii")


def test_show_only_prints_the_state_and_changes_nothing():
    r = _run("-ShowOnly")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "=== before" in out and "w32time: status=" in out and "peers: " in out
    assert "poll interval (SpecialPollInterval): " in out and "Windows keeps it within " in out
    assert "startup resync task: " in out
    assert "w32tm /query /status:" in out and "=== after" not in out


def test_without_admin_it_refuses_before_changing_anything():
    r = _run()
    assert r.returncode != 0 and "admin PowerShell" in r.stderr and "nothing was changed" in r.stderr
    assert "=== before" in r.stdout and "--- 1." not in r.stdout


def test_a_poll_interval_windows_would_clamp_is_refused_before_anything_changes():
    """2026-10-31: 900 s ran every 1024 s (2^MinPollInterval); the script now refuses such a value, checked before
    the admin check so it runs here too."""
    r = _run("-PollSec", "900")
    assert r.returncode != 0 and "-PollSec 900 is outside" in r.stderr and "nothing was changed" in r.stderr
    assert "--- 1." not in r.stdout
