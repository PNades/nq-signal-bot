"""Cost estimates and the ask-first gate for every Databento pull (CLAUDE.md rule 2, SPEC §3.2).

The Standard plan includes `ohlcv-1s` (16+ years) and the last 12 months of L1 history (`trades`, `mbp-1`)
*at the time of the pull*. Everything else is pay-as-you-go: at step 8 that is the warm-up `trades` and any
window day that has left the included L1 window by the time it is pulled. Every pull prints the
`metadata.get_cost` amounts first; any pay-as-you-go amount needs the owner's OK (`--approve-usd X`, no free
threshold for paid pulls); included items proceed after printing. The classification is redone on every
job run, so a day that ages out between runs needs approval too (and waits until it has it).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from nqbot.clients.databento_client import OHLCV_1S

L1_INCLUDED_MONTHS = 12  # §3.2: Standard plan includes the last 12 months of L1 history


def months_back(day: date, months: int) -> date:
    """The same day-of-month `months` earlier (clamped to the month's last day)."""
    y, m = divmod(day.year * 12 + day.month - 1 - months, 12)
    m += 1
    for d in range(day.day, 0, -1):
        try:
            return date(y, m, d)
        except ValueError:
            continue
    raise AssertionError("unreachable")


# Safety margin, not a SPEC number: a day at the exact boundary may already be outside the included window when
# the request reaches Databento, so it is treated as pay-as-you-go (it can only make the bot ask more, never spend).
L1_BOUNDARY_MARGIN_DAYS = 1


def l1_included_from(today: date) -> date:
    """First date whose L1 data is still treated as inside the included window."""
    return months_back(today, L1_INCLUDED_MONTHS) + timedelta(days=L1_BOUNDARY_MARGIN_DAYS)


def is_payg(schema: str, start: datetime, today: date) -> bool:
    """True if this request is charged on top of the plan."""
    if schema == OHLCV_1S:
        return False
    return start.date() < l1_included_from(today)


@dataclass
class Estimate:
    what: str
    lines: list[tuple[str, int, float, bool]] = field(default_factory=list)  # (item, days, usd, payg)

    def add(self, item: str, days: int, usd: float, payg: bool) -> Estimate:
        self.lines.append((item, days, float(usd), payg))
        return self

    @property
    def payg_usd(self) -> float:
        return sum(usd for _, _, usd, payg in self.lines if payg)

    @property
    def included_usd(self) -> float:
        return sum(usd for _, _, usd, payg in self.lines if not payg)

    def text(self) -> str:
        # Owner (2026-10-05): data the Standard plan includes costs nothing extra, so it is shown as $0; get_cost's
        # figure for it (list price, or 0) is kept only as a note.
        rows = [f"Databento cost estimate (metadata.get_cost) - {self.what}:"]
        for item, days, usd, payg in self.lines:
            if payg:
                rows.append(f"  {item}: {days} day(s) -> ${usd:,.2f} PAY-AS-YOU-GO")
            else:
                note = f" (included in Standard; get_cost says ${usd:,.2f})" if usd else " (included in Standard)"
                rows.append(f"  {item}: {days} day(s) -> $0.00{note}")
        rows.append(f"  total extra charge: ${self.payg_usd:,.2f} (pay-as-you-go only; included data is $0)")
        return "\n".join(rows)


def check(est: Estimate, *, approved_usd: float | None, echo=print) -> bool:
    """Print the estimate; True if the pay-as-you-go items may be pulled (they cost $0, or the owner approved at
    least their amount). Without approval they wait (never bought); included items go ahead regardless."""
    echo(est.text())
    if est.payg_usd <= 0:
        return True
    need_cents = math.ceil(round(est.payg_usd * 100, 6))  # rounded up to the cent, so the suggestion always passes
    if approved_usd is not None and round(approved_usd * 100) >= need_cents:
        echo(f"  approved: ${approved_usd:,.2f}")
        return True
    echo(f"  NOT approved{'' if approved_usd is None else f' (approved ${approved_usd:,.2f})'}: the pay-as-you-go "
         f"items wait. Ask the owner, then re-plan with --approve-usd {need_cents / 100:.2f}")
    return False
