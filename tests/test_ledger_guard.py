"""The Performance page's ledger guard (SPEC §9b item 6, §7 rule 2b; the unit test step 14 requires): months 7-9
before the step-16 record, sealed months before the step-20 record, and months 7-9 (and sealed months) at trade or day
level at any time are refused, including through the CSV export."""
import json
from datetime import date

import pytest

from nqbot.performance import export, guard
from nqbot.performance.ledger import Trade

SETTINGS = {"months": {1: "2025-10", 2: "2025-11", 3: "2025-12", 4: "2026-01", 5: "2026-02", 6: "2026-03",
                       7: "2026-04", 8: "2026-05", 9: "2026-06", 10: "2026-07", 11: "2026-08", 12: "2026-09"}}
DEV, WF, SEALED, AFTER, BEFORE = date(2026, 1, 14), date(2026, 5, 13), date(2026, 8, 12), date(2026, 10, 14), date(2025, 9, 10)


def trade(day: date, source: str = "backtest", tid: str | None = None) -> Trade:
    return Trade(id=tid or f"{source}-{day}", account="mffur1", source=source, date=day, entry_time="10:00:00",
                 exit_time="10:20:00", direction="long", style="d", minis=2, micros=0, entry=21000.0, exit=21010.0,
                 net_usd=390.6, r=1.2, commission_usd=9.36)


def g(*passed: str) -> guard.Guard:
    return guard.Guard(SETTINGS, frozenset(passed))


LEDGER = [trade(DEV), trade(WF), trade(SEALED), trade(AFTER), trade(BEFORE), trade(WF, "demo"), trade(AFTER, "live")]


def kept(level: guard.Level, gd: guard.Guard) -> set[str]:
    return {t.id for t in guard.apply(LEDGER, level, gd).trades}


def test_phases():
    gd = g()
    assert [gd.phase_of(t) for t in LEDGER] == ["development", "walk_forward", "sealed", "sealed", "outside", "live", "live"]


@pytest.mark.parametrize("level", ["trade", "day", "total"])
def test_before_any_gate_only_development_and_live(level):
    assert kept(level, g("6", "8b", "12")) == {f"backtest-{DEV}", f"demo-{WF}", f"live-{AFTER}"}


def test_walk_forward_totals_only_after_gate_16():
    held = guard.apply(LEDGER, "total", g("12")).held
    assert held["walk_forward"]["gate"] == "16" and held["sealed"]["gate"] == "20"
    gd = g("12", "16")
    assert f"backtest-{WF}" in kept("total", gd)
    # never at trade or day level, gate or not
    assert f"backtest-{WF}" not in kept("day", gd) and f"backtest-{WF}" not in kept("trade", gd)
    assert guard.apply(LEDGER, "day", gd).held["walk_forward"]["gate"] is None


def test_sealed_totals_only_after_gate_20_and_never_day_or_trade():
    assert f"backtest-{SEALED}" not in kept("total", g("16"))
    gd = g("16", "20")
    assert {f"backtest-{SEALED}", f"backtest-{AFTER}"} <= kept("total", gd)
    for level in ("trade", "day"):
        assert not ({f"backtest-{SEALED}", f"backtest-{AFTER}"} & kept(level, gd))


def test_before_the_window_never():
    assert f"backtest-{BEFORE}" not in kept("total", g("16", "20"))


def test_explicit_requests_refused():
    with pytest.raises(guard.Refused) as e:
        guard.require(g("12"), "total", phases=["walk_forward"])
    assert e.value.gate == "16"
    with pytest.raises(guard.Refused) as e:
        guard.require(g("16"), "total", phases=["sealed"])
    assert e.value.gate == "20"
    for level in ("trade", "day"):
        with pytest.raises(guard.Refused):
            guard.require(g("16", "20"), level, phases=["walk_forward"])
        with pytest.raises(guard.Refused):
            guard.require(g("16", "20"), level, days=[WF])
        with pytest.raises(guard.Refused):
            guard.require(g("16", "20"), level, days=[AFTER])
    guard.require(g(), "trade", phases=["development"], days=[DEV])   # allowed: no exception
    guard.require(g("16"), "total", phases=["walk_forward"])


def test_csv_export_goes_through_the_guard():
    with pytest.raises(guard.Refused):
        export.csv_text(LEDGER, g("16", "20"), phases=["walk_forward"])
    with pytest.raises(guard.Refused):
        export.csv_text(LEDGER, g("16", "20"), days=[SEALED])
    text, held = export.csv_text(LEDGER, g("16", "20"))
    ids = {line.split(",")[0] for line in text.splitlines()[1:]}
    assert ids == {f"backtest-{DEV}", f"demo-{WF}", f"live-{AFTER}"}
    assert set(held) == {"walk_forward", "sealed", "outside"}


def test_gate_record(tmp_path):
    p = tmp_path / "gate_record.json"
    assert guard.gates_passed(p) == frozenset()                  # missing: nothing passed
    p.write_text("{not json", encoding="utf-8")
    assert guard.gates_passed(p) == frozenset()                  # unreadable: nothing passed
    p.write_text(json.dumps({"gates": [{"gate": "6"}, {"gate": "16"}]}), encoding="utf-8")
    assert guard.gates_passed(p) == {"6", "16"}
    assert guard.Guard.from_files(SETTINGS, p).why_not("walk_forward", "total") is None


def test_the_committed_record_has_no_gate_16_or_20_yet():
    passed = guard.gates_passed()
    assert "12" in passed and not ({"16", "20"} & passed)
