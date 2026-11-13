"""Owner 2026-11-13 review before 13c: hand-built tests for code the reviewers found untested (no code change):
the 13d zone-book comparison, the `mbp-1` reader, the labeler's path summary and structures reached."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from nqbot.clients import mbp1
from nqbot.events.zone_compare import differing_trades
from nqbot.features.session_tape import NS
from nqbot.labeler.firsthit import PathIndex
from nqbot.labeler.outcomes import STOP, TARGET, Labeler


# --- events/zone_compare.differing_trades ------------------------------------------------------------------------------

def _book(times, snaps):
    return SimpleNamespace(times=list(times), snaps=list(snaps))


def test_differing_trades_counts_the_trades_inside_differing_stretches():
    ts = np.arange(0, 100, 10, dtype=np.int64)          # trades at 0, 10, ..., 90
    z1 = {"z1": ("a", "b")}
    a = _book([0, 50], [z1, {}])
    assert differing_trades(a, _book([0, 50], [z1, {}]), ts) == 0            # same books
    b = _book([0, 30, 60], [z1, {"z1": ("a",)}, {}])                          # members differ on [30, 50), zone gone at 50 vs 60
    # [30, 50): a has z1(a,b), b has z1(a) -> trades 30, 40; [50, 60): a {} vs b z1(a) -> trade 50; from 60 equal
    assert differing_trades(a, b, ts) == 3
    renamed = _book([0, 50], [{"z9": ("a", "b")}, {}])                        # the same members under another id
    assert differing_trades(a, renamed, ts) == 5                               # ids count (zone_diff_any)
    assert differing_trades(_book([], []), _book([], []), ts) == 0
    assert differing_trades(a, b, np.array([], dtype=np.int64)) == 0


def test_differing_trades_before_a_books_first_snapshot():
    ts = np.array([0, 5, 10, 15], dtype=np.int64)
    a = _book([0], [{"z1": ("a",)}])
    b = _book([10], [{"z1": ("a",)}])                    # b has no zones until 10
    assert differing_trades(a, b, ts) == 2                # trades 0 and 5


# --- clients/mbp1.load -----------------------------------------------------------------------------------------------

def test_mbp1_load_skips_action_n_maps_empty_sides_and_sorts(tmp_path, monkeypatch):
    import databento

    dt = np.dtype([("ts_event", "<u8"), ("action", "S1"), ("bid_px_00", "<i8"), ("ask_px_00", "<i8"),
                   ("bid_sz_00", "<u4"), ("ask_sz_00", "<u4")])
    S = int(mbp1.PRICE_SCALE)
    chunk1 = np.array([(300, b"A", 100 * S, 101 * S, 5, 6), (100, b"A", 99 * S, 100 * S, 1, 2)], dtype=dt)
    chunk2 = np.array([(200, b"N", 1 * S, 2 * S, 9, 9), (400, b"C", mbp1.UNDEF_PRICE, 102 * S, 0, 3)], dtype=dt)

    class Store:
        def to_ndarray(self, count):
            assert count == mbp1.CHUNK
            return iter([chunk1, chunk2])
    monkeypatch.setattr(databento.DBNStore, "from_file", staticmethod(lambda path: Store()))
    f = tmp_path / "mbp.dbn.zst"
    assert mbp1.load(f) is None                           # no file: no book (sessions before 2025-10-07)
    f.write_bytes(b"x")
    b = mbp1.load(f)
    assert list(b.ts) == [100, 300, 400]                  # sorted; the action-N record skipped
    assert b.at(50) is None                               # before the first record
    assert b.at(150) == (99.0, 100.0, 1.0, 2.0) and b.at(350) == (100.0, 101.0, 5.0, 6.0)
    bid, ask, bsz, asz = b.at(10**6)
    assert bid != bid and ask == 102.0 and asz == 3.0     # an empty bid side is NaN


# --- labeler/outcomes.Labeler.path_summary and reached -----------------------------------------------------------------

def _lab(prices):
    lab = object.__new__(Labeler)
    lab.tape = SimpleNamespace(ts=np.arange(len(prices), dtype=np.int64) * NS, price=np.asarray(prices, float))
    lab.path = PathIndex(np.asarray(prices, float))
    lab.close_i = len(prices)
    return lab


def test_path_summary_of_a_stopped_long_that_later_reached_its_target():
    lab = _lab([100, 98, 96, 94, 101, 106, 103])
    fill = SimpleNamespace(idx=0, px=100.0)
    out = lab.path_summary(fill, stop=96.0, target=105.0, d=+1, info=(STOP, 2, 2, None, 2))
    assert out["mae"] == 4.0 and out["mae_t"] == 2 * NS and out["mfe"] == 0.0
    assert out["overshoot"] == 2.0                        # 94 after the 96 stop
    assert out["stopped_then_target"] is True and out["stt_delay_sec"] == 3.0   # 106 >= 105 + 1 tick, 3 s later
    assert out["extension"] is None and out["returned_to_entry"] is None


def test_path_summary_of_a_short_that_hit_its_target_then_returned():
    lab = _lab([100, 101, 95, 92, 97, 100.5])
    fill = SimpleNamespace(idx=0, px=100.0)
    out = lab.path_summary(fill, stop=104.0, target=95.0, d=-1, info=(TARGET, 2, None, 2, 2))
    assert out["mae"] == 1.0 and out["mfe"] == 5.0        # up to 101 against, down to 95 by the exit
    assert out["extension"] == 3.0                        # on to 92 after the target
    assert out["returned_to_entry"] is True and out["rte_t"] == 5 * NS
    assert out["overshoot"] is None and out["stopped_then_target"] is None


def test_target_needs_a_trade_one_tick_through_for_stopped_then_target():
    lab = _lab([100, 96, 105, 104])                       # 105 exactly: not 1 tick through
    out = lab.path_summary(SimpleNamespace(idx=0, px=100.0), stop=96.0, target=105.0, d=+1,
                           info=(STOP, 1, 1, None, 1))
    assert out["stopped_then_target"] is False and out["stt_delay_sec"] is None


def test_reached_lists_structures_in_time_order():
    lab = _lab([100, 102, 104, 103, 108, 99])
    zones = [{"id": "far", "lo": 107.0, "hi": 109.0}, {"id": "near", "lo": 103.5, "hi": 105.0},
             {"id": "never", "lo": 120.0, "hi": 121.0}]
    got = lab.reached(SimpleNamespace(idx=0, px=100.0), zones, +1)
    assert got == [["near", 2 * NS], ["far", 4 * NS]]
    short = lab.reached(SimpleNamespace(idx=0, px=100.0), [{"id": "below", "lo": 97.0, "hi": 99.5}], -1)
    assert short == [["below", 5 * NS]]                   # a short reaches a zone below by its upper edge


@pytest.mark.parametrize("d", [+1, -1])
def test_path_summary_without_an_exit_inside_the_path(d):
    lab = _lab([100, 100.25, 99.75])
    out = lab.path_summary(SimpleNamespace(idx=0, px=100.0), stop=100 - d * 10, target=100 + d * 10, d=d,
                           info=("timeout", None, None, None, 3))
    assert out["mae"] == 0.25 and out["mfe"] == 0.25
