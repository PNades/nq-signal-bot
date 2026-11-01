"""Touch and re-arm, events and touch groups (SPEC §5.4 Touch and re-arm, IDs, Direction, Overlapping events;
feature_spec F5.6, F5.7, F5.10).

Per zone (`zone_id`), over its membership segments (the zone book), each trade judged against the zone's edges as of
that trade (the union of its members' bands, F5.4, F5.17):
- **touch band** = [lo − `zone_proximity`, hi + `zone_proximity`]; **re-arm band** = ± (`zone_proximity` +
  `rearm_dist`);
- **armed** at the first trade outside the re-arm band after it was last disarmed; the **approach side** = the side
  price is on then (+1 above: a floor, long; −1 below: a ceiling, short), unchanged while armed;
- **touch** = the first trade, while armed, inside the touch band that price itself entered: the previous trade,
  judged against the same (current) edges, was outside it. If the previous trade is inside too, the zone moved onto
  price: no touch, disarmed (§5.4 "a zone moving onto price is not a touch"). A trade beyond the touch band on the
  other side (a gap, or an update that moved the zone across price) disarms it too, without a touch;
- **unresolved breaks**: while a member's break is unresolved (a high/low inside its sweep window within
  `sweep_max_depth`; a chart level beyond its band without a settled 1-minute bar; a provisional role being crossed
  back the same way), trades on that far side don't re-arm the zone, so coming back inside is the same event;
- a zone founded (or moved) on top of price starts unarmed; an absorbed zone's arming state ends with it; a merge
  survivor keeps its own.
Containers: gameplan zones use the same rule (single-member zones that never merge); the golden zones use
`golden_zone.GoldenTouches` (owner 2026-10-27), recorded by `OpeningRange`.

Events (F5.7): `event_id` = `<UTC timestamp, second resolution>-<zone_id>`; direction from the approach side (golden
zones: the fib orientation). Touch groups (F5.10): events touched by the same trade (same `ts_event` and sequence)
share a group; a deep-golden-zone touch always starts its own group, and the other zones touched by that trade join
whichever golden zone contains them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np

from nqbot.features import level_state as ls
from nqbot.features.session_tape import MIN_NS, NS, Tape

CEILING, FLOOR = ls.CEILING, ls.FLOOR
MAX_STEPS = 1_000_000   # a guard on the per-zone loop


def event_id(t_ns: int, zone_id: str) -> str:
    return f"{datetime.fromtimestamp(t_ns // NS, timezone.utc):%Y%m%dT%H%M%SZ}-{zone_id}"


def order_id(event: str, style: str, n: int, account: str, leg: str) -> str:
    """§5.4 / §10.3: `<event_id>-<entry style><n>-<account>-<leg>`."""
    return f"{event}-{style}{n}-{account}-{leg}"


def role_of(side: int) -> str:
    return FLOOR if side > 0 else CEILING


@dataclass
class Touch:
    zone_id: str
    t: int                  # the touch trade's ts_event
    idx: int                # its index in the tape (same trade = same group)
    price: float
    side: int               # approach side: +1 from above (long), -1 from below (short)
    members: tuple          # uids at the touch
    lo: float               # zone edges at the touch
    hi: float
    kind: str = "level"     # level / gameplan / golden
    part: str | None = None          # golden_zone_part
    group: int | None = None
    event_id: str = ""

    @property
    def direction(self) -> int:
        return 1 if self.side > 0 else -1

    @property
    def role(self) -> str:
        return role_of(self.side)


@dataclass
class ZoneTouches:
    tape: Tape
    book: Any                        # zones.ZoneBook
    levels: dict                     # uid -> RegLevel
    prox: float
    rearm: float
    touches: list = field(default_factory=list)
    disarms: list = field(default_factory=list)     # (zone_id, t, reason) for reports

    def run(self) -> ZoneTouches:
        for zid in self.book.zone_ids():
            self._zone(zid)
        self.touches.sort(key=lambda x: (x.idx, x.zone_id))
        return self

    def _zone(self, zid: str) -> None:
        tape = self.tape
        armed, side = False, 0
        for a, z, members in self.book.segments(zid):
            i, j = tape.idx_before(a), tape.idx_before(z)
            if j <= i:
                continue
            lo, hi = _edges([self.levels[m] for m in members], i, j)
            p = tape.price[i:j]
            prev = np.r_[tape.price[i - 1] if i > 0 else np.nan, p[:-1]]
            pr, rd = self.prox, self.rearm
            k = 0
            steps = 0
            while k < len(p):
                steps += 1
                if steps > MAX_STEPS:
                    raise RuntimeError(f"zone {zid}: no progress")
                pk, lk, hk, qk = p[k:], lo[k:], hi[k:], prev[k:]
                with np.errstate(invalid="ignore"):
                    if not armed:
                        up = np.flatnonzero(pk > hk + pr + rd)
                        dn = np.flatnonzero(pk < lk - pr - rd)
                        e = min((x[0] for x in (up, dn) if len(x)), default=None)
                        if e is None:
                            break
                        s = 1 if len(up) and up[0] == e else -1
                        t = int(tape.ts[i + k + e])
                        hold = self._suppressed(members, s, t)
                        if hold is not None:
                            nxt = tape.idx_before(hold)
                            k = max(k + e + 1, nxt - i)
                            continue
                        armed, side = True, s
                        k += e + 1
                        continue
                    inside = (pk >= lk - pr) & (pk <= hk + pr)
                    was_in = (qk >= lk - pr) & (qk <= hk + pr)
                    other = (pk < lk - pr) if side > 0 else (pk > hk + pr)
                    hits = [(np.flatnonzero(inside & ~was_in), "touch"), (np.flatnonzero(inside & was_in), "onto"),
                            (np.flatnonzero(other), "across")]
                    e, what = min(((x[0], w) for x, w in hits if len(x)), default=(None, None))
                    if e is None:
                        break
                    idx = i + k + e
                    t = int(tape.ts[idx])
                    if what == "touch":
                        self.touches.append(Touch(zid, t, idx, float(p[k + e]), side, tuple(members),
                                                  float(lo[k + e]), float(hi[k + e])))
                    else:
                        self.disarms.append((zid, t, what))
                    armed = False
                    k += e + 1

    def _suppressed(self, members, s: int, t: int) -> int | None:
        """An unresolved break on side s among the members at t -> when to look again (else None)."""
        until = None
        for m in members:
            lv = self.levels[m]
            st = lv.state.state_at(t)
            hit = False
            if lv.kind == ls.SWEEP and st.unresolved is not None and st.unresolved["d"] == s:
                hit = True
            elif lv.kind in ls.BAR_CROSS and st.role is not None:
                if not st.crossed and ls.up(st.role) == s:
                    hit = True
                elif st.crossed and not st.broken_back and -ls.up(st.orig_role) == s:
                    hit = True
            if hit:
                nxt = _next_change(lv.state, t)
                until = nxt if until is None else min(until, nxt)
        return until


def _next_change(state: ls.LevelState, t: int) -> int:
    """The next instant the level's break can resolve: its next transition, or the next 1-minute boundary."""
    nxt = t - t % MIN_NS + MIN_NS
    for te, _ in state.events:
        if te > t:
            return min(te, nxt)
    return nxt


