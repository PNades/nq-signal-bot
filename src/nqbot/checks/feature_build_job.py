"""The feature build's scheduling (owner 2026-11-12, SPEC §13 Q-step13b (f)): the job `feature_build` (`nqbot run
feature_build --until HH:MM`), its chunks, the child processes, the pass-2 pool and the build report.

Outside the build key (not in `feature_build.CODE_PARTS`): nothing here changes what a session writes. Every session
is built in its own process by `nqbot features build-day D --pass N` (`feature_build.build_day_cmd`, inside the key)
from its checkpoint, so this module only decides when a session runs and how many run at once.

- `p0` the warm-up, `p1:<date>` pass 1 one session at a time in order (each starts from the previous one's state);
- pass 2 is one chunk (`p2:<first>~<last>`): a rolling pool of up to the plan's `batch` sessions at once (as memory
  allows), the next session starting as soon as any finishes, longest first by its pass-1 build time (the old fixed
  batches waited for each batch's slowest session). A stop request, `--until` or a failing session stops new starts;
  the sessions running then finish, and the chunk re-runs next time with only the sessions not done.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time as _time
from datetime import date
from pathlib import Path
from typing import Any

from nqbot import calendar as cal_mod
from nqbot import months as months_mod
from nqbot.checks import feature_build as fb
from nqbot.checks.step8b import Env
from nqbot.storage import atomic_write_text

JOB = fb.JOB
PEAK_TAG = "CHILD_PEAK_GB"
EST_PEAK_GB = 4.5        # assumed per-session peak before one is measured (4.29 GB, `step11 build-day 2026-03-20`)
MIN_FREE_GB_EXTRA = 4.0  # headroom kept free besides the children (as runner.RAM_RESERVE_GB)
POLL_SEC = 2.0           # how often the pass-2 pool looks at its sessions
GB = 2**30


def chunk_ids(doc: dict[str, Any]) -> list[str]:
    """`<pass>:<what>:<key>:<first session>`: the build key and the chain's first session (its warm-up root) name
    every chunk, and the pass-2 chunk names the plan's first and last session, so a re-plan (other months) never
    inherits a chunk finished for another chain or another set of days."""
    key = doc["key"]
    days = doc["sessions"]
    root = days[0]
    return ([f"p0:warmup:{key}:{root}"] + [f"p1:{d}:{key}:{root}" for d in days]
            + [f"p2:{days[0]}~{days[-1]}:{key}:{root}"])


def _child(argv: list[str], log: Path) -> subprocess.Popen:
    """One session in its own process; its whole output goes to `log` (a file, not a pipe: several children run at
    once and a full pipe buffer would stall one until it is read)."""
    code = ("import sys, psutil; from nqbot.cli import main; rc = main(sys.argv[1:]); i = psutil.Process().memory_info(); "
            f"print('{PEAK_TAG}', getattr(i, 'peak_wset', 0) / 2**30 or i.rss / 2**30, flush=True); sys.exit(rc)")
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w", encoding="utf-8") as fh:   # the child keeps its own handle; ours closes at once
        return subprocess.Popen([sys.executable, "-c", code, *argv], stdout=fh, stderr=subprocess.STDOUT,
                                env={**os.environ, "PYTHONUTF8": "1"})


def _out(log: Path) -> str:
    return log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""


def _peak(stdout: str) -> float | None:
    for line in reversed((stdout or "").splitlines()):
        if line.startswith(PEAK_TAG):
            return round(float(line.split()[1]), 3)
    return None


def _grown_gb(p) -> float:
    """How far a running session has already grown (its peak working set so far, GB); 0 if it can't be read."""
    import psutil

    try:
        i = psutil.Process(p.pid).memory_info()
    except (AttributeError, psutil.Error, OSError):
        return 0.0
    return (getattr(i, "peak_wset", 0) or i.rss) / GB


def headroom_gb(free_mb: float | None, running: list, per: float) -> float | None:
    """Memory left for one more session: free memory, less the reserve, less what each running session may still
    grow by to its expected peak `per` (a session starts small and reaches its peak later). None = unknown."""
    if free_mb is None:
        return None
    return free_mb / 1024 - MIN_FREE_GB_EXTRA - sum(max(0.0, per - _grown_gb(p)) for p in running)


