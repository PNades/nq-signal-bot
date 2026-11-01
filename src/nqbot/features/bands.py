"""Level bands over time (feature_spec F0.1, F5.4b): the band of a level as of any instant and as of each trade.

Every level is a band [lo, hi]. "As of t" = after every update with known time <= t. Two kinds:
- `StepBand`: changes only at given instants (a profile instant, a board's `t_apply`, a level created or moved) and
  stays fixed in between; it doesn't exist before its first instant (and after `end_ns`, if set).
- `TradeBand` (the VWAP lines, F3.1): moves on every trade of its tape; as of t = after the last trade <= t.
`at_trades(i0, i1)` gives the band as of each trade i0..i1-1 of the tape (NaN where the band doesn't exist), so the
state machines judge every trade against the band in force at its own timestamp (F5.17) without a Python loop.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from nqbot.features.session_tape import Tape


@dataclass
class StepBand:
    tape: Tape
    times: np.ndarray       # int64 ns, sorted: the band changes at these instants
    lo: np.ndarray
    hi: np.ndarray
    end_ns: int | None = None

    @classmethod
    def const(cls, tape: Tape, start_ns: int, lo: float, hi: float, end_ns: int | None = None) -> StepBand:
        return cls(tape, np.array([start_ns], dtype=np.int64), np.array([lo], float), np.array([hi], float), end_ns)

    @property
    def start_ns(self) -> int:
        return int(self.times[0])

    def at(self, t_ns: int) -> tuple[float, float] | None:
        if self.end_ns is not None and t_ns >= self.end_ns:
            return None
        k = int(np.searchsorted(self.times, t_ns, side="right")) - 1
        if k < 0 or np.isnan(self.lo[k]):
            return None
        return float(self.lo[k]), float(self.hi[k])

    def at_trades(self, i0: int, i1: int) -> tuple[np.ndarray, np.ndarray]:
        ts = self.tape.ts[i0:i1]
        k = np.searchsorted(self.times, ts, side="right") - 1
        ok = k >= 0
        if self.end_ns is not None:
            ok &= ts < self.end_ns
        kk = np.where(ok, k, 0)
        lo = np.where(ok, self.lo[kk], np.nan)
        hi = np.where(ok, self.hi[kk], np.nan)
        return lo, hi


@dataclass
class TradeBand:
    """center[i] = the line after trade i (NaN before the line exists); band = center ± half_width."""
    tape: Tape
    center: np.ndarray      # aligned with tape trades
    half_width: float
    start_ns: int

    def at(self, t_ns: int) -> tuple[float, float] | None:
        if t_ns < self.start_ns:
            return None
        i = self.tape.idx_at(t_ns)
        if i == 0 or np.isnan(self.center[i - 1]):
            return None
        c = float(self.center[i - 1])
        return c - self.half_width, c + self.half_width

    def at_trades(self, i0: int, i1: int) -> tuple[np.ndarray, np.ndarray]:
        c = self.center[i0:i1]
        return c - self.half_width, c + self.half_width


def values_at(band, ts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(lo, hi) of `band` at each instant of the sorted array `ts`, NaN where `band.at` gives None (the same values,
    many instants at once)."""
    ts = np.asarray(ts, dtype=np.int64)
    if isinstance(band, StepBand):
        k = np.searchsorted(band.times, ts, side="right") - 1
        good = k >= 0
        if band.end_ns is not None:
            good &= ts < band.end_ns
        kk = np.where(good, k, 0)
        lo, hi = np.where(good, band.lo[kk], np.nan), np.where(good, band.hi[kk], np.nan)
        bad = np.isnan(lo)
        return lo, np.where(bad, np.nan, hi)
    if isinstance(band, TradeBand):
        j = np.searchsorted(band.tape.ts, ts, side="right")
        good = (ts >= band.start_ns) & (j > 0)
        c = np.where(good, band.center[np.maximum(j - 1, 0)], np.nan)
        return c - band.half_width, c + band.half_width
    vals = [band.at(int(t)) for t in ts]
    return (np.array([v[0] if v is not None else np.nan for v in vals], float),
            np.array([v[1] if v is not None else np.nan for v in vals], float))
