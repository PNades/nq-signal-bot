"""Fixes from the independent review of step 11 part 1 (2026-10-31), each with a hand-built case:
IB roles at the lock (F3.6), carried single-print ids (Q-step11b (3)), LVN smoothing on narrow profiles and half-up
identity (F3.4), the provisional role ending at the cross back (F2.6 step 5), the ES ratio before/after the open
(F0.11, Q-step11b (2)), a roll without a recorded spread, the early-close RTH bound (F9.10), LVN spans and the 10:30 IB
replacement in the engine, damage.load, and the no-lookahead shift test over approach, vol_tod and the ES path."""

import json
from datetime import date, time

import numpy as np
import pytest
from pytest import approx

from nqbot.config import load_features, load_settings
from nqbot.features import chart_levels as cl
from nqbot.features import damage
from nqbot.features import level_state as ls
from nqbot.features import profile as pf
from nqbot.features import rvol
from nqbot.features import session_tape as st
from nqbot.features import tpo
from nqbot.features.approach import approach_features
from nqbot.features.bands import StepBand
from nqbot.features.carry import CarryState
from tests.test_chart_levels import carry_for, session, synthetic_tape

DAY = date(2026, 3, 6)
NS = st.NS
MIN = st.MIN_NS
P = ls.Params(rearm_dist=10, flip_margin=0, flip_bar_minutes=5, sweep_max_depth=15, sweep_reclaim_bars=3)


def t_et(h, m, s=0, day=DAY):
    return st.et_ns(day, time(h, m, s))


# --- F3.6: the IB starts as a ceiling / floor at the lock --------------------------------------------------------

def test_ib_high_locked_near_price_records_its_break():
    """Review finding 1: IB high 20,100 (band 20,096-20,104) locked with price at 20,095, then a rally to 20,150."""
    tape = st.Tape.from_arrays(DAY, [t_et(10, 0, 5), t_et(10, 1, 0)], [20_095.0, 20_150.0])
    band = StepBand.const(tape, t_et(10, 0), 20_096.0, 20_104.0)
    kw = dict(end_ns=t_et(10, 20))
    plain = ls.LevelState("ib", ls.SWEEP, tape, band, P, st.bars(tape, 1), st.bars(tape, 5), **kw).run()
    assert [n for _, n in plain.events] == ["role_floor"]               # without a role: the break is lost
    ib = ls.LevelState("ib", ls.SWEEP, tape, band, P, st.bars(tape, 1), st.bars(tape, 5), initial_role=ls.CEILING,
                       **kw).run()
    s = ib.at(t_et(10, 1, 0))
    assert s["crossed"] and s["cross_count_today"] == 1 and s["xb_depth"] == approx(46.0)   # > 15: broke at once


def test_engine_gives_ib_its_roles_and_replaces_it_at_1030():
    o = st.rth_open_ns(DAY)
    rows = [(st.cme_start_ns(DAY) + MIN, 20_050.0), (o + 2 * MIN, 20_000.0), (o + 28 * MIN, 20_200.0),
            (o + 30 * MIN + 5 * NS, 20_210.0), (o + 45 * MIN, 20_100.0), (o + 65 * MIN, 20_100.0)]
    tape = st.Tape.from_arrays(DAY, [r[0] for r in rows], [r[1] for r in rows])
    cs = session(tape)
    ib = {lid: recs for lid, recs in cs.levels.items() if lid.startswith("ib_")}
    assert set(ib) == {f"ib_high-{DAY}", f"ib_low-{DAY}", f"ib_high-{DAY}-1030"}
    assert ib[f"ib_high-{DAY}"][0].state.initial_role == ls.CEILING
    assert ib[f"ib_low-{DAY}"][0].state.initial_role == ls.FLOOR
    assert ib[f"ib_high-{DAY}-1030"][0].state.initial_role is None        # fresh: the approach rule
    assert cs.level_at(f"ib_high-{DAY}", o + 59 * MIN) is not None and cs.level_at(f"ib_high-{DAY}", o + 60 * MIN) is None
    assert cs.level_at(f"ib_high-{DAY}-1030", o + 60 * MIN)["band"][0] == approx(20_210.0 - 4)


# --- Q-step11b (3): a carried single print keeps its ids --------------------------------------------------------

def test_carried_single_print_keeps_its_id_next_session():
    rows = [(t_et(9, 35), 100.5), (t_et(9, 50), 100.5)] + [(t_et(10, 1) + k * NS, 100.5 + k) for k in range(1, 10)]
    rows += [(t_et(10, 40), 110.5), (t_et(11, 0), 101.5), (t_et(11, 5), 102.5)]       # fills from the bottom
    tape = st.Tape.from_arrays(DAY, [r[0] for r in rows], [r[1] for r in rows])
    sp = tpo.SinglePrints(1.0, 8, 60.0).run_session(tape, DAY, [], end_ns=t_et(12, 0))
    (lid,) = [k for k, v in sp.levels.items() if v.end_ns is None]
    carried = [tpo.Run.from_json(r.to_json()) for r in sp.carry()]
    nxt = date(2026, 3, 9)
    tape2 = st.Tape.from_arrays(nxt, [st.et_ns(nxt, time(10, 0))], [200.0])
    sp2 = tpo.SinglePrints(1.0, 8, 60.0).run_session(tape2, nxt, carried, end_ns=st.et_ns(nxt, time(11, 0)))
    assert [k for k, v in sp2.levels.items() if v.end_ns is None] == [lid]


