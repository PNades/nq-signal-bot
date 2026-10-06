import json
from datetime import date, datetime, timedelta

import pytest

from nqbot.jobs.dummy import DummyJob
from nqbot.jobs.lock import ALL_LOCKS, DATABENTO, SKYLIT, LockHeld, lock_path
from nqbot.jobs import runner as runner_mod
from nqbot.jobs.runner import (ET, effective_max_workers, in_lock_window, next_window_start, p90,
                               parse_until, run_job, state_path)
from nqbot.jobs.status import job_lines
from nqbot.storage import GB
from nqbot.storage import MARKER


def run(job, settings, paths, log, clock, until_min=60, **kw):
    return run_job(job, settings, paths, until=clock.now() + timedelta(minutes=until_min), log=log,
                   now=clock.now, sleep=clock.sleep, **kw)


def outputs(paths):
    d = paths.data_dir / "jobs" / "dummy" / "output"
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def test_runs_all_chunks(settings, paths, log, clock):
    r = run(DummyJob(4, 10), settings, paths, log, clock)
    assert (r.status, r.done, r.total) == ("complete", 4, 4)
    assert outputs(paths) == [f"chunk-00{i}.txt" for i in range(4)]
    assert not any(lock_path(paths.root, k).exists() for k in ALL_LOCKS)


def test_stops_at_until_and_resumes(settings, paths, log, clock):
    # 10-minute chunks, 25 minutes to go: chunk 1 (no history) and 2 run; 3rd would end at 30 > 25.
    r = run(DummyJob(5, 600), settings, paths, log, clock, until_min=25)
    assert (r.status, r.done) == ("until", 2)
    r = run(DummyJob(5, 600), settings, paths, log, clock, until_min=60)
    assert (r.status, r.done, r.ran) == ("complete", 5, ["chunk-002", "chunk-003", "chunk-004"])
    state = json.loads(state_path(paths, "dummy").read_text())
    assert len(state["runs"]) == 2 and state["total"] == 5


def test_refuses_without_marker(settings, paths, log, clock, drive):
    (drive / MARKER).unlink()
    r = run(DummyJob(2, 1), settings, paths, log, clock)
    assert (r.status, r.done) == ("ssd_missing", 0)
    assert outputs(paths) == []


def test_stops_cleanly_when_ssd_unplugged_mid_chunk(settings, paths, log, clock, drive):
    class Unplug(DummyJob):
        def run_chunk(self, chunk_id, ctx):
            if chunk_id == "chunk-001":
                (drive / MARKER).unlink()
                raise OSError("device not ready")
            super().run_chunk(chunk_id, ctx)

    r = run(Unplug(3, 1), settings, paths, log, clock)
    assert (r.status, r.ran) == ("ssd_missing", ["chunk-000"])
    (drive / MARKER).write_text("")
    r = run(DummyJob(3, 1), settings, paths, log, clock)
    assert r.ran == ["chunk-001", "chunk-002"]


def test_chunk_error_with_ssd_present_propagates(settings, paths, log, clock):
    class Broken(DummyJob):
        def run_chunk(self, chunk_id, ctx):
            raise ValueError("bug")

    with pytest.raises(ValueError):
        run(Broken(1, 1), settings, paths, log, clock)


def test_low_space_refuses(settings, paths, log, clock):
    settings["min_free_gb"] = 10**9
    assert run(DummyJob(1, 1), settings, paths, log, clock).status == "low_space"


def test_lock_window_only_when_enforced(settings, paths, log):
    from tests.conftest import FakeClock

    clock = FakeClock(datetime(2026, 10, 5, 9, 0, tzinfo=ET))  # Monday 09:00
    trading = lambda d: True
    assert run(DummyJob(1, 1), settings, paths, log, clock, is_trading_day=trading).status == "complete"
    settings["ops"]["job_lock_window_enforced"] = True
    r = run(DummyJob(2, 1), settings, paths, log, clock, is_trading_day=trading)
    assert r.status == "lock_window"


def test_job_lock_held_by_other_machine(settings, paths, log, clock):
    paths.root.mkdir(parents=True)
    for kind in ALL_LOCKS:  # a job without `locks` needs both
        lock_path(paths.root, kind).write_text(json.dumps({"host": "home-pc", "pid": 1, "job": "fold"}))
        with pytest.raises(LockHeld, match=f"unlock --{kind}"):
            run(DummyJob(1, 1), settings, paths, log, clock)
        lock_path(paths.root, kind).unlink()
        assert not any(lock_path(paths.root, k).exists() for k in ALL_LOCKS)  # the other one was let go


def test_low_memory_reaches_chunks(settings, paths, log, clock):
    run(DummyJob(1, 1), settings, paths, log, clock)
    text = (paths.data_dir / "jobs" / "dummy" / "output" / "chunk-000.txt").read_text()
    assert "low_memory=True" in text


