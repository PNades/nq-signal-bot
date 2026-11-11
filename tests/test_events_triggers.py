"""Style (c) firings: the failed audit / audited hold and the sweep reclaim (feature_spec F6.1, F6.2; owner 2026-11-10,
SPEC §13 Q-step13b (2): every completion is a decision point), on hand-built tapes."""

from __future__ import annotations

from datetime import time
from types import SimpleNamespace

import pytest

from nqbot.events import triggers as trig
from nqbot.events.common import GROUPS, decision_id, round_limit, round_stop, round_target, round_trigger
from nqbot.events.snapshot import TRACK_HORIZON_SEC
from nqbot.config import load_features
from nqbot.features import level_state as ls
from nqbot.features import session_tape as st
from nqbot.features import touches as tch
from nqbot.features.bands import StepBand
from tests.test_touches_labels import DAY

NS = st.NS


def at(h, m, s=0):
    return st.et_ns(DAY, time(h, m, s))


class FakeSF:
    """The engine-stage interface `triggers` reads, around one gameplan-like container zone with a fixed band."""

    def __init__(self, trades, lo, hi, arms=(), members=()):
        self.nq = st.Tape.from_arrays(DAY, [t for t, _ in trades], [p for _, p in trades])
        self.c = SimpleNamespace(cs_nq=SimpleNamespace(bars_1m=st.bars(self.nq, 1), opening=None))
        band = StepBand.const(self.nq, int(self.nq.ts[0]) - NS, lo, hi)
        self.containers = [SimpleNamespace(uid="gp_1", band=band, active=lambda t: True)]
        self.arms = {"gp_1": sorted(arms)}
        self.retested_today = []
        self.book = SimpleNamespace(merged_into={})
        self.mem = list(members)

    def group_of(self, e):
        return frozenset({e.event_id})

    def world(self, e, t):
        return self, e

    def members_at(self, e, t):
        return self.mem


def touch(sf, idx, side=-1):
    t = int(sf.nq.ts[idx])
    return tch.Touch("gp_1", t, idx, float(sf.nq.price[idx]), side, (), 100.0, 102.0, kind="gameplan",
                     event_id=tch.event_id(t, "gp_1"))


# the F6.1 test (ceiling [100, 102], a short): a trade at 101 at 10:00:20; the 10:00 bar closes inside at 100.5; the
# 10:01 bar closes at 99.75 -> the audit completes at 10:02:00 with trigger bar 10:01
F61 = [(at(10, 0, 5), 99.5), (at(10, 0, 20), 101.0), (at(10, 0, 50), 100.5), (at(10, 1, 30), 99.75)]
FAR = at(11, 0)


def test_the_f61_example_completes_at_the_trigger_bar_close():
    sf = FakeSF(F61, 100.0, 102.0)
    out = trig.style_c(sf, touch(sf, 0), FAR)
    assert [f.decision_ts for f in out] == [at(10, 2)]
    f = out[0]
    assert (f.bar_start, f.bar_close, f.trigger_kind, f.n) == (at(10, 1), 99.75, "audit", 1)
    assert f.audit_depth == pytest.approx(1.0)                  # 101 - the near edge 100
    assert f.audit_depth_frac == pytest.approx(0.5)
    assert f.audit_seconds_inside == pytest.approx(70.0)        # 10:00:20 -> 10:01:30 inside the band


def test_a_close_inside_the_band_does_not_complete():
    sf = FakeSF(F61[:3], 100.0, 102.0)
    assert trig.style_c(sf, touch(sf, 0), FAR) == []


def test_a_second_completion_needs_a_new_trade_inside():
    more = F61 + [(at(10, 2, 30), 100.25), (at(10, 2, 50), 99.5),        # back inside, closes out: firing 2 at 10:03
                  (at(10, 3, 40), 99.0)]                                 # 10:03 closes out without a trade inside
    sf = FakeSF(more, 100.0, 102.0)
    out = trig.style_c(sf, touch(sf, 0), FAR)
    assert [(f.n, f.decision_ts) for f in out] == [(1, at(10, 2)), (2, at(10, 3))]
    assert decision_id("E", "c", 2) == "E-c2"


def test_a_rearm_during_the_trigger_bar_does_not_cancel_it_but_one_before_does():
    more = F61 + [(at(10, 2, 30), 100.25), (at(10, 2, 50), 99.5)]
    sf = FakeSF(more, 100.0, 102.0, arms=[at(10, 2, 10)])
    assert [f.n for f in trig.style_c(sf, touch(sf, 0), FAR)] == [1, 2]
    sf = FakeSF(more, 100.0, 102.0, arms=[at(10, 1, 59)])
    assert [f.n for f in trig.style_c(sf, touch(sf, 0), FAR)] == [1]


