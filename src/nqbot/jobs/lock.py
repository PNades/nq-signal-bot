"""The job lock files on the SSD: only one machine runs jobs on the SSD at a time (§11 One machine).

Two locks (owner 2026-10-05): the Skylit lock (step-7 checks, `skylit plan`, Skylit pull jobs) and the Databento
lock (`databento plan`, Databento pull jobs), so the step-10 and step-8 backfills run in parallel; every other job
holds both. Each lock file is created exclusively and holding host, pid, the process start time and the job. A lock left by a dead
process on this machine is stale and reclaimed (the start time guards against Windows reusing the pid).
A lock held by another machine is never broken automatically: if the SSD was unplugged mid-job (the
lock can't be removed then), `nqbot unlock` shows the holder and removes it once the owner confirms
that job is no longer running, so the job can resume on this machine.

Shared holds (owner 2026-11-12, SPEC §13 Q-step13b (f) (2), two feature builds at once with `parallel_builds`): a job
that only reads the stored data may hold a lock shared (`SharedLock`): several shared holders at once, never together
with the exclusive holder. Each shared holder has its own file beside the lock (`<lock file>.shared-<host>-<pid>-<job>`).
"""

from __future__ import annotations

import json
import logging
import os
import socket
import time
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

import psutil

SKYLIT, DATABENTO = "skylit", "databento"
ALL_LOCKS = (SKYLIT, DATABENTO)  # also the acquisition order
LOCK_FILES = {SKYLIT: ".nqbot_skylit.lock", DATABENTO: ".nqbot_databento.lock"}
SHARED_TAG = ".shared-"
# Release (owner 2026-11-15, after the build-budget mutex stuck twice on 2026-11-15): Windows refuses to delete a file
# another process has open (WinError 32), and a starter waiting for a lock opens it every 50 ms to read the holder, so
# the delete is retried for this long before it fails loudly (it used to fail silently, leaving a live holder's lock).
RELEASE_WAIT_SEC = 5.0
RELEASE_RETRY_SEC = 0.05

_log = logging.getLogger("nqbot")
_HELD_HERE: set[str] = set()   # lock files a JobLock of this process holds now (for `reclaim_own`)


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


class LockReleaseFailed(OSError):
    """Our lock file could not be removed within RELEASE_WAIT_SEC (it stays, held by this live process)."""


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
def job_locks(root: Path, kinds: Iterable[str], job: str, shared: bool = False) -> Iterator[None]:
    """Hold the given job locks (in ALL_LOCKS order) for `job`, alone or `shared`; if one is taken, none is kept."""
    wanted = set(kinds)
    with ExitStack() as stack:
        for kind in ALL_LOCKS:
            if kind in wanted:
                stack.enter_context(SharedLock(root, kind, job, unlock_cmd(kind)) if shared else
                                    JobLock(lock_path(root, kind), job, unlock_cmd(kind), shared_of=(root, kind)))
        yield


def force_unlock(path: Path) -> dict | None:
    """Remove the lock whoever holds it (only after the owner confirms that job has stopped)."""
    holder = read_lock(path)
    path.unlink(missing_ok=True)
    return holder


def take_stale(path: Path, holder: dict | None) -> None:
    """Remove a dead holder's lock file so that only one process does it (owner 2026-11-13 review: two starters that
    both read the same dead holder could each remove it, the second removing the first's new lock, and both would
    hold it): the file is renamed away (one rename wins), and if what was moved is not that dead holder (someone took
    the lock over meanwhile) it is put back."""
    moved = path.with_name(f"{path.name}.stale-{socket.gethostname()}-{os.getpid()}-{time.time_ns()}")
    try:
        os.rename(path, moved)
    except OSError:   # gone already (another process took it over first) or in use: the create decides
        return
    if read_lock(moved) == holder:
        moved.unlink(missing_ok=True)
        return
    try:
        os.rename(moved, path)   # a live holder's lock: give it back
    except OSError:
        moved.unlink(missing_ok=True)


