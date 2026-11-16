"""The "from today" Monte Carlo for one actual account (SPEC §9b item 6; owner 2026-11-15, rework 4).

It covers only that account's own stage, from where it stands today (balance, high water, floor; payouts taken; days
traded): a passed evaluation becomes a new funded account with its own ID, so a path never changes stage or starts a
new evaluation. The same lifecycle engine as the strategy view (`lifecycle.run` with `start=` and `restart=False`):
- **Evaluation:** each path runs until its profit target (passed, with the minimum trading days) or its floor (failed),
  or the horizon. Headline: the chance of passing before failing, the median days to each.
- **Funded:** each path runs with payouts whenever the account's rules allow (§6.7: buffer, minimum / maximum, % of
  profit, days in the cycle, profit days, the calendar clock, profit since the last, consistency, the floor lock after
  a payout) until its floor or the horizon. Headline: the chance of reaching the next payout before the floor, the
  median days to it, and the payouts over the horizon.
Days are the strategy's guarded backtest days in this account's dollars (its risk per trade, commissions, daily caps).

Not known before the engine records it (step 22): where a funded account's current payout cycle started after a
payout. With a payout taken, the cycle starts at the account's `last_payout_date` (or the latest recorded payout's
date) when it has one, else it is assumed to start today (its days, profit days and clock at 0); `start["cycle_from"]`
says which, and the panel shows it in a note (owner 2026-11-16).
"""
from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np

from . import lifecycle as L
from .acctypes import Rules, day_series, of
from .ledger import Trade

N_PATHS = 1000


def stage_of(account: dict[str, Any]) -> str:
    return "evaluation" if str(account.get("phase") or "") == "evaluation" else "funded"


def lifecycle_for(account: dict[str, Any]) -> tuple[L.Lifecycle, Rules, Rules]:
    """The account's own stage as a one-stage lifecycle, and the rules for its days (before / after a first payout)."""
    name = str(account.get("name"))
    if stage_of(account) == "evaluation":
        r = of(account, id=name, label=name, stage="evaluation")
        lc = L.Lifecycle(evaluation=r, funded=None, min_trading_days=int(account.get("min_trading_days") or 0),
                         payout=None, fees=L.Fees(0.0, 0.0))
        return lc, r, r
    r = of(account, id=name, label=name, stage="funded")
    afp = account.get("after_first_payout")
    after = of({**account, **afp}, id=f"{name}:after", label=name, stage="funded") if afp else r
    lc = L.Lifecycle(evaluation=None, funded=r, min_trading_days=0, payout=L.PayoutRules.of(account), fees=L.Fees(0.0, 0.0))
    return lc, r, after


def _last_payout(account: dict[str, Any]) -> date | None:
    got = account.get("last_payout_date")
    dates = [got] + [p.get("date") for p in account.get("payouts") or [] if isinstance(p, dict)]
    parsed = [d if isinstance(d, date) else date.fromisoformat(str(d)) for d in dates if d]
    return max(parsed) if parsed else None


def start_state(account: dict[str, Any], state: dict[str, Any], real_days: list[tuple[date, float]]) -> dict[str, Any]:
    """Where the account stands today, as the engine's start (the funded payout cycle: see the module note).
    `real_days`: the account's own sessions so far (date, P&L), oldest first."""
    lc, r, _ = lifecycle_for(account)
    rule = r.rule
    payouts = int(account.get("payouts_taken") or 0)
    last = _last_payout(account) if lc.payout else None
    if last is not None:
        payouts = max(payouts, 1)
    locked = bool(rule.lock_usd is not None and rule.lock_trigger in ("first_payout", "floor_or_first_payout")
                  and (payouts > 0 or state["floor_usd"] >= rule.lock_usd - 0.005))
    po = lc.payout
    today = real_days[-1][0] if real_days else None
    if payouts == 0:
        cycle, cycle_from, start_bal = real_days, "account start", rule.start_usd
    elif last is not None:
        cycle = [(d, v) for d, v in real_days if d > last]
        cycle_from = f"last payout {last.isoformat()}"
        start_bal = rule.start_usd + sum(v for d, v in real_days if d <= last)
    else:
        cycle, cycle_from, start_bal = [], "today (assumed)", state["balance_usd"]
    profit_days = sum(1 for _, v in cycle if po and po.profit_day_min_usd is not None and v >= po.profit_day_min_usd)
    if payouts == 0:
        cal = round(len(cycle) * 7 / 5)
    elif last is not None and today is not None:
        cal = max(0, (today - last).days)
    else:
        cal = 0
    return {"balance": state["balance_usd"], "high": state["high_water_usd"], "floor": state["floor_usd"],
            "locked": locked, "days_in": len(real_days), "payouts": payouts, "cycle_start": start_bal,
            "cycle_days": len(cycle), "cycle_profit_days": profit_days, "cycle_calendar_days": cal, "cycle_from": cycle_from}


