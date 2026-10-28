"""Day-type crosses on settled 5-minute bars (owner 2026-10-27, SPEC §13 Q-dtcross, F3.10, F11.6)."""

from nqbot.config import load_features
from nqbot.features import daytype as dt

VWAP = 20_000.0


def test_a_trend_day_pullback_with_three_1m_closes_below_vwap_is_no_cross():
    """Owner test: three 1-minute closes below VWAP, but no settled 5-minute bar below -> 0 crosses (the old rule
    counts 2: down, then back up)."""
    one_minute = [20_020, 20_010, 19_995, 19_990, 19_998, 20_005, 20_015, 20_030]
    assert dt.crosses_1m_closes([(c, VWAP) for c in one_minute]) == 2
    bars = [dt.LevelBar(20_015, 20_020, VWAP, VWAP),   # settled above
            dt.LevelBar(20_018, 19_990, VWAP, VWAP),   # opens above, closes below: not settled
            dt.LevelBar(19_992, 20_015, VWAP, VWAP),   # opens below, closes above: not settled
            dt.LevelBar(20_016, 20_030, VWAP, VWAP)]   # settled above
    assert dt.crosses(bars) == 0


def test_a_chop_day_with_settled_bars_on_alternating_sides_is_counted():
    bars = [dt.LevelBar(20_010, 20_012, VWAP, VWAP),   # above (sets the side)
            dt.LevelBar(19_995, 19_990, VWAP, VWAP),   # below: 1
            dt.LevelBar(20_004, 20_008, VWAP, VWAP),   # above: 2
            dt.LevelBar(20_001, 19_999, VWAP, VWAP),   # straddles: no change
            dt.LevelBar(19_996, 19_994, VWAP, VWAP)]   # below: 3
    assert dt.crosses(bars) == 3


def test_the_level_is_judged_as_of_the_bar_open_and_close():
    # open 20,001 vs VWAP 20,000 at the open; close 19,999 vs VWAP 19,998 at the close: settled above
    assert dt.settled_side(dt.LevelBar(20_001, 19_999, 20_000, 19_998)) == dt.ABOVE
    # the same prices with VWAP 20,000 at the close: the close is below, not settled
    assert dt.settled_side(dt.LevelBar(20_001, 19_999, 20_000, 20_000)) is None
    # on the level is not beyond (margin 0); no level yet is no side
    assert dt.settled_side(dt.LevelBar(20_000, 20_005, 20_000, 20_000)) is None
    assert dt.settled_side(dt.LevelBar(20_001, 20_005, None, 20_000)) is None


def label(vwap_crosses, close, new_highs=7, new_lows=2, **kw):
    """RTH range 20,000-20,100; the default counts make an up day (new_extreme_skew 5, new highs dominate)."""
    f = load_features()
    return dt.eod_label(vwap_crosses, close, 20_100.0, 20_000.0, new_highs, new_lows, f["trend_max_vwap_crosses"],
                        f["trend_close_pct"], **kw)


def test_eod_label_ignores_poc_crosses():
    """Owner 2026-10-27 (Q-dtcross follow-up): 1 VWAP cross, 3 POC crosses, close in the top 15% on an up day -> trend;
    with the former POC condition (the step-13 comparison only) -> range."""
    assert label(1, 20_086) == dt.TREND
    assert label(1, 20_086, poc_crosses=3, max_poc_crosses=dt.REPORT_MAX_POC_CROSSES) == dt.RANGE
    assert label(1, 20_086, poc_crosses=2, max_poc_crosses=dt.REPORT_MAX_POC_CROSSES) == dt.TREND


def test_eod_label_vwap_crosses_and_close():
    assert label(3, 20_095) == dt.RANGE              # 3 VWAP crosses
    assert label(0, 20_050) == dt.RANGE              # close in the middle of the range
    assert label(2, 20_008, new_highs=1, new_lows=6) == dt.TREND  # down day closing in the bottom 10%


def test_trend_side_is_the_sign_of_new_highs_minus_new_lows():
    """Owner 2026-10-27 (Q-dtside): positive -> up, negative -> down, zero -> no side (range)."""
    assert (dt.trend_side(7, 2), dt.trend_side(2, 7), dt.trend_side(4, 4)) == (dt.UP, dt.DOWN, None)
    assert label(0, 20_095, new_highs=4, new_lows=4) == dt.RANGE


def test_a_v_reversal_day_closing_high_is_range():
    """Owner test: the morning's new lows dominate (2 new highs, 9 new lows), then price rallies and closes in the top
    20% of the range -> the side is down, the close isn't in the bottom 20% -> range."""
    assert label(1, 20_090, new_highs=2, new_lows=9) == dt.RANGE


def test_a_steady_up_day_is_trend_up():
    """Owner test: new highs all day (10 new highs, 1 new low at the open), 1 VWAP cross, close in the top 20%."""
    assert dt.trend_side(10, 1) == dt.UP
    assert label(1, 20_092, new_highs=10, new_lows=1) == dt.TREND


def test_config_cross_bar_is_5_minutes_and_no_poc_limit():
    f = load_features()
    assert f["daytype_cross_bar_minutes"] == 5
    assert f["trend_max_vwap_crosses"] == 2 and f["trend_close_pct"] == 0.20
    assert "trend_max_poc_crosses" not in f
    assert "vwap_crosses" in f["daytype_inputs"]
