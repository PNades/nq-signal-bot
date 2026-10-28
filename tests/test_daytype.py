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


def test_config_cross_bar_is_5_minutes_and_thresholds_stay_2():
    f = load_features()
    assert f["daytype_cross_bar_minutes"] == 5
    assert (f["trend_max_vwap_crosses"], f["trend_max_poc_crosses"]) == (2, 2)
    assert "vwap_crosses" in f["daytype_inputs"]
