"""The level state machine for every level type (F2.6, F2.6b, F3.9, F5.2, F5.9) and the owner's answers of
2026-10-31 (SPEC §13 Q-step11a)."""

from datetime import date, time

import numpy as np
import pytest
from pytest import approx

from nqbot.features import block_state as bs
from nqbot.features import level_state as ls
from nqbot.features import rvol
from nqbot.features import session_tape as st
from nqbot.features import sweep as sw
from nqbot.features.bands import StepBand

DAY = date(2026, 3, 6)
NS = st.NS
T0 = st.et_ns(DAY, time(10, 0))
P = ls.Params(rearm_dist=10, flip_margin=0, flip_bar_minutes=5, sweep_max_depth=15, sweep_reclaim_bars=3)


def at(sec):
    return T0 + int(round(sec * NS))


def make(kind, trades, lo, hi, *, start=T0 - 60 * NS, params=P, normals=None, blocks=(), sizes=None, sides=None):
    secs = [x[0] for x in trades]
    tape = st.Tape.from_arrays(DAY, [at(x) for x in secs], [x[1] for x in trades], sizes, sides)
    band = StepBand.const(tape, start, lo, hi)
    lv = ls.LevelState("L", kind, tape, band, params, st.bars(tape, 1), st.bars(tape, params.flip_bar_minutes),
                       normals=normals, blocks=list(blocks), end_ns=at(secs[-1]) + 600 * NS)
    return lv.run()


def names(lv):
    return [n for _, n in lv.events]


# --- F2.6 (node kind) --------------------------------------------------------------------------------------------

def test_f26_cross_then_flip_on_settled_5m_bar():
    lv = make(ls.NODE, [(0, 89), (10, 101.25), (300, 101.5), (590, 102)], 100, 101)
    s = lv.at(at(20))
    assert s["role"] == ls.FLOOR and s["crossed"] and s["provisional_role"] and s["flip_count"] == 0
    f = lv.at(at(600))                       # the 10:05 bar (open 101.5, close 102) known at 10:10:00
    assert f["role"] == ls.FLOOR and not f["crossed"] and f["flip_count"] == 1 and f["minutes_since_flip"] == 0
    assert lv.at(at(599))["flip_count"] == 0


def test_f26_reclaim_needs_rearm_dist_beyond_the_original_edge():
    lv = make(ls.NODE, [(0, 89), (10, 101.25), (20, 90.25)], 100, 101)
    assert lv.at(at(30))["crossed"] and not lv.at(at(30))["reclaimed"]
    lv = make(ls.NODE, [(0, 89), (10, 101.25), (20, 90)], 100, 101)
    s = lv.at(at(30))
    assert s["role"] == ls.CEILING and not s["crossed"] and s["reclaimed"] and s["xb_depth"] is None


def test_f26_post_cross_retest_cross_back_then_reclaim_or_resume():
    base = [(0, 89), (10, 101.25), (20, 111), (30, 100.5), (40, 99.75)]
    lv = make(ls.NODE, base + [(50, 90)], 100, 101)
    assert names(lv) == ["role_ceiling", "cross", "prov_armed", "cross_back", "reclaim"]
    s = lv.at(at(25))
    assert s["armed"] and s["active"] and s["role"] == ls.FLOOR      # armed as a provisional floor at 111
    s = lv.at(at(45))
    assert s["crossed"] and not s["active"]                            # broke back: no events until reclaim/resume
    lv = make(ls.NODE, base + [(50, 101.25)], 100, 101)
    assert names(lv)[-1] == "resume" and lv.at(at(55))["cross_count_today"] == 1   # the same episode resumes


def test_no_role_until_rearm_dist_outside():
    lv = make(ls.NODE, [(0, 95), (10, 101.5)], 100, 101)               # 95 is only 5 below: no role, no cross
    assert lv.at(at(20))["role"] is None and names(lv) == []


# --- F2.6b cross features -------------------------------------------------------------------------------------

