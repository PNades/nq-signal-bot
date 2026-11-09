"""Full-suite passes and the job check (owner 2026-10-29, CLAUDE.md rule 14 (f)): a long job (`nqbot run`, `nqbot
backup`, `nqbot resume`, `nqbot queue add`) runs only code whose commit passed the full suite (`uv run nqbot test`
with no pytest arguments, `PYTEST EXIT CODE: 0`).

`nqbot test` records a pass in `<data_dir>/jobs/test_passes.jsonl` (on the SSD, so a pass on the laptop counts on
the PC after `git pull`) only when the tracked files were unchanged from HEAD before and after the run, and HEAD did
not move. A commit counts as passed if it was recorded, or if a recorded commit is its ancestor and only Markdown
files (`*.md`, the docs-only tier (a)) changed since. Uncommitted changes to tracked files other than Markdown
refuse the job: the code that would run isn't the commit. If git or the record can't be read, the job warns clearly
and goes on (rule (f): "refuses ... or warns clearly if that can't be checked")."""

from __future__ import annotations

import socket
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from nqbot.storage import Paths, append_jsonl, read_jsonl

PASS_FILE = "test_passes.jsonl"
REPO = Path(__file__).resolve().parents[2]
DOCS_SUFFIX = ".md"  # the docs-only tier (rule 14 (a))

Git = Callable[[list[str]], str]  # runs `git <args>` in the repo, returns stdout; raises on failure


class GitError(RuntimeError):
    pass


def _git(args: list[str]) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        raise GitError(f"git {' '.join(args)}: {e}") from e
    if r.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {r.stderr.strip() or r.returncode}")
    return r.stdout


def pass_path(paths: Paths) -> Path:
    return paths.data_dir / "jobs" / PASS_FILE


@dataclass(frozen=True)
class Verdict:
    ok: bool | None  # True passed, False refuse, None can't be checked (warn, go on)
    message: str


def dirty_files(git: Git = _git) -> list[str]:
    """Tracked files that differ from HEAD (staged or not); untracked files don't count."""
    out = git(["status", "--porcelain", "--untracked-files=no"])
    return [line[3:].split(" -> ")[-1].strip('"') for line in out.splitlines() if line.strip()]


def head(git: Git = _git) -> str:
    return git(["rev-parse", "HEAD"]).strip()


@dataclass(frozen=True)
class RunState:
    commit: str | None  # None: the run can't be recorded (git unreadable or tracked files changed)
    reason: str


def before_run(git: Git = _git) -> RunState:
    try:
        dirty = dirty_files(git)
        sha = head(git)
    except GitError as e:
        return RunState(None, f"git unreadable ({e})")
    if dirty:
        return RunState(None, f"tracked files changed ({', '.join(dirty[:5])}{' ...' if len(dirty) > 5 else ''})")
    return RunState(sha, "")


def record(paths: Paths, state: RunState, code: int, wall_sec: float, *, git: Git = _git,
           now: datetime | None = None, host: str | None = None) -> str:
    """After a full run: append the pass if it counts; return the line to print."""
    if code != 0:
        return "full-suite pass not recorded (the suite failed)"
    if state.commit is None:
        return f"full-suite pass NOT recorded: {state.reason}; commit, then run uv run nqbot test again"
    after = before_run(git)
    if after.commit != state.commit:
        return f"full-suite pass NOT recorded: HEAD or tracked files changed during the run ({after.reason or 'HEAD moved'})"
    append_jsonl(pass_path(paths), {"commit": state.commit, "finished": (now or datetime.now()).isoformat(timespec="seconds"),
                                    "host": host or socket.gethostname(), "wall_sec": round(wall_sec)})
    return f"full-suite pass recorded for commit {state.commit[:7]} ({pass_path(paths)})"


def check(paths: Paths, git: Git = _git) -> Verdict:
    try:
        dirty = [f for f in dirty_files(git) if not f.lower().endswith(DOCS_SUFFIX)]
        sha = head(git)
    except GitError as e:
        return Verdict(None, f"can't read git, so can't check the full-suite pass: {e}")
    if dirty:
        return Verdict(False, f"uncommitted changes to tracked files ({', '.join(dirty[:5])}"
                              f"{' ...' if len(dirty) > 5 else ''}): the job would run code no full-suite pass covers. "
                              f"Commit, run uv run nqbot test (PYTEST EXIT CODE: 0), then start the job")
    try:
        passed = [r.get("commit") for r in read_jsonl(pass_path(paths))]
    except (OSError, ValueError) as e:
        return Verdict(None, f"can't read {pass_path(paths)}, so can't check the full-suite pass: {e}")
    if sha in passed:
        return Verdict(True, f"commit {sha[:7]} passed the full suite")
    for c in reversed([c for c in passed if c]):  # newest first
        try:
            git(["merge-base", "--is-ancestor", c, sha])
        except GitError:
            continue  # not an ancestor (or unknown here)
        try:
            changed = [f for f in git(["diff", "--name-only", c, sha]).splitlines() if f.strip()]
        except GitError:
            continue
        if all(f.lower().endswith(DOCS_SUFFIX) for f in changed):
            return Verdict(True, f"commit {sha[:7]}: only docs changed since {c[:7]}, which passed the full suite")
    return Verdict(False, f"commit {sha[:7]} hasn't passed the full suite (CLAUDE.md rule 14 (c), (f)): run "
                          f"uv run nqbot test (no arguments) and get PYTEST EXIT CODE: 0 first")


def require(paths: Paths, echo: Callable[[str], None], what: str) -> bool:
    """True if the job may start. Prints a refusal or, when the pass can't be checked, a clear warning."""
    v = check(paths)
    if v.ok is False:
        echo(f"refused: {what}: {v.message}")
        return False
    if v.ok is None:
        echo(f"WARNING: {what} starts unchecked: {v.message}")
    from nqbot import build_pin   # owner 2026-11-09 (Q-dryrun13 (1)): long jobs run from the pinned build worktree

    try:
        pv = build_pin.check(paths)
    except (OSError, ValueError, KeyError) as e:
        echo(f"WARNING: {what}: can't read the build pin ({e})")
        return True
    if not pv.ok:
        echo(f"refused: {what}: {pv.message}")
        return False
    return True
