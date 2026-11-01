"""Regression tests for the independent review of step 11 part 2 (2026-10-31): each finding with its hand-built case
(SPEC §13 Q-step11d review note; feature_spec F0.7, F1.8, F2.1, F2.4, F2.7, F2.11, F2.14, F2.15, F2.19)."""

import pytest
from pytest import approx

from tests.test_node_levels import BOARD, KING, at, nodes, reference_rolls
from tests.test_skylit_session import KING_NQ, QQQ_CEIL, SPY_CEIL, session


def test_a1_rolls_wait_for_a_block_that_starts_between_boards():
    # boards every 20 s (NDXP's blocks come from QQQ board times); the block 25-35 s starts mid-segment
    boards = [(s, 755, {700: -3.0, 759: 1.0, 760: 2.0}) for s in range(0, 200, 20)]
    trades = [(0, 755), (30, 760.7), (31, 755), (150, 755)]
    mn = nodes(boards, trades, end_sec=300, blocks=[(at(25), at(35))])
    got = [(e.t, e.kind, e.old, e.new) for e in mn.ceiling.side.events]
    want = [(e.t, e.kind, e.old, e.new) for e in reference_rolls(mn, +1).events]
    assert got == want
    assert all(not (25 < e.t - at(0) / 1e9 < 35) for e in mn.ceiling.side.events)
    assert mn.ceiling.at(at(30.5))["confirmed"] == 760


def test_a2_beach_ball_null_inside_a_block():
    rd = 10
    mn = nodes([(0, 90, BOARD)], [(0, 89), (10, 101.25), (20, 100 - rd), (200, 89)], blocks=[(at(30), at(100))])
    assert mn.node_at(100.5, at(25))["beach_ball_count_today"] == 1
    x = mn.node_at(100.5, at(50))
    assert x["crossed"] is None and x["beach_ball_count_today"] is None and x["bb_minutes_since_reclaim"] is None
    assert mn.node_at(100.5, at(100))["beach_ball_count_today"] is None   # the block end instant (closed span)
    assert mn.node_at(100.5, at(101))["beach_ball_count_today"] == 1


def test_a3_beach_ball_depth_when_the_reclaim_is_judged_at_a_block_end():
    mn = nodes([(0, 90, BOARD)], [(0, 89), (10, 101.5), (15, 103), (50, 85), (150, 85)], blocks=[(at(30), at(100))])
    beach = mn.levels[100.5].beach
    assert len(beach) == 1 and beach[0]["t_reclaim"] == at(100)
    assert beach[0]["depth"] == approx(2.0)


def test_a4_beach_ball_seconds_beyond_skip_a_missing_strike():
    boards = [(0, 90, BOARD), (12, 90, dict(KING)), (30, 90, BOARD)]
    mn = nodes(boards, [(0, 89), (10, 101.25), (15, 103), (40, 103), (50, 89)])
    beach = mn.levels[100.5].beach
    assert len(beach) == 1 and beach[0]["sec_beyond"] == approx(40.0)


def test_a5_roll_growth_against_a_base_board_where_the_new_node_was_below_spot():
    boards = [(0, 758, {700: -3.0, 757: 1.0, 760: 2.0})]
    boards += [(s, 755, {700: -3.0, 757: 1.5, 760: 0.8}) for s in range(1, 6)]
    boards += [(s, 755, {700: -3.0, 757: 1.5, 760: 0.8}) for s in range(20, 120)]
    mn = nodes(boards, [(0, 755), (110, 755)], end_sec=200)
    got = [(e.t, e.kind, e.old, e.new, e.old_shrink_pct, e.new_growth_pct) for e in mn.ceiling.side.events]
    want = [(e.t, e.kind, e.old, e.new, e.old_shrink_pct, e.new_growth_pct) for e in reference_rolls(mn, +1).events]
    assert got == want
    rolls = [e for e in mn.ceiling.side.events if e.kind == "roll"]
    assert rolls and rolls[0].new_growth_pct == approx(0.5)


