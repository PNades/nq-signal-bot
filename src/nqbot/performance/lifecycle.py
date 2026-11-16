"""A plan's lifecycle with payouts (SPEC §6.7; owner 2026-11-15): one engine for the Performance page's Monte Carlo and
Drawdown replay, written so the step 15-16 prop simulator reuses it as is.

In order, for every path until the horizon ends:
1. **Evaluation** (the plan's evaluation stage): the evaluation fee is paid when it starts. It passes at a session end
   with the balance at or above start + profit target and at least `min_trading_days` days traded in it; it fails
   when an end-of-day balance is at or below the floor in force (§6.7 floor rule).
2. **Funded** (on a pass, the next session): the activation fee is paid; a fresh account at the funded stage's start
   balance, floor and risk.
3. **Payouts** at a session end, after the floor update (§6.7 new rule types), whenever every condition holds:
   - withdrawable = balance − `payout_baseline_usd` (start + `funded_buffer_usd`), or with `payout_pct_of_profit`
     min(`payout_max_usd`, pct × that); capped at `payout_max_usd` and at what is left of `payout_cap_group_usd`;
   - withdrawable ≥ max(`payout_min_usd`, `payout_target_usd`) (`payout_target_usd` defaults to the minimum);
   - `payout_min_days` trading days in the current cycle; `payout_wait_calendar_days` calendar days since the first
     funded session (first payout) or the last payout; `payout_profit_days_min` days of at least
     `payout_profit_day_min_usd` in the cycle; after the first payout, `payout_profit_since_last_usd` of profit since it;
   - consistency (a funded stage with `consistency_pct`): the cycle's best day ≤ pct × the cycle's profit.
   The amount is the whole withdrawable (the owner's max-payout policy); the balance drops by it and a new cycle
   starts. A `first_payout` / `floor_or_first_payout` lock locks the floor at `drawdown_lock_usd` for good. After the
   first payout the `after_first_payout` cap fields apply (Builder: $500 a day). The payout that uses the last of a
   cap group's cap closes the account (no more payouts are possible): the path ends there.
4. **Keep trading funded**; on reaching a floor the funded account fails and a new evaluation starts the next session.

Money: payouts are gross (before the profit split) and the trader's share (× `profit_split_pct`, null = gross); fees are
the plan's `evaluation_fee_usd` per evaluation started and `activation_fee_usd` per pass; a fee the plan doesn't state
is unknown (NaN), never guessed; net cash = the trader's share − fees.

Simplifications (shown in the panel's notes): days come as whole strategy days in each stage's dollars (its risk per
trade, commissions and daily caps, `acctypes.day_usd`); the consistency daily cap is measured from the stage's start
(no dip); scaling tiers are not applied (the stage's own risk per trade); a payout never resets the high-water mark.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from .acctypes import Rules
from .floor import Rule

# stages of a path
EVAL, FUNDED, ENDED = 0, 1, 2


@dataclass(frozen=True)
class PayoutRules:
    """A funded stage's payout rules (§6.7 field names)."""
    baseline_usd: float
    min_usd: float
    target_usd: float
    max_usd: float | None
    pct_of_profit: float | None
    profit_since_last_usd: float | None
    min_days: int | None
    wait_calendar_days: int | None
    profit_days_min: int | None
    profit_day_min_usd: float | None
    consistency_pct: float | None
    split_pct: float | None
    cap_group_usd: float | None

    @property
    def need(self) -> float:
        """The withdrawable amount a payout needs."""
        return max(self.min_usd, self.target_usd)

    @property
    def threshold_profit(self) -> float:
        """The profit above the baseline at which the withdrawable reaches `need` (the payout line on the charts)."""
        return self.need / self.pct_of_profit if self.pct_of_profit else self.need

    @classmethod
    def of(cls, fields: dict[str, Any]) -> PayoutRules:
        start = float(fields["start_balance_usd"])
        f = lambda k: None if fields.get(k) is None else float(fields[k])  # noqa: E731
        i = lambda k: None if fields.get(k) is None else int(fields[k])  # noqa: E731
        mn = f("payout_min_usd") or 0.0
        consistency = f("consistency_pct") if fields.get("profit_cap_mode") == "consistency" else None
        return cls(baseline_usd=start + (f("funded_buffer_usd") or 0.0), min_usd=mn, target_usd=f("payout_target_usd") or mn,
                   max_usd=f("payout_max_usd"), pct_of_profit=f("payout_pct_of_profit"),
                   profit_since_last_usd=f("payout_profit_since_last_usd"), min_days=i("payout_min_days"),
                   wait_calendar_days=i("payout_wait_calendar_days"), profit_days_min=i("payout_profit_days_min"),
                   profit_day_min_usd=f("payout_profit_day_min_usd"), consistency_pct=consistency,
                   split_pct=f("profit_split_pct"), cap_group_usd=f("payout_cap_group_usd"))

    def brief(self) -> dict[str, Any]:
        return {"baseline_usd": self.baseline_usd, "need_usd": self.need, "threshold_usd": self.baseline_usd + self.threshold_profit,
                "max_usd": self.max_usd, "pct_of_profit": self.pct_of_profit, "profit_since_last_usd": self.profit_since_last_usd,
                "min_days": self.min_days, "wait_calendar_days": self.wait_calendar_days, "profit_days_min": self.profit_days_min,
                "profit_day_min_usd": self.profit_day_min_usd, "consistency_pct": self.consistency_pct,
                "split_pct": self.split_pct, "cap_group_usd": self.cap_group_usd}