def _measured_peak(env: Env, key: str) -> float | None:
    from nqbot.jobs.runner import load_state, state_path

    st = load_state(state_path(env.paths, JOB))
    vals = [v.get("child_peak_gb") or v.get("peak_gb") for c, v in (st.get("done") or {}).items()
            if f":{key}:" in c and c.startswith("p2")]
    vals = [v for v in vals if v]
    return max(vals) if vals else None


def _pass1_sec(env: Env, key: str, day: date) -> float:
    try:
        x = json.loads(fb.summary_file(env, key, day).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0.0
    return float((x.get("pass1") or {}).get("build_sec") or 0.0)


class FeatureBuildJob:
    """`nqbot run feature_build --until HH:MM`: the two passes, one session per process (low memory)."""

    name = JOB

    def __init__(self):
        from nqbot.jobs.lock import DATABENTO, SKYLIT

        self.locks = (SKYLIT, DATABENTO)   # nothing may write the stored trades or boards it reads

    def _env(self, ctx) -> Env:
        from nqbot.config import load_features

        return Env(settings=ctx.settings, features=load_features(), paths=ctx.paths, log=ctx.log, echo=ctx.log.info,
                   cal=cal_mod.load())

    def chunks(self, ctx) -> list[str]:
        return chunk_ids(fb.load_plan(self._env(ctx)))

    def run_chunk(self, chunk_id: str, ctx) -> dict | None:
        from nqbot.checks.step8b_disloc import free_mb
        from nqbot.jobs.runner import StopChunk

        env = self._env(ctx)
        doc = fb.load_plan(env)
        kind, which, key, _root = chunk_id.split(":")
        days = [date.fromisoformat(x) for x in doc["sessions"]]
        if kind == "p0":
            if fb.warmup_file(env, key, days[0]).exists():
                return None
            mb = free_mb()
            if mb is not None and mb < (EST_PEAK_GB + MIN_FREE_GB_EXTRA) * 1024:
                raise StopChunk(f"{mb} MB free before the warm-up")
            log = fb.log_file(env, key, "warmup")
            proc = _child(["features", "build-warmup"], log)
            proc.wait()
            out = _out(log)
            if proc.returncode != 0:
                ctx.log.error("%s: warm-up failed (exit %s), log %s: %s", JOB, proc.returncode, log, out[-600:])
                raise StopChunk(f"the warm-up failed (exit {proc.returncode}; log {log})", status="failures")
            ctx.log.info("%s: warm-up done, process peak %s GB", JOB, _peak(out))
            return {"peak_gb": _peak(out)}
        if kind == "p1":
            d = date.fromisoformat(which)
            if fb.pass1_done(env, key, days, d):
                return None
            mb = free_mb()
            if mb is not None and mb < (EST_PEAK_GB + MIN_FREE_GB_EXTRA) * 1024:
                raise StopChunk(f"{mb} MB free before {d} (pass 1)")
            return {"peak_gb": self._pool(env, key, [d], 1, 1, ctx)}
        d0, d1 = (date.fromisoformat(x) for x in which.split("~"))
        todo = [d for d in days if d0 <= d <= d1 and not fb.pass2_done(env, key, d, days)]
        if not todo:
            return None
        todo.sort(key=lambda d: (-_pass1_sec(env, key, d), d))   # longest first: the slowest never starts last
        return {"peak_gb": self._pool(env, key, todo, 2, int(doc["batch"]), ctx)}

    def _pool(self, env: Env, key: str, todo: list[date], pass_no: int, n: int, ctx) -> float | None:
        """Run `todo` (in this order), up to `n` sessions at once as memory allows, starting the next as soon as one
        finishes. A stop or a failure stops new starts; the running sessions finish first."""
        from nqbot.checks.step8b_disloc import free_mb
        from nqbot.jobs.runner import StopChunk

        sleep = getattr(ctx, "sleep", None) or _time.sleep
        cap = max(1, min(n, ctx.max_workers or n))
        queue, running = list(todo), {}
        peaks: list[float] = []
        failed, stop = [], None
        while queue or running:
            for d, (log, p) in list(running.items()):
                if p.poll() is None:
                    continue
                del running[d]
                out = _out(log)
                pk = _peak(out)
                if pk is not None:
                    peaks.append(pk)
                if p.returncode != 0:
                    failed.append((d, p.returncode, log, out[-600:]))
                else:
                    ctx.log.info("%s: %s pass %d done, process peak %s GB", JOB, d, pass_no, pk)
            if queue and not failed and stop is None:
                stop = ctx.should_stop()
            while queue and not failed and stop is None and len(running) < cap:
                per = max(EST_PEAK_GB, _measured_peak(env, key) or 0.0, max(peaks, default=0.0))
                room = headroom_gb(free_mb(), [p for _, p in running.values()], per)
                if room is not None and room < per:
                    if not running:
                        raise StopChunk(f"{room + MIN_FREE_GB_EXTRA:.1f} GB free: not enough for one pass-{pass_no} "
                                        f"session ({per:.1f} GB)")
                    break   # the next starts when one finishes
                d = queue.pop(0)
                log = fb.log_file(env, key, f"{d.isoformat()}_p{pass_no}")
                running[d] = (log, _child(["features", "build-day", d.isoformat(), "--pass", str(pass_no)], log))
            if running:
                sleep(POLL_SEC)
            elif failed or stop is not None:
                break
        if failed:
            for d, rc, log, msg in failed:
                ctx.log.error("%s: %s pass %d failed (exit %s), log %s: %s", JOB, d, pass_no, rc, log, msg)
            raise StopChunk(f"{len(failed)} session(s) failed in pass {pass_no}: "
                            f"{', '.join(str(d) for d, *_ in failed)} (see the log)", status="failures")
        if stop is not None:
            raise StopChunk(stop)
        return max(peaks) if peaks else None

    def at_end(self, ctx) -> None:
        env = self._env(ctx)
        path = write_report(env, fb.load_plan(env))
        ctx.log.info("%s: report %s", JOB, path)


# --- the report (§7 rule 2b: months 7-12 as counts only) --------------------------------------------------------

def write_report(env: Env, doc: dict[str, Any]) -> Path:
    key = doc["key"]
    days = [date.fromisoformat(x) for x in doc["sessions"]]
    per_month: dict[int, dict] = {}
    dev_rows = []
    for d in days:
        k = months_mod.month_index(d, env.settings)
        m = per_month.setdefault(k, {"sessions": 0, "p1": 0, "p2": 0, "events": 0, "rows": 0})
        m["sessions"] += 1
        s = fb.summary_file(env, key, d)
        x = json.loads(s.read_text(encoding="utf-8")) if s.exists() else {}
        if fb.state_file(env, key, d).exists():
            m["p1"] += 1
        p2 = x.get("pass2")
        if p2:
            m["p2"] += 1
            m["events"] += p2.get("events", 0)
            m["rows"] += p2.get("rows", 0)
            if k in months_mod.DEVELOPMENT:
                dev_rows.append((d, p2))
    lines = [f"# Feature build (step 11 part 3), key {key}", "",
             f"features_version {doc['features_version']}, build version {doc['build_version']}; outputs under "
             f"`{fb.out_dir(env, key)}`. Months 7-12 are counts only (§7 rule 2b: walk-forward and sealed months appear "
             "in no report until they are scored).", "",
             "| ledger month | sessions | pass 1 done | pass 2 done | events | rows |", "|---|---|---|---|---|---|"]
    for k in sorted(per_month):
        m = per_month[k]
        lines.append(f"| {k} | {m['sessions']} | {m['p1']} | {m['p2']} | {m['events']} | {m['rows']} |")
    lines += ["", "## Development months (1-6), by session", "",
              "| session | events | tradeable | held | broke | timeout | day type | build s | total s | Skylit |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for d, p in dev_rows:
        lab = p.get("labels") or {}
        lines.append(f"| {d} | {p.get('events')} | {p.get('tradeable')} | {lab.get('held')} | {lab.get('broke')} | "
                     f"{lab.get('timeout')} | {p.get('day_type')} | {p.get('build_sec')} | {p.get('total_sec')} | "
                     f"{p.get('skylit')} |")
    path = env.paths.reports_dir / "step11" / "feature_build.md"
    atomic_write_text(path, "\n".join(lines) + "\n")
    return path
