"""Step-10 Skylit backfill (presd_fixes issues 1, 2, 7, 9, 10): mocked Skylit API only, temporary SSD."""

import json
import math
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from nqbot import cli
from nqbot.checks import step7
from nqbot.clients import skylit_backfill as bf
from nqbot.clients import skylit_credits as credits
from nqbot.clients.skylit import SkylitError
from nqbot.clients.skylit_pull import (SessionResult, Window, pull_session, pull_window, range_params,
                                       range_params_from_settings, session_windows)
from nqbot.clients.skylit_store import SkylitStore
from nqbot.config import load_features
from nqbot.jobs.registry import JOBS
from nqbot.jobs.runner import StopChunk, run_job
from tests.skylit_fakes import FakeSkylit, parse_z, pass_strike_window, range_body, z
from tests.skylit_fakes import make_client as _make_client

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
CAL = {"holidays": [{"date": date(2025, 10, 13)}], "early_closes": [{"date": date(2025, 10, 24), "early_close_et": "13:00"}]}
NOW = datetime(2026, 10, 6, 10, 0, tzinfo=ET)  # reach = 2025-10-06 10:30 ET (margin 30 min)
OCT6_LOST = ["0930", "0945", "1000", "1015"]


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def _vol_route(r):
    syms = r.url.params["symbols"].split(",")
    return {"data": {"missing": [], "symbols": [{"symbol": s, "dates": [r.url.params["from"]], "columns": {"svx30": [20.0]}}
                                                for s in syms]}, "meta": {}}


def _dp_print(day, ticker="QQQ", notional=2e6):
    t = datetime.combine(day, time(11, 0), tzinfo=ET).astimezone(UTC)
    return {"timestamp": z(t), "ticker": ticker, "price": 600.0, "size": int(notional / 600), "notional": notional,
            "venue": "FINN", "pctAvgVol": 1.0}


@pytest.fixture
def setup(paths, settings, log):
    settings["months"] = {1: "2025-10"}  # a one-month ledger keeps the mocked backfill small
    pass_strike_window(paths, settings)
    fake = FakeSkylit()
    fake.routes["/v1/vol/history"] = _vol_route
    fake.dp_prints = [_dp_print(date(2025, 8, 1) + timedelta(days=i), t) for i in range(100) for t in ("QQQ", "SPY")
                      if (date(2025, 8, 1) + timedelta(days=i)).weekday() < 5]
    clock = Clock(NOW)
    kw: dict = {}
    slept: list[float] = []

    def client(settings_, store, log_):
        c = _make_client(fake, on_call=store.log_call, **kw)
        c.account()
        return c

    lines: list[str] = []
    env = step7.Env(settings=settings, features=load_features(), paths=paths, log=log,
                    client_factory=lambda store: client(settings, store, log), now=clock, echo=lines.append, cal=CAL)

    def job():
        return bf.SkylitBackfillJob(client_factory=client, now=clock, cal=CAL)

    def run(until=datetime(2026, 10, 7, 7, 30, tzinfo=ET)):
        return run_job(job(), settings, paths, until=until, log=log, now=clock, sleep=slept.append,
                       is_trading_day=lambda d: True)

    class S:
        pass

    s = S()
    s.fake, s.env, s.clock, s.job, s.run, s.settings, s.paths, s.lines, s.client_kw = fake, env, clock, job, run, \
        settings, paths, lines, kw
    s.store = SkylitStore(paths)
    s.slept = slept
    return s


def _plan(s, approved=None):
    return bf.make_plan(s.env, approved)


def _range_calls(fake):
    return [r for r in fake.calls if r.url.path == "/v1/historical/range"]


def _day_record(s, d):
    return json.loads((s.paths.data_dir / "jobs" / bf.JOB_NAME / "days" / f"{d}.json").read_text(encoding="utf-8"))


# --- reach rule -----------------------------------------------------------------------------------------------

def test_reach_is_a_timestamp_not_a_date(setup):
    s = setup
    plan = bf.plan_backfill(s.env, NOW, ["QQQ", "SPY"])
    assert plan.lost_days == [date(2025, 10, 1), date(2025, 10, 2), date(2025, 10, 3)]
    assert plan.days[0] == date(2025, 10, 6)  # its afternoon is still in reach
    assert plan.last == date(2025, 10, 31)
    assert plan.dp_dates[0] == date(2025, 8, 18)  # month 1's 45-day lookback starts Sat 2025-08-16
    assert step7._f4_lower(s.env, date(2025, 10, 1)) == date(2025, 8, 16)
    # the estimate counts only reachable past windows: Oct 6 from 10:30, a holiday probe, the early close
    n = bf.windows_todo(s.env, plan, NOW)
    expected = sum(len(session_windows(d, CAL)) for d in plan.days) - len(OCT6_LOST)
    assert n == expected


def test_settings_params_equal_client_params(setup):
    s = setup
    w = session_windows(date(2025, 10, 7), CAL)[0]
    assert range_params_from_settings(s.settings, w) == range_params(s.env.client, w)


