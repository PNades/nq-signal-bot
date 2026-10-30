"""Front contract per CME trading day (the previous-session volume rule), roll days and the roll spread (SPEC §3.2;
feature_spec F0.2, F0.4)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pytest

from nqbot.clients.databento_client import DatabentoClient
from nqbot.clients.databento_contracts import (Roll, VolumeMissing, cme_day_span, cme_roll_date, expiry,
                                               front_by_volume, micro_symbol, next_quarterly, roll_days, roll_shift,
                                               roll_spread, seed_front, unexpired_quarterlies)
from tests.databento_fakes import FakeHistorical, et_ns


def utc(t: datetime) -> datetime:
    return t.astimezone(timezone.utc)


def test_cme_day_span_winter_summer_and_dst_sunday():
    start, end = cme_day_span(date(2025, 12, 16))  # EST: UTC-5
    assert (utc(start).isoformat(), utc(end).isoformat()) == ("2025-12-15T23:00:00+00:00", "2025-12-16T22:00:00+00:00")
    start, end = cme_day_span(date(2026, 6, 16))  # EDT: UTC-4
    assert (utc(start).hour, utc(end).hour) == (22, 21)
    # Monday 2026-03-09: the CME day starts Sunday 18:00 EDT (clocks went forward that morning), 23 h long.
    start, end = cme_day_span(date(2026, 3, 9))
    assert utc(start).isoformat() == "2026-03-08T22:00:00+00:00"
    assert (end - start).total_seconds() == 23 * 3600
    # Monday after the fall-back Sunday (2025-11-02): starts 18:00 EST.
    start, _ = cme_day_span(date(2025, 11, 3))
    assert utc(start).isoformat() == "2025-11-02T23:00:00+00:00"


# --- the front rule (owner 2026-10-30, SPEC §13 Q-frontroll) ---------------------------------------------------

DEC = [date(2025, 12, d) for d in (8, 9, 10, 11, 12, 15, 16, 17, 18, 19)]  # Mon 8 .. Fri 19, no holiday


def vols(old: dict, new: dict, root: str = "NQ", o: str = "NQZ5", n: str = "NQH6", far: str = "NQM6") -> dict:
    """CME-day volumes by session for the old and new contract (the one after them: always 1)."""
    days = set(old) | set(new)
    return {root: {o: old, n: new, far: {d: 1 for d in days}}}


def test_quarterly_helpers():
    assert next_quarterly("NQZ5", "NQ") == "NQH6" and next_quarterly("ESU6", "ES") == "ESZ6"
    assert next_quarterly("NQZ9", "NQ") == "NQH0"
    assert expiry("NQZ5", "NQ", date(2025, 12, 1)) == date(2025, 12, 19)
    assert expiry("NQH6", "NQ", date(2025, 12, 1)) == date(2026, 3, 20)
    assert cme_roll_date("NQZ5", "NQ", date(2025, 12, 1)) == date(2025, 12, 11)  # the Thursday 8 days before
    assert unexpired_quarterlies("NQ", date(2025, 12, 19), 2) == ["NQZ5", "NQH6"]  # expiry day still counts
    assert unexpired_quarterlies("ES", date(2025, 12, 22), 3) == ["ESH6", "ESM6", "ESU6"]


def test_switch_on_the_first_day_the_new_contracts_previous_session_volume_is_larger():
    # NQH6 first out-trades NQZ5 on Thursday 12-11: Friday 12-12 is the first session on NQH6 (from 18:00 Thursday).
    old = {d: 500 for d in [date(2025, 12, 5)] + DEC}
    new = {d: (600 if d >= date(2025, 12, 11) else 100) for d in [date(2025, 12, 5)] + DEC}
    new[date(2025, 12, 10)] = 500  # a tie keeps the front
    front = front_by_volume(vols(old, new), DEC, {"NQ": "NQZ5"})
    assert [front["NQ"][d] for d in DEC[:5]] == ["NQZ5"] * 4 + ["NQH6"]
    assert roll_days(front) == [Roll("NQ", date(2025, 12, 12), date(2025, 12, 11), "NQZ5", "NQH6")]


def test_no_switch_back_when_the_volumes_cross_again():
    old = {d: 500 for d in [date(2025, 12, 5)] + DEC}
    new = {d: 100 for d in [date(2025, 12, 5)] + DEC}
    new[date(2025, 12, 9)] = 600  # one heavy day for the new contract, then the old one leads again
    front = front_by_volume(vols(old, new), DEC, {"NQ": "NQZ5"})
    assert front["NQ"][date(2025, 12, 9)] == "NQZ5"
    assert all(front["NQ"][d] == "NQH6" for d in DEC[2:])  # from 12-10 on, never back to NQZ5
    assert len(roll_days(front)) == 1


def test_a_holiday_globex_day_does_not_trigger_a_switch():
    # Thanksgiving 2025-11-27 is not a session: its Globex day keeps NQZ5 even though NQH6 out-trades it that day;
    # Friday 11-28 is decided by Wednesday 11-26 (the previous full session), Monday 12-01 by Friday 11-28.
    days = [date(2025, 11, 25), date(2025, 11, 26), date(2025, 11, 27), date(2025, 11, 28), date(2025, 12, 1)]
    old = {date(2025, 11, 24): 500, date(2025, 11, 25): 500, date(2025, 11, 26): 500, date(2025, 11, 27): 10,
           date(2025, 11, 28): 100, date(2025, 12, 1): 100}
    new = {date(2025, 11, 24): 100, date(2025, 11, 25): 100, date(2025, 11, 26): 100, date(2025, 11, 27): 900,
           date(2025, 11, 28): 600, date(2025, 12, 1): 600}
    front = front_by_volume(vols(old, new), days, {"NQ": "NQZ5"})
    assert front["NQ"][date(2025, 11, 27)] == "NQZ5" and front["NQ"][date(2025, 11, 28)] == "NQZ5"
    assert front["NQ"][date(2025, 12, 1)] == "NQH6"
    assert roll_days(front) == [Roll("NQ", date(2025, 12, 1), date(2025, 11, 28), "NQZ5", "NQH6")]
    # A switch decided on Wednesday lands on Friday's session; the holiday between keeps the old contract.
    new[date(2025, 11, 26)] = 600
    front = front_by_volume(vols(old, new), days, {"NQ": "NQZ5"})
    assert front["NQ"][date(2025, 11, 27)] == "NQZ5" and front["NQ"][date(2025, 11, 28)] == "NQH6"
    assert roll_days(front) == [Roll("NQ", date(2025, 11, 28), date(2025, 11, 26), "NQZ5", "NQH6")]


def test_no_lookahead_day_ts_volume_never_decides_day_t():
    old = {d: 500 for d in [date(2025, 12, 5)] + DEC}
    new = {d: 100 for d in [date(2025, 12, 5)] + DEC}
    base = front_by_volume(vols(old, new), DEC, {"NQ": "NQZ5"})
    for i, day in enumerate(DEC):
        bumped = dict(new)
        bumped[day] = 10_000  # only day T's own volume changes
        front = front_by_volume(vols(old, bumped), DEC, {"NQ": "NQZ5"})
        assert front["NQ"][day] == base["NQ"][day] == "NQZ5"  # day T is unchanged ...
        if i + 1 < len(DEC):
            assert front["NQ"][DEC[i + 1]] == "NQH6"  # ... it decides T+1
    # The decision for the days up to T is the same whether or not later days are known (the live evening step).
    later = front_by_volume(vols(old, new), DEC[:4], {"NQ": "NQZ5"})
    assert later["NQ"] == {d: base["NQ"][d] for d in DEC[:4]}


def test_missing_volume_or_a_skipped_session_is_refused():
    old = {d: 500 for d in [date(2025, 12, 5)] + DEC}
    new = {d: 100 for d in [date(2025, 12, 5)] + DEC if d != date(2025, 12, 10)}
    with pytest.raises(VolumeMissing, match="NQH6 on 2025-12-10"):
        front_by_volume(vols(old, new), DEC, {"NQ": "NQZ5"})
    with pytest.raises(ValueError, match="skip the session 2025-12-10"):
        front_by_volume(vols(old, old), [d for d in DEC if d != date(2025, 12, 10)], {"NQ": "NQZ5"})


def test_seed_front_is_the_larger_of_the_two_nearest():
    v = {"NQ": {"NQZ5": {date(2025, 12, 5): 500}, "NQH6": {date(2025, 12, 5): 600}}}
    assert seed_front(v, date(2025, 12, 5)) == {"NQ": "NQH6"}
    v["NQ"]["NQH6"][date(2025, 12, 5)] = 400
    assert seed_front(v, date(2025, 12, 5)) == {"NQ": "NQZ5"}


def test_hourly_volumes_sum_the_cme_day_and_drive_the_fake_rolls(paths):
    """The stored ohlcv-1h bars of [18:00 ET D−1, 17:00 ET D) make D's volume; the fake's rolls come out of the rule."""
    from nqbot.clients.databento_store import DatabentoStore
    from nqbot.clients.databento_volume import load_volumes, pull_volumes, volume_sessions
    from tests.databento_fakes import LEAD_VOLUME, OTHER_VOLUME

    store = DatabentoStore(paths)
    fake = FakeHistorical(nq_roll=date(2025, 12, 15), es_roll=date(2025, 12, 17))
    lines: list[str] = []
    pull_volumes(DatabentoClient(fake, on_call=store.log_call), store, DEC, echo=lines.append)
    assert "total extra charge: $0.00" in "\n".join(lines)
    assert {c[2] for c in fake.downloads()} == {"NQZ5", "NQH6", "NQM6", "ESZ5", "ESH6", "ESM6"}
    sessions = volume_sessions(DEC)
    volumes = load_volumes(store, sessions)
    assert volumes["NQ"]["NQZ5"][date(2025, 12, 11)] == 23 * LEAD_VOLUME  # 23 hours: 18:00 -> 17:00
    assert volumes["NQ"]["NQH6"][date(2025, 12, 11)] == 23 * OTHER_VOLUME
    front = front_by_volume(volumes, DEC, seed_front(volumes, sessions[0]))
    assert roll_days(front) == [Roll("NQ", date(2025, 12, 15), date(2025, 12, 12), "NQZ5", "NQH6"),
                                Roll("ES", date(2025, 12, 17), date(2025, 12, 16), "ESZ5", "ESH6")]
    n = len(fake.downloads())
    pull_volumes(DatabentoClient(fake, on_call=store.log_call), store, DEC, echo=lines.append)
    assert len(fake.downloads()) == n and lines[-1] == "hourly volumes: all stored"  # never re-pulled


def test_micro_symbol():
    assert micro_symbol("NQZ5") == "MNQZ5"
    assert micro_symbol("NQH6") == "MNQH6"
    with pytest.raises(ValueError):
        micro_symbol("ESZ5")


def test_roll_spread_uses_the_last_bar_ending_by_17_00():
    prev = date(2025, 12, 12)
    roll = Roll("NQ", date(2025, 12, 15), prev, "NQZ5", "NQH6")
    # Old contract: last bar 16:59:59 (ends 17:00:00, usable) and one labelled 17:00:00 (ends 17:00:01, not usable).
    old = (np.array([et_ns(prev, 16, 59, 58), et_ns(prev, 16, 59, 59), et_ns(prev, 17, 0, 0)]),
           np.array([20001.0, 20002.0, 99999.0]))
    # New contract: thinner, its last bar before the cutoff is 16:59:40.
    new = (np.array([et_ns(prev, 16, 59, 10), et_ns(prev, 16, 59, 40), et_ns(prev, 17, 0, 0)]),
           np.array([20249.0, 20252.0, 1.0]))
    rec = roll_spread(roll, old, new)
    assert rec["old_close"] == 20002.0 and rec["new_close"] == 20252.0
    assert rec["spread"] == 250.0
    assert rec["new_bar_start_ns"] == et_ns(prev, 16, 59, 40)
    assert rec["roll_day"] == "2025-12-15"


def test_roll_spread_without_a_bar_is_an_error():
    prev = date(2025, 12, 12)
    roll = Roll("NQ", date(2025, 12, 15), prev, "NQZ5", "NQH6")
    late = (np.array([et_ns(prev, 17, 0, 0)]), np.array([1.0]))
    ok = (np.array([et_ns(prev, 16, 0, 0)]), np.array([1.0]))
    with pytest.raises(ValueError, match="NQH6"):
        roll_spread(roll, ok, late)


def test_roll_shift_f0_4_example():
    # F0.4: a carried PDH of 20,000 with spread +250 becomes 20,250 on the roll day; nothing shifts on other days.
    rolls = [{"root": "NQ", "roll_day": "2025-12-15", "spread": 250.0}]
    pdh = 20000.0
    assert pdh + roll_shift("NQ", date(2025, 12, 15), rolls) == 20250.0
    assert roll_shift("NQ", date(2025, 12, 16), rolls) == 0.0
    assert roll_shift("ES", date(2025, 12, 15), rolls) == 0.0

