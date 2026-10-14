"""Mapping-dislocation guard (F1.8, SPEC §5.1/§9, §13 Q-dislguard; owner 2026-10-14): hand-built examples."""

import numpy as np
import pytest

from nqbot.config import load_features
from nqbot.features import disloc_guard as dg

NS = dg.NS
R0 = 41.0
BP = R0 * 1e-4  # one basis point of R


@pytest.fixture
def params():
    return dg.GuardParams.from_features(load_features())


def _boards(n: int) -> tuple[np.ndarray, np.ndarray]:
    """n one-second boards with a flat R (the bot's R flat too unless a test says otherwise)."""
    return np.arange(n, dtype=np.int64) * NS, np.full(n, R0)


def _run(params, t, r, r_in=None):
    return dg.run_session("QQQ", params, t, r, np.full(len(t), R0) if r_in is None else r_in)


def test_params_are_the_owners(params):
    assert (params.pct, params.window_s, params.confirm_s, params.hold_s, params.gap_s) == (0.002, 120, 10, 60, 10)


def test_a_lone_spike_never_fires(params):
    t, r = _boards(200)
    r[100] = R0 + 50 * BP  # one board 50 bps up, the next back
    assert _run(params, t, r) == []


def test_a_lone_dip_never_fires(params):
    t, r = _boards(200)
    r[100] = R0 - 50 * BP
    assert _run(params, t, r) == []


def test_a_confirmed_jump_fires_at_its_second_board(params):
    t, r = _boards(200)
    r[100:] = R0 + 25 * BP  # a real 25-bp shift, held by every later board
    fires = _run(params, t, r)
    assert [(f.trigger, f.start_ns) for f in fires] == [(dg.TRIGGER_BOARD, 101 * NS)]
    assert fires[0].size == pytest.approx(25e-4, rel=0.02)


def test_a_jump_whose_starting_level_is_not_held_does_not_fire(params):
    """2026-02-20 QQQ: one board dips 19.8 bps, the next is back; the 'move' from that lone low must not fire."""
    t, r = _boards(200)
    r[100] = R0 - 19.8 * BP
    r[101:] = R0 + 1.0 * BP  # 20.8 bps above the lone low, but only ~1 bp above the held level
    assert _run(params, t, r) == []
    # the same rebound from a low that two boards held fires once its high is held too (boards 101 and 102)
    r[99] = R0 - 19.8 * BP
    assert [f.start_ns for f in _run(params, t, r)] == [102 * NS]


def test_two_way_stale_episodes_held_by_several_boards_fire(params):
    """2025-11-18 QQQ: R held +10.9 bps, then -10.9 bps 40 s later: 21.8 bps apart within 2 minutes."""
    t, r = _boards(200)
    r[60:64] = R0 + 10.9 * BP
    r[100:104] = R0 - 10.9 * BP
    fires = _run(params, t, r)
    assert [f.start_ns for f in fires] == [101 * NS] and fires[0].trigger == dg.TRIGGER_BOARD


def test_the_bots_r_trigger_fires_on_its_own(params):
    """Backstop: the R in force moves 0.25% (its range in the window), whatever the per-board R does."""
    t, r = _boards(200)
    r_in = np.full(200, R0)
    r_in[120:] = R0 * 1.0025
    fires = _run(params, t, r, r_in)
    assert [(f.trigger, f.start_ns) for f in fires] == [(dg.TRIGGER_BOT, 120 * NS)]
    assert fires[0].size == pytest.approx(0.0025, rel=0.01)


def test_the_hold_blocks_until_60_s_after_the_last_flagged_board(params):
    t, r = _boards(400)
    r[100:] = R0 + 25 * BP
    g = dg.ChainGuard("QQQ", params)
    blocked = []
    for k in range(400):
        g.on_board(int(t[k]), float(r[k]), R0)
        blocked.append(g.blocked(int(t[k])))
    # flagged from board 101 while a held low of the old level (boards 1-99) is inside the trailing 2 minutes:
    # the last flagged board is 218 (window (98, 218] still holds board 99)
    f = g.fires[0]
    last_flag = f.last_flag_ns // NS
    assert last_flag == 218 and len(g.fires) == 1
    assert not blocked[100] and blocked[101]
    assert f.end_ns == f.last_flag_ns + 60 * NS
    assert blocked[last_flag + 60] and not blocked[last_flag + 61]
    assert not g.blocked(f.end_ns + 1) and g.blocked(f.end_ns)
    spans = dg.block_spans(g.fires)
    assert spans == [(101 * NS, f.end_ns)]
    assert dg.in_block(spans, np.array([100, 101, last_flag + 60, last_flag + 61]) * NS).tolist() == [
        False, True, True, False]


def test_either_trigger_starts_a_block_and_overlapping_fires_merge(params):
    t, r = _boards(300)
    r[100:] = R0 + 25 * BP
    r_in = np.full(300, R0)
    r_in[150:] = R0 * 1.0025
    fires = _run(params, t, r, r_in)
    assert [f.trigger for f in fires] == [dg.TRIGGER_BOARD, dg.TRIGGER_BOT]  # each fire logged with its trigger
    spans = dg.block_spans(fires)
    assert len(spans) == 1 and spans[0][0] == 101 * NS and spans[0][1] == max(f.end_ns for f in fires)
    assert "QQQ" in fires[0].note() and "per-board" in fires[0].note() and "bot's R" in fires[1].note()


def test_a_gap_resets_both_windows(params):
    """Boards before a gap longer than ratio_gap_reset_sec never pair with boards after it."""
    t = np.concatenate([np.arange(50), 70 + np.arange(50)]).astype(np.int64) * NS
    r = np.full(100, R0)
    r[50:] = R0 + 25 * BP  # the shift happens across the 21-s gap
    r_in = r.copy()
    assert _run(params, t, r, r_in) == []
