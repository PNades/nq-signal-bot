"""GEX nodes on the futures (step 11 part 2; feature_spec F2.4, F2.6, F2.6b, F2.7, F2.11, F2.19 Q18, F1.8, F0.11)."""

import numpy as np
from pytest import approx

from nqbot.config import load_features
from nqbot.features import level_state as ls
from nqbot.features import node_switch as nsw
from nqbot.features import rolls as rl
from nqbot.features import session_tape as st
from nqbot.features.map_state import MapState
from nqbot.features.node_levels import MapNodes, Scale, spans_from_mask
from tests.test_map_state import DAY, NS, T0, mk

# these hand-built boards test F2.4's hysteresis and the rules built on it; the step-13 relevance debounce
# (node_relevance_min_sec, Q-step12a (3)) has its own tests (tests/test_relevance_debounce.py)
# the hand-built examples use the step-6 QQQ band 0.5 (frozen at 0.375 since owner 2026-11-12, Q-step13a-2 (7))
F = {**load_features(), "node_relevance_min_sec": 0, "node_band": {**load_features()["node_band"], "QQQ": 0.5}}


def tape_of(trades):
    return st.Tape.from_arrays(DAY, [T0 + int(round(s * NS)) for s, _ in trades], [p for _, p in trades])


def nodes(boards, trades, name="QQQ", features=None, blocks=(), scale=None, end_sec=None):
    f = features or F
    ms = MapState.build(mk(boards, name=name), f)
    tape = tape_of(trades)
    end = T0 + int((end_sec if end_sec is not None else trades[-1][0] + 600) * NS)
    return MapNodes(ms, tape, f, st.bars(tape, 1), st.bars(tape, f["flip_bar_minutes"]), blocks=list(blocks),
                    end_ns=end, scale=scale)


def at(sec):
    return T0 + int(round(sec * NS))


# a relevant ceiling node at strike 100.5: band [100, 101] with R = 1 and node_band 0.5
KING = {200.0: 1.0}
BOARD = {**KING, 100.5: 0.5}


def test_spans_from_mask():
    t = np.array([0, 10, 20, 30, 40])
    assert spans_from_mask(t, np.array([False, True, True, False, True]), 99) == [(10, 30), (40, 99)]


def test_f26_node_cross_on_its_band_and_role_track():
    mn = nodes([(0, 90, BOARD)], [(0, 89), (10, 101.25), (20, 111)])
    s = mn.node_at(100.5, at(15))
    assert s["band"] == (100.0, 101.0)
    assert s["crossed"] and s["role"] == ls.FLOOR and s["provisional_role"]
    tr = mn.levels[100.5].track
    assert tr.crosses == [(at(10), ls.CEILING)]
    assert tr.role_at(at(5)) == ls.CEILING and tr.role_at(at(15)) == ls.FLOOR


def test_f24_frozen_while_not_relevant_and_while_missing():
    # 100.5 relevant on the first board, 0.15 of King (not relevant) from 5 s to 30 s: the trade at 10 is not a cross
    boards = [(0, 90, BOARD), (5, 90, {**KING, 100.5: 0.15}), (30, 90, BOARD)]
    mn = nodes(boards, [(0, 89), (10, 101.25), (40, 100.5)])
    assert not mn.node_at(100.5, at(45))["crossed"]
    # missing from the board 5-30 s: the same
    boards = [(0, 90, BOARD), (5, 90, dict(KING)), (30, 90, BOARD)]
    mn = nodes(boards, [(0, 89), (10, 101.25), (40, 100.5)])
    assert not mn.node_at(100.5, at(45))["crossed"]
    assert mn.node_at(100.5, at(10))["band"] is None            # unknown on a board that doesn't carry it (F2.1)
    # relevant throughout: the trade at 10 is a cross
    assert nodes([(0, 90, BOARD)], [(0, 89), (10, 101.25), (40, 100.5)]).node_at(100.5, at(45))["crossed"]


