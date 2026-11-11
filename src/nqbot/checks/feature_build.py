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
NS_FLOAT_OK: frozenset = frozenset()   # float columns that must stay float64 (none: every time column is an integer)


# Q-dryrun13 (6) (owner 2026-11-09): the key covers only the configuration the build reads. These settings are read
# by the simulators, the models, the gates or live trading (execution costs and parameters are applied in the simulator,
# never stored in the build), so changing them never renames a build; `tests/test_feature_build.py` fails if the build
# reads one of them.
NON_BUILD_FEATURE_KEYS = frozenset({"tune_grids_13", "tune_grids_19"})
NON_BUILD_SETTING_KEYS = frozenset({
    "min_free_gb", "min_free_gb_live", "backup", "min_month1_sessions", "min_new_sealed_months", "grid_sample_frac",
    "ops", "archive_time", "heartbeat_min", "inactivity_alert_days", "retrain_every_months",
    "default_risk_usd", "max_entry_slip_ticks", "exit_slippage_ticks", "default_commission_per_mini_rt",
    "default_commission_per_micro_rt", "exit_retry_window_sec", "flat_retry_min", "flat_alert_sec",
    "unknown_alert_min", "webhook_timeout_sec", "entry_ttl_sec", "stop_buffer", "stop_buffer_floor", "entry_order_type",
    "enabled_entry_styles", "style_tie_ev_r", "pair_ext_min_rr", "pair_ext_max_zones", "baseline_rr", "lives_dust_usd", "max_loss_run_rate", "be_trigger", "be_offset_ticks",
    "guard_check_sec", "guard_flat_grace_sec", "max_engine_restarts", "guard_port", "engine_port", "ui_port",
    "sleep_detect_sec", "clock_max_offset_ms", "clock_max_rtt_ms", "clock_check_min", "ntp_server", "feed_stale_sec",
    "feed_lag_max_ms", "feed_recovery_sec", "deadman_url", "power_poll_sec", "battery_warn_step_pct",
    "battery_entry_floor_pct", "skylit_credit_warn", "skylit_reach_margin_min", "skylit_max_failures_in_row",
    "skylit_dropped_extra_tries", "skylit_dropped_retry_credits_per_night", "skylit_alongside_reserve_credits",
    "skylit_pack_margin", "skylit_credit_packs", "calendar_poll_min", "calendar_feed_url", "gameplan_model",
    "gameplan_effort", "gameplan_max_tokens", "gameplan_usd_per_mtok", "gameplan_max_usd_per_file", "tuning_start",
    "threshold_grid", "last_life_grid", "profile_ev_quantiles", "profile_risk_multiplier", "style_set_grid",
    "recency_halflife_months", "lgbm_grid", "tune_13", "ablation_groups", "bootstrap_paths", "default_max_three_loss_rate",
    "gate_account", "gates"})
# Q-dryrun13 (2): run-time overrides are for the step-13 tuning on months 1-4 only; the full-window build has none
OVERRIDE_MONTHS = frozenset({1, 2, 3, 4})


class BuildRefused(Exception):
    pass


# --- keys and paths -------------------------------------------------------------------------------------------

CODE_PARTS = ("features", "events", "checks/session_build.py", "checks/feature_build.py", "calendar.py",
              "clients/databento_store.py", "clients/skylit_store.py", "gameplan/parser.py")
# events/ = the step-13b engine stage (Q-dryrun13 (7)); the labeler (labeler/) has its own key (SPEC §13 Q-step13b (1))


