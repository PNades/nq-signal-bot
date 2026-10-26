"""F5.16 `es_at_level`, `es_stack_count` and the ES member types, with the dislocation-block rule (owner 2026-10-26,
SPEC §13 Q-dislguard-2 (a); feature_spec F1.8), for level events and area events alike.

An ES zone overlaps at a checked tau when the caller's `inside_at(tau)` is true (level events: NQ inside the event's
zone touch band and ES inside the ES zone's band +- `es_zone_proximity` at the same moment; area events: ES inside it,
the window starting at the visit start). It counts if one member holds rho at t, confirmed or provisional, and hasn't
been crossed against rho since tau (Q18, `trinity.counts_in_role`). The checked taus are the caller's (the window start
and every ES trade, NQ trade and board apply in it, F2.19).

A block makes the ES positions of the blocked chain's SPY/SPX nodes unreliable; ES chart levels built from ES trades
(ON high/low, PDH/PDL, ...) are not affected. `blocked` = the S&P chains blocked at any moment in the window:
- true if an ES zone with no member from a blocked chain meets the rule (the first such overlap gives the makeup);
- else null if a blocked chain is in `blocked` (a match at a SPY/SPX-node zone can't be ruled out);
- else false.
`es_stack_count` and the ES member types come from the zone that made it true, at its first overlap; null when
`es_at_level` is null; 0 and no types when it is false.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from nqbot.features.trinity import RoleTrack, counts_in_role

SP_CHAINS = frozenset({"SPY", "SPX"})


@dataclass
class EsZone:
    inside_at: Callable[[float], bool]
    members: list = field(default_factory=list)        # RoleTrack per member (F2.6 / F5.2 roles and crosses)
    chains: frozenset = frozenset()                     # S&P chains of its SPY/SPX node members (empty: chart levels only)
    makeup: Callable[[float], dict] | None = None       # tau -> {'es_stack_count', 'es_member_types'}


def first_overlap(z: EsZone, rho: str, taus, t: float) -> float | None:
    for tau in taus:
        if z.inside_at(tau) and any(counts_in_role(m, rho, tau, t) for m in z.members):
            return tau
    return None


def es_at_level(zones, rho: str, taus, t: float, blocked=frozenset()) -> dict:
    blocked = frozenset(blocked) & SP_CHAINS
    hits = [(tau, z) for z in zones if not (z.chains & blocked) and (tau := first_overlap(z, rho, taus, t)) is not None]
    if hits:
        tau, z = min(hits, key=lambda h: h[0])
        mk = z.makeup(tau) if z.makeup else {}
        return {"es_at_level": True, "es_stack_count": mk.get("es_stack_count", len(z.members)),
                "es_member_types": mk.get("es_member_types")}
    if blocked:
        return {"es_at_level": None, "es_stack_count": None, "es_member_types": None}
    return {"es_at_level": False, "es_stack_count": 0, "es_member_types": ()}


def single(inside_at: Callable[[float], bool], track: RoleTrack, chain: str | None = None,
           member_type: str | None = None) -> EsZone:
    """A one-member ES zone (an ES chart level, or a SPY/SPX node with its chain)."""
    return EsZone(inside_at=inside_at, members=[track], chains=frozenset({chain}) if chain else frozenset(),
                  makeup=lambda _tau: {"es_stack_count": 1, "es_member_types": (member_type,) if member_type else ()})
