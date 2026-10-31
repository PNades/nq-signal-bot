"""The job queue and `nqbot resume` (owner 2026-10-28, SPEC §11 Overnight jobs: unattended runs on the home PC).

The queue is `<data_dir>/jobs/queue.txt` on the SSD (so it travels with the data): one job name per line, `#`
comments, written only with `nqbot queue add|remove` (by the owner, or by Claude Code with the owner's OK). The
scheduled task `nqbot resume` (scripts/register_tasks_pc.ps1: at startup, daily 07:35 and every hour) runs the
first queued job that isn't complete with `--until`, then the next once it completes. Two copies can't run: the
task starts one instance at a time, and the job lock refuses a second run of any job (a dead process's lock on the
same machine is reclaimed, so a run cut off by a reboot, power loss or crash resumes from its last chunk).

A job is resumed only if its last run ended at --until (`until`), on an SSD drop (`ssd_missing`), inside the job-lock
window (`lock_window`), or never recorded an end (the process died). Every other end waits for the owner, who
resumes it by hand with `nqbot run <job>` (after which resume picks it up again): `stopped` (the owner asked),
`credits`, `failures`, `api_error`, `low_space`, a refusal before it started (`refused`: no approval, a missing key,
...), or anything new. A job whose last `ops.resume_max_crashes` resume
attempts each died or raised without finishing a chunk is held until `nqbot queue release`. After a reboot, the
first resume reads back every file written in the last `ops.resume_verify_days` days (`nqbot verify`) and holds
everything if any is damaged. A paid job is never started by resume unless its own plan already holds the owner's
approval (CLAUDE.md rule 2): resume only calls `nqbot run`, which refuses otherwise.
"""

from __future__ import annotations

import os
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import psutil

from nqbot.jobs.runner import load_state, plan_progress, state_path
from nqbot.storage import Paths, atomic_write_json, atomic_write_text

QUEUE_FILE = "queue.txt"
RESUME_STATE = "resume_state.json"
RESUMABLE = ("until", "ssd_missing", "lock_window")  # last-run ends resume picks up again by itself
DIED = "died"  # a run whose end was never recorded (reboot, power loss, killed) or that raised
BOOT_TOL_SEC = 5  # psutil.boot_time() can move by a second or two between reads
HOLD_SHOW_FILES = 5  # files named in the after-restart hold message (the rest counted; all in verify.md)
ET = ZoneInfo("America/New_York")


def queue_path(paths: Paths) -> Path:
    return paths.data_dir / "jobs" / QUEUE_FILE


def resume_state_path(paths: Paths) -> Path:
    return paths.data_dir / "jobs" / RESUME_STATE


def read_queue(paths: Paths) -> list[str]:
    p = queue_path(paths)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        name = line.split("#", 1)[0].strip()
        if name and name not in out:
            out.append(name)
    return out


def write_queue(paths: Paths, names: list[str]) -> None:
    header = "# nqbot resume runs these jobs in order (nqbot queue add|remove; SPEC §11 Overnight jobs)\n"
    atomic_write_text(queue_path(paths), header + "".join(f"{n}\n" for n in names))


def load_resume_state(paths: Paths) -> dict[str, Any]:
    import json

    p = resume_state_path(paths)
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {"attempts": {}, "held": {}, "hold_all": None, "last_boot": None}


def save_resume_state(paths: Paths, rs: dict[str, Any]) -> None:
    atomic_write_json(resume_state_path(paths), rs)


def last_end(state: dict[str, Any]) -> str | None:
    """How the job's last run ended: its status, DIED if it never recorded one, None if it never ran."""
    runs = state.get("runs") or []
    if not runs:
        return None
    return runs[-1].get("status") or DIED


def is_complete(state: dict[str, Any]) -> bool:
    n_done, total = plan_progress(state)
    return last_end(state) == "complete" and n_done >= total


@dataclass
class Decision:
    job: str
    action: str  # skip (complete) | run | wait (for the owner) | held
    reason: str


