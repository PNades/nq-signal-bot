"""The Accounts page's live values and checks (SPEC §9b item 1, §6.1, §6.5b, §6.7), on hand-worked numbers."""

from __future__ import annotations

import pytest

from nqbot.accounts import derive
from nqbot.accounts.presets import builtin_presets
from nqbot.accounts.schema import Account

S = {"lives_dust_usd": 25, "exit_slippage_ticks": 4, "max_entry_slip_ticks": 4, "baseline_rr": 2.5}
P = {p.id: p.fields for p in builtin_presets()}
NO_HOOKS = lambda: {}  # noqa: E731


def acct(pid="mffu_rapid_eod_25k_evaluation", **over):
    return {**P[pid], "name": "mffur1", **over}


def test_rapid_eval_numbers():
    d = derive.derive(Account.model_validate(acct()), S)
    assert d.daily_profit_limit_usd == pytest.approx(450.0)   # 30% x $1,500
    assert d.risk_usd_per_trade == 333 and d.risk_cap_usd == pytest.approx(333.33, abs=0.01) and d.fraction == "1/3"
    assert d.lives == 3                                        # floor((1000 - 25) / 333) + 1
    assert d.size_cap_minis == 2
    # 2 minis: (333/2 - 4.68 - 4 x $5) / $20 = 7.09 pts -> 7.25 (next tick); marketable entry 4 ticks less
    assert d.min_stop_full_risk_pts == {"passive": 7.25, "marketable": 6.25}
    # 1 micro: (333 - 1.90 - 4 x $0.50) / $2 = 164.55 -> 164.50; marketable 1 pt less
    assert d.max_stop_one_contract_pts == {"passive": 164.5, "marketable": 163.5} and d.one_contract == "micro"


def test_daily_profit_limits_by_stage():
    lim = lambda pid: derive.derive(Account.model_validate(acct(pid)), S).daily_profit_limit_usd  # noqa: E731
    assert lim("mffu_builder_25k_evaluation") is None          # no consistency rule
    assert lim("mffu_builder_25k_funded") == pytest.approx(1050)
    assert lim("mffu_builder_25k_after_first_payout") == pytest.approx(500)
    assert lim("mffu_pro_50k_evaluation") == pytest.approx(1500)
    assert lim("lucid_flex_25k_evaluation") == pytest.approx(625)


def test_size_cap_rules():
    assert derive.size_cap_minis(Account.model_validate(acct("mffu_pro_50k_funded"))) == 5   # 5 slots, minis first
    assert derive.size_cap_minis(Account.model_validate(acct("lucid_flex_25k_funded"))) == 1  # first scaling tier
    no_micros = Account.model_validate(acct(allow_micros=False))
    assert derive.size_cap_minis(no_micros) == 2
    assert derive.derive(no_micros, S).one_contract == "mini"


def test_risk_fraction_and_cap_checks():
    _, issues = derive.validate(acct(max_risk_fraction_divisor=5), [], S, NO_HOOKS)
    assert any(i.field == "max_risk_fraction_divisor" and i.level == "error" for i in issues)
    _, issues = derive.validate(acct(risk_usd_per_trade=400), [], S, NO_HOOKS)
    assert [i.field for i in issues if i.level == "error"] == ["risk_usd_per_trade"]   # above $1,000 / 3
    _, issues = derive.validate(acct(risk_usd_per_trade=250), [], S, NO_HOOKS)        # lower is fine
    assert not [i for i in issues if i.level == "error"]


def test_missing_and_inconsistent_fields_block_saving():
    _, issues = derive.validate(acct(consistency_pct=None), [], S, NO_HOOKS)
    assert ("consistency_pct", "error") in [(i.field, i.level) for i in issues]
    _, issues = derive.validate(acct("mffu_builder_25k_evaluation", firm_daily_loss_limit_usd=None), [], S, NO_HOOKS)
    assert ("firm_daily_loss_limit_usd", "error") in [(i.field, i.level) for i in issues]
    a, issues = derive.validate({k: v for k, v in acct().items() if k != "drawdown_usd"}, [], S, NO_HOOKS)
    assert a is None and ("drawdown_usd", "error") in [(i.field, i.level) for i in issues]
    _, issues = derive.validate(acct(), [acct()], S, NO_HOOKS)
    assert ("name", "error") in [(i.field, i.level) for i in issues]          # two accounts can't share a name
    _, issues = derive.validate(acct(), [acct()], S, NO_HOOKS, original_name="mffur1")   # editing itself is fine
    assert ("name", "error") not in [(i.field, i.level) for i in issues]


def test_warnings_dont_block():
    _, issues = derive.validate(acct("mffu_builder_25k_evaluation", bot_daily_loss_cap_usd=700), [], S, NO_HOOKS)
    assert [(i.field, i.level) for i in issues if i.level != "info"] == [("bot_daily_loss_cap_usd", "warning")]
    others = [acct(name="m2"), acct(name="m3", copied_accounts=0)]
    _, issues = derive.validate(acct(name="m4"), others, S, NO_HOOKS)
    assert not [i for i in issues if i.field == "firm"]                       # 3 MFFU accounts: allowed
    _, issues = derive.validate(acct(name="m4", copied_accounts=1), others, S, NO_HOOKS)
    assert [i.level for i in issues if i.field == "firm"] == ["warning"]      # 4 with the copy: warned
    drafts = [acct(name="d1", draft=True), acct(name="d2", draft=True), acct(name="d3", draft=True)]
    _, issues = derive.validate(acct(name="m4"), drafts, S, NO_HOOKS)
    assert not [i for i in issues if i.field == "firm"]                       # drafts don't count


def test_webhook_same_url_check_never_shows_a_url():
    url = "https://webhooks.traderspost.io/trading/webhook/abc/def"
    hooks = lambda: {"TP_WEBHOOK_A": url, "TP_WEBHOOK_B": url, "TP_WEBHOOK_C": url + "x"}  # noqa: E731
    others = [acct(name="m2", traderspost_webhook_env="TP_WEBHOOK_B")]
    _, issues = derive.validate(acct(traderspost_webhook_env="TP_WEBHOOK_A"), others, S, hooks)
    errs = [i for i in issues if i.field == "traderspost_webhook_env"]
    assert [i.level for i in errs] == ["error"] and "m2" in errs[0].message and url not in errs[0].message
    _, issues = derive.validate(acct(traderspost_webhook_env="TP_WEBHOOK_C"), others, S, hooks)
    assert not [i for i in issues if i.field == "traderspost_webhook_env"]
    _, issues = derive.validate(acct(), others, S, NO_HOOKS)
    assert [i.level for i in issues if i.field == "traderspost_webhook_env"] == ["info"]   # the PC: not available


def test_raising_risk_effect():
    a = Account.model_validate(acct())
    e = derive.risk_raise_effect(a, 2, S)
    assert e["new_risk_usd"] == 500 and e["lives_new"] == 2 and e["lives_old"] == 3   # floor(975/500)+1
    assert e["confirm_text"] == "mffur1 1/2"
    # at an 8-pt stop the $450 daily cap limits a 2.5R trade to $180 of risk; at 40 pts the max position binds first?
    rows = {r["stop_pts"]: r for r in e["rows"]}
    assert rows[8.0]["limited_by"] == "daily profit cap" and rows[8.0]["effective_risk_usd"] == pytest.approx(180)
    with pytest.raises(Exception):
        derive.risk_raise_effect(a, 5, S)
