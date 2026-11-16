"""Back up the panel's files (owner 2026-11-15, SPEC §9b): `<data_dir>/panel/` to Google Drive
`<drive_folder>/panel/` (settings.yaml `backup`: remote `gdrive`, folder "NQ bot backup"), so a lost SSD doesn't mean
re-entering accounts and presets. `rclone copy --checksum` then `rclone check`; the folder is a few kilobytes.
It takes only its own small lock (never the Skylit job lock the 13c builds hold). Run by hand with `nqbot ui backup`,
and by the hourly `nqbot status-push` task. The files hold no secret (the schema refuses them).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nqbot.jobs.lock import JobLock, LockHeld
from nqbot.storage import Paths
from nqbot.ui.panel_store import LOCK_FILE, PANEL_DIR

BACKUP_LOCK = ".panel_backup.lock"
EXCLUDES = ("--exclude", LOCK_FILE, "--exclude", BACKUP_LOCK, "--exclude", ".*.tmp")


def remote_dir(settings: dict[str, Any]) -> str:
    b = settings["backup"]
    return f"{b['rclone_remote']}:{b['drive_folder']}/{PANEL_DIR}"


def backup(paths: Paths, settings: dict[str, Any], *, rclone: Any = None, echo: Callable[[str], None] = print) -> int:
    from nqbot.backup import BackupRefused, SubprocessRclone, preflight
    from nqbot.rclone_path import RcloneNotFound, find_rclone

    src = paths.data_dir / PANEL_DIR
    if not src.is_dir():
        echo(f"panel backup: nothing to back up yet ({src} doesn't exist; the panel creates it on first use)")
        return 0
    if rclone is None:
        try:
            exe, how = find_rclone(settings)
        except RcloneNotFound as e:
            echo(f"panel backup: {e}")
            return 1
        echo(f"rclone: {exe} ({how})")
        rclone = SubprocessRclone(exe)
    try:
        preflight(rclone, settings["backup"]["rclone_remote"])
    except BackupRefused as e:
        echo(f"panel backup: {e}")
        return 1
    lock = JobLock(src / BACKUP_LOCK, "panel_backup", unlock_cmd="delete it only if no panel backup runs")
    try:
        lock.acquire()
    except LockHeld as e:
        echo(f"panel backup: another backup is running ({e})")
        return 1
    try:
        dst = remote_dir(settings)
        code, out = rclone.quick(["copy", "--checksum", *EXCLUDES, str(src), dst])
        if code != 0:
            echo(f"panel backup: rclone copy failed ({code}): {out.strip()[:300]}")
            return 1
        code, out = rclone.quick(["check", "--one-way", *EXCLUDES, str(src), dst])
        if code != 0:
            echo(f"panel backup: rclone check found differences ({code}): {out.strip()[:300]}")
            return 1
        echo(f"panel backup: {src} -> {dst} (copied and checked)")
        return 0
    finally:
        lock.release()
