"""Account types and accounts as rules for the Drawdown and Monte Carlo tabs (SPEC §9b item 6, §6.7; owner 2026-11-15).

Both tabs draw days from the strategy's backtest or simulator ledger (the guarded development days), never from one
account's own few trades. A type (every preset: MFFU Rapid, MFFU Builder, Pro, Lucid Flex at each stage, and the
owner's saved presets) or an account turns each strategy day into dollars under its own rules:
- risk per trade: its `risk_usd_per_trade`, capped at drawdown / N (§6.1);
- each trade: R x risk, minus the type's commissions for the trade's contracts (the ledger's minis / micros);
- daily caps: once the day's P&L reaches the daily profit limit (the consistency cap, §6.7) or falls to the daily loss
  cap (the bot's cap, else the firm's daily loss limit), no further trade that day (the trade that crosses it counts
  in full);
- the drawdown floor (`floor.Rule`). Payouts are not modelled (a payout-triggered lock never fires here);
- an evaluation with a profit target ends when the balance reaches start + target (passed).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable

from nqbot import risk

from .floor import Rule
from .ledger import Trade


@dataclass(frozen=True)
class Rules:
    id: str
    label: str
    stage: str
    kind: str
    rule: Rule
    risk_usd: float
    commission_mini: float
    commission_micro: float
    daily_profit_cap: float | None
    daily_loss_cap: float | None
    # an evaluation's pass balance (start + profit target): a Monte Carlo path that gets there stops (passed)
    target_balance: float | None = None

    def brief(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "stage": self.stage, "kind": self.kind, "rule": self.rule.text(),
                "risk_usd": self.risk_usd, "daily_profit_cap": self.daily_profit_cap, "daily_loss_cap": self.daily_loss_cap,
                "start_usd": self.rule.start_usd, "drawdown_usd": self.rule.drawdown_usd, "target_balance": self.target_balance}


def of(fields: dict[str, Any], *, id: str, label: str, stage: str = "", kind: str = "") -> Rules:
    """Rules from a preset's fields or an account (the same §6.7 field names)."""
    dd = float(fields["drawdown_usd"])
    n = risk.check_divisor(fields.get("max_risk_fraction_divisor") or risk.DEFAULT_DIVISOR)
    want = float(fields.get("custom_risk_usd") or fields.get("risk_usd_per_trade") or risk.default_risk_usd(dd, n))
    cap = None
    if fields.get("profit_cap_mode") == "consistency" and fields.get("profit_target_usd") and fields.get("consistency_pct"):
        cap = risk.consistency_cap_usd(float(fields["consistency_pct"]), float(fields["profit_target_usd"]),
                                       float(fields["start_balance_usd"]), None)
    loss = fields.get("bot_daily_loss_cap_usd") or fields.get("firm_daily_loss_limit_usd")
    evaluation = (stage or str(fields.get("phase") or "")) == "evaluation"
    target = (float(fields["start_balance_usd"]) + float(fields["profit_target_usd"])
              if evaluation and fields.get("profit_target_usd") else None)
    return Rules(id=id, label=label, stage=stage, kind=kind, rule=Rule.of(fields),
                 risk_usd=round(min(want, risk.risk_cap_usd(dd, n)), 2),
                 commission_mini=float(fields.get("commission_per_mini_rt") or 0),
                 commission_micro=float(fields.get("commission_per_micro_rt") or 0),
                 daily_profit_cap=None if cap is None else float(cap), daily_loss_cap=None if loss is None else float(loss),
                 target_balance=target)


def strategy_days(trades: Iterable[Trade]) -> list[list[Trade]]:
    """The strategy's days: every guarded backtest trade, grouped by New York trading date, in time order."""
    by: dict[Any, list[Trade]] = {}
    for t in sorted(trades, key=lambda t: (t.date, t.entry_time, t.id)):
        if t.source == "backtest":
            by.setdefault(t.date, []).append(t)
    return [by[d] for d in sorted(by)]


def day_usd(day: list[Trade], r: Rules) -> float:
    """One strategy day in this type's dollars, its daily caps applied."""
    total = 0.0
    for t in day:
        total += t.r * r.risk_usd - (t.minis * r.commission_mini + t.micros * r.commission_micro)
        if r.daily_profit_cap is not None and total >= r.daily_profit_cap:
            break
        if r.daily_loss_cap is not None and total <= -r.daily_loss_cap:
            break
    return round(total, 2)


def day_series(days: list[list[Trade]], r: Rules) -> list[float]:
    return [day_usd(d, r) for d in days]


def priced(trades: Iterable[Trade], r: Rules) -> list[Trade]:
    """The strategy's backtest trades in this plan's dollars (owner 2026-11-15): R x the plan's risk per trade (capped at
    drawdown / N), minus the plan's commissions for the trade's contracts; the worst open loss scaled by the same risk.
    R doesn't change by plan; real trades (demo, prop, live) keep their own dollars."""
    out = []
    for t in trades:
        if t.source != "backtest":
            out.append(t)
            continue
        comm = round(t.minis * r.commission_mini + t.micros * r.commission_micro, 2)
        own = (t.net_usd + t.commission_usd) / t.r if t.r else None
        mae = None if t.mae_usd is None else round(t.mae_usd * (r.risk_usd / own) if own else t.mae_usd, 2)
        out.append(replace(t, net_usd=round(t.r * r.risk_usd - comm, 2), commission_usd=comm, mae_usd=mae))
    return out
