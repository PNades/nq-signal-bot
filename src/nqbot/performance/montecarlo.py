"""The Monte Carlo (SPEC §9b item 6; owner 2026-11-15, §13 Q-perf-mc): 1,000 paths built from whole trading days
(never single trades: same-day trades stay together), each run through a drawdown floor (§6.7, `floor.py`).

Headline: method (a), whole days drawn with replacement (the fan and the chance of reaching the floor). Secondary,
"order luck": method (b), the same days reordered (every path ends at the same total; only the order changes).
Paths may start from an account's current state (balance, high water, floor in force): "from today".

A path stops where the account's run ends: on reaching the floor, or, for an evaluation with a profit target, on
reaching the target (the evaluation passes); its balance stays there to the end, so the fan's last column is the spread
of results and its median is "the median result at the end". Vectorised with numpy (paths x days); `floor_paths` is the
§6.7 rule of `floor.floor_path` on every path at once (checked against it in tests/test_perf_montecarlo.py). Payouts
are not modelled.
"""
from __future__ import annotations

from typing import Any, Literal, Sequence

import numpy as np

from .floor import Rule

N_PATHS = 1000  # §9b item 6


def floor_paths(rule: Rule, bal: np.ndarray, high0: float, floor0: float) -> tuple[np.ndarray, np.ndarray]:
    """For balances `bal` (paths x days): the floor set at each session end, and the first breach day (-1 = none).
    `high0`, `floor0`: the high water and the floor in force before the first day."""
    hw = np.maximum(np.maximum.accumulate(bal, axis=1), high0)
    if rule.type == "static":
        floor = np.full_like(bal, rule.start_usd - rule.drawdown_usd)
    else:
        floor = hw - rule.drawdown_usd
        if rule.lock_usd is not None and rule.lock_trigger in ("floor", "floor_or_first_payout"):
            floor = np.minimum(floor, rule.lock_usd)
    # a floor already locked before today stays there
    if rule.lock_usd is not None and floor0 >= rule.lock_usd:
        floor = np.full_like(bal, floor0)
    prev = np.concatenate([np.full((bal.shape[0], 1), floor0), floor[:, :-1]], axis=1)
    hit = bal <= prev
    first = np.where(hit.any(axis=1), hit.argmax(axis=1), -1)
    return floor, first


def _summary(rule: Rule, draws: np.ndarray, start: float, high0: float, floor0: float, target: float | None) -> dict[str, Any]:
    bal = start + np.cumsum(draws, axis=1)
    n, h = bal.shape
    _, breach = floor_paths(rule, bal, high0, floor0)
    passed = np.full(n, -1)
    if target is not None:
        hit = bal >= target
        passed = np.where(hit.any(axis=1), hit.argmax(axis=1), -1)
    big = h + 1
    b, t = np.where(breach >= 0, breach, big), np.where(passed >= 0, passed, big)
    stop = np.minimum(b, t)
    breached, reached = (b < t), (t < b)
    # each path's balance frozen from the day its run ends
    idx = np.minimum(np.arange(h)[None, :], np.where(stop < big, stop, h - 1)[:, None])
    frozen = np.take_along_axis(bal, idx, axis=1)
    bd, td = b[breached] + 1, t[reached] + 1
    return {"bal": frozen, "p_breach": round(float(breached.mean()), 4),
            "median_breach_day": int(np.median(bd)) if bd.size else None,
            "p_target": None if target is None else round(float(reached.mean()), 4),
            "median_target_day": int(np.median(td)) if td.size else None}


def run(day_pnl: Sequence[float], rule: Rule, *, n: int = N_PATHS, seed: int = 7, horizon: int | None = None,
        start: float | None = None, high0: float | None = None, floor0: float | None = None,
        target_balance: float | None = None,
        method: Literal["bootstrap", "permutation"] = "bootstrap") -> dict[str, Any]:
    """`day_pnl`: the strategy's days in this rule's dollars, in backtest order. `horizon`: trading days per path
    (default: as many as there are days). `start` / `high0` / `floor0`: today's balance, high water and floor (default:
    the rule's start). `target_balance`: an evaluation's pass balance (start + profit target); a path that gets there
    stops. Both methods are always computed; `method` picks which one the bands and paths show."""
    days = np.asarray(day_pnl, dtype=float)
    m = len(days)
    h = horizon or m
    start = rule.start_usd if start is None else start
    high0 = max(start, rule.start_usd) if high0 is None else high0
    floor0 = (rule.start_usd - rule.drawdown_usd) if floor0 is None else floor0
    if m == 0 or h == 0:
        return {"n": n, "days": 0, "p_breach": None, "median_breach_day": None, "p_target": None, "median_target_day": None,
                "median_end": None, "target_balance": target_balance, "order_luck": None, "bands": [],
                "worst": [], "best": [], "base": [], "base_floor": [], "method": method, "start_usd": start, "floor0": floor0}
    rng = np.random.default_rng(seed)
    boot = days[rng.integers(0, m, size=(n, h))]
    perm = np.stack([rng.permutation(days)[:h] if h <= m else days[rng.integers(0, m, size=h)] for _ in range(n)])
    a = _summary(rule, boot, start, high0, floor0, target_balance)
    b = _summary(rule, perm, start, high0, floor0, target_balance)
    shown = a if method == "bootstrap" else b
    profit = np.concatenate([np.zeros((n, 1)), shown["bal"] - start], axis=1)
    qs = np.quantile(profit, [0.05, 0.25, 0.5, 0.75, 0.95], axis=0, method="lower")
    bands = [{"p5": round(float(qs[0, i]), 2), "p25": round(float(qs[1, i]), 2), "p50": round(float(qs[2, i]), 2),
              "p75": round(float(qs[3, i]), 2), "p95": round(float(qs[4, i]), 2)} for i in range(h + 1)]
    dd = (profit - np.maximum.accumulate(profit, axis=1)).min(axis=1)
    order = np.argsort(dd, kind="stable")
    base_days = days[:h] if h <= m else np.concatenate([days, days[: h - m]])
    # the backtest order, stopped where its run ends like every path; its floor follows the stopped balances (it
    # stays where it was once the run has ended)
    base_bal = _summary(rule, base_days[None, :], start, high0, floor0, target_balance)["bal"][0]
    base_floor, _ = floor_paths(rule, base_bal[None, :], high0, floor0)
    r2 = lambda v: [round(float(x), 2) for x in v]  # noqa: E731
    return {
        "n": n, "days": h, "method": method, "start_usd": start, "floor0": floor0,
        "p_breach": a["p_breach"], "median_breach_day": a["median_breach_day"],
        "p_target": a["p_target"], "median_target_day": a["median_target_day"], "target_balance": target_balance,
        "median_end": bands[-1]["p50"],
        "order_luck": {"p_breach": b["p_breach"], "median_breach_day": b["median_breach_day"], "p_target": b["p_target"]},
        "bands": bands, "worst": r2(profit[order[0]]), "best": r2(profit[order[-1]]),
        "base": [0.0] + r2(base_bal - start),
        "base_floor": r2(np.concatenate([[floor0], base_floor[0]]) - start),
    }