def test_out_of_reach_answer_near_the_line_is_lost_elsewhere_raises(setup, log):
    s = setup
    client = s.env.client
    edge = Window(date(2025, 10, 6), datetime(2025, 10, 6, 10, 30, tzinfo=ET), datetime(2025, 10, 6, 10, 45, tzinfo=ET))
    s.fake.queue["/v1/historical/range"] = [(400, {"error": {"code": "INVALID_PARAMETER", "message": "too old"}}, {})]
    res = SessionResult(edge.day)
    assert pull_window(client, s.store, edge, result=res, log=log, now=lambda: NOW) is False
    assert res.lost == ["1030"] and not s.store.stored_windows(edge.day)  # not cached as empty
    inside = Window(date(2026, 3, 5), datetime(2026, 3, 5, 10, 0, tzinfo=ET), datetime(2026, 3, 5, 10, 15, tzinfo=ET))
    s.fake.queue["/v1/historical/range"] = [(400, {"error": {"code": "INVALID_PARAMETER", "message": "bad"}}, {})]
    with pytest.raises(SkylitError):
        pull_window(client, s.store, inside, result=SessionResult(inside.day), log=log, now=lambda: NOW)


# --- the job ----------------------------------------------------------------------------------------------------

def test_backfill_oldest_first_partial_day_dp_and_tempest_last(setup):
    s = setup
    assert "skylit_backfill" in JOBS
    _plan(s, approved=20_000)
    result = s.run()
    assert result.status == "complete", result.message
    chunks = json.loads((s.paths.data_dir / "jobs" / bf.JOB_NAME / "state.json").read_text())["runs"][-1]["chunks"]
    assert chunks == sorted(chunks[:-1]) + ["tempest_2025-10-31"]
    assert s.fake.calls[-1].url.path == "/v1/vol/history"  # Tempest last
    starts = [parse_z(r.url.params["from"]) for r in _range_calls(s.fake)]
    assert starts == sorted(starts)  # oldest window first
    oct6 = _day_record(s, "2025-10-06")["skylit"]
    assert oct6["lost"] == OCT6_LOST and len(oct6["pulled"]) == 22
    assert s.store.stored_windows(date(2025, 10, 6))[0] == "1030"
    hol = _day_record(s, "2025-10-13")["skylit"]
    assert hol["windows"] == 1 and len(hol["pulled"]) + len(hol["empty"]) >= 1
    assert _day_record(s, "2025-10-24")["skylit"]["windows"] == 15  # 09:30-13:00 + one probe
    cov = json.loads((s.paths.reports_dir / "step10" / "coverage.json").read_text())
    assert cov["skylit"]["beyond_365_days"]["fully_lost"] == ["2025-10-01", "2025-10-02", "2025-10-03"]
    assert cov["skylit"]["beyond_365_days"]["partial_days"] == {"2025-10-06": OCT6_LOST}
    assert cov["skylit"]["share_sessions_1m"] == 0.0
    assert cov["tempest"]["first_row"]["QQQ"] == cov["tempest"]["needed_from"]
    assert bf.fully_lost_days(s.paths) == {date(2025, 10, 1), date(2025, 10, 2), date(2025, 10, 3)}
    assert "Days beyond the 365-day limit" in (s.paths.reports_dir / "step10" / "coverage.md").read_text()
    spent = bf.spent_on_plan(s.store, bf.BackfillPlan.from_json(json.loads(bf.plan_path(s.paths).read_text())).plan_id)
    assert spent == 25 * len(_range_calls(s.fake)) + 5 * sum(r.url.path == "/v1/dark-pool/trades" for r in s.fake.calls) \
        + bf.vol_history_cost(2, *[date.fromisoformat(x) for x in (cov["tempest"]["needed_from"], "2025-10-31")])
    n = len(s.fake.calls)
    assert s.run().status == "complete" and len(s.fake.calls) - n <= 2  # nothing pulled twice (account call only)


def test_one_minute_share_and_dp_partial(setup):
    s = setup

    def mixed(r):
        start, end = parse_z(r.url.params["from"]), parse_z(r.url.params["to"])
        res = "1m" if start.astimezone(ET).date() == date(2025, 10, 8) and start.astimezone(ET).hour < 11 else "1s"
        return range_body(r.url.params["symbols"].split(","), start, end, 300, resolution=res)

    s.fake.routes["/v1/historical/range"] = mixed
    # no prints before 2025-09-02 (history starts there); 2025-09-15 refused as out of reach
    s.fake.dp_prints = [p for p in s.fake.dp_prints if p["timestamp"][:10] >= "2025-09-02"]
    s.fake.routes.pop("/v1/dark-pool/trades", None)
    original = s.fake.handler

    def handler(request):
        if request.url.path == "/v1/dark-pool/trades" and request.url.params.get("date") == "2025-09-15":
            s.fake.calls.append(request)
            import httpx
            return httpx.Response(400, json={"error": {"code": "INVALID_PARAMETER", "message": "too old"}},
                                  headers={"X-Credits-Remaining": str(s.fake.credits)})
        return original(request)

    s.fake.handler = handler
    _plan(s, approved=20_000)
    assert s.run().status == "complete"
    cov = json.loads((s.paths.reports_dir / "step10" / "coverage.json").read_text())
    assert cov["skylit"]["sessions_1m_dates"] == ["2025-10-08"]
    assert cov["skylit"]["per_month"]["month 1"]["sessions_1m"] == 1
    assert 0 < cov["skylit"]["share_windows_1m"] < cov["skylit"]["share_sessions_1m"]
    dp = cov["dark_pools"]
    assert dp["first_date_with_prints"] == "2025-09-02" and dp["status_counts"]["out_of_reach"] == 1
    assert "2025-09-15" in dp["not_pulled"] and "2025-08-29" in dp["not_pulled"]  # empty trading day: no coverage
    assert not s.store.dp_trades_path(date(2025, 8, 29)).exists()  # never cached as an empty day
    assert not s.store.dp_trades_path(date(2025, 9, 15)).exists()
    assert dp["sessions_dp_partial"] > 0


