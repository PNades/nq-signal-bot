"""The step-13 shared-event matcher (owner 2026-11-08, SPEC §13 Q-step13plan (4); golden zones Q-dryrun13 (5))."""

from nqbot.tuning.shared_events import match


def ev(eid, idx, d, founders=(), golden=False, lo=0.0, hi=0.0):
    return {"event_id": eid, "touch_idx": idx, "direction": d, "founding_level_ids": list(founders),
            "in_golden_zone": golden, "touch_zone_lo": lo, "touch_zone_hi": hi}


def test_same_trade_direction_and_a_common_founder():
    a = [ev("a1", 10, 1, ["poc", "vwap"]), ev("a2", 20, -1, ["pdh"])]
    b = [ev("b1", 10, 1, ["vwap", "node"]), ev("b2", 20, 1, ["pdh"]), ev("b3", 30, -1, ["pdh"])]
    assert match(a, b) == [("a1", "b1")]          # a2: other direction at 20, b3: another trade


def test_a_dropped_weak_member_still_matches():
    """`node_relevance_top_n` drops one weak node member: exact founder equality would call it a new event."""
    assert match([ev("a", 5, 1, ["poc", "qqq_node-600"])], [ev("b", 5, 1, ["poc"])]) == [("a", "b")]


def test_most_common_founders_win_and_a_tie_is_unmatched():
    a = [ev("a", 7, 1, ["x", "y"])]
    assert match(a, [ev("b1", 7, 1, ["x"]), ev("b2", 7, 1, ["x", "y"])]) == [("a", "b2")]
    assert match(a, [ev("b1", 7, 1, ["x"]), ev("b2", 7, 1, ["y"])]) == []


def test_golden_zone_events_match_on_overlapping_bands():
    a = [ev("g", 9, 1, golden=True, lo=100.0, hi=110.0)]
    assert match(a, [ev("h", 9, 1, golden=True, lo=105.0, hi=112.0)]) == [("g", "h")]
    assert match(a, [ev("h", 9, 1, golden=True, lo=111.0, hi=112.0)]) == []
    assert match(a, [ev("h", 9, 1, ["poc"])]) == []                      # a level event never matches a golden one
    # shallow and deep at one trade: the larger overlap wins
    b = [ev("s", 9, 1, golden=True, lo=100.0, hi=104.0), ev("d", 9, 1, golden=True, lo=104.0, hi=111.0)]
    assert match([ev("g", 9, 1, golden=True, lo=103.0, hi=110.0)], b) == [("g", "d")]


def test_one_to_one():
    a = [ev("a1", 3, 1, ["x"]), ev("a2", 3, 1, ["x"])]
    assert match(a, [ev("b", 3, 1, ["x"])]) == []                        # two events of A tie for one of B
