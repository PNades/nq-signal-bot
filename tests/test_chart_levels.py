"""The per-session chart-level engine (step 11 part 1): registry contents and creation times, the F5.14 key map,
session-to-session carry with a roll (F0.4), and the global no-lookahead test (F0.1, §7 rule 1)."""

from datetime import date, time, timedelta

import numpy as np
import pytest

from nqbot.config import load_features, load_settings
from nqbot.features import chart_levels as cl
from nqbot.features import prev_session as ps
from nqbot.features import session_tape as st
from nqbot.features.carry import CarryState

DAY = date(2026, 3, 6)          # a Friday
NS = st.NS


def synthetic_tape(day=DAY, seed=1, every_sec=6.0, start=20_000.0, symbol="NQH6"):
    rng = np.random.default_rng(seed)
    t0, t1 = st.cme_start_ns(day), st.cme_end_ns(day)
    n = int((t1 - t0) / NS / every_sec)
    ts = t0 + np.sort(rng.integers(0, t1 - t0, n))
    px = start + np.round(np.cumsum(rng.choice([-1, 0, 1], n, p=[0.3, 0.4, 0.3])) * 0.5 / 0.25) * 0.25
    size = rng.integers(1, 20, n).astype(float)
    side = rng.choice([b"B", b"A", b"N"], n, p=[0.48, 0.48, 0.04])
    return st.Tape.from_arrays(day, ts, px, size, side, symbol=symbol)


def carry_for(day=DAY):
    c = CarryState("NQ", contract="NQH6")
    rth = []
    for k in range(25):
        rth.append({"day": (day - timedelta(days=30 - k)).isoformat(), "open": 19_990.0, "high": 20_100.0 + k,
                    "low": 19_900.0 - k, "close": 20_000.0 + (k % 3), "ib_width": 50.0 + k})
    c.rth = rth
    c.prev_day = {"day": "2026-03-05", "high": 20_150.0, "low": 19_850.0}
    c.prev_rth = {"day": "2026-03-05", "high": 20_120.0, "low": 19_880.0}
    c.prev_week = {"week": "2026-02-22", "high": 20_400.0, "low": 19_600.0, "high_day": "2026-02-24",
                   "low_day": "2026-02-26"}
    c.week_start_ns = st.week_start_ns(day)
    c.weekly_anchor_ns = st.week_start_ns(day)
    c.weekly_volume = {str(int(round(p / 0.25))): 10.0 for p in np.arange(19_950, 20_050, 0.25)}
    prof = ps.profile_from_volume({str(int(round(p / 0.25))): float(1 + (p == 20_010)) * 5
                                   for p in np.arange(19_980, 20_040, 0.25)})
    prof["naked_at_start"] = True
    c.prev_profile = {"day": "2026-03-05", "globex_1800_1700": prof, "rth_0930_1600": prof}
    return c


def session(tape, carry=None, normals=True):
    f, s = load_features(), load_settings()
    inp = cl.SessionInputs(f, s, None, np.full(390, 600.0) if normals else None, {"em_1d_pct": 0.8})
    return cl.ChartSession(tape, carry or carry_for(tape.day), inp)


@pytest.fixture(scope="module")
def built():
    tape = synthetic_tape()
    return tape, session(tape)


def test_subtype_keys_cover_the_config_keys():
    f = load_features()
    keys = set(cl.SUBTYPE_KEY.values())
    assert keys == set(f["chart_level_half_width"]) == set(f["chart_level_half_width_cap"])
    assert keys <= set(f["flip_margin"]) and set(f["flip_margin"]) - keys == {"qqq_node", "ndxp_node", "dark_pool"}


