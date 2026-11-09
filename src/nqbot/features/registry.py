"""The session's level registry for zones (SPEC §5.4 Level registry; feature_spec F5.1, F5.9, F0.9).

One list per instrument:
- **NQ** (zones and events): part 1's NQ chart levels (`ChartSession.levels`), the relevant QQQ / NDXP nodes (part 2's
  `MapNodes` levels, registry id `<map>_node-<day>-<strike>`), and the containers (the gameplan zones here; the golden
  zones come from `OpeningRange`). Dark pools only with `dp_in_model` (off for the first model, owner 2026-10-06).
- **ES** (cross-market zones for `es_at_level`, F5.16): part 1's ES chart levels and the relevant SPY / SPX nodes.

Each `RegLevel` carries its band, its state machine (`level_state.LevelState`), its role track (`trinity.RoleTrack`,
for crosses against a role) and its **active spans** (F5.3): the band exists, the node is relevant (not frozen, F2.4)
and the level is not crossed, or crossed and armed in its provisional role (F2.6 step 5; a provisional role crossed
back is not active). A level with no role yet (F2.6 step 1) is not crossed, so it is active (F5.3).
A level id can be re-created in one session (an LVN span that returns); each record has its own `uid`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from nqbot.features import level_state as ls
from nqbot.features.bands import StepBand, TradeBand
from nqbot.features.node_levels import FLIP_KEY, _track
from nqbot.features.trinity import RoleTrack
from nqbot.features.zones import BookLevel

GEX_NODE = "gex_node"
CONTAINER = "container"
NODE_SUBTYPE = {"QQQ": "qqq_node", "NDXP": "ndxp_node", "SPY": "spy_node", "SPX": "spx_node"}


@dataclass
class RegLevel:
    uid: str                 # unique within the session (level_id, or level_id#k for a re-created record)
    level_id: str            # F5.1 identity (freshness attaches to it)
    sub_type: str
    key: str                 # F5.14 level-type key
    rtype: str               # registry type (gex_node, vwap, volume_profile, session_hl, opening_range, ...)
    instrument: str          # NQ / ES
    kind: str                # level_state kind
    multi_day: bool
    band: Any
    state: ls.LevelState
    track: RoleTrack
    chain: str | None = None     # map of a node (its dislocation chain: CHAIN in skylit_session)
    strike: float | None = None
    spans: list = field(default_factory=list)

    def pct_of_king(self, t: int) -> float | None:
        ns = self.state.node_series
        if ns is None:
            return None
        k = int(np.searchsorted(ns[0], t, side="right")) - 1
        if k < 0 or not np.isfinite(ns[1][k]):
            return None
        return float(ns[1][k])

    def active(self, t: int) -> bool:
        return any(a <= t < z for a, z in self.spans)

    def book_level(self) -> BookLevel:
        times = None if isinstance(self.band, TradeBand) else np.asarray(self.band.times, dtype=np.int64)
        node = self.rtype == GEX_NODE
        ns = self.state.node_series if node else None
        return BookLevel(self.uid, self.band, list(self.spans), self.pct_of_king if node else None, times,
                         pct_series=None if ns is None else (np.asarray(ns[0], dtype=np.int64), ns[1]))


def is_active(s: ls.State) -> bool:
    if s.frozen:
        return False
    if s.role is None or not s.crossed:
        return True
    return s.prov_armed and not s.broken_back


def active_spans(state: ls.LevelState, band: Any, end_ns: int) -> list[tuple[int, int]]:
    """[(a, z)) over which the level is an active zone member (F5.3)."""
    start = int(band.start_ns)
    stop = min(int(end_ns), int(state.end_ns) if state.end_ns is not None else int(end_ns))
    be = getattr(band, "end_ns", None)
    if be is not None:
        stop = min(stop, int(be))
    if stop <= start:
        return []
    times = {start}
    times.update(int(t) for t, _ in state.events if start <= t < stop)
    for a, z in state.freezes:
        times.update(x for x in (int(a), int(z)) if start <= x < stop)
    if isinstance(band, StepBand):   # a band that stops existing (NaN row) leaves the zone
        nan = ~np.isfinite(band.lo)
        if nan.any():
            ch = np.flatnonzero(np.r_[True, nan[1:] != nan[:-1]])
            times.update(int(band.times[k]) for k in ch if start <= band.times[k] < stop)
    elif isinstance(band, TradeBand):   # a moving line (VWAP) exists from its first trade, not at its anchor instant
        fin = np.isfinite(band.center)
        if len(fin):
            ch = np.flatnonzero(np.r_[True, fin[1:] != fin[:-1]])
            ts = band.tape.ts
            times.update(int(ts[k]) for k in ch if start <= ts[k] < stop)
    spans: list[list[int]] = []
    for t in sorted(times):
        on = band.at(t) is not None and is_active(state.state_at(t))
        is_open = bool(spans) and spans[-1][1] == stop
        if on and not is_open:
            spans.append([t, stop])
        elif not on and is_open:
            spans[-1][1] = t
    return [(a, z) for a, z in spans if z > a]


def _uids(records: list, base: str) -> list[str]:
    return [base if k == 0 else f"{base}#{k}" for k in range(len(records))]


def chart_levels(cs, end_ns: int) -> list[RegLevel]:
    """Part 1's chart levels of one ChartSession (NQ or ES)."""
    from nqbot.features.chart_levels import REGISTRY_TYPE

    out = []
    for lid, recs in cs.levels.items():
        for uid, r in zip(_uids(recs, lid), recs):
            st = r.state
            out.append(RegLevel(uid, lid, r.sub_type, r.key, REGISTRY_TYPE[r.key], r.instrument, st.kind,
                                r.multi_day, st.band, st, _track(st), spans=active_spans(st, st.band, end_ns)))
    return out


