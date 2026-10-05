"""`nqbot run <job> --until HH:MM`: resumable chunks on the SSD (SPEC §11 Overnight jobs, Low memory).

A job is an ordered list of chunk ids plus `run_chunk`. Progress is saved after every chunk, so the next
run resumes where the last one stopped. Before every chunk the runner checks: the SSD marker is still
there, free space >= min_free_gb, the job-lock window (once enforced, step 22), and that the chunk's
measured 90th-percentile duration fits before --until. Long chunks call `ctx.should_stop()` between
their own work units (§11: work is checked against the clock between work units) and raise `StopChunk`
to give up the chunk cleanly; it then re-runs from its start next time.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo

import psutil

from nqbot.jobs.lock import LOCK_NAME, JobLock
from nqbot.storage import GB, Paths, atomic_write_json, free_gb

ET = ZoneInfo("America/New_York")
DEFAULT_UNTIL = "07:30"  # §11: default --until 07:30
P90 = 0.90  # §11: a chunk starts only if its measured 90th-percentile duration fits
SPARE_CORES = 2  # §11: max_workers default = all cores but 2
RAM_RESERVE_GB = 4  # §11 Low memory: workers capped at floor((free RAM - 4 GB) / peak per session)
WINDOW_SEARCH_DAYS = 14  # how far ahead to look for the next trading-day job-lock window


class StopChunk(Exception):
    """Raised by a chunk when ctx.should_stop() says stop: the chunk is not marked done."""


class Job(Protocol):
    name: str

    def chunks(self, ctx: Ctx) -> list[str]: ...

    def run_chunk(self, chunk_id: str, ctx: Ctx) -> None: ...


@dataclass
class Ctx:
    paths: Paths
    settings: dict[str, Any]
    out_dir: Path  # <data_dir>/jobs/<job>: the job's own state and outputs
    low_memory: bool
    max_workers: int
    log: logging.Logger
    deadline: datetime  # --until, or the next job-lock window start if earlier (once enforced)
    should_stop: Callable[[], str | None]  # reason to stop now (clock, SSD gone, lock window), else None
    sleep: Callable[[float], None] = time.sleep


@dataclass
class RunResult:
    status: str  # complete | until | ssd_missing | low_space | lock_window
    done: int
    total: int
    ran: list[str] = field(default_factory=list)
    message: str = ""


def parse_until(text: str, now: datetime) -> datetime:
    """The next HH:MM (America/New_York) after `now`."""
    hh, mm = (int(x) for x in text.split(":"))
    local = now.astimezone(ET)
    target = datetime.combine(local.date(), dtime(hh, mm), tzinfo=ET)
    if target <= local:
        target = datetime.combine(local.date() + timedelta(days=1), dtime(hh, mm), tzinfo=ET)
    return target


def p90(durations: list[float]) -> float:
    """Nearest-rank 90th percentile; 0 when nothing has been measured yet."""
    if not durations:
        return 0.0
    ordered = sorted(durations)
    return ordered[max(0, math.ceil(P90 * len(ordered)) - 1)]


def effective_max_workers(setting: int | None, peak_gb_per_worker: float | None) -> int:
    workers = setting or max(1, (os.cpu_count() or 1) - SPARE_CORES)
    if peak_gb_per_worker:
        free = psutil.virtual_memory().available / GB
        workers = min(workers, max(1, math.floor((free - RAM_RESERVE_GB) / peak_gb_per_worker)))
    return workers


def in_lock_window(now: datetime, window: str, is_trading_day: Callable[[date], bool]) -> bool:
    start, end = (dtime.fromisoformat(t.strip()) for t in window.split("-"))
    local = now.astimezone(ET)
    return is_trading_day(local.date()) and start <= local.time() < end


def next_window_start(now: datetime, window: str, is_trading_day: Callable[[date], bool]) -> datetime | None:
    """Start of the next job-lock window on a trading day at or after `now` (None if none soon)."""
    start = dtime.fromisoformat(window.split("-")[0].strip())
    local = now.astimezone(ET)
    for i in range(WINDOW_SEARCH_DAYS):
        day = local.date() + timedelta(days=i)
        at = datetime.combine(day, start, tzinfo=ET)
        if at >= local and is_trading_day(day):
            return at
    return None


def state_path(paths: Paths, job_name: str) -> Path:
    return paths.data_dir / "jobs" / job_name / "state.json"


def load_state(path: Path) -> dict[str, Any]:
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"done": {}, "runs": []}


def run_job(
    job: Job,
    settings: dict[str, Any],
    paths: Paths,
    *,
    until: datetime,
    log: logging.Logger,
    now: Callable[[], datetime] = lambda: datetime.now(ET),
    sleep: Callable[[float], None] = time.sleep,
    is_trading_day: Callable[[date], bool] | None = None,
) -> RunResult:
    if is_trading_day is None:
        from nqbot.calendar import is_trading_day as _calendar_trading_day

        is_trading_day = _calendar_trading_day
    min_free = float(settings["min_free_gb"])
    ops = settings.get("ops") or {}
    window = ops.get("job_lock_window") if ops.get("job_lock_window_enforced") else None

    def refusal() -> tuple[str, str] | None:
        if not paths.mounted():
            return "ssd_missing", "SSD marker gone: stopping cleanly (resume with the same command)"
        if free_gb(paths.drive) < min_free:
            return "low_space", f"less than min_free_gb ({min_free:g} GB) free on {paths.drive}"
        if window and in_lock_window(now(), window, is_trading_day):
            return "lock_window", f"inside ops.job_lock_window {window} on a trading day"
        return None

    deadline = until
    if window:
        window_start = next_window_start(now(), window, is_trading_day)
        if window_start is not None and window_start < deadline:
            deadline = window_start

    def should_stop() -> str | None:
        stop = refusal()
        if stop:
            return stop[1]
        if now() >= deadline:
            return f"reached {deadline:%H:%M}"
        return None

    first = refusal()
    if first:
        log.error("refusing to start %s: %s", job.name, first[1])
        return RunResult(first[0], 0, 0, message=first[1])

    out_dir = paths.data_dir / "jobs" / job.name
    spath = state_path(paths, job.name)
    paths.root.mkdir(parents=True, exist_ok=True)
    with JobLock(paths.root / LOCK_NAME, job.name):
        state = load_state(spath)
        done: dict[str, Any] = state["done"]
        peaks = [d["peak_gb"] for d in done.values() if d.get("peak_gb")]
        ctx = Ctx(
            paths=paths,
            settings=settings,
            out_dir=out_dir,
            low_memory=bool(settings.get("low_memory", True)),
            max_workers=effective_max_workers(settings.get("max_workers"), max(peaks, default=None)),
            log=log,
            deadline=deadline,
            should_stop=should_stop,
            sleep=sleep,
        )
        chunks = job.chunks(ctx)
        pending = [c for c in chunks if c not in done]
        run = {"start": now().isoformat(), "until": deadline.isoformat(), "chunks": []}
        state["runs"].append(run)
        state["total"] = len(chunks)
        log.info("%s: %d/%d chunks done, %d to go, until %s", job.name, len(chunks) - len(pending),
                 len(chunks), len(pending), deadline.strftime("%Y-%m-%d %H:%M %Z"))
        result = RunResult("complete", len(chunks) - len(pending), len(chunks))
        for chunk in pending:
            stop = refusal()
            if stop is None and now() + timedelta(seconds=p90([d["sec"] for d in done.values()])) > deadline:
                stop = ("until", f"next chunk would not finish before {deadline:%H:%M}")
            if stop is not None:
                result.status, result.message = stop
                log.info("%s: stopping (%s)", job.name, stop[1])
                break
            t0 = now()
            try:
                job.run_chunk(chunk, ctx)
                sec = (now() - t0).total_seconds()
                done[chunk] = {"sec": sec, "finished": now().isoformat(), "peak_gb": _peak_gb()}
                run["chunks"].append(chunk)
                run["end"] = now().isoformat()
                atomic_write_json(spath, state)
            except StopChunk as e:
                stop = refusal()
                result.status = stop[0] if stop else "until"
                result.message = f"chunk {chunk} stopped early ({e}): it re-runs next time"
                log.info("%s: %s", job.name, result.message)
                break
            except Exception:
                if not paths.mounted():
                    done.pop(chunk, None)
                    result.status = "ssd_missing"
                    result.message = f"SSD unplugged during chunk {chunk}: it re-runs next time"
                    log.error("%s: %s", job.name, result.message)
                    break
                raise
            result.done += 1
            result.ran.append(chunk)
            log.info("%s: chunk %s done in %.1fs (%d/%d)", job.name, chunk, sec, result.done, result.total)
        run["end"] = now().isoformat()
        run["status"] = result.status
        if paths.mounted():
            atomic_write_json(spath, state)
    return result


def _peak_gb() -> float | None:
    info = psutil.Process().memory_info()
    # Windows: this process's peak working set so far (worker processes are not included; jobs that
    # use workers measure their own per-session peak when they are built).
    peak = getattr(info, "peak_wset", None)
    return round(peak / GB, 3) if peak else None
