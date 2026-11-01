"""Daily vol context from Tempest and realized vs implied (step 11 part 2; SPEC §3.1 Tempest, §5.2; feature_spec F2.21,
owner 2026-10-20 Q-vix, 2026-10-22 Tempest drafts).

Known at the session start. For QQQ, SPY, NDXP, SPX (Tempest's `SPXW` row) and VIX, the stored row of session
T − `tempest_lag_sessions` (`expected_move.TempestRows`: final copies only, pulled at or after a later pre-open), its
vol fields as stored: SVX 1d / 9d / 30d / 3m / 6m, ATM vol, term slope, 30-day risk reversal, butterfly, skew slope,
call/put tilt (`VOL_FIELDS`). VIX's `close` is Skylit's VIX reference price (the front VIX expiry's forward, it rolls
at each VX expiry), so only `vix_ref_chg` = ln(close(T−1) ÷ close(T−2)) enters, null when T−1 is a roll session (its
row's `front_min` is larger than T−2's). `rv_nq` = the annualised standard deviation of NQ RTH close-to-close log
returns over the last `rv_lookback_sessions` completed sessions (roll-shifted closes from the carry), `rv_iv_ratio` =
`rv_nq` ÷ (QQQ `svx30` / 100). `tempest_draft` flags a row that is not final by the draft rule (QQQ / SPY OHLC
present and a whole `front_min`); the live re-pull loop that waits for the final row is step 22.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Callable

import numpy as np

SYMBOLS = {"QQQ": "QQQ", "SPY": "SPY", "NDXP": "NDXP", "SPX": "SPXW", "VIX": "VIX"}
# F2.21's list in the stored column names: SVX 1d, 9d, 30d, 3m, 6m, ATM vol, term slope, 30-day risk reversal,
# butterfly, skew slope, call/put tilt
VOL_FIELDS = ("svx1d", "svx9", "svx30", "svx3m", "svx6m", "atm_iv_30", "term_slope", "rr25_30", "bf25_30", "skew_s30",
              "tilt_21")
OHLC = ("open", "high", "low", "close")
TRADING_DAYS_PER_YEAR = 252   # F2.21: annualising the daily realized vol


def _f(row: dict | None, key: str) -> float | None:
    if row is None:
        return None
    v = row.get(key)
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def row_is_final(row: dict | None) -> bool:
    """The draft rule (owner 2026-10-22, SPEC §3.1): a QQQ / SPY row is final when its OHLC is filled and its
    `front_min` is a whole number."""
    if row is None:
        return False
    fm = _f(row, "front_min")
    return all(_f(row, k) is not None for k in OHLC) and fm is not None and float(fm).is_integer()


def rv_nq(closes: list[float], n: int) -> float | None:
    """Annualised sd of the last n close-to-close log returns (needs n + 1 closes)."""
    c = [x for x in closes if x is not None]
    if len(c) < n + 1:
        return None
    r = np.diff(np.log(np.asarray(c[-(n + 1):], dtype=float)))
    return float(np.std(r, ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR)) if n > 1 else None


def vix_ref_chg(prev: dict | None, prev2: dict | None) -> float | None:
    c1, c2 = _f(prev, "close"), _f(prev2, "close")
    f1, f2 = _f(prev, "front_min"), _f(prev2, "front_min")
    if None in (c1, c2, f1, f2) or c1 <= 0 or c2 <= 0 or f1 > f2:
        return None
    return math.log(c1 / c2)


def features(rows, day: date, lag: int, session_back: Callable[[date, int], date], rth_closes: list[float],
             rv_lookback: int) -> dict[str, Any]:
    """`rows`: `expected_move.TempestRows`; `rth_closes`: completed RTH closes before `day`, oldest first."""
    d1 = session_back(day, lag)
    d2 = session_back(day, lag + 1)
    out: dict[str, Any] = {}
    for name, sym in SYMBOLS.items():
        row = rows.get(sym, d1)
        for k in VOL_FIELDS:
            out[f"{name.lower()}_{k}"] = _f(row, k)
    out["vix_ref_chg"] = vix_ref_chg(rows.get("VIX", d1), rows.get("VIX", d2))
    rv = rv_nq(rth_closes, rv_lookback)
    svx = _f(rows.get("QQQ", d1), "svx30")
    out["rv_nq"] = rv
    out["rv_iv_ratio"] = None if rv is None or not svx else rv / (svx / 100.0)
    out["tempest_draft"] = not all(row_is_final(rows.get(s, d1)) for s in ("QQQ", "SPY"))
    return out
