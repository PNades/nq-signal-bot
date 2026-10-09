"""NDXP mapping candidates (SPEC §5.1, §13 Q8b-1; step-8b findings R6, R9). History's NDXP `spot` is an NQ futures
price, so NDXP's R comes from quantities known before the open, one of two methods (the owner chooses by the lower
intraday p95 against the real NDX spot on his session recordings):

- **basis + carry:** ρ = previous session's NQ settlement ÷ NDX official close (same contract); the log basis decays
  linearly to the contract's final settlement: ln ρ(t) = ln ρ_prev · T(t) / T_prev. R_NDXP(t) = ρ(t).
- **QQQ × k:** k = NDX close ÷ QQQ close, averaged over the previous N sessions, each earlier QQQ close moved to the
  ex-dividend basis when an ex-date falls after it (close − dividend); R_NDXP = R_QQQ ÷ k.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np

ET = ZoneInfo("America/New_York")
NS = 1_000_000_000
QUARTER_MONTHS = {"H": 3, "M": 6, "U": 9, "Z": 12}  # CME equity index futures months
FINAL_SETTLEMENT_ET = time(9, 30)  # NQ final settlement: the special opening quotation of the third Friday
SETTLE_WINDOW_S = 30  # findings R6: settlement proxy = VWAP of the trades in the last 30 s before the close


def contract_expiry(symbol: str) -> datetime:
    """NQZ6 -> 2026-12-18 09:30 ET (third Friday of the contract month)."""
    month, year = QUARTER_MONTHS[symbol[-2]], 2020 + int(symbol[-1])
    first = date(year, month, 1)
    first_friday = first + timedelta(days=(4 - first.weekday()) % 7)
    return datetime.combine(first_friday + timedelta(days=14), FINAL_SETTLEMENT_ET, tzinfo=ET)


def settlement_vwap(ts_ns: np.ndarray, px: np.ndarray, size: np.ndarray, close_ns: int,
                    window_s: int = SETTLE_WINDOW_S) -> float | None:
    k = (ts_ns >= close_ns - window_s * NS) & (ts_ns < close_ns) & (size > 0)
    if not k.any():
        return None
    return float((px[k] * size[k]).sum() / size[k].sum())


def days_to_expiry(at_ns: np.ndarray | int, symbol: str) -> np.ndarray:
    return (contract_expiry(symbol).timestamp() * NS - np.asarray(at_ns, dtype=np.float64)) / NS / 86400


def basis_ratio(at_ns: np.ndarray, symbol: str, settle_prev: float, ndx_close_prev: float, prev_close_ns: int
                ) -> np.ndarray:
    """ρ(t) from the previous session's settlement ÷ NDX close, carried to t."""
    rho_prev = settle_prev / ndx_close_prev
    t_prev = float(days_to_expiry(prev_close_ns, symbol))
    return np.exp(np.log(rho_prev) * days_to_expiry(at_ns, symbol) / t_prev)


def qqq_k(day: date, ndx: dict[date, float], qqq: dict[date, float], dividends: dict[date, float], n: int
          ) -> float | None:
    """k known before `day`'s open: mean of NDX/QQQ over the previous n sessions with both closes, each QQQ close
    moved to the ex basis for every ex-date after it, up to and including `day`."""
    prev = sorted(s for s in set(ndx) & set(qqq) if s < day)[-n:]
    if len(prev) < n:
        return None
    ks = [ndx[s] / (qqq[s] - sum(a for ex, a in dividends.items() if s < ex <= day)) for s in prev]
    return float(np.mean(ks))
