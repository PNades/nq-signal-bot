from datetime import date

import pytest

from nqbot.months import LedgerMissing, ledger, month_index, require_development

S = {"months": {i + 1: f"{2025 + (9 + i) // 12}-{(9 + i) % 12 + 1:02d}" for i in range(12)}}


def test_ledger_format_and_lookup():
    assert ledger(S)[1] == ["2025-10"] and ledger(S)[12] == ["2026-09"]
    assert month_index(date(2026, 3, 17), S) == 6 and month_index(date(2027, 1, 1), S) is None


def test_development_guard():
    assert require_development(date(2026, 3, 2), S, [6]) == 6
    with pytest.raises(ValueError, match="development"):
        require_development(date(2026, 4, 1), S)  # month 7: walk-forward test month
    with pytest.raises(LedgerMissing):
        ledger({"months": None})


# --- strict validation (presd_fixes issue 6) -----------------------------------------------------------------

def test_merged_ledger_is_valid():
    from nqbot.months import validate

    merged = {1: ["2025-10", "2025-11"], **{i: f"{2025 + (10 + i - 1) // 12}-{(10 + i - 1) % 12 + 1:02d}" for i in range(2, 13)}}
    v = validate(merged, full=True)
    assert v[1] == ["2025-10", "2025-11"] and v[2] == ["2025-12"] and v[12] == ["2026-10"]


@pytest.mark.parametrize("bad, match", [
    ({1: date(2025, 10, 1)}, "quoted"),  # an unquoted 2025-10-01 is a YAML date
    ({1: 202510}, "quoted"),
    ({1: "2025-10", 2: "2025-12"}, "no gap"),
    ({1: "2025-10", 2: "2025-10"}, "no gap"),
    ({1: "2025-10", 3: "2025-11"}, "1..2"),
    ({1: "2025-10", 2: ["2025-11", "2025-12"]}, "only month 1"),
    ({1: ["2025-10"]}, "only month 1"),
    ({1: "2025-13"}, "quoted"),
    ({"x": "2025-10"}, "integers"),
])
def test_bad_ledgers_refused(bad, match):
    from nqbot.months import LedgerInvalid

    with pytest.raises(LedgerInvalid, match=match):
        ledger({"months": bad})
    with pytest.raises(LedgerMissing):  # every caller that refuses a missing ledger refuses a bad one
        ledger({"months": bad})


def test_settings_file_needs_all_12_months(tmp_path):
    from nqbot.config import ConfigError, load_settings
    from nqbot.months import LedgerInvalid, validate

    with pytest.raises(LedgerInvalid, match="1..12"):
        validate({1: "2025-10"}, full=True)
    (tmp_path / "settings.yaml").write_text("months: {1: \"2025-10\", 2: \"2025-11\"}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="1..12"):
        load_settings(tmp_path)
    (tmp_path / "settings.yaml").write_text("months: {1: 2025-10-01}\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(tmp_path)
