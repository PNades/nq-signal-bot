"""Step 11 part 1: carried state and roll shift (F0.4), session highs/lows and retested extremes (F3.7, F3.8), the
previous-session profile and value context (F3.2b, F3.12), freshness (F5.11), approach (F5.18) and the session volume
baselines (F9.10)."""

from datetime import date, time

import numpy as np
from pytest import approx

from nqbot.features import approach, carry, freshness, prev_session, profile, rvol, session_levels
from nqbot.features import session_tape as st
from nqbot.features import tpo

DAY = date(2026, 3, 6)
NS = st.NS
MIN = st.MIN_NS


def t_et(h, m, s=0, day=DAY):
    return st.et_ns(day, time(h, m, s))


# --- F0.4 carry and roll shift -------------------------------------------------------------------------------

def test_carried_pdh_shifted_on_the_roll_day_only():
    c = carry.CarryState("NQ", prev_day={"day": "2026-03-16", "high": 20_000.0, "low": 19_800.0})
    c.shift(0.0)
    assert c.prev_day["high"] == 20_000.0
    c.shift(250.0)
    assert c.prev_day["high"] == 20_250.0 and c.prev_day["low"] == 20_050.0


def test_shift_moves_every_carried_price_and_round_trips_json():
    run = tpo.Run("r", DAY, 0, [100, 101], [100.0, 101.0], 1.0, [False, False])
    prof = prev_session.profile_from_volume({"400": 5.0, "401": 3.0})        # ticks 400/401 = 100.00 / 100.25
    c = carry.CarryState("NQ", rth=[{"day": "x", "high": 10.0, "low": 5.0, "close": 7.0, "ib_width": 3.0}],
                         prev_profile={"day": "x", "globex_1800_1700": prof}, single_prints=[run.to_json()],
                         retest_candidates=[{"price": 50.0, "type": "pdh_pdl", "session": "2026-03-05"}])
    c.shift(217.5)
    assert c.rth[0]["close"] == 224.5 and c.single_prints[0]["bin_lo"] == [317.5, 318.5]
    assert c.prev_profile["globex_1800_1700"]["poc"] == approx(317.5) and "1270" in c.prev_profile["globex_1800_1700"]["volume"]
    assert c.retest_candidates[0]["price"] == 267.5 and c.ib_widths(5) == [3.0]
    assert carry.CarryState.from_json(c.to_json()) == c


def test_damaged_session_hands_no_levels():
    c = carry.CarryState("NQ", prev_day={"high": 1.0, "low": 0.0}, prev_rth={"high": 1.0, "low": 0.0},
                         prev_profile={"x": 1})
    c.drop_handed_levels()
    assert c.prev_day is None and c.prev_rth is None and c.prev_profile is None


# --- F3.7 session highs/lows ---------------------------------------------------------------------------------

def test_day_stats_on_rth_and_close():
    ts = [t_et(20, 0, day=date(2026, 3, 5)), t_et(9, 0), t_et(9, 30), t_et(12, 0), t_et(15, 59, 59), t_et(16, 30)]
    tape = st.Tape.from_arrays(DAY, ts, [100, 120, 110, 130, 105, 140])
    s = session_levels.day_stats(tape)
    assert (s["on_high"], s["on_low"]) == (120, 100) and (s["rth_high"], s["rth_low"]) == (130, 105)
    assert s["rth_close"] == 105 and (s["high"], s["low"]) == (140, 100)
    assert session_levels.on_levels(tape) == (120, 100) and session_levels.on_levels(tape, eth_empty=True) is None


# --- F3.8 retested extremes ----------------------------------------------------------------------------------

def test_retested_extreme_from_a_held_touch_within_tolerance():
    sessions = [date(2026, 3, d) for d in (2, 3, 4, 5, 6)]
    cand = [session_levels.Candidate(100.0, "on_extreme", date(2026, 3, 4))]
    held = [{"price": 100.75, "t_resolved": 5, "label": "held"}]          # 3 ticks
    out = session_levels.retests_from_touches(cand, held, DAY, sessions, tol_ticks=4, lookback=10)
    assert len(out) == 1 and out[0].created_ns == 5 and out[0].price == 100.0
    far = [{"price": 101.5, "t_resolved": 5, "label": "held"}]            # 6 ticks
    assert session_levels.retests_from_touches(cand, far, DAY, sessions, 4, 10) == []
    broke = [{"price": 100.75, "t_resolved": 5, "label": "broke"}]
    assert session_levels.retests_from_touches(cand, broke, DAY, sessions, 4, 10) == []
    same_day = [session_levels.Candidate(100.0, "on_extreme", DAY)]        # only later sessions count (FQ-22)
    assert session_levels.retests_from_touches(same_day, held, DAY, sessions, 4, 10) == []


