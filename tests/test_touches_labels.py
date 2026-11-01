"""F5.4 per-member invalidation, F5.6 touch and re-arm, F5.7 ids, F5.10 groups, F11.4 structure label (step 11 part 3)."""

from __future__ import annotations

from datetime import date

import numpy as np

from nqbot.features import level_state as ls
from nqbot.features import registry as rg
from nqbot.features import structure_label as slab
from nqbot.features import touches as tch
from nqbot.features.bands import StepBand
from nqbot.features.node_levels import _track
from nqbot.features.session_tape import MIN_NS, NS, Tape, bars, et_ns
from nqbot.features.zones import ZoneBook

DAY = date(2026, 3, 20)
P = ls.Params(rearm_dist=10, flip_margin=0, flip_bar_minutes=5, sweep_max_depth=15, sweep_reclaim_bars=3)


def at(h, m, s=0.0):
    from datetime import time
    return et_ns(DAY, time(h, m)) + int(s * NS)


def tape_of(points):
    ts, px = zip(*points)
    return Tape.from_arrays(DAY, np.array(ts, dtype=np.int64), np.array(px, float))


def level(tape, uid, kind, lo, hi, role=None, start=None):
    start = int(tape.ts[0]) if start is None else start
    band = StepBand.const(tape, start, lo, hi)
    st = ls.LevelState(uid, kind, tape, band, P, bars(tape, 1), bars(tape, 5), initial_role=role,
                       end_ns=int(tape.ts[-1]) + MIN_NS).run()
    rtype = rg.GEX_NODE if kind == ls.NODE else "session_hl"
    return rg.RegLevel(uid, uid, uid, "x", rtype, "NQ", kind, False, band, st, _track(st),
                       spans=rg.active_spans(st, band, int(tape.ts[-1]) + MIN_NS))


def zone_touches(tape, levels, prox=2.0, rearm=10.0):
    end = int(tape.ts[-1]) + MIN_NS
    book = ZoneBook([x.book_level() for x in levels], tape, lambda t: (10.0, 60.0), end_ns=end).run()
    zt = tch.ZoneTouches(tape, book, {x.uid: x for x in levels}, prox, rearm).run()
    return book, zt


def test_spec_touch_and_rearm_path():
    # F5.6: zone [100, 102], prox 2, rearm 10: 120 (armed from above) -> 104 (touch) -> 110 -> 115 (re-armed) ->
    # 103.5 (second touch)
    t0 = at(10, 0)
    tape = tape_of([(t0 + k * NS, p) for k, p in enumerate([120, 104, 110, 115, 103.5])])
    _, zt = zone_touches(tape, [level(tape, "L", ls.CHART, 100, 102)])
    assert [(x.idx, x.side) for x in zt.touches] == [(1, 1), (4, 1)]
    assert zt.touches[0].role == ls.FLOOR and zt.touches[0].direction == 1


def test_zone_moving_onto_price_is_not_a_touch():
    t0 = at(10, 0)
    tape = tape_of([(t0 + k * NS, 105.0) for k in range(10)])
    band = StepBand(tape, np.array([tape.ts[0], tape.ts[5]], dtype=np.int64), np.array([200.0, 104.0]),
                    np.array([202.0, 106.0]))
    st = ls.LevelState("M", ls.CHART, tape, band, P, bars(tape, 1), bars(tape, 5), end_ns=int(tape.ts[-1]) + 1).run()
    lv = rg.RegLevel("M", "M", "M", "x", "vwap", "NQ", ls.CHART, False, band, st, _track(st),
                     spans=rg.active_spans(st, band, int(tape.ts[-1]) + 1))
    _, zt = zone_touches(tape, [lv])
    assert zt.touches == [] and [w for _, _, w in zt.disarms] == ["onto"]


