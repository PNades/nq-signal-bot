"""One session's zones, events, structure labels and feature rows (step 11 part 3; SPEC §5.4, §5.4b, §5.6, §7.1;
feature_spec F5.1-F5.13, F5.16, F5.18, F8, F10, F11.4, F11.7).

`SessionFeatures.build()`:
1. the NQ and ES registries (`registry.py`): part 1's chart levels, part 2's nodes (QQQ / NDXP on NQ, SPY / SPX on ES),
   the gameplan containers; the golden zones come from part 1's `OpeningRange`;
2. the zone books (`zones.ZoneBook`; ES with the NQ parameters × ES ÷ NQ at the open, F0.11);
3. touches (`touches.py`: level zones, gameplan zones, golden zones), touch groups and event ids;
4. structure labels (`structure_label.py`), each evaluated at the distances the session reads (owner 2026-11-06, SPEC §13
   Q-density (c)): `hold_reaction_pts` (the structure label, 30), `history_hold_pts` (the outcome-history features:
   freshness, day memory, golden-zone history, 30), `retest_hold_pts` (F3.8, 20) and `pair_end_hold_pts` (the pair
   cut, Q-step11h (1): no pair at or after the event's own resolution at this distance, 20);
5. retested extremes (F3.8): a held touch within `retest_tol_ticks` of an earlier session's high/low creates a
   `retested_extreme` level at the label's resolution time; a new level can only change what happens after its
   creation, so each pass accepts only the earliest new retest(s) and the steps above are re-run until no new level
   appears (the same levels a single pass in time order, as live, would create).
`row(event, t)` is every structure-model input of an event as of t (t = the touch for the structure model; step 13
calls it at each pair's `decision_ts`, the same code). It reads only data with known time <= t; outcomes enter only as
labels resolved by t (freshness, day memory, earlier golden-zone touches). `end_state()` is what the next session needs
(the pass-1 checkpoint's part-3 state: multi-day touch records, retested extremes, retest candidates, today's EOD day
type).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from statistics import mean
from typing import Any

import numpy as np

from nqbot.features import calendar_flags as cf
from nqbot.features import cross_market as cm
from nqbot.features import daytype as dtm
from nqbot.features import golden_zone as gz
from nqbot.features import level_state as ls
from nqbot.features import registry as rg
from nqbot.features import session_levels as sl
from nqbot.features import structure_label as slab
from nqbot.features import touches as tch
from nqbot.features.approach import approach_features
from nqbot.features.bands import values_at
from nqbot.features.chart_levels import SUBTYPE_KEY
from nqbot.features.daytype_rows import DayType
from nqbot.features.es_level import EsZone, es_at_level
from nqbot.features.expected_move import dist3
from nqbot.features.freshness import TouchRecord, level_freshness, zone_freshness
from nqbot.features.gameplan_features import Gameplan
from nqbot.features.session_tape import MIN_NS, et_ns, rth_open_ns
from nqbot.features.zones import ZoneBook

NASDAQ = ("QQQ", "NDXP")
SP = ("SPY", "SPX")
MAPS = ("QQQ", "NDXP", "SPY", "SPX")
NQ_SUBTYPES = tuple(sorted(SUBTYPE_KEY)) + ("qqq_node", "ndxp_node")
RTYPES = ("gex_node", "vwap", "volume_profile", "session_hl", "opening_range", "expected_move")
MAX_RETEST_PASSES = 64   # a guard: one pass per distinct retest creation time in the session, plus one
MAX_ALT_BUILDS = 2       # sessions built without one touch group's own retested extremes kept at once (§11 Low memory)
ALT_MARGIN_NS = 5 * 60 * 10**9   # a horizon-limited such build runs 5 min (one 5-minute flip bar, F2.6b) past its horizon
NODE_TYPES = ("king", "gatekeeper", "significant", "normal")
# the F11.4 distances (owner 2026-11-06, SPEC §13 Q-density (c)): structure label, history features, F3.8, pair cut
HOLD_KEYS = ("hold_reaction_pts", "history_hold_pts", "retest_hold_pts", "pair_end_hold_pts")


@dataclass
class SessionContext:
    day: date
    features: dict
    settings: dict
    cal: dict
    cs_nq: Any                       # chart_levels.ChartSession (NQ)
    cs_es: Any                       # chart_levels.ChartSession (ES)
    sky: Any = None                  # skylit_session.SkylitSession, or None (no boards stored)
    vol: dict = field(default_factory=dict)
    flags: dict = field(default_factory=dict)
    mxe: Any = None                  # multiexp.MultiExp
    gameplan: dict | None = None     # usable effective gameplan (gameplan_features.usable)
    history: list = field(default_factory=list)          # carried TouchRecord of multi-day levels
    yesterday_day_type: str | None = None
    retested: list = field(default_factory=list)         # session_levels.Retested in force from earlier sessions
    retest_candidates: list = field(default_factory=list)  # session_levels.Candidate of earlier sessions
    sessions: list = field(default_factory=list)         # every planned session (for lookbacks)
    skylit_note: str | None = None


class NotWired(Exception):
    """A configuration this engine does not build (dark pools in the model, F4: added at a later retrain)."""


class FixedBook:
    """The zone-book interface for containers: one zone per container, never merged (F5.9)."""

    def __init__(self, levels: list, end_ns: int):
        self.lv = {x.uid: x for x in levels}
        self.end_ns = end_ns
        self.times = sorted({a for x in levels for a, _ in x.spans} | {z for x in levels for _, z in x.spans})

    def zone_ids(self) -> list[str]:
        return sorted(self.lv)

    def segments(self, zid: str) -> list:
        return [(a, z, (zid,)) for a, z in self.lv[zid].spans]

    def members(self, zid: str, t: int) -> tuple:
        return (zid,) if self.lv[zid].active(t) else ()

    def follow(self, zid: str, t: int) -> str | None:
        return zid if self.lv[zid].active(t) else None


class SessionFeatures:
    horizon_ns: int | None = None   # the whole session (a horizon-limited world sets it, `__init__`)

    def __init__(self, ctx: SessionContext, exclude_source: str | frozenset | None = None,
                 horizon_ns: int | None = None):
        """`exclude_source`: build the session as if the held labels of those events (one event id, or a touch group's
        event ids) had made no retested extreme: the world a touch group's rows are read from after such a level
        appears (Q-step11g (2); the whole touch group is the event's own, owner 2026-11-01, SPEC §13 Q-step11h (2)).
        `horizon_ns` (such a world only; owner 2026-11-12, SPEC §13 Q-step13b (e)): build it only to `horizon_ns` +
        `ALT_MARGIN_NS`; the build is causal (every quantity at t from data and levels at or before t, the global
        no-lookahead tests), so everything it answers at or before `horizon_ns` equals the whole-session build's;
        `world` never reads it later than that."""
        self.c = ctx
        self.exclude_source = (None if exclude_source is None else frozenset([exclude_source])
                               if isinstance(exclude_source, str) else frozenset(exclude_source))
        if horizon_ns is not None and self.exclude_source is None:
            raise ValueError("a horizon is only for a session built without a touch group's retested extremes")
        self._alts: dict[str, SessionFeatures] = {}
        self._own_out: dict[str, tuple] = {}   # event id -> its own (structure label, pair cut), `own_outcome`
        self._mine_uids: set[str] = set()
        self.f, self.day = ctx.features, ctx.day
        cs = ctx.cs_nq
        self.nq, self.es = cs.tape, ctx.cs_es.tape
        self.end_ns = cs.end_ns
        self.horizon_ns: int | None = None    # None: the whole session; else the latest time this build answers for
        if horizon_ns is not None and int(horizon_ns) + ALT_MARGIN_NS < cs.end_ns:
            self.horizon_ns = int(horizon_ns)
            self.end_ns = int(horizon_ns) + ALT_MARGIN_NS
        self.open_ns = rth_open_ns(self.day)
        self.flat_ns = int(cf.flat_by(self.day, ctx.cal, ctx.settings).timestamp()) * 10**9
        self.entry_end_ns = int(cf.entry_end(self.day, ctx.cal, ctx.settings).timestamp()) * 10**9
        self.golden_from = self.open_ns + int(self.f["ib_fib_start_minute"]) * MIN_NS
        self.all_from = self.open_ns + int(self.f["ib_lock_minute"]) * MIN_NS
        self.blackouts = [(int(a.timestamp()) * 10**9, int(b.timestamp()) * 10**9)
                          for a, b in cf.blackout_spans(self.day, ctx.cal, ctx.settings)]
        self.gp = Gameplan(ctx.gameplan, self.day, self.nq, self.es, ctx.settings, cs.bars_1m)
        self.dt = DayType(cs, self.f, cs.carry.ib_widths(int(self.f["ib_avg_sessions"])))
        self.retested_today: list = []
        self.retest_source: dict[str, str] = {}   # level_id of today's retested extremes -> the event that made it
        self.passes = 0

    # --- build ---------------------------------------------------------------------------------------------------
    def build(self) -> SessionFeatures:
        if self.f.get("dp_in_model"):
            raise NotWired("dp_in_model is true: dark-pool levels are not wired into the zones yet "
                                      "(owner 2026-10-06: added and tested at a later retrain)")
        for _ in range(MAX_RETEST_PASSES):
            self.passes += 1
            self._build_once()
            new = self._new_retests()
            if not new:
                return self
            # causal, as the live engine (one pass in time order): only the earliest new retest(s) are accepted. A
            # level created at t1 can change any touch or label after t1, so a retest found at t2 > t1 in this pass
            # may not exist once the t1 level is in; the next pass finds it again if it still does
            first = min(r.created_ns for r in new)
            for r in (r for r in new if r.created_ns == first):
                self._mine_uids.add(_retest_record(self.c.cs_nq, r))
                self.retested_today.append(r)
                self.retest_source[r.level_id] = r.source
        raise RuntimeError(f"{self.day}: retested extremes did not settle after {MAX_RETEST_PASSES} passes")

    def _build_once(self) -> None:
        c, f, iso = self.c, self.f, self.day.isoformat()
        made = _today_retests(c.cs_nq)   # retested extremes of any build of this session: only this build's own
        nq_levels = [x for x in rg.chart_levels(c.cs_nq, self.end_ns) if x.uid not in made or x.uid in self._mine_uids]
        es_levels = rg.chart_levels(c.cs_es, self.end_ns)
        if c.sky is not None:
            nq_levels += rg.node_levels(c.sky, NASDAQ, iso, self.end_ns)
            es_levels += rg.node_levels(c.sky, SP, iso, self.end_ns)
        self.containers = [rg.container(z.gid, "gameplan_zone", self.nq, z.lo, z.hi, self.open_ns, self.end_ns,
                                        c.cs_nq.bars_1m, c.cs_nq.bars_flip, f) for z in self.gp.zones]
        self.nq_levels = {x.uid: x for x in nq_levels}
        self.es_levels = {x.uid: x for x in es_levels}
        tol, maxw = float(f["confluence_tol"]), float(f["max_zone_width"])
        jb, qb = float(f["zone_join_buffer"]), float(f["zone_qualify_buffer"])   # Q-step12a (2)
        self.book = ZoneBook([x.book_level() for x in nq_levels], self.nq, lambda t: (tol, maxw, jb, qb),
                             end_ns=self.end_ns).run()
        sw = (self._es_switch(),)

        def es_params(t):
            k = self.es_ratio(t)   # every NQ-point parameter × ES ÷ NQ at the open (F0.11)
            return tol * k, maxw * k, jb * k, qb * k
        self.absorbed: dict[str, list] = {}   # survivor zone_id -> [(t, absorbed zone_id)] (Q-step12b (b))
        for young, (tm, old) in self.book.merged_into.items():
            self.absorbed.setdefault(old, []).append((int(tm), young))
        self.es_book = ZoneBook([x.book_level() for x in es_levels], self.es, es_params, param_times=sw,
                                end_ns=self.end_ns).run()
        prox, rearm = float(f["zone_proximity"]), float(f["rearm_dist"])
        zt = tch.ZoneTouches(self.nq, self.book, self.nq_levels, prox, rearm).run()
        level_t = zt.touches
        self.touch_stats = {"own_arms": zt.own_arms, "formation_touches": zt.formation_touches}
        cont = {x.uid: x for x in self.containers}
        gp_zt = tch.ZoneTouches(self.nq, FixedBook(self.containers, self.end_ns), cont, prox, rearm).run()
        gp_t = gp_zt.touches
        for x in gp_t:
            x.kind = "gameplan"
        gold = tch.golden_touches(c.cs_nq.opening, self.nq) if c.cs_nq.opening is not None else []
        # every zone's arm times (step 13b: a style-(c) audit must complete before its zone re-arms, F6.1)
        self.arms: dict[str, list] = {}
        for zid, t in zt.arms + gp_zt.arms:
            self.arms.setdefault(zid, []).append(int(t))
        if c.cs_nq.opening is not None:
            orr = c.cs_nq.opening
            for t, n in orr.touches_engine.arms:
                self.arms.setdefault(tch.golden_zone_id(n), []).append(orr._ns(t))
        for v in self.arms.values():
            v.sort()
        self.events = tch.assign_groups(level_t + gp_t + gold)
        self.by_id = {e.event_id: e for e in self.events}
        self.groups: dict[int, frozenset] = {}
        for e in self.events:
            self.groups[e.group] = self.groups.get(e.group, frozenset()) | {e.event_id}
        at = {e.event_id: self._labels(e) for e in self.events}
        hold, hist, ret, pend = (float(f[k]) for k in HOLD_KEYS)
        self.labels = {k: v[hold] for k, v in at.items()}            # the structure label (F11.4), an outcome
        self.hist_labels = {k: v[hist] for k, v in at.items()}       # what freshness / day memory / gz history read
        self.retest_labels = {k: v[ret] for k, v in at.items()}      # F3.8
        self.pair_end = {k: v[pend] for k, v in at.items()}          # Q-step11h (1): pairs end at its resolution
        self.records = [self._record(e) for e in self.events]

    def _es_switch(self) -> int:
        inp = self.c.cs_es.inp
        return int(max(self.open_ns, inp.scale_from_ns or self.open_ns))

    def es_ratio(self, t: int) -> float:
        """ES ÷ NQ at the open (F0.11): the previous session's before today's is known, today's from then."""
        inp = self.c.cs_es.inp
        return float(inp.scale_today if t >= self._es_switch() else inp.scale_prev)

    def _labels(self, e: tch.Touch) -> dict[float, slab.Label]:
        """The F11.4 label of `e` at every distance the session reads (the cross is the same for all)."""
        f = self.f
        t_end = slab.window_end(e.t, float(f["hold_window_min"]), self.flat_ns, self.end_ns)
        cross = self.cross(e, t_end)
        return {d: slab.label(e, self.nq, cross, t_end, d) for d in sorted({float(f[k]) for k in HOLD_KEYS})}

    def cross(self, e: tch.Touch, t_end: int) -> tuple[int, str] | None:
        """The zone's cross (F5.4, against the event's role) or its end within (touch, t_end]: (time, 'broke' |
        'ended'), else None. The structure label's rule; step 13b reads it to remove a pending entry (F7)."""
        if e.kind == "level":
            bars_1m = self.c.cs_nq.bars_1m
            frozen = {m: slab.frozen_single_print(self.nq_levels[m], bars_1m, e.role, e.t, t_end)
                      for m in e.members if self.nq_levels[m].kind == ls.SINGLE_PRINT}
            return slab.zone_cross(self.book, self.nq_levels, e.zone_id, e.role, e.t, t_end, frozen)
        if e.kind == "gameplan":
            return slab.container_cross(next(x for x in self.containers if x.uid == e.zone_id), e.role, e.t, t_end)
        return slab.golden_cross(self.c.cs_nq.opening, int(e.zone_id[1:]), e.t, t_end)

    def _level_ids(self, e: tch.Touch) -> tuple:
        if e.kind == "level":
            return tuple(sorted({self.nq_levels[m].level_id for m in e.members}))
        return (e.zone_id,)

    def _record(self, e: tch.Touch) -> TouchRecord:
        lab = self.hist_labels[e.event_id]
        return TouchRecord(e.t, self._level_ids(e), e.zone_id, e.role, lab.t_resolved, lab.label)

    def _new_retests(self) -> list:
        f = self.f
        tol = int(f["retest_tol_ticks"])
        in_force = {r.level_id for r in self.c.retested} | {r.level_id for r in self.retested_today}
        touches = [{"price": e.price, "t_resolved": self.retest_labels[e.event_id].t_resolved,
                    "label": self.retest_labels[e.event_id].label, "event_id": e.event_id} for e in self.events]
        cands = sl.dedupe_candidates(self.c.retest_candidates, tol)
        found = sl.retests_from_touches(cands, touches, self.day, self.c.sessions, tol,
                                        int(f["retest_lookback_sessions"]))
        if self.exclude_source is not None:
            found = [r for r in found if r.source not in self.exclude_source]
        if self.horizon_ns is not None:   # one made later can't change anything this build answers (causal)
            found = [r for r in found if r.created_ns <= self.horizon_ns]
        out = []
        for r in sorted((r for r in found if r.level_id not in in_force), key=lambda r: (r.created_ns, r.level_id)):
            # one level per price (owner 2026-11-01, Q-step11g (1)): none within the tolerance of a high/low level
            # (or a retested extreme) that exists at its creation, nor of one accepted just before it
            if self._high_low_near(r.price, r.created_ns, tol) or any(sl.within_tol(r.price, x.price, tol)
                                                                      for x in out):
                continue
            out.append(r)
        return out

    def _high_low_near(self, price: float, t: int, tol: int) -> bool:
        for x in self.nq_levels.values():
            if x.key in sl.HIGHS_LOWS_TYPES or x.key == "retested_extreme":
                b = x.band.at(t)
                if b is not None and sl.within_tol(price, (b[0] + b[1]) / 2, tol):
                    return True
        return False

    def group_of(self, e: tch.Touch) -> frozenset:
        """The event ids of `e`'s touch group (F5.10): the event's own, as for day memory (Q-step11h (2))."""
        return self.groups.get(e.group, frozenset({e.event_id}))

    def _own(self, e: tch.Touch, x) -> bool:
        """A retested extreme made by the held label of `e` or of a touch-group mate (never part of `e`'s rows,
        Q-step11g (2), Q-step11h (2))."""
        return self.retest_source.get(x.level_id) in self.group_of(e)

    # --- queries -------------------------------------------------------------------------------------------------
    def world(self, e: tch.Touch, t: int, need: int | None = None,
              horizon: int | None = None) -> tuple[SessionFeatures, tch.Touch]:
        """The session `e`'s rows at t are read from: this one, or, once the held label of `e` or of a touch-group mate
        has made a retested extreme by t, the session as it would be without those levels (they can merge zones and
        change later labels; owner 2026-11-01, SPEC §13 Q-step11g (2), Q-step11h (2)). The feature build's rows are at
        the touch, always before it; step 13 reads its `decision_ts` rows, zone edges and members through here too
        (and builds no pair at or after the event's own `t_pair_end`, Q-step11h (1)).
        `need`: the latest time the caller reads from the returned world (default t); `horizon` (>= need): how far a
        new such build may stop (owner 2026-11-12, Q-step13b (e): step 13's engine stage passes the touch group's
        latest `t_pair_end` + the band track's span); a cached build is reused only if it reaches `need`, else the
        session is built again (to `horizon`, or whole without one)."""
        grp = self.group_of(e)
        own_t = min((r.created_ns for r in self.retested_today if r.source in grp), default=None)
        if own_t is None or t < own_t:
            return self, e
        need = int(t if need is None else max(int(need), int(t)))
        alt = self._alts.pop(grp, None)
        if alt is not None and alt.horizon_ns is not None and need > alt.horizon_ns:
            alt = None
        if alt is None:
            h = int(horizon) if horizon is not None and int(horizon) >= need else None
            alt = SessionFeatures(self.c, exclude_source=grp, horizon_ns=h).build()
        self._alts[grp] = alt                      # most recently used last
        while len(self._alts) > MAX_ALT_BUILDS:
            self._alts.pop(next(iter(self._alts)))
        return alt, alt.by_id[e.event_id]

    def own_time(self, e: tch.Touch) -> int | None:
        """When `e`'s touch group first made a retested extreme (`world` switches there), else None."""
        grp = self.group_of(e)
        return min((r.created_ns for r in self.retested_today if r.source in grp), default=None)

    def own_cross(self, e: tch.Touch, t_end: int) -> tuple[int, str] | None:
        """The zone's cross within (touch, t_end] as `e`'s own trade sees it (owner 2026-11-13, SPEC §13
        Q-step13-review B2): read on the world its zone edges come from (Q-step11h (2)): this session's before the touch
        group's own retested extreme, the session without it from then (the two agree before it, so a cross found
        earlier stands). Golden and gameplan zones never contain a retested extreme: their cross is this session's."""
        x = self.cross(e, t_end)
        ot = self.own_time(e) if e.kind == "level" and self.exclude_source is None else None
        if ot is None or t_end < ot or (x is not None and x[0] < ot):
            return x
        from nqbot.events.snapshot import group_horizon

        w, we = self.world(e, ot, need=t_end, horizon=group_horizon(self, e))
        return w.cross(we, t_end)

    def own_outcome(self, e: tch.Touch) -> tuple[slab.Label, slab.Label]:
        """`e`'s structure label (`hold_reaction_pts`) and pair cut (`pair_end_hold_pts`, Q-step11h (1)) on the cross
        `own_cross` reads (owner 2026-11-13, Q-step13-review B2); what outcomes/, events/ and the decision points use.
        `labels` / `pair_end` stay as built: the session's own machinery (retested extremes at `retest_hold_pts`, the
        outcome history at `history_hold_pts` that later events and the next session read) is the one world live has."""
        got = self._own_out.get(e.event_id)
        if got is None:
            lab, pend = self.labels[e.event_id], self.pair_end[e.event_id]
            ot = self.own_time(e) if e.kind == "level" and self.exclude_source is None else None
            t_end = slab.window_end(e.t, float(self.f["hold_window_min"]), self.flat_ns, self.end_ns)
            if ot is not None and ot <= t_end:
                x = self.own_cross(e, t_end)
                lab = slab.label(e, self.nq, x, t_end, float(self.f["hold_reaction_pts"]))
                pend = slab.label(e, self.nq, x, t_end, float(self.f["pair_end_hold_pts"]))
            got = self._own_out[e.event_id] = (lab, pend)
        return got

    def structure_labels(self) -> dict[str, slab.Label]:
        """Every event's own structure label (`own_outcome`)."""
        return {e.event_id: self.own_outcome(e)[0] for e in self.events}

    def zone_edges(self, e: tch.Touch, t: int) -> tuple[float, float] | None:
        w, we = self.world(e, t)
        if w is not self:
            return w.zone_edges(we, t)
        if e.kind == "level":
            return _edges_of(self.members_at(e, t), t)
        if e.kind == "gameplan":
            x = next(x for x in self.containers if x.uid == e.zone_id)
            return x.band.at(t)
        d = self.c.cs_nq.opening.drawing_at(t)
        n = int(e.zone_id[1:])
        if d is None or n not in d.zone_ids:
            return None
        return d.band(*self.c.cs_nq.opening.fib.fibs(e.part))

    def members_at(self, e: tch.Touch, t: int) -> list:
        """The zone's members (level events) or its confluence members (containers: levels overlapping it)."""
        w, we = self.world(e, t)
        if w is not self:
            return w.members_at(we, t)
        if e.kind == "level":
            z = self.book.follow(e.zone_id, t)
            return [] if z is None else [x for x in (self.nq_levels[m] for m in self.book.members(z, t))
                                         if not self._own(e, x)]
        edges = self.zone_edges(e, t)
        if edges is None:
            return []
        out = []
        for x in self.nq_levels.values():
            b = x.band.at(t)
            if x.active(t) and b is not None and b[0] <= edges[1] and b[1] >= edges[0] and not self._own(e, x):
                out.append(x)
        return out

    def row(self, e: tch.Touch, t: int) -> dict[str, Any]:
        w, we = self.world(e, t)
        if w is not self:
            return w.row(we, t)
        f, c = self.f, self.c
        d = e.direction
        price = self.nq.last_price_at(t)
        edges = self.zone_edges(e, t)
        members = self.members_at(e, t)
        # outcomes: only touches before this event's own touch, their labels if resolved by t; never the event's own
        # touch or label (owner 2026-11-01, SPEC §13 Q-step11g (2)), also at a pair's later decision_ts
        lab_ok = [lb for eid, lb in self.hist_labels.items() if lb.t_resolved <= t and self.by_id[eid].t < e.t]
        out: dict[str, Any] = {"event_id": e.event_id, "zone_id": e.zone_id, "touch_group_id": f"{self.day}-{e.group}",
                               "day": self.day.isoformat(), "ts": int(e.t), "t": int(t), "event_type": e.kind,
                               "direction": d, "role": e.role, "touch_price": e.price, "price": price,
                               "in_golden_zone": e.kind == "golden", "golden_zone_part": e.part,
                               "tradeable": self._tradeable(e, t, members),
                               # event identity across builds (owner 2026-11-09, SPEC §13 Q-dryrun13 (5)): the touching
                               # trade, the zone's founding members (F5.5) and the zone's edges at the touch
                               "touch_idx": int(e.idx), "founding_level_ids": self._founding(e),
                               "touch_zone_lo": float(e.lo), "touch_zone_hi": float(e.hi)}
        out.update(self._geometry(e, t, edges, members, price))
        out.update(self._makeup(members, t))
        ids = sorted({m.level_id for m in members}) if e.kind == "level" else [e.zone_id]
        today = [r for r in self.records if r.t_touch < e.t]
        hist = [r for r in c.history if r.t_touch < e.t] + today
        out.update(zone_freshness(hist, ids, e.role, e.zone_id, t, zone_touches=today,
                                  absorbed=self.absorbed if e.kind == "level" else None))
        out.update(self._day_memory(t, lab_ok))
        out.update(_flat(c.cs_nq.features_at(t, d)))
        if c.sky is not None:
            out.update(_flat(c.sky.features_at(t, d)))
            for m in NASDAQ:
                if edges is not None:
                    out[f"{m.lower()}_pika_cloud_overlaps_zone"] = c.sky.pika(m, t, edges)["pika_cloud_overlaps_zone"]
            for m in MAPS:
                for k, v in c.sky.pockets(m, t, d).items():
                    out[f"{m.lower()}_{k}"] = v
            out.update(c.sky.trinity(e.t, t, e.role))
            out.update(self._es_at_level(e, t))
        out.update(cm.features_at(self.nq, self.es, t, f, d))
        out.update(approach_features(self.nq, t, e.side < 0, c.cs_nq.atr))
        out.update({f"dt_{k}": v for k, v in self.dt.at(t).items()})
        nh, nl = self.dt.counts_at(t)
        out["with_day_direction"] = dtm.with_day_direction(d, nh, nl) if t >= self.open_ns else None
        v = c.cs_nq.vwap["vwap0930"].at(t)
        out["vwap_side_vs_trade"] = None if v is None or price is None else dtm.vwap_side_vs_trade(
            d, price, v[0], float(f["chart_level_half_width"]["vwap"]))
        out.update(self.gp.features(t, price if price is not None else e.price, d, edges,
                                    own=e.zone_id if e.kind == "gameplan" else None, atr=c.cs_nq.atr))
        out.update({k: v for k, v in cf.clocks_at(t, self.day, c.cal, c.settings).items() if k != "minutes_since_open"})
        out.update(c.flags)
        out.update(c.vol)
        if c.mxe is not None and c.sky is not None:
            out.update(self._multiexp(t))
        if e.kind == "golden":
            out.update(self._golden(e, t, members))
        out["skylit_missing"] = c.sky is None
        return out

    def _founding(self, e: tch.Touch) -> list[str]:
        """The founding members' `level_id`s of a level event's zone (F5.5); containers have none."""
        if e.kind != "level":
            return []
        return sorted({self.nq_levels[u].level_id for u in self.book.anchors.get(e.zone_id, ()) if u in self.nq_levels})

    def _tradeable(self, e: tch.Touch, t: int, members: list) -> bool:
        start = self.golden_from if e.kind == "golden" else self.all_from
        if not (start <= t < self.entry_end_ns):
            return False
        if any(a <= t < b for a, b in self.blackouts):
            return False
        if self.c.sky is not None:
            chains = {m.chain for m in members if m.chain}
            if any(self.c.sky.blocked(ch, t) for ch in chains):
                return False
        return True

    def _geometry(self, e, t, edges, members, price) -> dict[str, Any]:
        out: dict[str, Any] = {k: None for k in GEOMETRY}
        if edges is None or price is None:
            return out
        lo, hi = edges
        w = hi - lo
        ctr = (lo + hi) / 2
        cs = self.c.cs_nq
        em = cs.em.points
        out.update(zone_lo=lo, zone_hi=hi, zone_width=w, distance_to_level_center=abs(price - ctr),
                   distance_to_level_center_frac=abs(price - ctr) / w if w > 0 else None,
                   distance_to_near_edge=(price - hi) if e.side > 0 else (lo - price))
        _, out["zone_width_atr"], out["zone_width_em"] = dist3(w, cs.atr, em)
        cents = [((m.band.at(t)[0] + m.band.at(t)[1]) / 2) for m in members if m.band.at(t) is not None]
        out["member_spread"] = (max(cents) - min(cents)) if cents else None
        for rt in RTYPES:
            ds = [abs((m.band.at(t)[0] + m.band.at(t)[1]) / 2 - ctr) for m in members
                  if m.rtype == rt and m.band.at(t) is not None]
            out[f"dist_center_{rt}"] = min(ds) if ds else None
        # the next level zone above and below (gap between zone edges)
        own = self.book.follow(e.zone_id, t) if e.kind == "level" else None
        above, below = None, None
        for zid in self.book.zones_at(t):
            if zid == own:
                continue
            ez = _edges_of([x for x in (self.nq_levels[m] for m in self.book.members(zid, t)) if not self._own(e, x)],
                           t)
            if ez is None:
                continue
            if ez[0] > hi and (above is None or ez[0] - hi < above):
                above = ez[0] - hi
            if ez[1] < lo and (below is None or lo - ez[1] < below):
                below = lo - ez[1]
        for name, v in (("next_zone_above", above), ("next_zone_below", below)):
            p_, a_, e_ = dist3(v, cs.atr, em)
            out[f"{name}_pts"], out[f"{name}_atr"], out[f"{name}_em"] = p_, a_, e_
        return out

    def _node_info(self, t: int) -> dict[str, dict]:
        out = {}
        sky = self.c.sky
        if sky is None:
            return out
        for m in NASDAQ:
            ms = sky.ms.get(m)
            if ms is None:
                continue
            i = ms.index_at(t)
            for n in sky._relevant_nodes(m, t):
                info = dict(n)
                if i >= 0:
                    rows = ms.rows(i)
                    j = np.flatnonzero(ms.mb.s_strike[rows] == n["strike"])
                    if len(j):
                        r = rows.start + int(j[0])
                        info.update(rug_neg_count=int(ms.rug_n[r]), rug_neg_pct_sum=_num(ms.rug_pct_sum[r]),
                                    rug_first_partner_pct=_num(ms.rug_first_pct[r]),
                                    gatekeeper_vs_beyond=_num(ms.gk_vs_beyond[r]))
                out[n["level_id"]] = info
        return out

    def _makeup(self, members: list, t: int) -> dict[str, Any]:
        # the counted members themselves (owner 2026-11-16, SPEC §13 Q-review-stack (b)): a record beside the counts
        # (an id column, never a model input: `tuning.structure_inputs.ID_COLS`), so a review card shows exactly the
        # levels the counts were made from; one entry per member, so its length is `stack_count`
        out: dict[str, Any] = {"stack_count": len(members),
                               "stack_level_ids": sorted(str(m.level_id) for m in members)}
        for st_ in NQ_SUBTYPES:
            out[f"n_{st_}"] = sum(1 for m in members if m.sub_type == st_)
        out["n_post_cross"] = 0
        info = self._node_info(t) if any(m.rtype == rg.GEX_NODE for m in members) else {}
        nodes = [info[m.level_id] for m in members if m.level_id in info]
        pcts = [n["pct_of_king"] for n in nodes]
        out.update(largest_pct_of_king=max(pcts) if pcts else None, min_pct_of_king=min(pcts) if pcts else None,
                   mean_pct_of_king=float(mean(pcts)) if pcts else None,
                   king_present=None if self.c.sky is None else any(n["node_type"] == "king" for n in nodes),
                   gatekeeper_present=None if self.c.sky is None else any(n["node_type"] == "gatekeeper"
                                                                         for n in nodes),
                   n_node_pos=sum(1 for n in nodes if n["node_sign"] > 0),
                   n_node_neg=sum(1 for n in nodes if n["node_sign"] < 0),
                   any_rug=any(n["rug"] for n in nodes) if nodes else None,
                   any_reverse_rug=any(n["reverse_rug"] for n in nodes) if nodes else None,
                   any_pika_member=any(n["in_pika_cloud_member"] for n in nodes) if nodes else None)
        for ty in NODE_TYPES:
            out[f"n_node_{ty}"] = sum(1 for n in nodes if n["node_type"] == ty)
        vel = [n["velocity"] for n in nodes if n.get("velocity") is not None]
        out["node_velocity_max_abs"] = max((abs(x) for x in vel), default=None)
        out["node_velocity_mean"] = float(mean(vel)) if vel else None
        msr = [n["minutes_since_relevant"] for n in nodes if n.get("minutes_since_relevant") is not None]
        out["node_minutes_since_relevant_min"] = min(msr) if msr else None
        for k in ("rug_neg_count", "rug_neg_pct_sum", "rug_first_partner_pct", "gatekeeper_vs_beyond",
                  "beach_ball_count_today", "bb_overshoot_depth"):
            vals = [n[k] for n in nodes if n.get(k) is not None]
            out[f"node_{k}_max"] = max(vals) if vals else None
        # F2.6b zone forms: members in a post-cross role, the primary member's cross features
        post = []
        for m in members:
            st = m.state.state_at(t)
            if st.crossed and not st.broken_back or st.flip_count > 0:
                post.append(m)
        out["n_post_cross"] = len(post)
        prim = max(post, key=lambda m: (m.state.state_at(t).t_cross or 0, m.uid), default=None)
        atr, em = self.c.cs_nq.atr, self.c.cs_nq.em.points
        st_out = prim.state.at(t, atr, em) if prim is not None else {}
        for k in XB_KEYS:
            out[f"primary_{k}"] = st_out.get(k)
        out["primary_member_type"] = None if prim is None else prim.sub_type
        flips = [m.state.state_at(t).flip_count for m in members]
        out["max_flip_count"] = max(flips) if flips else None
        out["sp_sessions_stacked"] = self._sp_stacked(members, t)
        return out

    def _sp_stacked(self, members, t) -> int | None:
        sp = self.c.cs_nq.sp
        bands = [m.band.at(t) for m in members if m.band.at(t) is not None]
        if sp is None or not bands:
            return None
        return sp.stacked(min(b[0] for b in bands), max(b[1] for b in bands), t)

    def _day_memory(self, t: int, labs: list) -> dict[str, Any]:
        held = sum(1 for x in labs if x.label == slab.HELD)
        broke = sum(1 for x in labs if x.label == slab.BROKE)
        to = sum(1 for x in labs if x.label == slab.TIMEOUT)
        n = held + broke + to
        return {"held_today": held, "broke_today": broke, "timeout_today": to,
                "held_rate_today": held / n if n else None, "yesterday_day_type": self.c.yesterday_day_type}

    def _es_at_level(self, e: tch.Touch, t: int) -> dict[str, Any]:
        """F5.16 by the overlap rule: at each checked tau (the touch, every NQ / ES trade and board apply up to t), NQ
        inside the event's zone touch band and ES inside an ES zone's band +- `es_zone_proximity` at the same moment.
        Evaluated for all taus at once (NQ once per tau, each ES zone segment on its own taus), the same tests one
        tau at a time makes."""
        sky = self.c.sky
        taus = np.asarray(sky.check_times(e.t, t), dtype=np.int64)
        nq_in = self._nq_inside_many(e, taus)
        pe = _prices_at(self.es, taus)
        q = np.array([sky.ctx.es_prox(tau) for tau in taus.tolist()], dtype=float)   # es_zone_proximity at each tau
        zones = []
        for zid in self.es_book.zone_ids():
            for a, z, mem in self.es_book.segments(zid):
                if z <= e.t or a > t:
                    continue
                lvs = [self.es_levels[m] for m in mem]
                i0, i1 = int(np.searchsorted(taus, a, side="left")), int(np.searchsorted(taus, z, side="left"))
                sub = taus[i0:i1]
                if len(sub):
                    lo, hi = _span([values_at(x.band, sub) for x in lvs])
                    p, qq = pe[i0:i1], q[i0:i1]
                    with np.errstate(invalid="ignore"):
                        hit = nq_in[i0:i1] & ~np.isnan(p) & ~np.isnan(lo) & (lo - qq <= p) & (p <= hi + qq)
                    inside_taus = [int(x) for x in sub[hit]]
                else:
                    inside_taus = []
                types = tuple(sorted(x.sub_type for x in lvs))
                zones.append(EsZone(lambda _tau: False, [x.track for x in lvs],
                                    frozenset(x.chain for x in lvs if x.chain),
                                    makeup=lambda _tau, n=len(lvs), ty=types: {"es_stack_count": n,
                                                                               "es_member_types": ty},
                                    inside_taus=inside_taus))
        blocked = {m for m in SP if sky.blocked_in(m, e.t, t)}
        out = es_at_level(zones, e.role, [int(x) for x in taus], t, blocked)
        mt = out.pop("es_member_types")
        out["es_member_types"] = None if mt is None else ",".join(mt)
        return out

    def _nq_inside_many(self, e: tch.Touch, taus: np.ndarray) -> np.ndarray:
        """NQ price inside the event's zone touch band (edges +- `zone_proximity`) at each tau."""
        prox = float(self.f["zone_proximity"])
        p = _prices_at(self.nq, taus)
        lo = np.full(len(taus), np.nan)
        hi = np.full(len(taus), np.nan)
        if e.kind == "level":
            # the zone's members change only at the book's snapshot times: one member list per stretch between them
            bt = np.asarray(self.book.times, dtype=np.int64)
            kk = np.searchsorted(bt, taus, side="right") - 1
            starts = np.flatnonzero(np.r_[True, kk[1:] != kk[:-1]]) if len(taus) else np.zeros(0, dtype=np.int64)
            ends = np.r_[starts[1:], len(taus)]
            for s0, s1 in zip(starts.tolist(), ends.tolist()):
                mem = self.members_at(e, int(taus[s0]))
                if not mem:
                    continue
                lo[s0:s1], hi[s0:s1] = _span([values_at(x.band, taus[s0:s1]) for x in mem])
        else:
            for n, tau in enumerate(taus.tolist()):
                ed = self.zone_edges(e, tau)
                if ed is not None:
                    lo[n], hi[n] = ed
        with np.errstate(invalid="ignore"):
            return ~np.isnan(p) & ~np.isnan(lo) & (lo - prox <= p) & (p <= hi + prox)

    def _multiexp(self, t: int) -> dict[str, Any]:
        sky = self.c.sky

        def r_of(m, tt):
            i = sky.board_at(m, tt)
            return None if i < 0 else float(sky.ms[m].mb.r[i])
        return self.c.mxe.features_at(t, {"nasdaq": self.nq.last_price_at(t), "sp": self.es.last_price_at(t)},
                                      {"nasdaq": sky.ctx.em(("QQQ"), t), "sp": sky.ctx.em("SPY", t)}, r_of,
                                      float(self.f["mx_wall_reach_em"]), blocked_of=sky.blocked)

    def _golden(self, e: tch.Touch, t: int, members: list) -> dict[str, Any]:
        """F10 touch history of that golden zone (resolved earlier touches only) and the nodes inside it."""
        # earlier touches of the same golden zone (F10: "earlier touches' outcomes"): touched before this event, so at
        # a pair's decision_ts neither the event itself nor a later touch counts
        mine = [x for x in self.events if x.zone_id == e.zone_id and x.t < e.t]
        res = [self.hist_labels[x.event_id] for x in mine if self.hist_labels[x.event_id].t_resolved <= t]
        last = mine[-1] if mine else None
        out = {"gz_touch_number": len(mine) + 1,
               "gz_prev_held": sum(1 for r in res if r.label == slab.HELD),
               "gz_prev_broke": sum(1 for r in res if r.label == slab.BROKE),
               "gz_prev_pending": len(mine) - len(res),
               "gz_last_run_pts": res[-1].run_pts if res else None,
               "gz_minutes_since_last_touch": None if last is None else (t - last.t) / MIN_NS}
        sky = self.c.sky
        edges = self.zone_edges(e, t)
        nq, es = self.nq.last_price_at(t), self.es.last_price_at(t)
        for m in MAPS:
            out[f"gz_nodes_{m.lower()}"] = None
        if sky is not None and edges is not None:
            for m in NASDAQ:
                if sky.board_at(m, t) >= 0 and not sky.blocked(m, t):
                    out[f"gz_nodes_{m.lower()}"] = sum(1 for n in sky._relevant_nodes(m, t)
                                                       if n["band"][0] <= edges[1] and n["band"][1] >= edges[0])
            if nq and es:
                k = self.es_ratio(t)   # S&P maps via ES: NQ prices × ES ÷ NQ at the open (F0.11)
                lo_es, hi_es = edges[0] * k, edges[1] * k
                for m in SP:
                    if sky.board_at(m, t) >= 0 and not sky.blocked(m, t):
                        out[f"gz_nodes_{m.lower()}"] = sum(1 for n in sky._relevant_nodes(m, t)
                                                           if n["band"][0] <= hi_es and n["band"][1] >= lo_es)
        return out

    # --- outputs -------------------------------------------------------------------------------------------------
    def touch_rows(self) -> list[dict[str, Any]]:
        return [self.row(e, e.t) for e in self.events]

    def daytype_rows(self) -> list[dict[str, Any]]:
        out = []
        sky = self.c.sky
        for t in self.dt.snapshot_times():
            r = {"day": self.day.isoformat(), "t": int(t), **self.dt.at(t), **self.c.vol}
            if sky is not None:
                feats = sky.features_at(t)
                for m in MAPS:
                    for k in ("regime_net", "regime_pos_share", "regime_long"):
                        r[f"{k}_{m.lower()}"] = feats.get(f"{m.lower()}_{k}")
            out.append(r)
        return out

    def outcome_rows(self) -> list[dict[str, Any]]:
        """The structure label (F11.4 at `hold_reaction_pts`) and the pair cut: `t_pair_end` / `pair_end_label` = the
        same rule at `pair_end_hold_pts` (Q-step11h (1): no pair at or after `t_pair_end`, `slab.pair_allowed`); both as
        the event's own trade sees them (`own_outcome`, owner 2026-11-13, Q-step13-review B2)."""
        out = []
        for e in self.events:
            lab, pend = self.own_outcome(e)
            out.append({"event_id": e.event_id, "day": self.day.isoformat(),
                        **{k: v for k, v in asdict(lab).items() if k != "event_id"},
                        "t_pair_end": int(pend.t_resolved), "pair_end_label": pend.label})
        return out

    def level_timeline(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Every NQ level's and the S&P nodes' band over the session, with the unit values (step 13a,
        `features.level_timeline`)."""
        from nqbot.features import level_timeline as lt

        return lt.session_rows(self)

    def eod(self) -> dict[str, Any] | None:
        return self.dt.eod()

    def end_state(self) -> dict[str, Any]:
        """Part 3's share of the checkpoint (SPEC §11 Expected run time)."""
        f = self.f
        alive = set()
        last = self.end_ns - 1
        for x in self.nq_levels.values():
            if x.multi_day and x.band.at(last) is not None:
                alive.add(x.level_id)
        for r in self.c.retested + self.retested_today:
            alive.add(r.level_id)
        hist = [r for r in self.c.history + self.records if alive & set(r.level_ids)]
        lookback = int(f["retest_lookback_sessions"])
        sessions = self.c.sessions
        retested = [r for r in self.c.retested + self.retested_today
                    if not sl.expired(r, _next_session(self.day, sessions) or self.day, sessions, lookback)]
        cands = list(self.c.retest_candidates) + self._candidates()
        order = sorted(d for d in sessions if d <= self.day)
        keep_from = order[-lookback] if len(order) >= lookback else (order[0] if order else self.day)
        # kept as they come (one per session and type) and merged within retest_tol_ticks only when used
        # (_new_retests): merging at each checkpoint would make the result depend on the order sessions arrive in
        cands = [x for x in cands if x.session >= keep_from]
        eod = self.eod()
        return {"history": [asdict(r) for r in hist], "retested": [_retested_json(r) for r in retested],
                "retest_candidates": [{"price": x.price, "type": x.type, "session": x.session.isoformat(),
                                       "cid": x.cid} for x in cands],
                "yesterday_day_type": None if eod is None else eod["day_type"]}

    def _candidates(self) -> list:
        """F3.8 candidates from today's registry: session highs/lows (ON, PDH/PDL, prior RTH, prior week, IB). Each
        ages from the session its extreme printed in (owner 2026-11-16, SPEC §13 Q-review-stack (c), `features_version`
        40): PDH/PDL and prior RTH the session before, prior week its own day of the previous week, ON and IB today;
        its identity (`cid`) stays the registry session's."""
        printed = getattr(self.c.cs_nq, "print_session", None) or {}
        out = []
        for x in self.nq_levels.values():
            if x.key in sl.HIGHS_LOWS_TYPES:
                b = x.band.at(int(x.band.start_ns))
                if b is not None:
                    p = (b[0] + b[1]) / 2
                    out.append(sl.Candidate(p, x.sub_type, printed.get(x.sub_type, self.day),
                                            f"{x.sub_type}-{self.day.isoformat()}-{p:g}"))
        return out


GEOMETRY = ("zone_lo", "zone_hi", "zone_width", "zone_width_atr", "zone_width_em", "member_spread",
            "distance_to_level_center", "distance_to_level_center_frac", "distance_to_near_edge",
            *(f"dist_center_{r}" for r in RTYPES),
            *(f"next_zone_{s}_{u}" for s in ("above", "below") for u in ("pts", "atr", "em")))
XB_KEYS = ("provisional_role", "flip_count", "cross_count_today", "minutes_since_cross", "minutes_since_flip",
           "xb_depth", "xb_depth_atr", "xb_depth_em", "xb_vol_rel", "xb_delta_rel", "xb_sec_beyond", "xb_settled_1m",
           "xb_settled_5m", "xb_reentries", "xb_node_pctking_chg", "xb_node_sign_flipped")


def _num(x) -> float | None:
    return None if x is None or not np.isfinite(x) else float(x)


def _flat(d: dict[str, Any]) -> dict[str, Any]:
    """Parquet-friendly values: a (lo, hi) band -> _lo/_hi, a dict -> one column per key."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, tuple) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v):
            out[f"{k}_lo"], out[f"{k}_hi"] = float(v[0]), float(v[1])
        elif isinstance(v, dict):
            for kk, vv in v.items():
                out[f"{k}_{kk:g}" if isinstance(kk, float) else f"{k}_{kk}"] = vv
        elif isinstance(v, (np.floating, np.integer, np.bool_)):
            out[k] = v.item()
        else:
            out[k] = v
    return out


def _retested_json(r) -> dict:
    return {"level_id": r.level_id, "price": r.price, "type": r.type, "session": r.session.isoformat(),
            "created_ns": int(r.created_ns), "source": r.source}


def retested_from_json(d: dict):
    return sl.Retested(d["level_id"], float(d["price"]), d["type"], date.fromisoformat(d["session"]),
                       int(d["created_ns"]), d.get("source") or "")


def _today_retests(cs) -> set:
    """Record uids of retested extremes made today on this chart session, by any build of the session."""
    s = getattr(cs, "_today_retests", None)
    if s is None:
        s = cs._today_retests = set()
    return s


def _retest_record(cs, r) -> str:
    """The chart-session record of retested extreme `r` (made once per start time; another build of the session may
    have made the same one), as its registry uid."""
    start = max(int(r.created_ns), int(cs.start_ns))
    recs = cs.levels.get(r.level_id, [])
    k = next((k for k, x in enumerate(recs) if int(x.state.band.start_ns) == start), None)
    if k is None:
        cs._const(r.level_id, "retested_extreme", r.price, start, True)
        k = len(cs.levels[r.level_id]) - 1
    uid = r.level_id if k == 0 else f"{r.level_id}#{k}"
    _today_retests(cs).add(uid)
    return uid


def _span(vals: list) -> tuple[np.ndarray, np.ndarray]:
    """The union of several bands' (lo, hi) arrays per instant (NaN where none exists)."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)   # all-NaN columns: no member band exists then
        return np.nanmin(np.vstack([v[0] for v in vals]), axis=0), np.nanmax(np.vstack([v[1] for v in vals]), axis=0)


def _prices_at(tape, ts: np.ndarray) -> np.ndarray:
    """`tape.last_price_at` at each instant (NaN before the first trade)."""
    j = np.searchsorted(tape.ts, ts, side="right")
    return np.where(j > 0, tape.price[np.maximum(j - 1, 0)].astype(float), np.nan)


def _edges_of(levels: list, t: int) -> tuple[float, float] | None:
    bs = [b for b in (x.band.at(t) for x in levels) if b is not None]
    return None if not bs else (min(b[0] for b in bs), max(b[1] for b in bs))


def _next_session(day: date, sessions: list) -> date | None:
    later = [d for d in sessions if d > day]
    return min(later) if later else None


def history_from_json(rows: list) -> list[TouchRecord]:
    return [TouchRecord(r["t_touch"], tuple(r["level_ids"]), r["zone_id"], r["role"], r["t_resolved"], r["label"])
            for r in rows]
