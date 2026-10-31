"""Step 11 checks on stored data (read-only, $0, no lock; §12 step 11).

- `chart_day(env, day)`: the one-day smoke run of the chart-level engine (owner 2026-10-31: one development day). The
  carried state comes from the `warmup_sessions` sessions before the day, replayed in carry-only mode (no level
  machines); features and levels are reported for the day only. Report `<reports_dir>/step11/chart_day_<day>.md`.
- `roll_report(env)`: F9.10's roll report (`rvol_today` at the RTH close and the median `vol_tod_60` by session
  relative to each roll day, −5…+5, months 1–6). Built; not run until the owner asks (no full-window runs yet).
"""

from __future__ import annotations

import os
import time as _time
from datetime import date, time
from typing import Any

import numpy as np
import psutil

from nqbot.checks.step8b import Env
from nqbot.clients.databento_contracts import session_before
from nqbot.clients.skylit_store import SkylitStore
from nqbot.features import chart_levels as cl
from nqbot.features import damage as dmg
from nqbot.features import expected_move as emod
from nqbot.features import rvol
from nqbot.features import session_tape as st
from nqbot.features import tpo
from nqbot.features.carry import CarryState
from nqbot.months import require_development
from nqbot.storage import atomic_write_text
from nqbot.warmup import warmup_sessions

class Refused(Exception):
    """The check can't run on that day (not a planned session)."""


SAMPLE_TIMES = (time(9, 45), time(10, 0), time(10, 31), time(12, 0), time(15, 30))
SAMPLE_KEYS = ("price", "atr", "em_pts", "rvol_today", "vwap18", "vwap0930", "daily_in_vah_zone", "daily_in_val_zone",
               "daily_va_depth", "weekly_in_vah_zone", "weekly_in_val_zone", "prev_poc_naked", "open_vs_prev_va",
               "prev_va_overlap", "dpoc_drift_em", "poc_migration_em_abs", "running_sigma", "em_line_above_k",
               "em_line_below_k", "or_fib_drawing", "or_fib_orientation", "or_fib_flips", "or_range_pts_1000",
               "or_fib_redrawn_1030", "or_range_vs_avg", "or_left_ib_since_lock", "or_range_broke_on")


def _plan(env: Env) -> dict[str, Any]:
    import json

    return json.loads((env.paths.data_dir / "jobs" / "databento_backfill" / "plan.json").read_text(encoding="utf-8"))


def _sessions(plan: dict[str, Any], root: str, cal) -> list[date]:
    from nqbot import calendar as cal_mod

    return sorted(d for d in (date.fromisoformat(x) for x in plan["front"][root]) if cal_mod.is_trading_day(d, cal))


def _session_back(day: date, n: int, cal) -> date:
    d = day
    for _ in range(n):
        d = session_before(d, cal)
    return d


def _replay_carry(env: Env, plan, root: str, days: list[date], damage: dict, minute_vols: dict) -> CarryState:
    """Carry-only replay: hand each session's levels and statistics on, no level machines."""
    carry = CarryState(root)
    rolls = env.db.rolls()
    for d in days:
        tape = st.load_tape(env.db, plan, root, d)
        if tape is None:
            env.echo(f"  {root} {d}: no stored trades, skipped")
            continue
        cl.start_session(carry, d, tape.symbol, cl.roll_spread_of(root, d, rolls))
        sp = tpo.SinglePrints(env.features["tpo_tick_pts"], env.features["sp_min_ticks"],
                              env.features["max_zone_width"]).run_session(
            tape, d, [tpo.Run.from_json(r) for r in carry.single_prints])
        dd = damage.get(d)
        if root == "NQ":
            minute_vols[d] = rvol.rth_minute_volumes(tape, env.cal)
        cl.fold_session(carry, tape, env.features, env.cal, dropped=bool(dd and dd.rth_dropped),
                        single_prints=sp.carry())
        del tape
    return carry


