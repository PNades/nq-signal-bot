"""A hand-built fake of `databento.Historical` for unit tests: no network, no charges, ever.

`timeseries.get_range(path=...)` writes a real zstd DBN file (metadata + records built with databento_dbn), so the
code under test reads it back through the real DBNStore path.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import databento_dbn as d
import zstandard

ET = ZoneInfo("America/New_York")
FIX = 1_000_000_000  # fixed-point prices (1e-9)
NS = 1_000_000_000
BASE = {"NQZ5": 20000.0, "NQH6": 20250.0, "MNQZ5": 20000.0, "MNQH6": 20250.0, "ESZ5": 6000.0, "ESH6": 6050.0}
INSTRUMENT = {"NQZ5": 1001, "NQH6": 1002, "ESZ5": 2001, "ESH6": 2002, "MNQZ5": 3001, "MNQH6": 3002}
USD_PER_DAY = {"trades": 1.0, "ohlcv-1s": 0.1, "mbp-1": 2.0}
TRADE_EVERY_SEC = 30 * 60


def ns(text: str) -> int:
    t = datetime.fromisoformat(text)
    return int(t.astimezone(timezone.utc).timestamp()) * NS


def dbn_bytes(schema: str, symbol: str, start_ns: int, end_ns: int, records: list[Any]) -> bytes:
    md = d.Metadata(dataset="GLBX.MDP3", start=start_ns, end=end_ns, stype_in=d.SType.RAW_SYMBOL,
                    stype_out=d.SType.INSTRUMENT_ID, schema=d.Schema.from_str(schema), symbols=[symbol],
                    partial=[], not_found=[], mappings=[])
    return zstandard.ZstdCompressor().compress(bytes(md) + b"".join(bytes(r) for r in records))


def trade(iid: int, ts: int, px: float, size: int = 1, side: str = "B", seq: int = 0) -> d.TradeMsg:
    s = {"B": d.Side.BID, "A": d.Side.ASK, "N": d.Side.NONE}[side]
    return d.TradeMsg(1, iid, ts, round(px * FIX), size, d.Action.TRADE, s, 0, ts + 1, sequence=seq)


def bar_1s(iid: int, ts: int, close: float) -> d.OHLCVMsg:
    c = round(close * FIX)
    return d.OHLCVMsg(d.RType.OHLCV_1S, 1, iid, ts, c, c, c, c, 1)


def default_records(schema: str, symbol: str, start_ns: int, end_ns: int) -> list[Any]:
    """A trade (or 1-second bar) every 30 minutes; 1-second bars also at the last two seconds before `end`
    (close BASE+1, BASE+2) and one starting exactly at `end` (outside the request, never sent)."""
    iid, base = INSTRUMENT[symbol], BASE[symbol]
    times = list(range(start_ns, end_ns, TRADE_EVERY_SEC * NS))
    if schema == "trades":
        return [trade(iid, t, base + (i % 4) * 0.25, seq=i) for i, t in enumerate(times)]
    if schema == "ohlcv-1s":
        return ([bar_1s(iid, t, base) for t in times if t < end_ns - 2 * NS]
                + [bar_1s(iid, end_ns - 2 * NS, base + 1), bar_1s(iid, end_ns - NS, base + 2)])
    if schema == "mbp-1":
        px = round(base * FIX)
        lvl = d.BidAskPair(bid_px=px, ask_px=px + FIX // 4, bid_sz=5, ask_sz=7)
        return [d.MBP1Msg(1, iid, start_ns, px, 0, d.Action.ADD, d.Side.BID, 0, start_ns + 1, levels=lvl)]
    raise ValueError(schema)


class _Symbology:
    def __init__(self, fake: FakeHistorical):
        self.fake = fake

    def resolve(self, *, dataset, symbols, stype_in, stype_out, start_date, end_date):
        self.fake.calls.append(("resolve", stype_in, tuple(symbols)))
        s0, s1 = str(start_date), str(end_date)
        if stype_in == "continuous":
            result = {}
            for name in symbols:
                old, new, roll = self.fake.rolls[name]
                result[name] = [{"d0": s0, "d1": roll.isoformat(), "s": str(INSTRUMENT[old])},
                                {"d0": roll.isoformat(), "d1": s1, "s": str(INSTRUMENT[new])}]
        else:
            by_id = {str(v): k for k, v in INSTRUMENT.items()}
            result = {iid: [{"d0": s0, "d1": s1, "s": by_id[iid]}] for iid in symbols}
            if self.fake.partial_ids:  # contracts resolve only around their own front period, as expired ones do
                for old, new, roll in self.fake.rolls.values():
                    result[str(INSTRUMENT[old])] = [{"d0": s0, "d1": (roll + timedelta(days=5)).isoformat(), "s": old}]
                    result[str(INSTRUMENT[new])] = [{"d0": (roll - timedelta(days=5)).isoformat(), "d1": s1, "s": new}]
                return {"result": result, "symbols": list(symbols), "stype_in": stype_in, "stype_out": stype_out,
                        "start_date": s0, "end_date": s1, "partial": sorted(result), "not_found": [],
                        "message": "OK", "status": 0}
        return {"result": result, "symbols": list(symbols), "stype_in": stype_in, "stype_out": stype_out,
                "start_date": s0, "end_date": s1, "partial": [], "not_found": [], "message": "OK", "status": 0}


class _Metadata:
    def __init__(self, fake: FakeHistorical):
        self.fake = fake

    def get_cost(self, *, dataset, symbols, schema, stype_in, start, end):
        self.fake.calls.append(("cost", schema, tuple(symbols), start, end))
        # A Standard subscriber: get_cost applies the plan, so included data costs $0 (1-second bars always; L1 from
        # `included_from`, UTC date of the request start); only older L1 is priced.
        if schema == "ohlcv-1s" or datetime.fromisoformat(start).astimezone(timezone.utc).date() >= self.fake.included_from:
            return 0.0
        days = (ns(end) - ns(start)) / (86400 * NS)
        return round(USD_PER_DAY[schema] * days, 4)


class _Timeseries:
    def __init__(self, fake: FakeHistorical):
        self.fake = fake

    def get_range(self, *, dataset, symbols, schema, stype_in, start, end, path):
        assert stype_in == "raw_symbol" and len(symbols) == 1
        if Path(path).exists():  # as databento 0.87 validate_file_write_path(exist_ok=False)
            raise FileExistsError(f"path {path} already exists")
        self.fake.calls.append(("get_range", schema, symbols[0], start, end))
        if self.fake.fail_on and self.fake.fail_on(schema, symbols[0], start):
            with open(path, "wb") as f:
                f.write(b"partial")  # a half-written download: must never become a cached file
            raise ConnectionError("connection dropped mid-download")
        s, e = ns(start), ns(end)
        with open(path, "wb") as f:
            f.write(dbn_bytes(schema, symbols[0], s, e, self.fake.records(schema, symbols[0], s, e)))


class FakeHistorical:
    """Front contracts: NQ.v.0 rolls NQZ5 -> NQH6 on `nq_roll`, ES.v.0 ESZ5 -> ESH6 on `es_roll` (UTC dates)."""

    def __init__(self, nq_roll: date = date(2025, 12, 15), es_roll: date = date(2025, 12, 16), records=default_records,
                 fail_on=None, included_from: date = date(2025, 11, 20), partial_ids: bool = False):
        self.included_from = included_from
        self.partial_ids = partial_ids
        self.rolls = {"NQ.v.0": ("NQZ5", "NQH6", nq_roll), "ES.v.0": ("ESZ5", "ESH6", es_roll)}
        self.records = records
        self.fail_on = fail_on
        self.calls: list[tuple] = []
        self.symbology = _Symbology(self)
        self.metadata = _Metadata(self)
        self.timeseries = _Timeseries(self)

    def downloads(self) -> list[tuple]:
        return [c for c in self.calls if c[0] == "get_range"]


def et_ns(day: date, hh: int, mm: int, ss: int = 0) -> int:
    return int(datetime(day.year, day.month, day.day, hh, mm, ss, tzinfo=ET).timestamp()) * NS

