"""Building one session's features from stored data (step 11 part 3), shared by the feature-build job and the step-11
smoke commands; nothing here refuses a month: the callers apply §7 rule 2b (the smoke commands take development
days only, the job writes files and reports months 7-12 as counts).

A session starts from the **state** after the previous session (SPEC §11 Expected run time, the pass-1 checkpoint):
both instruments' `carry.CarryState` (with part 3's touch history of multi-day levels, retested extremes, retest
candidates and yesterday's day type) and the NQ RTH minute volumes of the last sessions (the F9.10 normals). The first
session of a run gets it from a carry-only replay of the `warmup_sessions` sessions before it (no level machines; its
touch history starts empty) plus the last warm-up session's EOD day type.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace
from typing import Any

import numpy as np

from nqbot.checks.step8b import Env
from nqbot.clients.databento_contracts import session_before
from nqbot.clients.skylit_store import SkylitStore
from nqbot.features import chart_levels as cl
from nqbot.features import damage as dmg
from nqbot.features import daytype_rows as dr
from nqbot.features import expected_move as emod
from nqbot.features import gameplan_features as gpf
from nqbot.features import rvol
from nqbot.features import session_levels as sl
from nqbot.features import session_tape as st
from nqbot.features import tpo
from nqbot.features.carry import CarryState
from nqbot.features.session_features import SessionContext, SessionFeatures, history_from_json, retested_from_json
from nqbot.features.vwap import Vwap
from nqbot.warmup import warmup_sessions

MINUTE_VOLS_KEEP = 40   # NQ RTH minute-volume sessions kept in the state at least (room for holidays, damage)


def minute_vols_keep(features: dict) -> int:
    """Sessions of minute volumes kept: twice `vol_baseline_sessions`, at least MINUTE_VOLS_KEEP."""
    return max(MINUTE_VOLS_KEEP, 2 * int(features.get("vol_baseline_sessions", 0)))


class Refused(Exception):
    """The session can't be built (no stored trades, nothing to start from)."""


@dataclass
class DayState:
    """The state after `day` was folded in (what the next session starts from)."""
    day: str | None
    nq: dict
    es: dict
    minute_vols: dict = field(default_factory=dict)   # iso date -> list of RTH minute volumes (NQ)

    def to_json(self) -> dict[str, Any]:
        return {"day": self.day, "nq": self.nq, "es": self.es, "minute_vols": self.minute_vols}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> DayState:
        return cls(d["day"], d["nq"], d["es"], d.get("minute_vols") or {})

    def digest(self) -> str:
        import hashlib

        return hashlib.sha256(json.dumps(self.to_json(), sort_keys=True, default=str).encode()).hexdigest()


def plan(env: Env) -> dict[str, Any]:
    return json.loads((env.paths.data_dir / "jobs" / "databento_backfill" / "plan.json").read_text(encoding="utf-8"))


def planned_sessions(pl: dict[str, Any], cal) -> list[date]:
    from nqbot import calendar as cal_mod

    return sorted(d for d in (date.fromisoformat(x) for x in pl["front"]["NQ"]) if cal_mod.is_trading_day(d, cal))


def session_back(day: date, n: int, cal) -> date:
    d = day
    for _ in range(n):
        d = session_before(d, cal)
    return d


def _eod_of_tape(tape: st.Tape, features: dict, cal) -> str | None:
    o = st.rth_open_ns(tape.day)
    cs = SimpleNamespace(tape=tape, open_ns=o, close_ns=st.rth_close_ns(tape.day, cal), bars_1m=st.bars(tape, 1),
                         vwap={"vwap0930": Vwap.build(tape, o)}, daily=SimpleNamespace(at=lambda t: None), atr=None,
                         em=SimpleNamespace(points=None))
    e = dr.DayType(cs, features, []).eod()
    return None if e is None else e["day_type"]


def fold_only(env: Env, pl, d: date, state: DayState, damage: dict) -> DayState:
    """One session folded carry-only (no level machines, no zones): the levels and statistics handed on."""
    f, cal = env.features, env.cal
    rolls = env.db.rolls()
    out = {}
    vols = dict(state.minute_vols)
    for root, js in (("NQ", state.nq), ("ES", state.es)):
        carry = CarryState.from_json(js)
        tape = st.load_tape(env.db, pl, root, d)
        if tape is None:
            env.echo(f"  {root} {d}: no stored trades, skipped")
            out[root] = carry
            continue
        cl.start_session(carry, d, tape.symbol, cl.roll_spread_of(root, d, rolls))
        sp = tpo.SinglePrints(f["tpo_tick_pts"], f["sp_min_ticks"], f["max_zone_width"]).run_session(
            tape, d, [tpo.Run.from_json(r) for r in carry.single_prints])
        dd = damage.get(d)
        if root == "NQ":
            vols[d.isoformat()] = rvol.rth_minute_volumes(tape, cal).tolist()
            carry.yesterday_day_type = _eod_of_tape(tape, f, cal)
        cl.fold_session(carry, tape, f, cal, dropped=bool(dd and dd.rth_dropped), single_prints=sp.carry())
        out[root] = carry
    keep = sorted(vols)[-minute_vols_keep(f):]
    return DayState(d.isoformat(), out["NQ"].to_json(), out["ES"].to_json(), {k: vols[k] for k in keep})


