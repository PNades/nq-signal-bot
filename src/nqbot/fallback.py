"""Recording fallback to the laptop's internal drive (owner 2026-10-11, SPEC §11 Storage and §13: an exception to "all
data on the SSD", for the step-7 session recording only).

If the SSD isn't reachable or writable when `step7 stream-session --fallback-local` starts, or keeps failing during
the session, the recording goes to FALLBACK_DRIVE, laid out exactly like the SSD (`<rel_root>/data/raw/skylit/...`,
`reports/`, `logs/`). After the close `nqbot fallback-move` (also tried by the recorder itself once it ends) moves it
onto the SSD: a file the SSD lacks is copied, fsynced, checked and only then deleted locally; an identical file is
just deleted locally; a jsonl ledger gets its missing lines appended; anything else that differs stays put and is
listed."""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from nqbot.storage import MARKER, Paths, log_ssd_event

FALLBACK_DRIVE = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "nqbot" / "fallback"


def fallback_paths(rel_root: str, drive: Path | None = None) -> Paths:
    return Paths.under(drive or FALLBACK_DRIVE, rel_root)


def ssd_writable(paths: Paths) -> tuple[bool, str]:
    """The SSD's marker is there and a small file can be written, fsynced, read back and removed under the root."""
    try:
        if not paths.mounted():
            return False, f"marker {MARKER} not found on {paths.drive}"
    except OSError as e:
        return False, f"{type(e).__name__}: {e}"
    return folder_writable(paths.tmp_dir)


def folder_writable(folder: Path) -> tuple[bool, str]:
    """A small file can be written, fsynced, read back and removed in `folder` (created if missing)."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / f".probe_{uuid.uuid4().hex}"
        data = os.urandom(4096)
        with open(probe, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        ok = probe.read_bytes() == data
        probe.unlink()
        return (ok, "writable" if ok else "read-back differs")
    except OSError as e:
        return False, f"{type(e).__name__}: {e}"


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class MoveResult:
    moved: list[str] = field(default_factory=list)
    already_there: list[str] = field(default_factory=list)
    merged_lines: dict[str, int] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)


def move_fallback(src: Paths, dst: Paths, echo: Callable[[str], None] = print) -> MoveResult:
    """Move everything under src.root onto dst.root (see the module doc). Safe to run again after a failure."""
    res = MoveResult()
    if not src.root.exists():
        echo(f"nothing to move: {src.root} doesn't exist")
        return res
    files = sorted(p for p in src.root.rglob("*") if p.is_file())
    for p in files:
        rel = p.relative_to(src.root)
        target = dst.root / rel
        if target.exists():
            if target.stat().st_size == p.stat().st_size and _sha(target) == _sha(p):
                p.unlink()
                res.already_there.append(str(rel))
            elif p.suffix == ".jsonl" or p.suffix == ".log" or p.name.endswith(".txt"):
                have = set(target.read_text(encoding="utf-8", errors="replace").splitlines())
                extra = [line for line in p.read_text(encoding="utf-8", errors="replace").splitlines()
                         if line.strip() and line not in have]
                if extra:
                    with open(target, "a", encoding="utf-8") as f:
                        f.write("\n".join(extra) + "\n")
                        f.flush()
                        os.fsync(f.fileno())
                res.merged_lines[str(rel)] = len(extra)
                p.unlink()
            else:
                res.conflicts.append(str(rel))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
        shutil.copyfile(p, tmp)
        with open(tmp, "rb+") as f:
            os.fsync(f.fileno())
        if _sha(tmp) != _sha(p):
            tmp.unlink()
            res.conflicts.append(f"{rel} (copy differs, kept locally)")
            continue
        os.replace(tmp, target)
        p.unlink()
        res.moved.append(str(rel))
    for d in sorted((x for x in src.root.rglob("*") if x.is_dir()), key=lambda x: -len(x.parts)):
        try:
            d.rmdir()
        except OSError:
            pass
    echo(f"fallback move: {len(res.moved)} moved, {len(res.already_there)} already on the SSD, "
         f"{sum(res.merged_lines.values())} log/ledger lines merged, {len(res.conflicts)} conflicts kept locally")
    for c in res.conflicts:
        echo(f"  conflict: {c}")
    if res.moved or res.merged_lines:
        log_ssd_event("fallback move", dst.root, f"{len(res.moved)} files moved from {src.root}")
    return res