def chart_day(env: Env, day: date) -> Any:
    t0 = _time.monotonic()
    proc = psutil.Process(os.getpid())
    require_development(day, env.settings)
    f, cal = env.features, env.cal
    plan = _plan(env)
    damage = dmg.load(env.paths.reports_dir, env.settings)
    sessions = _sessions(plan, "NQ", cal)
    if day not in sessions:
        raise Refused(f"{day} is not a planned trading session")
    n = warmup_sessions(f)
    prior = [d for d in sessions if d < day][-n:]
    if not prior:
        raise Refused(f"{day}: no planned session before it to build the carry from")
    env.echo(f"step 11 chart-day {day}: carry from {len(prior)} prior sessions ({prior[0]} .. {prior[-1]}), "
             f"stored data only ($0, no lock)")
    minute_vols: dict[date, np.ndarray] = {}
    carry_nq = _replay_carry(env, plan, "NQ", prior, damage, minute_vols)
    carry_es = _replay_carry(env, plan, "ES", prior, damage, {})
    rows = emod.TempestRows.load(SkylitStore(env.paths))
    lag = f["tempest_lag_sessions"]
    rolls = env.db.rolls()
    normals = rvol.baseline(day, sessions, minute_vols.get, n=f["vol_baseline_sessions"],
                            damaged={d for d, x in damage.items() if x.rth_dropped}, cal=cal)
    dd = damage.get(day)
    out = {}
    tapes = {}
    for root, carry in (("NQ", carry_nq), ("ES", carry_es)):
        tape = st.load_tape(env.db, plan, root, day)
        if tape is None:
            raise Refused(f"{root} {day}: no stored trades")
        if not carry.rth:
            raise Refused(f"{root} {day}: no prior session folded into the carry")
        tapes[root] = tape
        spread = cl.roll_spread_of(root, day, rolls)
        roll = cl.start_session(carry, day, tape.symbol, spread)
        row = rows.get(emod.TEMPEST_SYMBOL[root], _session_back(day, lag, cal))
        inp = cl.SessionInputs(f, env.settings, cal, normals if root == "NQ" else None, row,
                               eth_empty=bool(dd and dd.eth_empty))
        if root == "ES":
            o = st.rth_open_ns(day)
            nq_t, es_t = tapes["NQ"], tape
            nq_o = nq_t.price[nq_t.idx_before(o)]
            es_o = es_t.price[es_t.idx_before(o)]
            inp.scale_today = float(es_o / nq_o)
            inp.scale_from_ns = int(max(nq_t.ts[nq_t.idx_before(o)], es_t.ts[es_t.idx_before(o)]))
            pn, pe = carry_nq.rth[-1].get("open"), carry_es.rth[-1].get("open")
            inp.scale_prev = float(pe / pn) if pn and pe else inp.scale_today
        cs = cl.ChartSession(tape, carry, inp, root)
        out[root] = (cs, roll, spread, row)
    path = _report(env, day, out, prior, normals is not None, _time.monotonic() - t0, proc.memory_info().rss)
    env.echo(f"  report {path} ({_time.monotonic() - t0:.0f} s, {proc.memory_info().rss / 2**30:.2f} GB)")
    return out


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.2f}"
    if isinstance(v, tuple):
        return "[" + ", ".join(_fmt(x) for x in v) + "]"
    return str(v)


