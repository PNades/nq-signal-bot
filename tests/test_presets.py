"""The built-in presets equal the SPEC §6.7 preset table, stage by stage (hand-written expectations from the table)."""

from __future__ import annotations

import pytest

from nqbot.accounts.presets import ACCOUNT_OWN_FIELDS, apply_preset, builtin_presets, preset_fields
from nqbot.accounts.schema import Account

P = {p.id: p.fields for p in builtin_presets()}

# the §6.7 table, one dict per stage: the fields it names
EXPECTED = {
    "mffu_rapid_eod_25k_evaluation": dict(start_balance_usd=25000, profit_target_usd=1500, drawdown_usd=1000,
        drawdown_type="eod_trailing", drawdown_lock_usd=None, dll_enabled=False, profit_cap_mode="consistency",
        consistency_pct=0.30, stop_at_target=True, min_trading_days=4, max_inactive_days=7, risk_usd_per_trade=333,
        max_minis=2, max_micros=20, commission_per_mini_rt=4.68, commission_per_micro_rt=1.90, flat_by="15:59"),
    "mffu_rapid_eod_25k_funded": dict(drawdown_lock_usd=25100, drawdown_lock_trigger="floor", profit_cap_mode="none",
        dll_enabled=False, funded_buffer_usd=1100, payout_min_usd=500, payout_max_usd=None,
        payout_profit_since_last_usd=250, max_inactive_days=7, phase="funded"),
    "mffu_rapid_eod_25k_after_first_payout": dict(drawdown_lock_usd=25100, payouts_taken=1, payout_profit_since_last_usd=250),
    "mffu_builder_25k_evaluation": dict(profit_target_usd=1500, drawdown_usd=1000, drawdown_lock_usd=None,
        profit_cap_mode="none", stop_at_target=True, min_trading_days=1, dll_enabled=True, firm_daily_loss_limit_usd=600,
        max_inactive_days=7),
    "mffu_builder_25k_funded": dict(drawdown_lock_usd=25100, drawdown_lock_trigger="floor", profit_cap_mode="consistency",
        profit_target_usd=2100, consistency_pct=0.50, funded_buffer_usd=1100, payout_min_usd=250, payout_max_usd=1000,
        payout_target_usd=1000, dll_enabled=True, firm_daily_loss_limit_usd=600, payout_min_days=2,
        after_first_payout={"profit_cap_mode": "consistency", "profit_target_usd": 1000, "consistency_pct": 0.50}),
    "mffu_builder_25k_after_first_payout": dict(profit_cap_mode="consistency", profit_target_usd=1000,
        consistency_pct=0.50, payouts_taken=1),
    "mffu_pro_50k_evaluation": dict(start_balance_usd=50000, profit_cap_mode="consistency", profit_target_usd=3000,
        consistency_pct=0.50, stop_at_target=True, min_trading_days=2, drawdown_usd=2000, drawdown_type="eod_trailing",
        drawdown_lock_usd=None, dll_enabled=False, max_minis=3, max_micros=30, scaling_tiers=None,
        risk_usd_per_trade=666, max_inactive_days=7),
    "mffu_pro_50k_funded": dict(start_balance_usd=50000, profit_cap_mode="none", dll_enabled=False, drawdown_usd=2000,
        drawdown_type="eod_trailing", drawdown_lock_trigger="first_payout", drawdown_lock_usd=50100, max_minis=5,
        max_micros=5, max_contracts_total=5, funded_buffer_usd=2100, payout_min_usd=1000, payout_max_usd=None,
        payout_profit_since_last_usd=None, payout_min_days=None, payout_wait_calendar_days=14, payout_clock_hold="off",
        profit_split_pct=0.80, payout_cap_group="mffu_pro", payout_cap_group_usd=100000, after_first_payout=None,
        risk_usd_per_trade=666, max_inactive_days=7),
    "lucid_flex_25k_evaluation": dict(firm="Lucid", start_balance_usd=25000, profit_cap_mode="consistency",
        profit_target_usd=1250, consistency_pct=0.50, stop_at_target=True, drawdown_usd=1000,
        drawdown_type="eod_trailing", drawdown_lock_usd=None, dll_enabled=True, firm_daily_loss_limit_usd=600,
        max_minis=2, max_micros=20, micro_mini_ratio=10, scaling_tiers=None, risk_usd_per_trade=333,
        min_trading_days=None, max_inactive_days=30, flip_hold_max_sec=5, flip_profit_share_max=0.50,
        flip_profit_share_warn=0.25, commission_per_mini_rt=3.50, commission_per_micro_rt=1.00),
    "lucid_flex_25k_funded": dict(start_balance_usd=25000, profit_cap_mode="none", drawdown_usd=1000,
        drawdown_type="eod_trailing", drawdown_lock_usd=25100, drawdown_lock_trigger="floor_or_first_payout",
        dll_enabled=True, firm_daily_loss_limit_usd=600,
        scaling_tiers=[{"min_profit_usd": None, "max_minis": 1, "max_micros": 10},
                       {"min_profit_usd": 1000, "max_minis": 2, "max_micros": 20}],
        scaling_tier_update="eod", micro_mini_ratio=10, funded_buffer_usd=None, payout_pct_of_profit=0.50,
        payout_max_usd=1000, payout_min_usd=500, payout_target_usd=1000, payout_profit_days_min=5,
        payout_profit_day_min_usd=100, payout_profit_since_last_usd=None, payout_min_days=None,
        payout_wait_calendar_days=None, profit_split_pct=0.90, after_first_payout=None, risk_usd_per_trade=333,
        max_inactive_days=30, firm_balance_from_zero=True, flip_hold_max_sec=5),
}


def test_every_section_9b_stage_is_offered():
    # §9b: Rapid EOD / Builder × evaluation / funded / after first payout; Pro 50k and Lucid Flex 25k × eval / funded
    assert set(P) == set(EXPECTED)


@pytest.mark.parametrize("pid", sorted(EXPECTED))
def test_preset_equals_the_spec_table(pid):
    for k, v in EXPECTED[pid].items():
        assert P[pid][k] == v, (pid, k)


@pytest.mark.parametrize("pid", sorted(EXPECTED))
def test_every_preset_is_a_valid_account(pid):
    a = Account.model_validate({**P[pid], "name": "t1"})
    assert a.flat_by == "15:59" and a.max_risk_fraction_divisor == 3


def test_presets_never_carry_the_accounts_own_fields():
    for fields in P.values():
        assert not set(fields) & (ACCOUNT_OWN_FIELDS - {"preset"})


def test_apply_preset_keeps_the_accounts_own_fields():
    pro = next(p for p in builtin_presets() if p.id == "mffu_pro_50k_funded")
    acc = apply_preset(pro, {"name": "pro1", "traderspost_webhook_env": "TP_WEBHOOK_PRO1", "copied_accounts": 2,
                             "drawdown_usd": 1})
    assert acc["name"] == "pro1" and acc["copied_accounts"] == 2 and acc["drawdown_usd"] == 2000
    assert acc["preset"] == "mffu_pro_50k_funded"
    assert "name" not in preset_fields(acc)
