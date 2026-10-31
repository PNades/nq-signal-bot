"""The job lock files on the SSD: only one machine runs jobs on the SSD at a time (§11 One machine).

Two locks (owner 2026-10-05): the Skylit lock (step-7 checks, `skylit plan`, Skylit pull jobs) and the Databento
lock (`databento plan`, Databento pull jobs), so the step-10 and step-8 backfills run in parallel; every other job
holds both. Each lock file is created exclusively and holding host, pid, the process start time and the job. A lock left by a dead
process on this machine is stale and reclaimed (the start time guards against Windows reusing the pid).
A lock held by another machine is never broken automatically: if the SSD was unplugged mid-job (the
lock can't be removed then), `nqbot unlock` shows the holder and removes it once the owner confirms
that job is no longer running, so the job can resume on this machine.
"""

from __future__ import annotations

import json
import os
import socket
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

import psutil

SKYLIT, DATABENTO = "skylit", "databento"
ALL_LOCKS = (SKYLIT, DATABENTO)  # also the acquisition order
LOCK_FILES = {SKYLIT: ".nqbot_skylit.lock", DATABENTO: ".nqbot_databento.lock"}


def lock_path(root: Path, kind: str) -> Path:
    return root / LOCK_FILES[kind]


def unlock_cmd(kind: str) -> str:
    return f"uv run nqbot unlock --{kind}"


class LockHeld(Exception):
    def __init__(self, path: Path, holder: dict | None, unlock_cmd: str = "uv run nqbot unlock"):
        self.holder = holder
        who = (
            f"job {holder.get('job')!r} (pid {holder.get('pid')} on {holder.get('host')}, "
            f"since {holder.get('started_utc')})"
            if holder
            else "an unreadable lock"
        )
        super().__init__(
            f"job lock {path} is held by {who}. If that job is no longer running (e.g. the SSD was "
            f"unplugged from the other machine), run: {unlock_cmd}"
        )


def _create_time(pid: int) -> float | None:
    try:
        return psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return None


def read_lock(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def holder_alive(holder: dict | None) -> bool:
    """True if the holder is a running process on this machine (its lock must not be removed by hand)."""
    if not holder or holder.get("host") != socket.gethostname():
        return False
    created = _create_time(int(holder.get("pid", -1)))
    recorded = holder.get("pid_created")
    return created is not None and (recorded is None or abs(created - recorded) <= 1)


def holder_dead_here(holder: dict | None) -> bool:
    """True if the lock was taken on this machine by a process that is no longer running: a stale lock the next job
    run takes over (`JobLock.acquire` writes its own pid and start time). Another machine's lock is never judged."""
    return bool(holder) and holder.get("host") == socket.gethostname() and not holder_alive(holder)


@contextmanager
def job_locks(root: Path, kinds: Iterable[str], job: str) -> Iterator[None]:
    """Hold the given job locks (in ALL_LOCKS order) for `job`; if one is taken, none is kept."""
    wanted = set(kinds)
    with ExitStack() as stack:
        for kind in ALL_LOCKS:
            if kind in wanted:
                stack.enter_context(JobLock(lock_path(root, kind), job, unlock_cmd(kind)))
        yield


def force_unlock(path: Path) -> dict | None:
    """Remove the lock whoever holds it (only after the owner confirms that job has stopped)."""
    holder = read_lock(path)
    path.unlink(missing_ok=True)
    return holder


class JobLock:
    def __init__(self, path: Path, job: str, unlock_cmd: str = "uv run nqbot unlock"):
        self.path = path
        self.job = job
        self.unlock_cmd = unlock_cmd
        self.host = socket.gethostname()
        self.pid = os.getpid()
        self._held = False

    def acquire(self) -> None:
        for attempt in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                holder = read_lock(self.path)
                if attempt == 0 and self._stale(holder):
                    self.path.unlink(missing_ok=True)
                    continue
                raise LockHeld(self.path, holder, self.unlock_cmd) from None
            info = {
                "host": self.host,
                "pid": self.pid,
                "pid_created": _create_time(self.pid),
                "job": self.job,
                "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(info, f)
                f.flush()
                os.fsync(f.fileno())
            self._held = True
            return

    def release(self) -> None:
        """Remove our lock. If the SSD is gone this can't be done: the lock stays (see `nqbot unlock`)."""
        if self._held:
            holder = read_lock(self.path)
            if holder and holder.get("host") == self.host and holder.get("pid") == self.pid:
                try:
                    self.path.unlink(missing_ok=True)
                except OSError:
                    pass
            self._held = False

    def _stale(self, holder: dict | None) -> bool:
        if holder is None or holder.get("host") != self.host:
            return False
        created = _create_time(int(holder.get("pid", -1)))
        if created is None:
            return True  # that process is gone
        recorded = holder.get("pid_created")
        return recorded is not None and abs(created - recorded) > 1  # pid reused by another process

    def __enter__(self) -> JobLock:
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
