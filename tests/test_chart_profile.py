"""Step 11 part 1: the session tape, F3.1 VWAP and σ bands, F3.2 POC, F3.3 value area / VAH-VAL zones, F3.4 LVNs."""

from datetime import date, time

import numpy as np
from pytest import approx

from nqbot.features import profile as pf
from nqbot.features import session_tape as st
from nqbot.features import vwap as vw

DAY = date(2026, 3, 6)
NS = st.NS
T0 = st.et_ns(DAY, time(10, 0))


def tape(prices, sizes=None, secs=None, sides=None):
    n = len(prices)
    ts = [T0 + int((secs[i] if secs else i) * NS) for i in range(n)]
    return st.Tape.from_arrays(DAY, ts, prices, sizes, sides)


# --- tape -----------------------------------------------------------------------------------------------------

def test_tape_known_at_and_bars_labelled_by_start():
    t = tape([100, 101, 102], secs=[0, 30, 60])
    assert t.idx_at(T0 + 30 * NS) == 2 and t.idx_before(T0 + 30 * NS) == 1
    assert t.last_price_at(T0 - 1) is None and t.last_price_at(T0 + 59 * NS) == 101
    b = st.bars(t, 1)
    assert list(b.start) == [T0, T0 + 60 * NS] and list(b.close) == [101, 102]
    # F0.1: the 10:00 bar covers [10:00, 10:01) and is known at 10:01:00, not at 10:00:59.999
    assert b.known_at(T0 + 60 * NS - 1_000_000) == 0 and b.known_at(T0 + 60 * NS) == 1


def test_tape_sides_and_sequence_order():
    t = st.Tape.from_arrays(DAY, [T0, T0, T0], [1, 2, 3], [1, 1, 1], [b"B", b"A", b"N"], sequence=[3, 1, 2])
    assert list(t.price) == [2, 3, 1] and list(t.side) == [-1, 0, 1]


def test_week_start_is_sunday_1800():
    assert st.week_start_ns(date(2026, 3, 6)) == st.et_ns(date(2026, 3, 1), time(18, 0))   # Friday -> Sunday 03-01
    assert st.week_start_ns(date(2026, 3, 2)) == st.et_ns(date(2026, 3, 1), time(18, 0))   # Monday


# --- F3.1 VWAP ------------------------------------------------------------------------------------------------

def test_vwap_and_sigma_feature_spec_example():
    t = tape([100, 102, 104], [1, 1, 2])
    v = vw.Vwap.build(t, T0)
    m, s = v.at(T0 + 2 * NS)
    assert m == approx(102.5) and s == approx(np.sqrt(2.75))
    assert v.line_at(2, T0 + 2 * NS) == approx(102.5 + 2 * np.sqrt(2.75))
    assert v.at(T0 - 1) is None


def test_vwap_anchor_excludes_earlier_trades_and_updates_every_trade():
    t = tape([50, 100, 102, 104], [5, 1, 1, 2])
    v = vw.Vwap.build(t, T0 + NS)        # anchor after the first trade
    assert v.at(T0 + NS)[0] == 100 and v.at(T0 + 3 * NS)[0] == approx(102.5)
    band = v.band(1, 4.0)
    lo, hi = band.at(T0 + 3 * NS)
    assert (lo + hi) / 2 == approx(102.5 + np.sqrt(2.75)) and hi - lo == approx(8.0)
    assert vw.sub_type("vwap18", -2) == "vwap18_dn2" and vw.key_of(0) == "vwap" and vw.key_of(1) == "vwap_band"


# --- F3.2 POC and its instants -------------------------------------------------------------------------------

def test_poc_waits_for_the_next_profile_instant():
    """Trades at 100.00 x5, 100.25 x3 before 10:01:00 and 100.25 x4 at 10:01:00.5 -> POC 100.00 at 10:01:00."""
    t = tape([100.0, 100.25, 100.25], [5, 3, 4], secs=[10, 20, 60.5])
    p = pf.DevelopingProfile.build("daily", t, T0, T0, T0 + 120 * NS, 60, None)
    assert p.at(T0 + 60 * NS).poc == 100.0
    assert p.at(T0 + 60 * NS + 600_000_000).poc == 100.0     # 10:01:00.6: still the 10:01:00 instant
    assert p.at(T0 + 120 * NS).poc == 100.25


def test_poc_ties_midpoint_then_volume_median_then_lower():
    assert pf.poc_index(np.array([5, 1, 5, 1, 1])) == 2           # nearest the midpoint (index 2)
    # equal distance from the midpoint (1.5): indices 1 and 2; the volume median decides
    assert pf.poc_index(np.array([6, 9, 9, 1])) == 1               # median at index 1 (half the volume at or below)
    assert pf.poc_index(np.array([1, 9, 9, 6])) == 2               # median at index 2
    # candidates 0 and 4, both 2 from the midpoint, median at index 2: an exact tie after both -> the lower price
    assert pf.poc_index(np.array([9, 1, 1, 1, 9])) == 0


