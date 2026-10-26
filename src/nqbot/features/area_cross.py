"""Cross-market features of VAH/VAL area events (owner 2026-10-26, SPEC §13 Q-areasp; feature_spec F5.8, F5.16, F2.19).

Pair-scorer inputs only, each evaluated at the pair's `decision_ts` t (the trigger model has no cross-market inputs and
the structure model's prediction is null for area events). Area events are built at step 19; this module holds the rule.
- Role: a VAH-area event (short) has rho = ceiling, a VAL-area event (long) rho = floor.
- Window [w0, t]: w0 = the visit start, the moment NQ last entered the area on this visit; with NQ inside both the daily
  and the weekly area at the first firing, the earliest moment since which NQ has been continuously inside at least one
  of the areas containing it at that firing (`visit_start`).
- A candidate (an ES level for `es_at_level`, a relevant SPY/SPX node for the `area_sp_*` columns) counts if at some tau
  in the window ES was inside its band +- `es_zone_proximity` and it counts in rho at t by the Q18 rule (holds rho at t,
  confirmed or provisional, not crossed against rho since tau; `trinity.counts_in_role`). SPY/SPX nodes also use Q19
  (an unroled node counts from ES's entry side) and are null during a dislocation block of their chain (F1.8).
The caller supplies, per candidate, the stretches when ES was inside its band (from the last trades and bands as of each
tau, data <= t only).
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


@dataclass
class Candidate:
    inside: list                                  # (start, end or None): ES inside the band +- es_zone_proximity
    track: RoleTrack = field(default_factory=RoleTrack)
    entered_from: str | None = None               # Q19, nodes only ('below' / 'above'); None for ES chart levels
    stats: Callable[[float], dict] | None = None  # tau -> {'pct_of_king', 'king', 'gatekeeper'} (nodes)


def first_overlap(c: Candidate, rho: str, w0: float, t: float) -> float | None:
    """The first tau in [w0, t] with ES inside c's band from which c still counts in rho at t; None if none."""
    taus = []
    for s, e in c.inside:
        lo, hi = max(s, w0), min(t, math.inf if e is None else e)
        if lo <= hi:
            taus.append(lo)
            taus += [ts for ts, _ in c.track.roles if lo < ts <= hi]  # a role change inside the stretch
    for tau in sorted(taus):
        if counts_in_role(c.track, rho, tau, t, c.entered_from):
            return tau
    return None


def es_at_level(levels, rho: str, w0: float, t: float) -> bool:
    """F5.16 for an area event (same column as for level events): one ES level that still counts is enough."""
    return any(first_overlap(c, rho, w0, t) is not None for c in levels)


def area_sp(nodes: dict, rho: str, w0: float, t: float, blocked=frozenset()) -> dict:
    """The area_sp_* columns. Per map: aligned, and the aligned node's stats at its first overlap (the earliest-overlapping
    node; ties -> the larger pct_of_king). A blocked map is null; area_sp_count counts aligned maps, null if all null."""
    out, count, known = {}, 0, 0
    for m in SP_MAPS:
        k = m.lower()
        if m in blocked:
            out.update({f"area_sp_aligned_{k}": None, f"area_sp_pct_of_king_{k}": None,
                        f"area_sp_king_{k}": None, f"area_sp_gatekeeper_{k}": None})
            continue
        known += 1
        hits = [(tau, c) for c in nodes.get(m, []) if (tau := first_overlap(c, rho, w0, t)) is not None]
        st = {}
        if hits:
            tau, c = min(hits, key=lambda h: (h[0], -(h[1].stats(h[0])["pct_of_king"] if h[1].stats else 0.0)))
            st = c.stats(tau) if c.stats else {}
            count += 1
        out.update({f"area_sp_aligned_{k}": bool(hits), f"area_sp_pct_of_king_{k}": st.get("pct_of_king"),
                    f"area_sp_king_{k}": st.get("king"), f"area_sp_gatekeeper_{k}": st.get("gatekeeper")})
    out["area_sp_count"] = count if known else None
    return out