def code_digest() -> str:
    """sha256 of the source that makes the build's outputs (line endings normalised, so both machines agree): a code
    change renames the build like a config change, so a resume never mixes outputs of two versions."""
    root = Path(__file__).resolve().parent.parent
    h = hashlib.sha256()
    for part in CODE_PARTS:
        p = root / part
        files = sorted(p.rglob("*.py")) if p.is_dir() else [p]
        for f in files:
            h.update(f.relative_to(root).as_posix().encode())
            h.update(f.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()


def key_config(env: Env) -> dict[str, Any]:
    """The configuration the build key covers (Q-dryrun13 (6)): features.yaml without the tuning grids, settings.yaml
    without the per-machine keys and the keys only the simulators, models, gates and live trading read."""
    feats = {k: v for k, v in env.features.items() if k not in NON_BUILD_FEATURE_KEYS}
    sett = {k: v for k, v in env.settings.items() if k not in LOCAL_SETTING_KEYS and k not in NON_BUILD_SETTING_KEYS}
    return {"features": feats, "settings": sett}


def build_key(env: Env) -> str:
    cal = env.cal if env.cal is not None else cal_mod.load()
    blob = json.dumps({**key_config(env), "calendar": cal, "build": BUILD_VERSION, "code": code_digest()},
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def out_dir(env: Env, key: str) -> Path:
    """Every build key has its own folder (Q-dryrun13 (7)): state, touches, day types, outcomes and, from step 13b,
    events, pairs and paths, so variant runs never overwrite each other."""
    return env.paths.data_dir / "processed" / "features" / f"v{env.features['features_version']}_{key}"


def events_dir(env: Env, key: str) -> Path:
    return out_dir(env, key) / "events"


def pairs_dir(env: Env, key: str) -> Path:
    return out_dir(env, key) / "pairs"


def paths_dir(env: Env, key: str) -> Path:
    return out_dir(env, key) / "paths"


def build_plan_copy(env: Env, key: str) -> Path:
    """The plan as made, kept in the build's own folder: a build is found by its key, not by the job's one plan."""
    return out_dir(env, key) / "build_plan.json"


# --- run-time overrides (Q-dryrun13 (2), owner 2026-11-09) --------------------------------------------------------

def parse_overrides(items: list[str] | None) -> dict[str, Any]:
    """`key=value` (value read as YAML; `a.b=v` sets one entry of a mapping, e.g. `flip_margin.vwap=2`)."""
    from ruamel.yaml import YAML

    yaml = YAML(typ="safe")
    out: dict[str, Any] = {}
    for it in items or []:
        k, sep, v = it.partition("=")
        if not sep or not k.strip():
            raise BuildRefused(f"an override is key=value, not {it!r}")
        out[k.strip()] = yaml.load(v)
    return out


def apply_overrides(features: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """A copy of `features` with the overrides applied; refuses an unknown key, the version, or a tuning grid."""
    import copy

    f = copy.deepcopy(features)
    for k, v in (overrides or {}).items():
        top, _, sub = k.partition(".")
        if top not in f:
            raise BuildRefused(f"override {k!r}: no such key in features.yaml")
        if top == "features_version" or top in NON_BUILD_FEATURE_KEYS:
            raise BuildRefused(f"override {k!r}: not a build parameter")
        if sub:
            if not isinstance(f[top], dict) or sub not in f[top]:
                raise BuildRefused(f"override {k!r}: {top} has no entry {sub!r}")
            f[top][sub] = v
        else:
            f[top] = v
    return f


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


def make_plan(env: Env, months: list[int], batch: int | None = None,
              overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """`overrides` (Q-dryrun13 (2)): run-time changes of features.yaml values for this build only, stored in the plan,
    covered by its key and applied by every session; the committed config is untouched. Only for builds of the step-13
    tuning months 1-4 (the full-window build refuses any)."""
    from nqbot.jobs.runner import record_plan

    overrides = dict(overrides or {})
    if overrides and not set(months) <= OVERRIDE_MONTHS:
        raise BuildRefused(f"run-time overrides are for the step-13 tuning on ledger months 1-4 only (months {months}); "
                           "a full-window build uses the committed configuration")
    if overrides:
        env.features = apply_overrides(env.features, overrides)
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
           "not_planned": len(gaps), "overrides": overrides}
    atomic_write_json(plan_path(env), doc)
    atomic_write_json(build_plan_copy(env, key), doc)
    record_plan(env.paths, JOB, chunk_ids(doc))
    by_month: dict[int, int] = {}
    for d in sessions:
        k = months_mod.month_index(d, env.settings)
        by_month[k] = by_month.get(k, 0) + 1
    env.echo(f"feature build plan: {len(sessions)} sessions in ledger months {months} "
             f"({', '.join(f'{k}: {n}' for k, n in sorted(by_month.items()))}); key {key}, pass-2 batches of {batch}")
    if gaps:
        env.echo(f"  {len(gaps)} trading days of those months have no planned Databento session (not built)")
    if overrides:
        env.echo(f"  run-time overrides (this build only): {json.dumps(overrides, sort_keys=True)}")
    env.echo(f"  outputs: {out_dir(env, key)}")
    env.echo(f"  run: uv run nqbot run {JOB} --until 07:30")
    return doc


def chunk_ids(doc: dict[str, Any]) -> list[str]:
    """`<pass>:<what>:<key>:<first session>`: the build key and the chain's first session (its warm-up root) name
    every chunk, and a pass-2 chunk names its first and last session, so a re-plan (other months, another batch size)
    never inherits a chunk finished for another chain or another set of days."""
    key, n = doc["key"], int(doc["batch"])
    days = doc["sessions"]
    root = days[0]
    p0 = [f"p0:warmup:{key}:{root}"]
    p1 = [f"p1:{d}:{key}:{root}" for d in days]
    p2 = [f"p2:{days[k]}~{days[min(k + n, len(days)) - 1]}:{key}:{root}" for k in range(0, len(days), n)]
    return p0 + p1 + p2


def load_plan(env: Env) -> dict[str, Any]:
    p = plan_path(env)
    if not p.exists():
        raise BuildRefused(f"no feature-build plan: run `uv run nqbot features plan --months 1-12` first ({p})")
    doc = json.loads(p.read_text(encoding="utf-8"))
    if doc.get("overrides"):   # the plan's run-time overrides apply to every session of it (Q-dryrun13 (2))
        env.features = apply_overrides(env.features, doc["overrides"])
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
    src = start_file(env, key, days, day)
    if not src.exists():
        raise BuildRefused(f"{day}: the warm-up state {src.name} is missing (chunk p0 runs first)" if days.index(day) == 0
                           else f"{day}: the previous session's state {src.name} is missing (pass 1 runs in order)")
    state = sb.DayState.from_json(json.loads(src.read_text(encoding="utf-8")))
    source = state.digest()
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
    summary["source"] = source
    if pass_no == 1:
        atomic_write_json(state_file(env, key, day), {**built.state_out.to_json(), "source": source})
    else:
        sp = state_file(env, key, day)
        doc_s = json.loads(sp.read_text(encoding="utf-8"))
        if doc_s.get("source") != source:
            raise BuildRefused(f"{day}: pass 1's checkpoint was built from another starting state (re-planned or "
                               "rebuilt since): pass 1 must run again first")
        want = sb.DayState.from_json(doc_s).digest()
        if built.state_out.digest() != want:
            raise BuildRefused(f"{day}: pass 2's end state differs from pass 1's checkpoint (the build is not "
                               "deterministic, or the data changed between the passes)")
        rows, eod, ev_info = write_day_files(out_dir(env, key), sf, day, key, env.features, settings=env.settings,
                                             damage_day=damage.get(day), events=True)
        summary.update(ev_info)
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


def write_day_files(base: Path, sf, day: date, key: str, features: dict, *, settings: dict | None = None,
                    damage_day=None, events: bool = False) -> tuple[list[dict], dict | None, dict]:
    """Pass 2's files of a session under `base` (touches/, daytype/, outcomes/, levels/ and, with `events`, the
    step-13b engine stage: events/, decisions/, decision_rows/, level_events/, `events.build`); returns the touch rows,
    the EOD day type and the engine stage's counts. The smoke run writes them the same way (to a temporary folder) and
    reads them back; the step-13-0 check harness writes no engine stage."""
    iso = day.isoformat()
    rows = sf.touch_rows()
    write_rows(base / "touches" / f"{iso}.parquet", rows, {"day": iso, "key": key})
    write_rows(base / "daytype" / f"{iso}.parquet", sf.daytype_rows(),
               {"day": iso, "key": key, "daytype_inputs": features["daytype_inputs"]})
    eod = sf.eod()
    outcomes = sf.outcome_rows() + ([{"event_id": f"eod-{iso}", "day": iso, "label": eod["day_type"],
                                      "how": "eod_day_type"}] if eod else [])
    write_rows(base / "outcomes" / f"{iso}.parquet", outcomes,
               {"day": iso, "key": key, "note": "outcomes: never read as features"})
    write_levels(base / "levels" / f"{iso}.parquet", sf, {"day": iso, "key": key})
    info: dict = {}
    if events:
        from nqbot.events import build as ev_build

        info = ev_build.write_session(base, sf, rows, damage_day, settings, key)
    return rows, eod, info


def write_levels(path: Path, sf, meta: dict) -> None:
    """The session's level timelines (step 13a, Q-dryrun13 (3); `features.level_timeline`): float64 band values (prices,
    not features), zstd parquet, written atomically."""
    rows, unit = sf.level_timeline()
    table = pa.Table.from_pylist(rows) if rows else pa.table({})
    table = table.replace_schema_metadata({b"nqbot": json.dumps({**meta, **unit}, sort_keys=True, default=str).encode()})
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd")
    atomic_write_bytes(path, buf.getvalue())


def read_levels(path: Path) -> tuple[pa.Table, dict]:
    """A session's level timelines and its metadata (unit values)."""
    t = pq.read_table(path)
    meta = json.loads((t.schema.metadata or {}).get(b"nqbot", b"{}"))
    return t.replace_schema_metadata(None), meta


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
    """zstd parquet, written atomically; floats as float32 (§11 Low memory: features are float32), integers and the
    time columns as int64; a column missing from some rows is null there."""
    keys: dict[str, None] = {}
    for r in rows:   # every column any row has (rows differ: golden-only, after-the-open, Skylit keys), first-seen order
        keys.update(dict.fromkeys(r))
    table = pa.Table.from_pylist([{k: r.get(k) for k in keys} for r in rows]) if rows else pa.table({})
    cols = []
    for name, col in zip(table.column_names, table.columns):
        # floats as float32 (features); integers stay int64: counts are exact, and nanosecond times (an outcome's
        # t_resolved) don't fit float32; a column that is int one day and float the next is merged by read_rows
        if pa.types.is_floating(col.type) and name not in ID_COLS and name not in NS_FLOAT_OK:
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
        kind, which, key, _root = chunk_id.split(":")
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
            if pass1_done(env, key, days, d):
                return None
            mb = free_mb()
            if mb is not None and mb < (EST_PEAK_GB + MIN_FREE_GB_EXTRA) * 1024:
                raise StopChunk(f"{mb} MB free before {d} (pass 1)")
            peak = self._run([d], 1, ctx)
            return {"peak_gb": peak}
        n = int(doc["batch"])
        d0, d1 = (date.fromisoformat(x) for x in which.split("~"))
        done = lambda d: pass2_done(env, key, d, days)
        peak = None
        while True:
            todo = [d for d in days if d0 <= d <= d1 and not done(d)]
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


def start_file(env: Env, key: str, days: list[date], day: date) -> Path:
    """The state `day` starts from: the warm-up before the plan's first session, else the previous session's."""
    k = days.index(day)
    return warmup_file(env, key, day) if k == 0 else state_file(env, key, days[k - 1])


def _digest_of(path: Path) -> str | None:
    try:
        return sb.DayState.from_json(json.loads(path.read_text(encoding="utf-8"))).digest()
    except (OSError, ValueError, KeyError):
        return None


def _source_of(path: Path, part: str | None = None) -> str | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    d = d.get(part) if part else d
    return d.get("source") if isinstance(d, dict) else None


def pass1_done(env: Env, key: str, days: list[date], day: date) -> bool:
    """`day`'s checkpoint exists and was built from the state it starts from now (a re-plan or a rebuilt earlier
    session makes it stale, and it is built again; so are the sessions after it, in turn)."""
    sf = state_file(env, key, day)
    want = _digest_of(start_file(env, key, days, day))
    return sf.exists() and want is not None and _source_of(sf) == want


def pass2_done(env: Env, key: str, day: date, days: list[date] | None = None) -> bool:
    """Pass 2 wrote every file of `day`: the summary's pass-2 entry is written last, after the three parquet files
    (a run stopped in between leaves no entry, so the session is built again). With `days` (the job), also built
    from the state the session starts from now."""
    s = summary_file(env, key, day)
    if not s.exists():
        return False
    try:
        p2 = json.loads(s.read_text(encoding="utf-8")).get("pass2")
    except ValueError:
        return False
    if not p2:
        return False
    if days is None:
        return True
    return p2.get("source") is not None and p2.get("source") == _digest_of(start_file(env, key, days, day))


def _measured_peak(env: Env, key: str) -> float | None:
    from nqbot.jobs.runner import load_state, state_path

    st = load_state(state_path(env.paths, JOB))
    vals = [v.get("child_peak_gb") or v.get("peak_gb") for c, v in (st.get("done") or {}).items()
            if f":{key}:" in c and c.startswith("p2")]
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
    # pass 2's file writing on the real rows, read back (a temporary folder on the SSD, removed after)
    import shutil
    tmp = env.paths.tmp_dir / f"build_day_smoke_{day.isoformat()}"
    try:
        write_day_files(tmp, sf, day, "smoke", env.features, settings=env.settings, damage_day=damage.get(day),
                        events=True)
        back = {part: read_rows([tmp / part / f"{day.isoformat()}.parquet"]) for part in ("touches", "daytype", "outcomes")}
        files_ok = (back["touches"].num_rows == len(rows) and back["daytype"].num_rows == len(dtr)
                    and back["outcomes"].num_rows >= len(sf.events)
                    and back["touches"].num_columns == len({k for r in rows for k in r}))
        lv_path = tmp / "levels" / f"{day.isoformat()}.parquet"
        lv, _meta = read_levels(lv_path)
        lv_keys = lv.column("key").to_pylist() if lv.num_rows else []
        files_ok = files_ok and lv.num_rows == len(sf.nq_levels) + sum(
            1 for x in sf.es_levels.values() if x.rtype == "gex_node")
        files_note = (f"{back['touches'].num_rows} touch rows x {back['touches'].num_columns} columns, "
                      f"{back['daytype'].num_rows} day-type rows, {back['outcomes'].num_rows} outcome rows, "
                      f"{lv.num_rows} level timelines ({lv_path.stat().st_size / 2**20:.1f} MB; "
                      + ", ".join(f"{k} {lv_keys.count(k)}" for k in sorted(set(lv_keys))) + ")")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
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
             f"- Pass-2 files written and read back: {'ok' if files_ok else 'MISMATCH'} ({files_note})",
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
             f"peak {peak:.2f} GB, deterministic {same and same_rows}, files {'ok' if files_ok else 'MISMATCH'})")
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
