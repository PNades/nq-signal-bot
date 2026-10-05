"""1- and 5-minute bars from trades (SPEC §3.2; feature_spec F0.1: labelled by start, ET-clock aligned, no empty bars)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pyarrow.parquet as pq

from nqbot.clients.databento_contracts import cme_day_span
from nqbot.features.bars import build_bars, write_bars
from tests.databento_fakes import NS, et_ns

DAY = date(2025, 12, 16)


def bars(trades, minutes):
    ts, seq, px, size, side = zip(*trades)
    return build_bars(np.array(ts), np.array(seq), np.array(px), np.array(size), np.array(side, dtype="S1"),
                      minutes).to_pydict()


def test_one_minute_ohlcv_and_aggressor_volume():
    t = [
        (et_ns(DAY, 9, 31, 0), 0, 100.00, 2, "B"),
        (et_ns(DAY, 9, 31, 20), 1, 101.00, 1, "A"),
        (et_ns(DAY, 9, 31, 40), 2, 99.50, 3, "N"),
        (et_ns(DAY, 9, 31, 59) + NS - 1, 3, 100.25, 4, "B"),  # 09:31:59.999999999: still the 09:31 bar
        (et_ns(DAY, 9, 32, 0), 4, 102.00, 5, "A"),  # 09:32:00.000: the 09:32 bar
    ]
    b = bars(t, 1)
    assert [x.value for x in b["start"]] == [et_ns(DAY, 9, 31), et_ns(DAY, 9, 32)]
    assert (b["open"][0], b["high"][0], b["low"][0], b["close"][0]) == (100.0, 101.0, 99.5, 100.25)
    assert (b["volume"][0], b["n_trades"][0], b["buy_volume"][0], b["sell_volume"][0]) == (10, 4, 6, 1)
    assert (b["open"][1], b["close"][1], b["volume"][1], b["sell_volume"][1]) == (102.0, 102.0, 5, 5)


def test_same_timestamp_trades_follow_sequence_and_input_order_does_not_matter():
    ts = et_ns(DAY, 10, 0, 5)
    t = [(ts, 7, 200.0, 1, "B"), (ts, 5, 198.0, 1, "A"), (ts, 6, 199.0, 1, "B")]
    b = bars(t, 1)
    assert (b["open"][0], b["close"][0]) == (198.0, 200.0)


def test_empty_minutes_have_no_bar_and_five_minute_alignment():
    t = [
        (et_ns(DAY, 9, 34, 59), 0, 10.0, 1, "B"),
        (et_ns(DAY, 9, 35, 0), 1, 11.0, 1, "B"),
        (et_ns(DAY, 9, 39, 59), 2, 12.0, 1, "B"),
        (et_ns(DAY, 9, 50, 0), 3, 13.0, 1, "B"),  # 09:40 and 09:45 are empty
    ]
    one = bars(t, 1)
    assert len(one["start"]) == 4
    five = bars(t, 5)
    assert [x.value for x in five["start"]] == [et_ns(DAY, 9, 30), et_ns(DAY, 9, 35), et_ns(DAY, 9, 50)]
    assert five["open"][1] == 11.0 and five["close"][1] == 12.0


def test_session_starts_at_18_00_et_on_a_dst_day():
    # Monday 2026-03-09: the CME day starts Sunday 18:00 EDT; the first 5-minute bar is labelled 18:00 ET.
    day = date(2026, 3, 9)
    start, _ = cme_day_span(day)
    first = int(start.timestamp()) * NS
    t = [(first, 0, 1.0, 1, "B"), (first + 4 * 60 * NS + 30 * NS, 1, 2.0, 1, "A"), (first + 5 * 60 * NS, 2, 3.0, 1, "B")]
    five = bars(t, 5)
    assert [x.value for x in five["start"]] == [first, first + 5 * 60 * NS]
    assert five["start"][0].tz_convert("America/New_York").strftime("%Y-%m-%d %H:%M") == "2026-03-08 18:00"


def test_early_close_day_bars_end_with_the_last_trade():
    day = date(2025, 11, 28)  # early close 13:00 ET (CME futures trade to 13:15)
    t = [(et_ns(day, 12, 59, 30), 0, 5.0, 1, "B"), (et_ns(day, 13, 14, 59), 1, 6.0, 1, "A")]
    one = bars(t, 1)
    assert [x.value for x in one["start"]] == [et_ns(day, 12, 59), et_ns(day, 13, 14)]


def test_no_trades_no_bars_and_parquet_round_trip(tmp_path):
    empty = build_bars(np.array([], dtype=np.int64), np.array([]), np.array([]), np.array([]), np.array([], dtype="S1"), 1)
    assert empty.num_rows == 0
    table = build_bars(np.array([et_ns(DAY, 9, 30)]), np.array([0]), np.array([1.5]), np.array([2]),
                       np.array([b"B"]), 1)
    path = tmp_path / "bars.parquet"
    write_bars(path, table, symbol="NQZ5", day=DAY, minutes=1)
    back = pq.read_table(path)
    assert back.num_rows == 1 and b"nqbot" in back.schema.metadata
    assert back.column("close").to_pylist() == [1.5]
