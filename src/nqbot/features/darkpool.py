"""Dark-pool levels from the raw prints (SPEC §3.1 Dark pools, docs/feature_spec.md F4). Step 7 uses it for
the top-prints cross-check and the late-print check; step 11 builds the levels from the same function.

Per ticker at session T: prints with trade timestamp <= 16:00 ET of session T - dp_lag_sessions and
>= that cutoff - dp_lookback_days calendar days; the dp_top_n largest by notional (ties: later timestamp first).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
CUTOFF_ET = time(16, 0)  # F4: prints through 16:00 ET of the cutoff session


@dataclass(frozen=True)
class TopList:
    ticker: str
    session: date
    prints: list[dict[str, Any]]  # each: timestamp (aware datetime), price, size, notional, ...
    cutoff: datetime
    lower: datetime
    dp_partial: bool


def previous_session(day: date, n: int, is_trading_day: Callable[[date], bool]) -> date:
    """The trading session n sessions before `day`."""
    d = day
    for _ in range(n):
        d -= timedelta(days=1)
        while not is_trading_day(d):
            d -= timedelta(days=1)
    return d


def _rank(rows: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda p: (p["notional"], p["timestamp"]), reverse=True)[:top_n]


def top_prints(prints: list[dict[str, Any]], ticker: str, session: date, *, top_n: int, lookback_days: int,
               lag_sessions: int, is_trading_day: Callable[[date], bool], history_start: date | None = None) -> TopList:
    """F4 top list for one ticker at session T. history_start = first ET date of pulled prints (for dp_partial)."""
    cutoff_day = previous_session(session, lag_sessions, is_trading_day)
    cutoff = datetime.combine(cutoff_day, CUTOFF_ET, tzinfo=ET)
    lower = cutoff - timedelta(days=lookback_days)
    rows = [p for p in prints if p["ticker"] == ticker and lower <= p["timestamp"] <= cutoff]
    partial = history_start is not None and lower < datetime.combine(history_start, time(0), tzinfo=ET)
    return TopList(ticker, session, _rank(rows, top_n), cutoff, lower, partial)


def top_prints_api_window(prints: list[dict[str, Any]], ticker: str, as_of: date, *, top_n: int,
                          lookback_days: int) -> list[dict[str, Any]]:
    """The same ranking over Skylit's top-prints window [as_of - lookback_days, as_of] (ET trade dates)."""
    first = as_of - timedelta(days=lookback_days)
    rows = [p for p in prints if p["ticker"] == ticker and first <= p["timestamp"].astimezone(ET).date() <= as_of]
    return _rank(rows, top_n)


def same_print(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Identity = ticker + print timestamp + price (§3.1)."""
    return (a.get("ticker"), a["timestamp"], round(float(a["price"]), 6)) == (b.get("ticker"), b["timestamp"], round(float(b["price"]), 6))