def decide(job: str, state: dict[str, Any], rs: dict[str, Any], max_crashes: int) -> Decision:
    if job in rs.get("held", {}):
        return Decision(job, "held", rs["held"][job])
    if is_complete(state):
        return Decision(job, "skip", "complete")
    end = last_end(state)
    if end is not None and end not in RESUMABLE and end != DIED and end != "complete":
        return Decision(job, "wait", f"its last run ended '{end}': waiting for the owner (resume it with "
                                     f"uv run nqbot run {job})")
    a = (rs.get("attempts") or {}).get(job) or {}
    if a.get("outcome") == "refused" and a.get("done_before") == len(state.get("done") or {}):
        return Decision(job, "wait", f"refused on its last start ({a.get('message', '')}): waiting for the owner "
                                     f"(fix it, then uv run nqbot run {job} or uv run nqbot queue release {job})")
    crashes = crash_count(job, state, rs)
    if crashes >= max_crashes:
        return Decision(job, "held", f"{crashes} resume attempts in a row died or failed without finishing a chunk")
    if end == "complete":  # complete, but a re-plan since added chunks not done (runner.record_plan)
        return Decision(job, "run", "re-planned since its last complete run")
    return Decision(job, "run", "never run" if end is None else f"last run ended '{end}'")


def crash_count(job: str, state: dict[str, Any], rs: dict[str, Any]) -> int:
    """Consecutive resume attempts of `job` that died (no outcome recorded) or raised, with no chunk finished since."""
    a = (rs.get("attempts") or {}).get(job)
    if not a:
        return 0
    done_now = len(state.get("done") or {})
    if done_now != a.get("done_before"):
        return 0  # progress since that attempt started
    if a.get("outcome") in (None, DIED):
        return int(a.get("crashes", 0)) + 1
    return 0


def boot_time() -> float:
    return psutil.boot_time()


