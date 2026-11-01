"""One session's zones, events, structure labels and feature rows (step 11 part 3; SPEC §5.4, §5.4b, §5.6, §7.1;
feature_spec F5.1-F5.13, F5.16, F5.18, F8, F10, F11.4, F11.7).

`SessionFeatures.build()`:
1. the NQ and ES registries (`registry.py`): part 1's chart levels, part 2's nodes (QQQ / NDXP on NQ, SPY / SPX on ES),
   the gameplan containers; the golden zones come from part 1's `OpeningRange`;
2. the zone books (`zones.ZoneBook`; ES with the NQ parameters × ES ÷ NQ at the open, F0.11);
3. touches (`touches.py`: level zones, gameplan zones, golden zones), touch groups and event ids;
4. structure labels (`structure_label.py`);
5. retested extremes (F3.8): a held touch within `retest_tol_ticks` of an earlier session's high/low creates a
   `retested_extreme` level at the label's resolution time; the new levels can only change what happens after their
   creation, so the steps above are re-run until no new level appears (a fixed point; each pass is causal).
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
MAX_RETEST_PASSES = 8
NODE_TYPES = ("king", "gatekeeper", "significant", "normal")


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
    def __init__(self, ctx: SessionContext):
        self.c = ctx
        self.f, self.day = ctx.features, ctx.day
        cs = ctx.cs_nq
        self.nq, self.es = cs.tape, ctx.cs_es.tape
        self.end_ns = cs.end_ns
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
        self.passes = 0

    # --- build ---------------------------------------------------------------------------------------------------
    def build(self) -> SessionFeatures:
        if self.f.get("dp_in_model"):
            raise NotImplementedError("dp_in_model is true: dark-pool levels are not wired into the zones yet "
                                      "(owner 2026-10-06: added and tested at a later retrain)")
        for _ in range(MAX_RETEST_PASSES):
            self.passes += 1
            self._build_once()
            new = self._new_retests()
            if not new:
                return self
            for r in new:
                self.c.cs_nq._const(r.level_id, "retested_extreme", r.price, max(r.created_ns, self.c.cs_nq.start_ns),
                                    True)
                self.retested_today.append(r)
        raise RuntimeError(f"{self.day}: retested extremes did not settle after {MAX_RETEST_PASSES} passes")

    def _build_once(self) -> None:
        c, f, iso = self.c, self.f, self.day.isoformat()
        nq_levels = rg.chart_levels(c.cs_nq, self.end_ns)
        es_levels = rg.chart_levels(c.cs_es, self.end_ns)
        if c.sky is not None:
            nq_levels += rg.node_levels(c.sky, NASDAQ, iso, self.end_ns)
            es_levels += rg.node_levels(c.sky, SP, iso, self.end_ns)
        self.containers = [rg.container(z.gid, "gameplan_zone", self.nq, z.lo, z.hi, self.open_ns, self.end_ns,
                                        c.cs_nq.bars_1m, c.cs_nq.bars_flip, f) for z in self.gp.zones]
        self.nq_levels = {x.uid: x for x in nq_levels}
        self.es_levels = {x.uid: x for x in es_levels}
        tol, maxw = float(f["confluence_tol"]), float(f["max_zone_width"])
        self.book = ZoneBook([x.book_level() for x in nq_levels], self.nq, lambda t: (tol, maxw),
                             end_ns=self.end_ns).run()
        sc = c.sky.ctx.scale if c.sky is not None else None
        sw = () if sc is None or sc.switch_ns is None else (sc.switch_ns,)

        def es_params(t):
            k = 1.0 if sc is None else (sc.today if sc.switch_ns is not None and t >= sc.switch_ns else sc.prev)
            return tol * k, maxw * k
        self.es_book = ZoneBook([x.book_level() for x in es_levels], self.es, es_params, param_times=sw,
                                end_ns=self.end_ns).run()
        prox, rearm = float(f["zone_proximity"]), float(f["rearm_dist"])
        level_t = tch.ZoneTouches(self.nq, self.book, self.nq_levels, prox, rearm).run().touches
        cont = {x.uid: x for x in self.containers}
        gp_t = tch.ZoneTouches(self.nq, FixedBook(self.containers, self.end_ns), cont, prox, rearm).run().touches
        for x in gp_t:
            x.kind = "gameplan"
        gold = tch.golden_touches(c.cs_nq.opening, self.nq) if c.cs_nq.opening is not None else []
        self.events = tch.assign_groups(level_t + gp_t + gold)
        self.by_id = {e.event_id: e for e in self.events}
        self.labels = {e.event_id: self._label(e) for e in self.events}
        self.records = [self._record(e) for e in self.events]

    def _label(self, e: tch.Touch) -> slab.Label:
        f = self.f
        t_end = slab.window_end(e.t, float(f["hold_window_min"]), self.flat_ns, self.end_ns)
        if e.kind == "level":
            bars_1m = self.c.cs_nq.bars_1m
            frozen = {m: slab.frozen_single_print(self.nq_levels[m], bars_1m, e.role, e.t, t_end)
                      for m in e.members if self.nq_levels[m].kind == ls.SINGLE_PRINT}
            cross = slab.zone_cross(self.book, self.nq_levels, e.zone_id, e.role, e.t, t_end, frozen)
        elif e.kind == "gameplan":
            cross = slab.container_cross(next(x for x in self.containers if x.uid == e.zone_id), e.role, e.t, t_end)
        else:
            cross = slab.golden_cross(self.c.cs_nq.opening, int(e.zone_id[1:]), e.t, t_end)
        return slab.label(e, self.nq, cross, t_end, float(f["hold_reaction_pts"]))

    def _level_ids(self, e: tch.Touch) -> tuple:
        if e.kind == "level":
            return tuple(sorted({self.nq_levels[m].level_id for m in e.members}))
        return (e.zone_id,)

    def _record(self, e: tch.Touch) -> TouchRecord:
        lab = self.labels[e.event_id]
        return TouchRecord(e.t, self._level_ids(e), e.zone_id, e.role, lab.t_resolved, lab.label)

    def _new_retests(self) -> list:
        f = self.f
        in_force = {r.level_id for r in self.c.retested} | {r.level_id for r in self.retested_today}
        touches = [{"price": e.price, "t_resolved": self.labels[e.event_id].t_resolved,
                    "label": self.labels[e.event_id].label} for e in self.events]
        found = sl.retests_from_touches(self.c.retest_candidates, touches, self.day, self.c.sessions,
                                        int(f["retest_tol_ticks"]), int(f["retest_lookback_sessions"]))
        return [r for r in found if r.level_id not in in_force]

    # --- queries -------------------------------------------------------------------------------------------------
    def zone_edges(self, e: tch.Touch, t: int) -> tuple[float, float] | None:
        if e.kind == "level":
            z = self.book.follow(e.zone_id, t)
            return None if z is None else self.book.edges(z, t, {u: x.band for u, x in self.nq_levels.items()})
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
        if e.kind == "level":
            z = self.book.follow(e.zone_id, t)
            return [] if z is None else [self.nq_levels[m] for m in self.book.members(z, t)]
        edges = self.zone_edges(e, t)
        if edges is None:
            return []
        out = []
        for x in self.nq_levels.values():
            b = x.band.at(t)
            if x.active(t) and b is not None and b[0] <= edges[1] and b[1] >= edges[0]:
                out.append(x)
        return out

    def row(self, e: tch.Touch, t: int) -> dict[str, Any]:
        f, c = self.f, self.c
        d = e.direction
        price = self.nq.last_price_at(t)
        edges = self.zone_edges(e, t)
        members = self.members_at(e, t)
        lab_ok = [lb for eid, lb in self.labels.items() if lb.t_resolved <= t and self.by_id[eid].t < t]
        out: dict[str, Any] = {"event_id": e.event_id, "zone_id": e.zone_id, "touch_group_id": f"{self.day}-{e.group}",
                               "day": self.day.isoformat(), "ts": int(e.t), "t": int(t), "event_type": e.kind,
                               "direction": d, "role": e.role, "touch_price": e.price, "price": price,
                               "in_golden_zone": e.kind == "golden", "golden_zone_part": e.part,
                               "tradeable": self._tradeable(e, t, members)}
        out.update(self._geometry(e, t, edges, members, price))
        out.update(self._makeup(members, t))
        ids = sorted({m.level_id for m in members}) if e.kind == "level" else [e.zone_id]
        hist = [r for r in c.history if r.t_touch < t] + [r for r in self.records if r.t_touch < t]
        out.update(zone_freshness(hist, ids, e.role, e.zone_id, t))
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
        bands = {u: x.band for u, x in self.nq_levels.items()}
        own = self.book.follow(e.zone_id, t) if e.kind == "level" else None
        above, below = None, None
        for zid in self.book.zones_at(t):
            if zid == own:
                continue
            ez = self.book.edges(zid, t, bands)
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
        out: dict[str, Any] = {"stack_count": len(members)}
        for st_ in NQ_SUBTYPES:
            out[f"n_{st_}"] = sum(1 for m in members if m.sub_type == st_)
        out["n_post_cross"] = 0
        info = self._node_info(t) if any(m.rtype == rg.GEX_NODE for m in members) else {}
        nodes = [info[m.level_id] for m in members if m.level_id in info]
        pcts = [n["pct_of_king"] for n in nodes]
        out.update(largest_pct_of_king=max(pcts) if pcts else None, min_pct_of_king=min(pcts) if pcts else None,
                   mean_pct_of_king=mean(pcts) if pcts else None,
                   king_present=any(n["node_type"] == "king" for n in nodes) if nodes else False,
                   gatekeeper_present=any(n["node_type"] == "gatekeeper" for n in nodes) if nodes else False,
                   n_node_pos=sum(1 for n in nodes if n["node_sign"] > 0),
                   n_node_neg=sum(1 for n in nodes if n["node_sign"] < 0),
                   any_rug=any(n["rug"] for n in nodes) if nodes else None,
                   any_reverse_rug=any(n["reverse_rug"] for n in nodes) if nodes else None,
                   any_pika_member=any(n["in_pika_cloud_member"] for n in nodes) if nodes else None)
        for ty in NODE_TYPES:
            out[f"n_node_{ty}"] = sum(1 for n in nodes if n["node_type"] == ty)
        vel = [n["velocity"] for n in nodes if n.get("velocity") is not None]
        out["node_velocity_max_abs"] = max((abs(x) for x in vel), default=None)
        out["node_velocity_mean"] = mean(vel) if vel else None
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
        sky = self.c.sky
        prox = float(self.f["zone_proximity"])
        es_bands = {u: x.band for u, x in self.es_levels.items()}

        def nq_inside(tau):
            ed = self.zone_edges(e, tau)
            p = self.nq.last_price_at(tau)
            return ed is not None and p is not None and ed[0] - prox <= p <= ed[1] + prox
        zones = []
        for zid in self.es_book.zone_ids():
            for a, z, mem in self.es_book.segments(zid):
                if z <= e.t or a > t:
                    continue
                lvs = [self.es_levels[m] for m in mem]

                def inside(tau, a=a, z=z, mem=mem):
                    if not (a <= tau < z) or not nq_inside(tau):
                        return False
                    p = self.es.last_price_at(tau)
                    bs = [es_bands[m].at(tau) for m in mem]
                    bs = [b for b in bs if b is not None]
                    if p is None or not bs:
                        return False
                    q = sky.ctx.es_prox(tau)
                    return min(b[0] for b in bs) - q <= p <= max(b[1] for b in bs) + q
                types = tuple(sorted(x.sub_type for x in lvs))
                zones.append(EsZone(inside, [x.track for x in lvs], frozenset(x.chain for x in lvs if x.chain),
                                    makeup=lambda _tau, n=len(lvs), ty=types: {"es_stack_count": n,
                                                                               "es_member_types": ty}))
        taus = [int(x) for x in sky.check_times(e.t, t)]
        blocked = {m for m in SP if sky.blocked_in(m, e.t, t)}
        out = es_at_level(zones, e.role, taus, t, blocked)
        mt = out.pop("es_member_types")
        out["es_member_types"] = None if mt is None else ",".join(mt)
        return out

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
        mine = [x for x in self.events if x.zone_id == e.zone_id and x.t < t]
        res = [self.labels[x.event_id] for x in mine if self.labels[x.event_id].t_resolved <= t]
        last = mine[-1] if mine else None
        out = {"gz_touch_number": len([x for x in mine if x.t < e.t]) + 1,
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
                k = es / nq   # S&P maps via ES, scaled by ES ÷ NQ
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
        return [{"event_id": e.event_id, "day": self.day.isoformat(), **{k: v for k, v in
                 asdict(self.labels[e.event_id]).items() if k != "event_id"}} for e in self.events]

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
        cands = [x for x in cands if x.session >= keep_from]
        eod = self.eod()
        return {"history": [asdict(r) for r in hist], "retested": [_retested_json(r) for r in retested],
                "retest_candidates": [{"price": x.price, "type": x.type, "session": x.session.isoformat()}
                                      for x in cands],
                "yesterday_day_type": None if eod is None else eod["day_type"]}

    def _candidates(self) -> list:
        """F3.8 candidates from today's registry: session highs/lows (ON, PDH/PDL, prior RTH, prior week, IB)."""
        out = []
        for x in self.nq_levels.values():
            if x.key in sl.HIGHS_LOWS_TYPES:
                b = x.band.at(int(x.band.start_ns))
                if b is not None:
                    out.append(sl.Candidate((b[0] + b[1]) / 2, x.sub_type, self.day))
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
            "created_ns": int(r.created_ns)}


def retested_from_json(d: dict):
    return sl.Retested(d["level_id"], float(d["price"]), d["type"], date.fromisoformat(d["session"]),
                       int(d["created_ns"]))


def _next_session(day: date, sessions: list) -> date | None:
    later = [d for d in sessions if d > day]
    return min(later) if later else None


def history_from_json(rows: list) -> list[TouchRecord]:
    return [TouchRecord(r["t_touch"], tuple(r["level_ids"]), r["zone_id"], r["role"], r["t_resolved"], r["label"])
            for r in rows]