def test_balance_runs_out_stops_cleanly_and_resumes(setup):
    s = setup
    _plan(s, approved=20_000)
    s.fake.credits = 1_600  # the 1,000-credit reserve for the alongside checks + a few windows
    r = s.run()
    assert r.status == "credits" and "balance" in r.message and "kept for the step-7 checks" in r.message
    assert s.fake.credits >= 1_000  # the reserve is never spent
    n_windows = len(_range_calls(s.fake))
    assert 0 < n_windows < 30
    s.fake.credits = 100_000  # top-up
    assert s.run().status == "complete"
    froms = [r.url.params["from"] for r in _range_calls(s.fake)]
    assert len(froms) == len(set(froms))  # no window pulled twice


def test_402_stops_cleanly(setup):
    s = setup
    _plan(s, approved=20_000)
    s.fake.queue["/v1/dark-pool/trades"] = [(402, {"error": {"code": "insufficient_credits", "message": "no"}}, {})]
    r = s.run()
    assert r.status == "credits" and r.done == 0


def test_approval_is_never_exceeded(setup):
    s = setup
    _plan(s, approved=20_000)
    job = s.job()
    from nqbot.jobs.runner import Ctx
    ctx = Ctx(paths=s.paths, settings=s.settings, out_dir=s.paths.data_dir / "jobs" / bf.JOB_NAME, low_memory=True,
              max_workers=1, log=s.env.log, deadline=NOW + timedelta(days=1), should_stop=lambda: None)
    job.chunks(ctx)
    job._budget.approved = job._budget.spent + 60  # room for two range calls
    with pytest.raises(StopChunk) as e:
        job.run_chunk("2025-10-07", ctx)
    assert e.value.status == "credits" and len(_range_calls(s.fake)) == 2
    b = bf.Budget(approved=100, spent=95, balance=lambda: None)
    with pytest.raises(StopChunk):
        b.take(credits.DP_TRADES_PAGE + 1, "x")
    b.take(5, "x")
    assert b.spent == 100


def test_plan_refusals(setup):
    s = setup
    with pytest.raises(credits.CreditRefused, match="--approve-credits"):
        _plan(s, approved=None)  # over 5,000 credits without the owner's approval
    assert not bf.plan_path(s.paths).exists()
    with pytest.raises(credits.CreditRefused):
        _plan(s, approved=100)  # an approval smaller than the estimate
    assert not bf.plan_path(s.paths).exists()


def test_job_refuses_when_approval_is_short_or_strikes_changed(setup):
    s = setup
    plan = _plan(s, approved=20_000)
    data = plan.to_json()
    data["approved_credits"] = 1000
    bf.plan_path(s.paths).write_text(json.dumps(data))
    with pytest.raises(bf.BackfillRefused, match="what's left"):
        s.run()
    data["approved_credits"] = 20_000
    bf.plan_path(s.paths).write_text(json.dumps(data))
    s.settings["skylit_max_strikes"] = 300
    pass_strike_window(s.paths, s.settings)  # the owner raised it and re-checked (D2)
    with pytest.raises(bf.BackfillRefused, match="re-run nqbot skylit plan"):
        s.run()


# --- cached step-7 sample days (issue 10) ------------------------------------------------------------------------

DAY = date(2025, 10, 7)


def _pull_day(s, base=None, repull=None, **client_kw):
    from nqbot.clients.skylit_pull import SkylitPullJob
    SkylitPullJob.write_plan(s.paths, "skylit_sample_days", [DAY], None)  # DAY is a step-7 sample day
    c = _make_client(s.fake, on_call=s.store.log_call, **client_kw)
    return pull_session(c, s.store, DAY, log=s.env.log, cal=CAL, base=base, repull=repull)


def test_cached_sample_day_is_reused(setup):
    s = setup
    _pull_day(s)
    n = len(_range_calls(s.fake))
    _plan(s, approved=20_000)
    assert s.run().status == "complete"
    assert not any(parse_z(r.url.params["from"]).astimezone(ET).date() == DAY for r in _range_calls(s.fake)[n:])
    assert len(_day_record(s, DAY.isoformat())["skylit"]["cached"]) == 26


def test_raised_strikes_use_the_raise_copies(setup):
    s = setup
    _pull_day(s)  # sample day pulled with 200
    raise_base = s.store.base("strike_window_raise", pulled="max300")
    _pull_day(s, base=raise_base, repull="strike_window_raise", max_strikes=300)  # nqbot step7 sample-days --raised
    s.settings["skylit_max_strikes"] = 300
    pass_strike_window(s.paths, s.settings)  # the owner raised it and re-checked (D2)
    s.client_kw["max_strikes"] = 300
    n = len(_range_calls(s.fake))
    _plan(s, approved=20_000)
    assert s.run().status == "complete"
    assert not any(parse_z(r.url.params["from"]).astimezone(ET).date() == DAY for r in _range_calls(s.fake)[n:])
    reuse = _day_record(s, DAY.isoformat())["skylit"]["reuse"]
    assert len(reuse["copied_from_raise"]) == 26 and reuse["pull_again"] is False
    aside = s.store.base("strike_window_raise", pulled="superseded_max200")
    assert len(s.store.stored_windows(DAY, aside)) == 26  # moved, never deleted
    params = range_params_from_settings(s.settings, session_windows(DAY, CAL)[0])
    assert s.store.range_cached(DAY, "0930", params)


