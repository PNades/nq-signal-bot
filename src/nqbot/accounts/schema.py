"""The account schema (SPEC §6.7 fields, §9b item 1; owner 2026-11-15: the panel file never holds a secret).

One pydantic model checks every account the Accounts page saves, the panel store loads, and (from step 15) the prop
simulator and the live engine read. Field names and meanings are §6.7's; the panel-only additions are `draft` (a
pre-filled account the owner hasn't checked yet: never traded or simulated), `preset` (the preset stage it was filled
from), `payouts` (the recorded payouts, for the payout clock and the cap group), `first_funded_trade_date` and
`pending_risk` (a raise of risk waiting for the account's sweep, §6.1, §9b).

No secrets: only the `.env` variable name of the account's TradersPost webhook is stored, never a URL; every text
value is checked for URLs and token-like strings (a copied or lost SSD can't leak anything).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nqbot import risk

NAME_RE = re.compile(r"^[a-z0-9]+$")
WEBHOOK_ENV_RE = re.compile(r"^TP_WEBHOOK_[A-Z0-9_]+$")
# anything that looks like a secret: a URL, or a long run of key-like characters (API keys, tokens, webhook ids)
_URL_RE = re.compile(r"(?i)\b(?:https?|wss?|ftp)://|www\.|\.(?:com|io|net|dev)/")
# a key-like run: 24+ letters/digits with no separator, or a UUID (webhook ids); ids like mffu_rapid_eod_25k pass
_TOKEN_RE = re.compile(r"[A-Za-z0-9]{24,}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-")


class SecretRefused(ValueError):
    pass


def refuse_secrets(value: Any, where: str = "") -> None:
    """Raise if any text inside `value` looks like a URL, a key or a login."""
    if isinstance(value, str):
        if _URL_RE.search(value) or _TOKEN_RE.search(value) or "@" in value and "." in value.split("@")[-1]:
            raise SecretRefused(f"{where or 'a field'} looks like a URL, key or login; the panel file never holds "
                                "secrets (put it in .env and store only the variable name)")
    elif isinstance(value, dict):
        for k, v in value.items():
            refuse_secrets(v, f"{where}.{k}" if where else str(k))
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            refuse_secrets(v, f"{where}[{i}]")


class ScalingTier(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_profit_usd: float | None = None  # null = any profit, losses included
    max_minis: int = Field(ge=0)
    max_micros: int = Field(ge=0)
    max_contracts_total: int | None = Field(default=None, ge=1)


class CapStage(BaseModel):
    """`after_first_payout`: the cap fields that replace the current ones after the first payout (§6.7)."""
    model_config = ConfigDict(extra="forbid")
    profit_cap_mode: Literal["consistency", "none"] = "none"
    profit_target_usd: float | None = Field(default=None, gt=0)
    consistency_pct: float | None = Field(default=None, gt=0, le=1)


class Payout(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    amount_usd: float = Field(gt=0)
    accounts: int = Field(default=1, ge=1)  # accounts it was taken on (1 + copied accounts by default, §6.7 rule 3)


class PendingRisk(BaseModel):
    """A raise of risk (N down) the owner confirmed: it waits for the account's sweep and takes effect at the next
    session after it (§6.1, §9b)."""
    model_config = ConfigDict(extra="forbid")
    max_risk_fraction_divisor: int
    risk_usd_per_trade: float = Field(gt=0)
    confirmed_utc: str


class Account(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=False)

    # ── Account ──
    name: str
    active: bool = True
    draft: bool = False
    preset: str | None = None
    firm: str
    phase: Literal["evaluation", "funded"]
    demo: bool = False
    traderspost_webhook_env: str | None = None
    sim_fills: bool = True
    # ── Targets & caps ──
    profit_cap_mode: Literal["consistency", "none"] = "none"
    profit_target_usd: float | None = Field(default=None, gt=0)
    stop_at_target: bool = False
    consistency_pct: float | None = Field(default=None, gt=0, le=1)
    payout_target_usd: float | None = Field(default=None, gt=0)
    funded_buffer_usd: float | None = Field(default=None, ge=0)
    payout_min_usd: float | None = Field(default=None, gt=0)
    payout_profit_since_last_usd: float | None = Field(default=None, ge=0)
    payout_max_usd: float | None = Field(default=None, gt=0)
    payout_pct_of_profit: float | None = Field(default=None, gt=0, le=1)
    payout_profit_days_min: int | None = Field(default=None, ge=1)
    payout_profit_day_min_usd: float | None = Field(default=None, ge=0)
    payouts_taken: int = Field(default=0, ge=0)
    payouts: list[Payout] = Field(default_factory=list)
    first_funded_trade_date: date | None = None
    # plan fees (owner 2026-11-16, §6.7): the evaluation's price, paid once per evaluation started (one-time billing only),
    # and the activation fee on a pass; reports and the plan lifecycle only, never a trading input
    evaluation_fee_usd: float | None = Field(default=None, ge=0)
    evaluation_fee_billing: Literal["one_time"] | None = None
    activation_fee_usd: float | None = Field(default=None, ge=0)
    # the last payout's date (owner 2026-11-16): the funded payout cycle starts there until the engine records payouts
    # (step 22); empty = the "from today" Monte Carlo assumes the cycle starts today once a payout was taken
    last_payout_date: date | None = None
    after_first_payout: CapStage | None = None
    payout_wait_calendar_days: int | None = Field(default=None, ge=1)
    payout_clock_hold: Literal["off", "pause", "protect"] = "off"
    profit_split_pct: float | None = Field(default=None, gt=0, le=1)
    firm_balance_from_zero: bool = False
    flip_hold_max_sec: float | None = Field(default=None, gt=0)
    flip_profit_share_max: float | None = Field(default=None, gt=0, le=1)
    flip_profit_share_warn: float | None = Field(default=None, gt=0, le=1)
    payout_cap_group: str | None = None
    payout_cap_group_usd: float | None = Field(default=None, gt=0)
    copied_accounts: int = Field(default=0, ge=0)
    auto_protect_within_usd: float | None = Field(default=None, gt=0)
    # ── Drawdown & losses ──
    start_balance_usd: float = Field(gt=0)
    drawdown_usd: float = Field(gt=0)
    drawdown_type: Literal["static", "eod_trailing"]
    drawdown_lock_usd: float | None = Field(default=None, gt=0)
    drawdown_lock_trigger: Literal["floor", "first_payout", "floor_or_first_payout"] = "floor"
    dll_enabled: bool = False
    firm_daily_loss_limit_usd: float | None = Field(default=None, gt=0)
    bot_daily_loss_cap_usd: float | None = Field(default=None, gt=0)
    max_losses_per_day: int | None = Field(default=None, ge=1)
    # ── Firm rules ──
    min_trading_days: int | None = Field(default=None, ge=0)
    max_inactive_days: int | None = Field(default=None, ge=1)
    payout_min_days: int | None = Field(default=None, ge=1)
    commission_per_mini_rt: float = Field(ge=0)
    commission_per_micro_rt: float = Field(ge=0)
    # ── Position limits ──
    max_trades_per_day: int | None = Field(default=None, ge=1)
    max_minis: int = Field(ge=0)
    max_micros: int = Field(ge=0)
    micro_mini_ratio: int = Field(default=10, ge=1)
    max_contracts_total: int | None = Field(default=None, ge=1)
    allow_micros: bool = True
    allow_mixed: bool = True
    scaling_tiers: list[ScalingTier] | None = None
    scaling_tier_update: Literal["intraday", "eod"] = "intraday"
    # ── Sizing & trade rules ──
    max_risk_fraction_divisor: int = risk.DEFAULT_DIVISOR
    risk_usd_per_trade: float = Field(gt=0)
    custom_risk_usd: float | None = Field(default=None, gt=0)
    pending_risk: PendingRisk | None = None
    flat_by: str = "15:59"
    breakeven_enabled: bool = True
    lives_mode: bool = True
    last_life_top_pct: Literal["auto"] | float = "auto"
    max_loss_run_rate: float = Field(default=0.15, gt=0, le=1)
    # ── Strategy (Settings page, §6.8) ──
    risk_profile: Literal["aggressive", "normal", "conservative", "protect", "custom"] = "normal"
    min_ev_r: float = 0.20
    min_target_prob: float = Field(default=0.40, ge=0, le=1)
    lambda_risk_penalty: float = Field(default=0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _no_secrets(cls, data: Any) -> Any:
        refuse_secrets({k: v for k, v in (data or {}).items() if k != "traderspost_webhook_env"} if isinstance(data, dict) else data)
        return data

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME_RE.match(v or ""):
            raise ValueError("lowercase letters and digits only (e.g. mffur1)")
        return v

    @field_validator("traderspost_webhook_env")
    @classmethod
    def _webhook_env(cls, v: str | None) -> str | None:
        if v in (None, ""):
            return None
        if not WEBHOOK_ENV_RE.match(v):
            raise ValueError("the .env variable NAME of the webhook, e.g. TP_WEBHOOK_MFFUR1 (never the URL itself)")
        return v

    @field_validator("max_risk_fraction_divisor")
    @classmethod
    def _divisor(cls, v: int) -> int:
        try:
            return risk.check_divisor(v)
        except risk.RiskConfigError as e:
            raise ValueError("risk fraction must be 1/2, 1/3 or 1/4 of the drawdown") from e

    @field_validator("flat_by")
    @classmethod
    def _flat_by(cls, v: str) -> str:
        if not re.match(r"^(?:[01]\d|2[0-3]):[0-5]\d$", v):
            raise ValueError("HH:MM, 24-hour ET")
        return v

    @field_validator("last_life_top_pct")
    @classmethod
    def _last_life(cls, v: Any) -> Any:
        if v != "auto" and not (isinstance(v, (int, float)) and 0 < float(v) <= 1):
            raise ValueError("auto, or a share between 0 and 1 (e.g. 0.20)")
        return v


def account_dict(a: Account) -> dict[str, Any]:
    """The account as stored (YAML-friendly: dates as ISO text, nothing left as a model)."""
    return a.model_dump(mode="json")