def test_registry_types_and_creation_times(built):
    tape, cs = built
    sub_at = lambda t: {x["sub_type"] for x in cs.registry_at(t)}
    early = sub_at(st.et_ns(DAY, time(8, 0)))
    assert {"vwap18", "vwap18_up2", "poc_daily", "poc_weekly", "prev_poc", "prev_vah", "pdh", "pdl", "prior_rth_high",
            "prior_week_low"} <= early
    assert not early & {"on_high", "ib_high", "em_up1", "vwap0930"}         # not known yet
    assert "em_up1" in sub_at(st.et_ns(DAY, time(8, 30)))                     # the pre-open
    assert {"on_high", "on_low", "vwap0930"} <= sub_at(st.et_ns(DAY, time(9, 31)))
    assert "ib_high" not in sub_at(st.et_ns(DAY, time(9, 59))) and "ib_high" in sub_at(st.et_ns(DAY, time(10, 0)))
    for x in cs.registry_at(st.et_ns(DAY, time(12, 0))):
        lo, hi = x["band"]
        assert lo <= hi and x["key"] == cl.SUBTYPE_KEY[x["sub_type"]]
        if x["key"] in ("pdh_pdl", "ib", "on_extreme"):
            assert x["kind"] == "sweep"


def test_features_at_noon(built):
    tape, cs = built
    f = cs.features_at(st.et_ns(DAY, time(12, 0)), direction=+1)
    assert f["em_pts"] == pytest.approx(20_000 * 0.008)        # C = the last carried RTH close (k = 24: 24 % 3 = 0)
    assert f["atr"] is not None and f["vwap18"] is not None and f["or_fib_drawing"] is not None
    assert f["rvol_today"] is not None and f["minutes_since_open"] == 150
    assert cs.features_at(st.et_ns(DAY, time(8, 0)))["em_pts"] is None


def test_start_and_fold_session_roll_and_week():
    c = carry_for()
    roll = cl.start_session(c, date(2026, 3, 9), "NQM6", 217.5)      # Monday: a new week and a roll
    assert roll and c.prev_day["high"] == 20_150.0 + 217.5 and c.weekly_volume == {}
    assert c.weekly_anchor_ns == st.cme_start_ns(date(2026, 3, 9))
    tape = synthetic_tape(date(2026, 3, 9), symbol="NQM6", start=20_200.0)
    cl.fold_session(c, tape, load_features(), None, single_prints=[])
    assert c.session == "2026-03-09" and c.contract == "NQM6" and c.prev_day["day"] == "2026-03-09"
    assert c.rth[-1]["day"] == "2026-03-09" and c.weekly_volume and c.prev_profile["globex_1800_1700"]["poc"]
    c2 = CarryState("NQ", contract="NQM6", week_start_ns=st.week_start_ns(date(2026, 3, 9)))
    assert not cl.start_session(c2, date(2026, 3, 10), "NQM6", 0.0)


def test_the_week_keeps_the_session_each_extreme_printed_in():
    # F3.8 (owner 2026-11-16, SPEC §13 Q-review-stack (c)): a new extreme takes its session, an equal one keeps the
    # first; across a roll the days stay and the prices shift; the next week's prior-week levels carry them
    w = cl._week_with(None, "2026-03-09", 100.0, 90.0)
    w = cl._week_with(w, "2026-03-10", 105.0, 92.0)                  # new high Tuesday, low stays Monday
    w = cl._week_with(w, "2026-03-11", 105.0, 90.0)                  # equal high and low: the first print stays
    w = cl._week_with(w, "2026-03-12", 101.0, 85.0)                  # new low Thursday
    assert w == {"high": 105.0, "low": 85.0, "high_day": "2026-03-10", "low_day": "2026-03-12"}
    c = CarryState("NQ", contract="NQH6", week=dict(w), week_start_ns=st.week_start_ns(date(2026, 3, 12)))
    c.shift(10.0)
    assert (c.week["high"], c.week["high_day"]) == (115.0, "2026-03-10")
    cl.start_session(c, date(2026, 3, 16), "NQH6", 0.0)              # Monday: the week becomes the prior week
    assert c.prev_week["high_day"] == "2026-03-10" and c.prev_week["low_day"] == "2026-03-12"


