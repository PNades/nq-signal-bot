"""F10 opening-range fib and golden zone (SPEC §5.8, owner 2026-10-16), with the two step-13 variant switches
(owner 2026-10-26, SPEC §13 Q-fibvariants): `golden_zone_flip` and `golden_zone_redraw_1030`, both start true, chosen on
months 1-4 by total net R per month (§7.1 Tuning). The 09:45 start, the 10:00 lock, the fib levels and the swing rule
are owner rules, never tuned.

Two zones from one drawing (owner 2026-10-27, SPEC §13 Q-gzsplit): the shallow zone 0.5..`golden_zone_split_fib`
(0.618) and the deep zone 0.618..`golden_zone_upper_fib` (0.79). Each is its own container with its own `zone_id`;
both come from the same drawing (orientation, flips, 10:30 redraw), and whenever the drawing changes both get new
`zone_id`s. Each breaks on a 1-minute bar settling past its own far edge (F0.5): past 0.618 breaks the shallow zone
only (no flip, the deep zone is simply the next zone); past 0.79 breaks the deep zone and, with the flip on, flips the
drawing. Touches and re-arm: `GoldenTouches` (owner 2026-10-27: the deep zone is touched only by a trade past 0.618,
never by coming within `zone_proximity` of it); freshness and events are the caller's (F5.6, F5.7).

Times are seconds after 09:30:00 ET; trades and 1-minute bars come in time order. Each call first applies the 09:45
start, the 10:00 lock and the 10:30 redraw at their exact instants, so a trade at 10:00:00 no longer redraws and the
10:30 redraw reads the trades in [09:30, 10:30).
- Flip on: a 1-minute bar settling past the deep zone's 0.79 edge, away from the zone (F0.5, margin 0), breaks the
  deep zone (and the shallow one: it is past 0.618 too) and redraws the fib the other way on the same H and L (new
  `zone_id`s, `flips` + 1).
- Flip off: the same bar breaks both zones only; the drawing, its orientation and `zone_id`s stay, and later returns
  re-arm on the original side (§5.4 Containers; the re-arm itself is the caller's F5.6).
- Redraw off: the 10:00 drawing and the IB high/low locked at 10:00 stay all day (the IB follows the switch, §5.3).
A new extreme before 10:00 moves the drawing (orientation by the swing rule, flips restart) and keeps its `zone_id`s
if the orientation stays the same, like any zone that moves (F5.5); if the swing rule reverses the orientation they
are new zones, like a flip (owner 2026-10-26: the old zones' open events end, their pending entries are removed, touch
counts and freshness don't carry over), reported as a `Redraw`. Flips and the 10:30 redraw make new zones (F10). Prices
are the session's front-contract NQ trades (F0.4), so the range is the traded high/low (wicks), never bar bodies or
closes.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, replace

LONG, SHORT = "long", "short"
RUNNING, LOCKED, REDRAWN = "running", "locked_1000", "redrawn_1030"
SHALLOW, DEEP = "shallow", "deep"   # golden_zone_part (owner 2026-10-27, Q-gzsplit)
PARTS = (SHALLOW, DEEP)


@dataclass(frozen=True)
class Drawing:
    high: float
    low: float
    orientation: str        # long: up leg, levels at H - x(H - L); short: down leg, levels at L + x(H - L)
    source: str             # fib_drawing: running / locked_1000 / redrawn_1030
    shallow_id: int         # zone_id of the shallow zone (0.5 .. split fib)
    deep_id: int            # zone_id of the deep zone (split fib .. upper fib)
    flips: int = 0          # flips of this drawing's anchors (restart when the anchors change)

    @property
    def zone_ids(self) -> tuple[int, int]:
        return self.shallow_id, self.deep_id

    def zone_id(self, part: str) -> int:
        return self.shallow_id if part == SHALLOW else self.deep_id

    def level(self, x: float) -> float:
        span = self.high - self.low
        return self.high - x * span if self.orientation == LONG else self.low + x * span

    def band(self, near_fib: float, far_fib: float) -> tuple[float, float]:
        a, b = self.level(near_fib), self.level(far_fib)
        return min(a, b), max(a, b)

    def zone(self, upper_fib: float) -> tuple[float, float]:
        """The whole golden span 0.5 .. upper fib (both zones together)."""
        return self.band(0.5, upper_fib)


@dataclass(frozen=True)
class Break:
    t: float
    part: str               # shallow / deep
    zone_id: int            # the zone that broke: its open event ends, it re-arms against its own far edge
    flipped: bool           # True only for a deep break with the flip on (the drawing was redrawn the other way)


@dataclass(frozen=True)
class Redraw:
    t: float
    old_zone_ids: tuple     # (shallow, deep): open events on them end, their pending entries are removed (F7)
    new_zone_ids: tuple
    ib_moved: tuple         # sides whose IB level was replaced: subset of ("high", "low"); () before the 10:00 lock


def swing(t_high: float, t_low: float) -> str:
    """Owner's rule: low first then high = up leg (long pullback zone); high first = down leg (short)."""
    return LONG if t_low < t_high else SHORT


