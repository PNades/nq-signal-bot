"""Plans and their lifecycle with payouts (SPEC §9b item 6, §6.7; owner 2026-11-15): evaluation -> on the target,
funded -> payouts when the plan's rules allow -> on a floor, failed and a new evaluation, until the horizon ends. Hand
cases on one-trade days (R x $500 risk = the day's $, no commissions)."""
from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from nqbot.performance import lifecycle as LC
from nqbot.performance import plans as L
from nqbot.performance.ledger import Trade

EVAL = {"start_balance_usd": 25000, "drawdown_usd": 1000, "drawdown_type": "eod_trailing", "drawdown_lock_usd": None,
        "drawdown_lock_trigger": "floor", "risk_usd_per_trade": 500, "max_risk_fraction_divisor": 2,
        "profit_target_usd": 1500, "commission_per_mini_rt": 0, "commission_per_micro_rt": 0}
# Rapid-like funded: $1,100 buffer, $500 minimum, no maximum, $250 profit since the last payout; the floor locks at $25,100
FUNDED = {**EVAL, "profit_target_usd": None, "drawdown_lock_usd": 25100, "funded_buffer_usd": 1100, "payout_min_usd": 500,
          "payout_profit_since_last_usd": 250}


def plan(eval_fields=EVAL, funded_fields=FUNDED) -> L.Plan:
    presets = [SimpleNamespace(label="Test 25k", stage=st, fields=f, id=f"test_{st}", builtin=True, firm="X")
               for st, f in (("evaluation", eval_fields), ("funded", funded_fields)) if f]
    return L.plans(presets, lambda p: "Test")[0]


def days_of(rs: list[float], start: date = date(2026, 1, 5)) -> list[list[Trade]]:
    """One trade a day on consecutive weekdays."""
    out, d = [], start
    for i, r in enumerate(rs):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append([Trade(id=f"T{i}", account="strategy", source="backtest", date=d, entry_time="10:00:00",
                          exit_time="10:30:00", direction="long", style="d", minis=1, micros=0, entry=1.0, exit=2.0,
                          net_usd=r * 500, r=r, commission_usd=0.0)])
        d += timedelta(days=1)
    return out


def kinds(r):
    return [e["kind"] for e in r["events"]]


def test_plans_group_stages_by_name():
    pl = plan()
    assert pl.id == "plan:Test 25k" and pl.evaluation.target_balance == 26500 and pl.funded.target_balance is None
    assert pl.lifecycle.payout.baseline_usd == 26100 and pl.lifecycle.payout.need == 500


def test_pass_funded_then_payouts_whenever_allowed():
    # +$600 a day: passes on day 3 ($26,800); funded from day 4: day 6 $26,800 -> $700 above the $26,100 baseline, paid;
    # day 7 and 8: $600 each (>= $500, and $600 since the last >= $250)
    r = L.replay(days_of([1.2] * 8), plan())
    assert kinds(r) == ["passed", "payout", "payout", "payout"]
    assert [e["amount"] for e in r["events"] if e["kind"] == "payout"] == [700.0, 600.0, 600.0]
    t = r["totals"]
    assert (t["evaluations"], t["passes"], t["payouts_n"], t["payouts_gross"]) == (1, 1, 3, 1900.0)
    assert r["balance"][5] == 26800 and r["balance"][6] == 26700      # the day's balance before its payout
    # lines only while they apply: the target during the evaluation, the payout line while funded
    assert r["target"][:3] == [26500] * 3 and r["target"][3] is None
    assert r["payout_line"][:3] == [None] * 3 and r["payout_line"][3] == 26600
    assert r["payout_line"][6] == 26600                                 # max($26,600, $26,100 + $250)
    assert r["stage"] == ["evaluation"] * 3 + ["funded"] * 5


