"""Keep-awake and the powercfg warnings (presd_fixes issue 11, SPEC §11 Overnight jobs)."""

import pytest

from nqbot import power

GOOD = "    Current AC Power Setting Index: 0x00000000\n    Current DC Power Setting Index: 0x00000384\n"


def answers(**bad):
    """powercfg text per setting GUID: 0 everywhere, except the CHECKS named in `bad` (by index) -> value or None."""
    by_guid = {c[2]: bad.get(f"c{i}", 0) for i, c in enumerate(power.CHECKS)}

    def query(subgroup, setting):
        v = by_guid[setting]
        return None if v is None else (f"    Current AC Power Setting Index: 0x{v:08x}\n"
                                        "    Current DC Power Setting Index: 0x00000000\n")

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


def test_non_english_powercfg_is_read_by_position():
    german = ("    Aktueller Wechselstrom-Energieeinstellungsindex: 0x00000e10\n"
              "    Aktueller Gleichstrom-Energieeinstellungsindex: 0x00000000\n")
    w = power.power_warnings(lambda sub, setting: german if setting == power.CHECKS[0][2] else GOOD)
    assert len(w) == 1 and "value 3600" in w[0]


def test_keep_awake_nests_and_every_command_holds_it(monkeypatch, no_power_calls):
    """Owner 2026-10-11: every nqbot command holds it (a screen-off Modern Standby dropped the SSD under step8b);
    `nqbot run` / step7 holding it again inside must not release it early."""
    from nqbot import cli

    monkeypatch.setattr(power.os, "name", "nt")
    with power.keep_awake():
        with power.keep_awake():
            pass
        assert no_power_calls == [power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED]  # still held
    assert no_power_calls[-1] == power.ES_CONTINUOUS and len(no_power_calls) == 2
    seen = []
    monkeypatch.setattr(cli, "_main", lambda argv: seen.append(list(no_power_calls)) or 0)
    assert cli.main(["status"]) == 0 and seen[0][-1] == power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED
