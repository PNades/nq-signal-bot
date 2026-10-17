"""Owner 2026-10-14 (SPEC §9, §13 Q-mapstale): per-map board freshness, live only."""

from __future__ import annotations

import logging

import pytest

from nqbot.config import load_settings
from nqbot.live.map_freshness import NS, MapFreshness, blocks_from_arrivals

T0 = 1_000 * NS


def _f():
    return MapFreshness(["NDXP", "QQQ", "SPY"], stale_s=5, feed_fail_s=45, session_start_ns=T0, confirm_s=10)


def test_a_stale_map_blocks_entries_on_its_zones_only():
    f = _f()
    for m in ("NDXP", "QQQ", "SPY"):
        f.on_board(m, T0)
    for i in range(1, 7):
        f.on_board("QQQ", T0 + i * NS)  # QQQ keeps updating, NDXP and SPY go silent
    now = T0 + 6 * NS
    assert f.poll(now) == ["NDXP", "SPY"]
    assert not f.entry_allowed(["NDXP"], now) and not f.entry_allowed(["QQQ", "NDXP"], now)
    assert f.entry_allowed(["QQQ"], now)
    assert f.entry_allowed([], now)  # a zone of chart levels only (VWAP, POC, ...) has no map
    assert f.entry_allowed(["NDXP"], T0 + 5 * NS)  # exactly 5 s old is still fresh


def test_pending_entries_stay_until_the_map_passes_skylit_stale_sec():
    f = _f()
    f.on_board("NDXP", T0)
    assert f.keep_pending(["NDXP"], T0 + 30 * NS)  # stale, but placed on a fresh map and bracketed: stays
    assert not f.keep_pending(["NDXP"], T0 + 46 * NS)  # past 45 s: the feed-failure rule applies to this map
    assert f.failed("NDXP", T0 + 46 * NS) and not f.failed("NDXP", T0 + 45 * NS)


def test_every_block_is_logged_and_counted(caplog):
    f = _f()
    for m in ("NDXP", "QQQ", "SPY"):
        f.on_board(m, T0)
    with caplog.at_level(logging.INFO, logger="nqbot.live.map_freshness"):
        f.poll(T0 + 7 * NS)  # NDXP, QQQ, SPY all turn stale: three blocks open
        assert f.on_board("NDXP", T0 + 13 * NS) is None  # NDXP's next board after 13 s: the first, unconfirmed
        for m in ("QQQ", "SPY"):
            f.on_board(m, T0 + 13 * NS)
            f.on_board(m, T0 + 14 * NS)
        block = f.on_board("NDXP", T0 + 14 * NS)  # the second board, 1 s later: confirmed, the block was 5 -> 14 s
        f.on_board("NDXP", T0 + 15 * NS)  # 1 s later: no block
    assert block.map == "NDXP" and block.length_s == pytest.approx(9.0) and block.first_board_ns == T0 + 13 * NS
    assert "map NDXP: first board after 13.0 s without one" in caplog.text
    assert "map NDXP confirmed by a second board 1.0 s after the first: blocked for 9.0 s" in caplog.text
    assert "map NDXP stale: no board for 7.0 s" in caplog.text
    s = f.summary()
    assert s["NDXP"] == {"blocks": 1, "total_s": 9.0, "longest_s": 9.0, "confirm_wait_s": 1.0}
    assert s["QQQ"]["blocks"] == 1


def test_recording_replay_counts_the_same_blocks_and_closes_at_the_end():
    arrivals = [("NDXP", T0 + i * NS) for i in range(10)]  # every second to +9 s
    arrivals += [("NDXP", T0 + 22 * NS), ("NDXP", T0 + 23 * NS)]  # silent 13 s, then two boards: one 9 s block
    arrivals += [("NDXP", T0 + 40 * NS)]  # silent 17 s, then one board only: blocked to the end (+50 s)
    arrivals += [("QQQ", T0 + i * NS) for i in range(51)]  # every second, never stale
    f = blocks_from_arrivals(arrivals, ["NDXP", "QQQ"], 5, 45, T0, T0 + 50 * NS, 10)
    s = f.summary()
    # NDXP: +14 -> +23 (9 s), then +28 -> the end at +50 (22 s, never confirmed: 10 s past its first board at +40)
    assert s["NDXP"]["blocks"] == 2 and s["NDXP"]["total_s"] == pytest.approx(31.0)
    assert s["NDXP"]["longest_s"] == pytest.approx(22.0) and s["NDXP"]["confirm_wait_s"] == pytest.approx(11.0)
    assert s["QQQ"]["blocks"] == 0


def test_a_map_with_no_board_counts_from_the_session_start():
    f = _f()
    assert f.poll(T0 + 6 * NS) == ["NDXP", "QQQ", "SPY"]


def test_thresholds_are_checked():
    with pytest.raises(ValueError):
        MapFreshness(["NDXP"], stale_s=45, feed_fail_s=45, session_start_ns=T0, confirm_s=10)
    with pytest.raises(ValueError):
        MapFreshness(["NDXP"], stale_s=5, feed_fail_s=45, session_start_ns=T0, confirm_s=0)
    s = load_settings()
    assert s["skylit_map_stale_sec"] == 5 and s["skylit_map_stale_sec"] < s["skylit_stale_sec"]


def test_after_a_block_the_map_waits_for_a_second_board_within_the_confirm_window(caplog):
    """Owner 2026-10-17 (§13 Q-dislguard-2 (b)(ii)): the first board after a gap is unchecked by the dislocation guard,
    so the map stays blocked until a second board arrives within disl_guard_confirm_sec (10 s) of the first."""
    f = _f()
    f.on_board("SPY", T0)
    with caplog.at_level(logging.INFO, logger="nqbot.live.map_freshness"):
        assert f.on_board("SPY", T0 + 20 * NS) is None  # first board after 20 s
        assert not f.entry_allowed(["SPY"], T0 + 21 * NS)  # its board is 1 s old, but unconfirmed: still blocked
        assert f.on_board("SPY", T0 + 32 * NS) is None  # 12 s later: not within 10 s, this is the new first board
        assert not f.entry_allowed(["SPY"], T0 + 33 * NS)
        block = f.on_board("SPY", T0 + 40 * NS)  # 8 s after the new first: confirmed
    assert f.entry_allowed(["SPY"], T0 + 40 * NS)
    assert block.start_ns == T0 + 5 * NS and block.end_ns == T0 + 40 * NS and block.first_board_ns == T0 + 32 * NS
    assert "not confirmed within 10.0 s" in caplog.text and "confirmed by a second board 8.0 s after" in caplog.text
    assert len(f.blocks) == 1  # one block, logged once when it ends


def test_boards_without_a_gap_need_no_confirmation():
    f = _f()
    for i in range(10):
        f.on_board("QQQ", T0 + i * NS)
        assert f.entry_allowed(["QQQ"], T0 + i * NS)
    assert f.blocks == []


def test_the_confirm_window_is_the_guards():
    from nqbot.config import load_features
    assert load_features()["mapping"]["disl_guard_confirm_sec"] == 10
