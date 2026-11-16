"""The day-block Monte Carlo (SPEC §9b item 6): deterministic per seed, whole days only, run through the floor."""
import numpy as np

from nqbot.performance.floor import Rule, floor_path
from nqbot.performance.montecarlo import floor_paths, run

RULE = Rule(25000, 1000, "eod_trailing")


def test_deterministic_and_shaped():
    days = [300.0, -333.0, 650.0, -340.0, 120.0, -333.0, 900.0]
    a, b = run(days, RULE, n=200, seed=3), run(days, RULE, n=200, seed=3)
    assert a == b
    assert a["days"] == 7 and len(a["bands"]) == 8 and a["bands"][0]["p50"] == 0.0
    assert all(x["p5"] <= x["p25"] <= x["p50"] <= x["p75"] <= x["p95"] for x in a["bands"])
    assert a["base"][-1] == sum(days) and a["base_floor"][0] == -1000      # this order never reaches the floor
    # an evaluation's backtest order stops at the target like every path
    ev = run([600.0] * 5, RULE, n=20, seed=1, target_balance=26500)
    assert ev["base"] == [0.0, 600.0, 1200.0, 1800.0, 1800.0, 1800.0]


def test_whole_days_only():
    # every path is a sum of whole days: with two day values, every step is one of them (or 0 once its run has ended)
    days = [500.0, -200.0]
    res = run(days, RULE, n=50, seed=1)
    for path in (res["worst"], res["best"]):
        steps = {round(path[i + 1] - path[i], 2) for i in range(len(path) - 1)}
        assert steps <= {500.0, -200.0, 0.0}


def test_breach_by_hand():
    # one losing day of $1,000 takes any path that draws it to the floor ($24,000) on that day
    res = run([-1000.0], RULE, n=20, seed=0)
    assert res["p_breach"] == 1.0 and res["median_breach_day"] == 1
    assert run([400.0, 300.0], RULE, n=20, seed=0)["p_breach"] == 0.0


def test_permutation_keeps_the_total():
    days = [300.0, -333.0, 650.0]
    res = run(days, RULE, n=30, seed=2, method="permutation")
    assert res["bands"][-1]["p5"] == res["bands"][-1]["p95"] == round(sum(days), 2)


def test_vectorised_floor_equals_the_scalar_rule():
    rng = np.random.default_rng(5)
    for rule in (Rule(25000, 1000, "static"), RULE, Rule(25000, 1000, "eod_trailing", 25100, "floor"),
                 Rule(25000, 1000, "eod_trailing", 25100, "floor_or_first_payout"), Rule(50000, 2000, "eod_trailing", 50100, "first_payout")):
        bal = rule.start_usd + np.cumsum(rng.choice([-400.0, -333.0, 150.0, 600.0, 900.0], size=(40, 30)), axis=1)
        floors, first = floor_paths(rule, bal, rule.start_usd, rule.start_usd - rule.drawdown_usd)
        for k in range(bal.shape[0]):
            fp = floor_path(rule, list(bal[k]))
            assert np.allclose(floors[k], fp.floor)
            assert first[k] == (-1 if fp.breach_at is None else fp.breach_at)


def test_an_evaluation_stops_at_the_target_or_the_floor():
    # days of +600 or -1,000 over 5 days: three +600 in a row pass ($26,800 >= $26,500); any -1,000 reaches the trailing
    # floor (it sits $1,000 under the high water); so every path ends on one of the two, and stays there
    res = run([600.0, -1000.0], RULE, n=200, seed=4, target_balance=26500, horizon=5)
    assert abs(res["p_breach"] + res["p_target"] - 1.0) < 1e-9 and res["p_target"] > 0
    ends = {round(x, 2) for x in (res["worst"][-1], res["best"][-1])}
    assert ends <= {-1000.0, -400.0, 200.0, 1800.0}
    assert res["best"][-1] == 1800.0 and res["best"][-2] == 1800.0     # stopped at the target: flat to the end


def test_from_today_starts_from_the_state():
    # a locked floor stays where it is; the paths start at today's balance
    rule = Rule(25000, 1000, "eod_trailing", 25100, "floor")
    res = run([200.0, -100.0], rule, n=50, seed=3, start=26000, high0=26300, floor0=25100)
    assert res["start_usd"] == 26000 and res["base_floor"][0] == 25100 - 26000
    assert all(abs(x - (25100 - 26000)) < 1e-6 for x in res["base_floor"])
    assert res["p_breach"] == 0.0