def test_a6_minutes_since_relevant_kept_over_a_missing_board():
    q = [(0, 90, {**KING_NQ, 100.5: 0.5}), (100, 90, dict(KING_NQ)), (101, 90, {**KING_NQ, 100.5: 0.5})]
    s = session([(0, 89), (200, 89)], [(0, 40)], qqq=q)
    x = [r for r in s.registry_at(at(160)) if r["strike"] == 100.5][0]
    assert x["minutes_since_relevant"] == approx(160 / 60)
    q2 = [(0, 90, {**KING_NQ, 100.5: 0.5}), (100, 90, {**KING_NQ, 100.5: 0.1}), (101, 90, {**KING_NQ, 100.5: 0.5})]
    s2 = session([(0, 89), (200, 89)], [(0, 40)], qqq=q2)
    x2 = [r for r in s2.registry_at(at(160)) if r["strike"] == 100.5][0]
    assert x2["minutes_since_relevant"] == approx(59 / 60)                 # not relevant at 100 s: restarts at 101


def test_a7_pika_flags_mean_cloud_membership():
    lone = [(0, 90, {300.5: 1.0, 200.0: -0.05, 100.5: 0.5})]                # a positive relevant node, no cloud
    spy = [(0, 45, {150.5: -1.0, 50.5: 0.5})]       # SPY's node alone too (a negative King next to it)
    s = session([(0, 89), (10, 97)], [(0, 40), (10, 49)], qqq=lone, spy=spy)
    assert s.trinity(at(10), at(10), "ceiling")["trinity_spy"] is True
    x = [r for r in s.registry_at(at(10)) if r["strike"] == 100.5][0]
    assert x["in_pika_cloud_member"] is False
    assert s.trinity(at(10), at(10), "ceiling")["aligned_has_pika"] is False
    cloud = [(0, 90, {300.5: -1.0, 100.5: 0.5, 101.5: 0.4})]                # 100.5 and 101.5 adjacent, positive
    s2 = session([(0, 89), (10, 97)], [(0, 40)], qqq=cloud)
    x2 = [r for r in s2.registry_at(at(10)) if r["strike"] == 100.5][0]
    assert x2["in_pika_cloud_member"] is True


def test_a8_trinity_null_for_a_map_with_no_board_yet():
    spx_late = [(60, 45, {500.0: 1.0, 400.0: 0.5})]                         # SPX's first applied board at 60 s
    s = session([(0, 89), (5, 100.5)], [(0, 40), (5, 50.5)], qqq=QQQ_CEIL, ndxp=QQQ_CEIL, spy=SPY_CEIL, spx=spx_late)
    tr = s.trinity(at(10), at(10), "ceiling")
    assert tr["trinity_spx"] is None and tr["trinity_count"] is None and tr["maps_with_node_here"] is None
    assert tr["trinity_qqq"] is True and tr["trinity_spy"] is True


@pytest.mark.parametrize("tab,want", [({700.0: 1.0, 750.0: 0.5}, None),                   # no node ahead, no pocket
                                      ({700.0: 1.0, 750.0: 0.5, 760.0: 0.5}, 0)])         # a node ahead, no pocket
def test_a9_pocket_ahead_null_without_a_node_or_pocket_ahead(tab, want):
    s = session([(0, 750.5)], [(0, 40)], qqq=[(0, 750.5, tab)])
    assert s.pockets("QQQ", at(5), +1)["pocket_ahead_size"] == want


def test_c2_sp_maps_refuse_a_missing_es_scale():
    with pytest.raises(ValueError):
        nodes([(0, 90, BOARD)], [(0, 89), (10, 89)], name="SPY")


# --- owner answers to the review's questions (2026-10-31, SPEC §13 Q-step11d (8)-(11)) -----------------------

def test_q8_sp_null_when_the_nasdaq_leg_was_blocked_in_the_window():
    n = 300
    r = [1.0] * 20 + [1.0025] * (n - 20)                                 # QQQ's guard fires at 20 s (F1.8)
    qqq = [(t, 90, {**KING_NQ, 100.5: 0.5}) for t in range(n)]
    s = session([(0, 89), (10, 97), (600, 98)], [(0, 40), (600, 40)], qqq=qqq, spy=SPY_CEIL,
                spx=[(0, 45, {500.0: 1.0, 400.0: 0.5})], r={"QQQ": r}, end=900)
    assert not s.blocked("SPY", at(30)) and s.blocked("QQQ", at(30))
    assert s.trinity(at(10), at(30), "ceiling")["trinity_spy"] is None
    assert s.trinity(at(1), at(15), "ceiling")["trinity_spy"] is False   # the whole window before the block