def test_fold_session_records_the_weekly_print_days():
    c = CarryState("NQ", contract="NQM6", week_start_ns=st.week_start_ns(date(2026, 3, 9)))
    t1 = synthetic_tape(date(2026, 3, 9), symbol="NQM6", start=20_200.0)
    cl.fold_session(c, t1, load_features(), None, single_prints=[])
    assert c.week["high_day"] == c.week["low_day"] == "2026-03-09"
    t2 = synthetic_tape(date(2026, 3, 10), symbol="NQM6", start=21_000.0, seed=3)   # far above: a new high only
    cl.fold_session(c, t2, load_features(), None, single_prints=[])
    assert c.week["high_day"] == "2026-03-10" and c.week["low_day"] == "2026-03-09"


def test_carried_highs_lows_know_their_print_session(built):
    # PDH/PDL and the prior RTH printed the session before, the prior week on its own day; ON/IB are not listed
    _, cs = built
    assert cs.print_session["pdh"] == cs.print_session["pdl"] == date(2026, 3, 5)
    assert cs.print_session["prior_rth_high"] == date(2026, 3, 5)
    assert cs.print_session["prior_week_high"] == date(2026, 2, 24)
    assert cs.print_session["prior_week_low"] == date(2026, 2, 26)
    c = carry_for()
    c.prev_week = {**c.prev_week, "high_day": "2026-02-23", "low_day": "2026-02-27"}
    ps_ = cl._print_sessions(c, DAY)
    assert ps_["prior_week_high"] == date(2026, 2, 23) and ps_["prior_week_low"] == date(2026, 2, 27)
    assert not {"on_high", "on_low", "ib_high", "ib_low"} & set(ps_)


@pytest.mark.parametrize("missing", [("high_day",), ("low_day",), ("high_day", "low_day")])
def test_a_carry_without_the_weekly_print_days_stops_with_a_clear_error(missing):
    # owner 2026-11-20 (SPEC §13 Q-review-stack (c)): a checkpoint from code before features_version 40 is refused,
    # never aged by the old rule (the registry session)
    c = carry_for()
    c.prev_week = {k: v for k, v in c.prev_week.items() if k not in missing}
    with pytest.raises(cl.CarryTooOld, match="features_version 40") as e:
        cl._print_sessions(c, DAY)
    assert all(k in str(e.value) for k in missing) and "prior week" in str(e.value)
    with pytest.raises(cl.CarryTooOld):
        session(synthetic_tape(DAY), c)
    # the running week from an old checkpoint is refused at the next fold, before it could become a prior week
    old_week = {"high": 20_100.0, "low": 19_900.0}
    with pytest.raises(cl.CarryTooOld, match="carried week"):
        cl._week_with(old_week, "2026-03-10", 20_200.0, 19_800.0)
    c2 = CarryState("NQ", contract="NQM6", week=dict(old_week), week_start_ns=st.week_start_ns(date(2026, 3, 9)))
    with pytest.raises(cl.CarryTooOld):
        cl.fold_session(c2, synthetic_tape(date(2026, 3, 10), symbol="NQM6"), load_features(), None,
                        single_prints=[])


def _strip(d):
    return {k: v for k, v in d.items()}


@pytest.mark.slow
def test_no_lookahead_cut_and_shift():
    """F0.1 global: every feature and level state at t is unchanged when the data after t is removed, or shifted
    +1 s / +1 bar."""
    tape = synthetic_tape(seed=5, every_sec=8.0)
    full = session(tape)
    for t_ in (time(3, 0), time(9, 31, 30), time(9, 50), time(10, 0), time(10, 31), time(13, 7, 12)):
        t = st.et_ns(DAY, t_)
        n = tape.idx_at(t)
        variants = [tape.cut(t)]
        for shift in (NS, 60 * NS):
            ts = tape.ts.copy()
            ts[n:] += shift
            variants.append(st.Tape(DAY, tape.symbol, ts, tape.price, tape.size, tape.side))
        for v in variants:
            other = session(v)
            assert other.features_at(t, +1) == full.features_at(t, +1), t_
            a = {x["level_id"]: x for x in full.registry_at(t)}
            b = {x["level_id"]: x for x in other.registry_at(t)}
            assert a == b, t_
