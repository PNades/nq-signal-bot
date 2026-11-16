"""CSV export of the trade ledger (SPEC §9b item 6 Data): the same ledger guard applies. An export names the phases or
dates it wants; any that may not be shown trade by trade is refused outright (`guard.Refused`), and an export of
"everything" only ever contains what the guard lets through at trade level."""
from __future__ import annotations

import csv
import io
from datetime import date
from typing import Iterable

from .guard import Guard, Phase, apply, require
from .ledger import Trade

COLUMNS = ["id", "date", "entry_time", "exit_time", "account", "source", "direction", "style", "size", "minis", "micros",
           "entry", "exit", "net_usd", "r", "commission_usd", "mae_usd", "run"]


def csv_text(trades: Iterable[Trade], guard: Guard, *, phases: Iterable[Phase] = (), days: Iterable[date] = ()) -> tuple[str, dict]:
    """The CSV and what was held back ({phase: {count, reason, gate}}). Raises Refused for a refused explicit request."""
    phases, days = list(phases), list(days)
    require(guard, "trade", phases=phases, days=days)
    kept = apply(trades, "trade", guard)
    rows = kept.trades
    if phases:
        rows = [t for t in rows if guard.phase_of(t) in phases]
    if days:
        want = set(days)
        rows = [t for t in rows if t.date in want]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for t in rows:
        w.writerow(t.row())
    return buf.getvalue(), kept.held