def node_levels(sess, maps: tuple, day_iso: str, end_ns: int) -> list[RegLevel]:
    """Part 2's node levels of the given maps (relevant strikes; frozen while not relevant)."""
    from nqbot.features.boards import CHAIN_ROOT

    out = []
    for m in maps:
        mn = sess.nodes.get(m)
        if mn is None:
            continue
        for k, nl in sorted(mn.levels.items()):
            lid = f"{m.lower()}_node-{day_iso}-{k:g}"
            st = nl.state
            spans = active_spans(st, st.band, end_ns)
            if nl.level_spans is not None:   # node_relevance_top_n (Q-step12 (2)): an NQ level only while ranked
                spans = _intersect(spans, nl.level_spans)
            out.append(RegLevel(lid, lid, NODE_SUBTYPE[m], FLIP_KEY[m], GEX_NODE, CHAIN_ROOT[m], ls.NODE, False,
                                st.band, st, nl.track, chain=m, strike=float(k), spans=spans))
    return out


def _intersect(a: list, b: list) -> list[tuple[int, int]]:
    out = []
    for a0, a1 in a:
        for b0, b1 in b:
            lo, hi = max(a0, b0), min(a1, b1)
            if lo < hi:
                out.append((lo, hi))
    return sorted(out)


def container(level_id: str, sub_type: str, tape, lo: float, hi: float, start_ns: int, end_ns: int,
              bars_1m, bars_flip, features: dict) -> RegLevel:
    """A gameplan zone (F5.9): a container machine (a trade past the far edge crosses it, flip margin 0)."""
    band = StepBand.const(tape, start_ns, lo, hi)
    st = ls.LevelState(level_id, ls.CONTAINER, tape, band,
                       ls.Params(features["rearm_dist"], 0.0, features["flip_bar_minutes"], features["sweep_max_depth"],
                                 features["sweep_reclaim_bars"]), bars_1m, bars_flip, end_ns=end_ns).run()
    return RegLevel(level_id, level_id, sub_type, "gameplan", CONTAINER, "NQ", ls.CONTAINER, False, band, st,
                    _track(st), spans=[(start_ns, end_ns)])