def test_unresolved_sweep_blocks_far_side_rearm():
    # a PDH ceiling [100, 100.5] touched from below; price runs to 113 (beyond the re-arm band, but inside the sweep
    # window and within sweep_max_depth): no re-arm above; back to 99 = the same event; after the sweep resolves,
    # a move to 80 re-arms below and 99 is a new touch
    pts = [(at(10, 0, 0), 85), (at(10, 0, 10), 99), (at(10, 0, 20), 105), (at(10, 0, 30), 113),
           (at(10, 0, 40), 99), (at(10, 0, 50), 99.5), (at(10, 1, 30), 99), (at(10, 6, 0), 80), (at(10, 6, 10), 99)]
    tape = tape_of(pts)
    pdh = level(tape, "PDH", ls.SWEEP, 100, 100.5, role=ls.CEILING)
    _, zt = zone_touches(tape, [pdh])
    assert [x.idx for x in zt.touches] == [1, 8]
    assert all(x.side == -1 for x in zt.touches)


def test_event_ids_and_order_ids():
    eid = tch.event_id(at(10, 52, 0.4), "z17")
    assert eid.endswith("-z17") and len(eid.split("-")[0]) == len("20251119T155200Z")
    a = tch.order_id(eid, "b", 1, "mffur1", "NQ")
    b = tch.order_id(eid, "b", 2, "mffur1", "NQ")
    assert a != b and a.startswith(eid) and a.endswith("-b1-mffur1-NQ")


def test_touch_groups_golden_and_level():
    # one trade touches a shallow golden zone and a POC zone inside it -> 2 events, 1 group
    a = tch.Touch("g1", 100, 5, 20_045.0, 1, (), 20_038.2, 20_050.0, kind="golden", part="shallow")
    b = tch.Touch("z3", 100, 5, 20_045.0, 1, ("poc",), 20_040.0, 20_048.0)
    out = tch.assign_groups([a, b])
    assert len({x.group for x in out}) == 1
    # a shallow touch at 0.55 and a deep touch at 0.64 (different trades) -> 2 groups
    c = tch.Touch("g1", 100, 5, 20_045.0, 1, (), 20_038.2, 20_050.0, kind="golden", part="shallow")
    d = tch.Touch("g2", 200, 9, 20_036.0, 1, (), 20_021.0, 20_038.2, kind="golden", part="deep")
    assert len({x.group for x in tch.assign_groups([c, d])}) == 2
    # one trade gapping through both: the deep touch still starts its own group, a zone inside it joins it
    e = tch.Touch("g1", 300, 12, 20_030.0, 1, (), 20_038.2, 20_050.0, kind="golden", part="shallow")
    f = tch.Touch("g2", 300, 12, 20_030.0, 1, (), 20_021.0, 20_038.2, kind="golden", part="deep")
    z = tch.Touch("z9", 300, 12, 20_030.0, 1, ("lvn",), 20_028.0, 20_032.0)
    out = tch.assign_groups([e, f, z])
    gs = {x.zone_id: x.group for x in out}
    assert gs["g1"] != gs["g2"] and gs["z9"] == gs["g2"]


def _label_case(points, lo=100.0, hi=102.0, kind=ls.CHART, role=ls.CEILING):
    tape = tape_of(points)
    lv = level(tape, "L", kind, lo, hi, role=role)
    book, zt = zone_touches(tape, [lv])
    e = zt.touches[0]
    e.event_id = tch.event_id(e.t, e.zone_id)
    t_end = slab.window_end(e.t, 30, at(15, 59), at(17, 0))
    cross = slab.zone_cross(book, {lv.uid: lv}, e.zone_id, e.role, e.t, t_end)
    return slab.label(e, tape, cross, t_end, 10.0), e, book


def test_label_held_needs_a_trade_through():
    base = [(at(10, 0, 0), 85), (at(10, 0, 10), 101), (at(10, 0, 20), 103)]
    lab, e, _ = _label_case(base + [(at(10, 0, 30), 93.0), (at(10, 0, 40), 92.75)])
    assert e.role == ls.CEILING and lab.label == slab.HELD and lab.t_resolved == at(10, 0, 40)
    lab, _, _ = _label_case(base + [(at(10, 0, 30), 93.0)])
    assert lab.label == slab.TIMEOUT and lab.how == "window"


def test_label_broke_on_a_settled_candle():
    pts = [(at(10, 0, 0), 85), (at(10, 0, 10), 101), (at(10, 1, 0), 102.5), (at(10, 1, 30), 103),
           (at(10, 1, 59), 103), (at(10, 2, 10), 102.5)]
    lab, _, _ = _label_case(pts)
    assert lab.label == slab.BROKE and lab.t_resolved == at(10, 2, 0)


