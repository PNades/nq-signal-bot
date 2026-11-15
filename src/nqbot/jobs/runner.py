"""`nqbot run <job> --until HH:MM`: resumable chunks on the SSD (SPEC §11 Overnight jobs, Low memory).

A job is an ordered list of chunk ids plus `run_chunk`. Progress is saved after every chunk, so the next
run resumes where the last one stopped. Before every chunk the runner checks: the SSD marker is still
there, free space >= min_free_gb (the drive's, so a job running in parallel counts too), the job-lock
window (once enforced, step 22), and that the chunk's measured 90th-percentile duration fits before --until. Long chunks call `ctx.should_stop()` between
their own work units (§11: work is checked against the clock between work units) and raise `StopChunk`
to give up the chunk cleanly; it then re-runs from its start next time.

`nqbot stop <job>` (decision 2, presd_fixes D3) writes <data_dir>/jobs/<job>/stop.request: should_stop() then says
stop, so the job ends after the call it is in (its answer saved) with status `stopped`, never mid-call like Ctrl-C.
"""

from __future__ import annotations

import contextlib
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

from nqbot.jobs.lock import ALL_LOCKS, JobLock, job_locks
from nqbot.power import keep_awake
from nqbot.storage import GB, Paths, atomic_write_json, free_gb

ET = ZoneInfo("America/New_York")
DEFAULT_UNTIL = "07:30"  # §11: default --until 07:30
P90 = 0.90  # §11: a chunk starts only if its measured 90th-percentile duration fits
SPARE_CORES = 2  # §11: max_workers default = all cores but 2
RAM_RESERVE_GB = 10  # §11 Low memory: workers capped at floor((free RAM - 10 GB) / peak per session); the same
# reserve keeps the feature-build pool's room (feature_build_job), one for the whole machine (owner 2026-11-14: was 4;
# room for other programs while a job runs; moves to config/local.yaml with the next build-key change, SPEC §13)
WINDOW_SEARCH_DAYS = 14  # how far ahead to look for the next trading-day job-lock window
STOP_REQUEST = "stop.request"
RUN_LOCK = "run.lock"
POOL_PROGRESS = "pool_progress.json"   # sessions a chunk's pool finished (owner 2026-11-13 review; feature builds)
STOP_REASON = "stop requested (nqbot stop)"


class StopChunk(Exception):
    """Raised by a chunk when ctx.should_stop() says stop: the chunk is not marked done.

    `status` names a stop the chunk itself found (e.g. "credits": the Skylit budget or balance ran out); the run
    then ends with that status instead of the clock/SSD one."""

    def __init__(self, reason: str = "", status: str | None = None):
        super().__init__(reason)
        self.status = status


class Job(Protocol):
    """A job may also set `locks` (a tuple of nqbot.jobs.lock kinds); without it the job holds every job lock.
    `shared_locks = True` (owner 2026-11-12: the feature builds with `parallel_builds`) holds them shared, so other
    such jobs may run at once, and adds the job's own run lock (`<data_dir>/jobs/<job>/run.lock`): one run per job.
    Optional `at_start(ctx)` runs before the first chunk of every run and `at_end(ctx)` when a run ends normally
    (complete, or --until reached); either may raise StopChunk like a chunk.
    `p90_fit = False` (owner 2026-11-14, the step-13c driver): skip the rule that a chunk starts only if its measured
    90th-percentile time fits before --until. For a job whose chunks run child jobs that stop themselves at the same
    --until (a chunk that stops early is not done and re-runs); its chunks still start only before the deadline."""

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
    status: str  # complete | until | stopped | ssd_missing | low_space | lock_window | credits | api_error | failures
    done: int
    total: int
    ran: list[str] = field(default_factory=list)
    message: str = ""


