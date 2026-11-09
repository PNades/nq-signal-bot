"""Shared events between two builds (owner 2026-11-08, SPEC §13 Q-step13plan (4), declared before any result; golden
zones: owner 2026-11-09, Q-dryrun13 (5); §7.1 Tuning). Used from 13c to set each fold's trading bar on the events both
values of a grid share (Q-step12a (5)).

An event of run A and an event of run B are the same event if they come from the same touching trade (`touch_idx`),
in the same direction, and
- level events: share at least one founding member `level_id` (`founding_level_ids`, F5.5);
- golden-zone events: both are golden-zone events and their bands at the touch overlap (`touch_zone_lo/hi`);
if an event matches more than one event of the other run, the pair with the most founding members in common is taken
(golden: the largest overlap); if that is still tied, the events are unmatched. Matching is one-to-one.
"""

from __future__ import annotations

from typing import Any, Iterable


def _score(a: dict[str, Any], b: dict[str, Any]) -> float:
    ga, gb = bool(a.get("in_golden_zone")), bool(b.get("in_golden_zone"))
    if ga or gb:
        if not (ga and gb):
            return 0.0
        lo, hi = max(a["touch_zone_lo"], b["touch_zone_lo"]), min(a["touch_zone_hi"], b["touch_zone_hi"])
        return max(hi - lo, 0.0)   # the overlap's length (touching bands overlap, with length 0)
    return float(len(set(a.get("founding_level_ids") or ()) & set(b.get("founding_level_ids") or ())))


def _matches(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if bool(a.get("in_golden_zone")) and bool(b.get("in_golden_zone")):
        return a["touch_zone_lo"] <= b["touch_zone_hi"] and b["touch_zone_lo"] <= a["touch_zone_hi"]
    return _score(a, b) >= 1


def match(rows_a: Iterable[dict[str, Any]], rows_b: Iterable[dict[str, Any]]) -> list[tuple[str, str]]:
    """[(event_id of A, event_id of B)] of the shared events (rows: touch rows with `event_id`, `touch_idx`,
    `direction`, `founding_level_ids`, `in_golden_zone`, `touch_zone_lo`, `touch_zone_hi`)."""
    by_key: dict[tuple, tuple[list, list]] = {}
    for side, rows in ((0, rows_a), (1, rows_b)):
        for r in rows:
            by_key.setdefault((int(r["touch_idx"]), int(r["direction"])), ([], []))[side].append(r)
    out = []
    for xs, ys in by_key.values():
        if not xs or not ys:
            continue
        cand = {(i, j): _score(x, y) for i, x in enumerate(xs) for j, y in enumerate(ys) if _matches(x, y)}
        if not cand:
            continue
        best_a: dict[int, set] = {}
        best_b: dict[int, set] = {}
        for i in range(len(xs)):
            s = {j: v for (ii, j), v in cand.items() if ii == i}
            if s:
                m = max(s.values())
                best_a[i] = {j for j, v in s.items() if v == m}
        for j in range(len(ys)):
            s = {i: v for (i, jj), v in cand.items() if jj == j}
            if s:
                m = max(s.values())
                best_b[j] = {i for i, v in s.items() if v == m}
        for i, js in best_a.items():
            if len(js) == 1:
                j = next(iter(js))
                if best_b.get(j) == {i}:
                    out.append((xs[i]["event_id"], ys[j]["event_id"]))
    return sorted(out)
