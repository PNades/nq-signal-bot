"""One CME trading day's trades on its front contract, the base every chart-level feature reads (SPEC §3.2, §5.3;
feature_spec F0.1, F0.2, F0.4).

A `Tape` holds the trades of one instrument and one CME day [18:00 ET of the previous calendar day, 17:00 ET), sorted
by `ts_event` then exchange `sequence`. Times are UTC nanoseconds. "As of t" means every trade with `ts_event <= t`
(F0.1: a trade is known at its own `ts_event`), so `idx_at(t)` = the number of trades known at t. Side is +1 (B, buy
aggressor), -1 (A, sell aggressor) or 0 (N: counts in volume only, never in delta; SPEC §13 Q-sideN).

The front contract per CME day comes from the step-8 plan (`front_by_volume`, owner 2026-10-30, §13 Q-frontroll);
the loader never mixes two contracts in one tape.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from nqbot import calendar as cal_mod

ET = ZoneInfo("America/New_York")
NS = 1_000_000_000
MIN_NS = 60 * NS
PRICE_SCALE = 1e9           # Databento fixed-point prices (units of 1e-9), as in features/bars.py
TICK = 0.25                 # NQ and ES tick (F0.3)
CME_DAY_START = time(18, 0)
CME_DAY_END = time(17, 0)
RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)


def et_ns(day: date, at: time) -> int:
    """UTC ns of `at` ET on `day`."""
    return int(datetime.combine(day, at, tzinfo=ET).timestamp()) * NS


def cme_start_ns(day: date) -> int:
    """18:00 ET of the calendar day before CME day `day` (F0.2)."""
    return et_ns(day - timedelta(days=1), CME_DAY_START)


def cme_end_ns(day: date) -> int:
    return et_ns(day, CME_DAY_END)


def rth_open_ns(day: date) -> int:
    return et_ns(day, RTH_OPEN)


def rth_close_ns(day: date, cal: dict[str, Any] | None = None) -> int:
    """16:00 ET, or `early_close_et` on an early-close day (§1.2)."""
    return et_ns(day, cal_mod.early_close_et(day, cal) or RTH_CLOSE)


def week_start_ns(day: date) -> int:
    """Sunday 18:00 ET of the week holding CME day `day` (F0.2: week = [Sunday 18:00, Friday 17:00))."""
    d = day - timedelta(days=1)
    while d.weekday() != 6:   # Sunday
        d -= timedelta(days=1)
    return et_ns(d, CME_DAY_START)


def sec_after_open(day: date, t_ns: int) -> float:
    """Seconds after 09:30:00 ET (the time base of features/golden_zone.py and features/volume_norms.py)."""
    return (t_ns - rth_open_ns(day)) / NS


@dataclass
class Tape:
    day: date
    symbol: str
    ts: np.ndarray      # int64 UTC ns, sorted (then by sequence)
    price: np.ndarray   # float64 points
    size: np.ndarray    # float64 contracts
    side: np.ndarray    # int8: +1 B, -1 A, 0 N

    def __post_init__(self):
        if len(self.ts) and np.any(np.diff(self.ts) < 0):
            raise ValueError("tape timestamps must be sorted")

    @classmethod
    def from_arrays(cls, day: date, ts, price, size=None, side=None, sequence=None, symbol: str = "NQ") -> Tape:
        ts = np.asarray(ts, dtype=np.int64)
        n = len(ts)
        seq = np.arange(n) if sequence is None else np.asarray(sequence, dtype=np.int64)
        order = np.lexsort((seq, ts))
        price = np.asarray(price, dtype=np.float64)[order]
        size = (np.ones(n) if size is None else np.asarray(size, dtype=np.float64))[order]
        if side is None:
            sd = np.zeros(n, dtype=np.int8)
        else:
            raw = np.asarray(side)
            if raw.dtype.kind in "SUO":
                raw = raw.astype("S1")
                sd = np.where(raw == b"B", 1, np.where(raw == b"A", -1, 0)).astype(np.int8)
            else:
                sd = raw.astype(np.int8)
            sd = sd[order]
        return cls(day, symbol, ts[order], price, size, sd)

    @classmethod
    def from_records(cls, day: date, records: np.ndarray, symbol: str) -> Tape:
        """Databento `trades` records (structured array as stored)."""
        r = records[records["action"] == b"T"] if "action" in records.dtype.names else records
        return cls.from_arrays(day, r["ts_event"].astype(np.int64), r["price"] / PRICE_SCALE, r["size"], r["side"],
                               r["sequence"], symbol)

    def __len__(self) -> int:
        return len(self.ts)

    def idx_at(self, t_ns: int) -> int:
        """Number of trades known at t (ts_event <= t)."""
        return int(np.searchsorted(self.ts, t_ns, side="right"))

    def idx_before(self, t_ns: int) -> int:
        """Number of trades strictly before t."""
        return int(np.searchsorted(self.ts, t_ns, side="left"))

    def last_price_at(self, t_ns: int) -> float | None:
        i = self.idx_at(t_ns)
        return float(self.price[i - 1]) if i else None

    def window(self, t0_ns: int, t1_ns: int) -> slice:
        """Trades with t0 <= ts < t1."""
        return slice(self.idx_before(t0_ns), self.idx_before(t1_ns))

    def cut(self, t_ns: int) -> Tape:
        """The tape as known at t (for no-lookahead tests)."""
        n = self.idx_at(t_ns)
        return Tape(self.day, self.symbol, self.ts[:n], self.price[:n], self.size[:n], self.side[:n])


@dataclass(frozen=True)
class Bars:
    """Clock-aligned bars of one length built from a tape (F0.1: labelled by their start, known at start + length;
    a bar with no trades doesn't exist)."""
    start: np.ndarray   # int64 ns
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    first: np.ndarray   # index of the bar's first trade in the tape
    last: np.ndarray    # index of its last trade
    length_ns: int

    def __len__(self) -> int:
        return len(self.start)

    def known_at(self, t_ns: int) -> int:
        """Number of bars closed (known) at t: start + length <= t."""
        return int(np.searchsorted(self.start + self.length_ns, t_ns, side="right"))


def bars(tape: Tape, minutes: int) -> Bars:
    """ET minutes are whole UTC minutes (ET's offset is whole hours), so flooring UTC ns aligns to the ET clock."""
    length = minutes * MIN_NS
    if len(tape) == 0:
        e = np.zeros(0, dtype=np.int64)
        f = np.zeros(0)
        return Bars(e, f, f, f, f, f, e, e, length)
    st = tape.ts - np.mod(tape.ts, length)
    first = np.flatnonzero(np.r_[True, st[1:] != st[:-1]])
    last = np.r_[first[1:], len(st)] - 1
    p = tape.price
    return Bars(st[first], p[first], np.maximum.reduceat(p, first), np.minimum.reduceat(p, first), p[last],
                np.add.reduceat(tape.size, first), first, last, length)


# --- loading stored data (read-only) ---------------------------------------------------------------------------

def front_symbol(plan: dict[str, Any], root: str, day: date) -> str | None:
    return (plan.get("front", {}).get(root) or {}).get(day.isoformat())


def load_tape(store, plan: dict[str, Any], root: str, day: date) -> Tape | None:
    """The stored `trades` file of CME day `day` on its front contract (None if not stored)."""
    from nqbot.clients.databento_store import read_records

    sym = front_symbol(plan, root, day)
    if sym is None:
        return None
    path = store.raw_path("trades", sym, day)
    if not path.exists():
        return None
    return Tape.from_records(day, read_records(path), sym)