# --- F3.4: smoothing on a narrow profile, half-up identity ------------------------------------------------------

def test_smoothing_keeps_the_profile_length_and_centre_on_narrow_profiles():
    vol = np.array([9.0, 9.0, 1.0, 9.0, 9.0])                   # narrower than the 7-tick kernel (smooth 2, 3 sigma)
    s = pf.smooth(vol, 2, 3)
    k = pf.gaussian_kernel(2, 3)
    assert len(s) == 5 and int(np.argmin(s)) == 2
    assert s == approx(np.convolve(vol, k, "full")[3:8])
    assert len(pf.smooth(np.ones(10), 8, 3)) == 10                # 25-tick kernel, 10-tick profile


def test_lvn_identity_rounds_half_up():
    vol = np.zeros(41)
    vol[:18] = 100
    vol[23:] = 100                                               # valley at index 20
    lo = int(pf.to_tick(20_002.5 - 20 * 0.25))
    st_ = pf.stats(vol, lo, 0, {"lvn_smooth_ticks": 2, "lvn_gauss_trunc_sigma": 3, "lvn_max_pct": 0.3,
                                "lvn_merge_pts": 5})
    assert st_.lvns == ((20_005.0, 20_002.5),)                  # 20,002.5 / 5 = 4,000.5 -> up, not to even


# --- F2.6 step 5: the cross back ends the provisional role -----------------------------------------------------

def test_provisional_role_ends_at_the_cross_back():
    tape = st.Tape.from_arrays(DAY, [t_et(10, 0), t_et(10, 0, 10), t_et(10, 0, 20)], [89.0, 101.25, 99.75])
    lv = ls.LevelState("n", ls.NODE, tape, StepBand.const(tape, t_et(9, 59), 100.0, 101.0), P, st.bars(tape, 1),
                       st.bars(tape, 5), end_ns=t_et(10, 5)).run()
    assert lv.at(t_et(10, 0, 15))["provisional_role"] is True
    s = lv.at(t_et(10, 0, 25))
    assert s["crossed"] and s["provisional_role"] is False and not s["active"]


# --- F0.11 / Q-step11b (2): ES ratio before and after the open ------------------------------------------------

def test_es_equivalent_feature_spec_example():
    f = load_features()
    assert ls.Params.from_features(f, "pdh_pdl", 7_000 / 30_600).rearm_dist == approx(2.2876, abs=1e-4)


def es_session(tape, scale_from_ns):
    f, s = load_features(), load_settings()
    c = CarryState("ES", contract="ESH6", rth=[{"day": "2026-03-05", "open": 6_990.0, "high": 7_050.0, "low": 6_950.0,
                                                 "close": 7_000.0, "ib_width": 10.0}] * 25,
                   prev_day={"day": "2026-03-05", "high": 7_060.0, "low": 6_940.0})
    inp = cl.SessionInputs(f, s, None, None, {"em_1d_pct": 0.8}, scale_today=0.25, scale_prev=0.2,
                           scale_from_ns=scale_from_ns)
    return cl.ChartSession(tape, c, inp, root="ES")


def test_es_levels_use_the_previous_ratio_until_todays_is_known():
    o = st.rth_open_ns(DAY)
    tape = synthetic_tape(seed=3, every_sec=30.0, start=7_000.0, symbol="ESH6")
    cs = es_session(tape, o + 2 * NS)
    hw = load_features()["chart_level_half_width"]["pdh_pdl"]
    rec = cs.levels["es_pdh-2026-03-05"][0]
    before, after = rec.state.band.at(o + 1 * NS), rec.state.band.at(o + 2 * NS)
    assert before[1] - before[0] == approx(2 * hw * 0.2) and after[1] - after[0] == approx(2 * hw * 0.25)
    assert rec.state._base_params.rearm_dist == approx(10 * 0.2)
    assert rec.state.params_from[0][0] == o + 2 * NS and rec.state.params_from[0][1].rearm_dist == approx(10 * 0.25)
    assert {x["sub_type"] for x in cs.registry_at(o + 60 * MIN)} >= {"pdh", "pdl", "on_high", "on_low", "em_up1"}


# --- a roll without a recorded spread is refused; a bad spread leaves the carry whole -------------------------

def test_roll_without_a_spread_is_refused():
    c = carry_for()
    with pytest.raises(cl.RollSpreadMissing):
        cl.start_session(c, date(2026, 3, 9), "NQM6", None)
    assert cl.roll_spread_of("NQ", date(2026, 3, 17), [{"root": "NQ", "roll_day": "2026-03-17", "spread": 217.5}]) == 217.5
    assert cl.roll_spread_of("NQ", date(2026, 3, 18), [{"root": "NQ", "roll_day": "2026-03-17", "spread": 217.5}]) is None
    before = json.dumps(c.to_json(), sort_keys=True)
    with pytest.raises(ValueError):
        c.shift(217.3)
    assert json.dumps(c.to_json(), sort_keys=True) == before


