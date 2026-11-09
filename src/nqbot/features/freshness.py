"""F5.11 freshness (SPEC §5.4 Freshness; feature_spec F5.11).

Per level (`level_id`) and per role: `tests_so_far` = touches of zones containing the level before t (today, and since
creation for multi-day levels), `holds_so_far` = those whose structure label resolved *held* by t, `pending_tests` =
those not yet resolved at t, `minutes_since_last_test`. Zone features: the min / max / mean over the members plus the
zone's own test count (by `zone_id`). Only outcomes resolved by t count (F0.1). The touch records come from the event
builder (part 3); multi-day levels carry theirs.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Iterable

MIN_NS = 60 * 1_000_000_000
# Q-step12b (b) (owner 2026-11-03): a zone that absorbs another keeps the larger of the two touch counts. A diagnostic
# switch of the step-13-0 check only (False = features_version <= 36); every feature build refuses another value
# (`features.diagnostics.require_defaults`).
MERGE_KEEPS_COUNT = True


@dataclass(frozen=True)
class TouchRecord:
    t_touch: int
    level_ids: tuple
    zone_id: str
    role: str
    t_resolved: int | None = None     # the structure label's resolution time (None = never resolved yet)
    label: str | None = None          # held / broke / timeout


def level_freshness(touches: Iterable[TouchRecord], level_id: str, role: str, t: int) -> dict:
    mine = [x for x in touches if level_id in x.level_ids and x.role == role and x.t_touch < t]
    held = [x for x in mine if x.label == "held" and x.t_resolved is not None and x.t_resolved <= t]
    pending = [x for x in mine if x.t_resolved is None or x.t_resolved > t]
    last = max((x.t_touch for x in mine), default=None)
    return {"tests_so_far": len(mine), "holds_so_far": len(held), "pending_tests": len(pending),
            "minutes_since_last_test": None if last is None else (t - last) / MIN_NS}


def zone_test_count(own: list[TouchRecord], zone_id: str, t: int, absorbed: dict | None = None) -> int:
    """The zone's own touch count before t (F5.11). With `absorbed` ({zone_id: [(t_merge, absorbed zone_id)]}, the
    zone book's merges) a merge keeps the larger of the two zones' counts, never resetting or summing (owner
    2026-11-03, SPEC §13 Q-step12b (b)); later own touches add to it."""
    if not MERGE_KEEPS_COUNT or not absorbed or not absorbed.get(zone_id):
        return sum(1 for x in own if x.zone_id == zone_id and x.t_touch < t)
    evs = [(x.t_touch, 1, None) for x in own if x.zone_id == zone_id and x.t_touch < t]
    evs += [(int(tm), 0, y) for tm, y in absorbed[zone_id] if tm <= t]   # a merge at a touch's instant comes first
    n = 0
    for tm, kind, y in sorted(evs, key=lambda e: (e[0], e[1])):
        n = n + 1 if kind else max(n, zone_test_count(own, y, tm, absorbed))
    return n


def zone_freshness(touches: list[TouchRecord], member_ids: list[str], role: str, zone_id: str, t: int,
                   zone_touches: list[TouchRecord] | None = None, absorbed: dict | None = None) -> dict:
    """`zone_touches`: the records the zone's own count is taken from. A `zone_id` is unique within one session
    only (F5.5: every session numbers its zones from z1), so a caller holding carried records of earlier sessions
    passes today's records here; by default `touches` (all from one session). `absorbed`: see `zone_test_count`."""
    per = [level_freshness(touches, lid, role, t) for lid in member_ids]
    own = touches if zone_touches is None else zone_touches
    out = {"zone_tests_so_far": zone_test_count(own, zone_id, t, absorbed)}
    for k in ("tests_so_far", "holds_so_far", "pending_tests"):
        vals = [p[k] for p in per]
        out.update({f"{k}_min": min(vals, default=None), f"{k}_max": max(vals, default=None),
                    f"{k}_mean": float(mean(vals)) if vals else None})
    return out