def test_raised_strikes_without_copies_pull_again(setup):
    s = setup
    _pull_day(s)
    s.settings["skylit_max_strikes"] = 300
    pass_strike_window(s.paths, s.settings)  # the owner raised it and re-checked (D2)
    s.client_kw["max_strikes"] = 300
    _plan(s, approved=20_000)
    assert s.run().status == "complete"
    again = [r for r in s.store.ledger() if r.get("repull") == "strike_window_raise" and r.get("job") == bf.JOB_NAME]
    assert len(again) == 26
    assert len(s.store.stored_windows(DAY, s.store.base("strike_window_raise", pulled="superseded_max200"))) == 26


def test_other_parameter_mismatch_refuses_at_start(setup):
    s = setup
    _pull_day(s, metric="vanna")
    with pytest.raises(bf.BackfillRefused, match="other parameters"):
        _plan(s, approved=20_000)


# --- step 7: depth probe and report -------------------------------------------------------------------------------

def test_depth_probe_best_effort_and_last(setup):
    s = setup
    env = s.env
    env.settings["months"] = {i + 1: f"{2025 + (9 + i) // 12}-{(9 + i) % 12 + 1:02d}" for i in range(12)}
    env.now = Clock(datetime(2026, 10, 6, 20, 0, tzinfo=ET))
    probe_day = "2025-08-18"
    original = s.fake.handler

    def handler(request):
        if request.url.path == "/v1/dark-pool/trades" and request.url.params.get("date") == probe_day:
            s.fake.calls.append(request)
            import httpx
            return httpx.Response(400, json={"error": {"code": "INVALID_PARAMETER", "message": "too old"}},
                                  headers={"X-Credits-Remaining": str(s.fake.credits)})
        return original(request)

    s.fake.handler = handler
    s.fake.dp_prints = [_dp_print(date(2026, 1, 20) + timedelta(days=i), t) for i in range(70) for t in ("QQQ", "SPY")
                        if (date(2026, 1, 20) + timedelta(days=i)).weekday() < 5]
    rep = step7.check_darkpool(env, date(2026, 3, 16), [])
    assert rep["depth_probe"]["status"] == "out_of_reach" and rep["depth_probe"]["date"] == probe_day
    last = s.fake.calls[-1]  # the probe runs after every other call of the check
    assert last.url.path == "/v1/dark-pool/trades" and last.url.params["date"] == probe_day
    assert not s.store.dp_trades_path(date(2025, 8, 18)).exists()
    n = len(s.fake.calls)
    again = step7.check_darkpool(env, date(2026, 3, 16), [])
    assert again["depth_probe"]["recorded_before"] and len(s.fake.calls) == n  # not paid for twice


def test_empty_probe_is_no_coverage_not_cached(setup):
    s = setup
    env = s.env
    env.settings["months"] = {i + 1: f"{2025 + (9 + i) // 12}-{(9 + i) % 12 + 1:02d}" for i in range(12)}
    env.now = Clock(datetime(2026, 10, 6, 20, 0, tzinfo=ET))
    s.fake.dp_prints = [p for p in s.fake.dp_prints if p["timestamp"][:10] > "2025-09-01"]
    s.fake.dp_prints += [_dp_print(date(2026, 1, 20) + timedelta(days=i), t) for i in range(70) for t in ("QQQ", "SPY")
                         if (date(2026, 1, 20) + timedelta(days=i)).weekday() < 5]
    rep = step7.check_darkpool(env, date(2026, 3, 16), [])
    assert rep["depth_probe"]["status"] == "no_coverage"
    assert not s.store.dp_trades_path(date(2025, 8, 18)).exists()


def test_cheapest_packs():
    packs = [{"credits": 55000, "usd": 50}, {"credits": 175000, "usd": 150}, {"credits": 480000, "usd": 400}]
    assert bf.cheapest_packs(60_000, packs)["buy"] == [{"credits": 55000, "usd": 50, "count": 2}]
    assert bf.cheapest_packs(55_000, packs)["usd"] == 50
    one = bf.cheapest_packs(120_000, packs)  # 3 x 55k and 1 x 175k both $150: fewer packs wins
    assert one["buy"] == [{"credits": 175000, "usd": 150, "count": 1}]
    big = bf.cheapest_packs(500_000, packs)
    assert big["usd"] == 450 and big["packs"] == 2


def test_report_shortfall_and_pending_sections(setup):
    s = setup
    env = s.env
    s.fake.credits = 5_000
    step7.check_symbols(env)
    text = step7.check_report(env)
    assert "pending (runs alongside)" in text and "MISSING" in text
    bf_ = step7.backfill_estimate(env)
    assert bf_["shortfall"] == bf_["with_margin"] - 5_000 > 0
    assert bf_["with_margin"] == math.ceil(bf_["total"] * 1.1)
    assert bf_["packs"]["buy"] == [{"credits": 55000, "usd": 50, "count": 1}]
    assert "**shortfall" in text and "nqbot skylit plan --approve-credits" in text


def test_report_balance_fallback_without_a_key(setup):
    s = setup
    env = s.env
    step7.check_symbols(env)  # balance 100,000 recorded
    from nqbot.secrets import MissingSecret

    def no_key(store):
        raise MissingSecret("SKYLIT_API_KEY")

    env2 = step7.Env(settings=env.settings, features=env.features, paths=env.paths, log=env.log, client_factory=no_key,
                     now=env.now, echo=lambda x: None, cal=CAL)
    out = step7.backfill_estimate(env2)
    assert out["balance"] == 100_000 and out["balance_source"].startswith("the symbols report")
    assert step7.check_report(env2)


