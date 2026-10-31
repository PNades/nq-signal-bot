"""F9.10 volume baselines for a session: N(m), `rvol_today`, `vol_tod_w`, `minutes_since_open`, and the RTH normals the
F2.6b cross features divide by (owner 2026-10-17, B1; owner 2026-10-31, SPEC §13 Q-step11a).

The maths is `features/volume_norms.py` (unchanged); this module feeds it one session at a time:
- RTH minute volumes from the session's front-contract trades (side N counts in volume, F0.1);
- the baseline = the prior `vol_baseline_sessions` (20) full sessions: holidays and early closes left out, and the
  sessions the §3.2 damage rule drops (`damage.py`); fewer than 20 -> every feature null (never built on fewer);
- `RthNormals.between(a, b)` = the normal volume over [a, b) clipped to RTH (each minute's N(m) spread evenly over its
  60 seconds), so a normal over any set of seconds is pro-rated exactly like `volume_norms.window_normal`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Iterable

import numpy as np

from nqbot import calendar as cal_mod
from nqbot.features import volume_norms as vn
from nqbot.features.session_tape import MIN_NS, NS, Tape, rth_close_ns, rth_open_ns


def rth_minute_volumes(tape: Tape, cal: dict | None = None) -> np.ndarray:
    """Volume per ET RTH minute 0..389 (09:30 = 0); minutes after an early close stay 0."""
    o, c = rth_open_ns(tape.day), rth_close_ns(tape.day, cal)
    sl = tape.window(o, c)
    m = ((tape.ts[sl] - o) // MIN_NS).astype(np.int64)
    out = np.zeros(vn.RTH_MINUTES)
    np.add.at(out, m, tape.size[sl])
    return out


def is_full_session(day: date, cal: dict | None = None) -> bool:
    return cal_mod.is_trading_day(day, cal) and cal_mod.early_close_et(day, cal) is None


def baseline(day: date, sessions: Iterable[date], minute_volumes: Callable[[date], np.ndarray | None], *, n: int,
             damaged: set[date], cal: dict | None = None) -> np.ndarray | None:
    """N(m) from the `n` most recent full, undamaged sessions before `day` (None if fewer, or one has no data)."""
    prior = vn.baseline_sessions(day, sessions, full=lambda d: is_full_session(d, cal), damaged=damaged, n=n)
    if prior is None:
        return None
    vols = [minute_volumes(d) for d in prior]
    if any(v is None for v in vols):
        return None
    return vn.minute_normals(vols)


@dataclass
class RthNormals:
    """The normal volume over any span, clipped to RTH (used by `level_state` for `xb_vol_rel`)."""
    day: date
    normals: np.ndarray
    open_ns: int
    close_ns: int

    @classmethod
    def of(cls, day: date, normals: np.ndarray, cal: dict | None = None) -> RthNormals:
        return cls(day, np.asarray(normals, dtype=float), rth_open_ns(day), rth_close_ns(day, cal))

    def _cum(self, t: np.ndarray) -> np.ndarray:
        s = np.clip((np.asarray(t, dtype=np.int64) - self.open_ns) / NS, 0.0, (self.close_ns - self.open_ns) / NS)
        m = np.minimum((s // 60).astype(np.int64), len(self.normals) - 1)
        cum = np.r_[0.0, np.cumsum(self.normals)]
        return cum[m] + self.normals[m] * (s - 60 * m) / 60

    def between(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        if len(a) == 0:
            return np.zeros(0)
        return self._cum(b) - self._cum(a)

    def in_rth(self, ts: np.ndarray) -> np.ndarray:
        return (ts >= self.open_ns) & (ts < self.close_ns)


@dataclass
class SessionVolume:
    """Today's per-minute RTH volume against the baseline; every value as of t (trades with ts <= t)."""
    tape: Tape
    normals: np.ndarray | None
    clip_mult: float
    open_ns: int

    @classmethod
    def of(cls, tape: Tape, normals: np.ndarray | None, features: dict[str, Any]) -> SessionVolume:
        return cls(tape, normals, features["rvol_clip_mult"], rth_open_ns(tape.day))

    def _volume_between(self, a_ns: int, b_ns: int) -> float:
        """Volume of trades with a < ts <= b."""
        return float(self.tape.size[self.tape.idx_at(a_ns):self.tape.idx_at(b_ns)].sum())

    def rvol_today(self, t_ns: int) -> float | None:
        if self.normals is None:
            return None
        s = (t_ns - self.open_ns) / NS
        if s <= 0 or s > vn.RTH_SECONDS:
            return None
        k = int(s // 60)
        mv = np.array([self._volume_between(self.open_ns + m * MIN_NS - 1, self.open_ns + (m + 1) * MIN_NS - 1)
                       for m in range(k)]) if k else np.zeros(0)
        partial = self._volume_between(self.open_ns + k * MIN_NS - 1, t_ns)
        return vn.rvol_today(mv, partial, s, self.normals, self.clip_mult)

    def vol_tod(self, t_ns: int, w_sec: float) -> float | None:
        if self.normals is None:
            return None
        s = (t_ns - self.open_ns) / NS
        return vn.vol_tod(self._volume_between(t_ns - int(w_sec * NS), t_ns), self.normals, s, w_sec)

    def minutes_since_open(self, t_ns: int) -> float:
        return (t_ns - self.open_ns) / MIN_NS


def roll_report_rows(rolls: list[dict[str, Any]], sessions: list[date], value_of: Callable[[date], dict | None],
                     span: int = 5) -> list[dict[str, Any]]:
    """The step-11 roll report (F9.10): per roll and day offset -span..+span (sessions, the roll day = 0), the values
    `value_of(day)` returns (`rvol_today` at the RTH close and the median `vol_tod_60` over the RTH minutes)."""
    out = []
    order = sorted(sessions)
    for r in rolls:
        rd = date.fromisoformat(r["roll_day"])
        if rd not in order:
            continue
        k = order.index(rd)
        for off in range(-span, span + 1):
            if 0 <= k + off < len(order):
                d = order[k + off]
                out.append({"root": r["root"], "roll_day": r["roll_day"], "offset": off, "day": d.isoformat(),
                            **(value_of(d) or {})})
    return out