def test_f26b_depth_reentries_and_settled_bars():
    trades = [(-50, 89), (0, 100.5), (5, 101.5), (30, 103), (45, 100.5),  # cross at 10:00:05, depth 2, back inside
              (60, 101.5), (110, 102),                                  # 10:01 bar (101.5, 102): settled
              (120, 102), (170, 100.75),                                # 10:02 bar (102, 100.75): closes inside
              (180, 101.25), (230, 101.5),                              # 10:03 bar (101.25, 101.5): settled
              (300, 102), (590, 102.5)]                                 # 10:05 5-minute bar (102, 102.5): settled
    lv = make(ls.NODE, trades, 100, 101)
    s = lv.at(at(50))
    assert s["xb_depth"] == 2.0 and s["xb_reentries"] == 1
    s = lv.at(at(240))
    assert s["xb_settled_1m"] == 2 and s["xb_settled_5m"] == 0
    assert lv.at(at(600))["xb_settled_5m"] == 1 and lv.at(at(600))["flip_count"] == 1


def normals_flat(per_min=60.0):
    return rvol.RthNormals.of(DAY, np.full(390, per_min))


def test_f26b_volume_over_the_seconds_spent_beyond():
    """300 contracts traded beyond against a normal of 150 over the same seconds -> 2.0 (owner 2026-10-31: the normal
    over the seconds spent beyond only)."""
    trades = [(-50, 89), (10, 101.5), (40, 102), (70, 101.5), (160, 100.5), (400, 100.5)]
    lv = make(ls.NODE, trades, 100, 101, normals=normals_flat(60.0), sizes=[1, 100, 100, 100, 7, 7],
              sides=[b"N", b"B", b"B", b"B", b"A", b"A"])
    s = lv.at(at(400))                      # beyond 10 -> 160 s: 150 s at 1 contract/s
    assert s["xb_sec_beyond"] == approx(150) and s["xb_vol_rel"] == approx(2.0) and s["xb_delta_rel"] == approx(2.0)
    mid = lv.at(at(100))                    # the open stretch from 70 s counts up to t
    assert mid["xb_sec_beyond"] == approx(90) and mid["xb_vol_rel"] == approx(300 / 90)


def test_f26b_rth_part_only_before_the_open():
    """Owner 2026-10-31: only the RTH part of the seconds beyond counts, volume and normal both."""
    o = st.rth_open_ns(DAY)
    sec = lambda x: (o + x * NS - T0) / NS          # seconds after 09:30 -> the test's time base
    trades = [(sec(-200), 89), (sec(-60), 101.5), (sec(30), 102), (sec(60), 100.5)]
    lv = make(ls.NODE, trades, 100, 101, start=o - 300 * NS, normals=normals_flat(60.0), sizes=[1, 500, 30, 1])
    s = lv.at(o + 60 * NS)
    assert s["xb_sec_beyond"] == approx(120)                     # 09:29 -> 09:31, all of it
    assert s["xb_vol_rel"] == approx(30 / 60)                    # RTH part: 30 contracts over 60 RTH seconds
    early = lv.at(o - NS)
    assert early["xb_sec_beyond"] == approx(59) and early["xb_vol_rel"] is None   # no RTH second yet


def test_cross_features_null_in_original_role():
    lv = make(ls.NODE, [(0, 89), (10, 99)], 100, 101)
    assert lv.at(at(20))["xb_depth"] is None and lv.at(at(20))["role"] == ls.CEILING


# --- F5.2 chart levels (settled 1-minute bar) ------------------------------------------------------------------

def test_f52_chart_cross_needs_a_settled_1m_bar():
    trades = [(-50, 89), (0, 100.5), (20, 101.5), (59, 100.75),          # 10:00 bar reaches 101.5, closes 100.75
              (60, 101.25), (119, 101.5)]                               # 10:01 bar opens 101.25, closes 101.5
    lv = make(ls.CHART, trades, 100, 101)
    assert not lv.at(at(60))["crossed"]
    assert lv.at(at(30))["unresolved_break"]                             # beyond with no settled bar yet
    assert not lv.at(at(119))["crossed"] and lv.at(at(120))["crossed"]   # known at the 10:02:00 close


