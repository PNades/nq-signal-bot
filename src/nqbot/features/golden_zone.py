"""F10 opening-range fib and golden zone (SPEC §5.8, owner 2026-10-16), with the two step-13 variant switches
(owner 2026-10-26, SPEC §13 Q-fibvariants): `golden_zone_flip` and `golden_zone_redraw_1030`, both start true, chosen on
months 1-4 by total net R per month (§7.1 Tuning). The 09:45 start, the 10:00 lock, the fib levels and the swing rule
are owner rules, never tuned.

Times are seconds after 09:30:00 ET; trades and 1-minute bars come in time order. Each call first applies the 09:45
start, the 10:00 lock and the 10:30 redraw at their exact instants, so a trade at 10:00:00 no longer redraws and the
10:30 redraw reads the trades in [09:30, 10:30).
- Flip on: a 1-minute bar settling past the zone's 0.79 level, away from the zone (F0.5, margin 0), ends the setup and
  redraws the fib the other way on the same H and L (a new `zone_id`, `flips` + 1).
- Flip off: the same bar ends the setup only; the drawing, its orientation and `zone_id` stay, and later returns re-arm
  on the original side (§5.4 Containers; the re-arm itself is the caller's F5.6).
- Redraw off: the 10:00 drawing and the IB high/low locked at 10:00 stay all day (the IB follows the switch, §5.3).
A new extreme before 10:00 moves the drawing (orientation by the swing rule, flips restart) and keeps its `zone_id`
if the orientation stays the same, like any zone that moves (F5.5); if the swing rule reverses the orientation it is a
new zone, like a flip (owner 2026-10-26: the old zone's open event ends, its pending entries are removed, its touch count
and freshness don't carry over), reported as a `Redraw`. Flips and the 10:30 redraw make new zones (F10). Prices are the
session's front-contract NQ trades (F0.4), so the range is the traded high/low (wicks), never bar bodies or closes.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, replace

LONG, SHORT = "long", "short"
RUNNING, LOCKED, REDRAWN = "running", "locked_1000", "redrawn_1030"


@dataclass(frozen=True)
class Drawing:
    high: float
    low: float
    orientation: str        # long: up leg, levels at H - x(H - L); short: down leg, levels at L + x(H - L)
    source: str             # fib_drawing: running / locked_1000 / redrawn_1030
    zone_id: int
    flips: int = 0          # flips of this drawing's anchors (restart when the anchors change)

    def level(self, x: float) -> float:
        span = self.high - self.low
        return self.high - x * span if self.orientation == LONG else self.low + x * span

    def zone(self, upper_fib: float) -> tuple[float, float]:
        a, b = self.level(0.5), self.level(upper_fib)
        return min(a, b), max(a, b)


@dataclass(frozen=True)
class Invalidation:
    t: float
    zone_id: int            # the setup on this zone ends (F10 invalidation)
    flipped: bool           # True if the drawing was redrawn the other way (flip on)


@dataclass(frozen=True)
class Redraw:
    t: float
    old_zone_id: int        # an open event on it ends, its pending entries are removed (F7)
    new_zone_id: int
    ib_moved: tuple         # sides whose IB level was replaced: subset of ("high", "low"); () before the 10:00 lock


def swing(t_high: float, t_low: float) -> str:
    """Owner's rule: low first then high = up leg (long pullback zone); high first = down leg (short)."""
    return LONG if t_low < t_high else SHORT