def warmup_state(env: Env, pl, sessions: list[date], first: date, damage: dict, full: bool = False) -> DayState:
    """The state before `first` from the `warmup_sessions` planned sessions before it.
    full=False: every warm-up session folded carry-only (the smoke runs; touch history starts empty).
    full=True (the feature build, owner 2026-11-01, SPEC §13 Q-step11f (15)): sessions are folded carry-only only
    until both instruments have a completed session to start from, then each is built fully (zones, touches,
    labels; no rows), so multi-day freshness, retested extremes, retest candidates and yesterday's day type are
    already counting at the first session of the window."""
    prior = [d for d in sessions if d < first][-warmup_sessions(env.features):]
    if not prior:
        raise Refused(f"{first}: no planned session before it to build the state from")
    state = DayState(None, CarryState("NQ").to_json(), CarryState("ES").to_json(), {})
    for d in prior:
        ready = full and state.nq.get("rth") and state.es.get("rth")
        if ready:
            env.echo(f"  warm-up {d}: full build")
            built = build_day(env, d, state, pl, sessions, damage)
            state = built.state_out
            del built
        else:
            state = fold_only(env, pl, d, state, damage)
    return state


@dataclass
class Built:
    sf: SessionFeatures
    state_out: DayState
    info: dict


def build_day(env: Env, day: date, state: DayState, pl, sessions: list[date], damage: dict) -> Built:
    """Build `day` from `state` (the state after the previous session); returns the session and the next state."""
    f, cal, sett = env.features, env.cal, env.settings
    carry_nq, carry_es = CarryState.from_json(state.nq), CarryState.from_json(state.es)
    minute_vols = {date.fromisoformat(k): np.asarray(v, float) for k, v in state.minute_vols.items()}
    rows = emod.TempestRows.load(SkylitStore(env.paths))
    lag = f["tempest_lag_sessions"]
    rolls = env.db.rolls()
    normals = rvol.baseline(day, sessions, minute_vols.get, n=f["vol_baseline_sessions"],
                            damaged={d for d, x in damage.items() if x.rth_dropped}, cal=cal)
    dd = damage.get(day)
    tapes, cs = {}, {}
    roll_info = {}
    for root, carry in (("NQ", carry_nq), ("ES", carry_es)):
        tape = st.load_tape(env.db, pl, root, day)
        if tape is None:
            raise Refused(f"{root} {day}: no stored trades")
        if not carry.rth:
            raise Refused(f"{root} {day}: no prior session folded into the state")
        tapes[root] = tape
        spread = cl.roll_spread_of(root, day, rolls)
        roll_info[root] = cl.start_session(carry, day, tape.symbol, spread)
        row = rows.get(emod.TEMPEST_SYMBOL[root], session_back(day, lag, cal))
        lookback = int(f["retest_lookback_sessions"])
        retested = [retested_from_json(r) for r in carry.retested] if root == "NQ" else []
        retested = [r for r in retested if not sl.expired(r, day, sessions, lookback)]
        inp = cl.SessionInputs(f, sett, cal, normals if root == "NQ" else None, row,
                               eth_empty=bool(dd and dd.eth_empty), retested=retested)
        if root == "ES":
            o = st.rth_open_ns(day)
            nq_t, es_t = tapes["NQ"], tape
            inp.scale_today = float(es_t.price[es_t.idx_before(o)] / nq_t.price[nq_t.idx_before(o)])
            inp.scale_from_ns = int(max(nq_t.ts[nq_t.idx_before(o)], es_t.ts[es_t.idx_before(o)]))
            pn, pe = carry_nq.rth[-1].get("open"), carry_es.rth[-1].get("open")
            inp.scale_prev = float(pe / pn) if pn and pe else inp.scale_today
        cs[root] = (cl.ChartSession(tape, carry, inp, root), inp)
    sky, sky_note = _skylit(env, day, cs)
    ctx = _context(env, day, cs, sky, sky_note, carry_nq, sessions)
    sf = SessionFeatures(ctx).build()
    end = sf.end_state()
    # fold: hand today's levels and statistics to the next session
    for root, carry in (("NQ", carry_nq), ("ES", carry_es)):
        c_s = cs[root][0]
        cl.fold_session(carry, tapes[root], f, cal, dropped=bool(dd and dd.rth_dropped), single_prints=c_s.sp.carry()
                        if c_s.sp is not None else tpo.SinglePrints(f["tpo_tick_pts"], f["sp_min_ticks"],
                                                                     f["max_zone_width"]).run_session(
                            tapes[root], day, [tpo.Run.from_json(r) for r in carry.single_prints]).carry(),
                        retest_candidates=end["retest_candidates"] if root == "NQ" else None)
    carry_nq.retested = end["retested"]
    carry_nq.touch_history = end["history"]
    carry_nq.yesterday_day_type = end["yesterday_day_type"]
    vols = dict(state.minute_vols)
    vols[day.isoformat()] = rvol.rth_minute_volumes(tapes["NQ"], cal).tolist()
    keep = sorted(vols)[-minute_vols_keep(f):]
    out = DayState(day.isoformat(), carry_nq.to_json(), carry_es.to_json(), {k: vols[k] for k in keep})
    info = {"nq_symbol": tapes["NQ"].symbol, "es_symbol": tapes["ES"].symbol, "roll": roll_info,
            "nq_trades": len(tapes["NQ"]), "es_trades": len(tapes["ES"]), "skylit": sky_note,
            "have_normals": normals is not None, "gameplan": ctx.gameplan is not None}
    return Built(sf, out, info)