def test_f05_band_moving_inside_the_bar():
    tape = st.Tape.from_arrays(DAY, [at(-50), at(0), at(59)], [89, 102.25, 103])
    band = StepBand(tape, np.array([T0 - 60 * NS, at(30)]), np.array([100.0, 100.0]), np.array([102.0, 104.0]))
    lv = ls.LevelState("L", ls.CHART, tape, band, P, st.bars(tape, 1), st.bars(tape, 5), end_ns=at(300)).run()
    assert not lv.at(at(60))["crossed"]                                  # the band moved to [100, 104] by the close


# --- F3.9 sweep (highs/lows) ----------------------------------------------------------------------------------

def test_f39_pdh_swept_then_broke_at_once_over_depth():
    lv = make(ls.SWEEP, [(-50, 85), (0, 101), (20, 105), (50, 99.5)], 100, 100)
    s = lv.at(at(60))
    assert not s["crossed"] and s["sweep_depth"] == 5 and s["last_sweep_t"] == at(60)
    assert lv.at(at(30))["unresolved_break"]
    lv = make(ls.SWEEP, [(-50, 85), (0, 101), (20, 100 + 15 + 0.25)], 100, 100)
    assert lv.at(at(20))["crossed"] and (at(20), "cross") in lv.events


def test_f39_break_at_the_window_close():
    trades = [(-50, 85), (10, 101)] + [(60 * k + 30, 102) for k in range(1, 4)]
    lv = make(ls.SWEEP, trades, 100, 100)
    assert not lv.at(at(239))["crossed"] and lv.at(at(240))["crossed"]   # break bar + 3 minutes, closed at 10:04


def bars_tape(bar_list, lo, hi):
    """A tape whose 1-minute bars are the given (open, high, low, close), plus an arming trade before."""
    ts, px = [at(-120)], [lo - 30 if True else 0]
    for k, (o, h, l, c) in enumerate(bar_list):
        for j, p in enumerate((o, h, l, c)):
            ts.append(at(60 * k + 10 * j + 1))
            px.append(p)
    return ts, px


@pytest.mark.parametrize("bar_list", [
    [(99, 99.5, 98, 99), (99.5, 104, 99, 103), (103, 104, 98, 99)],                 # swept on bar 2's close
    [(99, 99.5, 98, 99), (99.5, 104, 99, 103), (103, 106, 102, 105), (105, 106, 103, 104), (104, 105, 102, 103)],
    [(99, 99.5, 98, 99), (99.5, 120, 99, 110)],                                    # over depth
])
def test_streaming_sweep_matches_sweep_judge(bar_list):
    lo = hi = 100.0
    ts, px = bars_tape(bar_list, lo, hi)
    tape = st.Tape.from_arrays(DAY, ts, px)
    band = StepBand.const(tape, at(-180), lo, hi)
    lv = ls.LevelState("L", ls.SWEEP, tape, band, P, st.bars(tape, 1), st.bars(tape, 5), end_ns=at(900)).run()
    bars = [sw.Bar(float((at(60 * k) - T0) / NS), o, h, l, c) for k, (o, h, l, c) in enumerate(bar_list)]
    ref = sw.judge(bars, lo, hi, sw.CEILING, True, P.sweep_max_depth, P.sweep_reclaim_bars)
    got = [(t, n) for t, n in lv.events if n in ("swept", "cross")][0]
    t_got = (got[0] - T0) / NS
    assert got[1] == ("swept" if ref.kind == "swept" else "cross")
    if ref.kind == "crossed" and ref.t % 60 == 0 and any(b.high - hi > 15 for b in bars):
        assert ref.t <= t_got < ref.t + 60       # over depth: judge reports the bar, the stream the trade
    else:
        assert t_got == ref.t


