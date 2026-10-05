"""The job lock file on the SSD: only one machine runs jobs on the SSD at a time (§11 One machine).

Created exclusively and holding host, pid, the process start time and the job. A lock left by a dead
process on this machine is stale and reclaimed (the start time guards against Windows reusing the pid).
A lock held by another machine is never broken automatically: if the SSD was unplugged mid-job (the
lock can't be removed then), `nqbot unlock` shows the holder and removes it once the owner confirms
that job is no longer running, so the job can resume on this machine.
"""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path

import psutil

LOCK_NAME = ".nqbot_job.lock"


class LockHeld(Exception):
    def __init__(self, path: Path, holder: dict | None):
        self.holder = holder
        who = (
            f"job {holder.get('job')!r} (pid {holder.get('pid')} on {holder.get('host')}, "
            f"since {holder.get('started_utc')})"
            if holder
            else "an unreadable lock"
        )
        super().__init__(
            f"job lock {path} is held by {who}. If that job is no longer running (e.g. the SSD was "
            f"unplugged from the other machine), run: uv run nqbot unlock"
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


def force_unlock(path: Path) -> dict | None:
    """Remove the lock whoever holds it (only after the owner confirms that job has stopped)."""
    holder = read_lock(path)
    path.unlink(missing_ok=True)
    return holder


class JobLock:
    def __init__(self, path: Path, job: str):
        self.path = path
        self.job = job
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
                raise LockHeld(self.path, holder) from None
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