def test_parse_until_next_occurrence():
    now = datetime(2026, 10, 4, 22, 0, tzinfo=ET)
    assert parse_until("07:30", now) == datetime(2026, 10, 5, 7, 30, tzinfo=ET)
    assert parse_until("23:00", now) == datetime(2026, 10, 4, 23, 0, tzinfo=ET)


def test_p90_nearest_rank():
    assert p90([]) == 0
    assert p90([5.0]) == 5.0
    assert p90(list(range(1, 11))) == 9
    assert p90(list(range(1, 21))) == 18


def test_in_lock_window():
    w = "08:30-16:15"
    yes = lambda d: True
    no = lambda d: False
    assert in_lock_window(datetime(2026, 10, 5, 8, 30, tzinfo=ET), w, yes)
    assert not in_lock_window(datetime(2026, 10, 5, 16, 15, tzinfo=ET), w, yes)
    assert not in_lock_window(datetime(2026, 10, 5, 12, 0, tzinfo=ET), w, no)


def test_chunk_stops_mid_way_at_until_and_reruns(settings, paths, log, clock):
    # One 20-minute chunk made of 4 units, 12 minutes to go: no duration measured yet, so it starts,
    # checks should_stop() between units, gives up at the deadline and is not marked done.
    r = run(DummyJob(1, 1200, units=4), settings, paths, log, clock, until_min=12)
    assert (r.status, r.done) == ("until", 0)
    assert outputs(paths) == []
    r = run(DummyJob(1, 1200, units=4), settings, paths, log, clock, until_min=60)
    assert (r.status, r.done) == ("complete", 1)


def test_lock_window_reached_mid_run(settings, paths, log):
    from tests.conftest import FakeClock

    settings["ops"]["job_lock_window_enforced"] = True
    clock = FakeClock(datetime(2026, 10, 5, 8, 20, tzinfo=ET))  # Monday 08:20, window starts 08:30
    r = run(DummyJob(5, 240), settings, paths, log, clock, until_min=600, is_trading_day=lambda d: True)
    # 4-minute chunks: 08:20-08:24, 08:24-08:28; the third would end at 08:32, past the window start.
    assert (r.status, r.done) == ("until", 2)
    assert clock.now() < datetime(2026, 10, 5, 8, 30, tzinfo=ET)


def test_next_window_start_skips_non_trading_days():
    fri_evening = datetime(2026, 10, 2, 20, 0, tzinfo=ET)
    weekdays = lambda d: d.weekday() < 5
    assert next_window_start(fri_evening, "08:30-16:15", weekdays) == datetime(2026, 10, 5, 8, 30, tzinfo=ET)


def test_effective_max_workers(monkeypatch):
    class Mem:
        available = 12 * GB

    monkeypatch.setattr(runner_mod.psutil, "virtual_memory", lambda: Mem)
    monkeypatch.setattr(runner_mod.os, "cpu_count", lambda: 12)
    assert effective_max_workers(None, None) == 10  # all cores but 2
    assert effective_max_workers(6, None) == 6
    assert effective_max_workers(None, 3.0) == 2  # floor((12 - 4) / 3)
    assert effective_max_workers(None, 20.0) == 1  # never below one


def test_status_estimates_nights_left(settings, paths, log, clock, monkeypatch):
    monkeypatch.setattr(runner_mod, "_peak_gb", lambda: 1.25)
    run(DummyJob(10, 3600), settings, paths, log, clock, until_min=150)  # 2 chunks fit, 8 left
    lines = job_lines(paths)
    assert lines == ["dummy: 2/10 chunks done, p90 chunk 3600s, ~8.0 h left, ~4 night(s) left, peak 1.2 GB RAM"]


def test_jobs_hold_only_their_own_lock(settings, paths, log, clock):
    """presd_fixes issue 3: the Skylit and Databento backfills run in parallel; other jobs need both locks."""
    from nqbot.clients.databento_pull import DatabentoPullJob
    from nqbot.clients.skylit_backfill import SkylitBackfillJob
    from nqbot.clients.skylit_pull import SkylitPullJob

    assert SkylitBackfillJob.locks == (SKYLIT,) and SkylitPullJob.locks == (SKYLIT,)
    assert DatabentoPullJob.locks == (DATABENTO,)

    class SkylitLike(DummyJob):
        locks = (SKYLIT,)

    paths.root.mkdir(parents=True)
    lock_path(paths.root, DATABENTO).write_text(json.dumps({"host": "home-pc", "pid": 1, "job": "databento_backfill"}))
    assert run(SkylitLike(1, 1), settings, paths, log, clock).status == "complete"
    with pytest.raises(LockHeld):
        run(DummyJob(1, 1), settings, paths, log, clock)


def test_run_job_keeps_windows_awake(settings, paths, log, clock, monkeypatch, no_power_calls):
    from nqbot import power

    monkeypatch.setattr(power.os, "name", "nt")
    run(DummyJob(1, 1), settings, paths, log, clock)
    assert no_power_calls == [power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED, power.ES_CONTINUOUS]
