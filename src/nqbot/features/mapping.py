"""Strike -> futures mapping ratio R = F / S (SPEC §5.1). Step 7 starts it; step 11 adds the rest of §5.1
(outlier rejection, re-acceptance, the open skip, band conversion and rounding).

S is the board's own `spot`; F is the last NQ/ES trade at or before the board's effective time τ:
τ = asOf for 1-second boards, asOf + 60 s for 1-minute boards until the spot-lag test clears them (§3.1 Ingest).
Timestamps are integer nanoseconds since the epoch (UTC), so "at or before" is exact.
"""

from __future__ import annotations

import numpy as np

ONE_MINUTE_TAU_SHIFT_NS = 60 * 1_000_000_000  # §3.1 Ingest: uncleared 1-minute boards hold the minute's end state


def board_tau_ns(as_of_ns: np.ndarray, resolution: np.ndarray | str, *, spot_lag_cleared: bool = False) -> np.ndarray:
    """Each board's effective time τ (ns)."""
    as_of_ns = np.asarray(as_of_ns, dtype=np.int64)
    res = np.broadcast_to(np.asarray(resolution), as_of_ns.shape)
    shift = np.where((res == "1m") & (not spot_lag_cleared), ONE_MINUTE_TAU_SHIFT_NS, 0)
    return as_of_ns + shift


def last_price_at_or_before(trade_ts_ns: np.ndarray, trade_px: np.ndarray, at_ns: np.ndarray) -> np.ndarray:
    """The last trade price with timestamp <= at (NaN before the first trade). trade_ts_ns must be sorted."""
    ts = np.asarray(trade_ts_ns, dtype=np.int64)
    if len(ts) and np.any(np.diff(ts) < 0):
        raise ValueError("trade timestamps must be sorted")
    idx = np.searchsorted(ts, np.asarray(at_ns, dtype=np.int64), side="right") - 1
    px = np.asarray(trade_px, dtype=np.float64)
    out = np.full(idx.shape, np.nan)
    ok = idx >= 0
    out[ok] = px[idx[ok]]
    return out


def ratio_for_boards(as_of_ns: np.ndarray, spot: np.ndarray, resolution: np.ndarray | str,
                     trade_ts_ns: np.ndarray, trade_px: np.ndarray, *, spot_lag_cleared: bool = False) -> np.ndarray:
    """R = F(τ) / spot per board (NaN when no trade precedes τ)."""
    tau = board_tau_ns(as_of_ns, resolution, spot_lag_cleared=spot_lag_cleared)
    return last_price_at_or_before(trade_ts_ns, trade_px, tau) / np.asarray(spot, dtype=np.float64)
