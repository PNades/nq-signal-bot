"""Features during a mapping-dislocation block (owner 2026-10-17, §13 Q-dislguard-2 (a)): hand-built examples."""

import numpy as np

from nqbot.config import load_features
from nqbot.features import block_state as bs
from nqbot.features import disloc_guard as dg

NS = dg.NS
SPAN = [(10 * NS, 70 * NS)]  # one block, 10 s -> 70 s


def _band(during_block: tuple[float, float], after: tuple[float, float] = (100.0, 101.0)):
    """The mapped band: (100, 101) outside the block, `during_block` (a wrong R) inside it."""
    return lambda t: during_block if SPAN[0][0] <= t < SPAN[0][1] else after  # corrected by the block end


def test_a_real_cross_during_the_block_is_marked_at_the_block_end():
    """Support at 100-101; price falls through it while the chain is blocked and is still below at the block end."""
    t = np.array([0, 5, 20, 40, 60, 80, 90]) * NS
    p = np.array([102, 102, 99, 98, 98.5, 98.4, 98.6])
    s = bs.replay_cross(bs.NodeCross(side=+1), t, p, _band((100.0, 101.0)), SPAN)
    assert s.crossed_ns == 70 * NS  # at the block end itself, not at a later trade


def test_the_block_end_is_a_level_check_not_a_transition():
    """Price stays beyond after the block without trading back through the band: still crossed at once at 70 s."""
    s = bs.NodeCross(side=-1)  # resistance at 100-101, price below
    s.on_trade(0, 99.0, 100.0, 101.0, blocked=False)
    s.on_trade(30 * NS, 103.0, 100.0, 101.0, blocked=True)  # through it inside the block: no transition
    assert s.crossed_ns is None and s.paused
    assert s.resume(70 * NS, 103.0, 100.0, 101.0) and s.crossed_ns == 70 * NS and not s.paused


def test_a_wrong_band_during_the_block_leaves_no_lasting_cross():
    """A wrong R maps the band to 103-104 during the block, so price 102 reads as through it: the state pauses, and at
    the block end the band in force (100-101) has price above it, so nothing is marked; a later real cross counts."""
    t = np.array([0, 20, 40, 60, 80, 90]) * NS
    p = np.array([102, 102, 102, 102, 102, 99.5])
    s = bs.replay_cross(bs.NodeCross(side=+1), t, p, _band((103.0, 104.0)), SPAN)
    assert s.crossed_ns == 90 * NS


def test_a_cross_undone_inside_the_block_is_not_marked():
    t = np.array([0, 20, 40, 60, 80]) * NS
    p = np.array([102, 99, 99, 102, 102])
    s = bs.replay_cross(bs.NodeCross(side=+1), t, p, _band((100.0, 101.0)), SPAN)
    assert s.crossed_ns is None


def test_the_block_end_uses_the_last_trade_at_or_before_it_and_trades_after_it_are_live():
    """A trade exactly at the block end is still inside it (blocked), and is the price the block end judges."""
    t = np.array([0, 70, 75]) * NS
    p = np.array([102, 99, 102])
    s = bs.replay_cross(bs.NodeCross(side=+1), t, p, _band((100.0, 101.0)), SPAN)
    assert s.crossed_ns == 70 * NS


def test_r_dependent_features_are_null_inside_a_block_only():
    t = np.array([0, 10, 40, 70, 71]) * NS
    out = bs.null_in_blocks(np.array([1.0, 2.0, 3.0, 4.0, 5.0]), t, SPAN)
    assert np.isnan(out[1:4]).all() and out[0] == 1.0 and out[4] == 5.0


def test_a_guard_block_drives_the_pause_end_to_end():
    """The spans come from ChainGuard (the bot's R steps 0.25% at 100 s and back at 130 s): blocked from 100 s."""
    params = dg.GuardParams.from_features(load_features())
    n = 400
    t = np.arange(n, dtype=np.int64) * NS
    r_in = np.full(n, 41.0)
    r_in[100:130] = 41.0 * 1.0025
    spans = dg.block_spans(dg.run_session("SPY", params, t, np.full(n, 41.0), r_in))
    (a, b), = spans
    price = np.where(t < 150 * NS, 102.0, 99.0)  # through the support at 150 s, inside the block
    s = bs.replay_cross(bs.NodeCross(side=+1), t, price, lambda _t: (100.0, 101.0), spans)
    assert a == 100 * NS and s.crossed_ns == b
