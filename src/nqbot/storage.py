"""The external SSD: find it by its marker file, build paths.*, check free space, write atomically.

SPEC §11: all project data lives on the SSD under paths.root. The drive is found by scanning mounted
drives for the marker file .nqbot_drive (never by drive letter); every path is built from paths.*.
"""

from __future__ import annotations

import errno
import gc
import json
import logging
import os
import shutil
import time
import traceback
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePath
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

MARKER = ".nqbot_drive"
GB = 1024**3
# Windows refuses to replace a file another process has open (an antivirus scan of a freshly written parquet of
# tens of MB, `nqbot status` reading it): retry the rename with backoff for about 30 s before failing (§11 Overnight
# jobs: atomic outputs; presd_fixes D5).
REPLACE_RETRY_TOTAL_SEC = 30.0
REPLACE_RETRY_FIRST_SEC = 0.1
REPLACE_RETRY_MAX_SEC = 5.0
_sleep = time.sleep  # tests replace it
# A USB drop of the SSD (owner 2026-10-11: the drive disappeared and came back within ~3-4 s, a dozen times an
# evening): a write that fails because the device vanished waits up to SSD_DROP_WAIT_SEC for the drive to come back,
# then is retried once. Every drop is logged to SSD_EVENT_LOG on the internal drive (writable while the SSD is gone)
# and, once it is back, to <root>/logs/ssd_events.log.
SSD_DROP_WAIT_SEC = 30.0
SSD_DROP_POLL_SEC = 0.5
# Owner 2026-10-13: on 10-12 03:51 the retry ran 3 ms after the drop because the old volume's folder still looked
# present, and failed again (the volume came back 4 s later). The drive counts as back only when a freshly opened
# probe file can be written, fsynced and removed SSD_STABLE_PROBES times in a row (SSD_DROP_POLL_SEC apart), and
# never sooner than SSD_DROP_MIN_WAIT_SEC after the drop.
SSD_STABLE_PROBES = 4
SSD_DROP_MIN_WAIT_SEC = 2.0
PROBE_PREFIX = ".nqbot_probe_"  # a .tmp name: backups skip it (backup.TEMP_FILTER)
DROP_ERRNOS = frozenset({errno.EBADF, errno.EIO, errno.ENODEV, errno.ENXIO})
# Windows: not ready, invalid handle, device not existing / connected, I/O device error, hardware error
DROP_WINERRORS = frozenset({21, 6, 55, 433, 1167, 1117, 483})
SSD_EVENT_LOG = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "nqbot" / "ssd_events.log"
_log = logging.getLogger("nqbot")


class SSDNotFound(Exception):
    pass


def candidate_drives() -> list[Path]:
    """Mounted drive roots to scan for the marker, never the system drive (tests replace this)."""
    if os.name == "nt":
        system = os.environ.get("SystemDrive", "C:").rstrip("\\").upper()
        # os.listdrives lists only drives that exist, so a dead network mapping isn't probed letter by letter.
        roots = [Path(d) for d in os.listdrives() if not d.upper().startswith(system)]
    else:
        roots = [p for base in ("/mnt", "/media", "/Volumes") for p in _subdirs(base)]
    return [r for r in roots if _exists(r)]


def _subdirs(base: str) -> list[Path]:
    try:
        return [p for p in Path(base).iterdir() if p.is_dir()]
    except OSError:
        return []


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:  # e.g. an empty card reader slot
        return False


def find_drive() -> Path | None:
    found = [d for d in candidate_drives() if _exists(d / MARKER)]
    if len(found) > 1:
        raise SSDNotFound(f"several drives carry the marker {MARKER}: {[str(d) for d in found]}; remove the stray ones")
    return found[0] if found else None


