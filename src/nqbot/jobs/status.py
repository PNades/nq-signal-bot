"""`nqbot status`: SSD use, the job locks, and each job's progress, peak memory and estimated nights left (§11)."""

from __future__ import annotations

import json
import math
import statistics
import shutil
from datetime import datetime

from nqbot.jobs import estimate as est
from nqbot.jobs.lock import ALL_LOCKS, holder_dead_here, holders
from nqbot.jobs.runner import p90, plan_progress, read_pool_progress
from nqbot.storage import GB, Paths


def job_lines(paths: Paths, now: datetime | None = None) -> list[str]:
    from nqbot.jobs.queue import read_queue

    now = now or datetime.now(est.ET)
    jobs_dir = paths.data_dir / "jobs"
    queue = set(read_queue(paths))
    lines = []
    for spath in sorted(jobs_dir.glob("*/state.json")) if jobs_dir.exists() else []:
        with open(spath, encoding="utf-8") as f:
            state = json.load(f)
        name = spath.parent.name
        done = state.get("done", {})
        n_done, total = plan_progress(state)
        left = total - n_done
        line = f"{name}: {n_done}/{total} chunks done"
        # owner 2026-11-19 (SPEC §13 Q-status-eta): the 13c driver and the feature builds by what their chunks are
        queued = name in queue or (name in est.BUILD_SLOTS and est.TUNE_JOB in queue)
        special = _special_estimate(paths, jobs_dir, name, state, now, queued) if left else None
        chunk_p90 = p90([d["sec"] for d in done.values()])
        if special:
            line += special
        elif left and chunk_p90:
            hours_left = left * chunk_p90 / 3600
            line += f", p90 chunk {chunk_p90:.0f}s, ~{hours_left:.1f} h left"
            window_h = _last_window_hours(state)
            if window_h:
                line += f", ~{math.ceil(hours_left / window_h)} night(s) left"
            fin = est.plain_finish(state, chunk_p90 / 3600, left, now, queued)
            if fin:
                line += f", {fin}"
        elif not left:
            line += " (complete)"
        pool = read_pool_progress(paths, name) if left else {}
        if pool.get("pass_total") and int(pool.get("pass_done") or 0) < int(pool["pass_total"]):
            # owner 2026-11-13 review: pass 2 is one chunk of many sessions, so "0/1 chunks" hid its progress
            line += f"; pass {pool.get('pass')}: {pool['pass_done']}/{pool['pass_total']} sessions done"
        peaks = [d["peak_gb"] for d in done.values() if d.get("peak_gb")]
        if peaks:  # two backfills may run side by side on the 16 GB laptop (§11 Low memory)
            line += f", peak {max(peaks):.1f} GB RAM"
        lines.append(line)
    return lines or ["no jobs yet"]


def _read(path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _special_estimate(paths: Paths, jobs_dir, name: str, state: dict, now: datetime, queued: bool) -> str | None:
    """The 13c driver's and the feature builds' own estimates (`jobs/estimate.py`); None: the plain one. Display only:
    an estimate that can't be made says so instead of stopping `nqbot status`."""
    try:
        if name == est.TUNE_JOB:
            plan = _read(jobs_dir / name / "tune.json")
            return est.tune_line(state, plan, _read(jobs_dir / name / "winners.json"), now, queued) if plan else None
        if name in est.BUILD_SLOTS:
            other = next(s for s in est.BUILD_SLOTS if s != name)
            return est.build_line(state, _read(jobs_dir / other / "state.json") or None,
                                  read_pool_progress(paths, name), now, queued)
    except (KeyError, ValueError, TypeError, ZeroDivisionError) as e:
        return f"; estimate unavailable ({type(e).__name__}: {e})"
    return None


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


def switch_lines(paths: Paths | None = None) -> list[str]:
    """Whether two feature builds may run at once on this machine (`parallel_builds` in config/local.yaml, owner
    2026-11-13: status shows it, with the file read; owner 2026-11-13 review: builds read the pinned worktree's copy,
    so a different value there is shown as a warning)."""
    from pathlib import Path

    from nqbot import build_pin, config   # through the module, so the test fixture's stand-in applies

    def word(v: bool) -> str:
        return "on" if v else "off"

    try:
        on = config.parallel_builds()
    except config.ConfigError as e:
        return [f"two builds at once (parallel_builds in config/local.yaml): unreadable ({e})"]
    lines = [f"two builds at once (parallel_builds in {config.CONFIG_DIR / 'local.yaml'}): {word(on)}"]
    try:
        pin = build_pin.read_pin(paths) if paths is not None else None
    except (OSError, ValueError):
        pin = None
    if pin and pin.get("host") == build_pin.socket.gethostname():
        wt = Path(pin["path"]) / "config"
        if not build_pin._same_path(wt, config.CONFIG_DIR):
            try:
                theirs = config.parallel_builds(wt)
            except config.ConfigError as e:
                lines.append(f"  WARNING: the build worktree's copy ({wt / 'local.yaml'}) is unreadable: {e}")
            else:
                if theirs != on:
                    lines.append(f"  WARNING: builds read the build worktree's copy, {wt / 'local.yaml'}: "
                                 f"{word(theirs)} (moving the pin copies this folder's over it)")
    return lines


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
    return lines + lock_lines(paths) + switch_lines(paths) + job_lines(paths)
