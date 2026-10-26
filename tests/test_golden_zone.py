"""F10 golden zone and the step-13 variant switches (owner 2026-10-16 / 2026-10-26, SPEC §5.8, §13 Q-fibvariants)."""

from pytest import approx

from nqbot.config import load_features
from nqbot.features import golden_zone as gz

M = 60.0  # seconds per minute; times are seconds after 09:30:00


def fib(flip=True, redraw=True):
    return gz.GoldenFib(start_minute=15, lock_minute=30, redraw_minute=60, upper_fib=0.79, flip=flip, redraw_1030=redraw)


def owner_range(g):
    """Low 20,000 first (09:32), then high 20,100 (09:40): long zone [20,021, 20,050] (the owner's flip example)."""
    g.on_trade(2 * M, 20_000)
    g.on_trade(10 * M, 20_100)
    g.advance(31 * M)  # past the 10:00 lock


def bar(g, minute, o, c):
    return g.on_bar(minute * M, (minute + 1) * M, o, c)


def test_up_leg_drawn_at_0945_and_new_extreme_redraws_before_the_lock():
    g = fib()
    g.on_trade(5 * M, 20_000)
    g.on_trade(14 * M, 20_200)
    g.advance(15 * M)
    d = g.drawing
    assert (d.orientation, d.source) == (gz.LONG, gz.RUNNING)
    assert d.zone(0.79) == (approx(20_042), approx(20_100))
    g.on_trade(25 * M, 20_300)
    assert g.drawing.high == 20_300 and g.redraws_before_lock == 1 and g.drawing.zone_id == d.zone_id
    g.on_trade(30 * M, 20_400)  # 10:00:00: locked, no redraw
    assert g.drawing.high == 20_300 and g.drawing.source == gz.LOCKED and g.ib_high == 20_300


def test_new_extreme_before_1000_reversing_the_orientation_is_a_new_zone():
    g = fib()
    g.on_trade(1 * M, 20_000)   # 09:31 low
    g.on_trade(10 * M, 20_100)  # 09:40 high
    g.advance(15 * M)
    z0 = g.drawing.zone_id
    assert g.drawing.orientation == gz.LONG
    out = g.on_trade(22 * M, 19_950)  # 09:52 new low: high first now -> short
    d = g.drawing
    assert d.orientation == gz.SHORT and d.zone_id != z0 and d.flips == 0
    assert out == [gz.Redraw(22 * M, z0, d.zone_id, ())]  # the old zone's open event ends, pending entries removed


def test_new_extreme_before_1000_keeping_the_orientation_keeps_the_zone_id():
    g = fib()
    g.on_trade(1 * M, 20_000)
    g.on_trade(10 * M, 20_100)
    g.advance(15 * M)
    z0 = g.drawing.zone_id
    assert g.on_trade(22 * M, 20_150) == []  # 09:52 new high: still low first -> long
    assert (g.drawing.orientation, g.drawing.zone_id, g.drawing.high) == (gz.LONG, z0, 20_150)


def test_flip_on_redraws_the_other_way_and_back():
    g = fib()
    owner_range(g)
    z0 = g.drawing.zone_id
    assert g.drawing.zone(0.79) == (approx(20_021), approx(20_050))
    out = bar(g, 35, 20_019, 20_010)  # 10:05 settles below 20,021
    assert out == [gz.Invalidation(36 * M, z0, True)]
    d = g.drawing
    assert d.orientation == gz.SHORT and d.flips == 1 and d.zone_id != z0
    assert d.zone(0.79) == (approx(20_050), approx(20_079))
    bar(g, 50, 20_085, 20_090)  # settles above 20,079
    assert g.drawing.orientation == gz.LONG and g.drawing.flips == 2
    assert g.drawing.zone(0.79) == (approx(20_021), approx(20_050))


def test_flip_off_a_settle_past_079_ends_the_event_and_the_zone_keeps_its_orientation():
    g = fib(flip=False)
    owner_range(g)
    before = g.drawing
    out = bar(g, 35, 20_019, 20_010)
    assert out == [gz.Invalidation(36 * M, before.zone_id, False)]  # the setup on this zone ends
    assert g.drawing == before  # same orientation (long), same zone_id, same levels, flips 0
    assert g.drawing.zone(0.79) == (approx(20_021), approx(20_050))
    assert bar(g, 50, 20_085, 20_090) == [] and g.drawing == before  # above 20,079 is not past a long zone's edge


def test_redraw_on_a_new_low_after_1000_redraws_at_1030_and_moves_the_ib_low():
    g = fib()
    owner_range(g)
    z0 = g.drawing.zone_id
    g.on_trade(42 * M, 19_950)  # 10:12
    out = g.advance(60 * M)
    assert out == [gz.Redraw(60 * M, z0, g.drawing.zone_id, ("low",))]
    d = g.drawing
    assert (d.low, d.high, d.orientation, d.source, d.flips) == (19_950, 20_100, gz.SHORT, gz.REDRAWN, 0)
    assert (g.ib_low, g.ib_high, g.redrawn_1030) == (19_950, 20_100, True)


def test_redraw_off_a_new_high_between_1000_and_1030_changes_nothing_at_1030():
    g = fib(redraw=False)
    owner_range(g)
    before = g.drawing
    g.on_trade(42 * M, 20_150)  # 10:12, above the 10:00 high
    assert g.advance(60 * M) == []
    assert g.drawing == before and before.source == gz.LOCKED
    assert (g.ib_high, g.ib_low, g.redrawn_1030) == (20_100, 20_000, False)


def test_no_new_extreme_keeps_the_1000_drawing():
    g = fib()
    owner_range(g)
    before = g.drawing
    g.on_trade(42 * M, 20_050)
    assert g.advance(60 * M) == [] and g.drawing == before and g.redrawn_1030 is False


def test_from_config_reads_the_switches():
    g = gz.from_config(load_features())
    assert (g.flip, g.redraw_1030) == (True, True)
    assert (g.t_start, g.t_lock, g.t_redraw, g.upper_fib) == (15 * M, 30 * M, 60 * M, 0.79)