class GoldenFib:
    def __init__(self, start_minute: float, lock_minute: float, redraw_minute: float, upper_fib: float,
                 flip: bool = True, redraw_1030: bool = True):
        self.t_start, self.t_lock, self.t_redraw = 60.0 * start_minute, 60.0 * lock_minute, 60.0 * redraw_minute
        self.upper_fib, self.flip, self.redraw_1030 = upper_fib, flip, redraw_1030
        self.h = self.l = self.t_h = self.t_l = None      # running range [09:30, 10:30)
        self.lock_h = self.lock_l = None                   # the 10:00 range
        self.ib_high = self.ib_low = None
        self.drawing: Drawing | None = None
        self.redrawn_1030: bool | None = None              # null before 10:30:00
        self.redraws_before_lock = 0
        self._history: list = []                           # (t, drawing) in time order, for F0.5's band at the bar open
        self._next_id = 0
        self._marks_done = 0

    # --- clock -----------------------------------------------------------------------------------------------------
    def advance(self, t: float) -> list:
        out = []
        if self._marks_done == 0 and t >= self.t_start and self.h is not None:
            self._draw(self.t_start, RUNNING, new_zone=True)
            self._marks_done = 1
        if self._marks_done == 1 and t >= self.t_lock:
            self.lock_h, self.lock_l = self.h, self.l
            self.ib_high, self.ib_low = self.h, self.l
            self._set(self.t_lock, replace(self.drawing, source=LOCKED))
            self._marks_done = 2
        if self._marks_done == 2 and t >= self.t_redraw:
            self._marks_done = 3
            self.redrawn_1030 = False
            moved = tuple(s for s, hit in (("high", self.h > self.lock_h), ("low", self.l < self.lock_l)) if hit)
            if self.redraw_1030 and moved:
                old = self.drawing.zone_id
                self._draw(self.t_redraw, REDRAWN, new_zone=True)
                self.redrawn_1030 = True
                if "high" in moved:
                    self.ib_high = self.h
                if "low" in moved:
                    self.ib_low = self.l
                out.append(Redraw(self.t_redraw, old, self.drawing.zone_id, moved))
        return out

    # --- inputs ----------------------------------------------------------------------------------------------------
    def on_trade(self, t: float, price: float) -> list:
        out = self.advance(t)
        if t >= self.t_redraw:
            return out
        new_extreme = self.h is None or price > self.h or price < self.l
        if self.h is None or price > self.h:
            self.h, self.t_h = price, t
        if self.l is None or price < self.l:
            self.l, self.t_l = price, t
        if new_extreme and self._marks_done == 1:  # [09:45, 10:00): redraw on the larger range, flips restart
            old = self.drawing
            self._draw(t, RUNNING, new_zone=swing(self.t_h, self.t_l) != old.orientation)
            self.redraws_before_lock += 1
            if self.drawing.zone_id != old.zone_id:  # the swing rule reversed the orientation: a new zone
                out.append(Redraw(t, old.zone_id, self.drawing.zone_id, ()))
        return out

    def on_bar(self, t_open: float, t_close: float, bar_open: float, bar_close: float) -> list:
        """A closed 1-minute bar [t_open, t_close): the F10 invalidation / flip, known at t_close."""
        out = self.advance(t_close)
        d = self.drawing
        if d is None:
            return out
        at_open = self._drawing_at(t_open)
        if at_open is None or at_open.zone_id != d.zone_id:
            return out  # the zone did not exist at the bar's open, so the bar cannot settle beyond it (F0.5)
        edge_open, edge_close = at_open.level(self.upper_fib), d.level(self.upper_fib)
        if d.orientation == LONG:   # zone below price; its far (0.79) edge is the lower edge
            settled = bar_open < edge_open and bar_close < edge_close
        else:
            settled = bar_open > edge_open and bar_close > edge_close
        if not settled:
            return out
        if self.flip:
            other = SHORT if d.orientation == LONG else LONG
            self._set(t_close, replace(d, orientation=other, zone_id=self._new_id(), flips=d.flips + 1))
        out.append(Invalidation(t_close, d.zone_id, self.flip))
        return out

    # --- helpers ---------------------------------------------------------------------------------------------------
    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _draw(self, t: float, source: str, new_zone: bool) -> None:
        zid = self._new_id() if new_zone or self.drawing is None else self.drawing.zone_id
        self._set(t, Drawing(self.h, self.l, swing(self.t_h, self.t_l), source, zid, 0))

    def _set(self, t: float, d: Drawing) -> None:
        self.drawing = d
        self._history.append((t, d))

    def _drawing_at(self, t: float) -> Drawing | None:
        i = bisect_right([h[0] for h in self._history], t)
        return self._history[i - 1][1] if i else None


def from_config(features: dict) -> GoldenFib:
    return GoldenFib(start_minute=features["ib_fib_start_minute"], lock_minute=features["ib_lock_minute"],
                     redraw_minute=features["ib_redraw_minute"], upper_fib=features["golden_zone_upper_fib"],
                     flip=bool(features["golden_zone_flip"]), redraw_1030=bool(features["golden_zone_redraw_1030"]))
