"""F2.4 relevant nodes with the 20% hold (SPEC §5.2, owner 2026-10-11): hand-built boards."""

import numpy as np

from nqbot.features import nodes

ENTER, DROP = 0.25, 0.20


def test_f24_example():
    # feature_spec F2.4 test: 0.24 -> 0.26 (becomes relevant) -> 0.22 (stays, held) -> 0.19 (drops)
    rel, held = nodes.relevance(np.arange(4), np.full(4, 600.0), np.array([0.24, 0.26, 0.22, 0.19]),
                                np.ones(4, bool), ENTER, DROP)
    assert rel.tolist() == [False, True, True, False]
    assert held.tolist() == [False, False, True, False]


def test_exactly_at_the_cutoffs():
    # 0.25 becomes relevant (>=); 0.20 stays (drops only below 0.20); a fresh 0.22 is not relevant
    rel, held = nodes.relevance(np.arange(3), np.full(3, 600.0), np.array([0.25, 0.20, 0.199]),
                                np.ones(3, bool), ENTER, DROP)
    assert rel.tolist() == [True, True, False] and held.tolist() == [False, True, False]
    rel, _ = nodes.relevance(np.array([0]), np.array([600.0]), np.array([0.22]), np.ones(1, bool), ENTER, DROP)
    assert rel.tolist() == [False]


def test_strikes_are_tracked_apart_and_a_missing_strike_keeps_its_state():
    # board 0: 600 at 0.30, 601 at 0.22; board 1: only 601 (0.30); board 2: 600 at 0.21 (absent on board 1: held)
    b = np.array([0, 0, 1, 2, 2])
    k = np.array([600.0, 601.0, 601.0, 600.0, 601.0])
    p = np.array([0.30, 0.22, 0.30, 0.21, 0.10])
    rel, held = nodes.relevance(b, k, p, np.ones(3, bool), ENTER, DROP)
    assert rel.tolist() == [True, False, True, True, False]
    assert held.tolist() == [False, False, False, True, False]


def test_boards_not_applied_neither_count_nor_change_the_state():
    # board 1 is not applied (e.g. before the open skip): its 0.10 does not drop the node, and its rows are not relevant
    rel, held = nodes.relevance(np.arange(3), np.full(3, 600.0), np.array([0.30, 0.10, 0.22]),
                                np.array([True, False, True]), ENTER, DROP)
    assert rel.tolist() == [True, False, True] and held.tolist() == [False, False, True]


def test_rows_out_of_order_and_no_king():
    # rows need not be sorted; a NaN % of King (no King on that board) keeps the state
    b = np.array([2, 0, 1])
    rel, held = nodes.relevance(b, np.full(3, 600.0), np.array([0.21, 0.30, np.nan]), np.ones(3, bool), ENTER, DROP)
    assert rel.tolist() == [True, True, True] and held.tolist() == [True, False, True]
