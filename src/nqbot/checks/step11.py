"""Step 11 checks on stored data (read-only, $0, no lock; §12 step 11).

- `chart_day(env, day)`: the one-day smoke run of the chart-level engine (owner 2026-10-31: one development day). The
  carried state comes from the `warmup_sessions` sessions before the day, replayed in carry-only mode (no level
  machines); features and levels are reported for the day only. Report `<reports_dir>/step11/chart_day_<day>.md`.
- `roll_report(env)`: F9.10's roll report (`rvol_today` at the RTH close and the median `vol_tod_60` by session
  relative to each roll day, −5…+5, months 1–6). Built; not run until the owner asks (no full-window runs yet).
- `skylit_day(env, day)` (part 2, owner OK 2026-10-31): the Skylit engine (`features/skylit_session.py`) on the same
  part-1 chart sessions, with the cross-market, multi-expiration, Tempest and calendar context. Report
  `<reports_dir>/step11/skylit_day_<day>.md`.
- `nodetype_check(env)` (F2.2, F2.5): Skylit's `nodeType` on the stored step-7 instants vs the bot's King / Gatekeeper;
  the pika / barney sign check (exit 1 if it fails). Report `<reports_dir>/step11/nodetype_check.md`.
"""

from __future__ import annotations

import os
import time as _time
from dataclasses import dataclass
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
from nqbot.features.boards import BoardsRefused  # noqa: F401  (the CLI's refusals)
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


@dataclass
class ChartBuild:
    """The part-1 chart sessions of one day (NQ and ES) and what went into them."""
    out: dict                 # root -> (ChartSession, roll day?, spread, Tempest row)
    inputs: dict              # root -> SessionInputs
    carry: dict               # root -> CarryState (after start_session: roll-shifted)
    prior: list
    sessions: list
    have_normals: bool