def test_settings_keys():
    from nqbot.config import load_settings

    st = load_settings()
    assert st["skylit_reach_margin_min"] == 30 and st["skylit_pack_margin"] == 0.10
    assert [p["credits"] for p in st["skylit_credit_packs"]] == [55000, 175000, 480000]


def test_cli_run_skylit_backfill_is_a_job():
    with pytest.raises(SystemExit) as e:
        cli.main(["run", "skylit_backfill", "--until", "7pm"])  # parsed (choice valid), --until rejected
    assert e.value.code == 2
    with pytest.raises(SystemExit):
        cli.main(["skylit", "nosuch"])


# --- regression tests for the independent review (2026-10-05) ---------------------------------------------------

def test_replan_next_day_keeps_pulled_days(setup):
    """Finding 1: a day pulled on night 1 that has since left the reach is not 'lost' on a re-plan."""
    s = setup
    _plan(s, approved=20_000)
    assert s.run().status == "complete"
    s.clock.t = NOW + timedelta(days=3)  # 2025-10-06..08 are now beyond the reach
    plan = _plan(s, approved=20_000)
    assert date(2025, 10, 7) in plan.days and date(2025, 10, 7) not in plan.lost_days
    n = len(_range_calls(s.fake))
    assert s.run().status == "complete" and len(_range_calls(s.fake)) == n
    assert bf.fully_lost_days(s.paths) == {date(2025, 10, 1), date(2025, 10, 2), date(2025, 10, 3)}


def test_reach_is_dst_safe():
    """Finding 5: 365 days are subtracted in UTC, not ET wall-clock time."""
    from nqbot.clients.skylit_pull import reach
    now = datetime(2026, 11, 1, 12, 0, tzinfo=ET)  # EST; a year earlier was EDT
    assert reach(now, 0) == now.astimezone(UTC) - timedelta(days=365)
    assert reach(now, 0).astimezone(ET).hour == 13  # 2025-11-01 13:00 EDT = 17:00 UTC


def test_transient_failure_retries_the_chunk(setup):
    """Finding 4: a call that gave up after the client's retries is tried again in the same run."""
    s = setup
    _plan(s, approved=20_000)
    s.fake.queue["/v1/historical/range"] = [(503, {"error": {"code": "unavailable", "message": "x"}}, {})] * 5
    assert s.run().status == "complete"
    froms = [r.url.params["from"] for r in _range_calls(s.fake)]
    assert len(froms) == len(set(froms)) + 5 and sum(s.slept) == 60  # only the failed window was asked again
    assert not bf.load_failed(s.paths)  # it worked on the second try: never recorded


def test_crash_between_move_and_copy_uses_the_copy(setup):
    """Finding 10: windows moved aside by a crashed run are still replaced by their raise copies."""
    s = setup
    _pull_day(s)
    raise_base = s.store.base("strike_window_raise", pulled="max300")
    _pull_day(s, base=raise_base, repull="strike_window_raise", max_strikes=300)
    aside = s.store.base("strike_window_raise", pulled="superseded_max200")
    import os
    for hhmm in s.store.stored_windows(DAY):  # what a crashed run left: moved, not yet copied
        for src, dst in zip(s.store.range_paths(DAY, hhmm), s.store.range_paths(DAY, hhmm, aside)):
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dst)
    s.settings["skylit_max_strikes"] = 300
    pass_strike_window(s.paths, s.settings)  # the owner raised it and re-checked (D2)
    s.client_kw["max_strikes"] = 300
    n = len(_range_calls(s.fake))
    _plan(s, approved=20_000)
    assert s.run().status == "complete"
    assert not any(parse_z(r.url.params["from"]).astimezone(ET).date() == DAY for r in _range_calls(s.fake)[n:])


def test_probe_record_is_reused_and_dp_mismatch_refuses(setup):
    """Findings 7 and 9."""
    s = setup
    first = bf.plan_backfill(s.env, NOW, ["QQQ", "SPY"]).dp_dates[0]
    rec = s.store.root / "darkpool" / "probe" / f"{first}.json"
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps({"date": first.isoformat(), "status": "no_coverage"}))
    _plan(s, approved=20_000)
    assert s.run().status == "complete"
    assert not any(r.url.params.get("date") == first.isoformat() for r in s.fake.calls)
    assert _day_record(s, first.isoformat())["dp"]["from_probe"]
    s.env.features["dp_min_notional"] = 500_000  # cached dates now differ
    with pytest.raises(bf.BackfillRefused, match="dark pools"):
        _plan(s, approved=20_000)


# --- regression tests for the second independent review (2026-10-05) ---------------------------------------------

def _ctx(s):
    from nqbot.jobs.runner import Ctx
    return Ctx(paths=s.paths, settings=s.settings, out_dir=bf.job_dir(s.paths), low_memory=True, max_workers=1,
               log=s.env.log, deadline=NOW + timedelta(days=1), should_stop=lambda: None, sleep=lambda x: None)


def _dp_answer(s, day, status, code):
    import httpx
    original = s.fake.handler

    def handler(request):
        if request.url.path == "/v1/dark-pool/trades" and request.url.params.get("date") == day:
            s.fake.calls.append(request)
            return httpx.Response(status, json={"error": {"code": code, "message": "x"}},
                                  headers={"X-Credits-Remaining": str(s.fake.credits)})
        return original(request)

    s.fake.handler = handler


