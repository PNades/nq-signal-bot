"""F3.6 IB high/low and F10 on a session tape, with features/golden_zone.py wired in unchanged."""

from datetime import date, time

import numpy as np
import pytest
from pytest import approx

from nqbot.config import load_features
from nqbot.features import golden_zone as gz
from nqbot.features import opening_range as orr
from nqbot.features import session_tape as st

DAY = date(2026, 3, 6)
NS = st.NS
O = st.rth_open_ns(DAY)


def m(minute, sec=0):
    return O + int(minute * 60 * NS) + int(sec * NS)


def build(rows, **over):
    f = {**load_features(), **over}
    rows = sorted(rows)
    tape = st.Tape.from_arrays(DAY, [r[0] for r in rows], [r[1] for r in rows])
    return orr.OpeningRange.build(tape, st.bars(tape, 1), f), tape


def test_up_leg_at_0945_zone():
    o, _ = build([(m(5), 20_000), (m(15), 20_200), (m(20), 20_150)])
    d = o.drawing_at(m(15))
    assert d.orientation == gz.LONG and d.zone(0.79) == (approx(20_042), approx(20_100))
    assert o.drawing_at(m(14, 59)) is None


def test_ib_levels_and_the_1030_move():
    rows = [(m(2), 20_000), (m(28), 20_200), (m(30, 5), 20_210), (m(40), 20_100), (m(65), 20_100)]
    o, tape = build(rows)
    ib = {lv.side: lv for lv in o.ib_at(m(30))}
    assert ib["high"].price == 20_200 and ib["low"].price == 20_000 and ib["high"].created_ns == m(30)
    assert o.ib_at(m(29, 59)) == []
    ib = {lv.side: lv for lv in o.ib_at(m(60))}
    assert ib["high"].price == 20_210 and ib["high"].level_id.endswith("-1030") and ib["low"].level_id == f"ib_low-{DAY}"
    f = o.features_at(m(59), tape, on_high=20_150, on_low=19_900)
    assert f["fib_redrawn_1030"] is None and f["range_pts_1000"] == 200 and f["left_ib_since_lock"] is True
    assert f["range_broke_on"] is True and f["minutes_since_lock"] == 29
    f = o.features_at(m(60), tape, None, None)
    assert f["fib_redrawn_1030"] is True and f["range_pts_1030"] == 210 and f["fib_drawing"] == gz.REDRAWN


def test_redraw_off_keeps_the_1000_ib_all_day():
    rows = [(m(2), 20_000), (m(28), 20_200), (m(30, 5), 20_210), (m(65), 20_100)]
    o, tape = build(rows, golden_zone_redraw_1030=False)
    ib = {lv.side: lv.price for lv in o.ib_at(m(70))}
    assert ib == {"high": 20_200, "low": 20_000}
    f = o.features_at(m(60), tape, None, None)
    assert f["fib_redrawn_1030"] is False and f["range_pts_1030"] == 200


def test_flips_and_minutes_since_flip():
    # owner's example: L 20,000 first, H 20,100 -> long zone [20,021, 20,050]; a bar settling below 20,021 flips
    rows = [(m(2), 20_000), (m(10), 20_100), (m(35), 20_020), (m(35, 50), 20_015), (m(36), 20_015)]
    o, tape = build(rows)
    f = o.features_at(m(36, 1), tape, None, None)
    assert f["fib_orientation"] == gz.SHORT and f["fib_flips"] == 1 and f["minutes_since_flip"] == approx(1 / 60)
    assert o.features_at(m(35, 59), tape, None, None)["fib_flips"] == 0


def test_dedupe_gives_the_same_output_as_feeding_every_trade():
    rng = np.random.default_rng(3)
    secs = np.sort(rng.uniform(0, 120 * 60, 4000))
    steps = rng.choice([-0.25, 0.0, 0.0, 0.25], size=4000)
    prices = 20_000 + np.cumsum(steps) * 8
    rows = [(O + int(s * NS), float(p)) for s, p in zip(secs, prices)]
    f = load_features()
    tape = st.Tape.from_arrays(DAY, [r[0] for r in rows], [r[1] for r in rows])
    a = orr.OpeningRange.build(tape, st.bars(tape, 1), f, dedupe=True)
    b = orr.OpeningRange.build(tape, st.bars(tape, 1), f, dedupe=False)
    assert a.touches == b.touches and a.breaks == b.breaks and a.redraws == b.redraws
    assert a.timeline_t == b.timeline_t and a.timeline == b.timeline and a.fed < b.fed
    assert len(a.touches) > 0


def test_fib_level_reached():
    rows = [(m(2), 20_000), (m(10), 20_100), (m(40), 20_060), (m(41), 20_035)]   # long: 0.5 at 20,050, 0.618 at 20,038.2
    o, tape = build(rows)
    assert o.fib_level_reached(m(39), m(40), tape) is None
    assert o.fib_level_reached(m(39), m(41), tape) == 0.618
