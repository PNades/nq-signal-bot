"""Credit estimates and the ask-first gate for every Skylit pull (CLAUDE.md rule 2, SPEC §3.1 Credits).

Every pull prints its estimate first. A pull over ASK_ABOVE_CREDITS proceeds only with an explicit
approval at least as large as the estimate (`--approve-credits N`, passed by Claude Code only after the
owner's OK); a pull the known balance can't cover is refused. Smaller pulls proceed after printing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from nqbot.clients.skylit import vol_history_cost

ASK_ABOVE_CREDITS = 5000  # CLAUDE.md rule 2: ask before any Skylit pull over 5,000 credits

# Per-call prices (docs/skylit/openapi.json and flowseeker-openapi.yaml, 2026-10-05).
RANGE_CALL = 25
HISTORICAL_CALL = 5
DP_TRADES_PAGE = 5
DP_TOP_PRINTS = 3
STREAM_PER_SYMBOL_OPEN = 1
STREAM_PER_SYMBOL_MINUTE = 1  # charged in advance per started minute


class CreditRefused(Exception):
    pass


@dataclass
class Estimate:
    what: str
    lines: list[tuple[str, int, int]] = field(default_factory=list)  # (item, count, credits)

    def add(self, item: str, count: int, credits_each: float) -> Estimate:
        self.lines.append((item, count, int(math.ceil(count * credits_each))))
        return self

    def add_total(self, item: str, count: int, credits: int) -> Estimate:
        self.lines.append((item, count, int(credits)))
        return self

    @property
    def total(self) -> int:
        return sum(c for _, _, c in self.lines)

    def text(self) -> str:
        rows = [f"Credit estimate - {self.what}:"]
        rows += [f"  {item}: {count} x -> {credits:,} credits" for item, count, credits in self.lines]
        rows.append(f"  total: {self.total:,} credits (${self.total / 1000:,.2f} at $0.001/credit)")
        return "\n".join(rows)


def range_estimate(what: str, calls: int) -> Estimate:
    return Estimate(what).add("/v1/historical/range calls", calls, RANGE_CALL)


def historical_estimate(what: str, calls: int) -> Estimate:
    return Estimate(what).add("/v1/historical calls", calls, HISTORICAL_CALL)


def dark_pool_estimate(what: str, dates: int, pages_per_date: float = 1.0, top_prints: int = 0) -> Estimate:
    est = Estimate(what).add("/v1/dark-pool/trades pages", int(math.ceil(dates * pages_per_date)), DP_TRADES_PAGE)
    if top_prints:
        est.add("/v1/dark-pool/top-prints calls", top_prints, DP_TOP_PRINTS)
    return est


def stream_estimate(what: str, n_symbols: int, minutes: float, reconnects: int = 0) -> Estimate:
    """Opens + minutes; each reconnect pays another open and starts a new minute charged in advance."""
    opens = n_symbols * (1 + reconnects)
    return (Estimate(what)
            .add("stream opens (per symbol)", opens, STREAM_PER_SYMBOL_OPEN)
            .add("stream symbol-minutes", n_symbols * (int(math.ceil(minutes)) + reconnects), STREAM_PER_SYMBOL_MINUTE))


def vol_history_estimate(what: str, n_symbols: int, start: date, end: date) -> Estimate:
    return Estimate(what).add_total(f"/v1/vol/history {start}..{end}", 1, vol_history_cost(n_symbols, start, end))


def check(est: Estimate, *, balance: int | None, approved: int | None, echo=print) -> None:
    """Print the estimate, then refuse if it needs the owner's OK or the balance can't cover it."""
    echo(est.text())
    if balance is not None:
        echo(f"  balance: {balance:,} credits")
        if est.total > balance:
            raise CreditRefused(f"estimate {est.total:,} exceeds the balance {balance:,}: top-up needed (ask the owner)")
    if est.total > ASK_ABOVE_CREDITS and (approved is None or approved < est.total):
        raise CreditRefused(
            f"estimate {est.total:,} is over {ASK_ABOVE_CREDITS:,} credits: ask the owner, then re-run with "
            f"--approve-credits {est.total}"
        )