def _numbers(sim: dict[str, Any], stage: str) -> dict[str, Any]:
    passed = sim["first_pass_day"] >= 0
    failed = sim["end_day"] >= 0
    paid = sim["first_payout_day"] >= 0
    s = lambda a: L.summary(a, digits=0) if a.size else None  # noqa: E731
    base = {"p_fail": round(float(failed.mean()), 4), "days_to_fail": s(sim["end_day"][failed])}
    if stage == "evaluation":
        return {**base, "stage": stage, "p_pass": round(float(passed.mean()), 4), "days_to_pass": s(sim["first_pass_day"][passed]),
                "p_open": round(float((~passed & ~failed).mean()), 4)}
    return {**base, "stage": stage, "p_payout": round(float(paid.mean()), 4), "days_to_payout": s(sim["first_payout_day"][paid]),
            "payouts_n": L.summary(sim["payouts_n"], digits=0), "payouts_gross": L.summary(sim["payouts_gross"]),
            "payouts_share": L.summary(sim["payouts_share"])}


def run(trades_days: list[list[Trade]], account: dict[str, Any], state: dict[str, Any], real_days: list[tuple[date, float]], *,
        horizon: int, n: int = N_PATHS, seed: int = 7) -> dict[str, Any]:
    m = len(trades_days)
    stage = stage_of(account)
    lc, r, after = lifecycle_for(account)
    if m == 0:
        return {"n": n, "horizon": horizon, "days": 0, "stage": stage, "numbers": None, "order_luck": None, "bands": []}
    days = np.asarray(day_series(trades_days, r), dtype=float)
    fa = np.asarray(day_series(trades_days, after), dtype=float)
    ev, fu = (days, days) if stage == "evaluation" else (np.zeros(m), days)
    st = start_state(account, state, real_days)
    rng = np.random.default_rng(seed)
    boot = rng.integers(0, m, size=(n, horizon))
    perm = np.stack([np.concatenate([rng.permutation(m) for _ in range(-(-horizon // m))])[:horizon] for _ in range(n)])
    cal = L.calendar_steps_typical(horizon)
    a = L.run(lc, ev, fu, fa, boot, cal, start=st, restart=False)
    b = L.run(lc, ev, fu, fa, perm, cal, start=st, restart=False)
    base = L.run(lc, ev, fu, fa, (np.arange(horizon) % m)[None, :], cal, start=st, restart=False, trace=1)
    # a path that ended keeps its last balance (the fan doesn't drop to zero)
    qs = np.quantile(a["balance"], [0.05, 0.25, 0.5, 0.75, 0.95], axis=0, method="lower")
    bands = [{"p5": round(float(qs[0, i]), 2), "p25": round(float(qs[1, i]), 2), "p50": round(float(qs[2, i]), 2),
              "p75": round(float(qs[3, i]), 2), "p95": round(float(qs[4, i]), 2)} for i in range(horizon + 1)]
    tr = base["trace"]
    nn = lambda arr: [None if np.isnan(x) else round(float(x), 2) for x in arr]  # noqa: E731
    return {"n": n, "horizon": horizon, "days": m, "stage": stage, "start": st, "numbers": _numbers(a, stage),
            "order_luck": _numbers(b, stage), "bands": bands,
            "split_pct": lc.payout.split_pct if lc.payout else None,
            "base": {"balance": [round(float(x), 2) for x in base["balance"][0]], "floor": nn(tr["floor"][0]),
                     "target": nn(tr["target"][0]), "payout_line": nn(tr["payout_line"][0]),
                     "stage": ["evaluation" if s == L.EVAL else "funded" if s == L.FUNDED else "ended" for s in tr["stage"][0]],
                     "events": tr["events"][0]},
            "rules": r.brief(), "payout": lc.payout.brief() if lc.payout else None, "min_trading_days": lc.min_trading_days}
