"""`nqbot status-push` (owner 2026-10-28, SPEC §11 Overnight jobs): a progress snapshot readable from the phone.

Writes `nqbot status` (SSD, locks, jobs, the resume queue), the clock check and the latest preflight warnings to
`<log_dir>/status_<host>.txt`, then copies that one file with rclone to
`<backup.rclone_remote>:<backup.drive_folder>/<backup.status_folder>/status_<host>.txt` (the Drive app shows it).
Run hourly by the task `nqbot status push` (scripts/register_tasks_pc.ps1). Reads only, apart from that file."""

from __future__ import annotations

import json
import socket
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from nqbot.storage import Paths, atomic_write_text

ET = ZoneInfo("America/New_York")


def latest_preflight(reports_dir: Path) -> list[str]:
    files = sorted((reports_dir / "preflight").glob("preflight_*.json")) if (reports_dir / "preflight").exists() else []
    if not files:
        return ["preflight: no report yet"]
    try:
        rep = json.loads(files[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [f"preflight: unreadable report {files[-1].name}"]
    warns = rep.get("warnings") or []
    head = f"preflight {rep.get('checked_at', '?')}: {len(warns)} warning(s)"
    return [head] + [f"  WARN {w}" for w in warns]


def snapshot(paths: Paths, settings: dict[str, Any], *, clock: Callable[[], tuple[list[str], list[str]]],
             now: datetime | None = None, host: str | None = None) -> str:
    from nqbot.jobs.queue import queue_lines
    from nqbot.jobs.status import status_lines

    now = now or datetime.now(ET)
    host = host or socket.gethostname()
    warn, ok = clock()
    lines = [f"nqbot status on {host} at {now.astimezone(ET):%Y-%m-%d %H:%M:%S} ET", ""]
    lines += status_lines(paths) + queue_lines(paths, settings)
    lines += [f"clock WARN {w}" for w in warn] + [f"clock ok   {o}" for o in ok]
    lines += latest_preflight(paths.reports_dir)
    return "\n".join(lines) + "\n"


def push(paths: Paths, settings: dict[str, Any], *, clock: Callable[[], tuple[list[str], list[str]]],
         rclone: Any = None, echo: Callable[[str], None] = print, host: str | None = None) -> int:
    from nqbot.backup import SubprocessRclone

    host = host or socket.gethostname()
    text = snapshot(paths, settings, clock=clock, host=host)
    local = paths.log_dir / f"status_{host}.txt"
    atomic_write_text(local, text)
    b = settings["backup"]
    rclone = rclone or SubprocessRclone(b["rclone_exe"])
    dst = f"{b['rclone_remote']}:{b['drive_folder']}/{b['status_folder']}/status_{host}.txt"
    code, out = rclone.quick(["copyto", str(local), dst])
    echo(text.rstrip())
    if code != 0:
        echo(f"status-push: rclone copyto failed ({code}): {out.strip()[:300]} (local copy: {local})")
        return 1
    echo(f"status-push: {local} -> {dst}")
    return 0
