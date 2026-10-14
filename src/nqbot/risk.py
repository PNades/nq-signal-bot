"""Per-trade risk cap, lives and the loss-run limit (SPEC §6.1, §6.5b, §6.8; owner 2026-10-14, §13 Q-riskfrac).

Shared by the prop simulator and live (rule 4): one place computes how much an account may risk and how many full
losses it can still take. Sizing into contracts (§6.1 order of operations) builds on these at step 15.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

# §6.1 (owner 2026-10-14): an account risks at most 1/N of its total drawdown, N a whole number from this set
# (1/2 is the upper limit; nothing below 1/4, the Conservative and Protect profiles already lower risk).
ALLOWED_DIVISORS = (2, 3, 4)
DEFAULT_DIVISOR = 3
GATE_DIVISOR = 3  # gate_account stays at 1/3 (§6.5b)
FULL_LOSS_R = -0.9  # §6.5b: a full loss = a stop outcome with account R <= -0.9


class RiskConfigError(ValueError):
    pass


def check_divisor(n: Any) -> int:
    """`max_risk_fraction_divisor` must be one of 2, 3, 4 (a bool or a fraction like 2.5 is refused)."""
    if isinstance(n, bool) or not isinstance(n, int) or n not in ALLOWED_DIVISORS:
        raise RiskConfigError(f"max_risk_fraction_divisor must be one of {ALLOWED_DIVISORS} (risk 1/2 to 1/4 of the "
                              f"drawdown), got {n!r}")
    return n


def divisor_of(account: dict[str, Any]) -> int:
    return check_divisor(account.get("max_risk_fraction_divisor", DEFAULT_DIVISOR))


def risk_cap_usd(drawdown_usd: float, n: int) -> float:
    """§6.1: the static per-trade cap, drawdown ÷ N (never shrinks after a loss)."""
    return drawdown_usd / check_divisor(n)


def default_risk_usd(drawdown_usd: float, n: int) -> int:
    """§6.1: `risk_usd_per_trade` follows the setting: floor(drawdown ÷ N) (the owner may type a lower amount)."""
    return math.floor(risk_cap_usd(drawdown_usd, n))


def trade_risk_usd(risk_usd_per_trade: float, profile_multiplier: float, drawdown_usd: float, n: int,
                   custom_risk_usd: float | None = None, remaining_daily_usd: float | None = None) -> float:
    """§6.1 order of operations, the dollar part: min(fixed risk × profile multiplier (Custom: custom_risk_usd),
    drawdown ÷ N, what is left of today's loss allowance when the account has a daily cap)."""
    base = custom_risk_usd if custom_risk_usd is not None else risk_usd_per_trade * profile_multiplier
    risk = min(base, risk_cap_usd(drawdown_usd, n))
    if remaining_daily_usd is not None:
        risk = min(risk, max(0.0, remaining_daily_usd))
    return risk


def lives(drawdown_remaining_usd: float, risk_usd: float, dust_usd: float) -> int:
    """§6.5b: floor((drawdown_remaining − lives_dust_usd) ÷ risk) + 1; <= 1 is the last life (0: only dust left)."""
    if risk_usd <= 0:
        raise RiskConfigError("risk must be positive")
    return max(0, math.floor((drawdown_remaining_usd - dust_usd) / risk_usd) + 1)


def loss_run_len(drawdown_usd: float, risk_usd: float, dust_usd: float) -> int:
    """§6.5b: L = the lives at a full drawdown (1/2 -> 2, 1/3 -> 3, 1/4 -> 4)."""
    return lives(drawdown_usd, risk_usd, dust_usd)


def loss_run_rate(account_r: Sequence[float], run_len: int) -> float | None:
    """§6.5b: the share of overlapping run_len-trade windows of the chronological trade sequence (across days) that are
    all full losses (account R <= -0.9). None when there are fewer trades than one window."""
    if run_len < 1:
        raise RiskConfigError("run length must be >= 1")
    full = [r <= FULL_LOSS_R for r in account_r]
    windows = len(full) - run_len + 1
    if windows < 1:
        return None
    count, in_row = 0, 0
    for i, f in enumerate(full):
        in_row = in_row + 1 if f else 0
        if i >= run_len - 1 and in_row >= run_len:
            count += 1
    return count / windows


def meets_loss_run_limit(rate: float | None, limit: float) -> bool:
    """`max_loss_run_rate` (0.15 for every N, owner 2026-10-14); no window yet = not shown to break it."""
    return rate is None or rate <= limit


RISK_UP = "risk_up"  # N goes down (e.g. 3 -> 2): typed confirmation, then waits for the sweep, next session after it
RISK_DOWN = "risk_down"  # N goes up (e.g. 3 -> 4): applies from the next trade, no confirmation
NO_CHANGE = "no_change"


def raises_risk(old_n: int, new_n: int) -> bool:
    """True when the change raises risk: a smaller N is a larger share of the drawdown (3 -> 2 = 1/3 -> 1/2)."""
    return check_divisor(new_n) < check_divisor(old_n)


def risk_change(old_n: int, new_n: int) -> str:
    """§6.1/§6.8/§9b (owner 2026-10-14): raising risk (N down) needs the typed confirmation and waits for the
    account's sweep; lowering risk (N up) applies from the next trade."""
    if raises_risk(old_n, new_n):
        return RISK_UP
    return RISK_DOWN if new_n > old_n else NO_CHANGE


def needs_typed_confirmation(old_n: int, new_n: int) -> bool:
    return risk_change(old_n, new_n) == RISK_UP


def waits_for_sweep(old_n: int, new_n: int) -> bool:
    """Raising risk waits for the sweep and takes effect at the next session after it; lowering risk does not."""
    return risk_change(old_n, new_n) == RISK_UP


def confirmation_text(account_name: str, n: int) -> str:
    """What the owner types to raise an account's risk to 1/N of the drawdown (§9b), e.g. `mffur1 1/2`."""
    return f"{account_name} 1/{check_divisor(n)}"


def confirm_risk_increase(account_name: str, new_n: int, typed: str) -> bool:
    return typed.strip() == confirmation_text(account_name, new_n)
