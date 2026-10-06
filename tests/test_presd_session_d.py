"""Pre-SSD session D (docs/presd_fixes.md, dry run 2 findings D1-D8): mocked Skylit API only, temporary SSD."""

import json
from pathlib import Path
from datetime import date, datetime, timedelta

import httpx
import pytest

from nqbot import cli
from nqbot.checks import step7
from nqbot.clients import skylit_backfill as bf
from nqbot.clients import skylit_pull as sp
from nqbot.clients.skylit import SkylitClient, SkylitError
from nqbot.clients.skylit_store import CacheMismatch, SkylitStore, read_meta
from nqbot.jobs.dummy import DummyJob
from nqbot.jobs.lock import SKYLIT, JobLock, job_locks, lock_path
from nqbot.jobs.runner import StopChunk, run_job, stop_request_path
from nqbot.secrets import Secret
from tests.skylit_fakes import SYMBOLS_MAP, parse_z, pass_strike_window
from tests.test_skylit_backfill import CAL, ET, _plan, _range_calls, setup  # noqa: F401 - the fixture
from tests.test_step7_checks import _dp_print, env  # noqa: F401 - the fixture


def _refuse_windows(s, pred, status=400, code="INVALID_PARAMETER"):
    """The fake answers `status` to every range call whose window start (ET) matches pred."""
    original = s.fake.handler

    def handler(request):
        if request.url.path == "/v1/historical/range" and pred(parse_z(request.url.params["from"]).astimezone(ET)):
            s.fake.calls.append(request)
            return httpx.Response(status, json={"error": {"code": code, "message": "bad window"}},
                                  headers={"X-Credits-Remaining": str(s.fake.credits)})
        return original(request)

    s.fake.handler = handler


def _calls_for(s, day, hhmm):
    return [r for r in _range_calls(s.fake)
            if parse_z(r.url.params["from"]).astimezone(ET).strftime("%Y-%m-%d %H%M") == f"{day} {hhmm}"]


# --- D1: failing windows ----------------------------------------------------------------------------------------

def test_a_refused_window_is_recorded_kept_and_never_pulled_again(setup):
    s = setup
    at = datetime(2025, 10, 20, 11, 0, tzinfo=ET)
    _refuse_windows(s, lambda t: t == at)
    _plan(s, approved=20_000)
    assert s.run().status == "complete"  # one bad window never ends the night
    rec = bf.load_failed(s.paths)["2025-10-20 1100"]
    assert rec["status"] == 400 and not rec["charged"] and rec["retries"] == 1 and rec["state"] == "failed"
    assert json.loads(open(rec["raw"], encoding="utf-8").read())["error"]["message"] == "bad window"  # error kept
    assert len(_calls_for(s, "2025-10-20", "1100")) == 2  # the main pass and the end-of-night retry
    md = (bf.coverage_dir(s.paths) / "coverage.md").read_text(encoding="utf-8")
    assert "## Failed windows and dark-pool dates" in md and "2025-10-20 1100: failed, 400 INVALID_PARAMETER" in md
    n = len(_range_calls(s.fake))
    _plan(s, approved=20_000)  # a re-plan with nothing left to pull: only the start-of-run retry asks it again
    assert s.run().status == "complete" and len(_range_calls(s.fake)) == n + 1
    assert len(_calls_for(s, "2025-10-20", "1100")) == 3 and bf.load_failed(s.paths)["2025-10-20 1100"]["retries"] == 2


def test_an_unconvertible_answer_is_kept_and_converted_again_for_free(setup, monkeypatch):
    """A TypeError in conversion (not only ValueError/KeyError) keeps the paid body; the end retry converts it from
    the kept copy without a second call."""
    s = setup
    real, fails = sp.range_to_tables, []

    def flaky(body, root_to_map, start, end):
        if start == datetime(2025, 10, 21, 12, 0, tzinfo=ET) and not fails:
            fails.append(1)
            raise TypeError("unexpected null")
        return real(body, root_to_map, start, end)

    monkeypatch.setattr(sp, "range_to_tables", flaky)
    _plan(s, approved=20_000)
    assert s.run().status == "complete"
    rec = bf.load_failed(s.paths)["2025-10-21 1200"]
    assert rec["code"] == "convert_failed" and rec["charged"] and rec["state"] == "recovered"
    assert len(_calls_for(s, "2025-10-21", "1200")) == 1  # paid once, never again
    meta = read_meta(s.store.range_paths(date(2025, 10, 21), "1200")[0])
    assert meta["converted_from"] == Path(rec["raw"]).name


def test_a_failed_write_keeps_the_answer(setup, monkeypatch):
    s = setup
    real, fails = SkylitStore.write_range, []

    def flaky(self, day, hhmm, *a, **kw):
        if (day, hhmm) == (date(2025, 10, 22), "1300") and not fails:
            fails.append(1)
            raise PermissionError(13, "held by antivirus")
        return real(self, day, hhmm, *a, **kw)

    monkeypatch.setattr(SkylitStore, "write_range", flaky)
    _plan(s, approved=20_000)
    assert s.run().status == "complete"
    rec = bf.load_failed(s.paths)["2025-10-22 1300"]
    assert rec["code"] == "write_failed" and rec["charged"] and rec["state"] == "recovered"
    assert len(_calls_for(s, "2025-10-22", "1300")) == 1


def test_k_failures_in_a_row_stop_cleanly(setup):
    s = setup
    _refuse_windows(s, lambda t: t.date() == date(2025, 10, 20))
    _plan(s, approved=20_000)
    r = s.run()
    assert r.status == "failures" and "5 windows or dates failed in a row" in r.message
    assert len(bf.load_failed(s.paths)) == 5
    first = set(bf.load_failed(s.paths))
    r = s.run()  # the start pass asks the five once more (refusals never stop it); the next five fail
    assert r.status == "failures" and len(bf.load_failed(s.paths)) == 10
    assert all(len(_calls_for(s, "2025-10-20", k.split()[1])) == (2 if k in first else 1)
               for k in bf.load_failed(s.paths))


def test_a_success_resets_the_count(setup):
    s = setup
    bad = {datetime(2025, 10, 20, 9, 30, tzinfo=ET) + timedelta(minutes=30 * i) for i in range(8)}  # every other one
    _refuse_windows(s, lambda t: t in bad)
    _plan(s, approved=20_000)
    assert s.run().status == "complete" and len(bf.load_failed(s.paths)) == 8


def test_plan_prints_the_balance_and_the_reserve(setup):
    s = setup
    _plan(s, approved=20_000)
    s.run()
    # chunks() echoes through the job's env (the log); make_plan echoes into s.lines
    assert any("kept for the step-7 checks running alongside" in line for line in s.lines)


