"""Step-13 variants va_edge_sweep and prev_profile_anchor (owner 2026-10-26, SPEC §13 Q-vaedge; F3.2b, F3.9)."""

from datetime import date, datetime, time

import pytest

from nqbot.config import load_features
from nqbot.features import prev_profile as pp
from nqbot.features import sweep as sw

M = 60.0
# prev VAH 100 held as a ceiling, band ± 4 (the va_edge start half-width): [96, 104]
LO, HI = 96.0, 104.0
BARS = [sw.Bar(0 * M, 103.0, 104.0, 102.5, 103.5),   # inside
        sw.Bar(1 * M, 104.5, 106.0, 104.25, 105.0),  # settles above the band (open and close beyond)
        sw.Bar(2 * M, 105.0, 105.5, 102.0, 103.0)]   # closes back inside within the sweep window


def test_config_switches_start_at_the_current_rules():
    f = load_features()
    assert f["va_edge_sweep"] is False and f["prev_profile_anchor"] == pp.GLOBEX
    assert f["tune_grids_13"]["vaedge_variants"] == {"va_edge_sweep": [False, True],
                                                     "prev_profile_anchor": [pp.GLOBEX, pp.RTH]}


def test_va_edge_is_sweep_eligible_only_with_the_switch_on():
    assert not sw.sweep_eligible("va_edge", {"va_edge_sweep": False})
    assert sw.sweep_eligible("va_edge", {"va_edge_sweep": True})
    assert sw.sweep_eligible("pdh_pdl", {"va_edge_sweep": False})
    assert not sw.sweep_eligible("poc", {"va_edge_sweep": True})  # prev POC stays a chart level


def test_sweep_on_a_settle_above_prev_vah_then_a_close_back_inside_is_swept_not_crossed():
    eligible = sw.sweep_eligible("va_edge", {"va_edge_sweep": True})
    out = sw.judge(BARS, LO, HI, sw.CEILING, eligible, max_depth=15, reclaim_bars=3)
    assert out == sw.Outcome("swept", 3 * M, 2.0)


def test_sweep_off_the_same_settle_crosses_the_chart_level():
    eligible = sw.sweep_eligible("va_edge", {"va_edge_sweep": False})
    assert sw.judge(BARS, LO, HI, sw.CEILING, eligible, max_depth=15, reclaim_bars=3) == sw.Outcome("crossed", 2 * M)


def test_sweep_on_too_deep_or_no_reclaim_in_the_window_breaks():
    deep = BARS[:1] + [sw.Bar(1 * M, 104.5, 120.0, 104.25, 105.0)]
    assert sw.judge(deep, LO, HI, sw.CEILING, True, max_depth=15, reclaim_bars=3) == sw.Outcome("crossed", 1 * M)
    stay = BARS[:2] + [sw.Bar(i * M, 105.0, 106.0, 104.5, 105.0) for i in range(2, 5)]
    assert sw.judge(stay, LO, HI, sw.CEILING, True, max_depth=15, reclaim_bars=3) == sw.Outcome("crossed", 5 * M)
    assert sw.judge(stay[:4], LO, HI, sw.CEILING, True, max_depth=15, reclaim_bars=3) is None  # window still open


def et(d, hh, mm):
    return datetime.combine(d, time(hh, mm), pp.ET)


def test_rth_anchor_ignores_trades_outside_0930_1600_of_the_prior_day():
    prev = date(2026, 3, 5)
    trades = [(et(date(2026, 3, 4), 20, 0), 101.0, 50),  # overnight (Globex day only)
              (et(prev, 8, 0), 101.0, 30),
              (et(prev, 10, 0), 100.0, 40),
              (et(prev, 15, 59), 100.25, 10),
              (et(prev, 16, 0), 101.0, 100),             # after the RTH close
              (et(prev, 16, 30), 101.0, 100)]
    rth = pp.volume_by_price(trades, pp.profile_window(prev, pp.RTH))
    assert rth == {100.0: 40, 100.25: 10}
    assert max(rth, key=rth.get) == 100.0  # RTH POC
    glx = pp.volume_by_price(trades, pp.profile_window(prev, pp.GLOBEX))
    assert glx[101.0] == 280 and max(glx, key=glx.get) == 101.0  # the Globex day keeps them


def test_windows_and_early_close():
    prev = date(2026, 3, 9)  # a Monday: its CME day starts Sunday 18:00
    assert pp.profile_window(prev, pp.GLOBEX) == (et(date(2026, 3, 8), 18, 0), et(prev, 17, 0))
    assert pp.profile_window(prev, pp.RTH, early_close=time(13, 0)) == (et(prev, 9, 30), et(prev, 13, 0))
    with pytest.raises(ValueError):
        pp.profile_window(prev, "rth")
