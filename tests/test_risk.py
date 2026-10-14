"""§6.1 / §6.5b / §6.8 risk fraction, lives and loss-run limit (owner 2026-10-14, §13 Q-riskfrac)."""

import pytest

from nqbot import risk
from nqbot.config import ConfigError, load_accounts, load_settings


@pytest.mark.parametrize("n", [1, 5, 2.5, 3.0, "3", True, None])
def test_divisor_whole_numbers_two_to_four_only(n):
    with pytest.raises(risk.RiskConfigError):
        risk.check_divisor(n)


def test_divisor_default_and_allowed():
    assert risk.divisor_of({}) == 3
    assert [risk.divisor_of({"max_risk_fraction_divisor": n}) for n in (2, 3, 4)] == [2, 3, 4]


def test_risk_follows_the_setting_on_a_1000_drawdown():
    assert [risk.default_risk_usd(1000, n) for n in (2, 3, 4)] == [500, 333, 250]
    assert risk.default_risk_usd(2000, 3) == 666  # Pro 50k preset


def test_trade_risk_capped_by_fraction_profile_and_daily_allowance():
    # half of the drawdown at most, even if a larger amount was typed
    assert risk.trade_risk_usd(700, 1.0, 1000, 2) == 500
    assert risk.trade_risk_usd(500, 0.5, 1000, 2) == 250  # Conservative
    assert risk.trade_risk_usd(500, 1.0, 1000, 2, custom_risk_usd=600) == 500  # Custom <= drawdown / N
    # Builder's $600 DLL at 1/2: after a full $500 loss only $100 is left for the day
    assert risk.trade_risk_usd(500, 1.0, 1000, 2, remaining_daily_usd=100) == 100
    assert risk.trade_risk_usd(333, 1.0, 1000, 3, remaining_daily_usd=-5) == 0


def test_lives_and_loss_run_length_per_fraction():
    dust = load_settings()["lives_dust_usd"]
    assert dust == 25
    assert [risk.loss_run_len(1000, risk.default_risk_usd(1000, n), dust) for n in (2, 3, 4)] == [2, 3, 4]
    # 1/3: two full losses leave ~$334 -> last life; a third leaves $1 (dust) -> 0
    assert risk.lives(334, 333, dust) == 1 and risk.lives(1, 333, dust) == 0
    # 1/2: one full loss puts the account on its last life
    assert risk.lives(1000, 500, dust) == 2 and risk.lives(500, 500, dust) == 1
    # 1/4: four lives, the last after three losses
    assert risk.lives(1000, 250, dust) == 4 and risk.lives(250, 250, dust) == 1


def test_loss_run_rate_overlapping_windows():
    r = [-1, -1, 2, -1, -1, -1, 1.5]  # 7 trades
    assert risk.loss_run_rate(r, 2) == pytest.approx(3 / 6)  # windows (0,1), (3,4), (4,5)
    assert risk.loss_run_rate(r, 3) == pytest.approx(1 / 5)  # (3,4,5)
    assert risk.loss_run_rate(r, 4) == 0
    assert risk.loss_run_rate([-1, -1], 3) is None
    assert risk.loss_run_rate([-0.95, -0.85], 2) == 0  # -0.85 is not a full loss (account R <= -0.9)


def test_loss_run_limit_is_0_15_for_every_fraction():
    s = load_settings()
    assert s["max_loss_run_rate"] == 0.15
    assert s["gate_account"]["max_three_loss_rate"] == s["default_max_three_loss_rate"] == 0.15  # gate keeps 1/3
    assert s["gate_account"]["risk_usd"] == risk.default_risk_usd(1000, risk.GATE_DIVISOR)
    assert risk.meets_loss_run_limit(0.15, 0.15) and not risk.meets_loss_run_limit(0.16, 0.15)
    assert risk.meets_loss_run_limit(None, 0.15)


def test_raise_needs_the_typed_confirmation():
    assert risk.is_raise(3, 2) and not risk.is_raise(3, 4) and not risk.is_raise(3, 3)
    assert risk.confirmation_text("mffur1", 2) == "mffur1 1/2"
    assert risk.confirm_raise("mffur1", 2, " mffur1 1/2 ")
    assert not risk.confirm_raise("mffur1", 2, "mffur1 1/3")
    assert not risk.confirm_raise("mffur1", 2, "yes")


def test_config_loader_refuses_a_fraction_outside_the_range(tmp_path):
    (tmp_path / "accounts.yaml").write_text(
        "accounts:\n  - {name: a1, max_risk_fraction_divisor: 2}\n  - {name: a2}\n", encoding="utf-8")
    assert [a["name"] for a in load_accounts(tmp_path)] == ["a1", "a2"]
    (tmp_path / "accounts.yaml").write_text("accounts:\n  - {name: a3, max_risk_fraction_divisor: 2.5}\n",
                                            encoding="utf-8")
    with pytest.raises(ConfigError, match="a3"):
        load_accounts(tmp_path)
