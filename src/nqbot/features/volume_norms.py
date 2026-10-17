"""Volume baselines for order flow (owner 2026-10-17, SPEC §5.7, §13 Q-featB B1; feature_spec F9.10).

N(m) = for each ET RTH minute m (0 = 09:30, 389 = 15:59), the median NQ volume in that minute over the prior
`vol_baseline_sessions` (20) full sessions: early closes, holidays and sessions the §3.2 damage rule drops are left out,
each prior session's volume from its own front contract (the series the detectors read). Fewer than 20 → null. Computed
from prior sessions only (the pre-open job; the backtest calls the same functions per session, rule 4).

- `vol_tod_w(s)` = volume in (s − w, s] ÷ Σ N(m) × (seconds of m inside the window ÷ 60): each minute's normal pro-rated
  by how much of it the window covers; null if the window leaves RTH.
- `rvol_today(s)` = Σ min(V_m, `rvol_clip_mult` × N(m)) ÷ Σ N(m) over the minutes since 09:30, the current minute
  pro-rated on both sides; each minute clipped before it is added, so one spike can't inflate the rest of the day.
- the detector scale (owner 2026-10-17: the floor and cap on today's part only) = tod_norm_w × clip(rvol_today,
  `of_scale_floor`, `of_scale_cap`), tod_norm_w = the window's pro-rated normal ÷ (the typical RTH minute's normal, the
  median of N, × w ÷ 60). The time-of-day part is never capped: the open and the close run far above 2× a typical minute.
- big trades by time of day only: the 99th percentile of combined order size per `big_trade_slot_min` slot over the
  prior sessions, clipped to [`big_trade_slot_floor`, `big_trade_slot_cap`] × the whole-session 99th percentile.
"""

from __future__ import annotations

from datetime import date
from typing import Callable, Iterable

import numpy as np

RTH_MINUTES = 390  # 09:30-16:00 ET (§1.2)
RTH_SECONDS = RTH_MINUTES * 60
BIG_TRADE_PCT = 99  # §5.7: the 99th percentile of combined order size


def baseline_sessions(day: date, sessions: Iterable[date], *, full: Callable[[date], bool], damaged: set[date],
                      n: int) -> list[date] | None:
    """The `n` most recent full, undamaged sessions before `day` (None if fewer: the volume features are null)."""
    prior = [d for d in sorted(sessions) if d < day and full(d) and d not in damaged][-n:]
    return prior if len(prior) == n else None


def minute_normals(prior_minute_volumes: list[np.ndarray]) -> np.ndarray:
    """N(m): the median volume per RTH minute over the prior sessions (each an array of 390 minute volumes)."""
    return np.median(np.stack([np.asarray(v, dtype=float) for v in prior_minute_volumes]), axis=0)


def window_normal(normals: np.ndarray, s: float, w: float) -> float | None:
    """Σ N(m) pro-rated over the window (s − w, s], s in seconds since 09:30; None if the window leaves RTH."""
    a = s - w
    if w <= 0 or a < 0 or s > RTH_SECONDS:
        return None
    total, m = 0.0, int(a // 60)
    while m * 60 < s:
        lo, hi = max(a, m * 60), min(s, (m + 1) * 60)
        total += normals[m] * (hi - lo) / 60
        m += 1
    return total


def vol_tod(volume: float, normals: np.ndarray, s: float, w: float) -> float | None:
    """`vol_tod_w`: the window's volume ÷ its pro-rated time-of-day normal."""
    n = window_normal(normals, s, w)
    return None if n is None or n <= 0 else volume / n


def tod_norm(normals: np.ndarray, s: float, w: float) -> float | None:
    """The window's normal ÷ the typical RTH minute's normal over the same length (1 = a typical stretch of the day)."""
    n = window_normal(normals, s, w)
    typical = float(np.median(normals))
    return None if n is None or typical <= 0 else n / (typical * w / 60)


def rvol_today(minute_volumes: np.ndarray, partial: float, s: float, normals: np.ndarray, clip_mult: float) -> float | None:
    """Today's relative volume at s seconds after 09:30: `minute_volumes` = the completed minutes' volumes (at least
    floor(s / 60) of them), `partial` = the volume so far in the current minute."""
    if s <= 0 or s > RTH_SECONDS:
        return None
    k, frac = int(s // 60), (s % 60) / 60
    v, n = np.asarray(minute_volumes[:k], dtype=float), normals[:k]
    num, den = float(np.minimum(v, clip_mult * n).sum()), float(n.sum())
    if frac > 0:
        num += min(partial, clip_mult * normals[k] * frac)
        den += normals[k] * frac
    return None if den <= 0 else num / den


def of_scale(normals: np.ndarray, s: float, w: float, rvol: float | None, floor: float, cap: float) -> float | None:
    """The volume-threshold scale for a detector window w ending at s: tod_norm_w × clip(rvol_today, floor, cap)."""
    t = tod_norm(normals, s, w)
    return None if t is None or rvol is None else t * min(max(rvol, floor), cap)


def big_trade_slot_thresholds(prior_orders: list[tuple[np.ndarray, np.ndarray]], slot_min: int, floor: float,
                              cap: float) -> np.ndarray:
    """Per-slot big-trade threshold from the prior sessions' combined RTH orders, each (seconds since 09:30, size):
    the slot's 99th percentile, clipped to [floor, cap] × the whole-session 99th percentile (a slot with no orders
    takes the whole-session value)."""
    secs = np.concatenate([np.asarray(t, dtype=float) for t, _ in prior_orders])
    size = np.concatenate([np.asarray(z, dtype=float) for _, z in prior_orders])
    whole = float(np.percentile(size, BIG_TRADE_PCT))
    n_slots = -(-RTH_MINUTES // slot_min)
    slot = np.minimum((secs // (slot_min * 60)).astype(int), n_slots - 1)
    out = np.full(n_slots, whole)
    for k in range(n_slots):
        x = size[slot == k]
        if len(x):
            out[k] = min(max(float(np.percentile(x, BIG_TRADE_PCT)), floor * whole), cap * whole)
    return out
