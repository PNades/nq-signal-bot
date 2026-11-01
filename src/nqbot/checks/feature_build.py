"""The feature build (step 11 part 3; SPEC §11 Expected run time, Low memory, Storage, Overnight jobs; §7 rule 2b).

`nqbot features plan --months 1-12` lists the window's planned sessions of those ledger months and writes
`<data_dir>/jobs/feature_build/plan.json` with the build key (sha256 of features.yaml, settings.yaml without the
per-machine and path keys, calendar.yaml and `BUILD_VERSION`); `nqbot run feature_build --until HH:MM` runs it, two
passes as in §11:
- **pass 1** (`p1:<date>:<key>`, sequential, oldest first): each session in its own process (low memory) builds the
  full zone and structure-label engine with the Skylit boards from the previous session's state, writes no rows, and
  saves the state after it (`state/state_<date>.json`: both carries with part 3's touch history, retested extremes,
  retest candidates, yesterday's day type, and the NQ RTH minute volumes). The first session starts from a carry-only
  replay of the `warmup_sessions` sessions before it.
- **pass 2** (`p2:<batch>:<key>`): each chunk runs up to `batch` sessions at once, each in its own process starting
  from its checkpoint; each writes `touches/<date>.parquet` (one row per event at its touch: the structure-model
  inputs), `daytype/<date>.parquet` (one row per day-type snapshot) and `outcomes/<date>.parquet` (structure labels
  and the EOD day type: outcomes, never read as features), and asserts its end state equals the next checkpoint.
Files go under `<data_dir>/processed/features/v<features_version>_<key>/`. A failing session stops the run cleanly
(status `failures`, the session and its error in the log; the owner looks before it resumes). The report
`<reports_dir>/step11/feature_build.md` names dates and values for months 1-6 only; months 7-12 appear as counts
(sessions, events, rows), never dates, values or outcomes (§7 rule 2b).
`nqbot step11 build-day D` (a development day) is the one-day smoke run: warm-up replay, the session built twice from
the same state (determinism), timings and memory, report `<reports_dir>/step11/build_day_<D>.md`.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import time as _time
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from nqbot import calendar as cal_mod
from nqbot import months as months_mod
from nqbot.checks import session_build as sb
from nqbot.checks.step8b import Env
from nqbot.storage import atomic_write_bytes, atomic_write_json, atomic_write_text

JOB = "feature_build"
BUILD_VERSION = 1        # bump when the build's code changes its outputs without a config change
PEAK_TAG = "CHILD_PEAK_GB"
EST_PEAK_GB = 4.5        # assumed per-session peak before one is measured (4.29 GB, `step11 build-day 2026-03-20`)
MIN_FREE_GB_EXTRA = 4.0  # headroom kept free besides the children (as runner.RAM_RESERVE_GB)
DEV_MONTHS = months_mod.DEVELOPMENT
LOCAL_SETTING_KEYS = {"paths", "max_workers", "max_fold_mem_gb", "low_memory", "rclone_path", "test_parallel"}
ID_COLS = {"ts", "t"}


class BuildRefused(Exception):
    pass


# --- keys and paths -------------------------------------------------------------------------------------------

def build_key(env: Env) -> str:
    sett = {k: v for k, v in env.settings.items() if k not in LOCAL_SETTING_KEYS}
    cal = env.cal if env.cal is not None else cal_mod.load()
    blob = json.dumps({"features": env.features, "settings": sett, "calendar": cal, "build": BUILD_VERSION},
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def out_dir(env: Env, key: str) -> Path:
    return env.paths.data_dir / "processed" / "features" / f"v{env.features['features_version']}_{key}"


def plan_path(env: Env) -> Path:
    return env.paths.data_dir / "jobs" / JOB / "plan.json"


def state_file(env: Env, key: str, day: date) -> Path:
    return out_dir(env, key) / "state" / f"state_{day.isoformat()}.json"


def warmup_file(env: Env, key: str, first: date) -> Path:
    """The full warm-up's state before the plan's first session (named by that session: a plan starting elsewhere
    needs its own)."""
    return out_dir(env, key) / "state" / f"state_warmup_{first.isoformat()}.json"


def log_file(env: Env, key: str, name: str) -> Path:
    return out_dir(env, key) / "logs" / f"{name}.txt"


def summary_file(env: Env, key: str, day: date) -> Path:
    return out_dir(env, key) / "days" / f"{day.isoformat()}.json"


# --- the plan -------------------------------------------------------------------------------------------------

def parse_months(text: str) -> list[int]:
    out = set()
    for part in text.split(","):
        a, _, b = part.partition("-")
        out.update(range(int(a), int(b or a) + 1))
    bad = [k for k in out if not 1 <= k <= 12]
    if bad:
        raise BuildRefused(f"months must be ledger months 1-12, got {bad}")
    return sorted(out)


def make_plan(env: Env, months: list[int], batch: int | None = None) -> dict[str, Any]:
    from nqbot.jobs.runner import record_plan

    if months != list(range(min(months), max(months) + 1)):
        # each session starts from the previous session's state: a gap would chain across it
        raise BuildRefused(f"ledger months {months} are not consecutive: the build chains each session's state "
                           "into the next, so plan one consecutive range (e.g. 1-12)")
    if env.features.get("dp_in_model"):
        raise BuildRefused("dp_in_model is true: dark-pool levels are not wired into the zones yet (owner 2026-11-01, "
                           "SPEC §13 Q-step11f: added and tested at the retrain that turns them on)")
    cal = env.cal
    pl = sb.plan(env)
    planned = sb.planned_sessions(pl, cal)
    want = {d for k in months for d in months_mod.days_of(k, env.settings)}
    sessions = [d for d in planned if d in want]
    if not sessions:
        raise BuildRefused(f"no planned session in ledger months {months}")
    gaps = [d for k in months for d in months_mod.days_of(k, env.settings)
            if cal_mod.is_trading_day(d, cal) and d not in set(planned)]
    batch = batch or max(1, (os.cpu_count() or 1) - 2)
    key = build_key(env)
    doc = {"months": months, "sessions": [d.isoformat() for d in sessions], "key": key, "batch": batch,
           "features_version": env.features["features_version"], "build_version": BUILD_VERSION,
           "not_planned": len(gaps)}
    atomic_write_json(plan_path(env), doc)
    record_plan(env.paths, JOB, chunk_ids(doc))
    by_month: dict[int, int] = {}
    for d in sessions:
        k = months_mod.month_index(d, env.settings)
        by_month[k] = by_month.get(k, 0) + 1
    env.echo(f"feature build plan: {len(sessions)} sessions in ledger months {months} "
             f"({', '.join(f'{k}: {n}' for k, n in sorted(by_month.items()))}); key {key}, pass-2 batches of {batch}")
    if gaps:
        env.echo(f"  {len(gaps)} trading days of those months have no planned Databento session (not built)")
    env.echo(f"  outputs: {out_dir(env, key)}")
    env.echo(f"  run: uv run nqbot run {JOB} --until 07:30")
    return doc


def chunk_ids(doc: dict[str, Any]) -> list[str]:
    key, n = doc["key"], int(doc["batch"])
    days = doc["sessions"]
    p0 = [f"p0:warmup:{key}"]
    p1 = [f"p1:{d}:{key}" for d in days]
    p2 = [f"p2:{k // n:03d}:{key}" for k in range(0, len(days), n)]
    return p0 + p1 + p2


def load_plan(env: Env) -> dict[str, Any]:
    p = plan_path(env)
    if not p.exists():
        raise BuildRefused(f"no feature-build plan: run `uv run nqbot features plan --months 1-12` first ({p})")
    doc = json.loads(p.read_text(encoding="utf-8"))
    key = build_key(env)
    if doc["key"] != key:
        raise BuildRefused(f"the configuration changed since the plan (key {doc['key']} -> {key}): re-plan with "
                           f"`uv run nqbot features plan --months {_months_text(doc['months'])}`")
    return doc


def _months_text(months: list[int]) -> str:
    return f"{min(months)}-{max(months)}" if months == list(range(min(months), max(months) + 1)) else \
        ",".join(map(str, months))


# --- one session (the child process) ----------------------------------------------------------------------------

def build_day_cmd(env: Env, day: date, pass_no: int) -> int:
    """`nqbot features build-day D --pass N`: one session of the plan (run by the job in its own process)."""
    doc = load_plan(env)
    key = doc["key"]
    days = [date.fromisoformat(x) for x in doc["sessions"]]
    if day not in days:
        raise BuildRefused(f"{day} is not a session of the feature-build plan")
    if pass_no == 2 and not state_file(env, key, day).exists():
        raise BuildRefused(f"{day}: pass 1 has not written {state_file(env, key, day).name}")
    t0 = _time.monotonic()
    pl = sb.plan(env)
    planned = sb.planned_sessions(pl, env.cal)
    damage = sb.load_damage(env)
    k = days.index(day)
    if k == 0:
        src = warmup_file(env, key, day)
        if not src.exists():
            raise BuildRefused(f"{day}: the warm-up state {src.name} is missing (chunk p0 runs first)")
    else:
        src = state_file(env, key, days[k - 1])
        if not src.exists():
            raise BuildRefused(f"{day}: the previous session's state {src.name} is missing (pass 1 runs in order)")
    state = sb.DayState.from_json(json.loads(src.read_text(encoding="utf-8")))
    before = [d for d in planned if d < day]
    want_prev = before[-1].isoformat() if before else None
    if state.day != want_prev:
        raise BuildRefused(f"{day}: {src.name} is the state after {state.day}, not after the planned session before "
                           f"it ({want_prev})")
    built = sb.build_day(env, day, state, pl, planned, damage)
    t_build = _time.monotonic() - t0
    sf = built.sf
    summary = {"day": day.isoformat(), "pass": pass_no, "events": len(sf.events), "passes": sf.passes,
               "build_sec": round(t_build, 1), "zones": len(sf.book.created), "es_zones": len(sf.es_book.created),
               "zone_recomputes": sf.book.recomputes, **built.info}
    if pass_no == 1:
        atomic_write_json(state_file(env, key, day), built.state_out.to_json())
    else:
        sp = state_file(env, key, day)
        want = sb.DayState.from_json(json.loads(sp.read_text(encoding="utf-8"))).digest()
        if built.state_out.digest() != want:
            raise BuildRefused(f"{day}: pass 2's end state differs from pass 1's checkpoint (the build is not "
                               "deterministic, or the data changed between the passes)")
        rows = sf.touch_rows()
        write_rows(out_dir(env, key) / "touches" / f"{day.isoformat()}.parquet", rows, {"day": day.isoformat(),
                                                                                         "key": key})
        write_rows(out_dir(env, key) / "daytype" / f"{day.isoformat()}.parquet", sf.daytype_rows(),
                   {"day": day.isoformat(), "key": key, "daytype_inputs": env.features["daytype_inputs"]})
        eod = sf.eod()
        outcomes = sf.outcome_rows() + ([{"event_id": f"eod-{day.isoformat()}", "day": day.isoformat(),
                                          "label": eod["day_type"], "how": "eod_day_type"}] if eod else [])
        write_rows(out_dir(env, key) / "outcomes" / f"{day.isoformat()}.parquet", outcomes,
                   {"day": day.isoformat(), "key": key, "note": "outcomes: never read as features"})
        summary.update(rows=len(rows), tradeable=sum(1 for r in rows if r["tradeable"]),
                       labels={lab: sum(1 for x in sf.labels.values() if x.label == lab)
                               for lab in ("held", "broke", "timeout")},
                       day_type=None if eod is None else eod["day_type"],
                       total_sec=round(_time.monotonic() - t0, 1))
    old = summary_file(env, key, day)
    prev_s = json.loads(old.read_text(encoding="utf-8")) if old.exists() else {}
    atomic_write_json(old, {**prev_s, f"pass{pass_no}": summary})
    env.echo(f"{day} pass {pass_no}: {len(sf.events)} events, {summary['build_sec']} s")
    return 0


def build_warmup_cmd(env: Env) -> int:
    """`nqbot features build-warmup`: the full warm-up before the plan's first session (owner 2026-11-01, SPEC §13
    Q-step11f (15)): every warm-up session built fully once there is a session to start from."""
    doc = load_plan(env)
    first = date.fromisoformat(doc["sessions"][0])
    pl = sb.plan(env)
    planned = sb.planned_sessions(pl, env.cal)
    t0 = _time.monotonic()
    state = sb.warmup_state(env, pl, planned, first, sb.load_damage(env), full=True)
    atomic_write_json(warmup_file(env, doc["key"], first), state.to_json())
    env.echo(f"warm-up before {first}: state after {state.day} ({_time.monotonic() - t0:.0f} s)")
    return 0


def write_rows(path: Path, rows: list[dict], meta: dict) -> None:
    """zstd parquet, written atomically; numbers as float32 except the time columns (§11 Low memory: features are
    float32); a column missing from some rows is null there."""
    keys: dict[str, None] = {}
    for r in rows:   # every column any row has (rows differ: golden-only, after-the-open, Skylit keys), first-seen order
        keys.update(dict.fromkeys(r))
    table = pa.Table.from_pylist([{k: r.get(k) for k in keys} for r in rows]) if rows else pa.table({})
    cols = []
    for name, col in zip(table.column_names, table.columns):
        # numbers as float32, counts too: an integer column one day and a float one the next would not concatenate
        if (pa.types.is_floating(col.type) or pa.types.is_integer(col.type)) and name not in ID_COLS:
            col = col.cast(pa.float32())
        cols.append(col)
    if rows:
        table = pa.table(cols, names=table.column_names)
    table = table.replace_schema_metadata({b"nqbot": json.dumps(meta, sort_keys=True, default=str).encode()})
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd")
    atomic_write_bytes(path, buf.getvalue())


def read_rows(paths: list[Path]) -> pa.Table:
    """Day files as one table: a session without Skylit boards (2025-10-01 to 10-06, before the 365-day reach) has no
    map columns, so columns missing from a day are filled with nulls (types promoted)."""
    tables = [pq.read_table(p) for p in paths]
    tables = [t for t in tables if t.num_columns]
    if not tables:
        return pa.table({})
    return pa.concat_tables([t.replace_schema_metadata(None) for t in tables], promote_options="permissive")


# --- the job --------------------------------------------------------------------------------------------------

def _child(argv: list[str], log: Path) -> subprocess.Popen:
    """One session in its own process; its whole output goes to `log` (a file, not a pipe: several children run at
    once and a full pipe buffer would stall one until it is read)."""
    code = ("import sys, psutil; from nqbot.cli import main; rc = main(sys.argv[1:]); i = psutil.Process().memory_info(); "
            f"print('{PEAK_TAG}', getattr(i, 'peak_wset', 0) / 2**30 or i.rss / 2**30, flush=True); sys.exit(rc)")
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w", encoding="utf-8") as fh:   # the child keeps its own handle; ours closes at once
        return subprocess.Popen([sys.executable, "-c", code, *argv], stdout=fh, stderr=subprocess.STDOUT,
                                env={**os.environ, "PYTHONUTF8": "1"})


def _wait(p: subprocess.Popen, log: Path) -> str:
    p.wait()
    return log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""


def _peak(stdout: str) -> float | None:
    for line in reversed((stdout or "").splitlines()):
        if line.startswith(PEAK_TAG):
            return round(float(line.split()[1]), 3)
    return None


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
        return chunk_ids(load_plan(self._env(ctx)))

    def run_chunk(self, chunk_id: str, ctx) -> dict | None:
        from nqbot.checks.step8b_disloc import free_mb
        from nqbot.jobs.runner import StopChunk

        env = self._env(ctx)
        doc = load_plan(env)
        kind, which, key = chunk_id.split(":")
        days = [date.fromisoformat(x) for x in doc["sessions"]]
        if kind == "p0":
            if warmup_file(env, key, days[0]).exists():
                return None
            mb = free_mb()
            if mb is not None and mb < (EST_PEAK_GB + MIN_FREE_GB_EXTRA) * 1024:
                raise StopChunk(f"{mb} MB free before the warm-up")
            log = log_file(env, key, "warmup")
            proc = _child(["features", "build-warmup"], log)
            out = _wait(proc, log)
            if proc.returncode != 0:
                ctx.log.error("%s: warm-up failed (exit %s), log %s: %s", JOB, proc.returncode, log, out[-600:])
                raise StopChunk(f"the warm-up failed (exit {proc.returncode}; log {log})", status="failures")
            ctx.log.info("%s: warm-up done, process peak %s GB", JOB, _peak(out))
            return {"peak_gb": _peak(out)}
        if kind == "p1":
            d = date.fromisoformat(which)
            if state_file(env, key, d).exists():
                return None
            mb = free_mb()
            if mb is not None and mb < (EST_PEAK_GB + MIN_FREE_GB_EXTRA) * 1024:
                raise StopChunk(f"{mb} MB free before {d} (pass 1)")
            peak = self._run([d], 1, ctx)
            return {"peak_gb": peak}
        n = int(doc["batch"])
        k = int(which)
        done = lambda d: pass2_done(env, key, d)
        peak = None
        while True:
            todo = [d for d in days[k * n:(k + 1) * n] if not done(d)]
            if not todo:
                return {"peak_gb": peak}
            why = ctx.should_stop()
            if why:
                raise StopChunk(why)
            mb = free_mb()
            per = max(EST_PEAK_GB, _measured_peak(env, key) or 0.0)
            take = min(len(todo), ctx.max_workers or n, n)
            if mb is not None:
                fit = int((mb / 1024 - MIN_FREE_GB_EXTRA) // per)
                if fit < 1:
                    raise StopChunk(f"{mb} MB free: not enough for one pass-2 session ({per:.1f} GB)")
                take = min(take, fit)
            pk = self._run(todo[:take], 2, ctx)   # the rest of the batch runs next, as memory allows
            peak = pk if peak is None else max(peak, pk or 0.0)

    def _run(self, days: list[date], pass_no: int, ctx) -> float | None:
        from nqbot.jobs.runner import StopChunk

        env = self._env(ctx)
        key = load_plan(env)["key"]
        procs = []
        for d in days:
            log = log_file(env, key, f"{d.isoformat()}_p{pass_no}")
            procs.append((d, log, _child(["features", "build-day", d.isoformat(), "--pass", str(pass_no)], log)))
        peaks, failed = [], []
        for d, log, p in procs:
            out = _wait(p, log)
            pk = _peak(out)
            if pk is not None:
                peaks.append(pk)
            if p.returncode != 0:
                failed.append((d, p.returncode, log, out[-600:]))
            else:
                ctx.log.info("%s: %s pass %d done, process peak %s GB", JOB, d, pass_no, pk)
        if failed:
            for d, rc, log, msg in failed:
                ctx.log.error("%s: %s pass %d failed (exit %s), log %s: %s", JOB, d, pass_no, rc, log, msg)
            raise StopChunk(f"{len(failed)} session(s) failed in pass {pass_no}: "
                            f"{', '.join(str(d) for d, *_ in failed)} (see the log)", status="failures")
        return max(peaks) if peaks else None

    def at_end(self, ctx) -> None:
        env = self._env(ctx)
        path = write_report(env, load_plan(env))
        ctx.log.info("%s: report %s", JOB, path)


def pass2_done(env: Env, key: str, day: date) -> bool:
    """Pass 2 wrote every file of `day`: the summary's pass-2 entry is written last, after the three parquet files
    (a run stopped in between leaves no entry, so the session is built again)."""
    s = summary_file(env, key, day)
    if not s.exists():
        return False
    try:
        return "pass2" in json.loads(s.read_text(encoding="utf-8"))
    except ValueError:
        return False


def _measured_peak(env: Env, key: str) -> float | None:
    from nqbot.jobs.runner import load_state, state_path

    st = load_state(state_path(env.paths, JOB))
    vals = [v.get("child_peak_gb") or v.get("peak_gb") for c, v in (st.get("done") or {}).items()
            if c.endswith(key) and c.startswith("p2")]
    vals = [v for v in vals if v]
    return max(vals) if vals else None


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
        s = summary_file(env, key, d)
        x = json.loads(s.read_text(encoding="utf-8")) if s.exists() else {}
        if state_file(env, key, d).exists():
            m["p1"] += 1
        p2 = x.get("pass2")
        if p2:
            m["p2"] += 1
            m["events"] += p2.get("events", 0)
            m["rows"] += p2.get("rows", 0)
            if k in DEV_MONTHS:
                dev_rows.append((d, p2))
    lines = [f"# Feature build (step 11 part 3), key {key}", "",
             f"features_version {doc['features_version']}, build version {doc['build_version']}; outputs under "
             f"`{out_dir(env, key)}`. Months 7-12 are counts only (§7 rule 2b: walk-forward and sealed months appear "
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


# --- the one-day smoke run (development days only) ----------------------------------------------------------------

def build_day_smoke(env: Env, day: date, full_warmup: bool = False) -> Path:
    import psutil

    from nqbot.features.session_tape import ET, NS

    months_mod.require_development(day, env.settings)
    proc = psutil.Process(os.getpid())
    t0 = _time.monotonic()
    pl = sb.plan(env)
    planned = sb.planned_sessions(pl, env.cal)
    if day not in planned:
        raise BuildRefused(f"{day} is not a planned trading session")
    damage = sb.load_damage(env)
    state = sb.warmup_state(env, pl, planned, day, damage, full=full_warmup)
    t_warm = _time.monotonic() - t0
    built = sb.build_day(env, day, state, pl, planned, damage)
    t_build = _time.monotonic() - t0 - t_warm
    sf = built.sf
    rows = sf.touch_rows()
    t_rows = _time.monotonic() - t0 - t_warm - t_build
    dtr = sf.daytype_rows()
    rss1 = proc.memory_info()
    peak1 = getattr(rss1, "peak_wset", 0) / 2**30 or rss1.rss / 2**30
    again = sb.build_day(env, day, state, pl, planned, damage)
    same = again.state_out.digest() == built.state_out.digest()
    same_rows = [r["event_id"] for r in again.sf.touch_rows()] == [r["event_id"] for r in rows]
    peak = getattr(proc.memory_info(), "peak_wset", 0) / 2**30 or rss1.rss / 2**30
    from datetime import datetime
    fmt = lambda ns: datetime.fromtimestamp(ns / NS, ET).strftime("%H:%M:%S")
    by_hour: dict[str, int] = {}
    for e in sf.events:
        h = fmt(e.t)[:2]
        by_hour[h] = by_hour.get(h, 0) + 1
    lab = {x: sum(1 for v in sf.labels.values() if v.label == x) for x in ("held", "broke", "timeout")}
    eod = sf.eod()
    lines = [f"# Step 11 build-day smoke run: {day}", "",
             "Stored data only (\\$0, no lock). The part-3 session engine (`features/session_features.py`) on the part-1 "
             "and part-2 engines, from " + ("a full warm-up build (as the feature-build job)" if full_warmup else
                                             "a carry-only warm-up replay (touch history starts empty)") + ".", "",
             f"- Multi-day touch records carried into the day: {len(state.nq.get('touch_history') or [])}; retested "
             f"extremes in force {len(state.nq.get('retested') or [])}; retest candidates "
             f"{len(state.nq.get('retest_candidates') or [])}; yesterday's day type "
             f"{state.nq.get('yesterday_day_type')}",
             f"- Wall time: warm-up replay {t_warm:.0f} s, build (zones, touches, labels) {t_build:.0f} s, rows "
             f"{t_rows:.0f} s; peak process memory {peak1:.2f} GB after one build ({peak:.2f} GB after the second)",
             f"- Built twice from the same state: end state identical {same}, same events {same_rows}",
             f"- NQ {built.info['nq_symbol']} ({built.info['nq_trades']:,} trades), ES {built.info['es_symbol']}; "
             f"Skylit: {built.info['skylit']}; gameplan: {built.info['gameplan']}; retest passes {sf.passes}",
             f"- NQ zones founded {len(sf.book.created)} (book recomputes {sf.book.recomputes:,} of "
             f"{sf.book.candidates:,} candidate instants); ES zones {len(sf.es_book.created)}",
             f"- Events {len(sf.events)} (level {sum(1 for e in sf.events if e.kind == 'level')}, golden "
             f"{sum(1 for e in sf.events if e.kind == 'golden')}, gameplan "
             f"{sum(1 for e in sf.events if e.kind == 'gameplan')}); tradeable {sum(1 for r in rows if r['tradeable'])}; "
             f"touch groups {len({e.group for e in sf.events})}",
             f"- Labels: held {lab['held']}, broke {lab['broke']}, timeout {lab['timeout']}; EOD day type "
             f"{None if eod is None else eod['day_type']}; day-type rows {len(dtr)}; row columns "
             f"{len(rows[0]) if rows else 0}", "",
             "## Events per hour (ET)", "", "| hour | events |", "|---|---|"]
    for h in sorted(by_hour):
        lines.append(f"| {h} | {by_hour[h]} |")
    lines += ["", "## RTH events (first 40)", "",
              "| touch | zone | kind | dir | price | members | label | resolved | tradeable |",
              "|---|---|---|---|---|---|---|---|---|"]
    rth = [(e, r) for e, r in zip(sf.events, rows) if e.t >= sf.open_ns][:40]
    for e, r in rth:
        lb = sf.labels[e.event_id]
        mem = ", ".join(sorted({sf.nq_levels[m].sub_type for m in e.members})) if e.kind == "level" else e.kind
        lines.append(f"| {fmt(e.t)} | {e.zone_id} | {e.kind}{'/' + e.part if e.part else ''} | {e.direction:+d} | "
                     f"{e.price:g} | {mem} | {lb.label} | {fmt(lb.t_resolved)} | {r['tradeable']} |")
    sample = [k for k in SAMPLE_KEYS if rows and k in rows[0]]
    if rth:
        lines += ["", "## Sample row values (first 5 RTH events)", "", "| feature | " +
                  " | ".join(fmt(e.t) for e, _ in rth[:5]) + " |", "|---|" + "---|" * len(rth[:5])]
        for k in sample:
            lines.append(f"| {k} | " + " | ".join(_fmt(r.get(k)) for _, r in rth[:5]) + " |")
    path = env.paths.reports_dir / "step11" / f"build_day_{day.isoformat()}.md"
    atomic_write_text(path, "\n".join(lines) + "\n")
    env.echo(f"  report {path} (warm-up {t_warm:.0f} s, build {t_build:.0f} s, rows {t_rows:.0f} s, "
             f"peak {peak:.2f} GB, deterministic {same and same_rows})")
    return path


SAMPLE_KEYS = ("stack_count", "zone_width", "largest_pct_of_king", "king_present", "tests_so_far_max",
               "holds_so_far_max", "zone_tests_so_far", "held_today", "broke_today", "yesterday_day_type",
               "trinity_count", "es_at_level", "es_stack_count", "qqq_pika_cloud_overlaps_zone", "qqq_pocket_ahead_size",
               "approach_move_15", "approach_straightness_15", "with_day_direction", "vwap_side_vs_trade",
               "dt_vwap_crosses", "dt_new_extreme_skew", "rs_nq_es_5", "distance_to_level_center",
               "next_zone_above_pts", "n_post_cross", "gp_in_zone", "tradeable")


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3g}"
    return str(v)