def test_weekly_profile_roll_keeps_only_new_contract():
    """A roll on Wednesday: the weekly profile from Wednesday's 18:00 open holds only the new contract's trades."""
    t = tape([20250.0, 20250.25], [3, 1])
    carried_old = {pf.to_tick(20000.0).item(): 50.0}
    roll_day = pf.DevelopingProfile.build("weekly", t, T0, T0, T0 + 60 * NS, 60, None, carried=None)
    assert roll_day.at(T0 + 60 * NS).volume == 4 and roll_day.at(T0 + 60 * NS).low == 20250.0
    later = pf.DevelopingProfile.build("weekly", t, T0 - 86_400 * NS, T0, T0 + 60 * NS, 60, None, carried=carried_old)
    assert later.at(T0 + 60 * NS).volume == 54 and later.at(T0 + 60 * NS).low == 20000.0


# --- F3.3 value area and VAH/VAL zones ----------------------------------------------------------------------

def test_value_area_one_tick_at_a_time():
    vol = np.array([1, 2, 3, 10, 4, 1, 1, 8], dtype=float)   # total 30, 70% = 21
    poc = pf.poc_index(vol)
    assert poc == 3
    # 10 -> up 4 (14) -> down 3 (17) -> down 2 (19) -> tie up 1 vs down 1: both (21)
    assert pf.value_area(vol, poc) == (0, 5)


def test_value_area_edge_keeps_adding_the_other_side():
    vol = np.array([10, 1, 1, 1, 1, 1, 1, 1, 1, 1], dtype=float)   # POC at the bottom edge; 70% of 19 = 13.3
    assert pf.value_area(vol, 0) == (0, 4)                          # 10 + 1 + 1 + 1 + 1 = 14, all from above


def test_va_depth_midpoint_of_vah_zone_is_half():
    s = pf.ProfileStats(0, low=90.0, high=110.0, poc=100.0, val=95.0, vah=104.0, lvns=(), volume=1.0)
    f = pf.va_features(s, 107.0, direction=-1)
    assert f["in_vah_zone"] and f["va_depth"] == approx(0.5) and f["va_direction_fit"] is True
    assert f["dist_vah"] == 3.0 and f["dist_val"] == 12.0
    g = pf.va_features(s, 92.5, direction=-1)
    assert g["in_val_zone"] and g["va_depth"] == approx(0.5) and g["va_direction_fit"] is False
    assert pf.va_features(s, 100.0)["va_depth"] is None and pf.va_features(None, 1.0)["in_vah_zone"] is None


# --- F3.4 LVNs ------------------------------------------------------------------------------------------------

def test_lvn_two_humps_valley_30_pct_vs_35_pct():
    """The qualifying rule on a smoothed profile: valley at 30% of the smaller peak -> one LVN; at 35% -> none."""
    base = np.array([1, 5, 10, 5, 0, 0, 0, 4, 8, 4, 1], dtype=float)
    s30 = base.copy(); s30[4:7] = [3.0, 2.4, 3.0]               # 2.4 = 30% of the smaller peak 8
    s35 = base.copy(); s35[4:7] = [3.0, 2.8, 3.0]               # 35%
    assert pf.lvn_indices(s30, 0.3) == [5]
    assert pf.lvn_indices(s35, 0.3) == []


def test_lvn_iteration_drops_then_recomputes_peaks():
    # two minima: 2 (index 3) and 6 (index 6). Index 6's left peak is 8 (between it and index 3), right peak 9:
    # 6 > 0.3 x 8, dropped. Then index 3's right peak becomes max over (3, edge) = 9; 2 <= 0.3 x min(10, 9) -> keeps.
    s = np.array([1, 10, 5, 2, 5, 8, 6, 9, 3], dtype=float)
    assert pf.lvn_indices(s, 0.3) == [3]


def test_gaussian_kernel_truncation_and_identity():
    k = pf.gaussian_kernel(8, 3)
    assert len(k) == 25 and k.sum() == approx(1.0) and k[12] == k.max()      # σ = 4 ticks, ±12 ticks
    vol = np.zeros(200); vol[:80] = 100; vol[120:] = 100                     # a gap of 40 ticks (10 pts)
    s = pf.stats(vol, pf.to_tick(20000.0).item(), 0, {"lvn_smooth_ticks": 8, "lvn_gauss_trunc_sigma": 3,
                                                       "lvn_max_pct": 0.3, "lvn_merge_pts": 5})
    assert len(s.lvns) == 1
    ident, price = s.lvns[0]
    assert ident % 5 == 0 and abs(price - ident) <= 2.5 and 20020.0 <= price <= 20030.0
