"""NQ / ES cross-market reads that need no map (step 11 part 2; SPEC §5.4; feature_spec F5.16, owner 2026-10-16 B5,
2026-10-31 Q-step11d (3)).

All from the session's front-contract tapes (F0.4), trades with `ts_event` <= t only:
- `smt_div`: NQ's high (low) over the last `smt_lookback_min` is a new high (low) versus its prior window of the same
  length while ES's is not (`nq_only`), the reverse (`es_only`), else `none`; null if a window has no trade;
- `rel_delta`: over the last `rel_delta_sec`, (B − A) ÷ (B + A) of NQ minus the same of ES, B / A = buy / sell
  aggressor volume, side-N trades left out of both (Q-sideN, Q-step11d (3)); null if a side has no aggressor volume;
  `delta_sign_agree` = both deltas non-zero with the same sign;
- `rs_nq_es_<k>` for k in {5, 15} minutes (B5, fixed): (NQ(t) ÷ NQ(t − k) − 1) − (ES(t) ÷ ES(t − k) − 1) in %,
  + = NQ stronger in the trade direction (shorts negated); null without a trade at or before t − k on the tape.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from nqbot.features.session_tape import MIN_NS, NS, Tape

RS_WINDOWS_MIN = (5, 15)   # owner 2026-10-16 (B5): fixed, never tuned
SMT_SIDES = ("high", "low")


def _extreme(tape: Tape, t0: int, t1: int, side: str) -> float | None:
    """Max (high) or min (low) trade price in (t0, t1]."""
    p = tape.price[tape.idx_at(t0):tape.idx_at(t1)]
    if not len(p):
        return None
    return float(p.max() if side == "high" else p.min())


def _new_extreme(tape: Tape, t: int, lookback_ns: int, side: str) -> bool | None:
    recent = _extreme(tape, t - lookback_ns, t, side)
    prior = _extreme(tape, t - 2 * lookback_ns, t - lookback_ns, side)
    if recent is None or prior is None:
        return None
    return recent > prior if side == "high" else recent < prior


def smt_div(nq: Tape, es: Tape, t: int, lookback_min: float) -> dict[str, str | None]:
    lb = int(round(lookback_min * MIN_NS))
    out = {}
    for side in SMT_SIDES:
        a, b = _new_extreme(nq, t, lb, side), _new_extreme(es, t, lb, side)
        out[f"smt_div_{side}"] = None if a is None or b is None else \
            "nq_only" if a and not b else "es_only" if b and not a else "none"
    return out


def _delta(tape: Tape, t: int, window_ns: int) -> tuple[float, float]:
    s = slice(tape.idx_at(t - window_ns), tape.idx_at(t))
    side, size = tape.side[s], tape.size[s]
    buy, sell = float(size[side > 0].sum()), float(size[side < 0].sum())
    return buy - sell, buy + sell


def rel_delta(nq: Tape, es: Tape, t: int, window_sec: float) -> dict[str, Any]:
    w = int(round(window_sec * NS))
    dn, vn = _delta(nq, t, w)
    de, ve = _delta(es, t, w)
    if vn <= 0 or ve <= 0:
        return {"rel_delta": None, "delta_sign_agree": None}
    return {"rel_delta": dn / vn - de / ve, "delta_sign_agree": bool(dn != 0 and de != 0 and np.sign(dn) == np.sign(de))}


def rs_nq_es(nq: Tape, es: Tape, t: int, direction: int | None = None) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    sgn = -1.0 if direction is not None and direction < 0 else 1.0
    for k in RS_WINDOWS_MIN:
        t0 = t - k * MIN_NS
        a0, a1, b0, b1 = nq.last_price_at(t0), nq.last_price_at(t), es.last_price_at(t0), es.last_price_at(t)
        if None in (a0, a1, b0, b1) or nq.symbol is None or es.symbol is None:
            out[f"rs_nq_es_{k}"] = None
            continue
        out[f"rs_nq_es_{k}"] = sgn * 100.0 * ((a1 / a0 - 1.0) - (b1 / b0 - 1.0))
    return out


def features_at(nq: Tape, es: Tape, t: int, features: dict, direction: int | None = None) -> dict[str, Any]:
    return {**smt_div(nq, es, t, features["smt_lookback_min"]), **rel_delta(nq, es, t, features["rel_delta_sec"]),
            **rs_nq_es(nq, es, t, direction)}
