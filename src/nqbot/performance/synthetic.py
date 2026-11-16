"""The synthetic ledger (`nqbot ui --synthetic` only; never the default): seeded, repeatable trades so the Performance
page can be built and checked before steps 15-16 write real ledgers. NOT RESULTS: every view that shows it carries the
"SYNTHETIC DATA" banner.

It spans every ledger month (1-12) so the ledger guard has walk-forward and sealed months to hold back, plus a stretch
after the window standing in for real trades: some on the TradersPost demo account (source "demo"), the rest as prop
trades on the accounts (source "prop"), so the real-trades views ("from today") have balances to start from.
The backtest is one strategy ledger (owner 2026-11-15: backtest phases are about the strategy, not the accounts):
account `STRATEGY`, at `STRATEGY_RISK_USD` a trade with the MFFU commissions; the Performance page prices it in each
plan's dollars (`acctypes.priced`). The real-trade stand-ins go to the given accounts (the panel's accounts, drafts
included) with their own risk per trade and commissions. New York trading dates from config/calendar.yaml; each trade
stores its worst open loss so the intraday drawdown can be shown.

Every account's state stays one its rules allow (owner 2026-11-15, `fit_rules`): a session whose end-of-day balance
would reach an evaluation's profit target (it would have passed: a new funded account with its own ID) or the floor in
force (it would have failed) is left out for that account, so its balance, high water and floor are always a live
account's.
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

from nqbot import calendar as cal
from nqbot import months as ledger_months

from .floor import Rule, floor_after
from .history import history
from .ledger import Trade

# picked so no account breaches in the backtest order and both funded floors lock (the Monte Carlo still shows the
# risk): closest approaches $305 / $1,393 / $660 on the default accounts
SEED = 96
# after the window: AFTER_SESSIONS sessions of real-trade stand-ins, a DEMO_SHARE of them on the TradersPost demo account
# and the rest as prop trades on the accounts (so the real-trades views have something to show before step 22)
AFTER_SESSIONS = 25
DEMO_SHARE = 0.35
DEMO_ACCOUNT = "demo"
# the strategy ledger's own sizing (the page re-prices it by plan)
STRATEGY = "strategy"
# the finished prop history (performance/history.py) trades on this many sessions before the window ends
HISTORY_SESSIONS = 70
STRATEGY_RISK_USD = 333.0
STRATEGY_COMMISSIONS = (4.68, 1.90)   # MFFU, per NQ / MNQ round trip

# used when the panel has no accounts: the three presets the step-14a mock showed
DEFAULT_ACCOUNTS: list[dict[str, Any]] = [
    {"name": "mffur1", "phase": "evaluation", "start_balance_usd": 25000, "drawdown_usd": 1000, "drawdown_type": "eod_trailing",
     "drawdown_lock_usd": None, "drawdown_lock_trigger": "floor", "risk_usd_per_trade": 333, "profit_target_usd": 1500,
     "min_trading_days": 4, "commission_per_mini_rt": 4.68, "commission_per_micro_rt": 1.9},
    {"name": "mffub1", "phase": "funded", "start_balance_usd": 25000, "drawdown_usd": 1000, "drawdown_type": "eod_trailing",
     "drawdown_lock_usd": 25100, "drawdown_lock_trigger": "floor", "risk_usd_per_trade": 333, "funded_buffer_usd": 1100,
     "payout_min_usd": 250, "payout_max_usd": 1000, "payout_target_usd": 1000, "payout_min_days": 2,
     "commission_per_mini_rt": 4.68, "commission_per_micro_rt": 1.9},
    {"name": "lucid1", "phase": "funded", "start_balance_usd": 25000, "drawdown_usd": 1000, "drawdown_type": "eod_trailing",
     "drawdown_lock_usd": 25100, "drawdown_lock_trigger": "floor_or_first_payout", "risk_usd_per_trade": 333,
     "payout_pct_of_profit": 0.5, "payout_max_usd": 1000, "payout_min_usd": 500, "payout_target_usd": 1000,
     "payout_profit_days_min": 5, "payout_profit_day_min_usd": 100, "profit_split_pct": 0.9,
     "commission_per_mini_rt": 3.5, "commission_per_micro_rt": 1.0},
]


def _trading_days(first: date, last: date) -> list[date]:
    out, d = [], first
    calendar_doc = cal.load()
    while d <= last:
        if d.weekday() < 5 and cal.is_trading_day(d, calendar_doc):
            out.append(d)
        d += timedelta(days=1)
    return out


def fit_rules(trades: list[Trade], accounts: list[dict[str, Any]]) -> list[Trade]:
    """Leave out, per account, each real-trade session after which the account would have passed its evaluation (end of
    day at or above start + profit target) or failed (at or below the floor in force)."""
    by_name = {str(a.get("name")): a for a in accounts if a.get("drawdown_type")}
    drop: set[str] = set()
    days: dict[str, dict[date, list[Trade]]] = {}
    for t in trades:
        if t.source in ("demo", "prop", "live") and t.account in by_name:
            days.setdefault(t.account, {}).setdefault(t.date, []).append(t)
    for name, by_day in days.items():
        acc = by_name[name]
        rule = Rule.of(acc)
        target = (rule.start_usd + float(acc["profit_target_usd"])
                  if str(acc.get("phase") or "") == "evaluation" and acc.get("profit_target_usd") else None)
        bal = hw = rule.start_usd
        floor = rule.start_usd - rule.drawdown_usd
        for d in sorted(by_day):
            eod = bal + sum(t.net_usd for t in by_day[d])
            # at or above the target is left out even before the minimum days (`stop_at_target`: no trading past it)
            if eod <= floor or (target is not None and eod >= target):
                drop.update(t.id for t in by_day[d])
                continue
            bal = eod
            hw = max(hw, bal)
            floor = floor_after(rule, hw)
    return [t for t in trades if t.id not in drop]


def ledger(settings: dict[str, Any], accounts: list[dict[str, Any]] | None = None, seed: int = SEED) -> list[Trade]:
    # only active accounts trade now; finished ones (passed, failed, closed) have their history (performance/history.py)
    accs = [a for a in (accounts or []) if (a.get("status") or "active") == "active"] or DEFAULT_ACCOUNTS
    first, last = ledger_months.span(settings)
    days = _trading_days(first, last)
    demo = _trading_days(last + timedelta(days=1), last + timedelta(days=60))[:AFTER_SESSIONS]
    rnd = random.Random(seed)
    out: list[Trade] = []
    px = 21000.0
    n = 0
    for k, d in enumerate(days + demo):
        is_demo = k >= len(days)
        progress = k / max(1, len(days))
        count = 0 if rnd.random() < 0.28 else 1 if rnd.random() < 0.55 else 2 if rnd.random() < 0.85 else 3
        for _ in range(count):
            win = rnd.random() < 0.40 + 0.12 * progress
            r = round(1.2 + rnd.random() * 2.4, 2) if win else (0.0 if rnd.random() < 0.15 else -1.0)
            long = rnd.random() < 0.5
            px += (rnd.random() - 0.5) * 120
            stop_pts = 10 + round(rnd.random() * 16)
            entry = round(px * 4) / 4
            exit_ = round((entry + (1 if long else -1) * r * stop_pts) * 4) / 4
            minute = 9 * 60 + 45 + int(rnd.random() * 300)
            hold = 3 + int(rnd.random() * 40)
            acc = accs[int(rnd.random() * len(accs))]
            mixed = rnd.random() >= 0.7
            minis, micros = (1, 3) if mixed else (2, 0)
            if is_demo:
                cm, cu, risk = float(acc["commission_per_mini_rt"]), float(acc["commission_per_micro_rt"]), float(acc.get("risk_usd_per_trade") or 333)
            else:
                (cm, cu), risk = STRATEGY_COMMISSIONS, STRATEGY_RISK_USD
            comm = minis * cm + micros * cu
            mae = -round(risk * (0.15 + rnd.random() * 0.8 if r > 0 else 1.0), 2)
            on_demo = is_demo and rnd.random() < DEMO_SHARE
            n += 1
            out.append(Trade(
                id=f"S{n}", account=DEMO_ACCOUNT if on_demo else str(acc["name"]) if is_demo else STRATEGY,
                source="demo" if on_demo else "prop" if is_demo else "backtest",
                date=d, entry_time=f"{minute // 60:02d}:{minute % 60:02d}:00",
                exit_time=f"{(minute + hold) // 60:02d}:{(minute + hold) % 60:02d}:00",
                direction="long" if long else "short", style="d" if rnd.random() < 0.55 else "c", minis=minis, micros=micros,
                entry=entry, exit=exit_, net_usd=round(r * risk - comm, 2), r=r, commission_usd=round(comm, 2),
                mae_usd=mae, run="synthetic"))
    # the finished prop history (archived accounts, owner 2026-11-16) on the sessions just before the window ends
    return fit_rules(out, accs) + history(days[-HISTORY_SESSIONS:])[1]


def history_accounts(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """The synthetic finished accounts (status, dates, payouts), the records of the history trades in `ledger`."""
    first, last = ledger_months.span(settings)
    return history(_trading_days(first, last)[-HISTORY_SESSIONS:])[0]
