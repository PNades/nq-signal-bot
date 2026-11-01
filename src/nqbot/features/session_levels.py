"""F3.7 overnight and prior-period extremes, F3.8 retested extremes (SPEC §5.3; feature_spec F3.7, F3.8).

- **ON high/low** = the max/min trade in [18:00, 09:30) of CME day D; levels from 09:30 (known then).
- **PDH/PDL** = the max/min over the previous session's CME day [18:00, 17:00) (the previous *session*: holidays are
  skipped); **prior RTH high/low** = the previous session's RTH; **prior week high/low** = the previous week
  [Sunday 18:00, Friday 17:00). All carried levels are roll-shifted (F0.4, `carry.py`); all are sweep-eligible.
- **Retested extremes** (F3.8, FQ-13/FQ-22): a candidate = a registry session high/low (ON, PDH/PDL, prior RTH, prior
  week, IB) of the last `retest_lookback_sessions` sessions; it becomes a `retested_extreme` level once a touch in a
  later session came within `retest_tol_ticks` of it and that touch's structure label resolved *held* (known at the
  resolution time); it ends `retest_lookback_sessions` sessions after its own session, crossed or not (owner
  2026-10-31, SPEC §13 Q-step11b (1)). The touches and labels come from the event builder (part 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np

from nqbot.features.session_tape import TICK, Tape, cme_start_ns, rth_close_ns, rth_open_ns

HIGHS_LOWS_TYPES = ("on_extreme", "pdh_pdl", "prior_rth", "prior_week", "ib")   # FQ-13 candidates


def _hl(tape: Tape, sl: slice) -> tuple[float, float] | None:
    p = tape.price[sl]
    return (float(p.max()), float(p.min())) if len(p) else None


def day_stats(tape: Tape, cal: dict | None = None) -> dict[str, Any]:
    """The session's own extremes and RTH close, for the next session's carried levels."""
    d = tape.day
    o, c = rth_open_ns(d), rth_close_ns(d, cal)
    full = _hl(tape, slice(0, len(tape)))
    rth = _hl(tape, tape.window(o, c))
    on = _hl(tape, tape.window(cme_start_ns(d), o))
    close = tape.last_price_at(c) if rth else None     # the last trade at or before the RTH close (F3.11)
    return {"day": d.isoformat(), "high": full and full[0], "low": full and full[1],
            "rth_high": rth and rth[0], "rth_low": rth and rth[1], "rth_close": close,
            "on_high": on and on[0], "on_low": on and on[1]}


def on_levels(tape: Tape, eth_empty: bool = False) -> tuple[float, float] | None:
    """(ON high, ON low) of today, or None (no overnight trades, or the overnight is damaged, §3.2)."""
    if eth_empty:
        return None
    return _hl(tape, tape.window(cme_start_ns(tape.day), rth_open_ns(tape.day)))


@dataclass(frozen=True)
class Candidate:
    price: float
    type: str
    session: date
    cid: str = ""      # identity fixed when the candidate is made (its price then); a roll shifts the price, not this


@dataclass(frozen=True)
class Retested:
    level_id: str
    price: float
    type: str          # the candidate's own type
    session: date      # E's own session
    created_ns: int    # the held touch's resolution time
    source: str = ""   # the event whose held label created it (its own rows never see it, Q-step11g (2))


def retests_from_touches(candidates: list[Candidate], touches: list[dict[str, Any]], today: date,
                         sessions: list[date], tol_ticks: int, lookback: int) -> list[Retested]:
    """Retested-extreme levels created today by held touches (each touch {'price', 't_resolved', 'label'}): a
    candidate from an earlier session within the lookback whose price the touch came within tol ticks of."""
    order = sorted(sessions)
    pos = {d: k for k, d in enumerate(order)}
    out, seen = [], set()
    for t in sorted(touches, key=lambda x: x["t_resolved"]):
        if t.get("label") != "held":
            continue
        for c in candidates:
            if c.session >= today or (today in pos and c.session in pos and pos[today] - pos[c.session] > lookback):
                continue
            if abs(t["price"] - c.price) <= tol_ticks * TICK + 1e-9:
                lid = (f"retested_extreme-{c.cid}" if c.cid else
                       f"retested_extreme-{c.type}-{c.session.isoformat()}-{c.price:g}")
                if lid not in seen:
                    seen.add(lid)
                    out.append(Retested(lid, c.price, c.type, c.session, int(t["t_resolved"]),
                                        str(t.get("event_id") or "")))
    return out


def within_tol(a: float, b: float, tol_ticks: int) -> bool:
    return abs(a - b) <= tol_ticks * TICK + 1e-9


def dedupe_candidates(candidates: list[Candidate], tol_ticks: int) -> list[Candidate]:
    """One candidate per price (owner 2026-11-01, SPEC §13 Q-step11g (1)): candidates within `retest_tol_ticks` of
    each other are one extreme, and the most recent session's occurrence is kept (its age limit runs from the latest
    session); taken newest first, a candidate within the tolerance of one already kept is dropped."""
    kept: list[Candidate] = []
    for c in sorted(candidates, key=lambda x: (-x.session.toordinal(), x.type, x.price)):
        if not any(within_tol(c.price, k.price, tol_ticks) for k in kept):
            kept.append(c)
    return sorted(kept, key=lambda x: (x.session, x.type, x.price))


def expired(level: Retested, today: date, sessions: list[date], lookback: int) -> bool:
    """FQ-22: it ends `retest_lookback_sessions` sessions after its own session."""
    order = sorted(d for d in sessions if level.session < d <= today)
    return len(order) > lookback