@dataclass(frozen=True)
class Paths:
    drive: Path
    root: Path
    data_dir: Path
    raw_dir: Path
    live_dir: Path
    models_dir: Path
    reports_dir: Path
    log_dir: Path
    tmp_dir: Path

    @classmethod
    def under(cls, drive: Path, rel_root: str) -> Paths:
        rel = PurePath(rel_root)
        if rel.is_absolute() or rel.drive or rel.root or ".." in rel.parts:
            raise SSDNotFound(f"paths.root must be a folder relative to the SSD (e.g. nqbot/), not {rel_root!r}")
        root = drive / rel
        data = root / "data"
        return cls(
            drive=drive,
            root=root,
            data_dir=data,
            raw_dir=data / "raw",
            live_dir=data / "live",
            models_dir=root / "models",
            reports_dir=root / "reports",
            log_dir=root / "logs",
            tmp_dir=root / "tmp",
        )

    def mounted(self) -> bool:
        return _exists(self.drive / MARKER)


def resolve_paths(settings: dict[str, Any]) -> Paths:
    rel_root = (settings.get("paths") or {}).get("root")
    if not rel_root:
        raise SSDNotFound("paths.root is not set in config/settings.yaml (set at step 4b, SSD day)")
    drive = find_drive()
    if drive is None:
        raise SSDNotFound(f"SSD marker {MARKER} not found on any mounted drive (is the SSD plugged in?)")
    return Paths.under(drive, rel_root)


def free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / GB


def replace_retry(src: str | Path, dst: str | Path) -> None:
    """os.replace that waits out a lock (an antivirus scan, a reader) on Windows: backoff from 0.1 s, doubling up to
    5 s a wait, until about 30 s have been waited (presd_fixes 17, D5)."""
    waited, wait = 0.0, REPLACE_RETRY_FIRST_SEC
    while True:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if waited >= REPLACE_RETRY_TOTAL_SEC:
                raise
            _sleep(wait)
            waited += wait
            wait = min(wait * 2, REPLACE_RETRY_MAX_SEC)