def test_floor_fails_and_a_new_evaluation_starts():
    # pass on day 3, lose the funded account on day 4 ($25,000 - $1,000), fail the next evaluation on day 5
    r = L.replay(days_of([1.2, 1.2, 1.2, -2.0, -2.0, 1.2]), plan())
    assert kinds(r) == ["passed", "failed", "failed"]
    t = r["totals"]
    assert (t["evaluations"], t["passes"], t["funded_fails"], t["eval_fails"]) == (3, 1, 1, 1)
    assert r["stage"] == ["evaluation"] * 3 + ["funded", "evaluation", "evaluation"] and t["ends_in"] == "evaluation"


def test_min_trading_days_delays_the_pass():
    # +$1,600 on day 1 is past the target, but the evaluation needs 4 trading days: it passes on day 4
    r = L.replay(days_of([3.2, 0.2, 0.2, 0.2, 0.2]), plan(eval_fields={**EVAL, "min_trading_days": 4}))
    assert [(e["kind"], e["day"]) for e in r["events"]] == [("passed", 4)]


def test_payout_lock_first_payout():
    # Pro-like: the funded floor trails with no cap until the first payout, then locks at $25,100 for good
    fu = {**FUNDED, "drawdown_lock_trigger": "first_payout", "payout_profit_since_last_usd": None}
    r = L.replay(days_of([1.2, 1.2, 1.2] + [1.2, 1.2, 1.2] + [-0.4]), plan(funded_fields=fu))
    assert kinds(r) == ["passed", "payout"]
    assert r["floor"][4] == 25200          # trailing: $26,200 high - $1,000, no cap at the lock
    assert r["floor"][5] == 25100          # the payout locks it
    assert r["floor"][6] == 25100


def test_lucid_share_of_profit_and_profit_days():
    # Lucid-like: withdrawable = min($1,000, 50% of the profit above start), needs $1,000, and 5 days of >= $100
    fu = {**EVAL, "profit_target_usd": None, "drawdown_lock_usd": 25100, "drawdown_lock_trigger": "floor_or_first_payout",
          "payout_pct_of_profit": 0.5, "payout_max_usd": 1000, "payout_min_usd": 500, "payout_target_usd": 1000,
          "payout_profit_days_min": 5, "payout_profit_day_min_usd": 100, "profit_split_pct": 0.9}
    pl = plan(funded_fields=fu)
    assert pl.lifecycle.payout.threshold_profit == 2000
    # funded from day 4: +$1,000 a day -> $27,000 on day 5 (2 profit days: not yet); paid on day 8 (5 profit days)
    r = L.replay(days_of([1.2] * 3 + [2.0] * 5), pl)
    pays = [e for e in r["events"] if e["kind"] == "payout"]
    assert [(e["day"], e["amount"]) for e in pays] == [(8, 1000.0)]
    assert r["totals"]["payouts_share"] == 900.0 and r["totals"]["split_pct"] == 0.9
    # no "profit since the last payout" rule: the payout line stays at the baseline + $2,000 after a payout
    assert set(v for v in r["payout_line"] if v is not None) == {27000}


def test_consistency_blocks_a_payout():
    # Builder-like funded: a cycle's best day must be <= 50% of the cycle's profit
    fu = {**FUNDED, "profit_cap_mode": "consistency", "profit_target_usd": 2100, "consistency_pct": 0.5,
          "payout_profit_since_last_usd": None}
    # funded: +$2,000 then +$100: profit $2,100, best day $2,000 > $1,050: no payout; then +$2,000 more: $4,100, ok
    r = L.replay(days_of([1.2] * 3 + [4.0, 0.2, 4.0]), plan(funded_fields=fu))
    assert [(e["kind"], e["day"]) for e in r["events"]] == [("passed", 3), ("payout", 6)]


def test_payout_clock_counts_calendar_days():
    # a 14-calendar-day clock from the first funded session: Mon 2026-01-08 (day 4) -> no payout before 01-22's span
    fu = {**FUNDED, "payout_wait_calendar_days": 14, "payout_profit_since_last_usd": None}
    r = L.replay(days_of([1.2] * 3 + [0.6] * 14), plan(funded_fields=fu))
    pay = [e for e in r["events"] if e["kind"] == "payout"]
    first_funded = date.fromisoformat(r["dates"][3])
    assert pay and (date.fromisoformat(pay[0]["date"]) - first_funded).days + 1 >= 14