def _skylit(env: Env, day: date, cs: dict):
    from nqbot.checks import step8b as s8
    from nqbot.features import boards as bd
    from nqbot.features.node_levels import Scale
    from nqbot.features.skylit_session import Context, SkylitSession

    f, cal, sett = env.features, env.cal, env.settings
    cs_nq, cs_es = cs["NQ"][0], cs["ES"][0]
    nq, es = cs_nq.tape, cs_es.tape
    base = s8.skylit_base(env, day)
    if not env.sky.stored_windows(day, base):
        return None, "no Skylit boards stored"
    open_ns, close_ns = st.rth_open_ns(day), st.rth_close_ns(day, cal)
    closes = bd.stored_closes(env.paths.raw_dir)
    k = bd.ndxp_k(day, closes, f)
    boards: dict = {}
    for m in ("QQQ", "SPY", "SPX", "NDXP"):          # QQQ before NDXP (QQQ × k); one map read at a time (§11)
        b, s_ = env.sky.read_range(day, base, symbols=[m])
        fut = nq if bd.CHAIN_ROOT[m] == "NQ" else es
        kw = {"qqq": boards["QQQ"], "k": k} if m == "NDXP" else {}
        boards[m] = bd.from_tables(m, day, b, s_, fut.ts, fut.price, f, sett, open_ns, close_ns, **kw)
        del b, s_
    inp = cs["ES"][1]
    scale = Scale(inp.scale_prev, inp.scale_today, max(open_ns, inp.scale_from_ns or open_ns))
    ctx = Context(f, sett, em_nq=cs_nq.em.points, em_es=cs_es.em.points, atr_nq=cs_nq.atr, atr_es=cs_es.atr,
                  em_frac={"QQQ": cs_nq.em.m, "SPY": cs_es.em.m}, preopen_ns=cl.preopen_ns(day, sett), scale=scale)
    es_levels = [r for recs in cs_es.levels.values() for r in recs]
    sess = SkylitSession(boards, nq, es, ctx, normals=cs_nq.normals, end_ns=st.cme_end_ns(day), es_levels=es_levels)
    notes = [f"{m} {mb.note}" for m, mb in boards.items() if getattr(mb, "note", None)]
    return sess, "; ".join(notes) or "ok"


def _context(env: Env, day: date, cs: dict, sky, sky_note, carry_nq: CarryState, sessions) -> SessionContext:
    from nqbot.clients import skylit_multiexp as smx
    from nqbot.features import calendar_flags as cf
    from nqbot.features import multiexp as mx
    from nqbot.features import vol_context as vc
    from nqbot.gameplan.parser import GameplanStore

    f, cal = env.features, env.cal
    snaps = []
    prev = session_before(day, cal)
    for tier in mx.TIER_ORDER:
        p = smx.snapshot_path(env.paths, tier, day)
        if p.exists():
            snaps.append(mx.parse(smx.read_snapshot(p), tier, day, prev, f, cl.cal_early_close(prev, cal)))
    rows = emod.TempestRows.load(SkylitStore(env.paths))
    closes = [x["close"] for x in carry_nq.rth]
    vol = vc.features(rows, day, f["tempest_lag_sessions"], lambda d, n: session_back(d, n, cal), closes,
                      f["rv_lookback_sessions"])
    gp = gpf.usable(GameplanStore.from_paths(env.paths).load_effective(day), day, env.settings)
    lookback = int(f["retest_lookback_sessions"])
    cands = [sl.Candidate(float(c["price"]), c["type"], date.fromisoformat(c["session"]), c.get("cid") or "")
             for c in carry_nq.retest_candidates]
    retested = [retested_from_json(r) for r in carry_nq.retested]
    retested = [r for r in retested if not sl.expired(r, day, sessions, lookback)]
    return SessionContext(day, f, env.settings, cal, cs["NQ"][0], cs["ES"][0], sky=sky, vol=vol,
                          flags=cf.day_flags(day, cal, env.settings), mxe=mx.MultiExp(snaps) if snaps else None,
                          gameplan=gp, history=history_from_json(carry_nq.touch_history),
                          yesterday_day_type=carry_nq.yesterday_day_type, retested=retested, retest_candidates=cands,
                          sessions=sessions, skylit_note=sky_note)


def load_damage(env: Env) -> dict:
    return dmg.load(env.paths.reports_dir, env.settings)
