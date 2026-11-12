"""Step 13-0: the owner's corrections, each with its hand-built case (SPEC §13 Q-step11h, Q-step12a, Q-step12b,
Q-step12 (2)/(4), Q-density (c)/(d), Q-step13-0; feature_spec F2.4, F2.6, F3.2/F3.3, F3.8, F5.3, F5.5, F5.6, F5.11,
F11.4). The 1-pt profile rows are in `tests/test_chart_profile.py`."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import numpy as np
import pytest

from nqbot.config import load_features
from nqbot.features import bands as bd
from nqbot.features import diagnostics as dg
from nqbot.features import freshness as fr
from nqbot.features import level_state as ls
from nqbot.features import node_rank as nr
from nqbot.features import nodes
from nqbot.features import registry as rg
from nqbot.features import session_features as sfm
from nqbot.features import session_levels as sl
from nqbot.features import structure_label as slab
from nqbot.features import touches as tch
from nqbot.features import zones as zn
from nqbot.features.bands import StepBand, TradeBand
from nqbot.features.chart_levels import ChartSession
from nqbot.features.freshness import TouchRecord
from nqbot.features.node_levels import _track
from nqbot.features.session_tape import MIN_NS, NS, Tape, bars, et_ns
from tests.test_touches_labels import DAY, P, at, level, tape_of, zone_touches

T0 = at(10, 0)


# --- Q-density (c): the label at several distances; Q-step11h (1): the pair cut ------------------------------------

def test_held_at_20_but_not_at_30():
    """A ceiling touch at 100 runs 25 pts down, then the zone breaks: held at 20 (resolved on the 20-pt move), broke at
    30 (the cross comes first)."""
    tape = tape_of([(T0, 100.0), (T0 + NS, 90.0), (T0 + 2 * NS, 79.75), (T0 + 3 * NS, 75.0), (T0 + 10 * NS, 110.0)])
    touch = tch.Touch("z1", T0, 0, 100.0, -1, (), 99.0, 101.0, event_id="e")
    cross = (int(T0 + 10 * NS), slab.BROKE)
    at20 = slab.label(touch, tape, cross, T0 + 30 * MIN_NS, 20.0)
    at30 = slab.label(touch, tape, cross, T0 + 30 * MIN_NS, 30.0)
    assert (at20.label, at20.t_resolved) == (slab.HELD, T0 + 2 * NS)       # 79.75 = 100 - 20 - 1 tick
    assert (at30.label, at30.t_resolved) == (slab.BROKE, T0 + 10 * NS)
    assert at30.t_resolved >= at20.t_resolved                             # a 30-pt resolution is never earlier


def test_pair_cut_strictly_before_the_own_resolution():
    assert slab.pair_allowed(99, 100) and not slab.pair_allowed(100, 100) and not slab.pair_allowed(101, 100)


def test_hold_distances_are_separate_settings():
    f = load_features()
    assert (f["hold_reaction_pts"], f["history_hold_pts"], f["pair_end_hold_pts"], f["retest_hold_pts"]) == (30, 30, 20, 20)
    assert sfm.HOLD_KEYS == ("hold_reaction_pts", "history_hold_pts", "retest_hold_pts", "pair_end_hold_pts")


# --- Q-step11h (2): the touch group is the event's own ------------------------------------------------------------

def test_touch_group_mates_retested_extreme_is_the_events_own(monkeypatch):
    """Events a and b touched by one trade (one group); b's held label made retested extreme r1 at 100: a's rows from
    100 on come from the session built without the group's retested extremes (both a and b excluded)."""
    built = []

    class Fake:
        def __init__(self, ctx, exclude_source=None, horizon_ns=None):
            built.append(exclude_source)
            self.by_id = {"a": "a-in-alt"}
            self.horizon_ns = horizon_ns

        def build(self):
            return self

    s = sfm.SessionFeatures.__new__(sfm.SessionFeatures)
    s.c, s._alts = object(), {}
    s.groups = {1: frozenset({"a", "b"}), 2: frozenset({"c"})}
    s.retested_today = [sl.Retested("r1", 20_000.0, "pdh", date(2026, 3, 19), 100, "b")]
    s.retest_source = {"r1": "b"}
    a = tch.Touch("z1", 10, 0, 1.0, 1, (), 0.0, 2.0, group=1, event_id="a")
    c = tch.Touch("z2", 10, 0, 1.0, 1, (), 0.0, 2.0, group=2, event_id="c")
    monkeypatch.setattr(sfm, "SessionFeatures", Fake)
    assert sfm.SessionFeatures is Fake
    w, we = s.world.__func__(s, a, 50) if hasattr(s.world, "__func__") else s.world(a, 50)
    assert w is s and we is a                                         # before r1 exists
    w, we = s.world(a, 150)
    assert isinstance(w, Fake) and we == "a-in-alt" and built == [frozenset({"a", "b"})]
    assert s._own(a, SimpleNamespace(level_id="r1")) and not s._own(c, SimpleNamespace(level_id="r1"))
    assert s.world(c, 150)[0] is s                                    # another group: r1 is part of its world


# --- Q-step12a (1): zone-book candidate times ----------------------------------------------------------------------