# --- node kind in a dislocation block (parity with block_state.NodeCross) -----------------------------------

@pytest.mark.parametrize("prices,lo,hi,expect_cross", [
    ([115, 99, 99, 99], 100, 101, True),        # through the floor inside the block, still below at its end
    ([115, 102, 102, 102], 103, 104, False),    # a band mapped wrongly: price "beyond" never judged in the block
    ([115, 99, 105, 105], 100, 101, False),     # the cross undone inside the block
])
def test_node_block_resume_matches_nodecross(prices, lo, hi, expect_cross):
    secs = [0, 30, 60, 90, 150]          # NodeCross resumes at the block end when the next trade arrives
    prices = prices + [prices[-1]]
    block = (at(20), at(100))
    if lo == 103:   # the wrongly-mapped band applies only inside the block; outside it is [100, 101]
        tape = st.Tape.from_arrays(DAY, [at(s) for s in secs], prices)
        band = StepBand(tape, np.array([T0 - 60 * NS, at(20), at(100)]), np.array([100.0, 103.0, 100.0]),
                        np.array([101.0, 104.0, 101.0]))
    else:
        tape = st.Tape.from_arrays(DAY, [at(s) for s in secs], prices)
        band = StepBand.const(tape, T0 - 60 * NS, lo, hi)
    lv = ls.LevelState("L", ls.NODE, tape, band, P, st.bars(tape, 1), st.bars(tape, 5), blocks=[block],
                       end_ns=at(400)).run()
    ref = bs.replay_cross(bs.NodeCross(side=+1), tape.ts, tape.price, lambda t: band.at(t), [block])
    crossed = lv.at(at(200))["crossed"]
    assert crossed == expect_cross == (ref.crossed_ns is not None)
    if expect_cross:
        assert lv.events[-1][0] == ref.crossed_ns == block[1]
    assert all(v is None for v in lv.at(at(50)).values())                 # outputs null inside the block


# --- owner 2026-10-31: the machine runs from the level's creation ------------------------------------------

def test_overnight_cross_is_a_real_cross():
    d0 = st.cme_start_ns(DAY)                       # 18:00 ET the evening before
    o = st.rth_open_ns(DAY)
    tape = st.Tape.from_arrays(DAY, [d0 + 60 * NS, o - 6 * 3600 * NS, o - 3600 * NS], [20_000, 20_100.5, 20_090])
    band = StepBand.const(tape, d0, 20_045, 20_053)      # PDH 20,049 ± 4, known from 18:00
    lv = ls.LevelState("pdh", ls.SWEEP, tape, band, P, st.bars(tape, 1), st.bars(tape, 5), end_ns=o + 60 * NS).run()
    s = lv.at(o)   # broke at 03:30 (over depth), flipped at 03:35 (a one-trade 5-minute bar settles): a real cross
    assert s["role"] == ls.FLOOR and s["cross_count_today"] == 1 and s["flip_count"] == 1
    assert s["xb_depth"] == approx(47.5) and s["minutes_since_cross"] == approx(360)


# --- Q8 (owner 2026-10-31): reclaim and resume need the cross back confirmed by the level's own rule ---------

def ev_time(lv, name):
    return [t for t, n in lv.events if n == name]


CHART_CROSSED = [(-50, 89), (0, 101.5), (59, 102)]          # 10:00 bar settles above the ceiling [100, 101]


def test_chart_touched_10_back_without_a_settled_bar_stays_crossed():
    trades = CHART_CROSSED + [(65, 101.5), (70, 90), (110, 100.5)]   # 10:01 bar dips to 90, closes inside the band
    lv = make(ls.CHART, trades, 100, 101)
    for t in (75, 120, 300):
        s = lv.at(at(t))
        assert s["crossed"] and not s["reclaimed"] and s["role"] == ls.FLOOR, t
    assert ev_time(lv, "reclaim") == [] and ev_time(lv, "cross_back") == []


