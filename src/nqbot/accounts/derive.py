"""What the Accounts page shows live as the owner types, and what it validates (SPEC §9b item 1, §6.1, §6.5b, §6.7).

Derived (for a fresh account, at its full drawdown):
- the daily profit limit (consistency mode: `consistency_pct` × `profit_target_usd`, the stage after the first payout
  when it applies; the dip rule raises it later, §13 Q-consistdip);
- risk per trade, the 1/N cap and the lives it leaves (§6.5b, `lives_dust_usd`);
- the smallest stop that still gets the full risk before the size cap binds, and the largest stop that still fits
  one micro (or one mini when micros are off), with the §6.1 all-in risk per contract, for a passive limit entry (no
  entry slip) and for a marketable / stop-limit entry (`max_entry_slip_ticks`).
Validation: errors block saving (a fraction other than 1/2, 1/3, 1/4; risk above drawdown ÷ N; missing fields; two
active accounts whose webhook variables resolve to the same URL); warnings don't (a bot daily loss cap above the
firm's DLL; a 4th MFFU or a 6th Lucid account, copies counted). The webhook check compares values from .env and never
shows one; on a machine with no `TP_WEBHOOK_*` it says "not available on this machine" (Q-dryrun13 (10)).
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import ValidationError

from nqbot import risk
from nqbot.accounts.schema import Account

# §6.1: NQ $20/pt ($5/tick), MNQ $2/pt ($0.50/tick), 4 ticks a point
NQ_USD_PER_PT, MNQ_USD_PER_PT, TICK_PTS = 20.0, 2.0, 0.25
NQ_USD_PER_TICK, MNQ_USD_PER_TICK = 5.0, 0.5
# §6.7 (owner): MFFU allows up to 3 sim-funded at once; Lucid up to 5 accounts at once (copies counted)
FIRM_ACCOUNT_LIMITS = {"MyFundedFutures": 3, "Lucid": 5}


@dataclass
class Issue:
    field: str
    level: str  # "error" | "warning" | "info"
    message: str


@dataclass
class Derived:
    risk_usd_per_trade: float | None = None
    risk_cap_usd: float | None = None
    fraction: str | None = None
    lives: int | None = None
    daily_profit_limit_usd: float | None = None
    daily_profit_limit_note: str = ""
    size_cap_minis: float | None = None
    min_stop_full_risk_pts: dict[str, float | None] = field(default_factory=dict)
    max_stop_one_contract_pts: dict[str, float | None] = field(default_factory=dict)
    one_contract: str = "micro"


def _ceil_tick(pts: float) -> float:
    return math.ceil(pts / TICK_PTS - 1e-9) * TICK_PTS


def _floor_tick(pts: float) -> float:
    return math.floor(pts / TICK_PTS + 1e-9) * TICK_PTS


def size_cap_minis(a: Account) -> float:
    """The largest position in mini-equivalents at profit 0 (§6.1 max position cap; the first scaling tier when the
    account has a scaling plan). Mini-equivalent firms: the larger of max minis and max micros ÷ ratio (2 minis or 20
    micros = 2). Firms that count every contract alike (`max_contracts_total`): minis fill the slots first, micros the
    rest at 1/ratio each (Pro 50k funded: 5 slots = 5 minis)."""
    minis, micros, total = a.max_minis, a.max_micros, a.max_contracts_total
    if a.scaling_tiers:
        t = a.scaling_tiers[0]
        minis, micros, total = t.max_minis, t.max_micros, t.max_contracts_total or total
    micros = micros if a.allow_micros else 0
    if total:
        m = min(minis, total)
        return m + min(micros, total - m) / a.micro_mini_ratio
    return float(max(minis, micros / a.micro_mini_ratio))


def derive(a: Account, settings: Mapping[str, Any]) -> Derived:
    d = Derived()
    n = a.max_risk_fraction_divisor
    d.fraction = f"1/{n}"
    d.risk_cap_usd = risk.risk_cap_usd(a.drawdown_usd, n)
    d.risk_usd_per_trade = a.risk_usd_per_trade
    r = min(a.risk_usd_per_trade, d.risk_cap_usd)
    d.lives = risk.lives(a.drawdown_usd, r, float(settings.get("lives_dust_usd", 0)))
    # daily profit limit: the stage in force (after the first payout once one is recorded)
    mode, target, pct = a.profit_cap_mode, a.profit_target_usd, a.consistency_pct
    if a.after_first_payout and a.payouts_taken >= 1:
        mode, target, pct = a.after_first_payout.profit_cap_mode, a.after_first_payout.profit_target_usd, a.after_first_payout.consistency_pct
        d.daily_profit_limit_note = "after the first payout"
    if mode == "consistency" and target and pct:
        d.daily_profit_limit_usd = risk.consistency_cap_usd(pct, target, a.start_balance_usd, None)
        d.daily_profit_limit_note = (d.daily_profit_limit_note + "; " if d.daily_profit_limit_note else "") + \
            f"{pct:.0%} of ${target:,.0f}, before any dip below the base"
    exit_ticks = float(settings.get("exit_slippage_ticks", 0))
    slip_ticks = float(settings.get("max_entry_slip_ticks", 0))
    cap = size_cap_minis(a)
    d.size_cap_minis = cap
    for entry, slip in (("passive", 0.0), ("marketable", slip_ticks)):
        # all-in risk of one mini: (stop + slip) × $20 + commission + exit slippage (§6.1)
        if cap > 0:
            per_mini = r / cap
            stop = (per_mini - a.commission_per_mini_rt - exit_ticks * NQ_USD_PER_TICK) / NQ_USD_PER_PT - slip * TICK_PTS
            d.min_stop_full_risk_pts[entry] = max(TICK_PTS, _ceil_tick(stop))
        else:
            d.min_stop_full_risk_pts[entry] = None
        if a.allow_micros and a.max_micros > 0:
            one = (r - a.commission_per_micro_rt - exit_ticks * MNQ_USD_PER_TICK) / MNQ_USD_PER_PT - slip * TICK_PTS
            d.one_contract = "micro"
        else:
            one = (r - a.commission_per_mini_rt - exit_ticks * NQ_USD_PER_TICK) / NQ_USD_PER_PT - slip * TICK_PTS
            d.one_contract = "mini"
        d.max_stop_one_contract_pts[entry] = _floor_tick(one) if one > 0 else None
    return d


def _pydantic_issues(e: ValidationError) -> list[Issue]:
    out = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err.get("loc", ())) or "account"
        msg = err.get("msg", "invalid").removeprefix("Value error, ")
        if err.get("type") == "missing":
            msg = "required"
        out.append(Issue(loc, "error", msg))
    return out


def webhook_values(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The TP_WEBHOOK_* variables this machine has (.env and the environment). Values are only compared, never shown."""
    from nqbot.secrets import REPO_ROOT, read_env_file

    src = dict(read_env_file(REPO_ROOT / ".env")) if env is None else dict(env)
    if env is None:
        src.update({k: v for k, v in os.environ.items() if k.startswith("TP_WEBHOOK_")})
    return {k: v for k, v in src.items() if k.startswith("TP_WEBHOOK_") and v}