def test_dp_400_inside_the_reach_is_recorded_and_retried_once(setup):
    """D1: a refusal well inside the reach is an error, not "out of reach": recorded, the job carries on, and the
    date is asked once more at the end of the night."""
    s = setup
    _dp_answer(s, "2025-10-20", 400, "INVALID_PARAMETER")
    _plan(s, approved=20_000)
    assert s.run().status == "complete"
    assert _day_record(s, "2025-10-20")["dp"]["status"] == "failed"
    rec = bf.load_failed(s.paths)["dp 2025-10-20"]
    assert rec["state"] == "failed" and rec["retries"] == 1 and rec["status"] == 400 and not rec["charged"]
    calls = [c for c in _dp_calls(s.fake) if c.get("date") == "2025-10-20"]
    assert len(calls) == 2  # the main pass and the end-of-night retry
    plan = bf.BackfillPlan.from_json(json.loads(bf.plan_path(s.paths).read_text(encoding="utf-8")))
    assert date(2025, 10, 20) not in bf.dp_todo(s.env, plan)  # never asked again


def test_a_window_that_keeps_failing_is_recorded_then_retried_once_at_the_end(setup):
    """D1 (decision 4): after the 1/5/15-minute waits the window is recorded and the job carries on; the retry
    pass at the end of the night asks it once more."""
    s = setup
    _plan(s, approved=20_000)
    s.fake.queue["/v1/historical/range"] = [(503, {"error": {"code": "unavailable", "message": "x"}}, {})] * 20
    r = s.run()
    assert r.status == "complete" and sum(s.slept) == 60 + 300 + 900
    rec = bf.load_failed(s.paths)["2025-10-06 1030"]
    assert rec["code"] == "gave_up" and rec["retries"] == 1 and rec["state"] == "recovered"
    assert s.store.range_cached(date(2025, 10, 6), "1030",
                                range_params_from_settings(s.settings, bf._windows_by_hhmm(date(2025, 10, 6), CAL)["1030"]))
    assert "1030" in _day_record(s, "2025-10-06")["skylit"]["failed"]


def test_dp_reserve_stops_before_a_date_it_cannot_finish(setup):
    s = setup
    _plan(s, approved=20_000)
    job = s.job()
    ctx = _ctx(s)
    job.chunks(ctx)
    first = bf.BackfillPlan.from_json(json.loads(bf.plan_path(s.paths).read_text())).dp_dates[0]
    job._budget.approved = job._budget.spent + 9  # a date needs a reserve of 2 pages = 10 credits
    with pytest.raises(StopChunk) as e:
        job.run_chunk(first.isoformat(), ctx)
    assert e.value.status == "credits" and not any(r.url.path == "/v1/dark-pool/trades" for r in s.fake.calls)


def test_backfill_day_with_older_strikes_refuses(setup):
    """Review 2: a non-sample day pulled with another skylit_max_strikes would mix strike sets."""
    s = setup
    c = _make_client(s.fake, on_call=s.store.log_call)
    pull_session(c, s.store, date(2025, 10, 9), log=s.env.log, cal=CAL)
    s.settings["skylit_max_strikes"] = 300
    pass_strike_window(s.paths, s.settings)  # the owner raised it and re-checked (D2)
    s.client_kw["max_strikes"] = 300
    with pytest.raises(bf.BackfillRefused, match="mix strike sets"):
        _plan(s, approved=20_000)


def test_replan_estimate_skips_settled_work_and_tempest_adds_only_the_tail(setup):
    s = setup
    _plan(s, approved=20_000)
    assert s.run().status == "complete"
    s.clock.t = NOW + timedelta(days=2)
    plan = _plan(s, approved=20_000)
    assert plan.estimate_total == 0  # empty and lost windows, refused dates: settled, not counted again
    approvals = (bf.job_dir(s.paths) / "approvals.jsonl").read_text().splitlines()
    assert len(approvals) == 2 and json.loads(approvals[1])["spent_before"] > 0
    s.settings["months"] = {1: "2025-10", 2: "2025-11"}  # a longer window: Tempest pulls only November
    plan = _plan(s, approved=50_000)
    assert bf.tempest_todo(s.store, plan) == (date(2025, 11, 1), date(2025, 11, 30))
    assert s.run(until=NOW + timedelta(days=10)).status == "complete"
    vol = [r for r in s.fake.calls if r.url.path == "/v1/vol/history"]
    assert vol[-1].url.params["from"] == "2025-11-01" and bf.tempest_todo(s.store, plan) is None


def test_coverage_lists_dates_only_for_development_months(setup):
    s = setup
    s.settings["months"] = {i + 1: f"{2025 + (9 + i) // 12}-{(9 + i) % 12 + 1:02d}" for i in range(12)}
    plan = bf.plan_backfill(s.env, NOW, ["QQQ", "SPY"])
    out = bf.job_dir(s.paths)
    rec = {"date": "2026-08-03", "skylit": {"windows": 26, "pulled": ["0945"], "cached": [], "empty": [],
                                           "lost": ["0930"], "reuse": {}, "resolutions": {"0945": "1m"}}}
    (out / "days").mkdir(parents=True)
    (out / "days" / "2026-08-03.json").write_text(json.dumps(rec))
    cov = bf.coverage(s.env, plan, out)
    assert cov["skylit"]["sessions_1m"] == 1 and cov["skylit"]["sessions_1m_dates"] == []  # sealed month 11
    assert cov["skylit"]["beyond_365_days"]["partial_count"] == 1
    assert not cov["skylit"]["beyond_365_days"]["partial_days"]
    assert "2026-08-03" not in json.dumps({k: v for k, v in cov.items() if k != "_lost_all"})


