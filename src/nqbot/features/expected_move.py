"""F0.8 distance units (ATR, expected move) and F3.11 expected-move (sigma) levels (SPEC §5.4 Distances; feature_spec
F0.8, F3.11; owner 2026-10-14 Q-sigma, 2026-10-22 Q-svxwknd).

- ATR = the simple mean of the true ranges of the last `atr_period` completed RTH sessions (RTH high, low, previous RTH
  close; roll-shifted), known at the session start. It needs `atr_period` + 1 sessions (the first true range needs the
  close before it); fewer -> null (F0.7).
- EM (NQ) = C × m: C = the NQ RTH close of session T − 1 (front contract, roll-shifted to today's), m = `em_1d_pct` ÷ 100
  of QQQ's pre-open Tempest row of session T − `tempest_lag_sessions`, as stored, never recomputed. ES: the ES close ×
  SPY's `em_1d_pct`. With `tempest_lag_sessions` = 2, m = `svx1d` ÷ 100 × √(calendar days from the T − 1 close to the T
  close ÷ 365). A missing row: no lines and EM null (never a stale row).
- Lines C × (1 + k·m) for k in `em_level_sigmas`, fixed for the session, registry type `em_level`; known at the
  pre-open (`preopen_start`, the Tempest row is pulled then).
- `running_sigma` = (price − C) ÷ (C·m); the nearest line above and below.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

TEMPEST_SYMBOL = {"NQ": "QQQ", "ES": "SPY"}
DAYS_PER_YEAR = 365   # Tempest's calendar-day convention (F3.11)


def true_ranges(sessions: list[dict[str, float]]) -> list[float]:
    """TR of each session after the first: max(H − L, |H − prev C|, |L − prev C|)."""
    out = []
    for prev, cur in zip(sessions, sessions[1:]):
        h, l, c = cur["high"], cur["low"], prev["close"]
        out.append(max(h - l, abs(h - c), abs(l - c)))
    return out


def atr(sessions: list[dict[str, float]], period: int) -> float | None:
    """ATR from the completed RTH sessions (oldest first, already roll-shifted to today's contract)."""
    if len(sessions) < period + 1:
        return None
    tr = true_ranges(sessions[-(period + 1):])
    return sum(tr) / period


def em_fraction(row: dict[str, Any] | None, lag_sessions: int, calendar_days: int | None = None) -> float | None:
    """m: `em_1d_pct` ÷ 100 as stored (lag 1), or the lag-2 calendar-day form from `svx1d` (F3.11)."""
    if row is None:
        return None
    if lag_sessions == 1:
        v = row.get("em_1d_pct")
        return None if v is None else v / 100.0
    if lag_sessions == 2:
        v = row.get("svx1d")
        if v is None or calendar_days is None:
            return None
        return v / 100.0 * math.sqrt(calendar_days / DAYS_PER_YEAR)
    raise ValueError(f"tempest_lag_sessions {lag_sessions} has no rule (F3.11 defines 1 and 2)")


@dataclass(frozen=True)
class ExpectedMove:
    close: float | None     # C (the previous RTH close, roll-shifted)
    m: float | None         # the one-day move as a fraction
    sigmas: tuple

    @property
    def points(self) -> float | None:
        return None if self.close is None or self.m is None else self.close * self.m

    def lines(self) -> dict[int, float]:
        if self.points is None:
            return {}
        return {k: self.close * (1 + k * self.m) for k in self.sigmas}

    def running_sigma(self, price: float) -> float | None:
        return None if self.points is None else (price - self.close) / self.points

    def nearest(self, price: float) -> tuple[tuple[int, float] | None, tuple[int, float] | None]:
        """(k, line) of the nearest line strictly above and the nearest at or below the price."""
        above = [(k, v) for k, v in self.lines().items() if v > price]
        below = [(k, v) for k, v in self.lines().items() if v <= price]
        return (min(above, key=lambda kv: kv[1]) if above else None,
                max(below, key=lambda kv: kv[1]) if below else None)


def sub_type(k: int) -> str:
    return f"em_{'up' if k > 0 else 'dn'}{abs(k)}"


def dist3(pts: float | None, atr_pts: float | None, em_pts: float | None) -> tuple:
    """A distance in points, ATR units and EM units (F0.8); a unit that isn't known is null."""
    if pts is None:
        return None, None, None
    return pts, (pts / atr_pts if atr_pts else None), (pts / em_pts if em_pts else None)


class TempestRows:
    """The stored final Tempest rows by (symbol, session date), from `SkylitStore.vol_files()` (final copies only)."""

    def __init__(self, rows: dict[tuple[str, date], dict[str, Any]]):
        self.rows = rows

    @classmethod
    def load(cls, store) -> TempestRows:
        import pyarrow.parquet as pq

        rows: dict[tuple[str, date], dict[str, Any]] = {}
        for p in store.vol_files():
            for r in pq.read_table(p).to_pylist():
                rows[(r["symbol"], r["date"])] = r
        return cls(rows)

    def get(self, symbol: str, day: date) -> dict[str, Any] | None:
        return self.rows.get((symbol, day))


def session_em(root: str, day: date, prev_close: float | None, rows: TempestRows, lag: int,
               session_back: Callable[[date, int], date], sigmas, calendar_days: int | None = None) -> ExpectedMove:
    """The day's expected move for NQ (QQQ row) or ES (SPY row): row of session T − lag."""
    row = rows.get(TEMPEST_SYMBOL[root], session_back(day, lag))
    return ExpectedMove(prev_close, em_fraction(row, lag, calendar_days), tuple(sigmas))
