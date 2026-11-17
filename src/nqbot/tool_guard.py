"""A PreToolUse hook for Claude Code (owner 2026-11-17; `.claude/settings.json`): blocks shell commands that break the
working rules before they run, and says which rule.

- **Rule 14 (e):** a test run (pytest, `python -m pytest`, `nqbot test`) is never piped (`| tail`, `| Select-Object` ...):
  run it bare and read its saved log.
- **Rule 15:** processes are stopped only by the PID recorded when they were started (`taskkill /PID n`,
  `Stop-Process -Id n`, `kill n`), never by name or pattern: `taskkill /IM` or `/FI`, `Stop-Process -Name`, anything
  piped into `Stop-Process`, `pkill`, `killall`, `kill $(pgrep ...)`, `xargs kill`, `wmic process ... delete|terminate`,
  `Win32_Process ... Terminate`.

Quoted text is ignored (a grep for the words "nqbot test" or a commit message is not a command). The hook reads Claude
Code's JSON on stdin (`tool_input.command` of the Bash and PowerShell tools) and exits 2 with the reason on stderr to
block; 0 otherwise. `check(command)` is the testable part.
"""

from __future__ import annotations

import json
import re
import sys

RULE_14E = ("Blocked by CLAUDE.md rule 14 (e): a test run (pytest / nqbot test) is never piped. Run it bare (its output "
            "is saved to <root>/logs/pytest_<time>.txt and ends with 'PYTEST EXIT CODE: N') and read that.")
RULE_15 = ("Blocked by CLAUDE.md rule 15: stop a process only by the PID recorded when you started it (taskkill /PID <n>, "
           "Stop-Process -Id <n>, kill <n>), never by name or pattern; a long job stops with `nqbot stop <job>`; anything "
           "else is the owner's to stop.")

_QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")
_RUNNER = re.compile(r"(?<![\w.-])(?:py\.test|pytest|nqbot(?:\.exe)?\s+test)(?![\w.-])", re.I)
_SEGMENT = re.compile(r"&&|\|\||;|\r?\n")
_PIPE = re.compile(r"(?<!\|)\|(?!\|)")
_KILL_BY_NAME = [
    re.compile(r"\btaskkill(?:\.exe)?\b[^;&\n]*?\s[/-]{1,2}(?:im|fi)\b", re.I),   # /IM, //IM (Git Bash), -IM
    re.compile(r"\b(?:stop-process|spps)\b[^;&\n|]*?\s-(?:n|na|nam|name|pr\w*name|processname)\b", re.I),
    re.compile(r"\|\s*(?:stop-process|spps)\b", re.I),
    re.compile(r"(?<![\w-])(?:pkill|killall)(?![\w-])", re.I),
    re.compile(r"\bpgrep\b[^;&\n]*\|\s*xargs\s+kill\b|\bkill\b[^;&\n]*\$\(\s*pgrep\b|\bxargs\s+kill\b", re.I),
    re.compile(r"\bwmic\b[^;&\n]*\bprocess\b[^;&\n]*\b(?:delete|terminate)\b", re.I),
    re.compile(r"\bwin32_process\b.*\bterminate\b", re.I | re.S),
]


def _unquoted(command: str) -> str:
    return _QUOTED.sub('""', command)


def check(command: str) -> str | None:
    """The rule a command breaks (its message), or None."""
    bare = _unquoted(command or "")
    for seg in _SEGMENT.split(bare):
        if _RUNNER.search(seg) and _PIPE.search(seg):
            return RULE_14E
    for pat in _KILL_BY_NAME:
        if pat.search(bare):
            return RULE_15
    return None


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0                     # not ours to judge: never block on a hook-input problem
    command = ((data.get("tool_input") or {}).get("command")) if isinstance(data, dict) else None
    reason = check(command) if isinstance(command, str) else None
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