def test_retested_extreme_ages_out():
    sessions = [date(2026, 3, d) for d in (2, 3, 4, 5, 6)]
    lv = session_levels.Retested("x", 100.0, "on_extreme", date(2026, 3, 2), 0)
    assert not session_levels.expired(lv, DAY, sessions, lookback=4)
    assert session_levels.expired(lv, DAY, sessions, lookback=3)


# --- F3.2b / F3.12 -------------------------------------------------------------------------------------------

def tick(p):
    return str(int(profile.to_tick(p)))


def test_previous_profile_poc_and_naked_flag():
    prof = prev_session.profile_from_volume({tick(100.0): 10, tick(100.25): 6, tick(99.75): 4})
    assert prof["poc"] == 100.0
    tape = st.Tape.from_arrays(DAY, [t_et(9, 30), t_et(9, 31)], [101.0, 100.0])
    assert prev_session.naked_at(tape, prof, True, t_et(9, 30, 30)) is True
    assert prev_session.naked_at(tape, prof, True, t_et(9, 31)) is False
    assert prev_session.naked_after(prof, np.array([99.75, 100.25])) is True


def test_rth_anchor_ignores_trades_outside_rth():
    prev = date(2026, 3, 5)
    ts = [t_et(20, 0, day=date(2026, 3, 4)), t_et(10, 0, day=prev), t_et(10, 1, day=prev), t_et(16, 30, day=prev)]
    tape = st.Tape.from_arrays(prev, ts, [50.0, 100.0, 100.0, 200.0], [99, 1, 1, 99])
    g = prev_session.profile_from_trades(tape, "globex_1800_1700")
    r = prev_session.profile_from_trades(tape, "rth_0930_1600")
    assert g["low"] == 50.0 and r["low"] == r["high"] == r["poc"] == 100.0


def test_value_context_feature_spec_numbers():
    prof = {"poc": 103.0, "vah": 110.0, "val": 100.0}
    assert prev_session.open_vs_prev_va(112.0, prof) == "above"
    assert prev_session.open_outside_prev_va_em(112.0, prof, 20.0) == approx(0.1)
    assert prev_session.open_outside_prev_va_em(105.0, prof, 20.0) == 0.0
    now = profile.ProfileStats(0, 0, 0, poc=104.0, val=106.0, vah=116.0, lvns=(), volume=1)
    first = profile.ProfileStats(0, 0, 0, poc=108.0, val=0, vah=0, lvns=(), volume=1)
    long = prev_session.context(105.0, prof, 20.0, now, first, direction=+1)
    assert long["prev_va_overlap"] == approx(0.4) and long["dpoc_drift_em"] == approx(-0.2)
    assert long["poc_migration_em"] == approx(0.05) and long["dist_prev_poc_em"] == approx(0.1)
    short = prev_session.context(105.0, prof, 20.0, now, first, direction=-1)
    assert short["poc_migration_em"] == approx(-0.05) and short["poc_migration_em_abs"] == approx(0.05)


# --- F5.11 freshness -----------------------------------------------------------------------------------------

def test_freshness_counts_only_resolved_holds():
    t1 = freshness.TouchRecord(t_et(10, 0), ("L",), "z1", "floor", t_resolved=t_et(10, 20), label="held")
    at_1010 = freshness.level_freshness([t1], "L", "floor", t_et(10, 10))
    assert at_1010 == {"tests_so_far": 1, "holds_so_far": 0, "pending_tests": 1, "minutes_since_last_test": 10.0}
    at_1030 = freshness.level_freshness([t1], "L", "floor", t_et(10, 30))
    assert at_1030["holds_so_far"] == 1 and at_1030["pending_tests"] == 0
    assert freshness.level_freshness([t1], "L", "ceiling", t_et(10, 30))["tests_so_far"] == 0   # per role
    z = freshness.zone_freshness([t1], ["L", "M"], "floor", "z1", t_et(10, 30))
    assert z["zone_tests_so_far"] == 1 and z["tests_so_far_min"] == 0 and z["tests_so_far_max"] == 1


# --- F5.18 approach ------------------------------------------------------------------------------------------

