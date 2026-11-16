"""The synthetic prop history (`nqbot ui --synthetic` only; owner 2026-11-16): a few finished accounts so the Performance
page's "All accounts" view and its totals have something to add up. NOT RESULTS.

Each account is built from its preset (the §6.7 rules and the plan fees) and played forward session by session until
its outcome: an evaluation that fails (an end of day at or below the floor in force) or passes (at or above the target
with its minimum days), a funded account that takes payouts as its rules allow and then fails or is closed. The trades
go in the ledger as prop trades; the account's status, dates and payouts go in its account record (every finished
account is archived). They traded before the current synthetic prop accounts (the sessions just before them).
"""
from __future__ import annotations

import random
from datetime import date
from typing import Any

from nqbot.accounts.presets import apply_preset, builtin_presets

from .floor import Rule, floor_after
from .ledger import Trade
from .lifecycle import PayoutRules

# name (lowercase letters and digits, as every account name), preset, outcome, the session it starts on (an index
# into the history sessions) or the account it follows
PLAN: list[tuple[str, str, str, int | str]] = [
    ("mffur91", "mffu_rapid_eod_25k_evaluation", "failed", 0),
    ("mffur92", "mffu_rapid_eod_25k_evaluation", "passed", 4),
    ("mffurf92", "mffu_rapid_eod_25k_funded", "closed", "mffur92"),
    ("lucid91", "lucid_flex_25k_evaluation", "passed", 8),
    ("lucidf91", "lucid_flex_25k_funded", "failed", "lucid91"),
    ("mffub91", "mffu_builder_25k_evaluation", "failed", 14),
]
SEED = 31
CLOSE_AFTER = 18          # sessions a "closed" funded account trades before it is closed
FORCE_AFTER = 25          # sessions after which the outcome is pushed (a near-sure win or loss each day)
FUNDED_GOOD = 14          # a funded account that fails trades well this many sessions first
P_WIN = {"failed": 0.30, "passed": 0.62, "closed": 0.56}


def _play(name: str, acc: dict[str, Any], outcome: str, days: list[date], rnd: random.Random) -> tuple[list[Trade], dict[str, Any]]:
    rule = Rule.of(acc)
    evaluation = acc.get("phase") == "evaluation"
    target = rule.start_usd + float(acc["profit_target_usd"]) if evaluation and acc.get("profit_target_usd") else None
    min_days = int(acc.get("min_trading_days") or 0)
    po = None if evaluation else PayoutRules.of(acc)
    risk = float(acc.get("risk_usd_per_trade") or 333)
    cm, cu = float(acc.get("commission_per_mini_rt") or 0), float(acc.get("commission_per_micro_rt") or 0)
    bal = hw = rule.start_usd
    floor = rule.start_usd - rule.drawdown_usd
    locked = False
    trades: list[Trade] = []
    payouts: list[dict[str, Any]] = []
    end = days[-1]
    for k, d in enumerate(days):
        p = P_WIN[outcome] if k < FORCE_AFTER else (0.05 if outcome == "failed" else 0.95)
        if outcome == "failed" and not evaluation:      # a funded account that does well (payouts), then fails
            p = 0.62 if k < FUNDED_GOOD else 0.12
        for j in range(1 + (rnd.random() < 0.4)):
            win = rnd.random() < p
            r = round(1.2 + rnd.random() * 1.8, 2) if win else -1.0
            minute = 9 * 60 + 45 + int(rnd.random() * 300)
            comm = round(2 * cm, 2)
            net = round(r * risk - comm, 2)
            bal += net
            trades.append(Trade(id=f"H-{name}-{k}-{j}", account=name, source="prop", date=d,
                                entry_time=f"{minute // 60:02d}:{minute % 60:02d}:00", exit_time=f"{(minute + 12) // 60:02d}:{(minute + 12) % 60:02d}:00",
                                direction="long" if rnd.random() < 0.5 else "short", style="d", minis=2, micros=0, entry=21000.0,
                                exit=21000.0 + r * 10, net_usd=net, r=r, commission_usd=comm,
                                mae_usd=-round(risk * (0.3 if win else 1.0), 2), run="synthetic-history"))
        if bal <= floor:                                   # the floor in force: failed
            end = d
            break
        hw = max(hw, bal)
        floor = rule.lock_usd if (locked and rule.lock_usd is not None) else floor_after(rule, hw)
        if target is not None and bal >= target and k + 1 >= min_days:   # passed
            end = d
            break
        if po is not None:                                 # a payout as the rules allow (the amount part)
            w = (bal - po.baseline_usd) * (po.pct_of_profit or 1)
            if po.max_usd is not None:
                w = min(w, po.max_usd)
            if w >= po.need and w > 0:
                payouts.append({"date": d.isoformat(), "amount_usd": round(w, 2)})
                bal -= w
                if rule.lock_usd is not None and rule.lock_trigger in ("first_payout", "floor_or_first_payout"):
                    locked, floor = True, rule.lock_usd
        if outcome == "closed" and k + 1 >= CLOSE_AFTER:
            end = d
            break
    meta = {"status": outcome, "started": days[0].isoformat(), "ended": end.isoformat(), "payouts": payouts,
            "payouts_taken": len(payouts)}
    return trades, meta


def history(sessions: list[date]) -> tuple[list[dict[str, Any]], list[Trade]]:
    """The finished accounts (records with status, dates, payouts) and their prop trades, on these sessions."""
    by_id = {p.id: p for p in builtin_presets()}
    rnd = random.Random(SEED)
    ends: dict[str, date] = {}
    accounts, trades = [], []
    for name, preset, outcome, start in PLAN:
        if isinstance(start, str):
            first = next(i for i, d in enumerate(sessions) if d > ends[start])
        else:
            first = start
        acc = {**apply_preset(by_id[preset]), "name": name, "draft": False}
        ts, meta = _play(name, acc, outcome, sessions[first:], rnd)
        ends[name] = date.fromisoformat(meta["ended"])
        accounts.append({**acc, **meta})
        trades += ts
    return accounts, trades
