"""An account's drawdown floor (SPEC §6.7; the same rule the step-15 prop simulator applies).

static: floor = start - drawdown. eod_trailing: floor = high water (end-of-day balances, start included) - drawdown,
then by the lock trigger: `floor` = min(trailing, lock); `first_payout` = trailing until the first payout, the lock from
then on; `floor_or_first_payout` = min(trailing, lock) until the first payout, the lock from then on.
A breach = an end-of-day balance at or below the floor in force (the one set at the previous session end). Intraday
equity is checked by the simulator from the stored paths; here only end-of-day balances are known.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence


@dataclass(frozen=True)
class Rule:
    start_usd: float
    drawdown_usd: float
    type: Literal["static", "eod_trailing"]
    lock_usd: float | None = None
    lock_trigger: Literal["floor", "first_payout", "floor_or_first_payout"] = "floor"

    @classmethod
    def of(cls, account: Any) -> "Rule":
        """From an account (the panel's schema, or a dict with the same fields)."""
        g = (lambda k: account[k]) if isinstance(account, dict) else (lambda k: getattr(account, k))
        lock = g("drawdown_lock_usd")
        return cls(float(g("start_balance_usd")), float(g("drawdown_usd")), g("drawdown_type"),
                   None if lock is None else float(lock), g("drawdown_lock_trigger") or "floor")

    def text(self) -> str:
        dd = f"${self.drawdown_usd:,.0f}"
        if self.type == "static":
            return f"{dd} static: floor ${self.start_usd - self.drawdown_usd:,.0f}"
        if self.lock_usd is None:
            return f"{dd} EOD-trailing, no lock"
        lock = f"${self.lock_usd:,.0f}"
        if self.lock_trigger == "first_payout":
            return f"{dd} EOD-trailing until the first payout, then locked at {lock}"
        if self.lock_trigger == "floor_or_first_payout":
            return f"{dd} EOD-trailing, locked at {lock} once it gets there or at the first payout"
        return f"{dd} EOD-trailing, locked at {lock} once it gets there"


@dataclass(frozen=True)
class FloorPath:
    floor: list[float]        # the floor set at each session end
    high: list[float]         # the high water at each session end
    locked_at: int | None     # index of the session end where the floor first sat at the lock
    breach_at: int | None     # index of the first session end at or below the floor in force


def floor_after(rule: Rule, high: float) -> float:
    """The floor in force after a session end with this high water, before any payout (§6.7)."""
    if rule.type == "static":
        return rule.start_usd - rule.drawdown_usd
    f = high - rule.drawdown_usd
    if rule.lock_usd is not None and rule.lock_trigger in ("floor", "floor_or_first_payout"):
        f = min(f, rule.lock_usd)
    return f


def floor_path(rule: Rule, eod: Sequence[float], payout_at: int | None = None) -> FloorPath:
    """`eod`: end-of-day balances, one per session; `payout_at`: index of the session end of the first payout."""
    floor: list[float] = []
    high: list[float] = []
    hw = rule.start_usd
    locked_at = breach_at = None
    prev = rule.start_usd - rule.drawdown_usd
    for i, bal in enumerate(eod):
        if breach_at is None and bal <= prev:
            breach_at = i
        hw = max(hw, bal)
        if rule.type == "static":
            f = rule.start_usd - rule.drawdown_usd
        else:
            trail = hw - rule.drawdown_usd
            paid = payout_at is not None and i >= payout_at
            if rule.lock_usd is None:
                f = trail
            elif rule.lock_trigger == "first_payout":
                f = rule.lock_usd if paid else trail
            elif rule.lock_trigger == "floor_or_first_payout":
                f = rule.lock_usd if paid else min(trail, rule.lock_usd)
            else:
                f = min(trail, rule.lock_usd)
            if locked_at is None and rule.lock_usd is not None and f == rule.lock_usd:
                locked_at = i
        floor.append(f)
        high.append(hw)
        prev = f
    return FloorPath(floor, high, locked_at, breach_at)
