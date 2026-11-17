"""The PreToolUse hook (owner 2026-11-17, `nqbot/tool_guard.py`, `.claude/settings.json`): piped test runs (rule 14 (e))
and stopping processes by name or pattern (rule 15) are blocked with the rule named; everything else passes."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from nqbot import tool_guard as tg

PIPED_TESTS = [
    "uv run nqbot test tests/test_config.py | tail -5",
    ".venv/Scripts/nqbot.exe test -m 'not slow' | head -40",
    "uv run pytest tests | grep FAILED",
    "python -m pytest -q 2>&1 | tail",
    "cd x && uv run nqbot test | Select-Object -Last 5",
    "uv run py.test | tee out.txt",
]
KILL_BY_NAME = [
    "taskkill /IM python.exe /F",
    "taskkill //IM node.exe //F",
    'taskkill /F /FI "IMAGENAME eq python.exe"',
    "Stop-Process -Name python",
    "Stop-Process -ProcessName node -Force",
    "Get-Process python | Stop-Process",
    "Get-Process | Where-Object { $_.CommandLine -match 'nqbot' } | Stop-Process -Force",
    "pkill -f nqbot",
    "killall node",
    "kill -9 $(pgrep -f uvicorn)",
    "pgrep python | xargs kill",
    "wmic process where name='python.exe' delete",
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Invoke-CimMethod -MethodName Terminate",
]
ALLOWED = [
    "uv run nqbot test tests/test_config.py",
    "uv run nqbot test tests/test_config.py tests/test_months.py -m \"not slow\"",
    "uv run nqbot test; tail -3 D:/nqbot/logs/pytest_x.txt",                 # a separate command after the run
    "uv run nqbot test || echo failed",                                       # || is not a pipe
    'grep -n "nqbot test" CLAUDE.md | head -5',                                # the words in quotes, not a run
    "git log --oneline | head -3",
    'git commit -m "never pipe nqbot test | tail; taskkill /IM is banned"',    # text in a message
    "taskkill //PID 1234 //T //F",
    "taskkill /PID 1234 /F",
    "Stop-Process -Id 4321 -Force",
    "kill 1234",
    "powershell -NoProfile -Command \"(Get-CimInstance Win32_Process -Filter 'ProcessId=12').Name\"",
    "netstat -ano | grep 8735",
    "npx tsc --noEmit -p .",
]


@pytest.mark.parametrize("cmd", PIPED_TESTS)
def test_piped_test_runs_are_blocked_by_rule_14e(cmd):
    assert tg.check(cmd) == tg.RULE_14E


@pytest.mark.parametrize("cmd", KILL_BY_NAME)
def test_stopping_by_name_or_pattern_is_blocked_by_rule_15(cmd):
    assert tg.check(cmd) == tg.RULE_15


@pytest.mark.parametrize("cmd", ALLOWED)
def test_everything_else_is_allowed(cmd):
    assert tg.check(cmd) is None


def _hook(payload) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "nqbot.tool_guard"], input=json.dumps(payload), capture_output=True,
                          text=True, timeout=60)


def test_the_hook_blocks_with_exit_2_and_names_the_rule():
    r = _hook({"tool_name": "Bash", "tool_input": {"command": "uv run nqbot test | tail"}})
    assert r.returncode == 2 and "rule 14 (e)" in r.stderr
    r = _hook({"tool_name": "PowerShell", "tool_input": {"command": "Stop-Process -Name python"}})
    assert r.returncode == 2 and "rule 15" in r.stderr
    r = _hook({"tool_name": "Bash", "tool_input": {"command": "taskkill //PID 99 //F"}})
    assert r.returncode == 0 and not r.stderr
    assert _hook({"tool_name": "Read", "tool_input": {"file_path": "x"}}).returncode == 0   # not a shell command


def test_the_settings_wire_the_hook_without_secrets():
    from pathlib import Path

    s = json.loads((Path(__file__).resolve().parents[1] / ".claude" / "settings.json").read_text(encoding="utf-8"))
    pre = s["hooks"]["PreToolUse"]
    assert any("Bash" in h["matcher"] and "PowerShell" in h["matcher"]
               and any("nqbot.tool_guard" in x["command"] for x in h["hooks"]) for h in pre)
    text = json.dumps(s).lower()
    assert not any(k in text for k in ("api_key", "token", "secret", "webhook", "password"))
