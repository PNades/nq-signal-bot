"""The Performance page's API (SPEC §9b item 6): the ledger guard on every route, the CSV export included, the empty
state before step 15 and the synthetic ledger behind `--synthetic` only."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from nqbot.config import load_features, load_settings
from nqbot.ui.app import PanelConfig, create_app

BASE = "http://127.0.0.1:8733"


def make(paths, tmp_path, synthetic: bool) -> TestClient:
    cfg = PanelConfig(port=8733, paths_fn=lambda: paths, token="tok", web_dir=tmp_path / "web", synthetic=synthetic,
                      settings=load_settings(), features=load_features())
    return TestClient(create_app(cfg), base_url=BASE)


@pytest.fixture
def real(paths, tmp_path):
    return make(paths, tmp_path, False)


@pytest.fixture
def synth(paths, tmp_path):
    return make(paths, tmp_path, True)


def test_before_step_15_every_source_says_no_data_yet(real):
    r = real.get("/api/performance").json()
    assert r["synthetic"] is False and r["kpis"]["trades"] == 0 and r["equity"]["points"] == []
    assert r["sources"]["backtest"] == "no backtest trades yet (from step 15)"
    assert r["sources"]["demo"].startswith("no demo trades yet")


def test_synthetic_only_behind_the_flag_and_held_back_by_the_guard(synth):
    r = synth.get("/api/performance").json()
    assert r["synthetic"] is True and r["kpis"]["trades"] > 0
    held = {n["phase"]: n for n in r["notices"]}
    assert held["walk_forward"]["gate"] == "16" and held["sealed"]["gate"] == "20"
    assert {p["phase"] for p in r["equity"]["points"]} <= {"development", "demo", "prop"}
    names = {a["name"] for a in r["accounts"]}
    assert {"mffur1", "mffub1"} <= names                 # the panel's own (draft) accounts carry the synthetic trades


def test_explicit_requests_refused_with_the_missing_gate(synth):
    r = synth.get("/api/performance?phase=walk_forward")
    assert r.status_code == 403 and r.json()["gate"] == "16"
    assert synth.get("/api/performance?phase=sealed").json()["gate"] == "20"
    assert synth.get("/api/performance/trades?phase=walk_forward").status_code == 403
    for phase in ("walk_forward", "sealed"):
        r = synth.get(f"/api/performance/export.csv?phase={phase}")
        assert r.status_code == 403


def test_trades_calendar_and_csv_never_show_held_months(synth):
    months = load_settings()["months"]
    held = {months[n] for n in range(7, 13)}
    rows = synth.get("/api/performance/trades").json()["trades"]
    assert rows and all(t["date"][:7] not in held or t["source"] != "backtest" for t in rows)
    csv = synth.get("/api/performance/export.csv").text.splitlines()
    assert csv[0].startswith("id,date") and all(line.split(",")[1][:7] not in held or ",backtest," not in line for line in csv[1:])
    cal = synth.get(f"/api/performance/calendar?month={months[8]}").json()
    assert cal["notice"]["phase"] == "walk_forward" and cal["notice"]["count"] > 0
    assert all(c["value"] is None for w in cal["weeks"] for c in w["days"] if c["in_month"])


def test_monte_carlo_and_drawdown_by_plan(synth):
    r = synth.get("/api/performance/montecarlo?mode=plans&compare=1").json()
    labels = [x["label"] for x in r["plans"]]
    assert labels[:4] == ["MFFU Rapid EOD 25k", "MFFU Builder 25k", "MFFU Pro 50k", "Lucid Flex 25k"]
    assert r["horizon"] == 60 and r["horizons"] == [30, 60, 120] and len(r["fan"]["bands"]) == 61
    n = r["fan"]["numbers"]
    assert 0 <= n["pass_rate"]["value"] <= 1 and n["days_to_pass"]["median"] is not None
    assert n["evaluations"]["median"] >= 1 and n["payouts_n"] is not None and r["fan"]["order_luck"]["pass_rate"] is not None
    # the plans' fees (owner 2026-11-16): MFFU Rapid EOD $97.20 per evaluation started, no activation fee; net cash shown
    assert n["fees_missing"] == [] and n["fees"]["median"] == pytest.approx(n["evaluations"]["median"] * 97.20)
    assert n["net_cash"] is not None and n["net_cash"]["median"] <= n["payouts_share"]["median"]
    assert len(r["compare"]) == len(r["plans"])
    assert synth.get("/api/performance/montecarlo?mode=plans").json()["compare"] is None     # not by default
    assert synth.get("/api/performance/montecarlo?mode=plans&horizon=45").status_code == 400
    held = {m for k, m in load_settings()["months"].items() if k >= 7}
    d = synth.get("/api/performance/drawdown?mode=plans&plan=plan:MFFU Pro 50k").json()
    rp = d["replay"]
    t = rp["totals"]
    assert t["evaluations"] >= 1 and rp["dates"] and all(x[:7] not in held for x in rp["dates"])   # the guard
    # a new evaluation after every floor (except one reached on the last day)
    assert t["evaluations"] in (1 + t["eval_fails"] + t["funded_fails"], t["eval_fails"] + t["funded_fails"])
    assert len(rp["balance"]) == len(rp["dates"]) == len(rp["floor"]) == len(rp["stage"])


def test_every_number_follows_the_plan(synth):
    # one strategy ledger, priced by the plan: Pro 50k risks $666 a trade, the 25k plans $333; R is the same
    rapid = synth.get("/api/performance?phase=development&plan=plan:MFFU Rapid EOD 25k").json()
    pro = synth.get("/api/performance?phase=development&plan=plan:MFFU Pro 50k").json()
    assert rapid["kpis"]["net_r"] == pro["kpis"]["net_r"] and pro["kpis"]["net_usd"] > 1.8 * rapid["kpis"]["net_usd"]
    assert pro["plan"]["label"] == "MFFU Pro 50k"
    tr = synth.get("/api/performance/trades?phase=development&plan=plan:MFFU Pro 50k").json()["trades"]
    assert tr and {t["account"] for t in tr} == {"strategy"}
    t0 = tr[0]
    assert t0["net_usd"] == pytest.approx(round(t0["r"] * 666 - t0["commission_usd"], 2))
    dev = load_settings()["months"][3]       # a development month (the latest month holds real-trade stand-ins only)
    cal_r = synth.get(f"/api/performance/calendar?unit=usd&month={dev}&plan=plan:MFFU Rapid EOD 25k").json()["header"]["value"]
    cal_p = synth.get(f"/api/performance/calendar?unit=usd&month={dev}&plan=plan:MFFU Pro 50k").json()["header"]["value"]
    assert cal_r != cal_p
    # the account filter applies to real trades only: the strategy's backtest trades stay
    one = synth.get("/api/performance?phase=development&account=mffur1&plan=plan:MFFU Rapid EOD 25k").json()
    assert one["kpis"]["trades"] == rapid["kpis"]["trades"]


def test_monte_carlo_and_drawdown_from_today(synth):
    # only the account's own stage, from where it stands today (owner 2026-11-15): mffur1 is an evaluation
    r = synth.get("/api/performance/montecarlo?mode=account&account=mffur1").json()
    st, n = r["state"], r["fan"]["numbers"]
    assert st["source"] == "synthetic" and r["fan"]["start"]["balance"] == st["balance_usd"]
    assert n["stage"] == "evaluation" and 0 <= n["p_pass"] <= 1 and "p_breach" not in r["fan"]
    assert set(r["fan"]["base"]["stage"]) <= {"evaluation", "ended"}
    assert r["fan"]["min_trading_days"] == 4                # the Rapid EOD evaluation's minimum days (the pips' marker)
    assert len(r["fan"]["bands"]) == 61
    assert len(synth.get("/api/performance/montecarlo?mode=account&account=mffur1&horizon=30").json()["fan"]["bands"]) == 31
    d = synth.get("/api/performance/drawdown?mode=account&account=mffur1").json()
    assert d["run"]["dates"] and all(x > "2026-09-30" for x in d["run"]["dates"])   # its real (prop) trades only
    assert d["run"]["target"][0] == 26500 and d["run"]["payout_line"][0] is None
    # one point per trade at its exit time, from the account's start balance (owner 2026-11-16)
    run = d["run"]
    assert run["per_trade"] is True and run["balance"][0] == 25000 and run["pnl"][0] is None
    assert sum(x is not None for x in run["pnl"]) == len(run["pnl"]) - 1 and run["times"] == sorted(set(run["times"]))
    assert run["day_first"][0] == 0
    # the lock from the account's own rules: the Rapid EOD evaluation's floor never locks
    assert run["lock_usd"] is None and run["start_floor"] == 24000 and run["payouts_taken"] == 0
    # the closest approach counts the room left after each close (the floor it set): never above today's room
    assert run["closest_room"] <= run["balance"][-1] - run["floor_now"] + 0.01
    assert run["floor_now"] == d["run"]["floor_now"] and run["floor_now"] == r["state"]["floor_usd"]   # as the Monte Carlo's
    rp = synth.get("/api/performance/drawdown?mode=plans&horizon=30").json()["replay"]
    assert len(rp["dates"]) == 30
    assert synth.get("/api/performance/montecarlo?mode=account&account=nobody").status_code == 404


def test_kpi_sparklines_per_trading_day(synth):
    r = synth.get("/api/performance?phase=prop&account=mffur1").json()
    k = r["kpi_trend"]
    assert len(k["dates"]) == len(k["series"]["net_usd"]) >= 2 and k["compare"]["days"] == min(5, len(k["dates"]) - 1)
    assert k["series"]["net_usd"][-1] == pytest.approx(r["kpis"]["net_usd"])


def test_from_today_needs_a_balance_before_step_22(real):
    assert real.get("/api/performance/montecarlo?mode=account&account=mffur1").json()["state"] is None


def test_equity_starts_at_zero_and_real_trades_per_trade(synth):
    # owner 2026-11-16: every curve starts at $0 / 0R; real-trade phases: one point per trade at its exit time
    dev = synth.get("/api/performance?phase=development&plan=plan:MFFU Rapid EOD 25k").json()["equity"]
    assert dev["per_trade"] is False and dev["points"][0]["usd"] == 0 and dev["points"][0]["start"] is True
    assert dev["points"][0]["time"] < dev["points"][1]["time"]
    prop = synth.get("/api/performance?phase=prop&account=mffur1").json()
    eq = prop["equity"]
    assert eq["per_trade"] is True and eq["points"][0]["usd"] == 0 and len(eq["points"]) == prop["kpis"]["trades"] + 1
    times = [p["time"] for p in eq["points"]]
    assert all(isinstance(t, int) for t in times) and times == sorted(set(times))
    assert eq["points"][-1]["usd"] == pytest.approx(prop["kpis"]["net_usd"])


def test_calendar_follows_the_phase(synth):
    months = load_settings()["months"]
    dev = synth.get("/api/performance/calendar?phase=development").json()
    assert dev["months"] and all(m <= months[6] for m in dev["months"])          # development months only
    prop = synth.get("/api/performance/calendar?phase=prop&account=mffur1").json()
    assert prop["months"] and all(m > months[12] for m in prop["months"])        # after the window
    assert synth.get("/api/performance/calendar?phase=nope").status_code == 400


def test_all_accounts_breakdown_with_the_archived_history(synth):
    # owner 2026-11-16: "All accounts" in a real-trades phase adds a per-account breakdown, archived accounts included
    r = synth.get("/api/performance?phase=prop&account=all").json()
    rows, tot = r["breakdown"]["rows"], r["breakdown"]["totals"]
    by = {x["account"]: x for x in rows}
    assert {"mffur91", "mffur92", "mffurf92", "lucid91", "lucidf91", "mffub91", "mffur1"} <= set(by)
    assert by["mffur91"]["status"] == "failed" and by["mffur91"]["archived"] is True and by["mffur92"]["archived"] is True   # passed too
    assert by["mffur1"]["archived"] is False and tot["archived"] == 6 and by["mffur1"]["last_trade"] >= by["mffur1"]["start"]
    assert by["mffur91"]["fees"] == 97.20 and by["mffurf92"]["fees"] == 0 and by["mffub91"]["fees"] == 63.00
    assert by["lucidf91"]["payouts"] == round(by["lucidf91"]["payouts_gross"] * 0.9, 2) > 0      # Lucid's 90% split
    assert by["mffur1"]["status"] == "active" and by["mffur1"]["end"] is None
    assert by["mffur1"]["fees"] == 97.20                              # from its preset (drafts made before the fee fields)
    assert tot["fees"] == round(sum(x["fees"] for x in rows), 2) and tot["fees_unknown"] == 0
    assert tot["net_cash"] == round(tot["payouts"] - tot["fees"], 2)
    assert tot["net_usd"] == pytest.approx(r["kpis"]["net_usd"])     # every prop trade, once
    accounts = {a["name"]: a for a in r["accounts"]}
    assert accounts["mffur91"]["archived"] is True and accounts["mffur1"]["status"] == "active"
    assert "breakdown" not in synth.get("/api/performance?phase=prop&account=mffur1").json()


def test_an_account_with_trades_is_never_deleted(synth):
    H = {"origin": BASE, "x-cf-token": "tok"}
    acc = synth.get("/api/accounts").json()
    rows = {a["account"]["name"]: a for a in acc["accounts"]}
    assert rows["mffur1"]["trades"] > 0
    r = synth.post("/api/accounts/delete", json={"name": "mffur1", "typed": "mffur1", "hash": acc["hash"]}, headers=H)
    assert r.status_code == 409 and "never deleted" in r.json()["error"]
