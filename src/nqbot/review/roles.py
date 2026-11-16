"""A level's role over the session, from the build's stored level events (`level_events/<date>.parquet`), for the
chart (SPEC §9b item 3 Levels, owner 2026-11-14: solid in the original role, dashed / hatched while provisional,
coloured by the current role, a marker at each cross, flip and reclaim).

The transitions are the state machine's own (`features/level_state.py`, F2.6 steps 1-5):
- `role_floor` / `role_ceiling`: the first role (price `rearm_dist` outside the band);
- `cross`: the opposite role, provisionally (a new episode);
- `flip`: the provisional role is confirmed (settled), `flips` + 1;
- `cross_back`: the provisional role was crossed back (the level makes no events until a reclaim or a resume);
- `resume`: the cross back is undone (the same episode goes on);
- `reclaim`: the original role returns.
- `natural_role`, `formation_role`, `return_role` (the 13-0 profile-level return, Q-step13-0 (e) (1)): a confirmed role
  set from price's side; the name doesn't carry the role, so the caller passes it (`(t, name, role)`), read from the
  last trade against the band at that instant (above: floor, below: ceiling).
Every other name (armed, prov_armed, break_start, back_start, resume_start, swept, swept_back, resume_swept) leaves
the role as it is; a name not listed here is logged and ignored (a new state-machine event must be added here).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Sequence

_log = logging.getLogger(__name__)

CEILING, FLOOR = "ceiling", "floor"
MARKER_NAMES = ("cross", "flip", "reclaim")   # §9b item 3: a marker at each cross, flip and reclaim
IGNORED = frozenset({"armed", "prov_armed", "break_start", "back_start", "resume_start", "swept", "swept_back",
                     "resume_swept"})
ROLE_FROM_PRICE = frozenset({"natural_role", "formation_role", "return_role"})


def opposite(role: str) -> str:
    return FLOOR if role == CEILING else CEILING


@dataclass(frozen=True)
class RoleStep:
    """From `t` on (until the next step): the role, whether it is provisional, whether it was crossed back."""
    t: int
    role: str | None
    provisional: bool
    broken_back: bool
    flips: int


def timeline(events: Iterable[Sequence]) -> list[RoleStep]:
    """The role steps of one level from its (t, name) or (t, name, role) events (any order; same-time events keep their
    given order)."""
    evs = sorted(enumerate(events), key=lambda x: (int(x[1][0]), x[0]))
    role = orig = None
    prov = broken = False
    flips = 0
    out: list[RoleStep] = []
    for _, ev in evs:
        t, name = ev[0], ev[1]
        if name in ROLE_FROM_PRICE:
            given = ev[2] if len(ev) > 2 else None
            if given not in (CEILING, FLOOR):
                continue
            role = orig = given
            prov = broken = False
        elif name in ("role_floor", "role_ceiling"):
            role = orig = FLOOR if name == "role_floor" else CEILING
            prov = broken = False
        elif name == "cross" and role is not None:
            orig, role = role, opposite(role)
            prov, broken = True, False
        elif name == "flip" and prov:
            prov, broken = False, False
            flips += 1
        elif name == "cross_back" and prov:
            broken = True
        elif name == "resume" and prov:
            broken = False
        elif name == "reclaim" and prov:
            role, prov, broken = orig, False, False
        else:
            if name not in IGNORED and name not in ("cross", "flip", "cross_back", "resume", "reclaim"):
                _log.warning("level event %r is not known to review.roles; ignored", name)
            continue
        step = RoleStep(int(t), role, prov, broken, flips)
        if out and out[-1].t == step.t:
            out[-1] = step
        elif not out or (out[-1].role, out[-1].provisional, out[-1].broken_back, out[-1].flips) != (
                step.role, step.provisional, step.broken_back, step.flips):
            out.append(step)
    return out


def role_at(steps: list[RoleStep], t: int) -> RoleStep | None:
    """The step in force at `t` (None before the first role)."""
    best = None
    for s in steps:
        if s.t <= t:
            best = s
        else:
            break
    return best


def markers(events: Iterable[Sequence]) -> list[tuple[int, str]]:
    """The cross / flip / reclaim instants of one level, in time order."""
    return sorted((int(e[0]), e[1]) for e in events if e[1] in MARKER_NAMES)