def test_approach_steady_rise_and_dip():
    t = t_et(10, 30)
    steady = [(t - (15 - i) * MIN, 20_040 + 4 * i) for i in range(16)]
    tape = st.Tape.from_arrays(DAY, [x[0] for x in steady], [x[1] for x in steady])
    f = approach.approach_features(tape, t, from_below=True, atr=200.0)
    assert f["approach_move_15"] == approx(0.3) and f["approach_straightness_15"] == approx(1.0)
    assert f["approach_move_1"] == approx(4 / 200) and f["approach_range_15"] == approx(56 / 200)
    dip = [(t - (15 - i) * MIN, p) for i, p in enumerate(
        [20_040, 20_044, 20_048, 20_052, 20_032, 20_052, 20_056, 20_060, 20_064, 20_068, 20_072, 20_076, 20_080,
         20_084, 20_088, 20_100])]
    tape = st.Tape.from_arrays(DAY, [x[0] for x in dip], [x[1] for x in dip])
    assert approach.approach_features(tape, t, True, 200.0)["approach_straightness_15"] == approx(60 / 100)
    assert approach.approach_features(tape, t, False, 200.0)["approach_move_15"] == approx(-0.3)   # from above


def test_approach_null_before_the_cme_day():
    t = st.cme_start_ns(DAY) + 5 * MIN
    tape = st.Tape.from_arrays(DAY, [st.cme_start_ns(DAY), t], [100.0, 101.0])
    f = approach.approach_features(tape, t, True, 100.0)
    assert f["approach_move_15"] is None and f["approach_straightness_15"] is None and f["approach_move_1"] is not None


# --- F9.10 session volume ------------------------------------------------------------------------------------

def test_rth_normals_window_matches_volume_norms():
    n = np.full(390, 1000.0)
    n[:30] = 3000.0
    rn = rvol.RthNormals.of(DAY, n)
    a = np.array([t_et(9, 59, 45)])
    b = np.array([t_et(10, 0, 45)])
    assert rn.between(a, b)[0] == approx(750 + 750)              # the feature_spec F9.10 window
    assert rn.between(np.array([t_et(9, 0)]), np.array([t_et(9, 30, 30)]))[0] == approx(1500)   # RTH part only
    assert list(rn.in_rth(np.array([t_et(9, 29, 59), t_et(9, 30), t_et(16, 0)]))) == [False, True, False]


def test_session_rvol_and_vol_tod():
    n = np.full(390, 3000.0)
    ts = [t_et(9, 31) + k * NS for k in range(60)]                # 6,000 contracts in the 09:31 minute
    tape = st.Tape.from_arrays(DAY, ts, np.full(60, 100.0), np.full(60, 100.0))
    sv = rvol.SessionVolume.of(tape, n, {"rvol_clip_mult": 5})
    assert sv.vol_tod(t_et(9, 32) - 1, 60) == approx(6000 / 3000, rel=1e-6)
    assert sv.rvol_today(t_et(9, 32)) == approx(6000 / 6000)      # 0 + 6,000 over two 3,000 minutes
    assert sv.minutes_since_open(t_et(10, 0)) == 30.0
    assert rvol.SessionVolume.of(tape, None, {"rvol_clip_mult": 5}).rvol_today(t_et(9, 32)) is None


def test_baseline_needs_twenty_full_undamaged_sessions():
    from datetime import timedelta

    days = [d for d in (date(2026, 2, 1) + timedelta(days=k) for k in range(40)) if d.weekday() < 5]
    rng = np.random.default_rng(7)
    vol = {d: np.full(390, float(rng.integers(0, 10_000))) for d in days}
    target = days[-1]
    full = [d for d in days[:-1] if rvol.is_full_session(d)]          # 2026-02-16 (Presidents' Day) is left out
    assert date(2026, 2, 16) not in full
    got = rvol.baseline(target, days, vol.get, n=20, damaged=set())
    assert got[0] == np.median([vol[d][0] for d in full[-20:]])
    assert rvol.baseline(full[19], days, vol.get, n=20, damaged=set()) is None     # 19 prior full sessions
    skip = rvol.baseline(target, days, vol.get, n=20, damaged={full[-1]})
    assert skip[0] == np.median([vol[d][0] for d in full[-21:-1]])


def test_roll_report_rows_offsets_around_each_roll():
    sessions = [date(2026, 3, d) for d in (9, 10, 11, 12, 13, 16, 17, 18, 19, 20)]
    rows = rvol.roll_report_rows([{"root": "NQ", "roll_day": "2026-03-17"}], sessions,
                                 lambda d: {"rvol_today_close": float(d.day)}, span=2)
    assert [(r["offset"], r["day"]) for r in rows] == [(-2, "2026-03-13"), (-1, "2026-03-16"), (0, "2026-03-17"),
                                                       (1, "2026-03-18"), (2, "2026-03-19")]
    assert rows[2]["rvol_today_close"] == 17.0
    assert rvol.roll_report_rows([{"root": "NQ", "roll_day": "2026-06-16"}], sessions, lambda d: {}) == []