def resume(paths: Paths, settings: dict[str, Any], *, run: Callable[[str], Any],
           verify: Callable[[datetime], list[str]],
           echo: Callable[[str], None] = print, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
           booted: Callable[[], float] = boot_time) -> int:
    """Run the queue (see the module docstring). `run(job)` is `nqbot run <job> --until ...` and returns its
    RunResult (it raises LockHeld if the job is already running); `verify(since)` returns the files it flagged
    (empty = nothing damaged).
    Exit codes as the CLI's: 0 done / ran, 1 verify found damage, 2 waiting for the owner, 3 a job lock is held."""
    from nqbot.jobs.lock import LockHeld

    ops = settings.get("ops") or {}
    max_crashes = int(ops["resume_max_crashes"])
    verify_days = int(ops["resume_verify_days"])
    rs = load_resume_state(paths)
    rs.setdefault("attempts", {})
    rs.setdefault("held", {})
    if rs.get("hold_all"):
        echo(f"resume: everything held: {rs['hold_all']} (check, then: uv run nqbot queue release)")
        return 2
    boot = booted()
    if rs.get("last_boot") is None or abs(boot - float(rs["last_boot"])) > BOOT_TOL_SEC:
        since = now() - timedelta(days=verify_days)
        echo(f"resume: first run since the computer started "
             f"({datetime.fromtimestamp(boot, timezone.utc):%Y-%m-%d %H:%M} UTC): checking every file written "
             f"since {since:%Y-%m-%d %H:%M} UTC")
        flagged = verify(since)
        if flagged:  # owner 2026-10-31: when the check ran and what it flagged, not the boot time
            shown = ", ".join(flagged[:HOLD_SHOW_FILES]) + (f" and {len(flagged) - HOLD_SHOW_FILES} more"
                                                            if len(flagged) > HOLD_SHOW_FILES else "")
            rs["hold_all"] = (f"nqbot verify at {now().astimezone(ET):%Y-%m-%d %H:%M} ET (first resume since the "
                              f"computer started) flagged {len(flagged)} file(s): {shown} (see "
                              f"<reports_dir>/verify/verify.md)")
            save_resume_state(paths, rs)
            echo(f"resume: HOLD: {rs['hold_all']}")
            return 1
        rs["last_boot"] = boot
        save_resume_state(paths, rs)
    queue = read_queue(paths)
    if not queue:
        echo("resume: the queue is empty (uv run nqbot queue add <job>)")
        return 0
    for job in queue:
        state = load_state(state_path(paths, job))
        d = decide(job, state, rs, max_crashes)
        if d.action == "skip":
            echo(f"resume: {job}: complete")
            continue
        if d.action == "held":
            rs["held"][job] = d.reason
            save_resume_state(paths, rs)
            echo(f"resume: {job}: HELD: {d.reason} (check its log, then: uv run nqbot queue release {job})")
            return 2
        if d.action == "wait":
            echo(f"resume: {job}: {d.reason}")
            return 2
        before = rs["attempts"].get(job)
        rs["attempts"][job] = {"started": now().isoformat(), "pid": os.getpid(),
                               "done_before": len(state.get("done") or {}),
                               "crashes": crash_count(job, state, rs), "outcome": None}
        save_resume_state(paths, rs)
        echo(f"resume: {job}: running ({d.reason})")
        try:
            result = run(job)
        except LockHeld as e:  # already running (by hand or another resume): not an attempt of ours
            if before is None:
                rs["attempts"].pop(job, None)
            else:
                rs["attempts"][job] = before
            save_resume_state(paths, rs)
            echo(f"resume: {job}: not started: {e}")
            return 3
        except Exception:
            rs["attempts"][job]["outcome"] = DIED
            save_resume_state(paths, rs)
            echo(f"resume: {job}: raised:\n{traceback.format_exc()}")
            return 2
        rs["attempts"][job]["outcome"] = result.status
        if result.status == "refused":
            rs["attempts"][job]["message"] = result.message
        save_resume_state(paths, rs)
        echo(f"resume: {job}: {result.status} ({result.done}/{result.total} chunks done)"
             + (f" - {result.message}" if result.message else ""))
        if result.status != "complete":
            return 0 if result.status in RESUMABLE or result.status == "stopped" else 2
    echo("resume: every queued job is complete")
    return 0


def release(paths: Paths, job: str | None = None) -> list[str]:
    """Clear a hold (one job's, or every hold incl. the after-restart verify hold); returns what was cleared."""
    rs = load_resume_state(paths)
    cleared = []
    if job is None:
        if rs.get("hold_all"):
            cleared.append("hold on everything")
            rs["hold_all"] = None
        jobs = list(rs.get("held", {}))
    else:
        jobs = [job]
    for j in jobs:
        if rs.get("held", {}).pop(j, None) is not None:
            cleared.append(j)
        if j in rs.get("attempts", {}):
            rs["attempts"][j]["crashes"] = 0
            rs["attempts"][j]["outcome"] = "released"
    save_resume_state(paths, rs)
    return cleared


def queue_lines(paths: Paths, settings: dict[str, Any]) -> list[str]:
    """For `nqbot status` and `nqbot queue show`: each queued job and what resume would do with it now. A hold is
    always shown, even with the queue empty (owner 2026-10-31: an empty queue hid the after-restart verify hold, which
    would still stop the next job queued)."""
    queue = read_queue(paths)
    rs = load_resume_state(paths)
    holds = ([f"  HOLD ALL: {rs['hold_all']} (lift: uv run nqbot queue release)"] if rs.get("hold_all") else [])
    holds += [f"  HELD {job} (not queued): {why} (lift: uv run nqbot queue release {job})"
              for job, why in (rs.get("held") or {}).items() if job not in queue]
    if not queue:
        return ["queue: empty"] + holds
    max_crashes = int((settings.get("ops") or {})["resume_max_crashes"])
    lines = ["queue (nqbot resume):"] + holds
    for job in queue:
        d = decide(job, load_state(state_path(paths, job)), rs, max_crashes)
        lines.append(f"  {job}: {d.action} ({d.reason})")
    return lines