def validate(raw: dict[str, Any], others: list[dict[str, Any]], settings: Mapping[str, Any],
             webhooks: Callable[[], dict[str, str]] = webhook_values, original_name: str | None = None) -> tuple[Account | None, list[Issue]]:
    """The account (or None when it doesn't parse) and every issue, errors first."""
    issues: list[Issue] = []
    try:
        a = Account.model_validate(raw)
    except ValidationError as e:
        return None, _pydantic_issues(e)
    except ValueError as e:  # a secret refused before field parsing
        return None, [Issue("account", "error", str(e))]
    cap = risk.risk_cap_usd(a.drawdown_usd, a.max_risk_fraction_divisor)
    if a.risk_usd_per_trade > cap + 1e-9:
        issues.append(Issue("risk_usd_per_trade", "error", f"above drawdown ÷ {a.max_risk_fraction_divisor} = ${cap:,.2f}"))
    if a.custom_risk_usd is not None and a.custom_risk_usd > cap + 1e-9:
        issues.append(Issue("custom_risk_usd", "error", f"above drawdown ÷ {a.max_risk_fraction_divisor} = ${cap:,.2f}"))
    if a.risk_profile == "custom" and a.custom_risk_usd is None:
        issues.append(Issue("custom_risk_usd", "error", "required with the Custom profile"))
    if a.profit_cap_mode == "consistency":
        if a.profit_target_usd is None:
            issues.append(Issue("profit_target_usd", "error", "required with a consistency rule"))
        if a.consistency_pct is None:
            issues.append(Issue("consistency_pct", "error", "required with a consistency rule"))
    if a.phase == "evaluation" and a.stop_at_target and a.profit_target_usd is None:
        issues.append(Issue("profit_target_usd", "error", "required when the account stops at its target"))
    if a.dll_enabled and a.firm_daily_loss_limit_usd is None:
        issues.append(Issue("firm_daily_loss_limit_usd", "error", "required when the daily loss limit is on"))
    if a.drawdown_lock_trigger in ("first_payout", "floor_or_first_payout") and a.drawdown_lock_usd is None:
        issues.append(Issue("drawdown_lock_usd", "error", "required with this lock trigger"))
    if a.drawdown_lock_usd is not None and a.drawdown_lock_usd <= a.start_balance_usd - a.drawdown_usd:
        issues.append(Issue("drawdown_lock_usd", "error", f"must be above the starting floor ${a.start_balance_usd - a.drawdown_usd:,.0f}"))
    if a.payout_cap_group and a.payout_cap_group_usd is None:
        issues.append(Issue("payout_cap_group_usd", "error", "required with a payout cap group"))
    if a.payout_max_usd is not None and a.payout_min_usd is not None and a.payout_min_usd > a.payout_max_usd:
        issues.append(Issue("payout_min_usd", "error", "above the maximum payout"))
    if a.max_minis == 0 and (a.max_micros == 0 or not a.allow_micros):
        issues.append(Issue("max_minis", "error", "the account can't hold any contract"))
    names = [o.get("name") for o in others if o.get("name") != original_name]
    if a.name in names:
        issues.append(Issue("name", "error", "another account already has this name"))
    # warnings
    if a.dll_enabled and a.bot_daily_loss_cap_usd and a.firm_daily_loss_limit_usd and a.bot_daily_loss_cap_usd > a.firm_daily_loss_limit_usd:
        issues.append(Issue("bot_daily_loss_cap_usd", "warning", f"above the firm's DLL (${a.firm_daily_loss_limit_usd:,.0f}): the firm's limit stops the day first"))
    limit = FIRM_ACCOUNT_LIMITS.get(a.firm)
    # only accounts still running count: a finished one (passed, failed, closed; owner 2026-11-16) no longer holds a place
    if limit and a.active and not a.draft and not a.demo and a.status == "active":
        same = [o for o in others if o.get("name") not in (a.name, original_name) and o.get("firm") == a.firm
                and o.get("active", True) and not o.get("draft") and not o.get("demo") and (o.get("status") or "active") == "active"]
        count = 1 + a.copied_accounts + sum(1 + int(o.get("copied_accounts") or 0) for o in same)
        if count > limit:
            issues.append(Issue("firm", "warning", f"{count} {a.firm} accounts (copies counted): the firm allows {limit} at once"))
    # webhooks: two active accounts must not resolve to the same URL (values compared, never shown)
    hooks = webhooks()
    if not hooks:
        issues.append(Issue("traderspost_webhook_env", "info", "same-URL check not available on this machine (no TP_WEBHOOK_* in its .env; it works on the laptop from step 21)"))
    elif a.active and a.traderspost_webhook_env:
        mine = hooks.get(a.traderspost_webhook_env)
        if mine is None:
            issues.append(Issue("traderspost_webhook_env", "warning", f"{a.traderspost_webhook_env} is not set in this machine's .env"))
        else:
            for o in others:
                if o.get("name") in (a.name, original_name) or not o.get("active", True) or o.get("draft"):
                    continue
                env = o.get("traderspost_webhook_env")
                if env and hooks.get(env) == mine:
                    issues.append(Issue("traderspost_webhook_env", "error", f"resolves to the same URL as account {o.get('name')}'s {env}"))
    if a.draft:
        issues.append(Issue("draft", "info", "draft: not traded or simulated until you check it and save it as checked"))
    order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda i: order[i.level])
    return a, issues


