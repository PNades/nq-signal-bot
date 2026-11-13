"""The build worktree pin (owner 2026-11-09, SPEC §13 Q-dryrun13 (1), CLAUDE.md rule 13).

Builds and every long job run from a separate git worktree at a short path (e.g. `C:\\nqb\\build`, clear of the
Windows long-path limit) with its own uv environment, pinned to a commit with a recorded full-suite pass (rule 14 (f));
coding continues in the main folder. `scripts/build_worktree.ps1` creates or moves the worktree and then runs
`uv run nqbot build-pin set` inside it, which records `<data_dir>/jobs/build_pin.json` (path, commit, host). The pin
moves only between jobs: `set` refuses while a job lock is held by a live process.

While a pin exists for this machine, `nqbot run`, `nqbot resume`, `nqbot backup` and `nqbot queue add` refuse unless
they run from the pinned folder at the pinned commit (`check`), so editing the main folder never halts a build and a
build never runs unpinned code. `nqbot build-pin clear` removes the pin (back to the main folder).
"""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from nqbot import testpass
from nqbot.storage import Paths, atomic_write_json

PIN_FILE = "build_pin.json"
MOVE_FILE = "build_pin_move.json"   # owner 2026-11-13 review: a pin move in progress (scripts/build_worktree.ps1)


class PinRefused(Exception):
    pass


def pin_path(paths: Paths) -> Path:
    return paths.data_dir / "jobs" / PIN_FILE


def read_pin(paths: Paths) -> dict | None:
    p = pin_path(paths)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8-sig"))


def move_path(paths: Paths) -> Path:
    return paths.data_dir / "jobs" / MOVE_FILE


def moving(paths: Paths) -> dict | None:
    """The live pin move on this machine (its script's process), else None (a dead script's file is ignored)."""
    from nqbot.jobs import lock

    h = lock.read_lock(move_path(paths)) if move_path(paths).exists() else None
    return h if h is not None and lock.holder_alive(h) else None


def begin_move(paths: Paths, pid: int, echo: Callable[[str], None] = print) -> None:
    """`build-pin move-begin --pid N` (scripts/build_worktree.ps1, N = its own process): record the move first, then
    check no job runs (owner 2026-11-13 review: a job starting between the old one-off check and the checkout ran
    while the worktree changed). A job that starts after this refuses (`check`), and `nqbot resume` checks before each
    of its jobs, so nothing starts until `move-end`."""
    from datetime import datetime, timezone

    from nqbot.jobs import lock

    other = moving(paths)
    if other is not None and other.get("pid") != pid:
        raise PinRefused(f"another pin move is running (pid {other.get('pid')}, since {other.get('started_utc')})")
    atomic_write_json(move_path(paths), {"host": socket.gethostname(), "pid": pid, "pid_created": lock._create_time(pid),
                                         "job": "build pin move",
                                         "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    held = _live_locks(paths)
    if held:
        end_move(paths)
        raise PinRefused(f"a job is running ({', '.join(held)}): the pin moves only between jobs")
    echo("no job running: the build worktree may move (jobs refuse until build-pin move-end)")


def end_move(paths: Paths, echo: Callable[[str], None] | None = None) -> None:
    move_path(paths).unlink(missing_ok=True)
    if echo is not None:
        echo("pin move finished: jobs may start")


def _same_path(a: Path | str, b: Path | str) -> bool:
    return str(Path(a).resolve()).lower() == str(Path(b).resolve()).lower()


@dataclass(frozen=True)
class PinVerdict:
    ok: bool
    message: str


def check(paths: Paths, repo: Path | None = None, git: testpass.Git = testpass._git,
          host: str | None = None) -> PinVerdict:
    """Is this process running from the pinned worktree at the pinned commit? (no pin for this host: yes)"""
    host = host or socket.gethostname()
    mv = moving(paths)
    if mv is not None and mv.get("host") == host:
        return PinVerdict(False, f"the build worktree is being moved (scripts/build_worktree.ps1, pid {mv.get('pid')}, "
                                 f"since {mv.get('started_utc')}): start the job when it has finished")
    pin = read_pin(paths)
    if pin is None or pin.get("host") != host:
        return PinVerdict(True, "no build pin on this machine")
    repo = repo or testpass.REPO
    if not _same_path(repo, pin["path"]):
        return PinVerdict(False, f"long jobs run from the build worktree {pin['path']} (pinned to "
                                 f"{pin['commit'][:7]}), not from {repo}: start it there "
                                 f"(cd {pin['path']}; uv run nqbot ...), or move the pin with "
                                 "scripts/build_worktree.ps1, or remove it (uv run nqbot build-pin clear)")
    try:
        sha = testpass.head(git)
    except testpass.GitError as e:
        return PinVerdict(False, f"can't read the build worktree's commit: {e}")
    if sha != pin["commit"]:
        return PinVerdict(False, f"the build worktree is at {sha[:7]}, the pin says {pin['commit'][:7]}: move the pin "
                                 "with scripts/build_worktree.ps1 (between jobs only)")
    return PinVerdict(True, f"build worktree {pin['path']} at the pinned commit {sha[:7]}")


def _live_locks(paths: Paths) -> list[str]:
    from nqbot.jobs import lock

    held = []
    for kind in lock.ALL_LOCKS:   # the exclusive holder and every shared one (builds at once, owner 2026-11-12)
        for _, h, mode in lock.holders(paths.root, kind):
            if h is not None and lock.holder_alive(h):
                held.append(f"{kind}{' shared' if mode == 'shared' else ''} ({h.get('job', '?')}, pid "
                            f"{h.get('pid', '?')})")
    return held


def set_pin(paths: Paths, echo: Callable[[str], None] = print, repo: Path | None = None,
            git: testpass.Git = testpass._git, host: str | None = None) -> dict:
    """Record this worktree and its HEAD as the build pin (run inside the worktree, after `uv sync`)."""
    repo = repo or testpass.REPO
    held = _live_locks(paths)
    if held:
        raise PinRefused(f"a job is running ({', '.join(held)}): the pin moves only between jobs")
    v = testpass.check(paths, git)
    if v.ok is not True:
        raise PinRefused(f"the build worktree's commit can't be pinned: {v.message}")
    doc = {"path": str(Path(repo).resolve()), "commit": testpass.head(git), "host": host or socket.gethostname()}
    atomic_write_json(pin_path(paths), doc)
    echo(f"build pin: {doc['path']} at {doc['commit'][:7]} on {doc['host']} ({pin_path(paths)})")
    return doc


def clear_pin(paths: Paths, echo: Callable[[str], None] = print) -> None:
    held = _live_locks(paths)
    if held:
        raise PinRefused(f"a job is running ({', '.join(held)}): the pin moves only between jobs")
    p = pin_path(paths)
    if p.exists():
        p.unlink()
        echo(f"build pin removed ({p})")
    else:
        echo("no build pin")