# --- D1: the oldest-window test in step 7 ------------------------------------------------------------------------

def test_oldest_window_pulls_the_first_safe_window_of_the_oldest_day(setup):
    s = setup
    rep = step7.check_oldest_window(s.env)
    # now 2026-10-06 10:00: reach 2025-10-06 10:00 + 30 min margin + 1 h safety -> the 11:30 window
    assert (rep["day"], rep["window"], rep["converted"]) == ("2025-10-06", "1130", True)
    assert rep["resolution"] and rep["boards"] > 0
    assert "1130" in s.store.stored_windows(date(2025, 10, 6))  # the backfill reuses it
    n = len(_range_calls(s.fake))
    _plan(s, approved=20_000)
    s.run()
    assert len(_calls_for(s, "2025-10-06", "1130")) == 1 and len(_range_calls(s.fake)) > n


def test_oldest_window_reports_a_conversion_failure(setup, monkeypatch):
    s = setup
    monkeypatch.setattr(sp, "range_to_tables", lambda *a: (_ for _ in ()).throw(TypeError("1m frames differ")))
    rep = step7.check_oldest_window(s.env)
    assert not rep["converted"] and "1m frames differ" in rep["error"] and rep["kept_in"]


def test_oldest_window_waits_for_the_strike_window_check(setup):
    s = setup
    pass_strike_window(s.paths, s.settings, proposed=s.settings["skylit_max_strikes"] + 50)
    with pytest.raises(step7.CheckRefused, match="strike-window check passes"):
        step7.check_oldest_window(s.env)


# --- D2: the strike-window gate --------------------------------------------------------------------------------

def test_plan_refuses_below_the_proposal_and_passes_once_raised(setup):
    s = setup
    pass_strike_window(s.paths, s.settings, proposed=300)
    with pytest.raises(bf.BackfillRefused, match="below the 300"):
        _plan(s, approved=20_000)
    s.settings["skylit_max_strikes"] = 300  # raised, but the sample days are still the old pull
    with pytest.raises(bf.BackfillRefused, match="sample-days --dates ... --raised"):
        _plan(s, approved=20_000)
    pass_strike_window(s.paths, s.settings, proposed=250)  # raised above the proposal and re-checked: passes
    assert step7.strike_window_check(s.env) == (True, [])


def test_plan_refuses_on_edge_violations_and_without_the_checks(setup):
    s = setup
    pass_strike_window(s.paths, s.settings, edge_violations={"SPY": {"boards": 5, "violations": 2, "first": "x"}})
    with pytest.raises(bf.BackfillRefused, match="edge"):
        _plan(s, approved=20_000)
    for f in step7.reports_dir(s.paths).glob("*.json"):
        f.unlink()
    ok, why = step7.strike_window_check(s.env)
    assert not ok and any("strike-window first" in w for w in why) and any("sample-days first" in w for w in why)


def test_report_says_not_ready(setup):
    s = setup
    pass_strike_window(s.paths, s.settings, proposed=300)
    text = step7.check_report(s.env)
    assert "Ready for the owner's step-10 OK: NOT READY**" in text and "strike-window check: NOT READY" in text
    assert "below the 300" in text


# --- D3: alongside checks, own files, nqbot stop -----------------------------------------------------------------

def test_ledger_reads_every_ledger_file(paths):
    from nqbot.clients.skylit import CallRecord

    def rec(at):
        return CallRecord("/v1/historical/range", {}, 200, 1, 10, 100, 25, at)

    SkylitStore(paths).log_call(rec("2026-10-06T14:00:02+00:00"))
    SkylitStore(paths, "ledger_parity.jsonl").log_call(rec("2026-10-06T14:00:01+00:00"))
    rows = SkylitStore(paths).ledger()
    assert [r["at"] for r in rows] == ["2026-10-06T14:00:01+00:00", "2026-10-06T14:00:02+00:00"]
    assert (SkylitStore(paths).root / "ledger_parity.jsonl").exists()


def test_late_prints_never_write_the_main_dark_pool_cache(env):  # noqa: F811
    prev = date(2026, 3, 13)
    env.fake.dp_prints = [_dp_print(prev - timedelta(days=i), 11, 2e6 + i) for i in range(0, 40)
                          if (prev - timedelta(days=i)).weekday() < 5]
    env.now.t = datetime(2026, 3, 16, 8, 0, tzinfo=ET)
    step7.check_late_prints_morning(env)
    main = env.store.root / "darkpool" / "trades"
    assert not main.exists() or not list(main.glob("*.parquet"))
    lookback = list((step7._lookback_base(env) / "darkpool" / "trades").glob("*.parquet"))
    assert lookback
    n = len(env.fake.calls)
    step7.check_late_prints_morning(env)  # a second run pulls nothing new for the lookback
    assert len([c for c in env.fake.calls[n:] if c.url.params.get("date") != prev.isoformat()]) == 0
    d = date.fromisoformat(lookback[0].stem)
    assert step7.adopt_lookback_copy(env, d) and env.store.dp_trades_path(d).read_bytes() == lookback[0].read_bytes()
    assert not step7.adopt_lookback_copy(env, d)  # already in the main cache


def test_backfill_adopts_a_late_print_copy_without_paying(setup):
    s = setup
    d = date(2025, 10, 20)
    step7.pull_dp_date(s.env, d, step7._lookback_base(s.env), "late_print")
    n = len([c for c in s.fake.calls if c.url.params.get("date") == d.isoformat()])
    _plan(s, approved=20_000)
    assert s.run().status == "complete"
    assert len([c for c in s.fake.calls if c.url.params.get("date") == d.isoformat()]) == n
    rec = json.loads((bf.job_dir(s.paths) / "days" / f"{d}.json").read_text(encoding="utf-8"))
    assert rec["dp"]["status"] == "from_late_print_check" and s.store.dp_trades_path(d).exists()


def test_backfill_leaves_the_reserve(setup):
    s = setup
    _plan(s, approved=20_000)
    s.fake.credits = 1_000 + 30  # one window above the reserve
    r = s.run()
    assert r.status == "credits" and s.fake.credits >= 1_000