@contextmanager
def atomic_path(path: Path) -> Iterator[Path]:
    """Temp file in the destination folder (same drive) -> fsync -> rename (§11 Overnight jobs).

    Yields a temp path that does not exist yet (writers such as databento's get_range refuse an existing file);
    on success the written file is fsynced and renamed onto `path`, on any error the temp file is removed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        yield tmp
        with open(tmp, "rb+") as f:
            os.fsync(f.fileno())
        replace_retry(tmp, path)
    except BaseException as e:
        # A writer that failed mid-write may still hold the temp file open through its frame in the traceback,
        # and Windows refuses to delete an open file: drop those frames' locals (closing it) before removing.
        traceback.clear_frames(e.__traceback__)
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass
        except OSError:
            gc.collect()
            try:
                os.remove(tmp)
            except OSError:
                pass  # swept by remove_stale_temps before the next write to this path
        raise


def remove_stale_temps(path: Path) -> None:
    """Delete temp files an earlier atomic write to `path` left behind (killed process, open handle on Windows)."""
    if path.parent.exists():
        for stale in path.parent.glob(f".{path.name}.*.tmp"):
            try:
                stale.unlink()
            except OSError:
                pass


def _anchor_ok(path: Path) -> bool:
    try:
        return Path(path.anchor or path).exists() and path.parent.exists()
    except OSError:
        return False


def is_device_drop(e: BaseException, path: Path) -> bool:
    """Did this error come from the drive vanishing (not from a full disk, a bad name or a locked file)?"""
    if not isinstance(e, OSError) or isinstance(e, PermissionError):
        return False
    if getattr(e, "winerror", None) in DROP_WINERRORS or e.errno in DROP_ERRNOS:
        return True
    return isinstance(e, (FileNotFoundError, NotADirectoryError)) and not _anchor_ok(path)


def log_ssd_event(op: str, path: Path, detail: str) -> None:
    """One line per SSD event, to the internal drive first (always writable), then the logger."""
    line = f"{datetime.now(timezone.utc).isoformat()} pid {os.getpid()} {op} {path}: {detail}"
    try:
        SSD_EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(SSD_EVENT_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    _log.warning("SSD event: %s", line)


def _probe(path: Path) -> bool:
    """Can a new file be written, fsynced and removed next to `path` right now (fresh handle each time)?"""
    folder = path.parent
    while not _anchor_ok(folder / "x"):
        if folder.parent == folder:
            return False
        folder = folder.parent
    probe = folder / f"{PROBE_PREFIX}{os.getpid()}_{uuid.uuid4().hex}.tmp"
    try:
        with open(probe, "wb") as f:
            f.write(b"ok")
            f.flush()
            os.fsync(f.fileno())
        os.remove(probe)
        return True
    except OSError:
        try:
            os.remove(probe)
        except OSError:
            pass
        return False


def wait_for_drive(path: Path, wait: float = SSD_DROP_WAIT_SEC) -> float | None:
    """Seconds until the drive holding `path` is back and stable (SSD_STABLE_PROBES good probes in a row, at least
    SSD_DROP_MIN_WAIT_SEC after the drop), or None after `wait`."""
    waited, good = 0.0, 0
    while waited <= wait:
        good = good + 1 if _probe(path) else 0
        if good >= SSD_STABLE_PROBES and waited >= SSD_DROP_MIN_WAIT_SEC:
            return waited
        _sleep(SSD_DROP_POLL_SEC)
        waited += SSD_DROP_POLL_SEC
    return None


def drop_retry(op: str, path: Path, attempt: Callable[[], Any], cleanup: Callable[[], None] | None = None) -> Any:
    """Run `attempt`; if it fails because the drive vanished, log it, wait for the drive and run it once more."""
    try:
        return attempt()
    except OSError as e:
        if not is_device_drop(e, path):
            raise
        log_ssd_event(op, path, f"device dropped ({type(e).__name__}: {e}); waiting up to {SSD_DROP_WAIT_SEC:.0f} s")
        back = wait_for_drive(path)
        if back is None:
            log_ssd_event(op, path, f"drive not back after {SSD_DROP_WAIT_SEC:.0f} s: giving up")
            raise
        log_ssd_event(op, path, f"drive back and stable after {back:.1f} s ({SSD_STABLE_PROBES} fresh-handle probes): "
                                "retrying once")
        if cleanup is not None:
            try:
                cleanup()
            except OSError:
                pass
        try:
            out = attempt()
        except OSError as e2:
            log_ssd_event(op, path, f"retry failed ({type(e2).__name__}: {e2})")
            raise
        log_ssd_event(op, path, "retry succeeded")
        _copy_event_to_ssd(path)
        return out


def _copy_event_to_ssd(path: Path) -> None:
    """Once the drive is back, keep the drop in <root>/logs/ssd_events.log too (best effort)."""
    for parent in path.parents:
        if parent.name == "data" or (parent / "logs").is_dir():
            root = parent.parent if parent.name == "data" else parent
            try:
                lines = SSD_EVENT_LOG.read_text(encoding="utf-8").splitlines()[-4:]
                with open(root / "logs" / "ssd_events.log", "a", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
            except OSError:
                pass
            return


def atomic_write_bytes(path: Path, data: bytes) -> None:
    def once() -> None:
        with atomic_path(path) as tmp:
            tmp.write_bytes(data)

    drop_retry("atomic write", path, once, cleanup=lambda: remove_stale_temps(path))


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_text(path, json.dumps(obj, indent=2, sort_keys=True, default=str))


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    """Append one line and fsync it (call ledgers: every paid or failed call is kept, CLAUDE.md rule 2)."""
    line = json.dumps(row, sort_keys=True, default=str) + "\n"

    def once() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if retrying and path.exists() and _last_line(path).rstrip("\r\n") == line.rstrip("\n"):
            return  # the first try did land before the drive dropped: never append it twice
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    retrying = False

    def again() -> None:
        nonlocal retrying
        retrying = True

    drop_retry("append", path, once, cleanup=again)


def _last_line(path: Path) -> str:
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - 65536))
        tail = f.read().decode("utf-8", errors="replace")
    lines = tail.splitlines(keepends=True)
    return lines[-1] if lines else ""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
