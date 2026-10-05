from datetime import date, datetime, time, timedelta

import pytest

from nqbot import calendar as cal_mod
from nqbot.calendar import ET, CalendarError, blackout_windows, early_close_et, is_trading_day, releases, validate
from nqbot.config import CONFIG_DIR, load_settings

OWNER_LIST = {"NFP", "FOMC Rate Decision", "FOMC Statement", "FOMC Press Conference", "FOMC Minutes", "Fed Chair",
              "CPI", "Core PCE", "PPI", "Advance GDP", "ISM Manufacturing PMI", "ISM Services PMI", "Retail Sales"}


@pytest.fixture(scope="module")
def cal():
    return cal_mod.load()


@pytest.fixture(scope="module")
def title_map():
    return load_settings()["news_title_map"]


def test_title_map_blackout_side_is_the_owner_list(title_map):
    assert set(title_map["blackout"]) == OWNER_LIST


def test_calendar_validates(cal, title_map):
    validate(cal, title_map)


def test_window_covers_14_months_back(cal):
    assert cal["meta"]["window_from"] <= date(2025, 8, 4)
    assert cal["meta"]["window_to"] == date(2027, 12, 31)  # holidays listed through 2027
    assert all(e["date"] <= date(2027, 12, 31) for e in cal["events"])
    first = min(e["date"] for e in cal["events"])
    assert first <= date(2025, 8, 15)


def test_every_listed_release_present(cal):
    names = {e["name"] for e in cal["events"] if e["blackout"]}
    assert names == OWNER_LIST


def test_live_extra_starts_empty_and_is_live_only(cal):
    assert cal["live_extra"] == []
    fake = {**cal, "events": [], "live_extra": [
        {"date": date(2026, 10, 6), "time_et": "10:00", "ff_title": "JOLTS Job Openings", "seen_at": "x"}]}
    assert blackout_windows(fake, date(2026, 10, 6), before_min=30, after_min=60, lead_sec=30) == []
    assert len(blackout_windows(fake, date(2026, 10, 6), before_min=30, after_min=60, lead_sec=30, live=True)) == 1


def test_holidays_and_early_closes(cal):
    assert not is_trading_day(date(2025, 12, 25), cal)
    assert not is_trading_day(date(2025, 11, 27), cal)  # Thanksgiving
    assert not is_trading_day(date(2026, 1, 1), cal)
    assert is_trading_day(date(2026, 10, 5), cal)
    assert early_close_et(date(2025, 11, 28), cal) == time(13, 0)
    assert early_close_et(date(2025, 12, 24), cal) == time(13, 0)
    assert early_close_et(date(2026, 10, 5), cal) is None


def test_opex_are_third_fridays_or_the_day_before_a_holiday(cal):
    for d in cal["opex"]:
        assert is_trading_day(d, cal)
        if d.weekday() == 4:
            assert 15 <= d.day <= 21, d
        else:  # third Friday was a holiday: the trading day before it
            third_friday = d + timedelta(days=(4 - d.weekday()) % 7)
            assert 15 <= third_friday.day <= 21 and not is_trading_day(third_friday, cal), d
    assert date(2026, 6, 18) in cal["opex"]  # Juneteenth Friday 2026-06-19
    assert len(cal["opex"]) == 29  # Aug 2025 - Dec 2027


def test_blackout_false_lines_never_block(cal):
    day = date(2025, 8, 12)  # CPI + Core CPI at 08:30
    assert len(blackout_windows(cal, day, before_min=30, after_min=60, lead_sec=30)) == 1
    only_core = {"events": [e for e in cal["events"] if e["date"] == day and not e["blackout"]], "live_extra": []}
    assert only_core["events"] and blackout_windows(only_core, day, before_min=30, after_min=60, lead_sec=30) == []


def test_validate_checks_live_extra_and_date_lists(cal, title_map):
    bad = {**cal, "live_extra": [{"date": "2026-10-06", "time_et": "10:00"}], "opex": ["soon"]}
    with pytest.raises(CalendarError) as e:
        validate(bad, title_map)
    msg = str(e.value)
    assert "live_extra[0]" in msg and "ff_title" in msg and "opex[0]" in msg


def test_times_are_quoted_strings_in_the_file():
    text = (CONFIG_DIR / "calendar.yaml").read_text(encoding="utf-8")
    assert "time_et: \"10:00\"" in text and "time_et: 10:00" not in text


def test_shutdown_postponements_recorded(cal):
    # Sep-2025 jobs report: scheduled 2025-10-03, not published that day (announced before 08:30).
    nfp_days = {e["date"] for e in cal["events"] if e["name"] == "NFP"}
    assert date(2025, 10, 3) not in nfp_days
    assert date(2025, 11, 20) in nfp_days
    assert any(c["name"] == "NFP" and c["scheduled"] == date(2025, 10, 3) for c in cal["schedule_changes"])


def test_fomc_day_windows_merge():
    fake = {"events": [
        {"date": date(2026, 1, 28), "time_et": "14:00", "name": "FOMC Rate Decision", "blackout": True},
        {"date": date(2026, 1, 28), "time_et": "14:30", "name": "FOMC Press Conference", "blackout": True},
        {"date": date(2026, 1, 28), "time_et": "14:00", "name": "FOMC Statement", "blackout": True},
    ], "live_extra": []}
    w = blackout_windows(fake, date(2026, 1, 28), before_min=30, after_min=60, lead_sec=30)
    assert w == [(datetime(2026, 1, 28, 13, 29, 30, tzinfo=ET), datetime(2026, 1, 28, 15, 30, tzinfo=ET))]


def test_validate_rejects_unsourced_and_unknown(cal, title_map):
    bad = {**cal, "events": [{"date": date(2026, 1, 2), "time_et": "8:30x", "name": "Jobless Claims",
                              "blackout": True, "source": "nowhere"}]}
    with pytest.raises(CalendarError) as e:
        validate(bad, title_map)
    msg = str(e.value)
    assert "nowhere" in msg and "8:30x" in msg and "Jobless Claims" in msg


def test_releases_sorted(cal):
    r = releases(cal)
    assert r == sorted(r, key=lambda x: x.at)