def test_wholly_lost_day_is_listed_once(setup):
    s = setup
    plan = bf.plan_backfill(s.env, NOW, ["QQQ", "SPY"])
    plan.lost_days.append(date(2025, 10, 6))
    out = bf.job_dir(s.paths)
    (out / "days").mkdir(parents=True)
    (out / "days" / "2025-10-06.json").write_text(json.dumps({"date": "2025-10-06", "skylit": {
        "windows": 26, "pulled": [], "cached": [], "empty": [], "lost": ["0930"], "reuse": {}, "resolutions": {}}}))
    cov = bf.coverage(s.env, plan, out)
    assert cov["skylit"]["beyond_365_days"]["fully_lost"].count("2025-10-06") == 1


def test_cheapest_packs_edges():
    assert bf.cheapest_packs(0, [])["buy"] == []
    with pytest.raises(ValueError):
        bf.cheapest_packs(10, [])


def test_report_balance_unknown(setup):
    s = setup
    from nqbot.secrets import MissingSecret

    def no_key(store):
        raise MissingSecret("SKYLIT_API_KEY")

    env2 = step7.Env(settings=s.settings, features=s.env.features, paths=s.paths, log=s.env.log,
                     client_factory=no_key, now=s.clock, echo=lambda x: None, cal=CAL)
    out = step7.backfill_estimate(env2)
    assert out["balance"] is None and out["shortfall"] is None and out["balance_source"].startswith("unknown")
    assert "shortfall: unknown" in step7.check_report(env2)


def test_depth_probe_error_is_reported_not_recorded(setup):
    s = setup
    env = s.env
    env.settings["months"] = {i + 1: f"{2025 + (9 + i) // 12}-{(9 + i) % 12 + 1:02d}" for i in range(12)}
    env.now = Clock(datetime(2026, 10, 6, 20, 0, tzinfo=ET))
    s.fake.dp_prints = [_dp_print(date(2026, 1, 20) + timedelta(days=i), t) for i in range(70) for t in ("QQQ", "SPY")
                        if (date(2026, 1, 20) + timedelta(days=i)).weekday() < 5]
    _dp_answer(s, "2025-08-18", 401, "unauthorized")
    rep = step7.check_darkpool(env, date(2026, 3, 16), [])
    assert rep["depth_probe"]["status"] == "error"
    assert not (s.store.root / "darkpool" / "probe" / "2025-08-18.json").exists()  # may be tried again


def test_cli_skylit_plan_end_to_end(setup, monkeypatch, capsys):
    s = setup
    from nqbot.clients import skylit_pull

    def fake_client(settings_, store, log_, **kw):
        c = _make_client(s.fake, on_call=store.log_call)
        c.account()
        return c

    monkeypatch.setattr(cli, "load_settings", lambda: s.settings)
    monkeypatch.setattr(skylit_pull, "make_client", fake_client)
    import functools
    monkeypatch.setattr(step7, "Env", functools.partial(step7.Env, now=lambda: NOW, cal=CAL))
    assert cli.main(["skylit", "plan"]) == cli.EXIT_REFUSED  # over 5,000 credits, no approval
    assert "--approve-credits" in capsys.readouterr().err and not bf.plan_path(s.paths).exists()
    assert cli.main(["skylit", "plan", "--approve-credits", "20000"]) == 0
    assert bf.plan_path(s.paths).exists()


# --- 31-day dark-pool calls (presd_fixes 17, owner 2026-10-05) ------------------------------------------------

def _range_report(s, matches=True, prints=2, dates=1):
    step7.write_report(s.env, "darkpool", {"prints": prints, "dates_pulled": dates,
                                           "range_check": {"matches": matches, "mismatches": []}})


def _dp_calls(fake):
    return [r.url.params for r in fake.calls if r.url.path == "/v1/dark-pool/trades"]


def test_dp_range_groups_keep_the_edge_one_date_per_call():
    edge = date(2025, 8, 20)
    todo = [date(2025, 8, 18), date(2025, 8, 19), date(2025, 8, 20), date(2025, 8, 21), date(2025, 8, 22),
            date(2025, 9, 19), date(2025, 9, 22), date(2025, 10, 31)]
    groups = bf.dp_range_groups(todo, edge)
    assert groups == [[date(2025, 8, 18)], [date(2025, 8, 19)], [date(2025, 8, 20)], [date(2025, 8, 21)],  # edge + 1 day
                      [date(2025, 8, 22)], [date(2025, 9, 19), date(2025, 9, 22)], [date(2025, 10, 31)]]  # gaps split
    run = [date(2025, 9, 1) + timedelta(days=i) for i in range(40) if (date(2025, 9, 1) + timedelta(days=i)).weekday() < 5]
    assert [len(g) for g in bf.dp_range_groups(run, edge)] == [23, 7]  # 9/1..10/1 (31 days), then 10/2..10/10
    assert all(g[-1] - g[0] <= timedelta(days=30) for g in groups)


def test_backfill_uses_31_day_calls_once_step7_matched(setup):
    s = setup
    s.clock.t = datetime(2026, 1, 15, 10, 0, tzinfo=ET)  # the whole dark-pool span is well inside the reach
    _range_report(s)
    plan = _plan(s, approved=20_000)
    assert plan.dp_range
    est_dp = next(line for line in bf.estimate(s.env, plan, s.clock.t).lines if "dark-pool" in line[0])
    assert "31-day" in est_dp[0] and est_dp[1] == 3  # 8/18..9/17, 9/18..10/17, 10/20..10/31: 1 page each
    assert s.run().status == "complete"
    calls = _dp_calls(s.fake)
    assert all("date_start" in q for q in calls) and len(calls) == 3
    assert [(q["date_start"], q["date_end"]) for q in calls][0] == ("2025-08-18", "2025-09-17")
    for d in plan.dp_dates:  # every date cached per date, same parameters as a one-date call
        assert bf.read_meta(s.store.dp_trades_path(d))["params"] == step7._dp_params(s.env, d)
    assert len(step7.read_prints([s.store.dp_trades_path(date(2025, 9, 2))])) == 2
    n = len(s.fake.calls)
    assert s.run().status == "complete" and len(s.fake.calls) - n <= 2  # nothing pulled twice


