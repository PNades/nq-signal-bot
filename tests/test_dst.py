"""Daylight-saving switches (owner 2026-11-02): the clocks went back at 02:00 on Sunday 2025-11-02 (EDT -> EST) and
go forward at 02:00 on Sunday 2026-03-08 (EST -> EDT), both inside the window.

What must hold across both switches (SPEC §1.2, §3.2; feature_spec F0.2):
- RTH is 09:30-16:00 ET on every side of a switch (13:30-20:00 UTC in EDT, 14:30-21:00 UTC in EST).
- The CME day [18:00 ET of the previous day, 17:00 ET) is always 23 real hours: a switch falls at 02:00 on a Sunday,
  while Globex is closed (Friday 17:00 to Sunday 18:00), so no CME day contains one. The 25-hour (2025-11-02) and
  23-hour (2026-03-08) days are the Sunday calendar days; the weekend gap is 50 h and 48 h.
- The flat-by (15:59 ET), the last entry (15:45 ET) and news blackouts stay at their ET wall times.
- `nqbot run --until` and the runner's chunk timing work in real time across 02:00 of a switch night.
The scheduled tasks are checked in tests/test_register_tasks_pc.py (local wall-time triggers) and the machine's
automatic daylight-saving setting in tests/test_machine_checks.py.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone

import numpy as np
import pytest

from nqbot import calendar as cal_mod
from nqbot.clients.databento_contracts import cme_day_span
from nqbot.clients.databento_pull import mbp1_span
from nqbot.clients.skylit_pull import session_windows
from nqbot.config import load_settings
from nqbot.features import calendar_flags as cf
from nqbot.features import session_tape as st
from nqbot.jobs.dummy import DummyJob
from nqbot.jobs.runner import ET, parse_until, run_job, state_path

S = load_settings()
UTC = timezone.utc
H = 3600 * st.NS

FALL_FRI, FALL_MON = date(2025, 10, 31), date(2025, 11, 3)        # EDT, then EST
SPRING_FRI, SPRING_MON = date(2026, 3, 6), date(2026, 3, 9)       # EST, then EDT
EDT_DAYS, EST_DAYS = (FALL_FRI, SPRING_MON), (FALL_MON, SPRING_FRI)


def iso(ns: int) -> str:
    return datetime.fromtimestamp(ns / st.NS, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def hm_utc(d: datetime) -> str:
    return d.astimezone(UTC).strftime("%H:%M")


def cal(events=(), early=()):
    return {"events": [{"date": d, "time_et": t, "name": n, "blackout": True} for d, t, n in events],
            "early_closes": [{"date": d, "early_close_et": t} for d, t in early], "holidays": [], "opex": [],
            "quarter_ends": [], "live_extra": []}


def weekdays(a: date, b: date) -> list[date]:
    return [a + timedelta(days=i) for i in range((b - a).days + 1) if (a + timedelta(days=i)).weekday() < 5]


# --- RTH -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("day,open_z,close_z", [
    (FALL_FRI, "2025-10-31T13:30:00Z", "2025-10-31T20:00:00Z"),
    (FALL_MON, "2025-11-03T14:30:00Z", "2025-11-03T21:00:00Z"),
    (SPRING_FRI, "2026-03-06T14:30:00Z", "2026-03-06T21:00:00Z"),
    (SPRING_MON, "2026-03-09T13:30:00Z", "2026-03-09T20:00:00Z")])
def test_rth_is_0930_to_1600_et_on_both_sides_of_each_switch(day, open_z, close_z):
    c = cal()
    assert (iso(st.rth_open_ns(day)), iso(st.rth_close_ns(day, c))) == (open_z, close_z)
    assert st.rth_close_ns(day, c) - st.rth_open_ns(day) == int(6.5 * H)
    assert st.sec_after_open(day, st.et_ns(day, time(10, 0))) == 1800.0          # 10:00 ET is 30 min in, every day
    clk = cf.clocks_at(st.et_ns(day, time(15, 0)), day, c, S)
    assert clk["minutes_since_open"] == 330.0 and clk["minutes_to_close"] == 60.0


def test_skylit_and_mbp1_windows_follow_et():
    for day in EDT_DAYS + EST_DAYS:
        w = session_windows(day, cal())
        summer = day in EDT_DAYS
        assert hm_utc(w[0].start) == ("13:30" if summer else "14:30")
        assert hm_utc(w[-1].end) == ("20:00" if summer else "21:00")
        assert all(x.start.astimezone(ET).date() == day for x in w)
        s, e = mbp1_span(day, cal())
        assert (hm_utc(s), hm_utc(e)) == (("13:25", "20:05") if summer else ("14:25", "21:05"))


# --- the CME day ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("day,start_z,end_z", [
    (FALL_FRI, "2025-10-30T22:00:00Z", "2025-10-31T21:00:00Z"),
    (FALL_MON, "2025-11-02T23:00:00Z", "2025-11-03T22:00:00Z"),     # opens 18:00 EST, the evening after the switch
    (SPRING_FRI, "2026-03-05T23:00:00Z", "2026-03-06T22:00:00Z"),
    (SPRING_MON, "2026-03-08T22:00:00Z", "2026-03-09T21:00:00Z")])  # opens 18:00 EDT
def test_cme_day_boundaries_on_the_switch_weeks(day, start_z, end_z):
    assert (iso(st.cme_start_ns(day)), iso(st.cme_end_ns(day))) == (start_z, end_z)
    assert iso(st.week_start_ns(day)) == iso(st.cme_start_ns(day - timedelta(days=day.weekday())))


def test_every_cme_day_is_23_hours_and_the_switch_falls_on_the_weekend():
    for day in weekdays(date(2025, 10, 27), date(2025, 11, 7)) + weekdays(date(2026, 3, 2), date(2026, 3, 13)):
        assert st.cme_end_ns(day) - st.cme_start_ns(day) == 23 * H, day
    # the Sunday calendar days carry the extra / missing hour, and so does the weekend gap
    assert st.et_ns(date(2025, 11, 3), time(0)) - st.et_ns(date(2025, 11, 2), time(0)) == 25 * H
    assert st.et_ns(date(2026, 3, 9), time(0)) - st.et_ns(date(2026, 3, 8), time(0)) == 23 * H
    assert st.cme_start_ns(FALL_MON) - st.cme_end_ns(FALL_FRI) == 50 * H
    assert st.cme_start_ns(SPRING_MON) - st.cme_end_ns(SPRING_FRI) == 48 * H
    # the weekly anchor (Sunday 18:00 ET) is the Monday's CME open on both switch weeks
    assert iso(st.week_start_ns(date(2025, 11, 7))) == "2025-11-02T23:00:00Z"
    assert iso(st.week_start_ns(date(2026, 3, 13))) == "2026-03-08T22:00:00Z"


def test_the_feature_tape_and_the_databento_pull_use_the_same_cme_day():
    # two implementations of F0.2 (features/session_tape.py and clients/databento_contracts.py) agree on every
    # weekday from the window's start to past the spring switch
    for day in weekdays(date(2025, 9, 1), date(2026, 4, 30)):
        start, end = cme_day_span(day)
        assert int(start.timestamp()) * st.NS == st.cme_start_ns(day), day
        assert int(end.timestamp()) * st.NS == st.cme_end_ns(day), day


def test_bars_align_to_the_et_clock_after_a_switch():
    # five-minute bars on the Monday after each switch start at 09:30 and 09:35 ET (whole-hour offsets keep the
    # UTC-aligned bar grid on the ET clock)
    for day in (FALL_MON, SPRING_MON):
        ts = [st.et_ns(day, time(9, 30, 10)), st.et_ns(day, time(9, 34, 59)), st.et_ns(day, time(9, 35, 1))]
        tape = st.Tape.from_arrays(day, ts, [100.0, 101.0, 102.0])
        b = st.bars(tape, 5)
        assert [datetime.fromtimestamp(int(x) / st.NS, ET).strftime("%H:%M") for x in b.start] == ["09:30", "09:35"]


# --- flat-by, last entry, blackouts --------------------------------------------------------------------------------

@pytest.mark.parametrize("day", EDT_DAYS + EST_DAYS)
def test_flat_by_and_last_entry_stay_at_their_et_times(day):
    c = cal()
    fb, last = cf.flat_by(day, c, S), cf.entry_end(day, c, S)
    assert fb.astimezone(ET).strftime("%H:%M") == "15:59" and last.astimezone(ET).strftime("%H:%M") == "15:45"
    assert hm_utc(fb) == ("19:59" if day in EDT_DAYS else "20:59")
    clk = cf.clocks_at(st.et_ns(day, time(15, 0)), day, c, S)
    assert clk["minutes_to_horizon_end"] == 59.0                                  # 15:00 -> 15:59 ET


def test_flat_by_on_an_early_close_after_the_fall_switch():
    d = date(2025, 11, 28)                                                         # EST, 13:00 early close
    c = cal(early=[(d, "13:00")])
    assert cf.flat_by(d, c, S).astimezone(UTC).strftime("%H:%M") == "17:59"       # 12:59 ET
    assert iso(st.rth_close_ns(d, c)) == "2025-11-28T18:00:00Z"


@pytest.mark.parametrize("day", EDT_DAYS + EST_DAYS)
def test_news_blackouts_stay_at_their_et_times(day):
    c = cal(events=[(day, "08:30", "CPI"), (day, "14:00", "FOMC Rate Decision")])
    spans = cal_mod.blackout_windows(c, day, before_min=S["blackout_before_min"], after_min=S["blackout_after_min"],
                                     lead_sec=S["news_flatten_lead_sec"])
    assert [(a.astimezone(ET).strftime("%H:%M:%S"), b.astimezone(ET).strftime("%H:%M")) for a, b in spans] == \
        [("07:59:30", "09:30"), ("13:29:30", "15:00")]
    assert int(spans[0][1].timestamp()) * st.NS == st.rth_open_ns(day)          # the 08:30 window ends at the open
    clk = cf.clocks_at(st.et_ns(day, time(13, 0)), day, c, S)
    assert clk["minutes_to_horizon_end"] == 29.5


# --- nqbot run --until and the runner's clock ----------------------------------------------------------------------

def test_until_0730_on_the_switch_nights():
    fall_eve = datetime(2025, 11, 1, 22, 0, tzinfo=ET)                            # Saturday 22:00 EDT
    t = parse_until("07:30", fall_eve)
    assert t.astimezone(UTC).isoformat() == "2025-11-02T12:30:00+00:00"          # 07:30 EST
    assert t.timestamp() - fall_eve.timestamp() == 10.5 * 3600                     # the night is an hour longer
    spring_eve = datetime(2026, 3, 7, 22, 0, tzinfo=ET)                           # Saturday 22:00 EST
    t = parse_until("07:30", spring_eve)
    assert t.astimezone(UTC).isoformat() == "2026-03-08T11:30:00+00:00"          # 07:30 EDT
    assert t.timestamp() - spring_eve.timestamp() == 8.5 * 3600
    # the same from a UTC "now"
    assert parse_until("07:30", datetime(2025, 11, 2, 2, 0, tzinfo=UTC)).astimezone(UTC).hour == 12


def test_until_inside_the_repeated_hour_is_never_in_the_past():
    # 01:15 EST on 2025-11-02 (the second 01:15; 06:15 UTC): --until 01:30 is the second 01:30 (06:30 UTC), not the
    # first (05:30 UTC, already past); --until 01:10 is tomorrow's
    now = datetime.fromtimestamp(datetime(2025, 11, 2, 6, 15, tzinfo=UTC).timestamp(), ET)
    assert now.fold == 1 and now.strftime("%H:%M") == "01:15"
    assert parse_until("01:30", now).astimezone(UTC).isoformat() == "2025-11-02T06:30:00+00:00"
    assert parse_until("01:10", now).astimezone(UTC).isoformat() == "2025-11-03T06:10:00+00:00"
    first = datetime.fromtimestamp(datetime(2025, 11, 2, 5, 15, tzinfo=UTC).timestamp(), ET)   # 01:15 EDT
    assert parse_until("01:30", first).astimezone(UTC).isoformat() == "2025-11-02T05:30:00+00:00"


class RealClock:
    """Real elapsed time shown as ET (as `datetime.now(ET)` does: the fold is set in the repeated hour)."""

    def __init__(self, start: datetime):
        self.ts = start.timestamp()

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.ts, ET)

    def sleep(self, sec: float) -> None:
        self.ts += sec


@pytest.mark.parametrize("start_utc", [datetime(2025, 11, 2, 5, 20, tzinfo=UTC),    # 01:20 EDT, clocks go back
                                       datetime(2026, 3, 8, 6, 20, tzinfo=UTC)])    # 01:20 EST, clocks go forward
def test_chunks_across_0200_of_a_switch_night_are_timed_in_real_seconds(settings, paths, log, start_utc):
    # five 20-minute chunks from 01:20 run through 02:00; each must record 1,200 s (wall-clock subtraction gave
    # -2,400 s or 4,800 s for the one across the switch) and the --until check must use real time
    clock = RealClock(start_utc)
    until = parse_until("07:30", clock.now())
    r = run_job(DummyJob(5, 1200), settings, paths, until=until, log=log, now=clock.now, sleep=clock.sleep)
    assert (r.status, r.done) == ("complete", 5)
    state = json.loads(state_path(paths, "dummy").read_text())
    secs = [c["sec"] for c in state["done"].values()]
    assert len(secs) == 5 and all(s == pytest.approx(1200.0) for s in secs)


def test_until_stops_the_run_in_real_time_on_the_fall_night(settings, paths, log):
    # from 01:20 EDT with --until 01:50 (the first 01:50, 30 real minutes away), 20-minute chunks: the first ends at
    # 01:40 EDT, the second would end 02:00 EDT = 01:00 EST, which wall-clock comparison would call "before 01:50"
    clock = RealClock(datetime(2025, 11, 2, 5, 20, tzinfo=UTC))
    until = parse_until("01:50", clock.now())
    assert until.astimezone(UTC).isoformat() == "2025-11-02T05:50:00+00:00"
    r = run_job(DummyJob(5, 1200), settings, paths, until=until, log=log, now=clock.now, sleep=clock.sleep)
    assert (r.status, r.done) == ("until", 1)
