"""1-minute and 5-minute bars built from trades (SPEC §3.2; feature_spec F0.1).

Bars are labelled by their start and known at start + length (the 09:31 1-minute bar covers
[09:31:00, 09:32:00) and is known at 09:32:00). They are clock-aligned to ET minutes: ET's UTC offset is a
whole number of hours, so flooring UTC nanoseconds to the bar length gives the same boundaries. A bar with
no trades doesn't exist. Trades sharing a `ts_event` are taken in exchange sequence order. Volume is split
by the exchange aggressor side (B = buy, A = sell; N counts in volume only).
"""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from nqbot.storage import atomic_write_bytes

NS_PER_MIN = 60_000_000_000
PRICE_SCALE = 1e9  # Databento fixed-point prices: units of 1e-9; divide (exact for every tick), never multiply by 1e-9
BAR_MINUTES = (1, 5)  # §3.2: 1-minute and 5-minute bars from trades
COMPRESSION = "zstd"
BAR_SCHEMA = pa.schema([
    ("start", pa.timestamp("ns", tz="UTC")), ("open", pa.float64()), ("high", pa.float64()), ("low", pa.float64()),
    ("close", pa.float64()), ("volume", pa.int64()), ("n_trades", pa.int64()), ("buy_volume", pa.int64()),
    ("sell_volume", pa.int64()),
])


def build_bars(ts_event_ns: np.ndarray, sequence: np.ndarray, price: np.ndarray, size: np.ndarray,
               side: np.ndarray, minutes: int) -> pa.Table:
    """Trades (price as float points) -> one row per bar with trades."""
    ts = np.asarray(ts_event_ns, dtype=np.int64)
    if len(ts) == 0:
        return BAR_SCHEMA.empty_table()
    order = np.lexsort((np.asarray(sequence, dtype=np.int64), ts))  # by ts_event, then sequence
    ts = ts[order]
    px = np.asarray(price, dtype=np.float64)[order]
    sz = np.asarray(size, dtype=np.int64)[order]
    sd = np.asarray(side).astype("S1")[order]
    length = minutes * NS_PER_MIN
    start = ts - np.mod(ts, length)
    first = np.flatnonzero(np.r_[True, start[1:] != start[:-1]])
    last = np.r_[first[1:], len(ts)] - 1
    return pa.Table.from_arrays([
        pa.array(start[first], type=pa.int64()).cast(pa.timestamp("ns", tz="UTC")),
        pa.array(px[first]),
        pa.array(np.maximum.reduceat(px, first)),
        pa.array(np.minimum.reduceat(px, first)),
        pa.array(px[last]),
        pa.array(np.add.reduceat(sz, first)),
        pa.array(np.diff(np.r_[first, len(ts)]).astype(np.int64)),
        pa.array(np.add.reduceat(np.where(sd == b"B", sz, 0), first)),
        pa.array(np.add.reduceat(np.where(sd == b"A", sz, 0), first)),
    ], schema=BAR_SCHEMA)


def bars_from_records(records: np.ndarray, minutes: int) -> pa.Table:
    """Databento `trades` records (structured array) -> bars."""
    return build_bars(records["ts_event"], records["sequence"], records["price"] / PRICE_SCALE, records["size"],
                      records["side"], minutes)


def write_bars(path: Path, table: pa.Table, *, symbol: str, day: date, minutes: int) -> None:
    meta = {b"nqbot": json.dumps({"symbol": symbol, "day": day.isoformat(), "minutes": minutes}).encode()}
    buf = io.BytesIO()
    pq.write_table(table.replace_schema_metadata(meta), buf, compression=COMPRESSION)
    atomic_write_bytes(path, buf.getvalue())
