"""Front contract per CME trading day, roll days and the roll spread (SPEC §3.2; feature_spec F0.2, F0.4)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pytest

from nqbot.clients.databento_client import DatabentoClient
from nqbot.clients.databento_contracts import (Roll, cme_day_span, contract_on, front_by_day, micro_symbol,
                                               roll_days, roll_shift, roll_spread)
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


def test_resolution_through_the_fake_and_roll_days():
    client = DatabentoClient(FakeHistorical(nq_roll=date(2025, 12, 15), es_roll=date(2025, 12, 17)))
    mappings, answers = client.resolve_front({"NQ": "NQ.v.0", "ES": "ES.v.0"}, date(2025, 12, 8), date(2025, 12, 20))
    assert len(answers) == 2
    assert contract_on(mappings["NQ"], date(2025, 12, 12)) == "NQZ5"
    assert contract_on(mappings["NQ"], date(2025, 12, 15)) == "NQH6"
    days = [date(2025, 12, d) for d in (8, 9, 10, 11, 12, 15, 16, 17, 18, 19)]
    front = front_by_day(mappings, days)
    assert front["NQ"][date(2025, 12, 12)] == "NQZ5" and front["NQ"][date(2025, 12, 15)] == "NQH6"
    assert front["ES"][date(2025, 12, 16)] == "ESZ5" and front["ES"][date(2025, 12, 17)] == "ESH6"
    rolls = roll_days(front)
    assert rolls == [Roll("NQ", date(2025, 12, 15), date(2025, 12, 12), "NQZ5", "NQH6"),
                     Roll("ES", date(2025, 12, 17), date(2025, 12, 16), "ESZ5", "ESH6")]


def test_holiday_keeps_the_previous_sessions_contract():
    # Databento switches on Thursday 2025-11-27 (Thanksgiving, not a session): the holiday's Globex trades stay on
    # the old contract (owner 2026-10-05); the roll lands on Friday's session and is measured at Wednesday's 17:00,
    # so the holiday's levels are shifted with everything else carried into Friday.
    mappings = {"NQ": [(date(2025, 11, 1), date(2025, 11, 27), "NQZ5"), (date(2025, 11, 27), date(2025, 12, 31), "NQH6")]}
    days = [date(2025, 11, d) for d in (24, 25, 26, 27, 28)]
    front = front_by_day(mappings, days)
    assert front["NQ"][date(2025, 11, 27)] == "NQZ5"
    assert front["NQ"][date(2025, 11, 28)] == "NQH6"
    assert roll_days(front) == [Roll("NQ", date(2025, 11, 28), date(2025, 11, 26), "NQZ5", "NQH6")]


def test_session_without_mapping_is_an_error():
    with pytest.raises(ValueError, match="no mapping"):
        front_by_day({"NQ": [(date(2025, 12, 1), date(2025, 12, 5), "NQZ5")]}, [date(2025, 12, 5)])


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

