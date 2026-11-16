"""The "from today" Monte Carlo of one actual account (owner 2026-11-15): only the account's own stage, from where it
stands today. An evaluation ends at its target or its floor; a funded account takes payouts as its rules allow and ends
at its floor; no path changes stage or starts a new evaluation."""
from datetime import date, timedelta

from nqbot.performance import account_mc as A
from nqbot.performance.ledger import Trade

EVAL = {"name": "e1", "phase": "evaluation", "start_balance_usd": 25000, "drawdown_usd": 1000, "drawdown_type": "eod_trailing",
        "drawdown_lock_usd": None, "drawdown_lock_trigger": "floor", "risk_usd_per_trade": 500, "max_risk_fraction_divisor": 2,
        "profit_target_usd": 1500, "commission_per_mini_rt": 0, "commission_per_micro_rt": 0}
FUNDED = {**EVAL, "name": "f1", "phase": "funded", "profit_target_usd": None, "drawdown_lock_usd": 25100,
          "drawdown_lock_trigger": "floor", "funded_buffer_usd": 1100, "payout_min_usd": 500}


def days_of(rs):
    out, d = [], date(2026, 1, 5)
    for i, r in enumerate(rs):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append([Trade(id=f"T{i}", account="strategy", source="backtest", date=d, entry_time="10:00:00", exit_time="10:30:00",
                          direction="long", style="d", minis=1, micros=0, entry=1.0, exit=2.0, net_usd=r * 500, r=r, commission_usd=0.0)])
        d += timedelta(days=1)
    return out


def real(vals, start=date(2026, 3, 2)):
    return [(start + timedelta(days=i), v) for i, v in enumerate(vals)]


def state(bal, high, floor):
    return {"balance_usd": bal, "high_water_usd": high, "floor_usd": floor}


def test_evaluation_from_today_ends_at_its_target():
    # $26,000 today, +$600 a day: $26,600 >= $26,500 on day 1 -> passed; the path ends there (no funded stage)
    r = A.run(days_of([1.2]), EVAL, state(26000, 26000, 25000), real([500.0, 500.0]), horizon=10, n=20)
    n = r["numbers"]
    assert n["stage"] == "evaluation" and n["p_pass"] == 1.0 and n["days_to_pass"]["median"] == 1 and n["p_fail"] == 0.0
    assert r["base"]["stage"][1] == "evaluation" and r["base"]["stage"][2] == "ended"
    assert all(e["kind"] != "payout" for e in r["base"]["events"])


def test_evaluation_from_today_ends_at_its_floor():
    # $25,200 with the floor at $25,000: -$300 a day fails on day 1
    r = A.run(days_of([-0.6]), EVAL, state(25200, 26000, 25000), [], horizon=10, n=20)
    n = r["numbers"]
    assert n["p_fail"] == 1.0 and n["days_to_fail"]["median"] == 1 and n["p_pass"] == 0.0


def test_evaluation_waits_for_its_minimum_trading_days():
    acc = {**EVAL, "min_trading_days": 4}
    r = A.run(days_of([3.2]), acc, state(25000, 25000, 24000), [], horizon=10, n=10)     # $26,600 on day 1, but 4 days needed
    assert r["numbers"]["days_to_pass"]["median"] == 4


def test_funded_from_today_payouts_until_the_floor_or_the_horizon():
    # $26,400 today, +$300 a day: $26,700 on day 1 -> $600 above the $26,100 baseline, paid; never a new evaluation
    r = A.run(days_of([0.6]), FUNDED, state(26400, 26400, 25100), real([300.0] * 5), horizon=6, n=20)
    n = r["numbers"]
    assert n["stage"] == "funded" and n["p_payout"] == 1.0 and n["days_to_payout"]["median"] == 1
    assert n["payouts_n"]["median"] >= 2 and n["p_fail"] == 0.0
    assert set(r["base"]["stage"]) == {"funded"}


def test_funded_floor_ends_it_without_a_new_evaluation():
    r = A.run(days_of([-2.0]), FUNDED, state(25500, 26000, 25100), [], horizon=8, n=10)
    n = r["numbers"]
    assert n["p_fail"] == 1.0 and n["p_payout"] == 0.0 and r["base"]["stage"][1] == "funded" and r["base"]["stage"][2] == "ended"


def test_a_locked_floor_stays_locked():
    acc = {**FUNDED, "drawdown_lock_trigger": "first_payout", "payouts_taken": 1}
    st = A.start_state(acc, state(26000, 27500, 25100), [])
    assert st["locked"] is True and st["cycle_start"] == 26000 and st["cycle_from"] == "today (assumed)"
    r = A.run(days_of([0.2]), acc, state(26000, 27500, 25100), [], horizon=5, n=5)
    assert set(v for v in r["base"]["floor"] if v is not None) == {25100}


def test_the_cycle_starts_at_the_last_payout_date():
    # Lucid-like: 5 days of $100+ per cycle; 3 of the 5 sessions after the last payout count
    acc = {**FUNDED, "payouts_taken": 1, "last_payout_date": "2026-03-03", "payout_profit_days_min": 5, "payout_profit_day_min_usd": 100}
    days_ = real([300.0, 200.0, 150.0, 50.0, 120.0, 110.0])          # 03-02 .. 03-07
    st = A.start_state(acc, state(26930, 26930, 25100), days_)
    assert st["cycle_from"] == "last payout 2026-03-03" and st["cycle_days"] == 4 and st["cycle_profit_days"] == 3
    assert st["cycle_start"] == 25000 + 500 and st["cycle_calendar_days"] == 4
