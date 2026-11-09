"""The global no-lookahead test of the whole feature build (step 11 part 3; SPEC §7 rule 1; feature_spec F0.1).

A synthetic CME day (NQ and ES trades, the four maps' boards with a dislocation-guard fire and strikes missing now and
then, a gameplan zone) goes through the part-1, part-2 and part-3 engines. At several times t, everything known at t —
the events touched by t and their rows (at the touch and at t), labels resolved by t, the zones and ES zones in force,
the day-type inputs and rows, every level's transitions, the node registry, leans, magnets and pika clouds against a
zone — must be unchanged when the data after t is removed, shifted one second or one minute later, or changed.

Step 13-0 (owner 2026-11-09): the baseline runs every correction at its config value (zone hysteresis, relevance
debounce, level jumps, own re-arm, 1-pt profile rows, candidate times); a second baseline (`F_VAR`) also switches the
step-13 variants on (`node_relevance_top_n` 5, `node_role_at_formation` strike_side) with a structure-label distance
apart from the retest and pair-cut distances, and goes through the same checks."""

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
from nqbot.features.session_features import MAX_ALT_BUILDS, SessionContext, SessionFeatures
from nqbot.features.session_levels import Candidate
from nqbot.features.skylit_session import Context, SkylitSession
from tests.test_chart_levels import DAY, carry_for, synthetic_tape

# the synthetic day moves less than NQ: a 10-point hold reaction (config 30, its history 30, retests and the pair cut
# 20) gives held labels, so freshness, day memory and retested extremes have something to count
F = {**load_features(), "hold_reaction_pts": 10.0, "history_hold_pts": 10.0, "retest_hold_pts": 10.0,
     "pair_end_hold_pts": 10.0}
# the step-13 variants on, and the label distances apart (structure label / history 12, retests / pair cut 10)
F_VAR = {**F, "node_relevance_top_n": 5, "node_role_at_formation": "strike_side", "hold_reaction_pts": 12.0,
         "history_hold_pts": 12.0}
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


# Retest candidates "from the previous session": fixed inputs known before the day, like real candidates. They are
# the prices of the first held level touches at or after 02:00, 09:30 and 11:30 (one per rounded price) in a build of
# this synthetic day without candidates, so retested extremes are created at several times and the retest passes run
# (`candidate_prices()` recomputes them; `test_the_synthetic_day_exercises_the_engine` fails if they stop doing so).
CAND_PRICES = (20022.5, 20035.0, 20033.5)
CANDS = [Candidate(p, "on_low", PREV, f"on_low-{PREV}-{k}") for k, p in enumerate(CAND_PRICES)]


def candidate_prices() -> list[float]:
    """How CAND_PRICES were found (run by hand if the synthetic day changes)."""
    nq, es, boards, gp = base_data()
    sf0 = build(nq, es, boards, gp, cands=[])
    held = [e for e in sf0.events if sf0.labels[e.event_id].label == "held" and e.kind == "level"]
    picks, used = [], set()
    for t0 in (st.et_ns(DAY, time(2, 0)), OPEN, OPEN + 2 * 3600 * NS):
        e = next((x for x in held if x.t >= t0 and round(x.price) not in used), None)
        if e is not None:
            used.add(round(e.price))
            picks.append(e.price)
    return picks


def base_data():
    rng = np.random.default_rng(11)
    nq = synthetic_tape(seed=4, every_sec=3.0)
    es = synthetic_tape(seed=5, every_sec=3.0, start=20_000.0 * SCALE, symbol="ESH6")
    return nq, es, boards_for(nq, es, rng), gameplan(nq)


def build(nq, es, boards, gp, cands=None, f=None, s=None):
    f, s = (F if f is None else f), (S if s is None else s)
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
    cands = list(CANDS if cands is None else cands)
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


pytestmark = pytest.mark.slow   # every test here builds whole synthetic sessions (CLAUDE.md rule 14: the full suite
                                # always runs them; a targeted run may skip them with -m "not slow")


@pytest.fixture(scope="session")
def full():
    """The baseline session, built once per test process and shared by every test here."""
    nq, es, boards, gp = base_data()
    return nq, es, boards, gp, build(nq, es, boards, gp)


@pytest.fixture(scope="session")
def full_var():
    """The baseline with the step-13 variants on (F_VAR)."""
    nq, es, boards, gp = base_data()
    return nq, es, boards, gp, build(nq, es, boards, gp, f=F_VAR)


