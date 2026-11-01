"""Calendar flags and session clocks (feature_spec F2.22; SPEC §1.2, §9)."""

from datetime import date, time

from pytest import approx

from nqbot import calendar as cal_mod
from nqbot.config import load_settings
from nqbot.features import calendar_flags as cf
from nqbot.features import session_tape as st

S = load_settings()


def cal(events=(), early=(), holidays=(), opex=(), quarter_ends=()):
    return {"events": [{"date": d, "time_et": t, "name": n, "blackout": b} for d, t, n, b in events],
            "early_closes": [{"date": d, "early_close_et": t} for d, t in early],
            "holidays": [{"date": d} for d in holidays], "opex": list(opex), "quarter_ends": list(quarter_ends),
            "live_extra": []}


def test_f222_fomc_horizon_end_and_in_session_release():
    d = date(2026, 3, 18)
    c = cal(events=[(d, "14:00", "FOMC Rate Decision", True)])
    clk = cf.clocks_at(st.et_ns(d, time(13, 0)), d, c, S)
    assert clk["minutes_to_horizon_end"] == approx(29.5)            # 14:00 − 30 min − 30 s = 13:29:30
    assert cf.clocks_at(st.et_ns(d, time(13, 45)), d, c, S)["minutes_to_horizon_end"] == 0.0   # inside the window
    assert cf.clocks_at(st.et_ns(d, time(15, 30)), d, c, S)["minutes_to_horizon_end"] == approx(29.0)  # flat-by
    flags = cf.day_flags(d, c, S)
    assert flags["fomc_day"] and flags["in_session_release_today"] and not flags["cpi_day"]
    pre = cal(events=[(d, "08:30", "CPI", True)])                   # 08:30 window ends 09:30: not in session
    f2 = cf.day_flags(d, pre, S)
    assert f2["cpi_day"] and not f2["in_session_release_today"]


def test_f222_half_day_flat_by_and_close():
    d = date(2025, 11, 28)
    c = cal(early=[(d, "13:00")])
    clk = cf.clocks_at(st.et_ns(d, time(12, 0)), d, c, S)
    assert clk["minutes_to_close"] == 60.0 and clk["minutes_to_horizon_end"] == 59.0
    assert clk["minutes_since_open"] == 150.0
    assert cf.day_flags(d, c, S)["half_day"]


def test_f222_opex_and_quarter_end():
    c = cal(opex=[date(2026, 3, 20)], quarter_ends=[date(2026, 3, 31)])
    f = cf.day_flags(date(2026, 3, 16), c, S)
    assert f["opex_week"] and f["days_to_monthly_expiry"] == 4
    assert not cf.day_flags(date(2026, 3, 23), c, S)["opex_week"]
    assert cf.day_flags(date(2026, 3, 31), c, S)["quarter_end_day"]
    # a quarter end on a Saturday: the last trading day before it
    c2 = cal(quarter_ends=[date(2027, 7, 3)])
    assert cf.day_flags(date(2027, 7, 2), c2, S)["quarter_end_day"]
    assert cf.day_flags(date(2027, 6, 30), c2, S)["days_to_quarter_end"] == 2


def test_events_list_equals_the_calendar_known_at_each_preopen():
    """F2.22 reads the calendar as known at `preopen_start`; the flags use `events` (the actual dates, as the
    blackouts do). That is the same list only if every rescheduled release was announced before its scheduled day
    (`announced_implicit` when given, else `announced`). If this fails, stop and ask the owner (rule 7)."""
    c = cal_mod.load()
    late = [(r["name"], r["scheduled"]) for r in c.get("schedule_changes") or []
            if (r.get("announced_implicit") or r.get("announced")) is None
            or (r.get("announced_implicit") or r.get("announced")) >= r["scheduled"]]
    assert late == []
