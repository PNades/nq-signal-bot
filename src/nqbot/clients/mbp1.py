"""Top of book from the stored NQ `mbp-1` files (step 13b: marketable-limit and stop-limit fills, market exits and the
`exit_slippage_ticks` measurement; SPEC §3.2, §5.5b, §12 step 13).

Read in chunks (§11 Low memory). Records with action N (Databento's 2026-08-08 standalone F_LAST records, no book
change; owner 2026-10-29, SPEC §13 Q-sideN) are skipped, as the completeness reader does. Undefined prices (an empty
side) are NaN. The book in force at t is the last record with `ts_event` <= t. The file covers 09:25-16:05 ET (or the
early close + 5 min); sessions before 2025-10-07 have none (`load` returns None: those rows are `no_book`, owner
2026-11-10, SPEC §13 Q-step13b (3)).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

PRICE_SCALE = 1e9
UNDEF_PRICE = np.iinfo(np.int64).max
NO_BOOK_ACTION = b"N"
CHUNK = 1_000_000
BOOK_END_TOL_NS = 1_000_000_000   # a record's book holds at most this long past the file's last record (Q-step13-review L2)


@dataclass
class TopOfBook:
    ts: np.ndarray       # int64 ns, sorted
    bid: np.ndarray      # float64 points (NaN = no bid)
    ask: np.ndarray
    bid_sz: np.ndarray   # float64 contracts
    ask_sz: np.ndarray

    def index_at(self, t: int) -> int:
        return int(np.searchsorted(self.ts, t, side="right")) - 1

    def at(self, t: int) -> tuple[float, float, float, float] | None:
        """(bid, ask, bid size, ask size) in force at t, or None outside the file's cover: before its first record, or
        more than `BOOK_END_TOL_NS` after its last (the file ends at 16:05 / the early close + 5 min; owner 2026-11-13,
        SPEC §13 Q-step13-review L2: the 16:04:59 book was read until 17:00, so outside the window a marketable limit
        is `no_book`, a stop-limit fills at its trade and a market exit reads the trades, Q-step13b (a) (3))."""
        k = self.index_at(t)
        if k < 0 or t > int(self.ts[-1]) + BOOK_END_TOL_NS:
            return None
        return float(self.bid[k]), float(self.ask[k]), float(self.bid_sz[k]), float(self.ask_sz[k])

    @classmethod
    def from_arrays(cls, ts, bid, ask, bid_sz, ask_sz) -> TopOfBook:
        ts = np.asarray(ts, dtype=np.int64)
        o = np.argsort(ts, kind="stable")
        f = lambda a: np.asarray(a, dtype=float)[o]   # noqa: E731
        return cls(ts[o], f(bid), f(ask), f(bid_sz), f(ask_sz))


def _px(a: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype=np.int64)
    return np.where(a == UNDEF_PRICE, np.nan, a / PRICE_SCALE)


def load(path: Path) -> TopOfBook | None:
    if not Path(path).exists():
        return None
    from databento import DBNStore

    parts = []
    for a in DBNStore.from_file(path).to_ndarray(count=CHUNK):
        a = a.reshape(-1)
        a = a[a["action"] != NO_BOOK_ACTION]
        parts.append((np.asarray(a["ts_event"], dtype=np.int64), _px(a["bid_px_00"]), _px(a["ask_px_00"]),
                      np.asarray(a["bid_sz_00"], dtype=float), np.asarray(a["ask_sz_00"], dtype=float)))
    if not parts:
        return TopOfBook.from_arrays([], [], [], [], [])
    return TopOfBook.from_arrays(*(np.concatenate([p[i] for p in parts]) for i in range(5)))
