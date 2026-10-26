"""F2.8 velocity, F2.9 velocity lean and F2.18 bias / whipsaw (owner 2026-10-25, SPEC §5.2).

- Per-node `velocity` stays signed, (v - v') / |v'| (it matches Skylit's `velocityPct`).
- The lean uses the change in **magnitude**, (|v| - |v'|) / |v'|, so a negative node growing from -1M to -2M counts +1.0
  (building), not -1.0.
- `lean_raw` = (sum above spot) - (sum below spot) of magnitude velocity x pct_of_king over relevant nodes, so positive =
  accumulation above spot = bias up; one sign everywhere (F2.9, F2.18).
- Above / below by the board's own spot (F2.1); a strike at spot is on neither side.
"""

from __future__ import annotations


def velocity(v: float | None, v_prev: float | None) -> float | None:
    """Signed per-node velocity (F2.8); null with no earlier board or a zero earlier value."""
    if v is None or v_prev is None or v_prev == 0:
        return None
    return (v - v_prev) / abs(v_prev)


def magnitude_velocity(v: float | None, v_prev: float | None) -> float | None:
    """The lean's per-node input: the change in |value| over |earlier value| (F2.9)."""
    if v is None or v_prev is None or v_prev == 0:
        return None
    return (abs(v) - abs(v_prev)) / abs(v_prev)


def lean_raw(nodes, spot: float) -> float:
    """`nodes`: (strike, magnitude velocity or None, pct_of_king, relevant). Above-spot sum minus below-spot sum."""
    total = 0.0
    for strike, mv, pct, relevant in nodes:
        if not relevant or mv is None or strike == spot:
            continue
        total += mv * pct if strike > spot else -mv * pct
    return total


def lean(times, raws, t: float, window_sec: float) -> float | None:
    """Time-weighted mean of the step function `lean_raw` over [t - window, t], only after the first board (F2.9)."""
    pts = [(ti, r) for ti, r in zip(times, raws) if ti <= t]
    if not pts:
        return None
    lo = max(t - window_sec, pts[0][0])
    acc = dur = 0.0
    for i, (ti, r) in enumerate(pts):
        end = pts[i + 1][0] if i + 1 < len(pts) else t
        d = min(end, t) - max(ti, lo)
        if d > 0:
            acc, dur = acc + r * d, dur + d
    return acc / dur if dur > 0 else pts[-1][1]


def bias(lean_value: float | None, deadband: float) -> str | None:
    """F2.18: up if lean > deadband, down if lean < -deadband, else pinned."""
    if lean_value is None:
        return None
    return "up" if lean_value > deadband else "down" if lean_value < -deadband else "pinned"


def trinity_bias(spy: str | None, spx: str | None, nasdaq: str | None) -> tuple[int, bool]:
    """(`trinity_bias_agree`, `trinity_bias_conflict`) for one trinity (SPY, SPX and QQQ or NDXP, Q17: twice)."""
    b = [x for x in (spy, spx, nasdaq) if x in ("up", "down")]
    return max((b.count("up"), b.count("down"))), ("up" in b and "down" in b)


def whipsaw(spy: str | None, spx: str | None, nasdaq: str | None, nasdaq_has_floor: bool,
            nasdaq_has_ceiling: bool) -> bool:
    """F2.18 flag for one trinity (`whipsaw_qqq`, `whipsaw_ndxp`): conflict while the Nasdaq map has a floor and a ceiling."""
    return trinity_bias(spy, spx, nasdaq)[1] and nasdaq_has_floor and nasdaq_has_ceiling