def _book_0317(switch: bool, every_trade: bool = False):
    """The 2026-03-17 09:35:09 shape: a node band N steps between trades so the zone {N, V} is 60.002 wide at the step
    (V released) and 59.998 at the next trade (V back in), with V a moving (VWAP-like) band."""
    ts = T0 + np.arange(10, dtype=np.int64) * NS
    tape = Tape.from_arrays(DAY, ts, np.full(10, 100.0))
    c = np.where(np.arange(10) < 5, 100.0, 99.996)
    v = TradeBand(tape, c, 4.0, int(ts[0]))
    n = StepBand(tape, np.array([ts[0], ts[4] + NS // 2], dtype=np.int64), np.array([44.002, 43.998]),
                 np.array([97.0, 97.0]))
    end = int(ts[-1]) + 1
    lv = [zn.BookLevel("N", n, [(int(ts[0]), end)], change_times=n.times), zn.BookLevel("V", v, [(int(ts[0]), end)])]
    old = zn.STEP_TIME_CANDIDATES
    zn.STEP_TIME_CANDIDATES = switch
    try:
        return zn.ZoneBook(lv, tape, lambda t: (10.0, 60.0), end_ns=end).run(every_trade=every_trade), ts
    finally:
        zn.STEP_TIME_CANDIDATES = old


def test_relation_flip_at_a_band_step_and_back_at_the_next_trade():
    book, ts = _book_0317(True)
    ref, _ = _book_0317(True, every_trade=True)
    step = int(ts[4]) + NS // 2
    assert sorted(book.zones_at(step).values()) == [("N",), ("V",)]        # 60.002 wide: V released
    assert book.zones_at(int(ts[5])) == {"z1": ("N", "V")}                  # 59.998 at the next trade: back in
    assert book.zones_at(int(ts[5])) == ref.zones_at(int(ts[5]))
    legacy, _ = _book_0317(False)                                          # features_version <= 36: missed
    assert sorted(legacy.zones_at(int(ts[9])).values()) == [("N",), ("V",)]


# --- Q-step12a (2): hysteresis ---------------------------------------------------------------------------------------

def _merge(levels, prev=(), jb=5.0, qb=5.0, ids=None):
    return zn.merge(list(levels), list(prev), 10.0, 60.0, ids or zn.IdSource(), jb, qb)


def _mem(zones):
    return sorted(tuple(z.members) for z in zones)


G = zn.Geom


def test_join_needs_max_width_minus_the_join_buffer():
    assert _mem(_merge([G("A", 100, 130), G("B", 129, 155)])[0]) == [("A", "B")]             # 55
    assert _mem(_merge([G("A", 100, 130), G("B", 129, 155.25)])[0]) == [("A",), ("B",)]      # 55.25
    assert _mem(_merge([G("A", 100, 130), G("B", 129, 155.25)], jb=0, qb=0)[0]) == [("A", "B")]   # no buffer


def test_zone_merge_needs_the_join_buffer_too():
    ids = zn.IdSource()
    z, _ = _merge([G("A", 100, 110), G("B", 200, 210)], ids=ids)
    z2, absorbed = _merge([G("A", 100, 110), G("B", 109, 155)], prev=z, ids=ids)           # 55: merge
    assert _mem(z2) == [("A", "B")] and absorbed == {"z2": "z1"}
    z3, _ = _merge([G("A", 100, 110), G("B", 109, 155.25)], prev=z, ids=zn.IdSource(2))   # 55.25: apart
    assert _mem(z3) == [("A",), ("B",)]


def test_a_kept_zone_may_reach_the_hard_cap():
    ids = zn.IdSource()
    z, _ = _merge([G("A", 100, 130), G("B", 129, 150)], ids=ids)
    assert _mem(_merge([G("A", 100, 130), G("B", 129, 160)], prev=z, ids=ids)[0]) == [("A", "B")]      # 60 kept
    assert _mem(_merge([G("A", 100, 130), G("B", 129, 160.25)], prev=z, ids=ids)[0]) == [("A",), ("B",)]


def test_kept_member_qualifies_within_the_qualify_buffer():
    ids = zn.IdSource()
    z, _ = _merge([G("A", 100, 120), G("B", 119, 139)], ids=ids)                          # overlap: one zone
    assert _mem(_merge([G("A", 100, 120), G("B", 125, 145)], prev=z, ids=ids)[0]) == [("A", "B")]      # gap 5
    assert _mem(_merge([G("A", 100, 120), G("B", 125.25, 145)], prev=z, ids=zn.IdSource(1))[0]) == [("A",), ("B",)]
    # a new level (not kept) still needs the plain F5.3 rule: gap 5 doesn't join
    assert _mem(_merge([G("A", 100, 120), G("B", 125, 145)])[0]) == [("A",), ("B",)]


def test_kept_member_centres_within_tol_plus_the_buffer():
    ids = zn.IdSource()
    z, _ = _merge([G("A", 100, 102), G("B", 105, 107)], ids=ids)                          # centres 5 apart
    assert _mem(_merge([G("A", 100, 102), G("B", 115, 117)], prev=z, ids=ids)[0]) == [("A", "B")]      # 15 = tol + 5
    assert _mem(_merge([G("A", 100, 102), G("B", 115.25, 117.25)], prev=z, ids=zn.IdSource(1))[0]) == [("A",), ("B",)]


def test_buffers_zero_are_the_old_merge_and_order_free():
    import random
    lv = [G("A", 100, 120), G("B", 118, 140), G("C", 139, 165), G("D", 300, 310), G("E", 305, 312, 0.9),
          G("F", 150, 151), G("G", 90, 95)]
    base = _mem(_merge(lv)[0])
    rnd = random.Random(3)
    for _ in range(20):
        sh = lv[:]
        rnd.shuffle(sh)
        assert _mem(_merge(sh)[0]) == base
    assert _mem(_merge(lv, jb=0, qb=0)[0]) == _mem(zn.merge(lv, [], 10.0, 60.0, zn.IdSource())[0])


def test_book_parity_with_every_trade_with_the_buffers():
    rng = np.random.default_rng(11)
    p = 20_000 + np.cumsum(rng.normal(0, 2.0, 2500))
    ts = T0 + np.arange(len(p), dtype=np.int64) * NS
    tape = Tape.from_arrays(DAY, ts, p)
    c = np.convolve(p, np.ones(150) / 150, mode="full")[:len(p)]
    c[:150] = np.nan
    end = int(ts[-1]) + 1
    levels = [zn.BookLevel("V", TradeBand(tape, c, 4.0, int(ts[0])), [(int(ts[0]), end)])]
    for k, x in enumerate(np.linspace(p.min(), p.max(), 11)):
        b = StepBand(tape, np.array([ts[0], ts[700] + NS // 3, ts[1600] + NS // 2], dtype=np.int64),
                     np.array([x - 4, x - 1.5, x - 6]), np.array([x + 4, x + 6.5, x + 2]))
        levels.append(zn.BookLevel(f"L{k}", b, [(int(ts[0]), end)], change_times=b.times))
    mk = lambda: zn.ZoneBook([zn.BookLevel(x.level_id, x.band, x.spans, change_times=x.change_times) for x in levels],
                             tape, lambda t: (10.0, 60.0, 5.0, 5.0), end_ns=end)
    fast, ref = mk().run(), mk().run(every_trade=True)
    for t in list(ts[::5]) + [int(ts[700]) + NS // 3, int(ts[1600]) + NS // 2]:
        assert fast.zones_at(int(t)) == ref.zones_at(int(t))


# --- Q-step12b (b): a merge keeps the larger touch count -----------------------------------------------------------

def test_merged_zone_keeps_the_larger_touch_count():
    rec = lambda t, z: TouchRecord(t, ("x",), z, ls.FLOOR)
    own = [rec(10, "z1"), rec(20, "z1"), rec(5, "z2"), rec(15, "z2"), rec(25, "z2"), rec(35, "z1")]
    absorbed = {"z1": [(30, "z2")]}
    assert fr.zone_test_count(own, "z1", 31, absorbed) == 3          # max(2, 3): no reset, no summing
    assert fr.zone_test_count(own, "z1", 40, absorbed) == 4          # + the later own touch
    assert fr.zone_test_count(own, "z1", 29, absorbed) == 2          # before the merge
    assert fr.zone_test_count(own, "z1", 40, None) == 3              # without the merge rule
    old = fr.MERGE_KEEPS_COUNT
    fr.MERGE_KEEPS_COUNT = False
    try:
        assert fr.zone_test_count(own, "z1", 40, absorbed) == 3
    finally:
        fr.MERGE_KEEPS_COUNT = old


# --- Q-step12b (c), (d): a crossed member's own re-arm --------------------------------------------------------------

def _path(prices):
    return tape_of([(T0 + k * NS, p) for k, p in enumerate(prices)])


def test_single_level_retest_needs_only_its_own_rearm():
    """Node ceiling [100, 102], prox 2, rearm 10: 85 arms from below, 99 = event 1 (short), 103 crosses it, 112 is its
    own re-arm (10 beyond 102; the zone's re-arm needs 114) and 103 = the post-cross retest (long); 108 doesn't re-arm,
    113 does, and 103 is the next retest."""
    tape = _path([85, 99, 103, 112, 103, 108, 103, 113, 103])
    n = level(tape, "N", ls.NODE, 100, 102, role=ls.CEILING)
    _, zt = zone_touches(tape, [n])
    assert [(x.idx, x.side) for x in zt.touches] == [(1, -1), (4, 1), (8, 1)]
    old = tch.OWN_REARM
    tch.OWN_REARM = False
    try:
        _, zt0 = zone_touches(tape, [n])
    finally:
        tch.OWN_REARM = old
    assert [x.idx for x in zt0.touches] == [1]                         # features_version <= 36


def test_own_rearm_short_of_rearm_dist_does_not_arm():
    tape = _path([85, 99, 103, 111.75, 103])
    _, zt = zone_touches(tape, [level(tape, "N", ls.NODE, 100, 102, role=ls.CEILING)])
    assert [x.idx for x in zt.touches] == [1]


def test_merged_zone_retest_from_the_members_own_rearm():
    """Zone {N [100, 102], S [104, 115]}: N is crossed at 103 and re-armed at 112 (its own band + 10) while price is
    still inside S, so the zone's re-arm band (127) is never reached; 103 back at N is the retest (long)."""
    tape = _path([85, 99, 103, 112, 103])
    n = level(tape, "N", ls.NODE, 100, 102, role=ls.CEILING)
    s = level(tape, "S", ls.CHART, 104, 115, role=ls.CEILING)
    _, zt = zone_touches(tape, [n, s])
    assert [(x.idx, x.side) for x in zt.touches] == [(1, -1), (4, 1)]
    assert zt.own_arms == 1


# --- Q-step12a (4): strike-side formation role and touch ------------------------------------------------------------

def _node_with_formation(prices, band=(100.0, 102.0), freezes=(), ftimes=None):
    tape = _path(prices)
    b = StepBand.const(tape, int(tape.ts[0]), *band)
    ft = [int(tape.ts[0])] if ftimes is None else ftimes
    st = ls.LevelState("N", ls.NODE, tape, b, P, bars(tape, 1), bars(tape, 5), end_ns=int(tape.ts[-1]) + MIN_NS,
                       freezes=list(freezes), formation_prox=2.0, formation_times=ft).run()
    lv = rg.RegLevel("N", "N", "N", "qqq_node", rg.GEX_NODE, "NQ", ls.NODE, False, b, st, _track(st),
                     spans=rg.active_spans(st, b, int(tape.ts[-1]) + MIN_NS))
    return tape, lv


def test_formation_touch_in_the_strike_side_role():
    tape, lv = _node_with_formation([100.5, 99, 95, 90])
    assert lv.state.formations == [(int(tape.ts[0]), 0, ls.CEILING)]
    assert lv.state.state_at(int(tape.ts[0])).role == ls.CEILING
    _, zt = zone_touches(tape, [lv])
    assert [(x.idx, x.side) for x in zt.touches] == [(0, -1)] and zt.formation_touches == 1


def test_formation_above_the_strike_is_a_floor_and_outside_the_band_none():
    tape, lv = _node_with_formation([103.5, 104, 110])
    assert lv.state.formations[0][2] == ls.FLOOR
    tape, lv = _node_with_formation([104.25, 110, 120])                  # beyond band + prox: the approach rule
    assert lv.state.formations == [] and lv.state.state_at(int(tape.ts[-1])).role == ls.FLOOR
    _, zt = zone_touches(tape, [lv])
    assert zt.touches == []


def test_formation_exactly_on_the_strike_takes_the_approach_rule():
    tape, lv = _node_with_formation([101.0, 101.5, 120])
    assert lv.state.formations == [] and lv.state.state_at(int(tape.ts[1])).role is None


def test_formation_at_the_next_trade_after_a_return_to_relevance():
    """Not relevant (frozen) from 2 s to 5 s, relevant again at 5 s: the first trade after it (6 s, 101.5) is inside the
    touch band, above the centre: a floor and a touch."""
    tape = _path([120, 120, 120, 120, 120, 120, 101.5, 110])
    t = tape.ts
    tape, lv = _node_with_formation([120, 120, 120, 120, 120, 120, 101.5, 110], freezes=[(int(t[2]), int(t[5]) + 1)],
                                    ftimes=[int(t[0]), int(t[5]) + 1])
    assert [(i, r) for _, i, r in lv.state.formations] == [(6, ls.FLOOR)]


# --- Q-step12b (a): level jumps ----------------------------------------------------------------------------------------

def test_split_jumps_cuts_a_step_band_only_where_bands_dont_overlap():
    tape = _path([25_000.0] * 4)
    t = tape.ts
    drift = StepBand(tape, t[:3].copy(), np.array([25_146.0, 25_148.0, 25_150.0]), np.array([25_154.0, 25_156.0, 25_158.0]))
    assert bd.split_jumps(drift) == [drift]                                # overlapping drift: one level
    jump = StepBand(tape, t[:3].copy(), np.array([25_146.0, 24_746.0, 24_748.0]), np.array([25_154.0, 24_754.0, 24_756.0]))
    a, b = bd.split_jumps(jump)
    assert list(a.times) == [t[0]] and a.end_ns == t[1] and list(b.times) == list(t[1:3]) and b.end_ns is None
    touching = StepBand(tape, t[:2].copy(), np.array([100.0, 104.0]), np.array([104.0, 108.0]))
    assert len(bd.split_jumps(touching)) == 1                              # touching counts as overlapping


def test_split_jumps_on_a_trade_band():
    tape = _path([100.0] * 6)
    c = np.array([np.nan, 100.0, 101.0, 120.0, 121.0, 121.5])
    a, b = bd.split_jumps(TradeBand(tape, c, 4.0, int(tape.ts[0])))
    assert a.at(int(tape.ts[2])) == (97.0, 105.0) and a.at(int(tape.ts[3])) is None
    assert b.start_ns == int(tape.ts[3]) and b.at(int(tape.ts[2])) is None and b.at(int(tape.ts[4])) == (117.0, 125.0)


def _fake_session(tape, alias=None):
    f = load_features()
    s = SimpleNamespace(f=f, tape=tape, bars_1m=bars(tape, 1), bars_flip=bars(tape, 5), normals=None,
                        end_ns=int(tape.ts[-1]) + MIN_NS, root="NQ", levels={}, jumps=[], level_alias_out={},
                        carry=SimpleNamespace(level_alias=alias or {}))
    s._jump_id = lambda lid, t, taken=frozenset(): ChartSession._jump_id(s, lid, t, taken)
    return s


def test_weekly_poc_jump_20251118_starts_a_new_level():
    """2025-11-18 13:19:00: the weekly POC moved 25,150 -> 24,750 (24,750's volume passed 25,150's): the old level ends
    at 13:19 (it leaves its zone without a cross) and a new level with a fresh state starts there."""
    d = date(2025, 11, 18)
    t_a, t_jump = et_ns(d, __import__("datetime").time(9, 30)), et_ns(d, __import__("datetime").time(13, 19))
    tape = Tape.from_arrays(d, np.array([t_a, t_a + NS, t_jump - NS, t_jump + NS, t_jump + 60 * NS], dtype=np.int64),
                            np.array([25_200.0, 25_200.0, 24_900.0, 24_752.0, 24_760.0]))
    poc = np.array([25_150.0, 25_150.0, 24_750.0])
    band = StepBand(tape, np.array([t_a, t_a + 60 * NS, t_jump], dtype=np.int64), poc - 4, poc + 4)
    s = _fake_session(tape)
    ChartSession._add(s, "poc_weekly-2025-11-16", "poc_weekly", band, multi_day=True)
    assert sorted(s.levels) == ["poc_weekly-2025-11-16", "poc_weekly-2025-11-16@20251118T181900Z"]
    old = s.levels["poc_weekly-2025-11-16"][0].state
    new = s.levels["poc_weekly-2025-11-16@20251118T181900Z"][0].state
    assert old.band.at(t_jump) is None and old.band.at(t_jump - 1) == (25_146.0, 25_154.0)
    assert new.band.at(t_jump) == (24_746.0, 24_754.0) and new.state_at(t_jump + NS).flip_count == 0
    assert s.jumps == [{"sub_type": "poc_weekly", "key": "poc", "level_id": "poc_weekly-2025-11-16", "t": t_jump,
                        "at_session_start": False, "returned_to": None}]   # 400 pts: no earlier piece there
    assert s.level_alias_out["poc_weekly-2025-11-16"]["id"] == "poc_weekly-2025-11-16@20251118T181900Z"
    # the zone founded by the weekly POC never follows it 400 pts onto price
    end = int(tape.ts[-1]) + MIN_NS
    ph = StepBand.const(tape, t_a, 25_148.0, 25_152.0)
    lv = [zn.BookLevel("ph", ph, [(t_a, end)], change_times=ph.times)]
    for lid, recs in s.levels.items():
        b = recs[0].state.band
        lv.append(zn.BookLevel(lid, b, rg.active_spans(recs[0].state, b, end), change_times=b.times))
    book = zn.ZoneBook(lv, tape, lambda t: (10.0, 60.0), end_ns=end).run()
    z_before = {z for z, m in book.zones_at(t_jump - 1).items() if "poc_weekly-2025-11-16" in m}
    after = book.zones_at(t_jump + NS)
    assert all("ph" in m for z, m in after.items() if z in z_before)       # the old zone stays at 25,150
    assert next(z for z, m in after.items() if "poc_weekly-2025-11-16@20251118T181900Z" in m) not in z_before


def test_multi_day_level_keeps_its_carried_id_unless_it_jumped_overnight():
    tape = _path([24_760.0] * 3)
    band = StepBand.const(tape, int(tape.ts[0]), 24_747.0, 24_755.0)
    alias = {"poc_weekly-w": {"id": "poc_weekly-w@20251118T181900Z", "lo": 24_746.0, "hi": 24_754.0}}
    s = _fake_session(tape, alias)
    ChartSession._add(s, "poc_weekly-w", "poc_weekly", band, multi_day=True)
    assert list(s.levels) == ["poc_weekly-w@20251118T181900Z"] and s.jumps == []
    far = StepBand.const(tape, int(tape.ts[0]), 25_000.0, 25_008.0)
    s2 = _fake_session(tape, alias)
    ChartSession._add(s2, "poc_weekly-w", "poc_weekly", far, multi_day=True)
    assert len(s2.levels) == 1 and next(iter(s2.levels)).startswith("poc_weekly-w@") and s2.jumps[0]["at_session_start"]


# --- Q-step13-0 (d1): a profile level back on an earlier piece's band is that piece's level ----------------------------

D18 = date(2025, 11, 18)


def _et(h, m, s=0):
    from datetime import time as _t
    return et_ns(D18, _t(h, m, s))


def _aba(last_before_return):
    """Daily POC A = 100 +- 4 from 10:00, B = 130 +- 4 from 10:01, A's band again from 10:05. Price 115 at 10:00 gives A
    the floor role; while away price settles at 80 for minutes (below A: a cross if A existed); the last trade before
    the return is `last_before_return`; 112 after."""
    ts = [_et(10, 0, 1), _et(10, 1, 30), _et(10, 2, 10), _et(10, 2, 50), _et(10, 3, 30), _et(10, 4, 50), _et(10, 5, 1),
          _et(10, 6)]
    px = [115.0, 80.0, 80.0, 80.0, 80.0, last_before_return, 112.0, 112.0]
    tape = Tape.from_arrays(D18, np.array(ts, dtype=np.int64), np.array(px))
    poc = np.array([100.0, 130.0, 100.0])
    band = StepBand(tape, np.array([_et(10, 0), _et(10, 1), _et(10, 5)], dtype=np.int64), poc - 4, poc + 4)
    s = _fake_session(tape)
    ChartSession._add(s, "poc_daily-x", "poc_daily", band, multi_day=False)
    return s


def test_a_b_a_returns_to_as_id_and_state_and_the_time_away_is_no_cross():
    s = _aba(110.0)
    b_id = "poc_daily-x@20251118T150100Z"
    assert list(s.levels) == ["poc_daily-x", b_id] and len(s.levels["poc_daily-x"]) == 1
    a = s.levels["poc_daily-x"][0].state
    assert a.band.at(_et(10, 0, 30)) == (96.0, 104.0) and a.band.at(_et(10, 3)) is None
    assert a.band.at(_et(10, 5)) == (96.0, 104.0)
    st = a.state_at(_et(10, 5, 30))
    assert st.role == ls.FLOOR and not st.crossed and st.cross_count == 0 and st.flip_count == 0
    assert not any(n in ("cross", "flip", "return_role") for _, n in a.events)
    assert [j["returned_to"] for j in s.jumps] == [None, "poc_daily-x"]
    assert s.levels[b_id][0].state.band.at(_et(10, 5)) is None             # B ends where A comes back


def test_a_return_with_price_on_the_other_side_takes_its_role_from_price():
    s = _aba(85.0)
    a = s.levels["poc_daily-x"][0].state
    st = a.state_at(_et(10, 5, 0))
    assert st.role == ls.CEILING and not st.crossed and st.cross_count == 0 and st.armed   # 96 - 85 = 11 >= 10
    assert [n for t, n in a.events if t == _et(10, 5)] == ["return_role"]


def test_the_20251118_switches_return_to_the_most_recent_overlapping_piece():
    """Weekly POC 25,150 -> 24,780 (13:14) -> 24,770 (13:15) -> 24,750 (13:19) -> 24,775 (13:43) -> 24,750 (13:44):
    24,775 +- 4 overlaps both 24,780's and 24,770's bands and goes back to 24,770's (active later); 24,750 comes back
    to its own. Three new levels instead of five."""
    times = [_et(12, 0), _et(13, 14), _et(13, 15), _et(13, 19), _et(13, 43), _et(13, 44)]
    tape = Tape.from_arrays(D18, np.array([_et(12, 0, 1), _et(13, 50)], dtype=np.int64), np.array([24_760.0] * 2))
    poc = np.array([25_150.0, 24_780.0, 24_770.0, 24_750.0, 24_775.0, 24_750.0])
    s = _fake_session(tape)
    ChartSession._add(s, "poc_weekly-w", "poc_weekly", StepBand(tape, np.array(times, dtype=np.int64), poc - 4,
                                                                  poc + 4), multi_day=True)
    w = "poc_weekly-w@20251118T"
    assert list(s.levels) == ["poc_weekly-w", w + "181400Z", w + "181500Z", w + "181900Z"]
    assert [j["returned_to"] for j in s.jumps] == [None, None, None, w + "181500Z", w + "181900Z"]
    al = s.level_alias_out["poc_weekly-w"]
    assert al["id"] == w + "181900Z" and [pid for pid, _ in al["pieces"]][-2:] == [w + "181500Z", w + "181900Z"]
    assert sorted(map(tuple, dict(al["pieces"])[w + "181500Z"])) == [(24_766.0, 24_774.0), (24_771.0, 24_779.0)]


def test_a_weekly_level_returns_to_a_piece_of_an_earlier_session_of_the_week():
    tape = _path([25_200.0] * 3)
    alias = {"poc_weekly-w": {"id": "poc_weekly-w@20251118T181900Z", "lo": 24_746.0, "hi": 24_754.0,
                              "pieces": [["poc_weekly-w", [[25_146.0, 25_154.0]]],
                                         ["poc_weekly-w@20251118T181900Z", [[24_746.0, 24_754.0]]]]}}
    s = _fake_session(tape, alias)
    ChartSession._add(s, "poc_weekly-w", "poc_weekly", StepBand.const(tape, int(tape.ts[0]), 25_147.0, 25_155.0),
                      multi_day=True)
    assert list(s.levels) == ["poc_weekly-w"]
    assert s.jumps[0]["at_session_start"] and s.jumps[0]["returned_to"] == "poc_weekly-w"
    assert [pid for pid, _ in s.level_alias_out["poc_weekly-w"]["pieces"]] == ["poc_weekly-w@20251118T181900Z",
                                                                               "poc_weekly-w"]


def test_a_level_that_is_not_a_profile_level_never_returns():
    tape = Tape.from_arrays(D18, np.array([_et(10, 0, 1), _et(10, 6)], dtype=np.int64), np.array([115.0, 112.0]))
    v = np.array([100.0, 130.0, 100.0])
    s = _fake_session(tape)
    ChartSession._add(s, "vwap18-x", "vwap18",
                      StepBand(tape, np.array([_et(10, 0), _et(10, 1), _et(10, 5)], dtype=np.int64), v - 4, v + 4),
                      multi_day=False)
    assert len(s.levels) == 3 and all(j["returned_to"] is None for j in s.jumps)


def test_the_roll_shifts_the_carried_piece_bands():
    from nqbot.features.carry import CarryState

    c = CarryState("NQ")
    c.level_alias = {"poc_weekly-w": {"id": "x", "lo": 1.0, "hi": 9.0, "pieces": [["x", [[1.0, 9.0], [11.0, 19.0]]]]}}
    c.shift(5.0)
    assert c.level_alias["poc_weekly-w"]["pieces"] == [["x", [[6.0, 14.0], [16.0, 24.0]]]]


# --- Q-step12a (3): relevance debounce ---------------------------------------------------------------------------------

def _rel(pcts, secs, min_sec=60.0, fast=0.40):
    n = len(pcts)
    b = np.arange(n)
    return nodes.relevance(b, np.full(n, 600.0), np.asarray(pcts, float), np.ones(n, bool), 0.25, 0.20,
                           board_t=np.asarray(secs, np.int64) * NS, min_sec=min_sec, fast=fast)[0].tolist()


def test_marginal_join_counts_after_60_s_of_board_time():
    assert _rel([0.30] * 5, [0, 20, 40, 60, 80]) == [False, False, False, True, True]


def test_marginal_join_falling_back_within_60_s_is_never_relevant():
    assert _rel([0.30, 0.30, 0.30, 0.22, 0.30, 0.30], [0, 20, 40, 50, 60, 100]) == [False] * 6


def test_fast_join_is_at_once():
    assert _rel([0.45, 0.30], [0, 5]) == [True, True]


def test_leave_recovering_within_60_s_stays_relevant_and_a_held_leave_leaves():
    assert _rel([0.5, 0.15, 0.15, 0.15, 0.22, 0.30], [0, 10, 30, 50, 60, 80]) == [True] * 6
    assert _rel([0.5, 0.15, 0.15, 0.15, 0.15], [0, 10, 30, 50, 70]) == [True, True, True, True, False]


def test_king_strike_switch_moves_every_node_together():
    """A bigger King appears on one board for 30 s: every node's % of King drops below the drop line together and
    recovers; with the debounce none of them leaves (without it all leave and rejoin)."""
    n_nodes, boards = 3, [0, 10, 40, 50]
    pct = {0: [0.5, 0.6, 0.45], 10: [0.15, 0.18, 0.12], 40: [0.5, 0.6, 0.45], 50: [0.5, 0.6, 0.45]}
    b = np.repeat(np.arange(len(boards)), n_nodes)
    k = np.tile([600.0, 605.0, 610.0], len(boards))
    p = np.concatenate([pct[s] for s in boards])
    t = np.asarray(boards, np.int64) * NS
    deb = nodes.relevance(b, k, p, np.ones(len(boards), bool), 0.25, 0.20, board_t=t, min_sec=60, fast=0.40)[0]
    raw = nodes.relevance(b, k, p, np.ones(len(boards), bool), 0.25, 0.20)[0]
    assert deb.all() and not raw[n_nodes:2 * n_nodes].any()


def test_debounce_zero_is_the_undebounced_rule():
    rng = np.random.default_rng(5)
    n = 4000
    b = np.sort(rng.integers(0, 400, n))
    k = rng.choice([600.0, 601.0, 602.0, 603.0], n)
    p = rng.uniform(0.1, 0.5, n)
    p[rng.random(n) < 0.03] = np.nan
    ok = rng.random(400) < 0.9
    t = np.cumsum(rng.integers(1, 5, 400)).astype(np.int64) * NS
    a = nodes.relevance(b, k, p, ok, 0.25, 0.20)
    z = nodes.relevance(b, k, p, ok, 0.25, 0.20, board_t=t, min_sec=0)
    assert (a[0] == z[0]).all() and (a[1] == z[1]).all()


def test_debounce_is_causal():
    pcts, secs = [0.30, 0.30, 0.30, 0.30, 0.15, 0.15], [0, 20, 40, 60, 70, 90]
    full = _rel(pcts, secs)
    for cut in range(1, len(pcts)):
        assert _rel(pcts[:cut], secs[:cut]) == full[:cut]


# --- Q-step12 (2): node_relevance_top_n --------------------------------------------------------------------------------

def _rank_case(board_vals, secs, protected_strike=None, top_n=5):
    """board_vals: one {strike: value} per board (the King = the largest |value|); every node is relevant here."""
    rows = [(i, k, v) for i, d in enumerate(board_vals) for k, v in sorted(d.items())]
    b = np.array([r[0] for r in rows])
    k = np.array([r[1] for r in rows], float)
    v = np.array([r[2] for r in rows], float)
    king = np.array([max(abs(x) for x in d.values()) for d in board_vals])
    pct = np.abs(v) / king[b]
    rel = np.ones(len(rows), bool)
    prot = k == protected_strike if protected_strike is not None else np.zeros(len(rows), bool)
    lvl = nr.level_relevant(b, k, v, pct, rel, np.full(len(board_vals), 0.0), np.asarray(secs, np.int64) * NS, prot,
                            top_n, 7, 60, 0.40)
    return {(int(b[i]), float(k[i])): bool(lvl[i]) for i in range(len(rows))}


def test_top_five_ranked_at_once_when_large_and_the_rest_not():
    vals = {float(s): 10.0 - s for s in range(8)}           # 10, 9, ..., 3 (pct 1.0 ... 0.3)
    got = _rank_case([vals], [0])
    assert [got[(0, float(s))] for s in range(8)] == [True] * 5 + [False] * 3


def test_protected_node_at_any_rank():
    vals = {float(s): 10.0 - s for s in range(8)}
    assert _rank_case([vals], [0], protected_strike=7.0)[(0, 7.0)] is True


def test_marginal_rank_join_waits_60_s_and_a_rank_leave_too():
    """Strike 5 (pct 0.30, rank 6) moves into the top 5 at 10 s when strike 4 shrinks to rank 8: 5 joins at 70 s, not
    before; strike 4 leaves only once it has been below 7th for 60 s."""
    b0 = {0.0: 10, 1.0: 9, 2.0: 8, 3.0: 7, 4.0: 6, 5.0: 3, 6.0: 2.9, 7.0: 2.8}
    b1 = {**b0, 4.0: 1.0}
    secs = [0, 10, 40, 70]
    got = _rank_case([b0, b1, b1, b1], secs)
    assert [got[(i, 5.0)] for i in range(4)] == [False, False, False, True]
    assert [got[(i, 4.0)] for i in range(4)] == [True, True, True, False]   # below 7th from 10 s: leaves at 70 s


def test_ranks_six_and_seven_stay_ranked():
    b0 = {0.0: 10, 1.0: 9, 2.0: 8, 3.0: 7, 4.0: 6, 5.0: 3, 6.0: 2.9}
    b1 = {**b0, 4.0: 2.95}                                   # strike 4 falls to 6th: still ranked
    got = _rank_case([b0, b1, b1], [0, 10, 200])
    assert got[(2, 4.0)] is True


def test_rank_ties_nearer_spot_then_lower_strike():
    r = nr.ranks(np.zeros(3, int), np.array([10.0, 20.0, 30.0]), np.array([5.0, 5.0, 5.0]), np.ones(3, bool),
                 np.array([22.0]))
    assert r.tolist() == [3, 1, 2]


# --- Q-density (d): retest_lookback_sessions -----------------------------------------------------------------------------

def test_retest_lookback_drops_older_candidates_and_extremes():
    days = [date(2026, 3, d) for d in (2, 3, 4, 5, 6, 9)]
    today = days[-1]
    cands = [sl.Candidate(100.0, "pdh", days[1], "a"), sl.Candidate(200.0, "pdh", days[2], "b")]   # 4 and 3 back
    touches = [{"price": 100.0, "t_resolved": 1, "label": "held", "event_id": "e1"},
               {"price": 200.0, "t_resolved": 2, "label": "held", "event_id": "e2"}]
    assert [r.price for r in sl.retests_from_touches(cands, touches, today, days, 4, 10)] == [100.0, 200.0]
    assert [r.price for r in sl.retests_from_touches(cands, touches, today, days, 4, 3)] == [200.0]
    r = sl.Retested("x", 100.0, "pdh", days[1], 0)
    assert not sl.expired(r, today, days, 10) and sl.expired(r, today, days, 3)


def test_variant_grids():
    g = load_features()["tune_grids_13"]
    assert g["node_variants"] == {"node_relevance_top_n": [None, 5], "node_role_at_formation": ["approach", "strike_side"]}
    assert g["retest_variants"] == {"retest_lookback_sessions": [10, 3, 5]}


# --- the diagnostic switches (owner 2026-11-09) --------------------------------------------------------------------------

def test_builds_refuse_a_changed_diagnostic_switch(monkeypatch):
    from nqbot.checks import session_build as sb

    assert dg.changed() == {}
    monkeypatch.setattr(zn, "STEP_TIME_CANDIDATES", False)
    with pytest.raises(dg.DiagnosticSwitches):
        sb.build_day(SimpleNamespace(), date(2026, 3, 20), None, None, [], {})
    with pytest.raises(dg.DiagnosticSwitches):
        sb.fold_only(SimpleNamespace(), None, date(2026, 3, 20), None, {})
    with pytest.raises(AttributeError):     # the harness flag passes the guard (then fails on the empty env)
        sb.build_day(SimpleNamespace(), date(2026, 3, 20), None, None, [], {}, allow_diagnostic_switches=True)


def test_switched_restores_and_rejects_unknown_names():
    with dg.switched({"nqbot.features.profile:PROFILE_ROW_TICKS": 1}):
        from nqbot.features import profile as pf
        assert pf.PROFILE_ROW_TICKS == 1 and dg.changed() == {"nqbot.features.profile:PROFILE_ROW_TICKS": 1}
    assert dg.changed() == {}
    with pytest.raises(ValueError):
        with dg.switched({"nqbot.features.zones:NOPE": 1}):
            pass
    assert set(dg.LEGACY) == {"cand", "jump", "rearm", "rows", "mergecount"}
    assert {v[0] for v in dg.LEGACY.values()} == set(dg.DEFAULTS)