def test_node_created_at_first_relevant_board_and_band_follows_r():
    boards = [(0, 90, {**KING, 100.5: 0.10}), (20, 90, BOARD)]
    mn = nodes(boards, [(0, 89), (30, 89)])
    assert mn.levels[100.5].state.band.start_ns == at(20)
    assert mn.node_at(100.5, at(10)) is None
    ms = MapState.build(mk([(0, 90, BOARD)], r=2.0), F)
    tape = tape_of([(0, 150), (10, 150)])
    mn2 = MapNodes(ms, tape, F, st.bars(tape, 1), st.bars(tape, 5), end_ns=at(600))
    assert mn2.node_at(100.5, at(5))["band"] == (200.0, 202.0)   # (strike ± 0.5) × R (F1.4)


def test_f26b_node_series_cross_features():
    boards = [(0, 90, BOARD), (15, 90, {**KING, 100.5: -0.3})]
    mn = nodes(boards, [(0, 89), (10, 101.25), (20, 102)])
    s = mn.node_at(100.5, at(25))
    assert s["xb_node_pctking_chg"] == approx(-0.2) and s["xb_node_sign_flipped"] is True


def test_f27_beach_ball():
    rd = F["rearm_dist"]
    mn = nodes([(0, 90, BOARD)], [(0, 89), (10, 101.5), (20, 103), (30, 100.5), (40, 100 - rd)])
    s = mn.node_at(100.5, at(50))
    assert s["beach_ball_count_today"] == 1 and s["bb_overshoot_depth"] == approx(2.0)
    assert s["bb_seconds_beyond"] == approx(20.0) and s["bb_seconds_to_reclaim"] == approx(30.0)
    assert mn.node_at(100.5, at(35))["beach_ball_count_today"] == 0


def test_f18_block_pauses_node_and_judges_at_block_end():
    blk = [(at(5), at(30))]
    mn = nodes([(0, 90, BOARD)], [(0, 89), (10, 101.25), (25, 101.5), (60, 101.5)], blocks=blk)
    assert mn.node_at(100.5, at(20))["crossed"] is None          # null inside the block
    s = mn.node_at(100.5, at(31))
    assert s["crossed"]
    assert mn.levels[100.5].track.crosses == [(at(30), ls.CEILING)]


def test_q_step11d_1_sp_flip_margin_key_and_scale():
    f = {**F, "flip_margin": {**F["flip_margin"], "qqq_node": 4.0, "ndxp_node": 8.0}}
    sc = Scale(prev=0.2, today=0.25, switch_ns=at(0))
    spy = nodes([(0, 90, BOARD)], [(0, 89), (10, 89)], name="SPY", features=f, scale=sc)
    p0, later = spy._params()
    assert p0.flip_margin == approx(0.8) and later[0][1].flip_margin == approx(1.0)
    assert p0.rearm_dist == approx(F["rearm_dist"] * 0.2)
    spx = nodes([(0, 90, {200.0: 1.0, 105.0: 0.5})], [(0, 89), (10, 89)], name="SPX", features=f, scale=sc)
    assert spx._params()[0].flip_margin == approx(1.6)
    ndxp = nodes([(0, 90, BOARD)], [(0, 89), (10, 89)], name="NDXP", features=f)
    assert ndxp._params()[0].flip_margin == approx(8.0) and ndxp._params()[1] == []


# --- F2.11 rolls ---------------------------------------------------------------------------------------------