def test_chart_reclaim_after_the_settled_bar_then_the_rearm_trade():
    trades = CHART_CROSSED + [(120, 99.5), (179, 99)] + [(190, 89.5)]      # 10:02 bar settles below, then 89.5
    lv = make(ls.CHART, trades, 100, 101)
    assert ev_time(lv, "cross_back") == [at(180)] and ev_time(lv, "reclaim") == [at(190)]
    assert lv.at(at(185))["crossed"] and lv.at(at(190))["reclaimed"]
    same_bar = CHART_CROSSED + [(120, 99.5), (150, 90), (179, 99)]          # the 90 dip inside the confirming bar
    lv = make(ls.CHART, same_bar, 100, 101)
    assert ev_time(lv, "reclaim") == [at(180)] and not lv.at(at(179))["reclaimed"]


SWEEP_CROSSED = [(-50, 85), (10, 120)]                     # PDH [100, 100] broken at once (20 > sweep_max_depth 15)


def test_high_low_reverse_sweep_closing_back_keeps_the_provisional_role():
    trades = SWEEP_CROSSED + [(70, 88), (110, 101)]        # 12 back below (< 15), the 10:01 bar closes back above
    lv = make(ls.SWEEP, trades, 100, 100)
    s = lv.at(at(130))
    assert s["crossed"] and s["role"] == ls.FLOOR and not s["reclaimed"]
    assert ev_time(lv, "swept_back") == [at(120)] and ev_time(lv, "reclaim") == []


def test_high_low_reverse_break_then_reclaim_at_the_window_close():
    trades = SWEEP_CROSSED + [(70, 88)] + [(60 * k + 50, 95) for k in range(1, 5)]   # stays below to 10:04
    lv = make(ls.SWEEP, trades, 100, 100)
    assert not lv.at(at(299))["reclaimed"]                  # break bar 10:01 + 3 minutes: resolved at 10:05:00
    assert ev_time(lv, "cross_back") == [at(300)] and ev_time(lv, "reclaim") == [at(300)]
    assert lv.at(at(300))["reclaimed"] and lv.at(at(300))["role"] == ls.CEILING


def test_resume_needs_the_same_confirmation():
    broken = CHART_CROSSED + [(120, 99.5), (179, 99)]                      # broken back at 10:03
    lv = make(ls.CHART, broken + [(190, 102), (230, 100.5)], 100, 101)      # one trade above, the bar closes inside
    assert ev_time(lv, "resume") == []
    lv = make(ls.CHART, broken + [(240, 101.5), (299, 102)], 100, 101)      # the 10:04 bar settles above
    # resumed at 10:05:00 (the same instant the 10:00 5-minute bar, 101.5 -> 102, settles above and flips it)
    assert ev_time(lv, "resume") == [at(300)] and lv.at(at(300))["role"] == ls.FLOOR
    # a reverse break at the window close only 5 below (< rearm_dist): broken back at 10:05:00, not reclaimed
    # (a reverse break beyond sweep_max_depth 15 is always >= rearm_dist 10 past the edge, so it reclaims at once)
    base = SWEEP_CROSSED + [(70, 95)] + [(60 * k + 50, 96) for k in range(1, 5)]
    lv = make(ls.SWEEP, base + [(310, 105), (350, 99)], 100, 100)           # a poke above that closes back below
    assert ev_time(lv, "cross_back") == [at(300)] and ev_time(lv, "reclaim") == []
    assert ev_time(lv, "resume_swept") == [at(360)] and ev_time(lv, "resume") == []
    lv = make(ls.SWEEP, base + [(310, 117)], 100, 100)                      # 17 above: a break at once
    assert ev_time(lv, "resume") == [at(310)]
    node = make(ls.NODE, [(0, 89), (10, 101.25), (20, 99.75), (30, 101.25)], 100, 101)   # nodes: unchanged
    assert [n for _, n in node.events] == ["role_ceiling", "cross", "cross_back", "resume"]
