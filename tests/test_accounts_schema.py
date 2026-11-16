"""The account schema (SPEC §6.7, §9b): names, the webhook variable name, no secrets anywhere (owner 2026-11-15)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nqbot.accounts.presets import builtin_presets
from nqbot.accounts.schema import Account, SecretRefused, refuse_secrets

BASE = {**next(p for p in builtin_presets() if p.id == "mffu_rapid_eod_25k_evaluation").fields, "name": "mffur1"}


def test_a_preset_account_parses_and_round_trips():
    a = Account.model_validate({**BASE, "traderspost_webhook_env": "TP_WEBHOOK_MFFUR1",
                                "payouts": [{"date": "2026-11-02", "amount_usd": 500}]})
    d = a.model_dump(mode="json")
    assert d["payouts"][0]["date"] == "2026-11-02" and Account.model_validate(d) == a


@pytest.mark.parametrize("name", ["MFFUR1", "mffu r1", "mffu-r1", "", "mffü1"])
def test_names_are_lowercase_letters_and_digits(name):
    with pytest.raises(ValidationError):
        Account.model_validate({**BASE, "name": name})


@pytest.mark.parametrize("value", [
    "https://webhooks.traderspost.io/trading/webhook/1234/abcd",
    "tp_webhook_mffur1", "TP_WEBHOOK_", "MY_WEBHOOK",
])
def test_only_the_webhook_variable_name_is_stored(value):
    with pytest.raises((ValidationError, SecretRefused)):
        Account.model_validate({**BASE, "traderspost_webhook_env": value})


@pytest.mark.parametrize("field, value", [
    ("firm", "https://my.firm.example/login"),
    ("payout_cap_group", "sk-ant-api03abcdefghijklmnopqrstuvwxyz0123"),
    ("firm", "owner@example.com"),
    ("payout_cap_group", "8f14e45f-ceea-4672-9b6c-1234567890ab"),
])
def test_secrets_are_refused_in_every_text_field(field, value):
    with pytest.raises((ValidationError, SecretRefused)):
        Account.model_validate({**BASE, field: value})


def test_refuse_secrets_reaches_nested_values_and_passes_ordinary_ids():
    refuse_secrets({"presets": [{"id": "mffu_rapid_eod_25k_after_first_payout", "label": "MFFU Rapid EOD 25k"}]})
    with pytest.raises(SecretRefused):
        refuse_secrets({"accounts": [{"notes": ["see www.example.com"]}]})


def test_risk_fraction_and_unknown_fields():
    with pytest.raises(ValidationError):
        Account.model_validate({**BASE, "max_risk_fraction_divisor": 2.5})
    with pytest.raises(ValidationError):
        Account.model_validate({**BASE, "password": "x"})   # no field outside the schema