def reference_rolls(mn, sign):
    """Feed every board (whole strike list) and every trade to a fresh RollSide (trades inside a block wait,
    `block_end` at its end, F1.8)."""
    side = rl.RollSide(sign, nsw.from_config(mn.f, "ceiling" if sign > 0 else "floor"))
    mb, tape = mn.ms.mb, mn.tape
    for i in range(len(mb)):
        t0 = int(mb.t_apply[i])
        t1 = int(mb.t_apply[i + 1]) if i + 1 < len(mb) else mn.end_ns
        ks, vs = mb.strikes(i)
        h, r = mn.half, float(mb.r[i])
        side.on_board(t0 / NS, float(mb.spot[i]), dict(zip(ks.tolist(), vs.tolist())),
                      {k: ((k - h) * r, (k + h) * r) for k in ks.tolist()}, bool(mb.one_minute[i]))
        for z in sorted(b for _, b in mn.blocks):
            if t0 <= z < t1 and tape.last_price_at(z) is not None and z == t0:
                side.block_end(z / NS, tape.last_price_at(z))
        for j in range(tape.idx_before(t0), tape.idx_before(t1)):
            t = int(tape.ts[j])
            for z in sorted(b for _, b in mn.blocks):
                if t0 < z <= t and z > (int(tape.ts[j - 1]) if j > tape.idx_before(t0) else t0):
                    side.block_end(z / NS, tape.last_price_at(z))
            side.on_trade(t / NS, float(tape.price[j]), blocked=mn.in_block(t))
    return side


def test_f211_driver_matches_feeding_every_trade():
    rng = np.random.default_rng(4)
    strikes = np.arange(740.0, 771.0)
    boards = []
    vals = rng.uniform(0.1, 1.0, len(strikes))
    spot = 755.0
    for s in range(0, 1800, 2):
        vals = np.abs(vals + rng.normal(0, 0.25, len(vals)))
        spot += rng.normal(0, 0.4)
        boards.append((s, spot, dict(zip(strikes, vals * rng.choice([-1, 1], len(vals), p=[0.2, 0.8])))))
    trades = []
    px = 755.0
    for s in np.arange(0, 1800, 0.5):
        px += rng.normal(0, 0.3)
        trades.append((float(s), px))
    mn = nodes(boards, trades, end_sec=1800, blocks=[(at(600), at(660))])
    kinds = set()
    for sign, mr in ((+1, mn.ceiling), (-1, mn.floor)):
        ref = reference_rolls(mn, sign)
        got = [(e.t, e.kind, e.old, e.new) for e in mr.side.events]
        want = [(e.t, e.kind, e.old, e.new) for e in ref.events]
        assert got == want
        kinds |= {k for _, k, _, _ in got}
        assert (mr.side.rolls_today, mr.side.roll_breaks_today) == (ref.rolls_today, ref.roll_breaks_today)
    assert {"roll", "rollback", "partial_rollback", "price_switch", "pause", "resume"} <= kinds


def test_f211_roll_down_wiring_and_episode_clock():
    # spot 755; 760 (2.0, ceiling) and 759 (1.0); from t = 1: 760 -> 0.8, 759 -> 1.5 -> roll-down at t = 60
    boards = [(0, 755, {700: -3.0, 759: 1.0, 760: 2.0})] + \
        [(s, 755, {700: -3.0, 759: 1.5, 760: 0.8}) for s in range(1, 120)]
    mn = nodes(boards, [(0, 755), (100, 755)], end_sec=200)
    s = mn.ceiling.at(at(60))
    assert s["confirmed"] == 759 and s["rolling"] and s["roll_steps"] == 1 and s["rolls_today"] == 1
    assert s["last_roll_shrink_pct"] == approx(0.6) and s["last_roll_growth_pct"] == approx(0.5)
    assert mn.ceiling.at(at(59))["confirmed"] == 760
    assert mn.ceiling.at(at(90))["minutes_since_episode_began"] == approx(0.5)
    assert mn.ceiling.at(at(90))["episode_board"] == 60


def test_f211_pause_on_a_trade_through_the_ceiling():
    boards = [(0, 755, {700: -3.0, 759: 1.0, 760: 2.0})] + \
        [(s, 755, {700: -3.0, 759: 1.5, 760: 0.8}) for s in range(1, 120)]
    mn = nodes(boards, [(0, 755), (70, 759.6), (100, 758.4)], end_sec=200)
    s = mn.ceiling.at(at(75))
    assert s["paused"] and not s["rolling"] and s["confirmed"] == 760 and s["roll_breaks_today"] == 1
    s = mn.ceiling.at(at(105))
    assert not s["paused"] and s["confirmed"] == 759
