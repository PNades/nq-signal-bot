"""Day-type VWAP / POC crosses (F3.10, F11.6; owner 2026-10-27, SPEC §13 Q-dtcross).

A cross is a clock-aligned `daytype_cross_bar_minutes` (5) bar settled on the other side (F0.5, margin 0): its open is
beyond the level as of the bar's open and its close beyond the level as of its close (the developing VWAP / POC move
like bands). The first settled bar sets the side without counting; a bar that doesn't settle leaves the side as it is.
Used by `vwap_crosses` (a day-type input), the EOD day-type label and `poc_crosses` (LightGBM day-type challenger
candidate only; owner 2026-10-27, §13 Q-dtcross follow-up: POC crosses are not part of the label).

`crosses_1m_closes` is the rule before 2026-10-27 (a 1-minute close on the other side of the previous close's side),
kept only for the step-13 count report that shows both rules side by side.
"""

from __future__ import annotations

from dataclasses import dataclass

ABOVE, BELOW = "above", "below"
UP, DOWN = "up", "down"
TREND, RANGE = "trend", "range"
# The former `trend_max_poc_crosses`, used only by the step-13 comparison report (SPEC §12 step 13).
REPORT_MAX_POC_CROSSES = 2


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


def trend_side(new_highs: int, new_lows: int) -> str | None:
    """SPEC §13 Q-dtside (owner 2026-10-27): the side of the new highs/lows at the close = the sign of
    `new_highs` - `new_lows`, the same F3.10 counts whose absolute value is `new_extreme_skew`; zero = no side."""
    d = new_highs - new_lows
    return UP if d > 0 else DOWN if d < 0 else None


def eod_label(vwap_crosses: int, close: float, rth_high: float, rth_low: float, new_highs: int, new_lows: int,
              max_vwap_crosses: int, close_pct: float, poc_crosses: int | None = None,
              max_poc_crosses: int | None = None) -> str:
    """F11.6: trend if VWAP crosses <= max_vwap_crosses and the close is in the outer close_pct of the RTH range on the
    side of the new highs/lows (`trend_side` at the close; no side = range); else range.
    `poc_crosses` / `max_poc_crosses` add the former POC condition, for the step-13 comparison report only."""
    if vwap_crosses > max_vwap_crosses:
        return RANGE
    if max_poc_crosses is not None and (poc_crosses is None or poc_crosses > max_poc_crosses):
        return RANGE
    extreme_side = trend_side(new_highs, new_lows)
    band = close_pct * (rth_high - rth_low)
    if extreme_side == UP:
        return TREND if close >= rth_high - band else RANGE
    if extreme_side == DOWN:
        return TREND if close <= rth_low + band else RANGE
    return RANGE


def with_day_direction(direction: int, new_highs: int, new_lows: int) -> int:
    """F11.7 (owner 2026-10-27, §13 Q-trenddir): trade direction (+1 long, -1 short) x sign(new_highs - new_lows) at t."""
    d = new_highs - new_lows
    return direction * ((d > 0) - (d < 0))


def vwap_side_vs_trade(direction: int, price: float, vwap: float, half_width: float) -> int:
    """F11.7: +1 if price is on the trade's side of the 09:30 VWAP (long above, short below), -1 if not, 0 within its
    band (VWAP +/- chart_level_half_width[vwap], edges included)."""
    if abs(price - vwap) <= half_width:
        return 0
    return direction if price > vwap else -direction


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