def _chart_sessions(env: Env, day: date) -> ChartBuild:
    """Part 1's engine for one development day: the carry replayed from the `warmup_sessions` prior sessions."""
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
    env.echo(f"step 11 {day}: carry from {len(prior)} prior sessions ({prior[0]} .. {prior[-1]}), "
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
    out, inputs, tapes = {}, {}, {}
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
        inputs[root] = inp
        out[root] = (cl.ChartSession(tape, carry, inp, root), roll, spread, row)
    return ChartBuild(out, inputs, {"NQ": carry_nq, "ES": carry_es}, prior, sessions, normals is not None)


def chart_day(env: Env, day: date) -> Any:
    t0 = _time.monotonic()
    proc = psutil.Process(os.getpid())
    cb = _chart_sessions(env, day)
    path = _report(env, day, cb.out, cb.prior, cb.have_normals, _time.monotonic() - t0, proc.memory_info().rss)
    env.echo(f"  report {path} ({_time.monotonic() - t0:.0f} s, {proc.memory_info().rss / 2**30:.2f} GB)")
    return cb.out


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


# --- step 11 part 2: the Skylit / cross-market engine -------------------------------------------------------------

SKY_KEYS = ("map_age_sec", "king_strike", "king_dominance", "king_changes_today", "regime_net", "regime_pos_share",
            "n_pos_relevant", "n_neg_relevant", "rainbow", "gatekeepers_to_king", "lean", "bias", "king_distance_em",
            "magnet_net", "rolling_ceiling", "roll_steps_ceiling", "rolling_floor", "roll_steps_floor",
            "in_pika_cloud", "price_in_pocket", "floor_dist_em", "ceiling_dist_em")
LEAN_SAMPLE_SEC = 10   # the lean distribution report samples every 10 s of RTH (F2.9: step 12 checks bias_deadband)
KING_SWITCHES_SHOWN = 12


def _build_skylit(env: Env, day: date, cb: ChartBuild):
    from nqbot.checks import step8b as s8
    from nqbot.features import boards as bd
    from nqbot.features.node_levels import Scale
    from nqbot.features.skylit_session import Context, SkylitSession

    f, cal, sett = env.features, env.cal, env.settings
    cs_nq, cs_es = cb.out["NQ"][0], cb.out["ES"][0]
    nq, es = cs_nq.tape, cs_es.tape
    base = s8.skylit_base(env, day)
    if not env.sky.stored_windows(day, base):
        raise Refused(f"no Skylit boards stored for {day}")
    open_ns, close_ns = st.rth_open_ns(day), st.rth_close_ns(day, cal)
    closes = bd.stored_closes(env.paths.raw_dir)
    k = bd.ndxp_k(day, closes, f)
    boards: dict = {}
    for m in ("QQQ", "SPY", "SPX", "NDXP"):          # QQQ before NDXP (QQQ × k)
        b, s_ = env.sky.read_range(day, base, symbols=[m])
        fut = nq if bd.CHAIN_ROOT[m] == "NQ" else es
        kw = {"qqq": boards["QQQ"], "k": k} if m == "NDXP" else {}
        boards[m] = bd.from_tables(m, day, b, s_, fut.ts, fut.price, f, sett, open_ns, close_ns, **kw)
        del b, s_
    inp = cb.inputs["ES"]
    scale = Scale(inp.scale_prev, inp.scale_today, max(open_ns, inp.scale_from_ns or open_ns))
    ctx = Context(f, sett, em_nq=cs_nq.em.points, em_es=cs_es.em.points, atr_nq=cs_nq.atr, atr_es=cs_es.atr,
                  em_frac={"QQQ": cs_nq.em.m, "SPY": cs_es.em.m}, preopen_ns=cl.preopen_ns(day, sett), scale=scale)
    es_levels = [r for recs in cs_es.levels.values() for r in recs]
    sess = SkylitSession(boards, nq, es, ctx, normals=cs_nq.normals, end_ns=st.cme_end_ns(day), es_levels=es_levels)
    return sess, {"k": k, "closes": None if closes is None else closes["folder"]}


def skylit_day(env: Env, day: date) -> Any:
    """Step 11 part 2 smoke run on one development day (owner OK 2026-10-31): the Skylit engine with the part-1
    chart sessions, the cross-market, multi-expiration, Tempest and calendar context. Report only."""
    import json

    from nqbot.clients import skylit_multiexp as smx
    from nqbot.features import calendar_flags as cf
    from nqbot.features import multiexp as mx
    from nqbot.features import vol_context as vc

    t0 = _time.monotonic()
    proc = psutil.Process(os.getpid())
    cb = _chart_sessions(env, day)
    t_chart = _time.monotonic() - t0
    sess, info = _build_skylit(env, day, cb)
    t_sky = _time.monotonic() - t0 - t_chart
    f, cal = env.features, env.cal
    snaps = []
    prev = session_before(day, cal)
    for tier in mx.TIER_ORDER:
        p = smx.snapshot_path(env.paths, tier, day)
        if p.exists():
            snaps.append(mx.parse(smx.read_snapshot(p), tier, day, prev, f, cl.cal_early_close(prev, cal)))
    mxe = mx.MultiExp(snaps)
    rows = emod.TempestRows.load(SkylitStore(env.paths))
    closes = [x["close"] for x in cb.carry["NQ"].rth]
    vol = vc.features(rows, day, f["tempest_lag_sessions"], lambda d, n: _session_back(d, n, cal), closes,
                      f["rv_lookback_sessions"])
    flags = cf.day_flags(day, cal, env.settings)
    s8_day = env.paths.reports_dir / "step8b" / "dislocations" / f"{day.isoformat()}.json"
    s8 = json.loads(s8_day.read_text(encoding="utf-8")) if s8_day.exists() else None
    path = _skylit_report(env, day, sess, info, mxe, vol, flags, s8, (t_chart, t_sky, _time.monotonic() - t0),
                          proc.memory_info().rss)
    env.echo(f"  report {path} ({_time.monotonic() - t0:.0f} s, {proc.memory_info().rss / 2**30:.2f} GB)")
    return sess


def _et(ns: int) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(ns / st.NS, st.ET).strftime("%H:%M:%S")


def _event_kinds(side) -> str:
    c: dict[str, int] = {}
    for e in side.side.events:
        c[e.kind] = c.get(e.kind, 0) + 1
    return ", ".join(f"{k} {v}" for k, v in sorted(c.items())) or "none"


def _skylit_report(env, day, sess, info, mxe, vol, flags, s8, times, rss):
    from nqbot.features import calendar_flags as cf
    from nqbot.features import cross_market as cm

    f = env.features
    t_chart, t_sky, wall = times
    nq, es = sess.tapes["NQ"], sess.tapes["ES"]
    lines = [f"# Step 11 skylit-day smoke run: {day}", "",
             "Stored data only (\\$0, no lock). The Skylit engine (`features/skylit_session.py`) on the part-1 chart "
             "sessions; frozen values used as they are (`map_latency_sec`, `board_content_offset_ms`, "
             "`tempest_lag_sessions`, NDXP QQQ × k).", "",
             f"- Wall time {wall:.0f} s (chart sessions {t_chart:.0f} s, Skylit engine {t_sky:.0f} s); process memory "
             f"{rss / 2**30:.2f} GB",
             f"- NQ {nq.symbol} ({len(nq):,} trades), ES {es.symbol} ({len(es):,} trades); NDXP k "
             f"{_fmt(info['k'])} (closes {info['closes']})", "",
             "## Boards and R", "",
             "| map | applied boards | step 8b boards | 1-minute | R median | R min | R max | F1.2 rejected | note |",
             "|---|---|---|---|---|---|---|---|---|"]
    for m, mb in sess.boards.items():
        n8 = None if s8 is None else (s8.get("chains", {}).get(m) or {}).get("boards")
        r = mb.r[np.isfinite(mb.r)]
        lines.append(f"| {m} | {len(mb):,} | {_fmt(n8)} | {int(mb.one_minute.sum())} | "
                     f"{_fmt(float(np.median(r)) if len(r) else None)} | {_fmt(float(r.min()) if len(r) else None)} | "
                     f"{_fmt(float(r.max()) if len(r) else None)} | {mb.rejected} | {mb.note} |")
    lines += ["", "## Dislocation guard (F1.8)", "", "| chain | fires (this run) | blocks | step 8b fires |",
              "|---|---|---|---|"]
    for chain in f["mapping"]["disl_guard_maps"]:
        fires = sess.fires.get(chain, [])
        g8 = None if s8 is None else ((s8.get("chains", {}).get(chain) or {}).get("guard") or {})
        f8 = None if g8 is None else len(g8.get("fires") or [])
        blk = ", ".join(f"{_et(a)}–{_et(z)}" for a, z in sess.blocks.get(chain, [])) or "none"
        lines.append(f"| {chain} | {len(fires)} | {blk} | {_fmt(f8)} |")
    lines += ["", "## Nodes, King switches and rolls", "",
              "| map | strikes ever relevant | mean relevant per board | King switches | ceiling events | floor events |",
              "|---|---|---|---|---|---|"]
    for m, ms in sess.ms.items():
        nb = len(ms.mb)
        per = np.bincount(ms.mb.s_board[ms.relevant], minlength=nb) if nb else np.zeros(0)
        mn = sess.nodes[m]
        lines.append(f"| {m} | {len(mn.levels)} | {_fmt(float(per.mean()) if nb else None)} | "
                     f"{len(ms.king_switches)} | {_event_kinds(mn.ceiling)} | {_event_kinds(mn.floor)} |")
    lines.append("")
    for m, ms in sess.ms.items():
        sw = ms.king_switches[:KING_SWITCHES_SHOWN]
        if sw:
            lines.append(f"- {m} King switches: " + "; ".join(f"{_et(t)} {o:g}→{n:g}" for t, o, n in sw)
                         + (" …" if len(ms.king_switches) > KING_SWITCHES_SHOWN else ""))
    times_ = [st.et_ns(day, t_) for t_ in SAMPLE_TIMES]
    head = "| feature | " + " | ".join(f"{t_:%H:%M}" for t_ in SAMPLE_TIMES) + " |"
    sep = "|---|" + "---|" * len(SAMPLE_TIMES)
    feats = [sess.features_at(t, +1) for t in times_]
    for m in sess.ms:
        p = m.lower() + "_"
        lines += ["", f"## {m} features (long where signed)", "", head, sep]
        for k in SKY_KEYS:
            lines.append(f"| {k} | " + " | ".join(_fmt(fx.get(p + k)) for fx in feats) + " |")
    lines += ["", "## Cross-map and cross-market", "", head, sep]
    cross = [cm.features_at(nq, es, t, f, +1) for t in times_]
    tri = {rho: [sess.trinity(t, t, rho) for t in times_] for rho in ("ceiling", "floor")}

    def r_of(m, tt):
        i = sess.board_at(m, tt)
        return None if i < 0 else float(sess.ms[m].mb.r[i])
    mxs = [mxe.features_at(t, {"nasdaq": nq.last_price_at(t), "sp": es.last_price_at(t)},
                           {"nasdaq": sess.ctx.em_nq, "sp": sess.ctx.em_es}, r_of, float(f["mx_wall_reach_em"]),
                           blocked_of=sess.blocked) for t in times_]
    clocks = [cf.clocks_at(t, day, env.cal, env.settings) for t in times_]
    rowsets = [(k, feats) for k in ("whipsaw_qqq", "whipsaw_ndxp", "trinity_bias_agree_qqq", "trinity_bias_conflict_qqq")]
    rowsets += [(k, cross) for k in ("smt_div_high", "smt_div_low", "rel_delta", "delta_sign_agree", "rs_nq_es_5",
                                     "rs_nq_es_15")]
    rowsets += [(k, mxs) for k in ("mx_snapshot", "mx_net_sign_nasdaq", "mx_0dte_agree_nasdaq",
                                   "mx_wall_above_dist_em_nasdaq", "mx_wall_below_dist_em_nasdaq", "mx_net_sign_sp",
                                   "mx_wall_above_dist_em_sp", "mx_wall_below_dist_em_sp")]
    rowsets += [(k, clocks) for k in ("minutes_to_close", "minutes_to_horizon_end")]
    for k, src in rowsets:
        lines.append(f"| {k} | " + " | ".join(_fmt(x.get(k)) for x in src) + " |")
    for rho in ("ceiling", "floor"):
        for k in ("trinity_count", "maps_with_node_here"):
            lines.append(f"| {k} ({rho}, at the instant) | " + " | ".join(_fmt(x.get(k)) for x in tri[rho]) + " |")
    lines += ["", "## Registry (QQQ / NDXP nodes)", ""]
    for t_ in (time(10, 0), time(12, 0)):
        t = st.et_ns(day, t_)
        reg = sorted(sess.registry_at(t), key=lambda x: (x["map"], x["strike"]))
        lines += [f"### {t_:%H:%M} ET ({len(reg)} nodes, NQ {_fmt(nq.last_price_at(t))})", "",
                  "| map | strike | band | type | pct | sign | role | crossed | prov | flips | crosses |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for x in reg:
            lines.append(f"| {x['map']} | {x['strike']:g} | {_fmt(x['band'])} | {x['node_type']} | "
                         f"{_fmt(x['pct_of_king'])} | {x['node_sign']} | {_fmt(x['role'])} | {_fmt(x['crossed'])} | "
                         f"{_fmt(x['provisional_role'])} | {_fmt(x['flip_count'])} | {_fmt(x['cross_count_today'])} |")
        lines.append("")
    lines += ["## Lean distribution (F2.9; step 12 checks `bias_deadband`)", "",
              f"Sampled every {LEAN_SAMPLE_SEC} s of RTH; `bias_deadband` {f['bias_deadband']}.", "",
              "| map | p5 | p25 | p50 | p75 | p95 | share beyond the deadband |", "|---|---|---|---|---|---|---|"]
    o, c = st.rth_open_ns(day), st.rth_close_ns(day, env.cal)
    for m, ms in sess.ms.items():
        vals = [ms.lean_at(t) for t in range(o, c, LEAN_SAMPLE_SEC * st.NS)]
        v = np.array([x for x in vals if x is not None])
        if not len(v):
            lines.append(f"| {m} | — | — | — | — | — | — |")
            continue
        q = np.percentile(v, [5, 25, 50, 75, 95])
        lines.append(f"| {m} | " + " | ".join(f"{x:.3f}" for x in q)
                     + f" | {float((np.abs(v) > f['bias_deadband']).mean()):.2f} |")
    lines += ["", "## Daily context (known at the session start)", "", "| feature | value |", "|---|---|"]
    for k in ("qqq_svx30", "qqq_svx1d", "qqq_term_slope", "spy_svx30", "ndxp_svx30", "spx_svx30", "vix_svx30",
              "vix_ref_chg", "rv_nq", "rv_iv_ratio", "tempest_draft"):
        lines.append(f"| {k} | {_fmt(vol.get(k))} |")
    for k, v in flags.items():
        lines.append(f"| {k} | {_fmt(v)} |")
    path = env.paths.reports_dir / "step11" / f"skylit_day_{day.isoformat()}.md"
    atomic_write_text(path, "\n".join(lines) + "\n")
    return path


def nodetype_check(env: Env) -> int:
    """F2.2 / F2.5 (owner 2026-10-25, Q3): on the stored step-7 `/v1/historical` instants, Skylit's pika strikes must
    be positive and barney strikes negative (the sign convention; exit 1 if not: stop and ask the owner), and a
    confusion table compares Skylit's king with the bot's King (on one board the confirmed King is that board's leader)
    and Skylit's gatekeeper (size-only: any non-King strike >= 25% of King) with the bot's Gatekeeper ∪ significant
    (relevant non-King). Classified over the strikes each instant carries; the report states its `maxStrikes`."""
    import json

    import pyarrow.parquet as pq

    from nqbot.features import node_switch as nsw

    f = env.features
    files = sorted((env.paths.raw_dir / "skylit" / "historical").glob("*.parquet"))
    if not files:
        raise Refused("no stored /v1/historical instants (step 7 historical-instants writes them)")
    conf: dict[str, dict[tuple[str, str], int]] = {}
    king_ok: dict[str, list[int]] = {}
    sign_bad: list[str] = []
    max_strikes = set()
    for p in files:
        tab = pq.read_table(p)
        meta = json.loads((tab.schema.metadata or {}).get(b"nqbot", b"{}"))
        max_strikes.add(str((meta.get("params") or {}).get("maxStrikes")))
        rows = tab.replace_schema_metadata(None).to_pylist()
        by: dict[tuple[str, Any], list[dict]] = {}
        for r in rows:
            by.setdefault((r["symbol"], r["as_of"]), []).append(r)
        for (sym, _), rs in by.items():
            values = {float(r["strike"]): float(r["value"]) for r in rs}
            king = nsw.leader(values, float(rs[0]["spot"]))
            kabs = abs(values[king]) if king is not None else 0.0
            c = conf.setdefault(sym, {})
            for r in rs:
                k, v, t = float(r["strike"]), float(r["value"]), r["node_type"]
                if (t == "pika" and not v > 0) or (t == "barney" and not v < 0):
                    sign_bad.append(f"{p.name} {sym} {k:g} {t} {v:g}")
                pct = abs(v) / kabs if kabs > 0 else 0.0
                bot = "king" if k == king else "relevant non-King" if pct >= f["min_node_pct_of_king"] else "normal"
                c[(t, bot)] = c.get((t, bot), 0) + 1
            sk = [float(r["strike"]) for r in rs if r["node_type"] == "king"]
            king_ok.setdefault(sym, []).append(int(len(sk) == 1 and sk[0] == king))
    bots = ("king", "relevant non-King", "normal")
    lines = ["# Step 11 nodeType check (F2.2, F2.5)", "",
             f"{len(files)} stored `/v1/historical` instants; `maxStrikes` {', '.join(sorted(max_strikes))}. The bot "
             "classifies each instant's strikes on that board alone (fresh relevance: at least "
             f"{f['min_node_pct_of_king']} of the board's King; one board, so the confirmed King = the board's King).",
             "", f"- **Sign check (F2.2):** {'PASS' if not sign_bad else 'FAIL'}: pika strikes positive, barney "
             f"strikes negative ({len(sign_bad)} violations)."]
    lines += [f"  - {x}" for x in sign_bad[:20]]
    for sym in sorted(conf):
        ok = king_ok[sym]
        lines += ["", f"## {sym}: King agreement {sum(ok)}/{len(ok)} instants", "",
                  "| Skylit nodeType | " + " | ".join(bots) + " |", "|---|" + "---|" * len(bots)]
        for t in sorted({t for t, _ in conf[sym]}):
            lines.append(f"| {t} | " + " | ".join(str(conf[sym].get((t, b), 0)) for b in bots) + " |")
    lines += ["", "Expected under the owner's Q3 rule: Skylit king ↔ bot King; Skylit gatekeeper ↔ bot relevant "
              "non-King. pika / barney are compared only for their sign (above); step 12 shows this table to the "
              "owner, who decides on any disagreement."]
    path = env.paths.reports_dir / "step11" / "nodetype_check.md"
    atomic_write_text(path, "\n".join(lines) + "\n")
    env.echo(f"  sign check {'PASS' if not sign_bad else 'FAIL'}; report {path}")
    return 0 if not sign_bad else 1
