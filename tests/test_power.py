"""Keep-awake and the powercfg warnings (presd_fixes issue 11, SPEC §11 Overnight jobs)."""

import pytest

from nqbot import power

GOOD = "    Current AC Power Setting Index: 0x00000000\n    Current DC Power Setting Index: 0x00000384\n"


def answers(**bad):
    """powercfg text per setting GUID: 0 everywhere, except the CHECKS named in `bad` (by index) -> value or None."""
    by_guid = {c[2]: bad.get(f"c{i}", 0) for i, c in enumerate(power.CHECKS)}

    def query(subgroup, setting):
        v = by_guid[setting]
        return None if v is None else f"    Current AC Power Setting Index: 0x{v:08x}\n"

    return query


def test_all_fine():
    assert power.power_warnings(answers()) == []


@pytest.mark.parametrize("i, value", [(0, 3600), (1, 10800), (2, 1), (3, 1)])
def test_each_bad_setting_warns(i, value):
    w = power.power_warnings(answers(**{f"c{i}": value}))
    assert len(w) == 1 and power.CHECKS[i][0] in w[0] and power.CHECKS[i][4] in w[0]


def test_unreadable_setting_says_check_by_hand():
    w = power.power_warnings(answers(c2=None))
    assert len(w) == 1 and "couldn't read lid close action" in w[0]


def test_not_windows_no_checks_no_calls(monkeypatch, no_power_calls):
    monkeypatch.setattr(power.os, "name", "posix")
    assert power.power_warnings() == []
    with power.keep_awake():
        pass
    assert no_power_calls == []


def test_keep_awake_cleared_after_an_error(monkeypatch, no_power_calls):
    monkeypatch.setattr(power.os, "name", "nt")
    with pytest.raises(RuntimeError):
        with power.keep_awake():
            raise RuntimeError("chunk failed")
    assert no_power_calls == [power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED, power.ES_CONTINUOUS]
