"""The external SSD: find it by its marker file, build paths.*, check free space, write atomically.

SPEC §11: all project data lives on the SSD under paths.root. The drive is found by scanning mounted
drives for the marker file .nqbot_drive (never by drive letter); every path is built from paths.*.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any

MARKER = ".nqbot_drive"
GB = 1024**3
# Windows refuses to replace a file another process has open (an antivirus scan, `nqbot status`
# reading it): retry the rename a few times before failing (§11 Overnight jobs: atomic outputs).
REPLACE_RETRIES = 20
REPLACE_RETRY_SEC = 0.1


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


def _replace(src: str, dst: Path) -> None:
    for attempt in range(REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == REPLACE_RETRIES - 1:
                raise
            time.sleep(REPLACE_RETRY_SEC)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Temp file in the destination folder (same drive) -> fsync -> rename (§11 Overnight jobs)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        _replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_text(path, json.dumps(obj, indent=2, sort_keys=True, default=str))
