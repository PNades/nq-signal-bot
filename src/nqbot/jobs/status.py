"""`nqbot status`: SSD use, the job locks, and each job's progress, peak memory and estimated nights left (§11)."""

from __future__ import annotations

import json
import math
import statistics
import shutil
from datetime import datetime

from nqbot.jobs.lock import ALL_LOCKS, holder_dead_here, holders
from nqbot.jobs.runner import p90, plan_progress
from nqbot.storage import GB, Paths


def job_lines(paths: Paths) -> list[str]:
    jobs_dir = paths.data_dir / "jobs"
    lines = []
    for spath in sorted(jobs_dir.glob("*/state.json")) if jobs_dir.exists() else []:
        with open(spath, encoding="utf-8") as f:
            state = json.load(f)
        done = state.get("done", {})
        n_done, total = plan_progress(state)
        left = total - n_done
        chunk_p90 = p90([d["sec"] for d in done.values()])
        line = f"{spath.parent.name}: {n_done}/{total} chunks done"
        if left and chunk_p90:
            hours_left = left * chunk_p90 / 3600
            line += f", p90 chunk {chunk_p90:.0f}s, ~{hours_left:.1f} h left"
            window_h = _last_window_hours(state)
            if window_h:
                line += f", ~{math.ceil(hours_left / window_h)} night(s) left"
        elif not left:
            line += " (complete)"
        peaks = [d["peak_gb"] for d in done.values() if d.get("peak_gb")]
        if peaks:  # two backfills may run side by side on the 16 GB laptop (§11 Low memory)
            line += f", peak {max(peaks):.1f} GB RAM"
        lines.append(line)
    return lines or ["no jobs yet"]


def lock_lines(paths: Paths) -> list[str]:
    """One line per holder of each job lock, exclusive ("held by") or shared ("shared by", two builds at once, owner
    2026-11-12); "free" only when the lock has no holder of either kind (owner 2026-11-13: a shared lock read "free"
    above its "shared by" lines)."""
    lines = []
    for kind in ALL_LOCKS:
        found = holders(paths.root, kind)
        if not found:
            lines.append(f"{kind} job lock: free")
            continue
        for path, h, mode in found:
            shared = mode == "shared"
            if not h:
                lines.append(f"{kind} job lock: unreadable {'shared ' if shared else ''}lock file {path}")
                continue
            line = (f"{kind} job lock: {'shared' if shared else 'held'} by job {h.get('job')!r} (pid {h.get('pid')} "
                    f"on {h.get('host')}, since {h.get('started_utc')})")
            if holder_dead_here(h):  # owner 2026-10-31: after a restart the dead run's lock read as still held
                line += (": that process is no longer running (stale; the next job run "
                         f"{'removes it' if shared else 'takes it over'})")
            lines.append(line)
    return lines


def switch_lines() -> list[str]:
    """Whether two feature builds may run at once on this machine (`parallel_builds` in config/local.yaml, owner
    2026-11-13: status shows it)."""
    from nqbot import config   # read through the module, so the test fixture's stand-in applies

    try:
        on = config.parallel_builds()
    except config.ConfigError as e:
        return [f"two builds at once (parallel_builds in config/local.yaml): unreadable ({e})"]
    return [f"two builds at once (parallel_builds in config/local.yaml): {'on' if on else 'off'}"]


def _last_window_hours(state: dict) -> float | None:
    """Median run window (start -> --until) over past runs: the best guess of one night's budget."""
    hours = []
    for run in state.get("runs") or []:
        start, until = (datetime.fromisoformat(run[k]) for k in ("start", "until"))
        h = (until - start).total_seconds() / 3600
        if h > 0:
            hours.append(h)
    return statistics.median(hours) if hours else None


def status_lines(paths: Paths) -> list[str]:
    usage = shutil.disk_usage(paths.drive)
    lines = [
        f"SSD {paths.drive} (root {paths.root}): {usage.used / GB:.0f} GB used, "
        f"{usage.free / GB:.0f} GB free of {usage.total / GB:.0f} GB"
    ]
    return lines + lock_lines(paths) + switch_lines() + job_lines(paths)
