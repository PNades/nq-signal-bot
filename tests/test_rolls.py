"""F2.11 ceilings / floors by Q6 lead time and Q9 price switches (owner 2026-10-25): hand-built boards at W = 60.

Prices in QQQ units with R = 1 for readability (the code takes bands already mapped to futures points); bands +-0.5.
"""

from nqbot.features.node_switch import ConfirmedNode
from nqbot.features.rolls import RollSide

B = 0.5


def side(sign=+1, w=60.0):
    return RollSide(sign=sign, switch=ConfirmedNode(window_sec=w, incumbent_max_share=0.25, gap_reset_sec=10.0))


def bands(values):
    return {k: (k - B, k + B) for k in values}


def board(s, t, spot, values, one_minute=False):
    return s.on_board(t, spot, values, bands(values), one_minute)


def kinds(s):
    return [(e.t, e.kind, e.old, e.new) for e in s.events]


def rolled_down():
    """Spot 755; ceiling 760 (2.0e6) and 759 (1.0e6); from t = 1, 760 -> 0.8e6 and 759 -> 1.5e6 (F2.11 test)."""
    s = side()
    board(s, 0, 755.0, {758: 0.2e6, 759: 1.0e6, 760: 2.0e6, 765: 0.5e6})
    for t in range(1, 100):
        board(s, t, 755.0, {758: 0.2e6, 759: 1.5e6, 760: 0.8e6, 765: 0.5e6})
    return s


def test_roll_down_after_the_window_with_shrink_and_growth_from_the_last_board_760_led():
    s = rolled_down()
    assert kinds(s) == [(60, "roll", 760, 759)]  # no switch before W of board time has passed
    e = s.events[0]
    assert abs(e.old_shrink_pct - 0.6) < 1e-12 and abs(e.new_growth_pct - 0.5) < 1e-12
    assert s.rolling and s.roll_steps() == 1 and s.anchor == 760 and s.rolls_today == 1


def test_price_inside_the_band_with_board_spot_above_the_strike_keeps_the_ceiling():
    s = rolled_down()
    for t in range(100, 300):  # QQQ spot 759.2 is above 759, NQ trades 759.3 inside [758.5, 759.5]
        board(s, t, 759.2, {758: 0.2e6, 759: 1.5e6, 760: 0.8e6, 765: 0.5e6})
        s.on_trade(t + 0.5, 759.3)
    assert s.confirmed == 759 and s.rolling and len(s.events) == 1


def test_trade_beyond_the_upper_edge_pauses_and_rechooses_at_once_then_resume_rechooses():
    s = rolled_down()
    vals = {758: 0.2e6, 759: 1.5e6, 760: 0.8e6, 765: 0.5e6}
    board(s, 100, 759.8, vals)
    s.on_trade(100.5, 759.6)  # beyond 759's upper edge 759.5
    assert kinds(s)[1:] == [(100.5, "pause", 759, None), (100.5, "price_switch", 759, 760)]
    assert s.paused and not s.rolling and s.break_node == 759 and s.rolls_today == 1 and s.roll_breaks_today == 1
    assert s.roll_steps() == 0
    s.on_trade(101, 760.6)  # through 760 too: 765 is the ceiling, above the anchor -> steps -1 (Q8)
    assert s.confirmed == 765 and s.roll_steps() == -1 and s.paused
    for t in range(102, 200):
        board(s, t, 761.0, vals)  # 759 and 760 excluded: no contest from them while price is above
    assert s.confirmed == 765 and len(s.events) == 4
    board(s, 200, 758.2, vals)
    s.on_trade(200.5, 758.4)  # below the break node's lower edge 758.5: resume, re-chosen at once
    assert ("resume" in [e.kind for e in s.events]) and s.confirmed == 759 and s.rolling and s.roll_steps() == 1
    assert s.events[-1].kind == "price_switch" and s.rolls_today == 1


def test_crossed_floor_above_price_largest_for_a_window_is_a_price_switch_not_a_roll():
    s = side()
    vals = {755: 2.0e6, 765: 1.0e6}
    for t in range(-200, 0):
        board(s, t, 757.0, vals)  # ceiling 765; 755 is below spot (the floor side's)
    assert s.confirmed == 765
    for t in range(0, 120):
        board(s, t, 754.0, vals)  # price fell through 755: now above spot and the largest there
    assert kinds(s) == [(46, "price_switch", 765, 755)]
    assert s.rolls_today == 0 and not s.episode


def test_ceiling_tug_of_war_never_rolls_and_59_1_split_rolls_down():
    s = side()
    for t in range(0, 601):
        a = 2.0e6 if (t // 30) % 2 == 0 else 1.0e6
        board(s, t, 755.0, {759: 3.0e6 - a, 760: a})
    assert s.events == [] and s.confirmed == 760
    s = side()
    for t in range(0, 200):
        board(s, t, 755.0, {759: 1.0e6, 760: 2.0e6} if t % 60 == 0 else {759: 2.0e6, 760: 1.0e6})
    assert kinds(s) == [(61, "roll", 760, 759)]


def test_partial_and_full_roll_back():
    s = side()
    board(s, 0, 755.0, {758: 1, 759: 1, 760: 9})
    for t in range(1, 100):
        board(s, t, 755.0, {758: 9, 759: 1, 760: 1})  # roll 760 -> 758 at 60
    for t in range(100, 200):
        board(s, t, 755.0, {758: 1, 759: 9, 760: 1})  # 759 leads: a partial roll-back at 146 (758 led 14 s of the last 60)
    for t in range(200, 300):
        board(s, t, 755.0, {758: 1, 759: 1, 760: 9})  # back to the anchor: full roll-back at 246
    assert [(t, k) for t, k, *_ in kinds(s)] == [(60, "roll"), (146, "partial_rollback"), (246, "rollback")]
    assert not s.episode and s.rollbacks_today == 1 and s.partial_rollbacks_today == 1


def test_floor_mirror_roll_up_and_pause():
    s = side(sign=-1)
    board(s, 0, 755.0, {750: 2.0e6, 751: 1.0e6})
    for t in range(1, 100):
        board(s, t, 755.0, {750: 0.8e6, 751: 1.5e6})
    assert kinds(s) == [(60, "roll", 750, 751)] and s.roll_steps() == 1
    s.on_trade(100, 750.4)  # below 751's lower edge 750.5
    assert s.paused and s.break_node == 751 and s.confirmed == 750


def test_dislocation_block_waits_and_judges_at_the_block_end():
    s = rolled_down()
    s.on_trade(100, 759.6, blocked=True)
    assert s.rolling and s.confirmed == 759
    s.block_end(130, 759.7)
    assert s.paused and s.break_node == 759


def test_one_minute_day_classifies_with_a_60s_window():
    s = side(w=30.0)
    for i, vals in enumerate([{759: 1, 760: 9}, {759: 9, 760: 1}, {759: 9, 760: 1}]):
        board(s, 60.0 * i, 755.0, vals, one_minute=True)
    assert kinds(s) == [(120.0, "roll", 760, 759)]
