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


def zone_freshness(touches: list[TouchRecord], member_ids: list[str], role: str, zone_id: str, t: int) -> dict:
    per = [level_freshness(touches, lid, role, t) for lid in member_ids]
    out = {"zone_tests_so_far": sum(1 for x in touches if x.zone_id == zone_id and x.t_touch < t)}
    for k in ("tests_so_far", "holds_so_far", "pending_tests"):
        vals = [p[k] for p in per]
        out.update({f"{k}_min": min(vals, default=None), f"{k}_max": max(vals, default=None),
                    f"{k}_mean": mean(vals) if vals else None})
    return out
