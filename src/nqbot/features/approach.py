"""F5.18 approach features (structure model; owner 2026-10-16, B4).

At the touch t, prices = the last NQ trade at or before each time (pre-09:30 Globex trades count; one CME day's front
contract: null if a window would reach before the tape's CME day, so never two contracts, §5.1), ATR = the F0.8 value
known at the touch, + = moving toward the zone (approach from below: rising; from above: falling):
- `approach_move_k` = the price change over (t − k, t] in the approach direction ÷ ATR, k ∈ {1, 15} minutes;
- `approach_range_k` = (max − min trade over (t − k, t]) ÷ ATR;
- `approach_straightness_15` = |p(t) − p(t − 15)| ÷ Σ|p(t − 15 + i) − p(t − 16 + i)|, i = 1…15 (prices sampled at each
  whole minute back from t; null if the sum is 0).
"""

from __future__ import annotations

import numpy as np

from nqbot.features.session_tape import MIN_NS, Tape, cme_start_ns

WINDOWS_MIN = (1, 15)   # owner 2026-10-16 (B4), fixed


def approach_features(tape: Tape, t_ns: int, from_below: bool, atr: float | None) -> dict:
    out = {f"approach_move_{k}": None for k in WINDOWS_MIN} | {f"approach_range_{k}": None for k in WINDOWS_MIN}
    out["approach_straightness_15"] = None
    start = cme_start_ns(tape.day)
    sign = 1.0 if from_below else -1.0
    p_t = tape.last_price_at(t_ns)
    if p_t is None:
        return out
    for k in WINDOWS_MIN:
        a = t_ns - k * MIN_NS
        p_a = tape.last_price_at(a)
        if a < start or p_a is None or not atr:
            continue
        out[f"approach_move_{k}"] = sign * (p_t - p_a) / atr
        seg = tape.price[tape.idx_at(a):tape.idx_at(t_ns)]
        if len(seg):
            out[f"approach_range_{k}"] = (float(seg.max()) - float(seg.min())) / atr
    if t_ns - 15 * MIN_NS >= start:
        pts = [tape.last_price_at(t_ns - j * MIN_NS) for j in range(15, -1, -1)]
        if all(p is not None for p in pts):
            steps = float(np.abs(np.diff(pts)).sum())
            if steps > 0:
                out["approach_straightness_15"] = abs(pts[-1] - pts[0]) / steps
    return out