def _edges(levels: list, i: int, j: int) -> tuple[np.ndarray, np.ndarray]:
    los, his = [], []
    for lv in levels:
        lo, hi = lv.band.at_trades(i, j)
        los.append(lo)
        his.append(hi)
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            return np.nanmin(np.vstack(los), axis=0), np.nanmax(np.vstack(his), axis=0)


def golden_touches(orr, tape: Tape) -> list[Touch]:
    """The golden zones' touches recorded by `OpeningRange` (owner 2026-10-27 rules), as events."""
    from nqbot.features import golden_zone as gz

    out = []
    for (t, tc), idx in zip(orr.touches, orr.touch_idx):
        d = orr.drawing_at(t)
        side = 1 if d.orientation == gz.LONG else -1
        lo, hi = d.band(*orr.fib.fibs(tc.part))
        out.append(Touch(golden_zone_id(tc.zone_id), int(t), int(idx), float(tape.price[idx]), side,
                         (), float(lo), float(hi), kind="golden", part=tc.part))
    return out


def golden_zone_id(n: int) -> str:
    return f"g{n}"


def assign_groups(touches: list[Touch]) -> list[Touch]:
    """F5.10 touch groups and F5.7 event ids (in place; returns the touches in time order)."""
    touches.sort(key=lambda x: (x.idx, x.kind != "golden", x.zone_id))
    n = 0
    k = 0
    while k < len(touches):
        j = k
        while j < len(touches) and touches[j].idx == touches[k].idx:
            j += 1
        same = touches[k:j]
        deep = [x for x in same if x.kind == "golden" and x.part == "deep"]
        rest = [x for x in same if x not in deep]
        for d in deep:
            n += 1
            d.group = n
        if rest:
            n += 1
            for x in rest:
                x.group = n
        for x in rest:   # zones inside a deep golden zone touched by this trade group with it
            if x.kind == "golden":
                continue
            c = (x.lo + x.hi) / 2
            for d in deep:
                if d.lo <= c <= d.hi:
                    x.group = d.group
                    break
        k = j
    seen: dict[str, int] = {}
    for x in touches:
        eid = event_id(x.t, x.zone_id)
        n_same = seen.get(eid, 0)
        seen[eid] = n_same + 1
        # two touches of one zone in the same second (a re-arm and a second touch inside one second): the later one
        # gets a suffix so order ids stay unique (§10.3)
        x.event_id = eid if n_same == 0 else f"{eid}.{n_same}"
    return touches
