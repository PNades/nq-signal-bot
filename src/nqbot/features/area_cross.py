"""Cross-market features of VAH/VAL area events (owner 2026-10-26, SPEC §13 Q-areasp; feature_spec F5.8, F5.16, F2.19).

Pair-scorer inputs only, each evaluated at the pair's `decision_ts` t (the trigger model has no cross-market inputs and
the structure model's prediction is null for area events). Area events are built at step 19; this module holds the rule.
- Role: a VAH-area event (short) has rho = ceiling, a VAL-area event (long) rho = floor.
- Window [w0, t]: w0 = the visit start, the moment NQ last entered the area on this visit; with NQ inside both the daily
  and the weekly area at the first firing, the earliest moment since which NQ has been continuously inside at least one
  of the areas containing it at that firing (`visit_start`).
- A candidate (an ES level for `es_at_level`, a relevant SPY/SPX node for the `area_sp_*` columns) overlaps at tau if ES
  is inside its band +- `es_zone_proximity` (and, for a node, it is relevant) as of tau. tau is checked at the visit
  start and at every ES trade, NQ trade and board apply in (w0, t], as the level-event Trinity rule does (F2.19); the
  caller passes those times and evaluates the candidate as of each (last trades and bands <= tau, data <= t only).
- It counts if it overlapped at some checked tau and counts in rho at t by the Q18 rule (holds rho at t, confirmed or
  provisional, not crossed against rho since tau; `trinity.counts_in_role`). SPY/SPX nodes also use Q19 (an unroled
  node counts from ES's entry side).
- Dislocation block (F1.8), the level-event rule: a blocked map's columns are null, and `area_sp_count` is null while
  either S&P chain is blocked (as `trinity_count` is for level events).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from nqbot.features.trinity import CEILING, FLOOR, RoleTrack, counts_in_role

SP_MAPS = ("SPY", "SPX")


def area_role(area_side: str) -> str:
    """'vah' (shorts only) -> ceiling, 'val' (longs only) -> floor."""
    return {"vah": CEILING, "val": FLOOR}[area_side]


def visit_start(inside: dict, t_first: float) -> float | None:
    """`inside`: for each area containing NQ at the first firing, its stretches (start, end or None) of NQ inside it.
    Returns the start of the unbroken stretch, over their union, that contains `t_first` (None if none does)."""
    ivs = sorted((s, math.inf if e is None else e) for spans in inside.values() for s, e in spans if s <= t_first)
    start, end = None, -math.inf
    for s, e in ivs:
        if start is None or s > end:  # a gap: a new stretch
            if start is not None and start <= t_first <= end:
                return start
            start, end = s, e
        else:
            end = max(end, e)
    return start if start is not None and start <= t_first <= end else None


def _always(_tau: float) -> bool:
    return True


@dataclass
class Candidate:
    inside_at: Callable[[float], bool]            # ES inside the band +- es_zone_proximity as of tau
    track: RoleTrack = field(default_factory=RoleTrack)
    relevant_at: Callable[[float], bool] = _always  # nodes: relevant as of tau (F2.4); ES chart levels: always
    entered_from: str | None = None               # Q19, nodes only ('below' / 'above'); None for ES chart levels
    stats: Callable[[float], dict] | None = None  # tau -> {'pct_of_king', 'king', 'gatekeeper'} (nodes)


def check_times(w0: float, t: float, times) -> list:
    """The visit start plus every ES trade, NQ trade and board apply time in (w0, t], in order."""
    return [w0] + sorted({x for x in times if w0 < x <= t})


def first_overlap(c: Candidate, rho: str, taus, t: float) -> float | None:
    """The first checked tau at which c overlaps and from which it still counts in rho at t; None if none."""
    for tau in taus:
        if c.inside_at(tau) and c.relevant_at(tau) and counts_in_role(c.track, rho, tau, t, c.entered_from):
            return tau
    return None


def es_at_level(levels, rho: str, taus, t: float) -> bool:
    """F5.16 for an area event (same column as for level events): one ES level that still counts is enough."""
    return any(first_overlap(c, rho, taus, t) is not None for c in levels)


def area_sp(nodes: dict, rho: str, taus, t: float, blocked=frozenset()) -> dict:
    """The area_sp_* columns. Per map: aligned, and the stats at its first overlap of the node that overlapped first among
    those still counting at t (a node that overlapped earlier but has since failed is skipped; a tie -> the larger
    pct_of_king). A blocked map is null; `area_sp_count` (aligned maps) is null while either S&P chain is blocked."""
    out, count = {}, 0
    for m in SP_MAPS:
        k = m.lower()
        if m in blocked:
            out.update({f"area_sp_aligned_{k}": None, f"area_sp_pct_of_king_{k}": None,
                        f"area_sp_king_{k}": None, f"area_sp_gatekeeper_{k}": None})
            continue
        hits = [(tau, c) for c in nodes.get(m, []) if (tau := first_overlap(c, rho, taus, t)) is not None]
        st = {}
        if hits:
            tau, c = min(hits, key=lambda h: (h[0], -(h[1].stats(h[0])["pct_of_king"] if h[1].stats else 0.0)))
            st = c.stats(tau) if c.stats else {}
            count += 1
        out.update({f"area_sp_aligned_{k}": bool(hits), f"area_sp_pct_of_king_{k}": st.get("pct_of_king"),
                    f"area_sp_king_{k}": st.get("king"), f"area_sp_gatekeeper_{k}": st.get("gatekeeper")})
    out["area_sp_count"] = None if any(m in blocked for m in SP_MAPS) else count
    return out
