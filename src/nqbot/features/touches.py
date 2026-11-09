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
  survivor keeps its own;
- **own re-arm (step 13, owner 2026-11-03, SPEC §13 Q-step12b (c), (d)):** a crossed member in its provisional role
  (crossed, not broken back) also arms the zone, on its crossed side, at the first trade >= `rearm_dist` beyond its
  own band there (after the zone was last disarmed): a post-cross retest needs only the member's own re-arm, not the
  zone's. Armed that way (and not also by the zone's own re-arm band), the touch, "onto" and "across" tests read that
  member's touch band (its band ± `zone_proximity`), so a merged zone already holding price doesn't disarm it: the
  retest is price coming back to the member (the zone's event, its edges and members as usual);
- **formation touch (`node_role_at_formation` = `strike_side`, Q-step12a (4); owner 2026-11-09):** the trade at which
  a node member took its strike-side formation role (`level_state` `formations`: the first trade after the node
  became relevant, inside its touch band) is a touch of the zone in that role, armed or not; it groups like any touch.
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
# Q-step12b (c) (owner 2026-11-03): the member's own re-arm. A diagnostic switch of the step-13-0 check only (False =
# features_version <= 36); every feature build refuses another value (`features.diagnostics.require_defaults`).
OWN_REARM = True


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
    own_arms: int = 0                                # zones armed by a member's own re-arm (reports)
    formation_touches: int = 0                       # formation touches recorded (reports)

    def run(self) -> ZoneTouches:
        for zid in self.book.zone_ids():
            self._zone(zid)
        self.touches.sort(key=lambda x: (x.idx, x.zone_id))
        return self

    def _zone(self, zid: str) -> None:
        tape = self.tape
        armed, side, via = False, 0, None
        for a, z, members in self.book.segments(zid):
            i, j = tape.idx_before(a), tape.idx_before(z)
            if j <= i:
                continue
            lo, hi = _edges([self.levels[m] for m in members], i, j)
            p = tape.price[i:j]
            prev = np.r_[tape.price[i - 1] if i > 0 else np.nan, p[:-1]]
            pr, rd = self.prox, self.rearm
            own = self._own_rearm(members, i, j) if OWN_REARM else {}
            own_up = own_dn = None
            if own:
                own_up = np.logical_or.reduce([u for u, _, _, _ in own.values()])
                own_dn = np.logical_or.reduce([d for _, d, _, _ in own.values()])
            if via is not None and via not in own:
                via = None   # the member that armed the zone left it or is no longer provisional
            forms = self._formations(members, i, j)     # [(segment index, role)], sorted
            k = 0
            steps = 0
            held = {1: 0, -1: 0}   # per side: the segment index before which that side can't arm (an unresolved break)
            while k < len(p):
                steps += 1
                if steps > MAX_STEPS:
                    raise RuntimeError(f"zone {zid}: no progress")
                pk, lk, hk, qk = p[k:], lo[k:], hi[k:], prev[k:]
                fk = next((f for f in forms if f[0] >= k), None)
                with np.errstate(invalid="ignore"):
                    if not armed:
                        up_m, dn_m = pk > hk + pr + rd, pk < lk - pr - rd
                        if own_up is not None:
                            up_m, dn_m = up_m | own_up[k:], dn_m | own_dn[k:]
                        up = np.flatnonzero(up_m)
                        dn = np.flatnonzero(dn_m)
                        up = up[up + k >= held[1]]
                        dn = dn[dn + k >= held[-1]]
                        e = min((x[0] for x in (up, dn) if len(x)), default=None)
                        if fk is not None and (e is None or fk[0] - k <= e):
                            k = self._formation_touch(zid, fk, i, p, lo, hi, members)
                            continue
                        if e is None:
                            break
                        s = 1 if len(up) and up[0] == e else -1
                        t = int(tape.ts[i + k + e])
                        hold = self._suppressed(members, s, t)
                        if hold is not None:
                            # only that (far) side waits for the break to resolve; the other side can still re-arm
                            held[s] = max(k + e + 1, tape.idx_before(hold) - i)
                            k += e + 1
                            continue
                        armed, side, via = True, s, None
                        if own_up is not None and not ((pk[e] > hk[e] + pr + rd) if s > 0 else (pk[e] < lk[e] - pr - rd)):
                            # armed by a member's own re-arm only: its band governs the touch (Q-step12b (c))
                            via = next(m for m, (u, d, _, _) in sorted(own.items()) if (u if s > 0 else d)[k + e])
                            self.own_arms += 1
                        k += e + 1
                        continue
                    if via is not None:
                        lk, hk = own[via][2][k:], own[via][3][k:]
                    inside = (pk >= lk - pr) & (pk <= hk + pr)
                    was_in = (qk >= lk - pr) & (qk <= hk + pr)
                    other = (pk < lk - pr) if side > 0 else (pk > hk + pr)
                    hits = [(np.flatnonzero(inside & ~was_in), "touch"), (np.flatnonzero(inside & was_in), "onto"),
                            (np.flatnonzero(other), "across")]
                    e, what = min(((x[0], w) for x, w in hits if len(x)), default=(None, None))
                    if fk is not None and (e is None or fk[0] - k <= e):
                        armed, via = False, None
                        k = self._formation_touch(zid, fk, i, p, lo, hi, members)
                        continue
                    if e is None:
                        break
                    idx = i + k + e
                    t = int(tape.ts[idx])
                    if what == "touch":
                        self.touches.append(Touch(zid, t, idx, float(p[k + e]), side, tuple(members),
                                                  float(lo[k + e]), float(hi[k + e])))
                    else:
                        self.disarms.append((zid, t, what))
                    armed, via = False, None
                    k += e + 1

    def _formation_touch(self, zid: str, fk: tuple, i: int, p, lo, hi, members) -> int:
        """Record a formation touch at segment index fk[0] in role fk[1]; returns the next index to look at."""
        k = fk[0]
        side = 1 if fk[1] == FLOOR else -1
        self.touches.append(Touch(zid, int(self.tape.ts[i + k]), i + k, float(p[k]), side, tuple(members),
                                  float(lo[k]), float(hi[k])))
        self.formation_touches += 1
        return k + 1

    def _formations(self, members, i: int, j: int) -> list[tuple[int, str]]:
        """The members' formation-role trades inside the segment's trades [i, j), as (index into the segment, role)."""
        out = []
        for m in members:
            for _, idx, role in getattr(self.levels[m].state, "formations", ()):
                if i <= idx < j:
                    out.append((idx - i, role))
        return sorted(set(out))

    def _own_rearm(self, members, i: int, j: int) -> dict:
        """{member uid: (up, down, lo, hi)} over the trades [i, j) for the members that ever are, there, in their
        provisional role (crossed, not broken back) >= `rearm_dist` beyond their own band on the crossed side (up: above,
        a long retest; down: below); lo / hi = the member's band as of each trade."""
        tape = self.tape
        ts = tape.ts[i:j]
        out = {}
        for m in members:
            lv = self.levels[m]
            st = lv.state
            if lv.kind == ls.CONTAINER:
                continue
            cuts = [int(ts[0])] + [int(t) for t, _ in st.events if ts[0] < t <= ts[-1]]
            if not st.state_at(cuts[0]).crossed and len(cuts) == 1:
                continue
            lo_b = hi_b = None
            up_m = np.zeros(j - i, dtype=bool)
            dn_m = np.zeros(j - i, dtype=bool)
            for c0, c1 in zip(cuts, cuts[1:] + [int(ts[-1]) + 1]):
                x = st.state_at(c0)
                if not (x.role is not None and x.crossed and not x.broken_back):
                    continue
                a = int(np.searchsorted(ts, c0, side="left"))
                z = int(np.searchsorted(ts, c1, side="left"))
                if z <= a:
                    continue
                if lo_b is None:
                    lo_b, hi_b = lv.band.at_trades(i, j)
                d = ls.up(x.orig_role)
                with np.errstate(invalid="ignore"):
                    hit = np.nan_to_num(ls.beyond(tape.price[i + a:i + z], lo_b[a:z], hi_b[a:z], d), nan=-np.inf) \
                        >= self.rearm
                (up_m if d > 0 else dn_m)[a:z] |= hit
            if up_m.any() or dn_m.any():
                out[m] = (up_m, dn_m, lo_b, hi_b)
        return out

    def _suppressed(self, members, s: int, t: int) -> int | None:
        """An unresolved break on side s among the members at t -> when to look again (else None)."""
        until = None
        px = self.tape.last_price_at(t)
        for m in members:
            lv = self.levels[m]
            st = lv.state.state_at(t)
            hit = False
            # a chart-level break is unresolved only while price is beyond its band on side s (always so when the zone's
            # own re-arm band is crossed; not so for a member's own re-arm inside another member, Q-step12b (c))
            b = lv.band.at(t)
            beyond = px is not None and b is not None and ls.beyond(px, b[0], b[1], s) > 0
            if lv.kind == ls.SWEEP and st.unresolved is not None and st.unresolved["d"] == s:
                hit = True
            elif lv.kind in ls.BAR_CROSS and st.role is not None and beyond:
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
