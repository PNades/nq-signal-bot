"""F3.6 IB high/low and F10 opening range and golden zone on a session tape (SPEC §5.3, §5.8; feature_spec F3.6, F10).

The drawing, its flips, the 10:30 redraw and the two golden zones' touches are `features/golden_zone.py`
(`GoldenFib`, `GoldenTouches`; owner 2026-10-16 / 10-26 / 10-27), used unchanged: this module feeds them the session's
RTH trades (seconds after 09:30, in exchange order) and 1-minute bars at their close, and records a timeline for
queries at any t. Both step-13 switches (`golden_zone_flip`, `golden_zone_redraw_1030`) come from config.

- Feeding: a trade whose price equals the previous fed trade's, with the same drawing in force, is skipped. Arm and
  touch conditions are mutually exclusive for one price and the range only moves on a new price, so the output is the
  same (tested); the marks (09:45, 10:00, 10:30) are applied at each minute boundary before that minute's trades.
- IB high/low (F3.6): registry levels from the 10:00 lock (sweep-eligible); at 10:30 a side the redraw moved is
  replaced by a fresh level (`ib_high-<day>-1030`), the other side kept; with the redraw off they stay all day.
- F10 outputs as of t: `fib_drawing`, `fib_orientation`, `fib_flips`, `minutes_since_flip`, `fib_redrawn_1030`,
  `range_pts_1000`, `range_pts_1030`, redraws before the lock, minutes since the lock, `left_ib_since_lock`,
  `range_broke_on`, the shallow / deep bands, and the 0 / 1.0 / −0.272 / −0.618 prices; `fib_level_reached`
  (deepest golden fib reached in a span) for the pair builder.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

from nqbot.features import golden_zone as gz
from nqbot.features.session_tape import MIN_NS, NS, Bars, Tape, rth_close_ns, rth_open_ns

EXTENSIONS = (0.0, 1.0, -0.272, -0.618)   # F10: level 0 (target), 1.0 (stop candidate 6), extensions (targets)


@dataclass
class IbLevel:
    level_id: str
    side: str           # 'high' / 'low'
    price: float
    created_ns: int
    end_ns: int | None = None


@dataclass
class OpeningRange:
    day: date
    fib: gz.GoldenFib
    touches_engine: gz.GoldenTouches
    open_ns: int
    close_ns: int
    timeline_t: list = field(default_factory=list)      # ns of each drawing change
    timeline: list = field(default_factory=list)        # (Drawing | None)
    touches: list = field(default_factory=list)         # (ns, golden_zone.Touch)
    breaks: list = field(default_factory=list)          # (ns, golden_zone.Break)
    redraws: list = field(default_factory=list)         # (ns, golden_zone.Redraw)
    flips_at: list = field(default_factory=list)        # ns of each flip
    ib_levels: list = field(default_factory=list)       # IbLevel
    range_1000: float | None = None
    range_1030: float | None = None
    redrawn_1030: bool | None = None
    redraws_t: list = field(default_factory=list)       # ns of each redraw before the lock (the count's timeline)
    fed: int = 0

    @classmethod
    def build(cls, tape: Tape, bars_1m: Bars, features: dict[str, Any], cal: dict | None = None,
              dedupe: bool = True) -> OpeningRange:
        fib = gz.from_config(features)
        o, c = rth_open_ns(tape.day), rth_close_ns(tape.day, cal)
        orr = cls(tape.day, fib, gz.GoldenTouches(fib, features["zone_proximity"], features["rearm_dist"]), o, c)
        orr._run(tape, bars_1m, dedupe)
        return orr

    def _sec(self, t_ns: int) -> float:
        return (t_ns - self.open_ns) / NS

    def _ns(self, sec: float) -> int:
        return self.open_ns + int(round(sec * NS))

    def _record(self, t_ns: int) -> None:
        d = self.fib.drawing
        if not self.timeline or self.timeline[-1] is not d:
            self.timeline_t.append(t_ns)
            self.timeline.append(d)

    def _outputs(self, t_ns: int, out: list) -> None:
        for x in out:
            if isinstance(x, gz.Redraw):
                self.redraws.append((t_ns, x))
                if x.ib_moved:
                    self._move_ib(x)
            elif isinstance(x, gz.Break):
                self.breaks.append((t_ns, x))
                if x.flipped:
                    self.flips_at.append(t_ns)
        self._marks(t_ns)
        self._record(t_ns)

    def _marks(self, t_ns: int) -> None:
        f = self.fib
        if f.ib_high is not None and not self.ib_levels:
            lock = self._ns(f.t_lock)
            d = self.day.isoformat()
            self.ib_levels = [IbLevel(f"ib_high-{d}", "high", f.ib_high, lock),
                              IbLevel(f"ib_low-{d}", "low", f.ib_low, lock)]
            self.range_1000 = f.ib_high - f.ib_low
        if f.redrawn_1030 is not None and self.redrawn_1030 is None:
            self.redrawn_1030 = f.redrawn_1030
            self.range_1030 = (f.h - f.l) if f.redrawn_1030 else self.range_1000
        while len(self.redraws_t) < f.redraws_before_lock:
            self.redraws_t.append(t_ns)

    def _move_ib(self, r: gz.Redraw) -> None:
        t = self._ns(r.t)
        for side in r.ib_moved:
            old = next(lv for lv in self.ib_levels if lv.side == side and lv.end_ns is None)
            old.end_ns = t
            price = self.fib.ib_high if side == "high" else self.fib.ib_low
            self.ib_levels.append(IbLevel(f"ib_{side}-{self.day.isoformat()}-1030", side, price, t))

    def _run(self, tape: Tape, bars_1m: Bars, dedupe: bool) -> None:
        i = tape.idx_before(self.open_ns)
        last_price, last_drawing = None, object()
        for m in range(self.open_ns, self.close_ns, MIN_NS):
            self._outputs(m, self.fib.advance(self._sec(m)))      # marks at the minute boundary, before its trades
            j = tape.idx_before(m + MIN_NS)
            for k in range(i, j):
                p = float(tape.price[k])
                if dedupe and p == last_price and self.fib.drawing is last_drawing:
                    continue
                t = int(tape.ts[k])
                s = self._sec(t)
                self._outputs(t, self.fib.on_trade(s, p))
                for touch in self.touches_engine.on_trade(s, p):
                    self.touches.append((t, touch))
                last_price, last_drawing = p, self.fib.drawing
                self.fed += 1
            i = j
            b = int(np.searchsorted(bars_1m.start, m))
            if b < len(bars_1m) and int(bars_1m.start[b]) == m:
                out = self.fib.on_bar(self._sec(m), self._sec(m + MIN_NS), float(bars_1m.open[b]),
                                      float(bars_1m.close[b]))
                self._outputs(m + MIN_NS, out)
        self._outputs(self.close_ns, self.fib.advance(self._sec(self.close_ns)))

    # --- queries -------------------------------------------------------------------------------------------------
    def drawing_at(self, t_ns: int) -> gz.Drawing | None:
        k = bisect_right(self.timeline_t, t_ns) - 1
        return self.timeline[k] if k >= 0 else None

    def ib_at(self, t_ns: int) -> list[IbLevel]:
        return [lv for lv in self.ib_levels if lv.created_ns <= t_ns and (lv.end_ns is None or lv.end_ns > t_ns)]

    def features_at(self, t_ns: int, tape: Tape, on_high: float | None, on_low: float | None,
                    atr: float | None = None, em: float | None = None, ib_avg: float | None = None) -> dict[str, Any]:
        d = self.drawing_at(t_ns)
        out: dict[str, Any] = {k: None for k in OUTPUTS}
        if d is None:
            return out
        # the drawing's own flip count restarts with the anchors (owner 2026-10-16), so with d.flips > 0 the last
        # flip at or before t belongs to it
        flips = [f for f in self.flips_at if f <= t_ns]
        lock = self._ns(self.fib.t_lock)
        out.update(fib_drawing=d.source, fib_orientation=d.orientation, fib_flips=d.flips,
                   minutes_since_flip=(t_ns - flips[-1]) / MIN_NS if d.flips and flips else None,
                   range_pts=d.high - d.low, redraws_before_lock=sum(1 for x in self.redraws_t if x <= t_ns),
                   minutes_after_open_established=self.fib.t_start / 60)
        if t_ns >= lock:
            out["minutes_since_lock"] = (t_ns - lock) / MIN_NS
            out["range_pts_1000"] = self.range_1000
            p = tape.price[tape.idx_before(lock):tape.idx_at(t_ns)]
            out["left_ib_since_lock"] = bool(len(p) and (p.max() > self.fib.lock_h or p.min() < self.fib.lock_l))
        if t_ns >= self._ns(self.fib.t_redraw):
            out["fib_redrawn_1030"] = self.redrawn_1030
            out["range_pts_1030"] = self.range_1030
        if on_high is not None and on_low is not None:
            out["range_broke_on"] = bool(d.high > on_high or d.low < on_low)
        if atr:
            out["range_atr"] = out["range_pts"] / atr
        if em:
            out["range_em"] = out["range_pts"] / em
        if ib_avg:
            out["range_vs_avg"] = out["range_pts"] / ib_avg
        out["shallow_band"] = d.band(*self.fib.fibs(gz.SHALLOW))
        out["deep_band"] = d.band(*self.fib.fibs(gz.DEEP))
        out["fib_prices"] = {x: d.level(x) for x in EXTENSIONS}
        return out

    def fib_level_reached(self, t0_ns: int, t1_ns: int, tape: Tape, levels) -> float | None:
        """The deepest golden fib level reached by trades in [t0, t1] on the drawing in force at t1 (a pair column);
        `levels` = `golden_fib_levels` (features.yaml)."""
        d = self.drawing_at(t1_ns)
        if d is None or d.high == d.low:
            return None
        p = tape.price[tape.idx_before(t0_ns):tape.idx_at(t1_ns)]
        if not len(p):
            return None
        span = d.high - d.low
        x = (d.high - p) / span if d.orientation == gz.LONG else (p - d.low) / span
        reached = [lv for lv in levels if x.max() >= lv]
        return max(reached) if reached else None


OUTPUTS = ("fib_drawing", "fib_orientation", "fib_flips", "minutes_since_flip", "fib_redrawn_1030", "range_pts",
           "range_pts_1000", "range_pts_1030", "range_atr", "range_em", "range_vs_avg", "redraws_before_lock",
           "minutes_after_open_established", "minutes_since_lock", "left_ib_since_lock", "range_broke_on",
           "shallow_band", "deep_band", "fib_prices")
