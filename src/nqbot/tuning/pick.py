"""The pre-declared pick rules of the step-13 tuning (SPEC §7.1 Tuning detector parameters; owner 2026-11-08, SPEC §13
Q-step13plan (8); the keep-current rule of the variants and Q13-vol; 2026-11-14, Q-step13c).

Each result: {"value": v, "diff": challenger - start (total net R per month), "lo", "hi": its 95% day-block interval}.
"""

from __future__ import annotations

from typing import Any, Callable

TIE_EPS = 1e-9


def best_value(start: Any, results: list[dict[str, Any]], distance: Callable[[Any, Any], float]) -> Any:
    """Detector grids (Q-step13plan (8)): the largest difference wins, the start value counting 0; none above 0: the
    start value stays; ties go to the value closest to the start value; equally far with exactly the same difference:
    the larger lower bound; still equal: the start value stays."""
    if not results:
        return start
    top = max(r["diff"] for r in results)
    if top <= TIE_EPS:
        return start
    tied = [r for r in results if abs(r["diff"] - top) <= TIE_EPS]
    near = min(distance(r["value"], start) for r in tied)
    tied = [r for r in tied if abs(distance(r["value"], start) - near) <= TIE_EPS]
    if len(tied) == 1:
        return tied[0]["value"]
    lo = max(r["lo"] for r in tied)
    tied = [r for r in tied if abs(r["lo"] - lo) <= TIE_EPS]
    return tied[0]["value"] if len(tied) == 1 else start


def keep_current(current: Any, results: list[dict[str, Any]]) -> Any:
    """Variants and Q13-vol: the current value stays unless another has a higher total net R per month with the 95%
    interval of the difference excluding 0; several: the one with the largest lower bound (a tie there: the current
    value stays)."""
    ok = [r for r in results if r["diff"] > 0 and r["lo"] > 0]
    if not ok:
        return current
    lo = max(r["lo"] for r in ok)
    tied = [r for r in ok if abs(r["lo"] - lo) <= TIE_EPS]
    return tied[0]["value"] if len(tied) == 1 else current


def keep_current_lower(current: Any, results: list[dict[str, Any]]) -> Any:
    """Row 14 by log loss (lower is better): a value replaces the current one only if its log loss is lower with the
    interval of the difference (value - current) excluding 0; several: the one with the most negative upper bound."""
    ok = [r for r in results if r["diff"] < 0 and r["hi"] < 0]
    if not ok:
        return current
    hi = min(r["hi"] for r in ok)
    tied = [r for r in ok if abs(r["hi"] - hi) <= TIE_EPS]
    return tied[0]["value"] if len(tied) == 1 else current


def numeric_distance(a: Any, b: Any) -> float:
    return abs(float(a) - float(b))


def index_distance(grid: list[Any]) -> Callable[[Any, Any], float]:
    """Distance by position in a grid (non-numeric values)."""
    return lambda a, b: abs(grid.index(a) - grid.index(b))


CHANGE_STEP = 1e6   # one more thing changed is always farther than any ttl difference (seconds)


def exec_distance(a: tuple, b: tuple) -> float:
    """Row 12 (owner 2026-11-14, SPEC §13 Q-step13c (7)): the distance of an (order type, ttl) point from the start
    point, so that the Q-step13plan (8) tie-break applies as in every other row: closer = fewer things changed (only the
    ttl, then only the order type, then both), then |ttl - start ttl| in seconds."""
    ot, ttl = a[0] != b[0], a[1] != b[1]
    rank = 0 if not (ot or ttl) else 1 if not ot else 2 if not ttl else 3   # only ttl < only order type < both
    return rank * CHANGE_STEP + abs(float(a[1]) - float(b[1]))