def report(raw: dict[str, Any], others: list[dict[str, Any]], settings: Mapping[str, Any],
           webhooks: Callable[[], dict[str, str]] = webhook_values, original_name: str | None = None) -> dict[str, Any]:
    """JSON for the panel: the derived values (when the account parses) and the issues."""
    a, issues = validate(raw, others, settings, webhooks, original_name)
    return {"derived": asdict(derive(a, settings)) if a else None, "issues": [asdict(i) for i in issues],
            "ok": not any(i.level == "error" for i in issues)}


# Stops (points) the raising-risk dialog shows the caps' effect at: a tight, a typical and a wide structural stop
EFFECT_STOPS_PTS = (8.0, 15.0, 25.0, 40.0)


def risk_raise_effect(a: Account, new_n: int, settings: Mapping[str, Any]) -> dict[str, Any]:
    """The raising-risk dialog (§9b): the new risk in dollars, the lives it leaves, the full losses that fail the
    account, and the effect of the caps at that fraction: for a few stops at the baseline RR (`baseline_rr`), the
    effective risk once the max position, the daily profit cap (a win must fit under it) and the DLL have limited size
    (sizes in whole contracts; the step-15 sizer packs minis and micros the same way)."""
    risk.check_divisor(new_n)
    dust = float(settings.get("lives_dust_usd", 0))
    new_risk = float(risk.default_risk_usd(a.drawdown_usd, new_n))
    lives_new = risk.lives(a.drawdown_usd, new_risk, dust)
    lives_old = risk.lives(a.drawdown_usd, min(a.risk_usd_per_trade, risk.risk_cap_usd(a.drawdown_usd, a.max_risk_fraction_divisor)), dust)
    rr = float(settings.get("baseline_rr", 2.5))
    exit_ticks = float(settings.get("exit_slippage_ticks", 0))
    cap_minis = size_cap_minis(a)
    d = derive(a, settings)
    daily_cap = d.daily_profit_limit_usd
    dll = a.bot_daily_loss_cap_usd or (a.firm_daily_loss_limit_usd if a.dll_enabled else None)
    rows = []
    for stop in EFFECT_STOPS_PTS:
        per_micro = stop * MNQ_USD_PER_PT + a.commission_per_micro_rt + exit_ticks * MNQ_USD_PER_TICK
        micros_cap = int(round(cap_minis * a.micro_mini_ratio))
        size_cap_risk = micros_cap * per_micro if a.allow_micros else int(cap_minis) * (stop * NQ_USD_PER_PT + a.commission_per_mini_rt + exit_ticks * NQ_USD_PER_TICK)
        limits = {"risk at 1/%d" % new_n: new_risk, "max position": size_cap_risk}
        if daily_cap:
            limits["daily profit cap"] = daily_cap / rr  # a win of rr × risk must stay under the cap
        if dll:
            limits["daily loss limit"] = dll
        binding = min(limits, key=lambda k: limits[k])
        rows.append({"stop_pts": stop, "effective_risk_usd": round(limits[binding], 2), "limited_by": binding})
    return {"new_divisor": new_n, "new_risk_usd": new_risk, "lives_new": lives_new, "lives_old": lives_old,
            "losses_that_fail": lives_new, "rr": rr, "rows": rows,
            "confirm_text": risk.confirmation_text(a.name, new_n)}
