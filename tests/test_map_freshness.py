"""Owner 2026-10-14 (SPEC §9, §13 Q-mapstale): per-map board freshness, live only."""

from __future__ import annotations

import logging

import pytest

from nqbot.config import load_settings
from nqbot.live.map_freshness import NS, MapFreshness, blocks_from_arrivals

T0 = 1_000 * NS


def _f():
    return MapFreshness(["NDXP", "QQQ", "SPY"], stale_s=5, feed_fail_s=45, session_start_ns=T0)


def test_a_stale_map_blocks_entries_on_its_zones_only():
    f = _f()
    for m in ("NDXP", "QQQ", "SPY"):
        f.on_board(m, T0)
    f.on_board("QQQ", T0 + 6 * NS)  # QQQ keeps updating, NDXP and SPY go silent
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
        block = f.on_board("NDXP", T0 + 13 * NS)  # NDXP's next board after 13 s: its block was 8 s
        f.on_board("QQQ", T0 + 13 * NS)
        f.on_board("SPY", T0 + 13 * NS)
        f.on_board("NDXP", T0 + 14 * NS)  # 1 s later: no block
    assert block.map == "NDXP" and block.length_s == pytest.approx(8.0)
    assert "map NDXP stale for 8.0 s" in caplog.text and "map NDXP stale: no board for 7.0 s" in caplog.text
    s = f.summary()
    assert s["NDXP"] == {"blocks": 1, "total_s": 8.0, "longest_s": 8.0} and s["QQQ"]["blocks"] == 1


def test_recording_replay_counts_the_same_blocks_and_closes_at_the_end():
    arrivals = [("NDXP", T0 + i * NS) for i in range(10)]  # every second to +9 s
    arrivals += [("NDXP", T0 + 22 * NS)]  # silent 13 s: one 8 s block
    arrivals += [("QQQ", T0 + i * NS) for i in range(31)]  # every second, never stale
    f = blocks_from_arrivals(arrivals, ["NDXP", "QQQ"], 5, 45, T0, T0 + 30 * NS)
    s = f.summary()
    # NDXP: 8 s after +9 s, then from +22 s silent to the end (+30 s): stale from +27 s, 3 s more
    assert s["NDXP"]["blocks"] == 2 and s["NDXP"]["total_s"] == pytest.approx(11.0)
    assert s["NDXP"]["longest_s"] == pytest.approx(8.0) and s["QQQ"]["blocks"] == 0


def test_a_map_with_no_board_counts_from_the_session_start():
    f = _f()
    assert f.poll(T0 + 6 * NS) == ["NDXP", "QQQ", "SPY"]


def test_thresholds_are_checked():
    with pytest.raises(ValueError):
        MapFreshness(["NDXP"], stale_s=45, feed_fail_s=45, session_start_ns=T0)
    s = load_settings()
    assert s["skylit_map_stale_sec"] == 5 and s["skylit_map_stale_sec"] < s["skylit_stale_sec"]
