"""The global no-lookahead test of the whole feature build (step 11 part 3; SPEC §7 rule 1; feature_spec F0.1).

A synthetic CME day (NQ and ES trades, the four maps' boards with a dislocation-guard fire and strikes missing now and
then, a gameplan zone) goes through the part-1, part-2 and part-3 engines. At several times t, everything known at t —
the events touched by t and their rows (at the touch and at t), labels resolved by t, the zones and ES zones in force,
the day-type inputs and rows, every level's transitions, the node registry, leans, magnets and pika clouds against a
zone — must be unchanged when the data after t is removed, shifted one second or one minute later, or changed."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import time, timedelta

import numpy as np
import pytest

from nqbot import calendar as cal_mod
from nqbot.config import load_features, load_settings
from nqbot.features import chart_levels as cl
from nqbot.features import session_tape as st
from nqbot.features.boards import MapBoards
from nqbot.features.node_levels import Scale
from nqbot.features.freshness import TouchRecord
from nqbot.features.session_features import SessionContext, SessionFeatures
from nqbot.features.session_levels import Candidate
from nqbot.features.skylit_session import Context, SkylitSession
from tests.test_chart_levels import DAY, carry_for, synthetic_tape

# the synthetic day moves less than NQ: a 10-point hold reaction (config 20) gives held labels, so freshness,
# day memory and retested extremes have something to count
F = {**load_features(), "hold_reaction_pts": 10.0}
S = load_settings()
CAL = cal_mod.load()
NS = st.NS
OPEN = st.rth_open_ns(DAY)
SCALE = 0.285
PREV = DAY - timedelta(days=1)   # the session before the synthetic day


def es_carry():
    c = carry_for()
    c.root, c.contract = "ES", "ESH6"
    for r in c.rth:
        for k in ("open", "high", "low", "close"):
            r[k] = r[k] * SCALE
    for d in (c.prev_day, c.prev_rth, c.prev_week):
        d["high"], d["low"] = d["high"] * SCALE, d["low"] * SCALE
    c.weekly_volume, c.prev_profile = {}, None
    return c


def boards_for(nq: st.Tape, es: st.Tape, rng) -> dict:
    out = {}
    end = st.rth_close_ns(DAY, CAL)
    for name, fut, r0, step, every in (("QQQ", nq, 41.0, 1.0, 1), ("NDXP", nq, 1.0, 10.0, 3), ("SPY", es, 10.0, 1.0, 1),
                                       ("SPX", es, 1.0, 5.0, 1)):
        p0 = float(fut.last_price_at(OPEN + 60 * NS))
        spot0 = p0 / r0
        n_k = 25
        strikes = np.round(spot0 / step) * step + step * (np.arange(n_k) - n_k // 2)
        vals = rng.uniform(0.05, 1.0, n_k) * rng.choice([-1, 1], n_k, p=[0.3, 0.7])
        t, ks, vs, offs, spot, r = [], [], [], [0], [], []
        for s in range(30, int((end - OPEN) / NS), every * 5):
            vals = vals * np.exp(rng.normal(0, 0.05, n_k))
            if rng.random() < 0.01:
                vals[rng.integers(n_k)] *= -1
            px = fut.last_price_at(OPEN + s * NS)
            present = rng.random(n_k) > 0.02
            t.append(OPEN + s * NS + (400_000_000 if name == "NDXP" else 0))
            ks.extend(strikes[present].tolist())
            vs.extend(vals[present].tolist())
            offs.append(len(ks))
            jump = 1.0025 if name == "QQQ" and 3600 <= s < 3700 else 1.0   # a guard fire an hour in (F1.8)
            spot.append(px / r0)
            r.append(r0 * jump)
        ta = np.array(t, dtype=np.int64)
        rr = np.array(r)
        out[name] = MapBoards(name, DAY, ta, ta, ta, np.array(spot), rr.copy(), rr.copy(), np.zeros(len(ta), bool),
                              np.array(offs, dtype=np.int64), np.array(ks), np.array(vs))
    return out


def gameplan(nq: st.Tape) -> dict:
    p = float(nq.last_price_at(OPEN + 3 * 3600 * NS))
    return {"status": "ok", "hvl": [p - 40, p - 40], "hvl0": None, "regime_line": None,
            "zones": [{"id": "E1", "side": "buy", "zone": [p - 12, p - 6],
                       "trigger": {"type": "failed_audit", "nq_price": None, "es_gates": []},
                       "invalidation": [{"instrument": "NQ", "direction": "below", "price": p - 30,
                                         "timeframe": "1m_close"}], "targets": []}],
            "scenario_sets": [{"frame": "ny_am", "scenarios": [{"id": "A", "prob": 0.6, "bias": "up"},
                                                               {"id": "B", "prob": 0.4, "bias": "down"}]}]}


CANDS: list = []   # retest candidates "from the previous session": fixed inputs, set once by base_data


def base_data():
    rng = np.random.default_rng(11)
    nq = synthetic_tape(seed=4, every_sec=3.0)
    es = synthetic_tape(seed=5, every_sec=3.0, start=20_000.0 * SCALE, symbol="ESH6")
    boards, gp = boards_for(nq, es, rng), gameplan(nq)
    if not CANDS:
        # prices of held touches spread over the day (overnight, the open, midday), so retested extremes are created
        # at several times and the retest passes run; as constants they are known before the day like real candidates
        sf0 = build(nq, es, boards, gp)
        held = [e for e in sf0.events if sf0.labels[e.event_id].label == "held" and e.kind == "level"]
        picks, used = [], set()
        for t0 in (st.et_ns(DAY, time(2, 0)), OPEN, OPEN + 2 * 3600 * NS):
            e = next((x for x in held if x.t >= t0 and round(x.price) not in used), None)
            if e is not None:
                used.add(round(e.price))
                picks.append(e.price)
        CANDS.extend(Candidate(float(p), "on_low", PREV, f"on_low-{PREV}-{k}") for k, p in enumerate(picks))
    return nq, es, boards, gp


def build(nq, es, boards, gp):
    f, s = F, S
    inp_nq = cl.SessionInputs(f, s, CAL, np.full(390, 600.0), {"em_1d_pct": 0.8})
    inp_es = cl.SessionInputs(f, s, CAL, None, {"em_1d_pct": 0.8}, scale_today=SCALE, scale_prev=SCALE,
                              scale_from_ns=OPEN)
    c_nq = carry_for()
    rng = np.random.default_rng(3)
    c_nq.rth5 = [{"day": f"d{k}", "full": True, "r": rng.normal(0, 1e-3, 78).tolist()} for k in range(12)]
    cs_nq, cs_es = cl.ChartSession(nq, c_nq, inp_nq, "NQ"), cl.ChartSession(es, es_carry(), inp_es, "ES")
    ctx = Context(f, s, em_nq=cs_nq.em.points, em_es=cs_es.em.points, atr_nq=cs_nq.atr, atr_es=cs_es.atr,
                  em_frac={"QQQ": cs_nq.em.m, "SPY": cs_es.em.m}, preopen_ns=cl.preopen_ns(DAY, s),
                  scale=Scale(SCALE, SCALE, OPEN))
    es_levels = [r for recs in cs_es.levels.values() for r in recs]
    sky = SkylitSession(boards, nq, es, ctx, normals=cs_nq.normals, end_ns=st.cme_end_ns(DAY), es_levels=es_levels)
    # carried from earlier sessions (known before the day): touch records of the multi-day levels, under a zone id
    # today's book also uses (z1), and retest candidates at prices the overnight trades through (F3.8)
    multi = sorted(lid for lid, recs in cs_nq.levels.items() if recs[0].multi_day)[:4]
    day0 = OPEN - 86_400 * NS
    history = [TouchRecord(day0 + k * 600 * NS, (lid,), "z1", "floor" if k % 2 else "ceiling",
                           day0 + (k * 600 + 300) * NS, "held" if k % 3 else "broke") for k, lid in enumerate(multi)]
    cands = list(CANDS)
    sc = SessionContext(DAY, f, s, CAL, cs_nq, cs_es, sky=sky, vol={"rv_iv_ratio": 0.9}, flags={"half_day": False},
                        gameplan=gp, yesterday_day_type="range", sessions=[PREV, DAY], history=history,
                        retest_candidates=cands)
    return SessionFeatures(sc).build()


def snapshot(sf: SessionFeatures, t: int) -> dict:
    ev = [e for e in sf.events if e.t <= t]
    out = {"events": [(e.event_id, e.zone_id, e.idx, e.side, e.members, e.group, e.kind, e.part) for e in ev],
           "rows_touch": [sf.row(e, e.t) for e in ev],
           "rows_t": [sf.row(e, t) for e in ev[-4:]],
           "labels": sorted((k, v.label, v.t_resolved) for k, v in sf.labels.items() if v.t_resolved <= t),
           "zones": sf.book.zones_at(t), "es_zones": sf.es_book.zones_at(t), "dt": sf.dt.at(t),
           "dt_rows": [r for r in sf.daytype_rows() if r["t"] <= t],
           "level_events": {u: [x for x in x_.state.events if x[0] <= t] for u, x_ in sf.nq_levels.items()
                            if x_.state.band.start_ns <= t},
           "registry": sorted(sf.c.sky.registry_at(t), key=lambda x: x["level_id"]),
           "lean": {m: ms.lean_at(t) for m, ms in sf.c.sky.ms.items()},
           "magnets": {m: sf.c.sky.magnets(m, t, -1) for m in sf.c.sky.ms}}
    p = float(sf.nq.last_price_at(t))
    out["pika"] = {m: sf.c.sky.pika(m, t, (p - 30, p + 30)) for m in ("QQQ", "NDXP")}
    return out


def cut_tape(tape: st.Tape, t: int, how) -> st.Tape:
    if how == "cut":
        return tape.cut(t)
    j = tape.idx_at(t)
    ts, px = tape.ts.copy(), tape.price.copy()
    if how == "changed":
        rng = np.random.default_rng(99)
        px[j:] = px[j:] + np.round(rng.normal(0, 8, len(px) - j) / 0.25) * 0.25
        return st.Tape(tape.day, tape.symbol, ts, px, tape.size, tape.side)
    ts[j:] += how
    return st.Tape(tape.day, tape.symbol, ts, px, tape.size, tape.side)


def cut_boards(mb: MapBoards, t: int, how) -> MapBoards:
    j = mb.index_at(t) + 1
    if how == "cut":
        o = mb.offsets[: j + 1]
        return replace(mb, as_of=mb.as_of[:j], tau=mb.tau[:j], t_apply=mb.t_apply[:j], spot=mb.spot[:j],
                       r_board=mb.r_board[:j], r=mb.r[:j], one_minute=mb.one_minute[:j], offsets=o,
                       s_strike=mb.s_strike[: o[-1]], s_value=mb.s_value[: o[-1]])
    if how == "changed":
        v = mb.s_value.copy()
        k0 = int(mb.offsets[j]) if j < len(mb.offsets) else len(v)
        v[k0:] = v[k0:] * np.random.default_rng(5).uniform(0.2, 3.0, len(v) - k0)
        r = mb.r.copy()
        r[j:] *= 1.0004
        return replace(mb, s_value=v, r=r, r_board=r.copy())
    sh = np.where(np.arange(len(mb)) >= j, how, 0)
    return replace(mb, as_of=mb.as_of + sh, tau=mb.tau + sh, t_apply=mb.t_apply + sh)


def same(a, b, path="") -> None:
    if isinstance(a, dict):
        assert set(a) == set(b), f"{path}: keys {set(a) ^ set(b)}"
        for k in a:
            same(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, (list, tuple)):
        assert len(a) == len(b), f"{path}: {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            same(x, y, f"{path}[{i}]")
    elif isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


@pytest.fixture(scope="module")
def full():
    nq, es, boards, gp = base_data()
    return nq, es, boards, gp, build(nq, es, boards, gp)


def test_the_synthetic_day_exercises_the_engine(full):
    *_, sf = full
    kinds = {e.kind for e in sf.events}
    assert len(sf.events) > 20 and "level" in kinds and "golden" in kinds
    assert any(e.t >= OPEN for e in sf.events) and any(e.t < OPEN for e in sf.events)
    assert sf.c.sky.blocks["QQQ"], "the guard fire an hour in should make a block"
    assert any(len(m) > 1 for m in sf.book.zones_at(OPEN + 2 * 3600 * NS).values()), "some zone merges levels"
    rows = sf.touch_rows()
    assert any(r["trinity_count"] is not None for r in rows) and any(r["es_at_level"] for r in rows)
    assert sum(r["tradeable"] for r in rows) > 0
    assert sf.c.history and len(sf.retested_today) >= 2 and sf.passes >= 3,         "carried touch records and retested extremes created at several times are exercised"
    assert any(r["tests_so_far_max"] for r in rows)
    late = [r for r in sf.daytype_rows() if r["t"] >= OPEN + 3 * 3600 * NS]
    assert late and all(r["vr_prior"] is not None and r["er_so_far"] is not None and r["er_120"] is not None
                        for r in late)


TIMES = [st.et_ns(DAY, time(4, 12, 7)), st.et_ns(DAY, time(10, 1, 3)), st.et_ns(DAY, time(10, 31, 30)),
         st.et_ns(DAY, time(13, 7, 12))]


@pytest.mark.parametrize("t", TIMES)
@pytest.mark.parametrize("how", ["cut", NS, 60 * NS, "changed"])
def test_no_lookahead(full, t, how):
    nq, es, boards, gp, sf = full
    want = snapshot(sf, t)
    if t > OPEN:
        assert want["events"] and want["rows_t"]
    got = snapshot(build(cut_tape(nq, t, how), cut_tape(es, t, how),
                         {m: cut_boards(mb, t, how) for m, mb in boards.items()}, gp), t)
    same(want, got, f"how={how}")


def test_bar_label_known_at_its_end(full):
    nq, *_ , sf = full
    b = sf.c.cs_nq.bars_1m
    k = int(np.searchsorted(b.start, st.et_ns(DAY, time(9, 31))))
    assert b.known_at(st.et_ns(DAY, time(9, 31, 59)) + 999_999_999) == k
    assert b.known_at(st.et_ns(DAY, time(9, 32))) == k + 1


def test_labels_resolved_after_t_never_count(full):
    *_, sf = full
    # every row's held / broke counts come only from labels resolved by its own time
    for e in sf.events[:60]:
        r = sf.row(e, e.t)
        done = [lb for eid, lb in sf.labels.items() if lb.t_resolved <= e.t and sf.by_id[eid].t < e.t]
        assert r["held_today"] == sum(1 for x in done if x.label == "held")


def test_a_planted_lookahead_is_caught(full, monkeypatch):
    """The test has teeth: day memory made to count every label, resolved by t or not, must fail it."""
    nq, es, boards, gp, sf = full
    orig = SessionFeatures._day_memory
    monkeypatch.setattr(SessionFeatures, "_day_memory", lambda self, t, labs: orig(self, t, list(self.labels.values())))
    t = TIMES[2]
    want = snapshot(sf, t)
    got = snapshot(build(cut_tape(nq, t, "changed"), cut_tape(es, t, "changed"),
                         {m: cut_boards(mb, t, "changed") for m, mb in boards.items()}, gp), t)
    with pytest.raises(AssertionError):
        same(want, got)


# --- an event's own touch and label never reach its own features (owner 2026-11-01, SPEC §13 Q-step11g (2)) ---------

def _decision_times(sf, e):
    lab = sf.labels[e.event_id]
    ts = [e.t, e.t + NS, lab.t_resolved, lab.t_resolved + 60 * NS, e.t + 45 * 60 * NS, sf.end_ns - NS]
    return sorted({t for t in ts if e.t <= t < sf.end_ns})


def test_own_label_never_reaches_own_features(full):
    """Every event's rows at its touch and at later decision times (after its own label resolved too) are unchanged
    when its own label (outcome, resolution time, run) is replaced."""
    *_, sf = full
    events = sf.events
    assert any(sf.labels[e.event_id].t_resolved < sf.end_ns - NS for e in events)
    for k, e in enumerate(events):
        times = _decision_times(sf, e)
        want = [sf.row(e, t) for t in times]
        lab, rec = sf.labels[e.event_id], sf.records[k]
        other = "broke" if lab.label == "held" else "held"
        sf.labels[e.event_id] = replace(lab, label=other, t_resolved=e.t + 1, run_pts=lab.run_pts + 7.0)
        sf.records[k] = replace(rec, label=other, t_resolved=e.t + 1)
        try:
            same(want, [sf.row(e, t) for t in times], f"{e.event_id}")
        finally:
            sf.labels[e.event_id], sf.records[k] = lab, rec


def test_own_retested_extreme_never_reaches_own_features(full):
    """A retested extreme made by an event's own held label (F3.8): that event's rows are the same as in a build
    where the level is never made."""
    nq, es, boards, gp, sf = full
    sources = {r.source for r in sf.retested_today if r.source in sf.by_id}
    assert sources, "the synthetic day makes a retested extreme from a held touch"
    for src in sorted(sources):
        orig = SessionFeatures._new_retests

        def without(self, src=src):
            return [r for r in orig(self) if r.source != src]
        SessionFeatures._new_retests = without
        try:
            alt = build(nq, es, boards, gp)
        finally:
            SessionFeatures._new_retests = orig
        e = sf.by_id[src]
        times = _decision_times(sf, e)
        same([sf.row(e, t) for t in times], [alt.row(alt.by_id[src], t) for t in times], src)