def test_label_timeout_window():
    pts = [(at(10, 0, 0), 85), (at(10, 0, 10), 101)] + [(at(10, 0, 10) + k * 60 * NS, 99.0) for k in range(1, 40)]
    lab, e, _ = _label_case(pts)
    assert lab.label == slab.TIMEOUT and lab.t_resolved == e.t + 30 * MIN_NS


def test_label_swept_pdh_then_hold():
    # a PDH swept to 105 and reclaimed, then a trade at 94.75 -> held (extreme 105, 105 - 10 - 0.25)
    pts = [(at(10, 0, 0), 85), (at(10, 0, 10), 100), (at(10, 0, 20), 105), (at(10, 0, 50), 99.5),
           (at(10, 1, 10), 99.0), (at(10, 2, 5), 95.0), (at(10, 2, 20), 94.75)]
    lab, _, _ = _label_case(pts, 100.0, 100.5, kind=ls.SWEEP)
    assert lab.label == slab.HELD and lab.extreme == 105


def test_zone_node_crossed_pdh_reclaimed_zone_not_crossed():
    # F5.4: zone {QQQ node, PDH}; price trades past the node band (the node is crossed) but is reclaimed within the PDH
    # sweep window -> the zone is not crossed, and the node is no longer an active member
    pts = [(at(10, 0, 0), 85), (at(10, 0, 10), 100), (at(10, 0, 20), 105), (at(10, 0, 50), 101),
           (at(10, 1, 10), 101.5), (at(10, 2, 30), 101.0), (at(10, 10, 0), 101.0)]
    tape = tape_of(pts)
    node = level(tape, "N", ls.NODE, 98, 104, role=ls.CEILING)
    pdh = level(tape, "PDH", ls.SWEEP, 100.5, 102.5, role=ls.CEILING)
    book, zt = zone_touches(tape, [node, pdh])
    e = zt.touches[0]
    assert set(e.members) == {"N", "PDH"}
    t_end = slab.window_end(e.t, 30, at(15, 59), at(17, 0))
    assert slab.zone_cross(book, {"N": node, "PDH": pdh}, e.zone_id, e.role, e.t, t_end) is None
    assert book.members(book.follow(e.zone_id, at(10, 5)), at(10, 5)) == ("PDH",)


def test_single_print_judged_against_the_band_frozen_at_the_touch():
    # F3.5: a single print [100, 110] touched from below (ceiling); its upper part fills, the live band shrinks to
    # [100, 104]; a bar settling at 105-106 is beyond the live band but not the frozen one -> not broke
    pts = [(at(10, 0, 0), 85), (at(10, 0, 10), 99), (at(10, 2, 0), 105), (at(10, 2, 30), 106), (at(10, 2, 59), 106),
           (at(10, 3, 30), 100)]
    tape = tape_of(pts)
    band = StepBand(tape, np.array([tape.ts[0], at(10, 1)], dtype=np.int64), np.array([100.0, 100.0]),
                    np.array([110.0, 104.0]))
    st = ls.LevelState("SP", ls.SINGLE_PRINT, tape, band, P, bars(tape, 1), bars(tape, 5), initial_role=ls.CEILING,
                       end_ns=int(tape.ts[-1]) + MIN_NS).run()
    lv = rg.RegLevel("SP", "SP", "single_print", "single_print", "volume_profile", "NQ", ls.SINGLE_PRINT, True, band,
                     st, _track(st), spans=rg.active_spans(st, band, int(tape.ts[-1]) + MIN_NS))
    book, zt = zone_touches(tape, [lv])
    e = zt.touches[0]
    t_end = slab.window_end(e.t, 30, at(15, 59), at(17, 0))
    live = slab.zone_cross(book, {"SP": lv}, e.zone_id, e.role, e.t, t_end)
    frozen = {"SP": slab.frozen_single_print(lv, bars(tape, 1), e.role, e.t, t_end)}
    assert live is not None and live[1] == slab.BROKE           # the live band would call it broke
    assert slab.zone_cross(book, {"SP": lv}, e.zone_id, e.role, e.t, t_end, frozen) is None