def test_alongside_checks_run_while_the_backfill_holds_the_lock(settings, drive, paths, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    seen = {}

    def fake_parity(env, session, tag, approve):
        seen["ledger"] = env.ledger_name

    monkeypatch.setattr(step7, "check_parity", fake_parity)
    monkeypatch.setattr(step7, "check_historical_instants", lambda env, approve: None)
    paths.root.mkdir(parents=True, exist_ok=True)
    with job_locks(paths.root, (SKYLIT,), "skylit_backfill"):
        assert cli.main(["step7", "parity", "--session", "2026-03-02"]) == 0
        assert seen["ledger"] == "ledger_parity.jsonl"
        assert cli.main(["step7", "historical-instants"]) == cli.EXIT_LOCKED  # a history pull still waits


def test_stop_request_ends_the_job_between_chunks(settings, paths, log, clock):
    class AskStop(DummyJob):
        def run_chunk(self, chunk_id, ctx):
            super().run_chunk(chunk_id, ctx)
            if chunk_id == "chunk-000":
                stop_request_path(ctx.paths, "dummy").write_text("{}")

    r = run_job(AskStop(3, 1), settings, paths, until=clock.now() + timedelta(hours=1), log=log, now=clock.now,
                sleep=clock.sleep)
    assert (r.status, r.done) == ("stopped", 1) and not stop_request_path(paths, "dummy").exists()


def test_stop_request_reaches_should_stop_inside_a_chunk(settings, paths, log, clock):
    class Long(DummyJob):
        def run_chunk(self, chunk_id, ctx):
            stop_request_path(ctx.paths, "dummy").parent.mkdir(parents=True, exist_ok=True)
            stop_request_path(ctx.paths, "dummy").write_text("{}")
            reason = ctx.should_stop()
            raise StopChunk(reason)

    r = run_job(Long(2, 1), settings, paths, until=clock.now() + timedelta(hours=1), log=log, now=clock.now,
                sleep=clock.sleep)
    assert (r.status, r.done) == ("stopped", 0) and "nqbot stop" in r.message


def test_an_old_stop_request_is_cleared_at_start(settings, paths, log, clock):
    stop_request_path(paths, "dummy").parent.mkdir(parents=True, exist_ok=True)
    stop_request_path(paths, "dummy").write_text("{}")
    r = run_job(DummyJob(2, 1), settings, paths, until=clock.now() + timedelta(hours=1), log=log, now=clock.now,
                sleep=clock.sleep)
    assert r.status == "complete"


def test_cli_stop(settings, drive, paths, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    assert cli.main(["stop", "skylit_backfill"]) == cli.EXIT_REFUSED
    assert "isn't running" in capsys.readouterr().err
    paths.root.mkdir(parents=True, exist_ok=True)
    with JobLock(lock_path(paths.root, SKYLIT), "skylit_backfill"):
        assert cli.main(["stop", "skylit_backfill"]) == 0
    assert stop_request_path(paths, "skylit_backfill").exists()


# --- D7: dropped connections ----------------------------------------------------------------------------------

def _client(exc_type, calls):
    def handler(request):
        if request.url.path == "/v1/historical/range":
            calls.append(1)
            raise exc_type("boom", request=request)
        return httpx.Response(200, json={}, headers={"X-Credits-Remaining": "100"})

    return SkylitClient(Secret("k"), symbols_map=SYMBOLS_MAP, transport=httpx.MockTransport(handler),
                        sleep=lambda s: None)


def test_a_dropped_range_call_is_retried_only_once():
    calls = []
    c = _client(httpx.ReadError, calls)
    t = datetime(2026, 3, 2, 9, 30, tzinfo=ET)
    with pytest.raises(SkylitError) as e:
        c.historical_range(["QQQ"], t, t + timedelta(minutes=15))
    assert e.value.code == "dropped" and len(calls) == 2


def test_a_connection_never_made_keeps_the_normal_retries():
    calls = []
    c = _client(httpx.ConnectError, calls)
    t = datetime(2026, 3, 2, 9, 30, tzinfo=ET)
    with pytest.raises(SkylitError) as e:
        c.historical_range(["QQQ"], t, t + timedelta(minutes=15))
    assert e.value.code == "gave_up" and len(calls) == 5


# --- D8: plain step-7 errors -------------------------------------------------------------------------------------

@pytest.mark.parametrize("exc, expect", [
    (SkylitError(400, "INVALID_PARAMETER", "too old"), "month 3 or later"),
    (CacheMismatch("pulled with other parameters"), "Don't delete or move anything"),
])
def test_step7_errors_print_a_hint(settings, drive, monkeypatch, capsys, exc, expect):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    def boom(*a, **kw):
        raise exc

    monkeypatch.setattr(step7, "check_darkpool", boom)
    assert cli.main(["step7", "darkpool", "--date", "2026-03-02"]) == cli.EXIT_REFUSED
    err = capsys.readouterr().err
    assert expect in err and "Traceback" not in err


def test_strike_window_timeout_hint(settings, drive, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(step7, "check_strike_window",
                        lambda *a, **kw: (_ for _ in ()).throw(SkylitError(504, "gave_up", "timed out")))
    assert cli.main(["step7", "strike-window", "--busy-day", "2026-03-02", "--sample-days", "2026-03-02"]) == 2
    assert "wait a few minutes" in capsys.readouterr().err


def test_darkpool_warns_about_an_early_month(env):  # noqa: F811
    lines = []
    env.echo = lines.append
    first = step7.months_mod.days_of(1, env.settings)[0]
    try:
        step7.check_darkpool(env, first + timedelta(days=7), [], depth_probe=False)
    except Exception:  # noqa: BLE001 - only the warning matters here
        pass
    assert any("month 3 or later" in line for line in lines)


# --- review fixes ----------------------------------------------------------------------------------------------

def test_a_recorded_dark_pool_date_is_never_asked_again_in_the_main_pass(setup):
    """Review 1: the K-th failure being a dark-pool date (chunk not done) must not call it again on the re-run."""
    from tests.test_skylit_backfill import _dp_answer

    s = setup
    _dp_answer(s, "2025-10-20", 400, "INVALID_PARAMETER")
    _plan(s, approved=20_000)
    s.run()
    n = len([c for c in s.fake.calls if c.url.params.get("date") == "2025-10-20"])
    from nqbot.jobs.runner import state_path

    state = json.loads(state_path(s.paths, bf.JOB_NAME).read_text(encoding="utf-8"))
    del state["done"]["2025-10-20"]  # as if the run had stopped at that chunk
    state_path(s.paths, bf.JOB_NAME).write_text(json.dumps(state), encoding="utf-8")
    s.run()  # only the two retry passes ask it, never the main pass
    assert len([c for c in s.fake.calls if c.url.params.get("date") == "2025-10-20"]) == n + 2


def test_a_late_print_copy_is_never_inside_a_range_call(setup):
    s = setup
    d = date(2025, 10, 20)
    step7.pull_dp_date(s.env, d, step7._lookback_base(s.env), "late_print")
    plan = bf.plan_backfill(s.env, s.clock(), ["QQQ", "SPY"])
    assert d not in bf.dp_todo(s.env, plan)


def test_the_whole_chain_has_no_edge_to_violate(setup):
    s = setup
    s.settings["skylit_max_strikes"] = "all"
    pass_strike_window(s.paths, s.settings, edge_violations={"QQQ": {"boards": 5, "violations": 5}})
    assert step7.strike_window_check(s.env) == (True, [])


def test_failures_are_retried_at_the_next_start_oldest_first_and_become_lost_out_of_reach(setup):
    """Owner 2026-10-06: retries at the start of every run and the end of every night, oldest first; a failure
    whose window leaves the 365-day reach while it waits is lost, and coverage.md says so."""
    s = setup
    bad = {datetime(2025, 10, 8, 11, 0, tzinfo=ET), datetime(2025, 10, 21, 11, 0, tzinfo=ET)}
    _refuse_windows(s, lambda t: t in bad)
    _plan(s, approved=20_000)
    assert s.run().status == "complete"
    s.clock.t += timedelta(days=3)  # 2025-10-08 has left the reach; 2025-10-21 hasn't
    assert s.run(until=s.clock.t + timedelta(hours=10)).status == "complete"
    failed = bf.load_failed(s.paths)
    assert failed["2025-10-08 1100"]["state"] == "lost" and failed["2025-10-21 1100"]["state"] == "failed"
    passes = [json.loads(line) for line in (bf.job_dir(s.paths) / "retries.jsonl").read_text().splitlines()]
    assert [p["when"] for p in passes] == ["end", "start"]  # nothing left to pull: no second pass in a row
    assert list(passes[0]["outcome"]) == ["2025-10-08 1100", "2025-10-21 1100"]  # oldest first
    assert list(passes[1]["outcome"]) == ["2025-10-08 1100", "2025-10-21 1100"]
    assert len(_calls_for(s, "2025-10-08", "1100")) == 2  # never called once out of reach
    cov = json.loads((bf.coverage_dir(s.paths) / "coverage.json").read_text(encoding="utf-8"))
    assert cov["skylit"]["beyond_365_days"]["partial_days"]["2025-10-08"] == ["1100"]
    md = (bf.coverage_dir(s.paths) / "coverage.md").read_text(encoding="utf-8")
    assert "2025-10-08 1100: lost" in md and "2025-10-21 1100: failed" in md


def test_a_retry_pass_ends_after_k_timeouts_but_the_run_goes_on(setup):
    s = setup
    _plan(s, approved=20_000)
    failed = {}
    for i in range(6):  # six windows recorded as timed out by an earlier run
        t = datetime(2025, 10, 20, 9, 30, tzinfo=ET) + timedelta(minutes=15 * i)
        failed[f"2025-10-20 {t:%H%M}"] = {
            "kind": "window", "date": "2025-10-20", "hhmm": t.strftime("%H%M"), "status": 503, "code": "gave_up",
            "message": "x", "charged": False, "raw": None, "at": "2026-10-06T00:00:00+00:00", "retries": 0,
            "state": "failed"}
    bf.failed_path(s.paths).parent.mkdir(parents=True, exist_ok=True)
    bf.failed_path(s.paths).write_text(json.dumps(failed), encoding="utf-8")
    s.fake.queue["/v1/historical/range"] = [(503, {"error": {"code": "unavailable", "message": "x"}}, {})] * 25
    r = s.run()
    assert r.status == "complete"  # the outage ended the start pass, never the run
    passes = [json.loads(line) for line in (bf.job_dir(s.paths) / "retries.jsonl").read_text().splitlines()]
    assert passes[0]["when"] == "start" and len(passes[0]["outcome"]) == 5
    assert all(v == "recovered" for v in passes[-1]["outcome"].values())
    assert all(r["state"] == "recovered" for r in bf.load_failed(s.paths).values())


def test_runner_hooks(settings, paths, log, clock):
    seen = []

    class Hooked(DummyJob):
        def at_start(self, ctx):
            seen.append("start")

        def at_end(self, ctx):
            seen.append("end")

    run_job(Hooked(2, 1), settings, paths, until=clock.now() + timedelta(hours=1), log=log, now=clock.now,
            sleep=clock.sleep)
    assert seen == ["start", "end"]

    class StopsAtStart(Hooked):
        def at_start(self, ctx):
            raise StopChunk("no", status="failures")

    seen.clear()
    r = run_job(StopsAtStart(2, 1), settings, paths, until=clock.now() + timedelta(hours=1), log=log, now=clock.now,
                sleep=clock.sleep)
    assert r.status == "failures" and r.ran == [] and seen == []  # no chunk, no end hook after a stop


def test_the_report_names_the_board_that_sets_the_strike_window():
    """Owner 2026-10-06: zero tolerance stays; if one unusual board drives the proposal, show it and the next
    smaller window (the credits are the same either way: range calls cost 25 whatever maxStrikes is)."""
    import numpy as np

    from nqbot.checks import step7_analysis as an
    from tests.test_step7_checks import _boards, _strikes, ts

    t = ts("2026-03-05T14:30:00+00:00")
    k = list(np.arange(1.0, 12.0))
    vals = [10.0] * 11
    vals[7], vals[2] = 1000.0, -300.0
    boards = _boards([{"symbol": "QQQ", "as_of": t, "spot": 6.0}, {"symbol": "SPY", "as_of": t, "spot": 6.0}])
    strikes = _strikes([("QQQ", t, a, b) for a, b in zip(k, vals)]
                       + [("SPY", t, a, 10.0 if a != 6 else 99.0) for a in k])
    res = an.strike_window(boards, strikes, 0.2, 2)
    assert res.proposed == 10 and res.next_smaller == 5
    assert res.drivers == [{"symbol": "QQQ", "as_of": "2026-03-05T14:30:00.000", "strike": 3.0, "need": 10}]
    lines = step7._driver_lines({"proposed_skylit_max_strikes": 10, "drivers": res.drivers,
                                 "boards_setting_proposal": 1, "next_smaller": 5})
    assert "set by 1 board(s): QQQ 2026-03-05T14:30:00.000 (strike 3.0)" in lines[0]
    assert "fits in 5 strikes" in lines[1] and "25 per 15-minute window either way" in lines[1] and "2.00x" in lines[1]


def test_a_dark_pool_retry_that_finds_no_prints_is_no_coverage_not_recovered(setup):
    """Fresh review 3: the dark-pool retry path; an empty trading day is settled as no coverage."""
    from tests.test_skylit_backfill import _dp_answer

    s = setup
    _dp_answer(s, "2025-10-20", 400, "INVALID_PARAMETER")
    _plan(s, approved=20_000)
    s.run()
    assert bf.load_failed(s.paths)["dp 2025-10-20"]["state"] == "failed"
    s.fake.handler = s.fake.__class__.handler.__get__(s.fake)  # Skylit answers again, but with no prints that day
    s.fake.dp_prints = [p for p in s.fake.dp_prints if not p["timestamp"].startswith("2025-10-20")]
    s.run()
    rec = bf.load_failed(s.paths)["dp 2025-10-20"]
    assert rec["state"] == "no_coverage" and not s.store.dp_trades_path(date(2025, 10, 20)).exists()


def test_a_paid_dark_pool_failure_is_never_called_again(setup):
    s = setup
    _plan(s, approved=20_000)
    s.run()
    failed = {"dp 2025-10-21": {"kind": "dp", "date": "2025-10-21", "hhmm": None, "status": None, "code": "page_order",
                                "message": "x", "charged": True, "raw": None, "at": "2026-10-06T00:00:00+00:00",
                                "retries": 0, "state": "failed"}}
    s.store.dp_trades_path(date(2025, 10, 21)).unlink()
    bf.failed_path(s.paths).write_text(json.dumps(failed), encoding="utf-8")
    n = len([c for c in s.fake.calls if c.url.params.get("date") == "2025-10-21"])
    s.run()
    assert len([c for c in s.fake.calls if c.url.params.get("date") == "2025-10-21"]) == n
    assert bf.load_failed(s.paths)["dp 2025-10-21"]["state"] == "failed"


def test_coverage_counts_dropped_windows_still_in_reach(setup):
    s = setup
    _plan(s, approved=20_000)
    s.run()
    failed = {"2025-10-21 1100": {"kind": "window", "date": "2025-10-21", "hhmm": "1100", "status": None,
                                  "code": "dropped", "message": "x", "charged": True, "raw": None,
                                  "at": "2026-10-06T00:00:00+00:00", "retries": 0, "state": "failed"}}
    bf.failed_path(s.paths).write_text(json.dumps(failed), encoding="utf-8")
    plan = bf.BackfillPlan.from_json(json.loads(bf.plan_path(s.paths).read_text(encoding="utf-8")))
    bf.write_coverage(s.env, plan, bf.job_dir(s.paths))
    md = (bf.coverage_dir(s.paths) / "coverage.md").read_text(encoding="utf-8")
    assert "Dropped connections still in reach: 1" in md and "2025-10-21 1100: failed, None dropped (paid; never" in md


def test_a_lookback_copy_with_other_parameters_refuses_the_plan(setup):
    from nqbot.clients.skylit_store import write_parquet, prints_to_table

    s = setup
    d = date(2025, 10, 20)
    write_parquet(s.store.dp_trades_path(d, step7._lookback_base(s.env)), prints_to_table([]),
                  {"params": {**step7._dp_params(s.env, d), "min_notional": 1}})
    with pytest.raises(bf.BackfillRefused, match="dark pools 2025-10-20"):
        _plan(s, approved=20_000)


def test_the_report_counts_every_board_that_sets_the_proposal():
    import numpy as np

    from nqbot.checks import step7_analysis as an
    from tests.test_step7_checks import _boards, _strikes, ts

    times = [ts("2026-03-05T14:30:00+00:00") + timedelta(seconds=i) for i in range(an.DRIVERS_LISTED + 2)]
    k = list(np.arange(1.0, 12.0))
    vals = [10.0] * 11
    vals[7], vals[2] = 1000.0, -300.0
    boards = _boards([{"symbol": "QQQ", "as_of": t, "spot": 6.0} for t in times])
    strikes = _strikes([("QQQ", t, a, b) for t in times for a, b in zip(k, vals)])
    res = an.strike_window(boards, strikes, 0.2, 2)
    assert len(res.drivers) == an.DRIVERS_LISTED and res.n_drivers == an.DRIVERS_LISTED + 2


def test_batch_warns_about_the_same_name_in_two_folders(settings, tmp_path):
    import shutil

    from nqbot.gameplan import parser as gp
    from tests.anthropic_fakes import FIXTURES, FakeAnthropic

    store = gp.GameplanStore(tmp_path / "raw", tmp_path / "processed", tmp_path / "reports")
    for folder in ("a", "b"):
        (store.raw_dir / folder).mkdir(parents=True)
        shutil.copy(FIXTURES / "NFP Gameplan.md", store.raw_dir / folder / "NFP Gameplan.md")
    lines = []
    try:
        gp.batch(FakeAnthropic(), settings, store, approved_usd=None, out=lines.append)
    except gp.GameplanRefused:
        pass
    assert any("NFP Gameplan.md is in more than one folder" in line for line in lines)


# --- owner 2026-10-06: extra tries for dropped windows -------------------------------------------------------------

def _record_dropped(s, keys):
    failed = bf.load_failed(s.paths)
    for key in keys:
        day, hhmm = key.split()
        failed[key] = {"kind": "window", "date": day, "hhmm": hhmm, "status": None, "code": "dropped", "message": "x",
                       "charged": True, "raw": None, "at": "2026-10-06T00:00:00+00:00", "retries": 0, "state": "failed"}
    bf.failed_path(s.paths).parent.mkdir(parents=True, exist_ok=True)
    bf.failed_path(s.paths).write_text(json.dumps(failed), encoding="utf-8")


def _drop_windows(s, pred):
    original = s.fake.handler

    def handler(request):
        if request.url.path == "/v1/historical/range" and pred(parse_z(request.url.params["from"]).astimezone(ET)):
            s.fake.calls.append(request)
            raise httpx.ReadError("connection dropped", request=request)
        return original(request)

    s.fake.handler = handler


def test_a_dropped_window_gets_another_try_at_the_end_of_the_night(setup):
    s = setup
    _plan(s, approved=20_000)
    _record_dropped(s, ["2025-10-20 1100"])
    assert s.run().status == "complete"
    rec = bf.load_failed(s.paths)["2025-10-20 1100"]
    assert rec["state"] == "recovered" and len(rec["dropped_tries"]) == 1 and rec["dropped_tries"][0]["credits"] == 25
    assert len(_calls_for(s, "2025-10-20", "1100")) == 1  # never in the main pass or the start pass
    md = (bf.coverage_dir(s.paths) / "coverage.md").read_text(encoding="utf-8")
    assert "Dropped-window retries: 1 window(s), 1 tries, 25 credits" in md
    assert "2025-10-20 1100: 1 tries, 25 credits, recovered" in md


def test_a_window_that_keeps_dropping_gets_three_spaced_tries_then_waits_for_its_reach(setup):
    s = setup
    _plan(s, approved=20_000)
    at = datetime(2025, 10, 20, 11, 0, tzinfo=ET)
    _record_dropped(s, ["2025-10-20 1100"])
    _drop_windows(s, lambda t: t == at)
    assert s.run().status == "complete"
    rec = bf.load_failed(s.paths)["2025-10-20 1100"]
    assert rec["state"] == "failed" and len(rec["dropped_tries"]) == 3
    assert len(_calls_for(s, "2025-10-20", "1100")) == 3  # each try sent once (no automatic re-send)
    assert sum(s.slept) >= sum(bf.DROPPED_ROUND_WAIT_SEC)  # spaced out
    s.run()  # tries used up: never called again
    assert len(_calls_for(s, "2025-10-20", "1100")) == 3


def test_the_nightly_cap_limits_dropped_tries(setup):
    s = setup
    s.settings["skylit_dropped_retry_credits_per_night"] = 50
    _plan(s, approved=20_000)
    keys = ["2025-10-20 1100", "2025-10-21 1100", "2025-10-22 1100"]
    _record_dropped(s, keys)
    _drop_windows(s, lambda t: t.time() == datetime(2025, 1, 1, 11, 0).time() and t.date() >= date(2025, 10, 20))
    s.run()
    tries = {k: len(bf.load_failed(s.paths)[k].get("dropped_tries", [])) for k in keys}
    assert tries == {"2025-10-20 1100": 1, "2025-10-21 1100": 1, "2025-10-22 1100": 0}  # oldest first, 50 credits


def test_a_dropped_window_out_of_reach_is_lost_without_a_call(setup):
    s = setup
    _plan(s, approved=20_000)
    _record_dropped(s, ["2025-10-07 1100"])
    s.clock.t += timedelta(days=3)
    s.run(until=s.clock.t + timedelta(hours=10))
    assert bf.load_failed(s.paths)["2025-10-07 1100"]["state"] == "lost"
    assert not _calls_for(s, "2025-10-07", "1100")


# --- owner 2026-10-06: a timed-out strike-window call is split, never shortened -----------------------------------

def _time_out_full_chain(env, longer_than_min):  # noqa: F811
    original = env.fake.handler

    def handler(request):
        q = request.url.params
        if request.url.path == "/v1/historical/range" and q.get("maxStrikes") == "all" \
                and (parse_z(q["to"]) - parse_z(q["from"])) > timedelta(minutes=longer_than_min):
            env.fake.calls.append(request)
            return httpx.Response(504, json={"error": {"code": "timeout", "message": "x"}},
                                  headers={"X-Credits-Remaining": str(env.fake.credits)})
        return original(request)

    env.fake.handler = handler


def _full_boards(env, d):  # noqa: F811
    from tests.skylit_fakes import range_body

    start = datetime.combine(d, step7.STRIKE_WINDOW_DEFAULT_START, tzinfo=ET)
    body = range_body(["QQQ", "NDXP", "SPY", "SPXW"], start, start + timedelta(minutes=15, milliseconds=-1),
                      env.fake.step_sec)
    return sorted({f["asOf"] for sym in body["data"]["symbols"] for f in sym["frames"]})


@pytest.mark.parametrize("longer_than", [5, 1])  # 5: the 15-minute call times out; 1: the 5-minute pieces too
def test_a_timed_out_strike_window_call_is_split_into_pieces_covering_the_same_15_minutes(env, longer_than):  # noqa: F811
    env.fake.step_sec = 60
    _time_out_full_chain(env, longer_than)
    d = date(2026, 3, 2)
    rep = step7.check_strike_window(env, d, [d])
    assert rep["proposed_skylit_max_strikes"] > 0
    b, _ = step7.read_fullchain(env, d)
    got = sorted({t.isoformat().replace("+00:00", "Z") for t in b["as_of"].to_pylist()})
    assert [g[:19] for g in got] == [x[:19] for x in _full_boards(env, d)]  # exactly the same boards
    manifest = json.loads(next((env.store.fullchain / "pieces").glob("2026-03-02_*.json")).read_text())
    assert {p["minutes"] for p in manifest["pieces"]} == ({5} if longer_than == 5 else {1})
    assert len(manifest["pieces"]) == (3 if longer_than == 5 else 15)
    assert any("consecutive" in line for line in env.lines)
    n = len(env.fake.calls)
    step7.check_strike_window(env, d, [d])  # cached: nothing pulled again
    assert len(env.fake.calls) == n


def test_a_refused_piece_never_leaves_a_shorter_measurement(env):  # noqa: F811
    _time_out_full_chain(env, 5)
    original = env.fake.handler

    def handler(request):
        q = request.url.params
        if request.url.path == "/v1/historical/range" and q.get("maxStrikes") == "all" \
                and parse_z(q["from"]).astimezone(ET).strftime("%H%M") == "0940":
            env.fake.calls.append(request)
            return httpx.Response(400, json={"error": {"code": "bad", "message": "x"}},
                                  headers={"X-Credits-Remaining": str(env.fake.credits)})
        return original(request)

    env.fake.handler = handler
    d = date(2026, 3, 2)
    with pytest.raises(SkylitError):
        step7.check_strike_window(env, d, [d])
    assert not list((env.store.fullchain / "pieces").glob("*.json"))  # no manifest, no report
    assert not list(step7.reports_dir(env.paths).glob("strike_window_*.json"))


# --- D1 for dark pools and Tempest (check-D, owner 2026-10-06) ---------------------------------------------------

def _dp_calls_for(s, day):
    return [c for c in s.fake.calls if c.url.path == "/v1/dark-pool/trades" and c.url.params.get("date") == day]


def _fail_dp_table_once(monkeypatch, day, exc=TypeError("unexpected null")):
    """step7's conversion fails once for the answer holding `day`'s prints (a converter bug, fixed by the retry)."""
    real, fails = step7.prints_to_table, []

    def flaky(rows):
        if rows and any(str(r.get("timestamp", "")).startswith(day) for r in rows) and not fails:
            fails.append(1)
            raise exc
        return real(rows)

    monkeypatch.setattr(step7, "prints_to_table", flaky)
    return fails


def test_an_unconvertible_dark_pool_answer_is_kept_and_converted_again_for_free(setup, monkeypatch):
    s = setup
    fails = _fail_dp_table_once(monkeypatch, "2025-10-21")
    _plan(s, approved=20_000)
    assert s.run().status == "complete"  # the night goes on
    assert fails
    rec = bf.load_failed(s.paths)["dp 2025-10-21"]
    assert rec["code"] == "convert_failed" and rec["charged"] and rec["complete"] and rec["state"] == "recovered"
    kept = json.loads(Path(rec["raw"]).read_text(encoding="utf-8"))
    assert kept["complete"] and kept["span"] == {"date": "2025-10-21"} and kept["pages"][0]["data"]
    assert len(_dp_calls_for(s, "2025-10-21")) == 1  # paid once, never again
    meta = read_meta(s.store.dp_trades_path(date(2025, 10, 21)))
    assert meta["converted_from"] == Path(rec["raw"]).name
    assert len(step7.read_prints([s.store.dp_trades_path(date(2025, 10, 21))])) == 2


def test_a_malformed_dark_pool_print_never_ends_the_night_or_pays_twice(setup):
    """The check-D case: a paid page with a print missing its timestamp was a KeyError that ended the night and lost
    the pages (paid again on every re-run)."""
    s = setup
    real_page = s.fake._dp_page

    def page(q):
        out = real_page(q)
        if q.get("date") == "2025-10-21":
            out["data"] = [*out["data"], {"ticker": "QQQ", "price": 600.0, "size": 1, "notional": 600.0}]
        return out

    s.fake._dp_page = page
    _plan(s, approved=20_000)
    assert s.run().status == "complete"
    rec = bf.load_failed(s.paths)["dp 2025-10-21"]
    assert rec["code"] == "convert_failed" and rec["charged"] and rec["complete"] and rec["state"] == "failed"
    assert Path(rec["raw"]).exists() and len(_dp_calls_for(s, "2025-10-21")) == 1
    assert s.store.dp_trades_path(date(2025, 10, 22)).exists()  # the dates after it were pulled
    assert s.run().status == "complete"  # the start and end retries convert the kept copy again: no call
    assert len(_dp_calls_for(s, "2025-10-21")) == 1
    assert bf.load_failed(s.paths)["dp 2025-10-21"]["retries"] == 2  # end of night 1, start of run 2
    md = (bf.coverage_dir(s.paths) / "coverage.md").read_text(encoding="utf-8")
    assert "dp 2025-10-21: failed, 200 convert_failed (paid; answer kept in" in md and "converted again for free" in md


def _cut_off_page_two(s, monkeypatch, day, times=1):
    """Pages of one print; the second page of `day` is refused `times` times (the first page was paid)."""
    import nqbot.clients.skylit as sk

    monkeypatch.setattr(sk, "DP_PAGE_LIMIT", 1)
    original, left = s.fake.handler, [times]

    def handler(request):
        q = request.url.params
        if request.url.path == "/v1/dark-pool/trades" and q.get("date") == day and q.get("cursor") == "1" \
                and left[0] > 0:
            left[0] -= 1
            s.fake.calls.append(request)
            return httpx.Response(400, json={"error": {"code": "INVALID_CURSOR", "message": "x"}},
                                  headers={"X-Credits-Remaining": str(s.fake.credits)})
        return original(request)

    s.fake.handler = handler


def test_a_cut_off_dark_pool_answer_is_kept_and_gets_one_paid_re_pull(setup, monkeypatch):
    s = setup
    _cut_off_page_two(s, monkeypatch, "2025-10-21")
    _plan(s, approved=20_000)
    assert s.run().status == "complete"
    rec = bf.load_failed(s.paths)["dp 2025-10-21"]
    assert rec["code"] == "cut_off" and rec["charged"] and not rec["complete"] and rec["state"] == "recovered"
    kept = json.loads(Path(rec["raw"]).read_text(encoding="utf-8"))
    assert not kept["complete"] and len(kept["pages"]) == 1  # the paid first page is kept
    assert rec["repull"]["outcome"] == "recovered" and rec["repull"]["credits"] == 15  # 3 pages of 5
    assert len(_dp_calls_for(s, "2025-10-21")) == 2 + 3  # page 1 + the refused page 2, then the re-pull
    assert len(step7.read_prints([s.store.dp_trades_path(date(2025, 10, 21))])) == 2
    md = (bf.coverage_dir(s.paths) / "coverage.md").read_text(encoding="utf-8")
    assert "Paid re-pulls of a kept answer" in md and ": 1, 15 credits" in md
    assert "dp 2025-10-21: 15 credits, recovered, now recovered" in md


def test_a_dark_pool_re_pull_is_used_once_then_never_called_again_and_lost_out_of_reach(setup, monkeypatch):
    s = setup
    _cut_off_page_two(s, monkeypatch, "2025-10-21", times=2)  # the re-pull is cut off too
    _plan(s, approved=20_000)
    assert s.run().status == "complete"
    rec = bf.load_failed(s.paths)["dp 2025-10-21"]
    # 10: page 1 and the refused page 2 (counted on the safe side until the next run re-reads the ledger)
    assert rec["state"] == "failed" and rec["repull"]["credits"] == 10 and "cut_off" in rec["repull"]["outcome"]
    n = len(_dp_calls_for(s, "2025-10-21"))
    assert s.run().status == "complete" and len(_dp_calls_for(s, "2025-10-21")) == n  # its one re-pull is used
    md = (bf.coverage_dir(s.paths) / "coverage.md").read_text(encoding="utf-8")
    assert "is cut off or unreadable: its one re-pull is used)" in md
    s.clock.t += timedelta(days=20)  # 2025-10-21 has left the reach
    assert s.run(until=s.clock.t + timedelta(hours=10)).status == "complete"
    assert bf.load_failed(s.paths)["dp 2025-10-21"]["state"] == "lost"


def test_a_dark_pool_re_pull_waits_for_the_nightly_cap(setup, monkeypatch):
    s = setup
    s.settings["skylit_dropped_retry_credits_per_night"] = 0
    _cut_off_page_two(s, monkeypatch, "2025-10-21")
    _plan(s, approved=20_000)
    assert s.run().status == "complete"
    rec = bf.load_failed(s.paths)["dp 2025-10-21"]
    assert rec["state"] == "failed" and "repull" not in rec and len(_dp_calls_for(s, "2025-10-21")) == 2
    md = (bf.coverage_dir(s.paths) / "coverage.md").read_text(encoding="utf-8")
    assert "one paid re-pull while in reach)" in md
    s.settings["skylit_dropped_retry_credits_per_night"] = 500  # the next night has room
    assert s.run().status == "complete"
    assert bf.load_failed(s.paths)["dp 2025-10-21"]["state"] == "recovered"


def test_a_31_day_answer_that_cant_be_converted_keeps_its_dates_and_is_never_paid_twice(setup, monkeypatch):
    from tests.test_skylit_backfill import _dp_calls, _range_report

    s = setup
    s.clock.t = datetime(2026, 1, 15, 10, 0, tzinfo=ET)
    _range_report(s)
    fails = _fail_dp_table_once(monkeypatch, "2025-08-18")  # the first 31-day call (8/18..9/17)
    _plan(s, approved=20_000)
    assert s.run().status == "complete" and fails
    failed = bf.load_failed(s.paths)
    group = [k for k, r in failed.items() if r.get("shares") == "dp 2025-08-18"]
    assert len(group) == 22 and failed["dp 2025-08-18"]["code"] == "convert_failed"
    assert all(r["state"] == "recovered" for r in failed.values())  # the end retry converted the kept copy
    calls = _dp_calls(s.fake)
    assert len(calls) == 3 and [q["date_start"] for q in calls].count("2025-08-18") == 1  # never paid twice
    assert s.store.dp_trades_path(date(2025, 9, 17)).exists() and s.store.dp_trades_path(date(2025, 8, 18)).exists()


def test_an_unconvertible_tempest_answer_is_kept_and_converted_again_for_free(setup, monkeypatch):
    s = setup
    real, fails = bf.vol_history_to_table, []

    def flaky(body):
        if not fails:
            fails.append(1)
            raise KeyError("columns")
        return real(body)

    monkeypatch.setattr(bf, "vol_history_to_table", flaky)
    _plan(s, approved=20_000)
    assert s.run().status == "complete"
    (rec,) = [r for r in bf.load_failed(s.paths).values() if r["kind"] == "tempest"]
    assert rec["code"] == "convert_failed" and rec["complete"] and rec["state"] == "recovered"
    assert len([c for c in s.fake.calls if c.url.path == "/v1/vol/history"]) == 1  # paid once
    plan = bf.BackfillPlan.from_json(json.loads(bf.plan_path(s.paths).read_text(encoding="utf-8")))
    assert bf.tempest_todo(s.store, plan) is None
    assert json.loads((bf.job_dir(s.paths) / "tempest.json").read_text(encoding="utf-8"))["files"]


def test_an_unreadable_tempest_answer_gets_one_paid_re_pull(setup):
    s = setup
    _plan(s, approved=20_000)
    original, served = s.fake.handler, []

    def handler(request):
        if request.url.path == "/v1/vol/history" and not served:
            served.append(1)
            s.fake.calls.append(request)
            s.fake.credits -= 1
            return httpx.Response(200, content=b"{not json", headers={"X-Credits-Remaining": str(s.fake.credits)})
        return original(request)

    s.fake.handler = handler
    assert s.run().status == "complete"
    (rec,) = [r for r in bf.load_failed(s.paths).values() if r["kind"] == "tempest"]
    assert rec["code"] == "undecodable" and not rec["complete"] and rec["state"] == "recovered"
    assert Path(rec["raw"]).read_bytes() == b"{not json" and rec["repull"]["outcome"] == "recovered"
    assert len([c for c in s.fake.calls if c.url.path == "/v1/vol/history"]) == 2


def test_an_unreadable_window_answer_gets_one_paid_re_pull_within_the_cap(setup):
    """Owner 2026-10-06: a paid window whose kept body is itself unreadable (not a conversion bug) gets one paid
    re-pull while in reach, within the nightly cap; coverage.md lists it."""
    s = setup
    at = datetime(2025, 10, 21, 12, 0, tzinfo=ET)
    original, served = s.fake.handler, []

    def handler(request):
        if request.url.path == "/v1/historical/range" and parse_z(request.url.params["from"]).astimezone(ET) == at \
                and len(served) < 2:
            served.append(1)
            s.fake.calls.append(request)
            s.fake.credits -= 25
            return httpx.Response(200, content=b'{"data": {"symbols": [', headers={"X-Credits-Remaining": str(s.fake.credits)})
        return original(request)

    s.fake.handler = handler
    s.settings["skylit_dropped_retry_credits_per_night"] = 0  # night 1: no room for the re-pull
    _plan(s, approved=20_000)
    assert s.run().status == "complete"
    rec = bf.load_failed(s.paths)["2025-10-21 1200"]
    assert rec["code"] == "undecodable" and rec["charged"] and rec["complete"] is False and rec["state"] == "failed"
    assert Path(rec["raw"]).read_bytes() == b'{"data": {"symbols": [' and "repull" not in rec
    assert len(_calls_for(s, "2025-10-21", "1200")) == 1
    md = (bf.coverage_dir(s.paths) / "coverage.md").read_text(encoding="utf-8")
    assert "2025-10-21 1200: failed" in md and "one paid re-pull while in reach)" in md
    s.settings["skylit_dropped_retry_credits_per_night"] = 500
    assert s.run().status == "complete"  # the re-pull is unreadable again: its one re-pull is now used
    rec = bf.load_failed(s.paths)["2025-10-21 1200"]
    assert rec["state"] == "failed" and rec["repull"]["credits"] == 25 and len(_calls_for(s, "2025-10-21", "1200")) == 2
    assert s.run().status == "complete" and len(_calls_for(s, "2025-10-21", "1200")) == 2  # never a third
    md = (bf.coverage_dir(s.paths) / "coverage.md").read_text(encoding="utf-8")
    assert "2025-10-21 1200: 25 credits" in md and "its one re-pull is used)" in md


def test_an_unreadable_window_answer_re_pulled_successfully_is_recovered(setup):
    s = setup
    at = datetime(2025, 10, 21, 12, 0, tzinfo=ET)
    original, served = s.fake.handler, []

    def handler(request):
        if request.url.path == "/v1/historical/range" and parse_z(request.url.params["from"]).astimezone(ET) == at \
                and not served:
            served.append(1)
            s.fake.calls.append(request)
            s.fake.credits -= 25
            return httpx.Response(200, content=b"garbled", headers={"X-Credits-Remaining": str(s.fake.credits)})
        return original(request)

    s.fake.handler = handler
    _plan(s, approved=20_000)
    assert s.run().status == "complete"
    rec = bf.load_failed(s.paths)["2025-10-21 1200"]
    assert rec["state"] == "recovered" and rec["repull"]["outcome"] == "recovered"
    assert len(_calls_for(s, "2025-10-21", "1200")) == 2
    assert s.store.range_cached(date(2025, 10, 21), "1200",
                                sp.range_params_from_settings(s.settings, bf._windows_by_hhmm(date(2025, 10, 21), CAL)["1200"]))