class GoldenFib:
    def __init__(self, start_minute: float, lock_minute: float, redraw_minute: float, upper_fib: float,
                 split_fib: float, flip: bool = True, redraw_1030: bool = True):
        self.t_start, self.t_lock, self.t_redraw = 60.0 * start_minute, 60.0 * lock_minute, 60.0 * redraw_minute
        self.upper_fib, self.split_fib, self.flip, self.redraw_1030 = upper_fib, split_fib, flip, redraw_1030
        self.h = self.l = self.t_h = self.t_l = None      # running range [09:30, 10:30)
        self.lock_h = self.lock_l = None                   # the 10:00 range
        self.ib_high = self.ib_low = None
        self.drawing: Drawing | None = None
        self.redrawn_1030: bool | None = None              # null before 10:30:00
        self.redraws_before_lock = 0
        self._history: list = []                           # (t, drawing) in time order, for F0.5's band at the bar open
        self._next_id = 0
        self._marks_done = 0

    def fibs(self, part: str) -> tuple[float, float]:
        """(near, far) fib of a zone; the far edge is the one a settled bar breaks."""
        return (0.5, self.split_fib) if part == SHALLOW else (self.split_fib, self.upper_fib)

    def band(self, part: str, d: Drawing | None = None) -> tuple[float, float]:
        return (d or self.drawing).band(*self.fibs(part))

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
                old = self.drawing.zone_ids
                self._draw(self.t_redraw, REDRAWN, new_zone=True)
                self.redrawn_1030 = True
                if "high" in moved:
                    self.ib_high = self.h
                if "low" in moved:
                    self.ib_low = self.l
                out.append(Redraw(self.t_redraw, old, self.drawing.zone_ids, moved))
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
            if self.drawing.zone_ids != old.zone_ids:  # the swing rule reversed the orientation: new zones
                out.append(Redraw(t, old.zone_ids, self.drawing.zone_ids, ()))
        return out

    def on_bar(self, t_open: float, t_close: float, bar_open: float, bar_close: float) -> list:
        """A closed 1-minute bar [t_open, t_close): each zone's break (and the deep zone's flip), known at t_close."""
        out = self.advance(t_close)
        d = self.drawing
        if d is None:
            return out
        at_open = self._drawing_at(t_open)
        if at_open is None or at_open.zone_ids != d.zone_ids:
            return out  # the zones did not exist at the bar's open, so the bar cannot settle beyond them (F0.5)
        for part in PARTS:
            far = self.fibs(part)[1]
            edge_open, edge_close = at_open.level(far), d.level(far)
            if d.orientation == LONG:   # zones below price; the far edge is the lower edge
                settled = bar_open < edge_open and bar_close < edge_close
            else:
                settled = bar_open > edge_open and bar_close > edge_close
            if settled:
                out.append(Break(t_close, part, d.zone_id(part), part == DEEP and self.flip))
        if self.flip and any(b.part == DEEP for b in out if isinstance(b, Break)):
            other = SHORT if d.orientation == LONG else LONG
            self._set(t_close, replace(d, orientation=other, shallow_id=self._new_id(), deep_id=self._new_id(),
                                       flips=d.flips + 1))
        return out

    # --- helpers ---------------------------------------------------------------------------------------------------
    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _draw(self, t: float, source: str, new_zone: bool) -> None:
        if new_zone or self.drawing is None:
            ids = self._new_id(), self._new_id()
        else:
            ids = self.drawing.zone_ids
        self._set(t, Drawing(self.h, self.l, swing(self.t_h, self.t_l), source, *ids, 0))

    def _set(self, t: float, d: Drawing) -> None:
        self.drawing = d
        self._history.append((t, d))

    def _drawing_at(self, t: float) -> Drawing | None:
        i = bisect_right([h[0] for h in self._history], t)
        return self._history[i - 1][1] if i else None


