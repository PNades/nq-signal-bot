"""The month ledger in settings.yaml `months` (SPEC §7 rule 2b), written at step 4b.

Format: month number -> calendar month "YYYY-MM", or a list of them when one ledger month spans several
calendar months (the §12 step-4b month-1 merge: the leftover sessions of the first month plus the next):
    months: {1: ["2025-10", "2025-11"], 2: "2025-12", ..., 12: "2026-10"}
Months 1-6 are development, 7-9 walk-forward test, 10-12 sealed.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

DEVELOPMENT = range(1, 7)  # §7 rule 2b
WALK_FORWARD = range(7, 10)
SEALED = range(10, 13)


class LedgerMissing(Exception):
    pass


class NotDevelopmentMonth(ValueError):
    """A date outside the development months where only development dates are allowed (§7 rule 2b)."""


def ledger(settings: dict[str, Any]) -> dict[int, list[str]]:
    months = settings.get("months")
    if not months:
        raise LedgerMissing("settings.yaml `months` is not set yet (written at step 4b, SSD day)")
    out = {}
    for k, v in months.items():
        out[int(k)] = [str(x) for x in v] if isinstance(v, (list, tuple)) else [str(v)]
    return out


def month_index(day: date, settings: dict[str, Any]) -> int | None:
    """The ledger month number holding `day`, or None if it is outside the window."""
    key = f"{day.year:04d}-{day.month:02d}"
    for number, yms in ledger(settings).items():
        if key in yms:
            return number
    return None


def days_of(number: int, settings: dict[str, Any]) -> list[date]:
    """Every calendar date of one ledger month (all its calendar months)."""
    out = []
    for ym in ledger(settings)[number]:
        y, m = (int(x) for x in ym.split("-"))
        d = date(y, m, 1)
        while d.month == m:
            out.append(d)
            d += timedelta(days=1)
    return out


def span(settings: dict[str, Any]) -> tuple[date, date]:
    """First and last date of the whole window."""
    days = [d for n in ledger(settings) for d in days_of(n, settings)]
    return min(days), max(days)


def require_development(day: date, settings: dict[str, Any], months: range | list[int] = DEVELOPMENT) -> int:
    """Refuse any date outside the given development months (sample days, plots, review cards)."""
    n = month_index(day, settings)
    if n is None or n not in months:
        raise NotDevelopmentMonth(f"{day} is ledger month {n}, not in development months {list(months)} (§7 rule 2b)")
    return n
