"""Console output that survives a pulled or re-mounted SSD (owner 2026-10-16).

A scheduled task's stdout and stderr are a log file on the SSD (`scripts/nqbot_task.ps1`, cmd.exe's redirect). On
2026-10-15 the SSD re-mounted dozens of times during the recording (each re-mount kills the open handle) and was then
pulled: the recorder switched its parts to the internal drive as designed, printed one line about it, the write to
the dead log handle raised OSError, and the process died with exit 1 (nothing more recorded).

`resilient_console()` wraps sys.stdout and sys.stderr for the length of a command. A write or flush that fails with
OSError is logged to the SSD event log and the output carries on in a freshly opened copy of the task's own log
(`NQBOT_TASK_LOG`, appended: the SSD is often back within seconds), or, while that can't be opened, in a file of the
same name in the internal drive's fallback logs folder (moved onto the SSD by `nqbot fallback-move`, merged line by
line); from the fallback the task log is tried again every REOPEN_EVERY_SEC. If nothing can be written the output is
dropped. Printing never raises.
"""

from __future__ import annotations

import io
import os
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import IO, Any, Iterator

TASK_LOG_ENV = "NQBOT_TASK_LOG"  # set by scripts/nqbot_task.ps1: the full path of the task's log file
REOPEN_EVERY_SEC = 60.0  # on the fallback, how often the task log on the SSD is tried again


def fallback_log_dir() -> Path:
    from nqbot.fallback import FALLBACK_DRIVE

    return FALLBACK_DRIVE / "nqbot" / "logs"


def _event(line: str) -> None:
    """Straight to the SSD event log file (the logger writes to the very streams being repaired)."""
    from nqbot.storage import SSD_EVENT_LOG

    try:
        SSD_EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(SSD_EVENT_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().astimezone().isoformat()} pid {os.getpid()} console: {line}\n")
    except OSError:
        pass


class _Shared:
    """Where both streams write once the original failed (one file keeps their order)."""

    def __init__(self, log_dir: Path | None = None, clock=time.monotonic):
        self.log_dir = log_dir
        self.clock = clock
        self.lock = threading.Lock()
        self.file: IO[str] | None = None
        self.path: Path | None = None
        self.on_fallback = False
        self.last_try = 0.0

    def _task_log(self) -> Path | None:
        task = os.environ.get(TASK_LOG_ENV)
        return Path(task) if task else None

    def _fallback_path(self) -> Path:
        task = self._task_log()
        name = task.name if task else f"console_{datetime.now():%Y%m%d}.txt"
        return (self.log_dir or fallback_log_dir()) / name

    @staticmethod
    def _open(path: Path) -> IO[str] | None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            return open(path, "a", encoding="utf-8", errors="replace")
        except OSError:
            return None

    def _close(self) -> None:
        if self.file is not None:
            try:
                self.file.close()
            except OSError:
                pass
        self.file = None

    def _choose(self, why: str) -> None:
        """Open the task log afresh, else the fallback; one event line per switch."""
        self._close()
        self.last_try = self.clock()
        task = self._task_log()
        f = self._open(task) if task is not None else None
        if f is not None:
            self.file, self.path, self.on_fallback = f, task, False
        else:
            path = self._fallback_path()
            self.file, self.path, self.on_fallback = self._open(path), path, True
        where = self.path if self.file is not None else "nowhere (nothing writable)"
        _event(f"{why}; output continues in {where}")
        if self.file is not None:
            try:
                self.file.write(f"--- {datetime.now().astimezone().isoformat()}: {why}; continuing here\n")
                self.file.flush()
            except OSError:
                self._close()

    def write(self, s: str, why: str | None) -> None:
        with self.lock:
            if why is not None and (self.file is None or self.path is None):
                self._choose(why)
            elif self.on_fallback and self._task_log() is not None and self.clock() - self.last_try >= REOPEN_EVERY_SEC:
                self.last_try = self.clock()
                f = self._open(self._task_log())
                if f is not None:
                    self._close()
                    self.file, self.path, self.on_fallback = f, self._task_log(), False
                    _event(f"task log writable again: output continues in {self.path}")
            if self.file is None:
                if self.clock() - self.last_try >= REOPEN_EVERY_SEC:
                    self._choose("console output had nowhere to go")
                if self.file is None:
                    return
            for attempt in range(2):
                try:
                    self.file.write(s)
                    self.file.flush()
                    return
                except OSError as e:
                    if attempt:
                        self._close()
                        return
                    self._choose(f"console log {self.path} write failed ({type(e).__name__}: {e})")
                    if self.file is None:
                        return


class SafeStream(io.TextIOBase):
    """A text stream that tries the original and then the shared target; never raises OSError."""

    def __init__(self, original: Any, which: str, shared: _Shared):
        self._original = original
        self._which = which
        self._shared = shared
        self._broken = False

    def write(self, s: str) -> int:
        if not self._broken:
            try:
                self._original.write(s)
                self._original.flush()  # tasks run unbuffered anyway: a failure shows on this line, not later
                return len(s)
            except OSError as e:
                self._broken = True
                self._shared.write(s, f"console {self._which} write failed ({type(e).__name__}: {e})")
                return len(s)
        self._shared.write(s, None)
        return len(s)

    def flush(self) -> None:
        if not self._broken:
            try:
                self._original.flush()
            except OSError as e:
                self._broken = True
                self._shared.write("", f"console {self._which} flush failed ({type(e).__name__}: {e})")

    def writable(self) -> bool:
        return True

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return getattr(self._original, "encoding", "utf-8")

    @property
    def errors(self) -> str | None:  # type: ignore[override]
        return getattr(self._original, "errors", None)

    def isatty(self) -> bool:
        try:
            return bool(self._original.isatty())
        except (OSError, ValueError, AttributeError):
            return False

    def fileno(self) -> int:
        return self._original.fileno()

    def __getattr__(self, name: str) -> Any:  # buffer, reconfigure, ...: the original's
        return getattr(self._original, name)


@contextmanager
def resilient_console(log_dir: Path | None = None, clock=time.monotonic) -> Iterator[None]:
    """sys.stdout / sys.stderr wrapped in SafeStream until the block ends (then put back as they were)."""
    if isinstance(sys.stdout, SafeStream) or isinstance(sys.stderr, SafeStream):
        yield
        return
    shared = _Shared(log_dir, clock)
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = SafeStream(old_out, "stdout", shared), SafeStream(old_err, "stderr", shared)
    try:
        yield
    finally:
        sys.stdout, sys.stderr = old_out, old_err
        with shared.lock:
            shared._close()
