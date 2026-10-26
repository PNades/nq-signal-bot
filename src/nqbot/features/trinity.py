"""F2.19 Trinity and F5.16 `es_at_level`: which nodes / ES levels count in the event's role (owner 2026-10-25, Q18/Q19;
`es_at_level` 2026-10-26; SPEC §5.2, §5.4).

A node counts in role rho if it holds rho at t, confirmed or provisional (F2.6 step 5), and has not been crossed against
rho since its overlap moment tau. This replaces "crossed nodes don't count", so post-cross retests are not biased: a
crossed ceiling holding the provisional floor role counts for a long event. A node with no role yet (F2.6 step 1) counts
in rho for Trinity only when its instrument entered the band from rho's side (a ceiling from below, a floor from above).
The overlap search over [t0, t] (prices, bands) is the caller's; this decides the role part at tau and at t.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

CEILING, FLOOR = "ceiling", "floor"


@dataclass
class RoleTrack:
    """One node's F2.6 role history: `roles` = (ts, role or None), in time order; `crosses` = (ts, role crossed)."""
    roles: list = field(default_factory=list)
    crosses: list = field(default_factory=list)

    def role_at(self, ts: float) -> str | None:
        i = bisect_right([r[0] for r in self.roles], ts)
        return self.roles[i - 1][1] if i else None


def entry_role(entered_from: str | None) -> str | None:
    """Q19: an unroled node's role for Trinity from the side its instrument entered from ('below' / 'above')."""
    return CEILING if entered_from == "below" else FLOOR if entered_from == "above" else None


def counts_in_role(track: RoleTrack, rho: str, tau: float, t: float, entered_from: str | None = None) -> bool:
    """True if the node, overlapped at tau, counts toward Trinity in role rho at t (tau <= t)."""
    at_tau, at_t = track.role_at(tau), track.role_at(t)
    if at_tau is None:
        at_tau = entry_role(entered_from)
        if at_t is None:
            at_t = at_tau
    if at_tau != rho or at_t != rho:
        return False
    return not any(tau < ts <= t and r == rho for ts, r in track.crosses)