def test_backfill_stays_one_date_per_call_without_the_step7_match(setup):
    s = setup
    s.clock.t = datetime(2026, 1, 15, 10, 0, tzinfo=ET)
    _range_report(s, matches=False)
    plan = _plan(s, approved=20_000)
    assert not plan.dp_range
    assert s.run().status == "complete"
    assert all("date" in q and "date_start" not in q for q in _dp_calls(s.fake))


def test_an_empty_trading_day_in_a_range_is_not_asked_again(setup):
    s = setup
    s.clock.t = datetime(2026, 1, 15, 10, 0, tzinfo=ET)
    gap = date(2025, 9, 3)
    s.fake.dp_prints = [p for p in s.fake.dp_prints if not p["timestamp"].startswith(gap.isoformat())]
    _range_report(s)
    _plan(s, approved=20_000)
    assert s.run().status == "complete"
    assert not s.store.dp_trades_path(gap).exists()  # no coverage: never cached as a permanent empty day
    assert _day_record(s, gap.isoformat())["dp"] == {"status": "no_coverage", "prints": 0, "via_range": True}
    assert len(_dp_calls(s.fake)) == 3  # the gap's own chunk didn't start another range call


def test_range_groups_never_span_a_date_already_paid_for():
    edge = date(2025, 1, 1)
    todo = [date(2025, 9, 1), date(2025, 9, 2), date(2025, 9, 4), date(2025, 9, 5)]  # 9/3 cached
    assert bf.dp_range_groups(todo, edge) == [[date(2025, 9, 1), date(2025, 9, 2)], [date(2025, 9, 4), date(2025, 9, 5)]]
    fri_mon = [date(2025, 9, 5), date(2025, 9, 8)]  # a weekend in between is fine
    assert bf.dp_range_groups(fri_mon, edge) == [fri_mon]


def test_range_no_data_404_settles_the_dates(setup):
    s = setup
    s.clock.t = datetime(2026, 1, 15, 10, 0, tzinfo=ET)
    s.fake.dp_prints = []
    _range_report(s)
    plan = _plan(s, approved=20_000)
    s.fake.queue["/v1/dark-pool/trades"] = [(404, {"error": {"code": "no_data", "message": "none"}}, {})] * 3
    assert s.run().status == "complete"
    rec = _day_record(s, plan.dp_dates[0].isoformat())["dp"]
    assert rec["status"] == "no_coverage" and rec["via_range"]
    n = len(_dp_calls(s.fake))
    assert s.run().status == "complete" and len(_dp_calls(s.fake)) == n  # never asked again


def test_a_range_answer_is_kept_before_it_is_split(setup):
    """A range answer that can't be split (a print outside its dates) is kept: re-runs never pay for it again."""
    s = setup
    s.clock.t = datetime(2026, 1, 15, 10, 0, tzinfo=ET)
    _range_report(s)
    _plan(s, approved=20_000)
    stray = _dp_print(date(2025, 12, 1))
    real = s.fake._dp_page
    s.fake._dp_page = lambda q: {**real(q), "data": real(q)["data"] + ([stray] if "date_start" in q else [])}
    r = s.run()  # each call's dates are recorded together with it (check D): three failures, never five in a row
    assert r.status == "complete"
    n = len(_dp_calls(s.fake))
    assert n == 3 and s.run().status == "complete"
    assert len(_dp_calls(s.fake)) == n  # never paid again
    failed = bf.load_failed(s.paths)
    assert all(v["charged"] and v["code"] == "range_date" for v in failed.values())
    plan = bf.BackfillPlan.from_json(json.loads(bf.plan_path(s.paths).read_text(encoding="utf-8")))
    assert {bf.dp_key(d) for d in plan.dp_dates} <= set(failed)
    assert list((s.store.root / "darkpool" / "range").glob("*.parquet"))


def test_a_crash_before_the_range_record_never_pays_again(setup, monkeypatch):
    """Fresh review #1: the per-date files are written, then the process dies before the dp_range record: the empty
    trading day in that range is settled again from the kept answer, never by a new call (rule 2)."""
    s = setup
    s.clock.t = datetime(2026, 1, 15, 10, 0, tzinfo=ET)
    gap = date(2025, 9, 3)
    s.fake.dp_prints = [p for p in s.fake.dp_prints if not p["timestamp"].startswith(gap.isoformat())]
    _range_report(s)
    _plan(s, approved=20_000)
    real, failed = bf.atomic_write_json, []

    def crash_once(path, obj):
        if "dp_range" in str(path) and not failed:
            failed.append(path)
            raise OSError("unplugged")
        real(path, obj)

    monkeypatch.setattr(bf, "atomic_write_json", crash_once)
    with pytest.raises(OSError):
        s.run()
    n = len(_dp_calls(s.fake))
    assert s.run().status == "complete"
    later = _dp_calls(s.fake)[n:]
    assert all(q["date_start"] > "2025-09-17" for q in later)  # 8/18..9/17 (gap included) never asked again
    assert _day_record(s, gap.isoformat())["dp"]["status"] == "no_coverage"
