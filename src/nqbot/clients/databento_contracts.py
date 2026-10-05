"""Front contract per CME trading day and roll-day spreads (SPEC §3.2 Front month, §5.1 Same contract; feature_spec F0.4).

A CME trading day D runs [18:00 ET of the previous calendar day, 17:00 ET of D) (F0.2). Databento's continuous
`NQ.v.0` / `ES.v.0` (volume roll) maps each UTC date to one instrument; the contract mapped to date D is used for
the whole CME day D from 18:00, so a session never mixes contract months (the overnight, VWAP and profiles of a
roll day are on the new contract). A non-session weekday (calendar holiday with a Globex session) keeps the
previous session's contract (owner 2026-10-05), so everything up to the roll session is on one contract; the
switch, its spread and the shift of every carried level (the holiday's trades included) land on the next session.

Roll days: on the first session of a new contract, every level and statistic carried from earlier sessions is
shifted by `roll_spread` = new − old contract, each the close of the last 1-second bar ending at or before 17:00 ET
of the previous trading day (bars are labelled by their start, F0.1).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Sequence
from zoneinfo import ZoneInfo

import numpy as np

from nqbot import calendar as cal_mod

ET = ZoneInfo("America/New_York")
CME_DAY_START = time(18, 0)  # F0.2: the CME trading day starts 18:00 ET of the previous calendar day
CME_DAY_END = time(17, 0)  # F0.2 / §3.2: ends 17:00 ET; the roll spread is read at or before 17:00
ONE_SECOND_NS = 1_000_000_000
CONTINUOUS = {"NQ": "NQ.v.0", "ES": "ES.v.0"}  # §3.2: volume roll, historical and live alike
MICRO_OF = {"NQ": "MNQ"}  # MNQ follows the NQ contract month (paired TradersPost tickers, §3.2)


def cme_day_span(day: date) -> tuple[datetime, datetime]:
    """[18:00 ET of day−1, 17:00 ET of day) as aware ET datetimes (DST-safe)."""
    start = datetime.combine(day - timedelta(days=1), CME_DAY_START, tzinfo=ET)
    end = datetime.combine(day, CME_DAY_END, tzinfo=ET)
    return start, end


def contract_on(intervals: Sequence[tuple[date, date, str]], day: date) -> str | None:
    """The raw symbol mapped to `day` by [d0, d1) intervals, or None."""
    for d0, d1, raw in intervals:
        if d0 <= day < d1:
            return raw
    return None


def front_by_day(mappings: dict[str, Sequence[tuple[date, date, str]]], days: Sequence[date],
                 cal: dict[str, Any] | None = None) -> dict[str, dict[date, str]]:
    """{root: {day: raw symbol}} for every given weekday. Sessions take Databento's mapping for their date (a
    session without one is an error); a non-session weekday keeps the previous session's contract (owner
    2026-10-05), or its own mapping before any session; without either it is left out."""
    out: dict[str, dict[date, str]] = {}
    for root, intervals in mappings.items():
        per: dict[date, str] = {}
        last_session: str | None = None
        for day in sorted(days):
            mapped = contract_on(intervals, day)
            if cal_mod.is_trading_day(day, cal):
                if mapped is None:
                    raise ValueError(f"{CONTINUOUS.get(root, root)} has no mapping for session {day}")
                per[day] = last_session = mapped
            elif (held := last_session or mapped) is not None:
                per[day] = held
        out[root] = per
    return out


def micro_symbol(raw: str, root: str = "NQ") -> str:
    """'NQZ5' -> 'MNQZ5': the micro of the same contract month."""
    if not raw.startswith(root):
        raise ValueError(f"{raw!r} is not a {root} contract")
    return MICRO_OF[root] + raw[len(root):]


@dataclass(frozen=True)
class Roll:
    root: str
    roll_day: date  # first session on the new contract
    prev_session: date  # previous trading day: both contracts' 1-second closes are read here
    old: str
    new: str


def roll_days(front: dict[str, dict[date, str]], cal: dict[str, Any] | None = None) -> list[Roll]:
    """Every session whose contract differs from the previous session's (sessions only)."""
    rolls = []
    for root, per in front.items():
        sessions = [d for d in sorted(per) if cal_mod.is_trading_day(d, cal)]
        for prev, day in zip(sessions, sessions[1:]):
            if per[prev] != per[day]:
                rolls.append(Roll(root, day, prev, per[prev], per[day]))
    return sorted(rolls, key=lambda r: (r.roll_day, r.root))


def close_at_or_before(bar_start_ns: np.ndarray, close: np.ndarray, at_ns: int,
                       bar_len_ns: int = ONE_SECOND_NS) -> tuple[float, int] | None:
    """(close, bar start) of the last bar that ends at or before `at_ns`; None if there is none."""
    starts = np.asarray(bar_start_ns, dtype=np.int64)
    if len(starts) and np.any(np.diff(starts) < 0):
        raise ValueError("bar starts must be sorted")
    idx = int(np.searchsorted(starts + bar_len_ns, at_ns, side="right")) - 1
    if idx < 0:
        return None
    return float(np.asarray(close, dtype=np.float64)[idx]), int(starts[idx])


def spread_cutoff_ns(prev_session: date) -> int:
    """17:00 ET of the previous trading day, in UTC ns."""
    at = datetime.combine(prev_session, CME_DAY_END, tzinfo=ET)
    return int(at.timestamp()) * ONE_SECOND_NS


def roll_spread(roll: Roll, old_bars: tuple[np.ndarray, np.ndarray],
                new_bars: tuple[np.ndarray, np.ndarray]) -> dict[str, Any]:
    """new − old at the previous session's 17:00 cutoff, from each contract's (bar start ns, close) 1-second bars."""
    cutoff = spread_cutoff_ns(roll.prev_session)
    old = close_at_or_before(*old_bars, cutoff)
    new = close_at_or_before(*new_bars, cutoff)
    if old is None or new is None:
        missing = [s for s, v in ((roll.old, old), (roll.new, new)) if v is None]
        raise ValueError(f"{roll.root} roll {roll.roll_day}: no 1-second bar at or before 17:00 ET "
                         f"{roll.prev_session} for {missing}")
    rec = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in asdict(roll).items()}
    return {**rec, "old_close": old[0], "old_bar_start_ns": old[1], "new_close": new[0],
            "new_bar_start_ns": new[1], "spread": new[0] - old[0]}


def roll_shift(root: str, day: date, rolls: Sequence[dict[str, Any]]) -> float:
    """The amount every carried level/statistic of `root` is shifted by on `day`: the spread on a roll day, else 0."""
    for r in rolls:
        if r["root"] == root and r["roll_day"] == day.isoformat():
            return float(r["spread"])
    return 0.0
