"""Front contract per CME trading day and roll-day spreads (SPEC §3.2 Front month, §5.1 Same contract; feature_spec F0.4).

A CME trading day D runs [18:00 ET of the previous calendar day, 17:00 ET of D) (F0.2). The front contract follows
the previous-session volume rule (owner 2026-10-30, SPEC §13 Q-frontroll): the next quarterly becomes the front from
18:00 ET starting session T if its CME-day volume (18:00–17:00) on the previous full session exceeded the current
front's, so day T's contract is decided from data up to 17:00 ET of T−1 only; never switched back. One contract is
used for the whole CME day from 18:00, so a session never mixes contract months (the overnight, VWAP and profiles
of a roll day are on the new contract). A non-session weekday (calendar holiday with a Globex session) never
triggers a switch and keeps the previous session's contract (owner 2026-10-05), so the switch, its spread and the
shift of every carried level (the holiday's trades included) land on a session. `front_by_volume` is the one
function the backtest (step 8) and the live evening step (step 22) both use.

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
MICRO_OF = {"NQ": "MNQ"}  # MNQ follows the NQ contract month (paired TradersPost tickers, §3.2)
QUARTER_MONTH = {"H": 3, "M": 6, "U": 9, "Z": 12}  # CME month codes of the quarterly contracts
QUARTER_CODE = {m: c for c, m in QUARTER_MONTH.items()}
# CME's roll date, the Thursday 8 days before the third-Friday expiry: reported next to the bot's roll, never used.
CME_ROLL_DAYS_BEFORE_EXPIRY = 8


class VolumeMissing(ValueError):
    """A contract's CME-day volume for a session the front rule needs is not stored."""


def cme_day_span(day: date) -> tuple[datetime, datetime]:
    """[18:00 ET of day−1, 17:00 ET of day) as aware ET datetimes (DST-safe)."""
    start = datetime.combine(day - timedelta(days=1), CME_DAY_START, tzinfo=ET)
    end = datetime.combine(day, CME_DAY_END, tzinfo=ET)
    return start, end


# --- quarterly contracts ----------------------------------------------------------------------------------

def _parse(raw: str, root: str) -> tuple[str, int]:
    code, digit = raw[len(root):len(root) + 1], raw[len(root) + 1:]
    if not raw.startswith(root) or code not in QUARTER_MONTH or not digit.isdigit() or len(digit) != 1:
        raise ValueError(f"{raw!r} is not a quarterly {root} contract")
    return code, int(digit)


def contract_symbol(root: str, year: int, month: int) -> str:
    """('NQ', 2025, 12) -> 'NQZ5' (Databento raw symbol: one year digit)."""
    return f"{root}{QUARTER_CODE[month]}{year % 10}"


def contract_month(raw: str, root: str, near: date) -> tuple[int, int]:
    """(year, month) of a quarterly raw symbol; the year digit is read as the first year from near.year − 1 on."""
    code, digit = _parse(raw, root)
    year = near.year - 1
    while year % 10 != digit:
        year += 1
    return year, QUARTER_MONTH[code]


def expiry(raw: str, root: str, near: date) -> date:
    """The third Friday of the contract month (equity-index quarterly expiry)."""
    year, month = contract_month(raw, root, near)
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def cme_roll_date(old: str, root: str, near: date) -> date:
    """CME's roll date out of `old`: the Thursday 8 days before its expiry (reported only, never used)."""
    return expiry(old, root, near) - timedelta(days=CME_ROLL_DAYS_BEFORE_EXPIRY)


def next_quarterly(raw: str, root: str) -> str:
    """'NQZ5' -> 'NQH6': the next quarterly contract, the only candidate to replace the front."""
    code, digit = _parse(raw, root)
    month = QUARTER_MONTH[code] + 3
    return f"{root}{QUARTER_CODE[month - 12 if month > 12 else month]}{(digit + (month > 12)) % 10}"


def unexpired_quarterlies(root: str, day: date, n: int) -> list[str]:
    """The n quarterly contracts nearest to expiry whose expiry is on or after `day`, nearest first."""
    raw = contract_symbol(root, day.year, ((day.month - 1) // 3 + 1) * 3)
    if expiry(raw, root, day) < day:
        raw = next_quarterly(raw, root)
    out = [raw]
    while len(out) < n:
        out.append(next_quarterly(out[-1], root))
    return out


# --- the front contract (owner 2026-10-30, SPEC §13 Q-frontroll) ---------------------------------------------

def session_before(day: date, cal: dict[str, Any] | None = None) -> date:
    """The last trading day before `day` (a holiday's Globex day is not a full session, §1.2)."""
    d = day - timedelta(days=1)
    while not cal_mod.is_trading_day(d, cal):
        d -= timedelta(days=1)
    return d


def _volume(volumes: dict[str, dict[date, int]], root: str, raw: str, session: date) -> int:
    v = (volumes.get(raw) or {}).get(session)
    if v is None:
        raise VolumeMissing(f"{root}: no CME-day volume stored for {raw} on {session}")
    return int(v)


def seed_front(volumes: dict[str, dict[str, dict[date, int]]], session: date) -> dict[str, str]:
    """The front on `session`, the last session before the days the rule decides: the larger CME-day volume of the
    two nearest unexpired quarterlies that day (only a starting point; every later day follows the rule)."""
    out = {}
    for root, vols in volumes.items():
        near, nxt = unexpired_quarterlies(root, session, 2)
        out[root] = nxt if _volume(vols, root, nxt, session) > _volume(vols, root, near, session) else near
    return out


def front_by_volume(volumes: dict[str, dict[str, dict[date, int]]], days: Sequence[date], seed: dict[str, str],
                    cal: dict[str, Any] | None = None) -> dict[str, dict[date, str]]:
    """{root: {day: raw symbol}} for every given weekday by the previous-session volume rule.

    `volumes[root][raw][session]` is the contract's CME-day volume (18:00–17:00 ET); `seed[root]` is the front on
    the last session before `days`. For each session T, the next quarterly after the current front replaces it if
    its volume on the previous full session (T−1) exceeded the front's (a tie keeps the front); day T's own volume
    never decides day T, and the front never switches back. A non-session weekday keeps the previous session's
    contract and never triggers a switch. The sessions in `days` must be consecutive (a missing session is an
    error); a missing volume raises VolumeMissing."""
    days = sorted(days)
    out: dict[str, dict[date, str]] = {}
    for root, front in seed.items():
        vols = volumes.get(root) or {}
        per: dict[date, str] = {}
        prev = session_before(days[0], cal)
        for day in days:
            if not cal_mod.is_trading_day(day, cal):
                per[day] = front
                continue
            if session_before(day, cal) != prev:
                raise ValueError(f"{root}: the days skip the session {session_before(day, cal)} before {day}")
            nxt = next_quarterly(front, root)
            if _volume(vols, root, nxt, prev) > _volume(vols, root, front, prev):
                front = nxt
            per[day] = front
            prev = day
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
