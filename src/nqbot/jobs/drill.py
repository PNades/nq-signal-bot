"""`dummy_drill` (owner 2026-10-28): a configurable dummy job for the PC's resume drill (SPEC §11 Overnight jobs).

`nqbot drill setup --chunks N --sleep S` writes its parameters (an older drill's folder is moved aside first); the
job then runs N chunks of S seconds each (checking ctx.should_stop() every second), and every finished chunk appends
one line (chunk, pid, boot time) to `drill_log.jsonl`. Cut the power or restart mid-run: the resume task should pick
it up. `nqbot drill check` says whether every chunk finished exactly once and in how many processes and boots."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil

from nqbot.jobs.runner import Ctx, StopChunk, load_state, state_path
from nqbot.storage import Paths, append_jsonl, atomic_write_json, read_jsonl, replace_retry

NAME = "dummy_drill"
PARAMS = "params.json"
LOG = "drill_log.jsonl"
DEFAULT_CHUNKS, DEFAULT_SLEEP_SEC = 5, 1.0  # used only before `nqbot drill setup`
UNIT_SEC = 1.0  # should_stop() is checked this often inside a chunk


def job_dir(paths: Paths) -> Path:
    return paths.data_dir / "jobs" / NAME


class DrillJob:
    name = NAME

    def _params(self, ctx: Ctx) -> dict[str, Any]:
        p = ctx.out_dir / PARAMS
        if p.exists():
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        return {"chunks": DEFAULT_CHUNKS, "sleep_sec": DEFAULT_SLEEP_SEC}

    def chunks(self, ctx: Ctx) -> list[str]:
        return [f"chunk-{i:04d}" for i in range(int(self._params(ctx)["chunks"]))]

    def run_chunk(self, chunk_id: str, ctx: Ctx) -> None:
        left = float(self._params(ctx)["sleep_sec"])
        while left > 0:
            reason = ctx.should_stop()
            if reason:
                raise StopChunk(reason)
            step = min(UNIT_SEC, left)
            ctx.sleep(step)
            left -= step
        append_jsonl(ctx.out_dir / LOG, {"chunk": chunk_id, "pid": os.getpid(), "boot": round(psutil.boot_time()),
                                         "finished": datetime.now(timezone.utc).isoformat(timespec="seconds")})


def setup(paths: Paths, chunks: int, sleep_sec: float) -> Path | None:
    """Start a fresh drill; an older drill's folder is moved aside (returned), never deleted."""
    d = job_dir(paths)
    moved = None
    if d.exists():
        moved = d.with_name(f"{NAME}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}")
        replace_retry(d, moved)
    atomic_write_json(d / PARAMS, {"chunks": chunks, "sleep_sec": sleep_sec,
                                   "set_up": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return moved


def check(paths: Paths) -> dict[str, Any]:
    d = job_dir(paths)
    params_file = d / PARAMS
    total = json.loads(params_file.read_text(encoding="utf-8"))["chunks"] if params_file.exists() else DEFAULT_CHUNKS
    rows = read_jsonl(d / LOG) if (d / LOG).exists() else []
    seen: dict[str, int] = {}
    for r in rows:
        seen[r["chunk"]] = seen.get(r["chunk"], 0) + 1
    state = load_state(state_path(paths, NAME))
    twice = sorted(c for c, n in seen.items() if n > 1)
    clean = len(seen) == total and not twice and len(state["done"]) == total
    return {"total": total, "finished": len(seen), "finished_twice": twice, "processes": len({r["pid"] for r in rows}),
            "boots": len({r["boot"] for r in rows}), "runs": len(state.get("runs") or []), "clean": clean}