def test_nothing_at_or_after_the_pair_cut():
    sf = FakeSF(F61, 100.0, 102.0)
    assert trig.style_c(sf, touch(sf, 0), at(10, 2)) == []           # decision_ts must be < t_pair_end
    assert len(trig.style_c(sf, touch(sf, 0), at(10, 2) + 1)) == 1


def test_a_long_audited_hold():
    # floor [100, 102] (long, approached from above): inside at 101, the bar closes above 102
    trades = [(at(10, 0, 5), 102.5), (at(10, 0, 20), 101.0), (at(10, 0, 50), 102.25)]
    sf = FakeSF(trades, 100.0, 102.0)
    out = trig.style_c(sf, touch(sf, 0, side=1), FAR)
    assert [f.decision_ts for f in out] == [at(10, 1)]
    assert out[0].audit_depth == pytest.approx(1.0)


class SweptMember:
    kind = ls.SWEEP

    def __init__(self, t_sweep):
        self.state = SimpleNamespace(at=lambda t: {"last_sweep_t": t_sweep, "sweep_depth": 4.0,
                                                   "sweep_seconds_beyond": 12.0})


def test_the_f62_sweep_reclaim_is_tagged():
    # PDH ceiling [100, 100.5]: a sweep to 104; a bar closes at 100.25 (inside: swept, no trigger); the next at 99.5
    trades = [(at(10, 0, 5), 99.75), (at(10, 0, 20), 104.0), (at(10, 0, 50), 100.25), (at(10, 1, 40), 99.5)]
    sf = FakeSF(trades, 100.0, 100.5, members=[SweptMember(at(10, 0, 30))])
    out = trig.style_c(sf, touch(sf, 0), FAR)
    assert [f.decision_ts for f in out] == [at(10, 2)]
    assert (out[0].trigger_kind, out[0].swept, out[0].sweep_depth) == ("sweep_reclaim", True, 4.0)


def test_groups_and_horizon_match_the_config():
    g = load_features()["tune_grids_13"]
    assert {k: list(v) for k, v in GROUPS.items()} == {k: list(v) for k, v in
                                                       g["detectors"]["flip_margin"]["groups"].items()}
    assert TRACK_HORIZON_SEC >= max(g["execution"]["entry_ttl_sec"])


def test_order_price_rounding():
    assert (round_stop(100.1, 1), round_stop(100.1, -1)) == (100.0, 100.25)       # away from entry
    assert (round_target(100.1, 1), round_target(100.1, -1)) == (100.0, 100.25)   # toward entry
    assert (round_limit(100.1, 1), round_limit(100.1, -1)) == (100.0, 100.25)     # passive
    assert (round_trigger(100.1, 1), round_trigger(100.1, -1)) == (100.25, 100.0)
    assert round_stop(100.25, 1) == 100.25


def test_touches_record_the_zone_rearm_time():
    # F5.6 path: 120 (armed from above) -> 104 (touch) -> 110 -> 115 (re-armed) -> 103.5 (second touch)
    from tests.test_touches_labels import level, tape_of, zone_touches

    t0 = at(10, 0)
    tape = tape_of([(t0 + k * NS, p) for k, p in enumerate([120, 104, 110, 115, 103.5])])
    _, zt = zone_touches(tape, [level(tape, "L", ls.CHART, 100, 102)])
    assert [x.t_armed for x in zt.touches] == [t0, t0 + 3 * NS]
    assert [t for _, t in zt.arms] == [t0, t0 + 3 * NS]


def test_damage_drops_events_within_the_pad_of_a_damaged_gap():
    from datetime import datetime

    from nqbot.events.build import damage_drop
    from nqbot.features.damage import DayDamage
    from nqbot.features.session_tape import ET

    sf = SimpleNamespace(f={"hold_window_min": 30}, flat_ns=at(15, 59), end_ns=at(17, 0))
    e = SimpleNamespace(t=at(10, 0))
    gap = lambda h, m: (datetime(DAY.year, DAY.month, DAY.day, h, m, tzinfo=ET),   # noqa: E731
                        datetime(DAY.year, DAY.month, DAY.day, h, m + 2, tzinfo=ET))
    assert damage_drop(sf, e, DayDamage(DAY, rth_gap_minutes=[gap(10, 44)]), 15)       # window to 10:30, pad 15
    assert not damage_drop(sf, e, DayDamage(DAY, rth_gap_minutes=[gap(10, 46)]), 15)
    assert damage_drop(sf, e, DayDamage(DAY, rth_gap_minutes=[gap(9, 43)]), 15)        # ends 9:45, pad to 10:00
    assert damage_drop(sf, e, DayDamage(DAY, rth_dropped=True), 15)
    assert not damage_drop(sf, e, None, 15)
