"""The synthetic ledger's accounts follow their rules (owner 2026-11-15): every session's end-of-day balance, high water
and floor are states a live account can be in: never at or past the floor in force, and an evaluation never at or above
its profit target (it would have passed: a new funded account with its own ID)."""
import pytest

from nqbot.accounts.presets import apply_preset, builtin_presets
from nqbot.config import load_settings
from nqbot.performance import state, synthetic
from nqbot.performance.floor import Rule, floor_path
from nqbot.performance.stats import days


def preset_accounts():
    by_id = {p.id: p for p in builtin_presets()}
    return [{**apply_preset(by_id["mffu_rapid_eod_25k_evaluation"]), "name": "mffur1"},
            {**apply_preset(by_id["mffu_builder_25k_evaluation"]), "name": "mffub1"},
            {**apply_preset(by_id["lucid_flex_25k_funded"]), "name": "lucid1"}]


@pytest.mark.parametrize("accounts", [synthetic.DEFAULT_ACCOUNTS, preset_accounts()], ids=["defaults", "presets"])
def test_every_account_state_is_allowed_by_its_rules(accounts):
    trades = synthetic.ledger(load_settings(), accounts)
    for acc in accounts:
        mine = [t for t in trades if t.account == acc["name"] and t.source in state.REAL]
        assert mine, f"{acc['name']} has real-trade stand-ins"
        rule = Rule.of(acc)
        eod, b = [], rule.start_usd
        for d in days(mine):
            b += d.usd
            eod.append(b)
        fp = floor_path(rule, eod)
        assert fp.breach_at is None, f"{acc['name']} reached its floor"
        prev = rule.start_usd - rule.drawdown_usd
        target = rule.start_usd + float(acc["profit_target_usd"]) if acc.get("phase") == "evaluation" else None
        for bal, fl, hi in zip(eod, fp.floor, fp.high):
            assert bal > prev and hi >= bal and fl <= hi
            if target is not None:
                assert bal < target, f"{acc['name']} is an evaluation at or above its target"
            prev = fl
        st = state.from_trades(trades, acc, synthetic=True)
        assert st["balance_usd"] == pytest.approx(eod[-1]) and st["floor_usd"] < st["balance_usd"] <= st["high_water_usd"]
        assert not st["floor_reached"]