def test_rth_keep_guard():
    f = {**load_features(), "ib_avg_sessions": 60}
    inp = cl.SessionInputs(f, load_settings(), None, None, None)
    with pytest.raises(ValueError, match="RTH_KEEP"):
        cl.ChartSession(synthetic_tape(every_sec=600.0), carry_for(), inp)


# --- F9.10: RTH ends at the early close -----------------------------------------------------------------------

def test_rvol_stops_at_the_early_close():
    d = date(2025, 11, 28)                                     # early close 13:00 (calendar.yaml)
    ts = [st.et_ns(d, time(12, 0)), st.et_ns(d, time(13, 5))]
    sv = rvol.SessionVolume.of(st.Tape.from_arrays(d, ts, [1.0, 1.0]), np.full(390, 100.0), {"rvol_clip_mult": 5})
    assert sv.rvol_today(st.et_ns(d, time(12, 59))) is not None
    assert sv.rvol_today(st.et_ns(d, time(13, 5))) is None and sv.vol_tod(st.et_ns(d, time(13, 5)), 60) is None


# --- LVN spans in the engine ---------------------------------------------------------------------------------

def test_lvn_reappearing_is_a_new_record_under_the_same_id():
    tape = synthetic_tape(every_sec=600.0)
    cs = session(tape)
    cs.levels = {}
    t = [t_et(10, k) for k in range(5)]
    mk = lambda lv: pf.ProfileStats(0, 0, 0, 0, 0, 0, lv, 1.0)
    prof = pf.DevelopingProfile("daily", np.array(t, dtype=np.int64),
                                [mk(((20_000.0, 20_001.0),)), mk(((20_000.0, 20_001.25),)), mk(()),
                                 mk(((20_000.0, 20_000.5),)), mk(())])
    cs._profile_levels(prof, "daily", "x")
    recs = cs.levels["lvn_daily-x-20000"]
    assert len(recs) == 2
    assert [int(v) for v in recs[0].state.band.times] == t[:2] and recs[0].state.band.end_ns == t[2]
    assert recs[1].state.band.start_ns == t[3] and recs[1].state.band.end_ns == t[4]
    assert cs.level_at("lvn_daily-x-20000", t[2]) is None and cs.level_at("lvn_daily-x-20000", t[3]) is not None


# --- damage.load from the stored file --------------------------------------------------------------------------

def test_damage_load_reads_the_step8_file(tmp_path):
    (tmp_path / "step8").mkdir()
    rows = [{"cme_day": "2025-11-28", "part": "RTH", "span": "11-28 09:30–11-28 13:00 ET", "series": "NQ trades",
             "empty_minutes": 40, "baseline_empty_max": 0, "baseline_gap_max_sec": 6.5, "gaps": []}]
    (tmp_path / "step8" / "dataset_condition.json").write_text(json.dumps({"damage": rows}), encoding="utf-8")
    s = load_settings()
    assert damage.load(tmp_path, s)[date(2025, 11, 28)].rth_dropped
    assert damage.load(tmp_path / "missing", s) == {}


# --- the no-lookahead shift test over approach, vol_tod and the ES path (F0.1, F5.18, F9.10) ------------------

def _variants(tape, t):
    n = tape.idx_at(t)
    out = [tape.cut(t)]
    for shift in (NS, 60 * NS):
        ts = tape.ts.copy()
        ts[n:] += shift
        out.append(st.Tape(tape.day, tape.symbol, ts, tape.price, tape.size, tape.side))
    return out


def test_no_lookahead_approach_vol_tod_and_es():
    tape = synthetic_tape(seed=9, every_sec=10.0)
    es_tape = synthetic_tape(seed=4, every_sec=20.0, start=7_000.0, symbol="ESH6")
    o = st.rth_open_ns(DAY)
    full_es = es_session(es_tape, o + 2 * NS)
    normals = np.full(390, 600.0)
    for t_ in (time(9, 31, 1), time(10, 0), time(11, 17, 33)):
        t = st.et_ns(DAY, t_)
        ref = {(fb, a): approach_features(tape, t, fb, a) for fb in (True, False) for a in (150.0,)}
        ref_vol = rvol.SessionVolume.of(tape, normals, {"rvol_clip_mult": 5}).vol_tod(t, 60)
        for v in _variants(tape, t):
            for (fb, a), r in ref.items():
                assert approach_features(v, t, fb, a) == r, t_
            assert rvol.SessionVolume.of(v, normals, {"rvol_clip_mult": 5}).vol_tod(t, 60) == ref_vol
        a_es = {x["level_id"]: x for x in full_es.registry_at(t)}
        for v in _variants(es_tape, t):
            other = es_session(v, o + 2 * NS)
            assert {x["level_id"]: x for x in other.registry_at(t)} == a_es, t_
            assert other.features_at(t) == full_es.features_at(t)