_VARIANTS: dict = {}


def variant(full, t: int, how, f=None):
    """The session built from the data with everything after t removed / shifted / changed. The changed build at
    TIMES[2] is kept (the planted-leak check reads it too); the others are built for their one test only."""
    nq, es, boards, gp, _ = full
    key = (t, how, id(f))
    if key in _VARIANTS:
        return _VARIANTS[key]
    sf = build(cut_tape(nq, t, how), cut_tape(es, t, how), {m: cut_boards(mb, t, how) for m, mb in boards.items()}, gp,
               f=f)
    if key == (TIMES[2], "changed", id(None)):
        _VARIANTS[key] = sf
    return sf


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
    # step 13-0: the corrections are exercised
    assert sf.touch_stats["own_arms"] > 0, "a post-cross retest armed by its member's own re-arm"
    assert all(r["touch_idx"] >= 0 for r in rows) and any(r["founding_level_ids"] for r in rows)


def test_the_variant_day_exercises_the_corrections(full_var):
    """F_VAR: formation touches, a debounced relevance change, more than 7 relevant nodes on a board, the label
    distances apart, and every outcome-only column kept out of the rows."""
    from nqbot.features import nodes

    *_, sf = full_var
    assert sf.touch_stats["formation_touches"] > 0
    ms = sf.c.sky.ms["QQQ"]
    raw = nodes.relevance(ms.mb.s_board, ms.mb.s_strike, ms.pct, np.ones(len(ms.mb), bool),
                          F_VAR["min_node_pct_of_king"], F_VAR["node_drop_pct"])[0]
    assert (raw != ms.relevant).any(), "the debounce changes some relevance"
    per_board = np.bincount(ms.mb.s_board[ms.relevant], minlength=len(ms.mb))
    assert per_board.max() > 7, "a board with more relevant nodes than the leave rank"
    levels = [nl.level_spans for nl in sf.c.sky.nodes["QQQ"].levels.values()]
    assert all(x is not None for x in levels) and any(x == [] or len(x) > 1 for x in levels), \
        "top-n keeps some relevant node out of the NQ levels some of the time"
    assert any(sf.labels[k].label != sf.retest_labels[k].label for k in sf.labels), "the distances differ"
    rows, outs = sf.touch_rows(), sf.outcome_rows()
    assert all("t_pair_end" not in r and "label" not in r and "t_resolved" not in r for r in rows)
    assert all(o["t_pair_end"] == sf.pair_end[o["event_id"]].t_resolved for o in outs)


TIMES = [st.et_ns(DAY, time(4, 12, 7)), st.et_ns(DAY, time(10, 1, 3)), st.et_ns(DAY, time(10, 31, 30)),
         st.et_ns(DAY, time(13, 7, 12))]


@pytest.mark.parametrize("t", TIMES)
@pytest.mark.parametrize("how", ["cut", NS, 60 * NS, "changed"])
def test_no_lookahead(full, t, how):
    nq, es, boards, gp, sf = full
    want = snapshot(sf, t)
    if t > OPEN:
        assert want["events"] and want["rows_t"]
    got = snapshot(variant(full, t, how), t)
    same(want, got, f"how={how}")


@pytest.mark.parametrize("t", TIMES[1:3])
@pytest.mark.parametrize("how", ["cut", NS, 60 * NS, "changed"])
def test_no_lookahead_with_the_step13_variants(full_var, t, how):
    nq, es, boards, gp, sf = full_var
    want = snapshot(sf, t)
    assert want["events"] and want["rows_t"]
    same(want, snapshot(variant(full_var, t, how, f=F_VAR), t), f"how={how}")


def test_the_build_reads_only_configuration_its_key_covers():
    """Q-dryrun13 (6): a build session reading a setting or feature value the build key leaves out fails here."""
    from nqbot.checks import feature_build as fb

    class Rec(dict):
        def __init__(self, d):
            super().__init__(d)
            self.read: set = set()

        def __getitem__(self, k):
            self.read.add(k)
            return super().__getitem__(k)

        def get(self, k, default=None):
            self.read.add(k)
            return super().get(k, default)

        def __contains__(self, k):
            self.read.add(k)
            return super().__contains__(k)

    f, s = Rec(F_VAR), Rec(S)
    nq, es, boards, gp = base_data()
    build(nq, es, boards, gp, f=f, s=s)
    assert f.read and s.read
    assert not (f.read & fb.NON_BUILD_FEATURE_KEYS), f.read & fb.NON_BUILD_FEATURE_KEYS
    assert not (s.read & fb.NON_BUILD_SETTING_KEYS), s.read & fb.NON_BUILD_SETTING_KEYS


