"""Day-type VWAP / POC crosses (F3.10, F11.6; owner 2026-10-27, SPEC §13 Q-dtcross).

A cross is a clock-aligned `daytype_cross_bar_minutes` (5) bar settled on the other side (F0.5, margin 0): its open is
beyond the level as of the bar's open and its close beyond the level as of its close (the developing VWAP / POC move
like bands). The first settled bar sets the side without counting; a bar that doesn't settle leaves the side as it is.
Used by `vwap_crosses` (a day-type input), the EOD day-type label and `poc_crosses` (LightGBM day-type challenger).

`crosses_1m_closes` is the rule before 2026-10-27 (a 1-minute close on the other side of the previous close's side),
kept only for the step-13 count report that shows both rules side by side.
"""

from __future__ import annotations

from dataclasses import dataclass

ABOVE, BELOW = "above", "below"


@dataclass(frozen=True)
class LevelBar:
    open: float
    close: float
    level_at_open: float | None    # the VWAP / developing POC as of the bar's open (None = not defined yet)
    level_at_close: float | None   # ... as of its close


def settled_side(b: LevelBar) -> str | None:
    """F0.5 with margin 0: the side the bar settled on, or None if it straddles (or the level isn't defined)."""
    if b.level_at_open is None or b.level_at_close is None:
        return None
    if b.open > b.level_at_open and b.close > b.level_at_close:
        return ABOVE
    if b.open < b.level_at_open and b.close < b.level_at_close:
        return BELOW
    return None


def crosses(bars: list[LevelBar]) -> int:
    """Settled-bar crosses of a moving level (the current rule), over clock-aligned bars in time order."""
    side, n = None, 0
    for b in bars:
        s = settled_side(b)
        if s is None:
            continue
        if side is not None and s != side:
            n += 1
        side = s
    return n


def crosses_1m_closes(closes: list[tuple[float, float | None]]) -> int:
    """The old rule, for the step-13 comparison only: (1-minute close, level as of that close) pairs in time order; a
    cross is a close on the opposite side from the previous sided close (a close on the level has no side)."""
    side, n = None, 0
    for c, level in closes:
        if level is None or c == level:
            continue
        s = ABOVE if c > level else BELOW
        if side is not None and s != side:
            n += 1
        side = s
    return n