@dataclass(frozen=True)
class Touch:
    t: float
    part: str
    zone_id: int
    touch_group: int        # a deep-zone touch never shares a group with a shallow-zone touch (owner 2026-10-27)


class GoldenTouches:
    """Touch and re-arm of the two golden zones (owner 2026-10-27, SPEC §13 Q-gzsplit; F5.6 otherwise).

    Positions are fib depths x (0 = the range extreme on the trade's target side, larger = a deeper pullback).
    - Shallow zone, approached from the 0 side: armed at a trade at x <= 0.5 - (zone_proximity + rearm_dist) / span,
      touched at the first armed trade at x >= 0.5 - zone_proximity / span (F5.6's band on that side).
    - Deep zone, only ever reached through the shallow zone: touched at the first armed trade past the split fib
      (x > 0.618; coming within zone_proximity of 0.618 is not a touch); armed at a trade back on the shallow side at
      x <= 0.618 - rearm_dist / span (the container re-arm against its far edge, §5.4).
    New zone_ids (flip, reversal before 10:00, 10:30 redraw) start unarmed and arm by the same rules. Each touch gets its
    own touch group here; the event builder adds the other zones the same trade touches (F5.10), never a shallow-zone
    touch to a deep-zone one."""

    def __init__(self, fib: GoldenFib, zone_proximity: float, rearm_dist: float):
        self.fib, self.prox, self.rearm = fib, zone_proximity, rearm_dist
        self._armed: dict[int, bool] = {}
        self._next_group = 0

    def depth(self, price: float) -> float:
        d = self.fib.drawing
        span = d.high - d.low
        return (d.high - price) / span if d.orientation == LONG else (price - d.low) / span

    def on_trade(self, t: float, price: float) -> list:
        """Call after GoldenFib.on_trade for the same trade, so the drawing is the one in force."""
        d = self.fib.drawing
        if d is None or d.high == d.low:
            return []
        span, x, split = d.high - d.low, self.depth(price), self.fib.split_fib
        rules = ((SHALLOW, x <= 0.5 - (self.prox + self.rearm) / span, x >= 0.5 - self.prox / span),
                 (DEEP, x <= split - self.rearm / span, x > split))
        out = []
        for part, arm, touch in rules:
            zid = d.zone_id(part)
            armed = self._armed.get(zid, False)
            if armed and touch:
                self._next_group += 1
                out.append(Touch(t, part, zid, self._next_group))
                self._armed[zid] = False
            elif not armed and arm:
                self._armed[zid] = True
        return out


def from_config(features: dict) -> GoldenFib:
    return GoldenFib(start_minute=features["ib_fib_start_minute"], lock_minute=features["ib_lock_minute"],
                     redraw_minute=features["ib_redraw_minute"], upper_fib=features["golden_zone_upper_fib"],
                     split_fib=features["golden_zone_split_fib"], flip=bool(features["golden_zone_flip"]),
                     redraw_1030=bool(features["golden_zone_redraw_1030"]))