class JobLock:
    def __init__(self, path: Path, job: str, unlock_cmd: str = "uv run nqbot unlock",
                 shared_of: tuple[Path, str] | None = None, reclaim_own: bool = False):
        self.path = path
        self.shared_of = shared_of   # (root, kind) of an exclusive data lock: it also waits for no shared holder
        # a lock only one thread of a process ever takes (the build-budget mutex): one held by this very process that
        # no JobLock of ours holds can only be left by a release that failed, so it is taken back (owner 2026-11-15)
        self.reclaim_own = reclaim_own
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
                    take_stale(self.path, holder)
                    continue
                if attempt == 0 and self._own_leftover(holder):
                    _log.warning("%s: taking back our own leftover lock %s (an earlier release failed)", self.job,
                                 self.path)
                    take_stale(self.path, holder)
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
            _HELD_HERE.add(str(self.path))
            if self.shared_of is not None:
                self._refuse_if_shared(*self.shared_of)
            return

    def _refuse_if_shared(self, root: Path, kind: str) -> None:
        """After creating the lock file: a live shared holder (a build reading the data) refuses us; a dead one's file
        on this machine is removed (`SharedLock` mirrors this, so the two never hold at once)."""
        for path in sorted(root.glob(shared_glob(kind))):
            holder = read_lock(path)
            if self._stale(holder):
                path.unlink(missing_ok=True)
                continue
            self.release()
            raise LockHeld(path, holder, self.unlock_cmd)

    def release(self) -> None:
        """Remove our lock. A delete (or read) refused because another process has the file open is retried for
        RELEASE_WAIT_SEC, then logged and raised (`LockReleaseFailed`; owner 2026-11-15). A lock no longer ours is
        left alone. If the SSD is gone this can't be done: the lock stays (see `nqbot unlock`)."""
        if not self._held:
            return
        try:
            self._remove_ours()
        finally:
            if not self._held:
                _HELD_HERE.discard(str(self.path))

    def _remove_ours(self) -> None:
        t0 = time.monotonic()
        while True:
            try:
                with open(self.path, encoding="utf-8") as f:
                    holder = json.load(f)
                if not (isinstance(holder, dict) and holder.get("host") == self.host
                        and holder.get("pid") == self.pid):
                    self._held = False   # someone else's now: never removed
                    return
                self.path.unlink()
                self._held = False
                return
            except FileNotFoundError:
                self._held = False       # gone already, or the SSD with it (the lock stays: `nqbot unlock`)
                return
            except (OSError, ValueError) as e:
                if not self.path.parent.exists():
                    self._held = False   # the SSD went away
                    return
                if time.monotonic() - t0 > RELEASE_WAIT_SEC:
                    self._held = False   # not ours to retry again; a reclaim_own lock is taken back next time
                    _log.error("%s: could not remove our lock %s within %.0f s: %s", self.job, self.path,
                               RELEASE_WAIT_SEC, e)
                    raise LockReleaseFailed(f"could not remove the lock {self.path} within "
                                            f"{RELEASE_WAIT_SEC:.0f} s: {e}") from e
                time.sleep(RELEASE_RETRY_SEC)

    def _own_leftover(self, holder: dict | None) -> bool:
        """This live process's own lock, while no JobLock of ours holds it (only with `reclaim_own`)."""
        if not (self.reclaim_own and not self._held and str(self.path) not in _HELD_HERE and holder
                and holder.get("host") == self.host and holder.get("pid") == self.pid):
            return False
        recorded, created = holder.get("pid_created"), _create_time(self.pid)
        return recorded is None or created is None or abs(created - recorded) <= 1

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


# --- shared holds (two feature builds at once) ---------------------------------------------------------------------

def shared_glob(kind: str) -> str:
    return LOCK_FILES[kind] + SHARED_TAG + "*"


def shared_path(root: Path, kind: str, job: str) -> Path:
    return root / f"{LOCK_FILES[kind]}{SHARED_TAG}{socket.gethostname()}-{os.getpid()}-{job}"


def holders(root: Path, kind: str) -> list[tuple[Path, dict | None, str]]:
    """Every holder of the `kind` lock: (its file, the holder, "exclusive" | "shared")."""
    out = []
    p = lock_path(root, kind)
    if p.exists():
        out.append((p, read_lock(p), "exclusive"))
    for s in sorted(root.glob(shared_glob(kind))) if root.exists() else []:
        out.append((s, read_lock(s), "shared"))
    return out


class SharedLock(JobLock):
    """A shared hold on a data lock. The holder creates its own file, then looks for the exclusive lock file; an
    exclusive holder creates the lock file, then looks for shared files (`JobLock._refuse_if_shared`). Whoever looks
    second sees the other and backs off, so the two never hold at once (in a near tie both may back off: a refusal,
    never an overlap). A dead process's lock on this machine is reclaimed; another machine's never automatically."""

    def __init__(self, root: Path, kind: str, job: str, unlock_cmd: str = "uv run nqbot unlock"):
        super().__init__(shared_path(root, kind, job), job, unlock_cmd)
        self.exclusive = lock_path(root, kind)

    def acquire(self) -> None:
        super().acquire()
        for attempt in range(2):
            if not self.exclusive.exists():
                return
            holder = read_lock(self.exclusive)
            if attempt == 0 and self._stale(holder):
                take_stale(self.exclusive, holder)
                continue
            self.release()
            raise LockHeld(self.exclusive, holder, self.unlock_cmd)
        self.release()
        raise LockHeld(self.exclusive, read_lock(self.exclusive), self.unlock_cmd)
