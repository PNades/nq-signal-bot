"""The month ledger in settings.yaml `months` (SPEC §7 rule 2b), written at step 4b by `nqbot setup-ssd`.

Format: month number -> calendar month "YYYY-MM", or a list of them when one ledger month spans several
calendar months (the §12 step-4b month-1 merge: the leftover sessions of the first month plus the next):
    months: {1: ["2025-10", "2025-11"], 2: "2025-12", ..., 12: "2026-10"}
Months 1-6 are development, 7-9 walk-forward test, 10-12 sealed.

Every read checks the format (presd_fixes issue 6): month numbers 1, 2, ... with no gap, quoted "YYYY-MM" strings
(an unquoted 2025-10-01 is a YAML date and would match nothing), a list only for month 1, and calendar months
consecutive with no gap or repeat. Loading settings.yaml (config.load_settings) also requires all 12 months.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

DEVELOPMENT = range(1, 7)  # §7 rule 2b
WALK_FORWARD = range(7, 10)
SEALED = range(10, 13)
LEDGER_MONTHS = 12  # §7 rule 2b: a 12-month window (step 18 adds the top-up months)
YM = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class LedgerMissing(Exception):
    pass


class LedgerInvalid(LedgerMissing):
    """A malformed ledger: refused wherever a missing one is (every command that catches LedgerMissing)."""


class NotDevelopmentMonth(ValueError):
    """A date outside the development months where only development dates are allowed (§7 rule 2b)."""


def ym_add(ym: str, n: int) -> str:
    y, m = (int(x) for x in ym.split("-"))
    y, m = divmod(y * 12 + m - 1 + n, 12)
    return f"{y:04d}-{m + 1:02d}"


def validate(months: Any, *, full: bool = False) -> dict[int, list[str]]:
    """The ledger as {number: [YYYY-MM, ...]}, or LedgerInvalid. `full`: exactly LEDGER_MONTHS months."""
    if not isinstance(months, dict) or not months:
        raise LedgerInvalid(f"settings.yaml `months` must be a map of month number -> \"YYYY-MM\", got {months!r}")
    try:
        numbers = sorted(int(k) for k in months)
    except (TypeError, ValueError):
        raise LedgerInvalid(f"settings.yaml `months`: month numbers must be integers, got {list(months)}") from None
    want = list(range(1, (LEDGER_MONTHS if full else len(numbers)) + 1))
    if numbers != want:
        raise LedgerInvalid(f"settings.yaml `months`: month numbers must be {want[0]}..{want[-1]}, got {numbers}")
    out: dict[int, list[str]] = {}
    for k, v in months.items():
        n = int(k)
        values = list(v) if isinstance(v, (list, tuple)) else [v]
        if isinstance(v, (list, tuple)) and (n != 1 or len(values) < 2):
            raise LedgerInvalid(f"settings.yaml `months`: only month 1 may list several calendar months (the step-4b "
                                f"month-1 merge), got {n}: {v!r}")
        for x in values:
            if not isinstance(x, str) or not YM.match(x):
                raise LedgerInvalid(f"settings.yaml `months`: month {n} must be a quoted \"YYYY-MM\" string, got {x!r} "
                                    f"({type(x).__name__})")
        out[n] = values
    flat = [ym for n in sorted(out) for ym in out[n]]
    for a, b in zip(flat, flat[1:]):
        if b != ym_add(a, 1):
            raise LedgerInvalid(f"settings.yaml `months`: calendar months must follow each other with no gap or repeat, "
                                f"{a} is followed by {b}")
    return out


def ledger(settings: dict[str, Any]) -> dict[int, list[str]]:
    months = settings.get("months")
    if not months:
        raise LedgerMissing("settings.yaml `months` is not set yet (written at step 4b, SSD day: uv run nqbot setup-ssd)")
    return validate(months)


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
