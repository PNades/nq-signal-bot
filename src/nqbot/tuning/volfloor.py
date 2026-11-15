"""The volatility-scaled stop floor re-fitted on months 1-4 (owner 2026-10-08/11-08, SPEC §13 Q13-vol; 2026-11-14,
Q-step13c), read-only: the step-8b volatility study (findings R12) made a module and run with the frozen mapping values.

Per session: QQQ's event-moment crossing errors on NQ (`step8b_gate.chain_crossings`, the board in force by its apply
time, `map_latency_sec`) with the trailing volatility at each crossing, the mean high-low range of the 1-minute NQ bars
completed in the last 15 clock minutes (`labeler.candidates.trailing_range`, the function the labeler uses).
The fit (the study's rule): the fixed minimum = the 95th percentile of |error| in the quietest quartile of the trailing
volatility; k = the smallest k on a 0.005 grid in [0, 10] with max(minimum, k x volatility) covering >= 95% of the
crossings. QQQ only (owner 2026-11-14, SPEC §13 Q-step13c (8)): NQ stops beyond a QQQ node; NDXP keeps its fixed 6.50
(its crossing error barely grows with volatility, Spearman 0.06 on the five October recordings, and a fit would rest on
those 5 days: its history spot is an NQ price, so months 1-4 hold no NDXP crossings), chart levels 0.
"""

from __future__ import annotations

import numpy as np

WINDOW_MIN = 15            # Q13-vol: the trailing 15-minute volatility (vol15)
COVERAGE = 0.95            # the floor covers 95% of the crossings
QUIET_Q = 0.25             # the fixed minimum: the quietest quartile's ...
QUIET_P = 0.95             # ... 95th percentile
K_GRID = np.linspace(0.0, 10.0, 2001)
SOURCE = "QQQ"


def fit(err: np.ndarray, vol: np.ndarray) -> dict:
    a = np.abs(np.asarray(err, dtype=np.float64))
    v = np.asarray(vol, dtype=np.float64)
    ok = np.isfinite(a) & np.isfinite(v)
    a, v = a[ok], v[ok]
    if len(a) < 20:
        raise ValueError(f"only {len(a)} crossings with a known volatility: too few to fit the floor")
    quiet = v <= np.quantile(v, QUIET_Q)
    fmin = float(np.quantile(a[quiet], QUIET_P))
    k = next((float(kk) for kk in K_GRID if (a <= np.maximum(fmin, kk * v)).mean() >= COVERAGE), None)
    if k is None:
        raise ValueError("no k in [0, 10] covers 95% of the crossings")
    floor = np.maximum(fmin, k * v)
    cuts = np.quantile(v, [0.25, 0.5, 0.75])
    b = np.digitize(v, cuts)
    return {"min": round(fmin, 4), "k": round(k, 4), "n": int(len(a)), "coverage": float((a <= floor).mean()),
            "quartiles": [{"q": i + 1, "n": int((b == i).sum()),
                           "coverage_scaled": float((a[b == i] <= floor[b == i]).mean()) if (b == i).any() else None,
                           "mean_floor": float(floor[b == i].mean()) if (b == i).any() else None}
                          for i in range(4)], "vol_cuts": [float(c) for c in cuts]}


def coverage_fixed(err: np.ndarray, vol: np.ndarray, fixed: float) -> float:
    a = np.abs(np.asarray(err, dtype=np.float64))
    ok = np.isfinite(a) & np.isfinite(np.asarray(vol, dtype=np.float64))
    return float((a[ok] <= fixed).mean()) if ok.any() else float("nan")


def override(res: dict) -> dict:
    """The labeler's `stop_floor_vol` override from a fit."""
    return {"window_min": WINDOW_MIN, SOURCE: {"min": res["min"], "k": res["k"]}}