def test_q9_multiexp_stale_cutoff_follows_an_early_close():
    from datetime import date, time

    from nqbot.config import load_features
    from nqbot.features import multiexp as mx
    from tests.test_multiexp import doc, qqq_root

    f = load_features()
    day, prev = date(2025, 12, 1), date(2025, 11, 28)                   # 11-28 closed at 13:00
    ok = mx.parse(doc([qqq_root("2025-11-28T17:59:30Z")]), "0925", day, prev, f, time(13, 0))   # 12:59:30 ET
    assert ok.roots["QQQ"] is not None
    stale = mx.parse(doc([qqq_root("2025-11-28T17:58:00Z")]), "0925", day, prev, f, time(13, 0))
    assert stale.roots["QQQ"] is None
    assert mx.parse(doc([qqq_root("2025-11-28T17:59:30Z")]), "0925", day, prev, f).roots["QQQ"] is None  # 15:59 rule


def test_q10_pocket_gap_skips_the_node_at_price():
    tab = {700.0: 1.0, 750.0: 0.5, 752.0: 0.02, 753.0: 0.03, 754.0: 0.02, 760.0: 0.5}
    s = session([(0, 749.8)], [(0, 40)], qqq=[(0, 749.8, tab)])           # price inside 750's band [749.5, 750.5]
    p = s.pockets("QQQ", at(5), +1)
    assert p["pocket_ahead_size"] == 3 and p["pocket_ahead_pts"] == approx(3.0)


def test_q11_a_flip_bar_that_formed_partly_while_frozen_is_not_judged():
    from tests.test_level_state import at as lat
    from tests.test_level_state import make_frozen

    # crossed 10:00:10; the 10:05 bar (10:05-10:10) would flip at 10:10, but the node is frozen 10:06:40-10:08:20
    trades = [(0, 89), (10, 101.25), (300, 101.5), (590, 102), (610, 102), (890, 102.5), (950, 102.5)]
    lv = make_frozen(trades, 100, 101, freezes=[(400, 500)])
    assert lv.at(lat(650))["flip_count"] == 0
    assert lv.at(lat(900))["flip_count"] == 1                            # the next full bar (10:10-10:15) flips
    assert make_frozen(trades, 100, 101).at(lat(650))["flip_count"] == 1


def test_owner_frozen_moments_never_create_trinity_overlaps():
    """Owner 2026-11-01 (SPEC §13 Q-step11e): Trinity needs a RELEVANT node in the role; an overlap found while the
    node was relevant keeps counting at a later t ([touch, t] rule), but moments while it is frozen (not relevant or
    missing from the board) never create one."""
    nq = [(0, 89), (10, 97), (60, 98)]
    # SPY's node relevant until 12 s, under 20% of King from 12 s: ES reaches its band at 15 s (frozen) -> no overlap
    spy = [(t, 45, {150.5: -1.0, 50.5: 0.5 if t < 12 else 0.1}) for t in range(40)]
    s = session(nq, [(0, 40), (15, 49)], qqq=QQQ_CEIL, spy=spy, spx=[(0, 45, {500.0: 1.0, 400.0: 0.5})])
    assert s.trinity(at(10), at(30), "ceiling")["trinity_spy"] is False
    # ES inside at 10 s while still relevant -> the overlap counts at 30 s although the node is frozen by then
    s2 = session(nq, [(0, 40), (10, 49)], qqq=QQQ_CEIL, spy=spy, spx=[(0, 45, {500.0: 1.0, 400.0: 0.5})])
    assert s2.trinity(at(10), at(30), "ceiling")["trinity_spy"] is True
    # the strike missing from the boards 12-30 s: ES reaching the band at 15 s doesn't count either
    gone = [(t, 45, {150.5: -1.0, 50.5: 0.5} if (t < 12 or t >= 30) else {150.5: -1.0}) for t in range(40)]
    s3 = session(nq, [(0, 40), (15, 49), (31, 44)], qqq=QQQ_CEIL, spy=gone, spx=[(0, 45, {500.0: 1.0, 400.0: 0.5})])
    assert s3.trinity(at(10), at(29), "ceiling")["trinity_spy"] is False