def test_fees_and_net_cash():
    ev = {**EVAL, "evaluation_fee_usd": 100}
    fu = {**FUNDED, "activation_fee_usd": 50}
    r = L.replay(days_of([1.2] * 8), plan(eval_fields=ev, funded_fields=fu))
    t = r["totals"]
    assert t["fees"] == 150.0 and t["net_cash"] == 1900.0 - 150.0 and t["fees_missing"] == []


def test_missing_fees_are_listed_never_guessed():
    r = L.replay(days_of([1.2] * 8), plan())
    assert r["totals"]["fees"] is None and r["totals"]["net_cash"] is None
    assert r["totals"]["fees_missing"] == ["evaluation fee", "activation fee"]
    res = L.monte_carlo(days_of([1.2]), plan(), horizon=10, n=20)
    assert res["numbers"]["net_cash"] is None and res["numbers"]["fees_missing"] == ["evaluation fee", "activation fee"]


def test_monte_carlo_numbers_and_fan():
    ev = {**EVAL, "evaluation_fee_usd": 100}
    fu = {**FUNDED, "activation_fee_usd": 0}
    res = L.monte_carlo(days_of([1.2]), plan(eval_fields=ev, funded_fields=fu), horizon=8, n=20)
    n = res["numbers"]
    assert n["pass_rate"]["value"] == 1.0 and n["days_to_pass"]["median"] == 3
    assert n["evaluations"]["median"] == 1 and n["payouts_n"]["median"] == 3 and n["payouts_gross"]["median"] == 1900
    assert n["fees"]["median"] == 100 and n["net_cash"]["median"] == 1800
    assert len(res["bands"]) == 9 and res["base"]["stage"][1] == "evaluation"


def test_monte_carlo_restarts_after_a_floor():
    # -$1,000 a day: every evaluation fails on its first day and a new one starts the next: 5 evaluations in 5 days (none
    # is started after the last day)
    res = L.monte_carlo(days_of([-2.0]), plan(eval_fields={**EVAL, "evaluation_fee_usd": 10},
                                              funded_fields={**FUNDED, "activation_fee_usd": 0}), horizon=5, n=10)
    n = res["numbers"]
    assert n["evaluations"]["median"] == 5 and n["pass_rate"]["value"] == 0.0 and n["fees"]["median"] == 50


def test_cap_group_ends_the_path():
    fu = {**FUNDED, "payout_cap_group_usd": 1300, "payout_profit_since_last_usd": None}
    r = L.replay(days_of([1.2] * 10), plan(funded_fields=fu))
    pays = [e["amount"] for e in r["events"] if e["kind"] == "payout"]
    assert sum(pays) == 1300 and kinds(r)[-1] == "capped" and r["totals"]["ends_in"] == "ended"


def test_engine_vectorised_equals_one_path_at_a_time():
    pl = plan(eval_fields={**EVAL, "evaluation_fee_usd": 100}, funded_fields={**FUNDED, "activation_fee_usd": 25})
    ev, fu, fa = L._days(days_of([1.2, -1.0, 0.5, -1.0, 2.0, 0.3]), pl)
    rng = np.random.default_rng(3)
    idx = rng.integers(0, 6, size=(50, 40))
    cal = LC.calendar_steps_typical(40)
    all_ = LC.run(pl.lifecycle, ev, fu, fa, idx, cal)
    for p in (0, 7, 31):
        one = LC.run(pl.lifecycle, ev, fu, fa, idx[p:p + 1], cal)
        for k in ("evaluations", "passes", "payouts_n", "payouts_gross", "fees"):
            assert one[k][0] == pytest.approx(all_[k][p])
        assert np.allclose(one["balance"][0], all_["balance"][p])


def test_order_luck_and_horizons():
    res = L.monte_carlo(days_of([1.2, -1.0, 0.5, -1.0]), plan(), horizon=30, n=200)
    assert res["order_luck"]["pass_rate"] is not None and len(res["bands"]) == 31
