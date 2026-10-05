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
