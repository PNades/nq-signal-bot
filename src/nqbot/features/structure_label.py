"""F11.4 structure label (SPEC §7.1 Structure label; feature_spec F11.4), shared with step 13.

For an event touched at t0 in role ρ (ceiling: hold = down, floor: hold = up):
- **held**: after the touch, a trade `hold_reaction_pts` + 1 tick (the target-style trade-through, §5.5b) beyond the
  touch's extreme in the hold direction (the extreme = the deepest price reached into or past the zone since the touch,
  touch trade included), before the zone is crossed;
- **broke**: the zone is crossed (F5.4): every current member is crossed against ρ by its own rule (members that
  joined after the touch count; post-cross retests: the provisional role crossed back), or its last member leaves by
  such a cross. Golden zones: a 1-minute bar settled past that zone's far edge (`OpeningRange.breaks`); gameplan zones:
  a trade past the far edge (the container machine);
- **timeout**: neither within `hold_window_min`, or the zone loses all its members without a cross (a golden zone
  replaced by a redraw), at that moment.
The window ends at min(t0 + `hold_window_min`, the session flat-by) for touches before the flat-by (SPEC §1.2: the
flat-by sets the label timeout), else at min(t0 + `hold_window_min`, the CME day's end). The resolution time is what
freshness (F5.11), day memory (F5.12) and retested extremes (F3.8) read, each only once it is <= t (F0.1).
These labels are outcomes: they go to the outcome files, never into a feature row.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from nqbot.features.session_tape import MIN_NS, TICK, Tape
from nqbot.features.touches import CEILING, Touch

HELD, BROKE, TIMEOUT = "held", "broke", "timeout"


@dataclass(frozen=True)
class Label:
    event_id: str
    label: str
    t_resolved: int
    extreme: float          # the touch's extreme (deepest into / past the zone) up to the resolution
    run_pts: float          # the largest move from the running extreme in the hold direction before the resolution
    how: str                # held / broke / window / zone_ended


def crossed_against(track, rho: str, t0: int, t: int) -> bool:
    return any(t0 < tc <= t and r == rho for tc, r in track.crosses) and track.role_at(t) != rho


def zone_cross(book, levels: dict, zid: str, rho: str, t0: int, t_end: int, frozen: dict | None = None
               ) -> tuple[int, str] | None:
    """(time, 'broke' | 'ended') for a level zone, or None within (t0, t_end].
    `frozen`: single-print members at the touch, {uid: (cross time against the band frozen at the touch or None,
    end time)} (F3.5: an event judges a single print against its band frozen at the touch; it ends when filled)."""
    frozen = frozen or {}
    times = sorted({T for T in book.times if t0 < T <= t_end} |
                   {x for c, e in frozen.values() for x in (c, e) if x is not None and t0 < x <= t_end})
    prev = [m for m in book.members(zid, t0) if m not in frozen]
    cur_id = zid
    for T in times:
        z = book.follow(cur_id, T) if cur_id is not None else None
        live = [] if z is None else [m for m in book.members(z, T) if m not in frozen]
        if z is not None:
            cur_id = z
        alive_sp = [u for u, (c, e) in frozen.items() if (c is None or c > T) and e > T]
        crossed_sp_now = [u for u, (c, e) in frozen.items() if c == T]
        if not live and not alive_sp:
            left = [m for m in prev if m in levels]
            if crossed_sp_now or any((T, rho) in levels[m].track.crosses for m in left):
                return T, BROKE
            return T, "ended"
        if live and not alive_sp and all(crossed_against(levels[m].track, rho, t0, T) for m in live):
            return T, BROKE
        prev = live
    return None


def frozen_single_print(level, bars_1m, rho: str, t0: int, t_end: int) -> tuple[int | None, int]:
    """A single-print member's (cross time, end time) against its band frozen at the touch: the close of the first
    1-minute bar closing after the touch that settles beyond it in rho's breaking direction (F0.5, margin 0)."""
    b = level.band.at(t0)
    end = int(level.band.end_ns) if getattr(level.band, "end_ns", None) is not None else t_end + 1
    if b is None:
        return None, end
    lo, hi = b
    up = 1 if rho == CEILING else -1
    ends = bars_1m.start + bars_1m.length_ns
    sel = (ends > t0) & (ends <= min(t_end, end))
    if up > 0:
        hit = sel & (bars_1m.open > hi) & (bars_1m.close > hi)
    else:
        hit = sel & (bars_1m.open < lo) & (bars_1m.close < lo)
    k = np.flatnonzero(hit)
    return (int(ends[k[0]]) if len(k) else None), end


def container_cross(level, rho: str, t0: int, t_end: int) -> tuple[int, str] | None:
    for tc, r in level.track.crosses:
        if t0 < tc <= t_end and r == rho:
            return tc, BROKE
    return None


def golden_cross(orr, zone_n: int, t0: int, t_end: int) -> tuple[int, str] | None:
    """A break of that golden zone, or its replacement (a reversal before 10:00, the 10:30 redraw) = ended."""
    best = None
    for t, b in orr.breaks:
        if b.zone_id == zone_n and t0 < t <= t_end:
            best = (t, BROKE)
            break
    for t, r in orr.redraws:
        if zone_n in r.old_zone_ids and t0 < t <= t_end and (best is None or t < best[0]):
            best = (t, "ended")
            break
    return best


def label(touch: Touch, tape: Tape, cross: tuple[int, str] | None, t_end: int, hold_pts: float) -> Label:
    i = touch.idx
    j = tape.idx_at(t_end)
    p = tape.price[i:j]
    short = touch.role == CEILING
    if short:
        ext = np.maximum.accumulate(p)
        held = np.flatnonzero(p <= ext - hold_pts - TICK + 1e-9)
        run = ext - p
    else:
        ext = np.minimum.accumulate(p)
        held = np.flatnonzero(p >= ext + hold_pts + TICK - 1e-9)
        run = p - ext
    t_held = int(tape.ts[i + held[0]]) if len(held) else None
    t_cross, how = cross if cross is not None else (None, None)
    if t_held is not None and (t_cross is None or t_held < t_cross):
        res, lab, why = t_held, HELD, HELD
    elif t_cross is not None and how == BROKE:
        res, lab, why = t_cross, BROKE, BROKE
    elif t_cross is not None:
        res, lab, why = t_cross, TIMEOUT, "zone_ended"
    else:
        res, lab, why = t_end, TIMEOUT, "window"
    k = int(np.searchsorted(tape.ts[i:j], res, side="right"))
    k = max(k, 1)
    return Label(touch.event_id, lab, int(res), float(ext[min(k, len(ext)) - 1]) if len(ext) else touch.price,
                 float(run[:k].max()) if len(run) else 0.0, why)


def window_end(t0: int, hold_window_min: float, flat_by_ns: int, day_end_ns: int) -> int:
    cap = flat_by_ns if t0 < flat_by_ns else day_end_ns
    return int(min(t0 + int(hold_window_min * MIN_NS), cap))