def parse_until(text: str, now: datetime) -> datetime:
    """The next HH:MM (America/New_York) after `now`. Compared as instants (owner 2026-11-02): two datetimes with the
    same tzinfo compare by wall clock, so in the hour repeated when the clocks go back a target could come out in the
    past; both occurrences of a repeated time are candidates, the first after `now` wins."""
    hh, mm = (int(x) for x in text.split(":"))
    local = now.astimezone(ET)
    for day in (local.date(), local.date() + timedelta(days=1)):
        for fold in (0, 1):
            target = datetime.combine(day, dtime(hh, mm, fold=fold), tzinfo=ET)
            if target.timestamp() > now.timestamp():
                return target
    raise AssertionError("unreachable: tomorrow's HH:MM is always after now")


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


def stop_request_path(paths: Paths, job_name: str) -> Path:
    return paths.data_dir / "jobs" / job_name / STOP_REQUEST


def run_lock_path(paths: Paths, job_name: str) -> Path:
    return paths.data_dir / "jobs" / job_name / RUN_LOCK


def state_path(paths: Paths, job_name: str) -> Path:
    return paths.data_dir / "jobs" / job_name / "state.json"


def pool_progress_path(paths: Paths, job_name: str) -> Path:
    return paths.data_dir / "jobs" / job_name / POOL_PROGRESS


