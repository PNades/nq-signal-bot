"""Guards against a runaway write reaching the repo (2026-10-07: a Status-edit script replaced an empty string,
CLAUDE.md grew from 47 KB to 50 MB and was committed and pushed).

Two rules (owner 2026-10-07):
- no tracked or staged file over MAX_FILE_BYTES;
- a guarded file (CLAUDE.md) may not more than double or fall below half of its committed size in one change.

Used three ways, so a bad write is stopped wherever it happens:
- `python -m nqbot.repo_guard --worktree`: the Claude Code PostToolUse hook (.claude/settings.json) after every
  Bash/Write/Edit; exit 2 tells Claude Code the change is refused;
- `python -m nqbot.repo_guard --staged`: the git pre-commit hook (.githooks/pre-commit, `core.hooksPath`);
- tests/test_repo_guard.py, in every test run.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

MAX_FILE_BYTES = 1_000_000          # owner 2026-10-07: no tracked file over 1 MB
GUARDED = ("CLAUDE.md",)            # owner 2026-10-07: Status/CLAUDE.md edits
MAX_GROWTH, MIN_SHRINK = 2.0, 0.5   # refuse more than double or less than half of the committed size

REPO = Path(__file__).resolve().parents[2]


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True)


def committed_size(path: str, rev: str = "HEAD") -> int | None:
    out = _git("cat-file", "-s", f"{rev}:{path}")
    return int(out.stdout) if out.returncode == 0 else None


def staged_size(path: str) -> int | None:
    out = _git("cat-file", "-s", f":{path}")
    return int(out.stdout) if out.returncode == 0 else None


def size_change_problem(path: str, old: int | None, new: int | None) -> str | None:
    """Why a change of a guarded file from `old` to `new` bytes is refused, or None."""
    if old is None or new is None or old == 0:
        return None
    ratio = new / old
    if ratio > MAX_GROWTH or ratio < MIN_SHRINK:
        return (f"{path}: {old:,} -> {new:,} bytes ({ratio:.2f}x the committed size); a change to it must stay "
                f"within {MIN_SHRINK:g}x-{MAX_GROWTH:g}x. Restore it with `git checkout -- {path}` and redo the edit")
    return None


def too_big(path: str, size: int) -> str | None:
    if size > MAX_FILE_BYTES:
        return f"{path}: {size:,} bytes, over the {MAX_FILE_BYTES:,}-byte limit for a tracked file"
    return None


def check_worktree() -> list[str]:
    """The working tree against HEAD: guarded files' size change, and tracked files over the limit."""
    problems = []
    for path in GUARDED:
        f = REPO / path
        if f.exists():
            p = size_change_problem(path, committed_size(path), f.stat().st_size)
            if p:
                problems.append(p)
    for path in tracked_files():
        f = REPO / path
        if f.is_file():
            p = too_big(path, f.stat().st_size)
            if p:
                problems.append(p)
    return problems


def check_staged() -> list[str]:
    """What is about to be committed: every staged file's size, and guarded files' change against HEAD."""
    names = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").stdout.decode().split("\0")
    problems = []
    for path in filter(None, names):
        size = staged_size(path)
        if size is not None:
            p = too_big(path, size)
            if p:
                problems.append(p)
        if path in GUARDED:
            p = size_change_problem(path, committed_size(path), size)
            if p:
                problems.append(p)
    return problems


def tracked_files() -> list[str]:
    return [p for p in _git("ls-files", "-z").stdout.decode().split("\0") if p]


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "--worktree"
    problems = check_staged() if mode == "--staged" else check_worktree()
    if problems:
        print("repo_guard refused:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 2  # Claude Code hooks: exit 2 = blocking error fed back to Claude; git: any non-zero aborts
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