def test_bar_label_known_at_its_end():
    b = st.bars(synthetic_tape(seed=4, every_sec=3.0), 1)   # the baseline's NQ 1-minute bars, without the build
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
    got = snapshot(variant(full, t, "changed"), t)
    with pytest.raises(AssertionError):
        same(want, got)


# --- an event's own touch and label never reach its own features (owner 2026-11-01, SPEC §13 Q-step11g (2)) ---------

def _decision_times(sf, e):
    lab = sf.labels[e.event_id]
    ts = [e.t, e.t + NS, lab.t_resolved, lab.t_resolved + 60 * NS, e.t + 45 * 60 * NS, sf.end_ns - NS]
    return sorted({t for t in ts if e.t <= t < sf.end_ns})


OWN_SHARDS = 3
LABEL_DICTS = ("labels", "hist_labels", "retest_labels", "pair_end")


@pytest.mark.parametrize("shard", range(OWN_SHARDS))
def test_own_label_never_reaches_own_features(full, shard):
    """Every event's rows at its touch and at later decision times (after its own label resolved too) are unchanged
    when its own label (outcome, resolution time, run) is replaced. The events are split over OWN_SHARDS tests (every
    event is checked by exactly one)."""
    *_, sf = full
    events = sf.events
    assert any(sf.labels[e.event_id].t_resolved < sf.end_ns - NS for e in events)
    for k, e in enumerate(events):
        if k % OWN_SHARDS != shard:
            continue
        times = _decision_times(sf, e)
        want = [sf.row(e, t) for t in times]
        # the label is replaced in this session and in every session the event's rows are read from (the one
        # without its own retested extreme, after that level's creation)
        worlds = {id(w): (w, we) for w, we in (sf.world(e, t) for t in times)}
        saved = []
        for w, we in worlds.values():
            j = w.events.index(we)
            labs = {name: getattr(w, name)[we.event_id] for name in LABEL_DICTS}
            rec = w.records[j]
            saved.append((w, we, j, labs, rec))
            for name, lab in labs.items():   # every distance's label (Q-density (c)): the features read hist_labels
                other = "broke" if lab.label == "held" else "held"
                getattr(w, name)[we.event_id] = replace(lab, label=other, t_resolved=we.t + 1,
                                                        run_pts=lab.run_pts + 7.0)
            w.records[j] = replace(rec, label="broke" if rec.label == "held" else "held", t_resolved=we.t + 1)
        try:
            same(want, [sf.row(e, t) for t in times], f"{e.event_id}")
        finally:
            for w, we, j, labs, rec in saved:
                for name, lab in labs.items():
                    getattr(w, name)[we.event_id] = lab
                w.records[j] = rec


def test_own_retested_extreme_never_reaches_own_features(full):
    """A retested extreme made by the held label of an event or of a touch-group mate (F3.8; the whole touch group is
    the event's own, owner 2026-11-01, SPEC §13 Q-step11h (2)): that event's rows are the same as in a build where
    the group's levels are never made."""
    nq, es, boards, gp, sf = full
    sources = {r.source for r in sf.retested_today if r.source in sf.by_id}
    assert sources, "the synthetic day makes a retested extreme from a held touch"
    for src in sorted(sources):
        orig = SessionFeatures._new_retests
        grp = sf.group_of(sf.by_id[src])

        def without(self, grp=grp):
            return [r for r in orig(self) if r.source not in grp]
        SessionFeatures._new_retests = without
        try:
            alt = build(nq, es, boards, gp)
        finally:
            SessionFeatures._new_retests = orig
        e = sf.by_id[src]
        times = _decision_times(sf, e)
        same([sf.row(e, t) for t in times], [alt.row(alt.by_id[src], t) for t in times], src)
        ea = alt.by_id[src]
        same([sf.zone_edges(e, t) for t in times], [alt.zone_edges(ea, t) for t in times], src)
        same([sorted(x.uid for x in sf.members_at(e, t)) for t in times],
             [sorted(x.uid for x in alt.members_at(ea, t)) for t in times], src)
        assert len(sf._alts) <= MAX_ALT_BUILDS