@dataclass(frozen=True)
class Fees:
    evaluation_usd: float | None
    activation_usd: float | None

    def missing(self) -> list[str]:
        return [k for k, v in (("evaluation fee", self.evaluation_usd), ("activation fee", self.activation_usd)) if v is None]


@dataclass(frozen=True)
class Lifecycle:
    """What the engine needs of a plan: each stage's rules, the payout rules, the fees."""
    evaluation: Rules | None
    funded: Rules | None
    min_trading_days: int
    payout: PayoutRules | None
    fees: Fees


def _floor(rule: Rule, hw: np.ndarray, locked: np.ndarray) -> np.ndarray:
    """The floor in force after a session end (§6.7): static, or EOD-trailing with its lock."""
    if rule.type == "static":
        return np.full_like(hw, rule.start_usd - rule.drawdown_usd)
    f = hw - rule.drawdown_usd
    if rule.lock_usd is not None:
        if rule.lock_trigger in ("floor", "floor_or_first_payout"):
            f = np.minimum(f, rule.lock_usd)
        f = np.where(locked, rule.lock_usd, f)
    return f


def run(lc: Lifecycle, ev_days: np.ndarray, fu_days: np.ndarray, fa_days: np.ndarray, idx: np.ndarray,
        cal_steps: np.ndarray, *, trace: int = 0, start: dict[str, Any] | None = None,
        restart: bool = True) -> dict[str, Any]:
    """Paths x days through the lifecycle.

    `ev_days` / `fu_days` / `fa_days`: each strategy day in the evaluation / funded / after-first-payout stage's dollars;
    `idx[p, d]`: which strategy day path p trades on day d; `cal_steps[d]`: calendar days from day d-1 to day d (the
    payout clock); `trace`: how many paths (from path 0) to record day by day with their events.

    One account from where it stands today (owner 2026-11-15, the "from today" Monte Carlo): `start` sets the first
    stage's state (`balance`, `high`, `floor`, `locked`, `days_in`, `payouts` taken, `cycle_start`, `cycle_days`,
    `cycle_profit_days`, `cycle_calendar_days`; fees already paid are not counted again) and `restart=False` ends a
    path at its floor or, for an evaluation, at its pass (a passed evaluation becomes a new funded account with its own
    ID, so nothing after it belongs to this account).
    """
    n, h = idx.shape
    first_eval = lc.evaluation is not None
    first = lc.evaluation or lc.funded
    assert first is not None
    po = lc.payout
    fees = lc.fees
    ev_fee = np.nan if fees.evaluation_usd is None else fees.evaluation_usd
    act_fee = np.nan if fees.activation_usd is None else fees.activation_usd

    stage = np.full(n, EVAL if first_eval else FUNDED)
    bal = np.full(n, first.rule.start_usd)
    hw = bal.copy()
    floor = np.full(n, first.rule.start_usd - first.rule.drawdown_usd)
    locked = np.zeros(n, dtype=bool)
    days_in = np.zeros(n, dtype=int)                   # days traded in the current account
    # the current funded payout cycle
    cyc_start = bal.copy()
    cyc_days = np.zeros(n, dtype=int)
    cyc_cal = np.zeros(n)
    cyc_profit_days = np.zeros(n, dtype=int)
    cyc_best = np.full(n, -np.inf)
    acct_payouts = np.zeros(n, dtype=int)              # payouts on the current funded account
    # totals per path
    evaluations = np.full(n, 1 if first_eval else 0)
    funded_accounts = np.zeros(n, dtype=int) if first_eval else np.ones(n, dtype=int)
    passes = np.zeros(n, dtype=int)
    eval_fails = np.zeros(n, dtype=int)
    funded_fails = np.zeros(n, dtype=int)
    payouts_n = np.zeros(n, dtype=int)
    gross = np.zeros(n)
    fee_total = np.full(n, ev_fee if first_eval else 0.0)
    cap_left = np.full(n, np.inf if (po is None or po.cap_group_usd is None) else po.cap_group_usd)
    first_pass_day = np.full(n, -1)
    pass_lengths: list[np.ndarray] = []                # trading days each passing evaluation took
    if start is not None:
        bal = np.full(n, float(start["balance"]))
        hw = np.full(n, float(start.get("high", start["balance"])))
        floor = np.full(n, float(start["floor"]))
        locked = np.full(n, bool(start.get("locked", False)))
        days_in = np.full(n, int(start.get("days_in", 0)))
        acct_payouts = np.full(n, int(start.get("payouts", 0)))
        cyc_start = np.full(n, float(start.get("cycle_start", first.rule.start_usd)))
        cyc_days = np.full(n, int(start.get("cycle_days", 0)))
        cyc_profit_days = np.full(n, int(start.get("cycle_profit_days", 0)))
        cyc_cal = np.full(n, float(start.get("cycle_calendar_days", 0)))
        evaluations = np.zeros(n, dtype=int)
        funded_accounts = np.zeros(n, dtype=int)
        fee_total = np.zeros(n)
    first_payout_day = np.full(n, -1)
    end_day = np.full(n, -1)                           # the day a path's account reached its floor (restart=False)
    balances = np.zeros((n, h + 1))
    balances[:, 0] = bal
    t = min(trace, n)
    tr: dict[str, Any] = {k: np.full((t, h + 1), np.nan) for k in ("floor", "target", "payout_line")}
    tr["stage"] = np.full((t, h + 1), -1)
    if t:
        tr["floor"][:, 0] = floor[:t]
        tr["stage"][:, 0] = stage[:t]
        if first_eval and lc.evaluation and lc.evaluation.target_balance is not None:
            tr["target"][:, 0] = lc.evaluation.target_balance
        elif not first_eval and po is not None:
            tr["payout_line"][:, 0] = po.baseline_usd + po.threshold_profit
    events: list[list[dict[str, Any]]] = [[] for _ in range(t)]

    ev_rule = lc.evaluation.rule if lc.evaluation else None
    fu_rule = lc.funded.rule if lc.funded else None
    target = lc.evaluation.target_balance if lc.evaluation and lc.evaluation.target_balance is not None else None

    def start_account(mask: np.ndarray, rules: Rules) -> None:
        nonlocal bal, hw, floor, locked, days_in, cyc_start, cyc_days, cyc_cal, cyc_profit_days, cyc_best, acct_payouts
        r = rules.rule
        bal = np.where(mask, r.start_usd, bal)
        hw = np.where(mask, r.start_usd, hw)
        floor = np.where(mask, r.start_usd - r.drawdown_usd, floor)
        locked = np.where(mask, False, locked)
        days_in = np.where(mask, 0, days_in)
        cyc_start = np.where(mask, r.start_usd, cyc_start)
        cyc_days = np.where(mask, 0, cyc_days)
        cyc_cal = np.where(mask, 0.0, cyc_cal)
        cyc_profit_days = np.where(mask, 0, cyc_profit_days)
        cyc_best = np.where(mask, -np.inf, cyc_best)
        acct_payouts = np.where(mask, 0, acct_payouts)

    for d in range(h):
        live = stage != ENDED
        ev, fu = live & (stage == EVAL), live & (stage == FUNDED)
        k = idx[:, d]
        v = np.where(ev, ev_days[k], np.where(fu & (acct_payouts > 0), fa_days[k], np.where(fu, fu_days[k], 0.0)))
        bal = bal + v
        days_in = days_in + live
        # 1. the floor in force (set at the previous session end)
        hit = live & (bal <= floor)
        ok = live & ~hit
        # 2. the session end: high water and the floor; the funded cycle's counts
        hw = np.where(ok, np.maximum(hw, bal), hw)
        if ev_rule is not None:
            floor = np.where(ok & ev, _floor(ev_rule, hw, locked), floor)
        if fu_rule is not None:
            floor = np.where(ok & fu, _floor(fu_rule, hw, locked), floor)
        fok = ok & fu
        cyc_days = cyc_days + fok
        cyc_cal = cyc_cal + np.where(fok, cal_steps[d], 0.0)
        if po is not None and po.profit_day_min_usd is not None:
            cyc_profit_days = cyc_profit_days + (fok & (v >= po.profit_day_min_usd))
        cyc_best = np.where(fok, np.maximum(cyc_best, v), cyc_best)
        # 3. a pass
        passed = np.zeros(n, dtype=bool)
        if target is not None:
            passed = ok & ev & (bal >= target) & (days_in >= lc.min_trading_days)
        # 4. a payout
        paid = np.zeros(n, dtype=bool)
        amount = np.zeros(n)
        if po is not None:
            profit = bal - po.baseline_usd
            w = profit * po.pct_of_profit if po.pct_of_profit else profit
            if po.max_usd is not None:
                w = np.minimum(w, po.max_usd)
            w = np.minimum(w, cap_left)
            can = fok & (w >= po.need) & (w > 0)
            if po.min_days:
                can &= cyc_days >= po.min_days
            if po.wait_calendar_days:
                can &= cyc_cal >= po.wait_calendar_days
            if po.profit_days_min:
                can &= cyc_profit_days >= po.profit_days_min
            if po.profit_since_last_usd:
                can &= (acct_payouts == 0) | (bal - cyc_start >= po.profit_since_last_usd)
            if po.consistency_pct:
                can &= cyc_best <= po.consistency_pct * (bal - cyc_start)
            paid = can
            amount = np.where(paid, np.round(w, 2), 0.0)
        # the day's record (end of day, before the transitions below)
        balances[:, d + 1] = bal
        if t:
            tr["stage"][:, d + 1] = np.where(live[:t], stage[:t], -1)
            # the floor in force after this session end (a payout's lock included)
            shown = floor[:t]
            if fu_rule is not None and fu_rule.lock_usd is not None and fu_rule.lock_trigger in ("first_payout", "floor_or_first_payout"):
                shown = np.where(paid[:t], fu_rule.lock_usd, shown)
            tr["floor"][:, d + 1] = np.where(live[:t], shown, np.nan)
            if target is not None:
                tr["target"][:, d + 1] = np.where(ev[:t], target, np.nan)
            if po is not None:
                line = np.full(t, po.baseline_usd + po.threshold_profit)
                if po.profit_since_last_usd:   # later payouts also need that much profit since the last one
                    line = np.maximum(line, np.where(acct_payouts[:t] > 0, cyc_start[:t] + po.profit_since_last_usd, -np.inf))
                tr["payout_line"][:, d + 1] = np.where(fu[:t], line, np.nan)
            for p in range(t):
                if hit[p]:
                    events[p].append({"day": d + 1, "kind": "failed", "stage": "evaluation" if ev[p] else "funded",
                                      "balance": round(float(bal[p]), 2)})
                elif passed[p]:
                    events[p].append({"day": d + 1, "kind": "passed", "days": int(days_in[p]), "balance": round(float(bal[p]), 2)})
                elif paid[p]:
                    events[p].append({"day": d + 1, "kind": "payout", "amount": float(amount[p]), "n": int(acct_payouts[p]) + 1,
                                      "balance": round(float(bal[p]), 2)})
        # transitions (a new account after the last day is not started: no fee, not counted)
        more = d < h - 1
        # (a) a floor: the account fails; a new evaluation (or, for a funded-only plan, a new funded account) next session
        eval_fails += hit & ev
        funded_fails += hit & fu
        if hit.any() and not restart:
            end_day = np.where(hit & (end_day < 0), d + 1, end_day)
            stage = np.where(hit, ENDED, stage)
        elif hit.any():
            again = lc.evaluation or lc.funded
            assert again is not None
            start_account(hit, again)
            stage = np.where(hit, EVAL if first_eval else FUNDED, stage)
            if first_eval and more:
                evaluations += hit
                fee_total = fee_total + np.where(hit, ev_fee, 0.0)
            elif more:
                funded_accounts += hit
        # (b) a pass: funded next session (an evaluation-only plan starts a new evaluation)
        if passed.any():
            passes += passed
            pass_lengths.append(days_in[passed].copy())
            first_pass_day = np.where(passed & (first_pass_day < 0), d + 1, first_pass_day)
            if not restart:
                stage = np.where(passed, ENDED, stage)
            elif lc.funded is not None:
                start_account(passed, lc.funded)
                stage = np.where(passed, FUNDED, stage)
                if more:
                    funded_accounts += passed
                    fee_total = fee_total + np.where(passed, act_fee, 0.0)
            else:
                assert lc.evaluation is not None
                start_account(passed, lc.evaluation)
                if more:
                    evaluations += passed
                    fee_total = fee_total + np.where(passed, ev_fee, 0.0)
        # (c) a payout: the balance drops by it, a new cycle, the payout lock
        if paid.any():
            assert po is not None and fu_rule is not None
            bal = bal - amount
            gross = gross + amount
            first_payout_day = np.where(paid & (first_payout_day < 0), d + 1, first_payout_day)
            payouts_n += paid
            acct_payouts = acct_payouts + paid
            cap_left = cap_left - amount
            cyc_start = np.where(paid, bal, cyc_start)
            cyc_days = np.where(paid, 0, cyc_days)
            cyc_cal = np.where(paid, 0.0, cyc_cal)
            cyc_profit_days = np.where(paid, 0, cyc_profit_days)
            cyc_best = np.where(paid, -np.inf, cyc_best)
            if fu_rule.lock_usd is not None and fu_rule.lock_trigger in ("first_payout", "floor_or_first_payout"):
                locked = locked | paid
                floor = np.where(paid, fu_rule.lock_usd, floor)
            capped = paid & (cap_left <= 0.005)
            stage = np.where(capped, ENDED, stage)
            if t:
                for p in np.flatnonzero(capped[:t]):
                    events[p].append({"day": d + 1, "kind": "capped"})
    split = po.split_pct if (po is not None and po.split_pct is not None) else 1.0
    share = gross * split
    out: dict[str, Any] = {
        "balance": balances, "stage_end": stage, "evaluations": evaluations, "funded_accounts": funded_accounts,
        "passes": passes, "eval_fails": eval_fails, "funded_fails": funded_fails, "payouts_n": payouts_n,
        "payouts_gross": gross, "payouts_share": share, "fees": fee_total, "net_cash": share - fee_total,
        "first_pass_day": first_pass_day, "first_payout_day": first_payout_day, "end_day": end_day,
        "pass_lengths": np.concatenate(pass_lengths) if pass_lengths else np.zeros(0, dtype=int),
    }
    if t:
        out["trace"] = {**tr, "events": events}
    return out


def calendar_steps_from_dates(dates: list[Any]) -> np.ndarray:
    """Calendar days between consecutive sessions (the first session counts 1)."""
    out = [1.0]
    for a, b in zip(dates, dates[1:]):
        out.append(float((b - a).days))
    return np.asarray(out[:len(dates)])


def calendar_steps_typical(h: int) -> np.ndarray:
    """A Monte Carlo path's calendar: five sessions a week (Monday after the weekend counts 3)."""
    return np.asarray([3.0 if d % 5 == 0 and d else 1.0 for d in range(h)])


def summary(values: np.ndarray, *, digits: int = 2) -> dict[str, float | None]:
    """Median and the 10-90% range across paths (NaN = unknown, all-NaN → None)."""
    v = np.asarray(values, dtype=float)
    if v.size == 0 or np.isnan(v).all():
        return {"median": None, "lo": None, "hi": None}
    v = v[~np.isnan(v)]
    q = np.quantile(v, [0.1, 0.5, 0.9], method="lower")
    r = lambda x: round(float(x), digits)  # noqa: E731
    return {"median": r(q[1]), "lo": r(q[0]), "hi": r(q[2])}


def isnan(x: float | None) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))
