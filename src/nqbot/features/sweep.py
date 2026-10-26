"""F3.9 bounce / sweep / break and F5.2 chart-level crosses on 1-minute bars, with the `va_edge_sweep` step-13 variant
(owner 2026-10-26, SPEC §13 Q-vaedge).

- Sweep-eligible: the highs/lows level types (F5.14 keys `on_extreme`, `pdh_pdl`, `prior_rth`, `prior_week`, `ib`,
  `retested_extreme`), plus the previous session's VAH/VAL (`va_edge`) when `va_edge_sweep` is on (start off).
- Sweep-eligible (F3.9): from the bar holding the first trade beyond the band, a break is immediate on a trade more than
  `sweep_max_depth` beyond; a bar closing not beyond the band within the window (the break bar's own close, then
  `sweep_reclaim_bars` full bars) is a sweep (held); otherwise a break at the last window bar's close.
- Chart levels (F5.2, F0.5 with margin 0): crossed at the close of the first bar whose open and close are both beyond.
Bars are closed 1-minute bars in time order with a fixed band; the step-11 builder runs the same rule on trades (the
break bar's depth check is per trade there).
"""

from __future__ import annotations

from dataclasses import dataclass

CEILING, FLOOR = "ceiling", "floor"
HIGHS_LOWS = frozenset({"on_extreme", "pdh_pdl", "prior_rth", "prior_week", "ib", "retested_extreme"})
BAR_SEC = 60.0


@dataclass(frozen=True)
class Bar:
    t_open: float
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Outcome:
    kind: str           # 'crossed' | 'swept'
    t: float            # known at (a bar close; for an over-depth break, the open of the bar that went too deep)
    depth: float = 0.0  # sweeps: the deepest point beyond the band (pts)


def sweep_eligible(level_type: str, features: dict) -> bool:
    return level_type in HIGHS_LOWS or (level_type == "va_edge" and bool(features["va_edge_sweep"]))


def _beyond(price: float, lo: float, hi: float, role: str) -> float:
    """How far past the band in the breaking direction (> 0 = beyond)."""
    return price - hi if role == CEILING else lo - price


def judge(bars, lo: float, hi: float, role: str, eligible: bool, max_depth: float, reclaim_bars: int) -> Outcome | None:
    """The first resolution of the band [lo, hi] held in `role`, or None while unresolved / never beyond."""
    if not eligible:
        for b in bars:
            if _beyond(b.open, lo, hi, role) > 0 and _beyond(b.close, lo, hi, role) > 0:
                return Outcome("crossed", b.t_open + BAR_SEC)
        return None
    extreme = (lambda b: b.high) if role == CEILING else (lambda b: b.low)
    start = next((i for i, b in enumerate(bars) if _beyond(extreme(b), lo, hi, role) > 0), None)
    if start is None:
        return None
    depth = 0.0
    for b in bars[start:start + reclaim_bars + 1]:
        depth = max(depth, _beyond(extreme(b), lo, hi, role))
        if depth > max_depth:
            return Outcome("crossed", b.t_open)
        if _beyond(b.close, lo, hi, role) <= 0:
            return Outcome("swept", b.t_open + BAR_SEC, depth)
    window = bars[start:start + reclaim_bars + 1]
    return Outcome("crossed", window[-1].t_open + BAR_SEC) if len(window) == reclaim_bars + 1 else None