def read_pool_progress(paths: Paths, job_name: str) -> dict[str, Any]:
    """What a job's pool recorded inside its current chunk (empty if nothing): `finished_total` (sessions ever
    finished, never reset), `pass`, `pass_done`, `pass_total`, `session_sec`."""
    try:
        return json.loads(pool_progress_path(paths, job_name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load_state(path: Path) -> dict[str, Any]:
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"done": {}, "runs": []}


def plan_progress(state: dict[str, Any]) -> tuple[int, int]:
    """(chunks done, total) of the job's current plan. `done` keeps every chunk id ever finished, including ids of
    an older plan (a re-plan gives new ids), so only the ids in the last run's `chunks` list count. A state written
    before that list was kept: the done count is capped at `total`, which is exact when its last run completed."""
    done = state.get("done") or {}
    if "chunks" in state:
        return sum(1 for c in state["chunks"] if c in done), len(state["chunks"])
    total = int(state.get("total", len(done)))
    return min(len(done), total), total


def record_plan(paths: Paths, job_name: str, chunk_ids: list[str]) -> tuple[int, int]:
    """A re-plan (owner 2026-10-30): save the new plan's chunk ids in the job's state.json, so status shows its
    progress against them and the queue, seeing chunks not done, runs the job again. The runner sets the same list
    again from job.chunks() at its next start. Returns (done, total) of the new plan."""
    spath = state_path(paths, job_name)
    state = load_state(spath)
    state["chunks"] = list(chunk_ids)
    state["total"] = len(chunk_ids)
    state["replanned"] = datetime.now(ET).isoformat()
    atomic_write_json(spath, state)
    return plan_progress(state)


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
        if window_start is not None and window_start.timestamp() < deadline.timestamp():
            deadline = window_start

    stop_file = stop_request_path(paths, job.name)

    def should_stop() -> str | None:
        stop = refusal()
        if stop:
            return stop[1]
        if stop_file.exists():
            return STOP_REASON
        if now().timestamp() >= deadline.timestamp():
            return f"reached {deadline:%H:%M}"
        return None

    first = refusal()
    if first:
        log.error("refusing to start %s: %s", job.name, first[1])
        return RunResult(first[0], 0, 0, message=first[1])

    out_dir = paths.data_dir / "jobs" / job.name
    spath = state_path(paths, job.name)
    paths.root.mkdir(parents=True, exist_ok=True)
    shared = bool(getattr(job, "shared_locks", False))
    if shared:
        run_lock_path(paths, job.name).parent.mkdir(parents=True, exist_ok=True)
    # Keep Windows awake while the job runs (owner 2026-10-05, §11 Overnight jobs; lid-close sleep still applies).
    run_lock = JobLock(run_lock_path(paths, job.name), job.name) if shared else contextlib.nullcontext()
    data_locks = job_locks(paths.root, getattr(job, "locks", None) or ALL_LOCKS, job.name, shared=shared)
    with keep_awake(display=True), run_lock, data_locks:
        if stop_file.exists():  # left by a run that ended before it saw it: this run was asked for after it
            log.info("%s: removing an old stop request (%s)", job.name, stop_file)
            stop_file.unlink(missing_ok=True)
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
        state["chunks"] = list(chunks)  # the current plan's ids: status and the queue count only these
        log.info("%s: %d/%d chunks done, %d to go, until %s", job.name, len(chunks) - len(pending),
                 len(chunks), len(pending), deadline.strftime("%Y-%m-%d %H:%M %Z"))
        result = RunResult("complete", len(chunks) - len(pending), len(chunks))

        def hook(name: str) -> bool:
            """Run the job's optional `at_start` / `at_end(ctx)` (e.g. the Skylit backfill's retry of failed
            windows, at the start of every run and the end of every night). False: the run stops here."""
            fn = getattr(job, name, None)
            if fn is None:
                return True
            try:
                fn(ctx)
            except StopChunk as e:
                stop = refusal() or (("stopped", STOP_REASON) if stop_file.exists() else None)
                result.status = e.status or (stop[0] if stop else "until")
                result.message = f"{name} stopped early ({e})"
                log.info("%s: %s", job.name, result.message)
                return False
            except Exception:
                if not paths.mounted():
                    result.status, result.message = "ssd_missing", f"SSD unplugged during {name}"
                    log.error("%s: %s", job.name, result.message)
                    return False
                raise
            return True

        started = hook("at_start")
        for chunk in pending if started else []:
            stop = refusal() or (("stopped", STOP_REASON) if stop_file.exists() else None)
            # instants, not wall clock (owner 2026-11-02): ET datetimes add and compare by wall time across a switch
            fit = p90([d["sec"] for d in done.values()]) if getattr(job, "p90_fit", True) else 0.0
            if stop is None and now().timestamp() + fit > deadline.timestamp():
                stop = ("until", f"next chunk would not finish before {deadline:%H:%M}")
            if stop is not None:
                result.status, result.message = stop
                log.info("%s: stopping (%s)", job.name, stop[1])
                break
            t0 = now()
            try:
                out = job.run_chunk(chunk, ctx)
                sec = now().timestamp() - t0.timestamp()   # real seconds, also across 02:00 on a switch night
                # a chunk run in its own process reports that process's peak (it is not this process's)
                child = out.get("peak_gb") if isinstance(out, dict) else None
                peaks_now = [p for p in (_peak_gb(), child) if p]
                done[chunk] = {"sec": sec, "finished": now().isoformat(), "peak_gb": max(peaks_now, default=None)}
                if child:
                    done[chunk]["child_peak_gb"] = child
                run["chunks"].append(chunk)
                run["end"] = now().isoformat()
                atomic_write_json(spath, state)
            except StopChunk as e:
                stop = refusal() or (("stopped", STOP_REASON) if stop_file.exists() else None)
                result.status = e.status or (stop[0] if stop else "until")
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
        # the night ended normally (not a stop or a fault), and a chunk ran since at_start (else it would just repeat it)
        if started and result.ran and result.status in ("complete", "until"):
            hook("at_end")
        run["end"] = now().isoformat()
        run["status"] = result.status
        if paths.mounted():
            atomic_write_json(spath, state)
            stop_file.unlink(missing_ok=True)  # served (or not needed any more: the run ended)
    return result


def _peak_gb() -> float | None:
    info = psutil.Process().memory_info()
    # Windows: this process's peak working set so far (worker processes are not included; jobs that
    # use workers measure their own per-session peak when they are built).
    peak = getattr(info, "peak_wset", None)
    return round(peak / GB, 3) if peak else None