def _report(env: Env, day: date, out: dict, prior: list[date], have_normals: bool, wall: float, rss: int):
    cs, roll, spread, row = out["NQ"]
    lines = [f"# Step 11 chart-day smoke run: {day}", "",
             f"Stored data only (\\$0, no lock); carry from {len(prior)} prior sessions ({prior[0]} … {prior[-1]}), "
             f"replayed carry-only. Single prints older than that start are missing (smoke check only).", "",
             f"- NQ contract {cs.tape.symbol} ({len(cs.tape):,} trades); roll day: {roll} (spread {spread})",
             f"- ATR {_fmt(cs.atr)}; EM {_fmt(cs.em.points)} (C {_fmt(cs.em.close)}, m {_fmt(cs.em.m)}; Tempest row "
             f"{'found' if row else 'missing'}); F9.10 normals: {'yes' if have_normals else 'none (fewer than 20)'}",
             f"- Wall time {wall:.0f} s, process memory {rss / 2**30:.2f} GB", ""]
    by_key: dict[str, int] = {}
    for recs in cs.levels.values():
        for r in recs:
            by_key[r.key] = by_key.get(r.key, 0) + 1
    lines += ["## Level records by type (F5.14 key)", "", "| key | records |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in sorted(by_key.items())] + [""]
    for t_ in SAMPLE_TIMES:
        t = st.et_ns(day, t_)
        reg = sorted(cs.registry_at(t), key=lambda x: x["band"][0])
        lines += [f"## Registry at {t_:%H:%M} ET ({len(reg)} levels, price {_fmt(cs.tape.last_price_at(t))})", "",
                  "| level | key | kind | band | role | crossed | prov | flips | crosses | active | xb_depth |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for x in reg:
            lines.append(f"| {x['level_id']} | {x['key']} | {x['kind']} | {_fmt(x['band'])} | {_fmt(x['role'])} | "
                         f"{_fmt(x['crossed'])} | {_fmt(x['provisional_role'])} | {_fmt(x['flip_count'])} | "
                         f"{_fmt(x['cross_count_today'])} | {_fmt(x['active'])} | {_fmt(x['xb_depth'])} |")
        lines.append("")
    lines += ["## Session features (long direction where signed)", "",
              "| feature | " + " | ".join(f"{t_:%H:%M}" for t_ in SAMPLE_TIMES) + " |",
              "|---|" + "---|" * len(SAMPLE_TIMES)]
    feats = [cs.features_at(st.et_ns(day, t_), direction=+1) for t_ in SAMPLE_TIMES]
    for k in SAMPLE_KEYS:
        lines.append(f"| {k} | " + " | ".join(_fmt(fx.get(k)) for fx in feats) + " |")
    es, _, es_spread, es_row = out["ES"]
    lines += ["", f"## ES cross-market levels ({es.tape.symbol}, EM {_fmt(es.em.points)})", "",
              "| level | band at 12:00 | role | crossed | flips |", "|---|---|---|---|---|"]
    for x in sorted(es.registry_at(st.et_ns(day, time(12, 0))), key=lambda x: x["band"][0]):
        lines.append(f"| {x['level_id']} | {_fmt(x['band'])} | {_fmt(x['role'])} | {_fmt(x['crossed'])} | "
                     f"{_fmt(x['flip_count'])} |")
    path = env.paths.reports_dir / "step11" / f"chart_day_{day.isoformat()}.md"
    atomic_write_text(path, "\n".join(lines) + "\n")
    return path


def roll_report(env: Env) -> Any:
    """F9.10's roll report over months 1-6: per NQ roll and session offset -5..+5, `rvol_today` at the RTH close and
    the median `vol_tod_60` over the RTH minutes, each against its own 20-session baseline (stored data only)."""
    from nqbot.features import volume_norms as vn
    from nqbot.months import days_of

    f, cal = env.features, env.cal
    plan = _plan(env)
    sessions = _sessions(plan, "NQ", cal)
    dev = {d for m in range(1, 7) for d in days_of(m, env.settings)}
    rolls = [r for r in env.db.rolls() if r["root"] == "NQ" and date.fromisoformat(r["roll_day"]) in dev]
    damage = dmg.load(env.paths.reports_dir, env.settings)
    damaged = {d for d, x in damage.items() if x.rth_dropped}
    need: set[date] = set()
    for r in rolls:
        k = sessions.index(date.fromisoformat(r["roll_day"]))
        need |= set(sessions[max(0, k - 5 - 30):k + 6])
    vols: dict[date, np.ndarray] = {}
    for d in sorted(need):
        tape = st.load_tape(env.db, plan, "NQ", d)
        if tape is not None:
            vols[d] = rvol.rth_minute_volumes(tape, cal)

    def value_of(d: date) -> dict | None:
        normals = rvol.baseline(d, sessions, vols.get, n=f["vol_baseline_sessions"], damaged=damaged, cal=cal)
        if normals is None or d not in vols:
            return None
        n_min = (st.rth_close_ns(d, cal) - st.rth_open_ns(d)) // st.MIN_NS
        v = vols[d]
        rv = vn.rvol_today(v, 0.0, n_min * 60, normals, f["rvol_clip_mult"])
        tod = [v[m] / normals[m] for m in range(n_min) if normals[m] > 0]
        return {"rvol_today_close": rv, "vol_tod_60_median": float(np.median(tod)) if tod else None}

    rows = rvol.roll_report_rows(rolls, sessions, value_of)
    lines = ["# Step 11 roll report (F9.10, months 1-6)", "",
             "Per NQ roll and session offset (roll day = 0): `rvol_today` at the RTH close and the median `vol_tod_60` "
             "over the RTH minutes, each against its own 20-session baseline. Stored data only.", "",
             "| roll day | offset | session | rvol_today (close) | vol_tod_60 median |", "|---|---|---|---|---|"]
    lines += [f"| {r['roll_day']} | {r['offset']:+d} | {r['day']} | {_fmt(r.get('rvol_today_close'))} | "
              f"{_fmt(r.get('vol_tod_60_median'))} |" for r in rows]
    path = env.paths.reports_dir / "step11" / "roll_report.md"
    atomic_write_text(path, "\n".join(lines) + "\n")
    env.echo(f"  report {path}")
    return rows
