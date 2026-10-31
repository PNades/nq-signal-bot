"""Regression tests for the step-7 review findings (2026-10-05), and the paths the review found untested."""

import asyncio
import json
import shutil
import struct
import time as _time
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pyarrow as pa
import pytest

from nqbot import cli, months, storage
from nqbot.checks import step7
from nqbot.checks import step7_analysis as an
from nqbot.clients import skylit_pull as sp
from nqbot.clients.skylit import SkylitError
from nqbot.clients.skylit_pull import SessionResult, SkylitPullJob, pull_session, pull_window, session_windows
from nqbot.clients.skylit_store import SkylitStore
from nqbot.clients.skylit_stream import SkylitStream
from nqbot.config import load_features
from nqbot.jobs.lock import ALL_LOCKS, DATABENTO, SKYLIT, lock_path
from nqbot.jobs.runner import run_job
from nqbot.secrets import Secret
from tests.skylit_fakes import KEY, SYMBOLS_MAP, FakeClock, FakeSkylit, sse, z
from tests.skylit_fakes import make_client as _make_client

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
MONTHS = {i + 1: f"{2025 + (9 + i) // 12}-{(9 + i) % 12 + 1:02d}" for i in range(12)}
CAL = {"holidays": [{"date": date(2026, 4, 3)}], "early_closes": []}

# the fakes date their prints in 2025: before the real dp_history_start (step 7, 2026-10-06)
def _features():
    return {**load_features(), "dp_history_start": "2025-01-01"}


def client_for(fake, store, clock=None):
    c = _make_client(fake, clock, on_call=store.log_call)
    c.account()
    return c


class Now:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def env(paths, settings, log):
    settings["months"] = dict(MONTHS)
    fake = FakeSkylit()
    e = step7.Env(settings=settings, features=_features(), paths=paths, log=log,
                  client_factory=lambda store: client_for(fake, store),
                  now=Now(datetime(2026, 10, 6, 20, 0, tzinfo=ET)), echo=lambda s: None,
                  clock_offset_ms=lambda: 1.5)
    e.fake = fake
    return e


# --- 1. a cached empty holiday probe stays empty --------------------------------------------------------

def test_cached_empty_probe_does_not_pull_the_holiday(paths, log):
    fake = FakeSkylit()
    store = SkylitStore(paths)
    client = client_for(fake, store)
    probe_start = datetime(2026, 4, 3, 9, 30, tzinfo=ET)
    empty = {"data": {"from": z(probe_start), "to": z(probe_start), "symbols": []},
             "meta": {"metric": "gamma", "resolution": "1s", "mode": "historical", "cached": False}}
    fake.queue["/v1/historical/range"] = [(200, empty, {})]
    first = pull_session(client, store, date(2026, 4, 3), log=log, cal=CAL)
    assert first.empty == ["0930"] and first.pulled == ["0930"]
    n = len(fake.calls)
    again = pull_session(client, store, date(2026, 4, 3), log=log, cal=CAL)
    assert len(fake.calls) == n and again.cached == ["0930"]  # no 25-window pull of a closed day


def test_probe_with_symbol_not_found_is_empty_but_a_normal_window_raises(paths, log):
    fake = FakeSkylit()
    store = SkylitStore(paths)
    client = client_for(fake, store)
    err = (404, {"error": {"code": "symbol_not_found", "message": "none"}}, {})
    fake.queue["/v1/historical/range"] = [err]
    assert pull_session(client, store, date(2026, 4, 3), log=log, cal=CAL).empty == ["0930"]
    fake.queue["/v1/historical/range"] = [err]
    with pytest.raises(SkylitError):
        pull_window(client, store, session_windows(date(2026, 3, 5), CAL)[0], result=SessionResult(date(2026, 3, 5)), log=log)


# --- 6. charged calls always reach the ledger; a charged 200 is never retried ------------------------------

def test_undecodable_200_is_not_retried_and_kept(paths, log):
    fake = FakeSkylit()
    store = SkylitStore(paths)
    client = client_for(fake, store)
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/v1/account":
            return httpx.Response(200, json={"data": {"creditsBalance": 1000}}, headers={"X-Credits-Remaining": "1000"})
        return httpx.Response(200, content=b"{not json", headers={"X-Credits-Remaining": "975"})

    client._http = httpx.Client(base_url="https://api.skylit.ai", transport=httpx.MockTransport(handler))
    w = session_windows(date(2026, 3, 5), CAL)[0]
    with pytest.raises(SkylitError, match="undecodable"):
        pull_window(client, store, w, result=SessionResult(w.day), log=log)
    assert len([c for c in calls if c.url.path == "/v1/historical/range"]) == 1
    assert (store.root / "failed").exists() and next((store.root / "failed").iterdir()).read_bytes() == b"{not json"
    assert store.ledger()[-1]["error"].startswith("undecodable") and store.ledger()[-1]["window"] == "2026-03-05 0930"


def test_unconvertible_answer_is_kept(paths, log):
    fake = FakeSkylit()
    store = SkylitStore(paths)
    client = client_for(fake, store)
    fake.routes["/v1/historical/range"] = lambda r: {"data": {"symbols": [{"symbol": "AAPL", "axes": [], "frames": []}]},
                                                     "meta": {"resolution": "1s"}}
    w = session_windows(date(2026, 3, 5), CAL)[0]
    from nqbot.clients.skylit_pull import WindowFailed

    with pytest.raises(WindowFailed, match="kept in") as e:
        pull_window(client, store, w, result=SessionResult(w.day), log=log)
    assert e.value.charged and e.value.raw_path.exists()
    assert len(list((store.root / "failed").iterdir())) == 1


def test_network_error_retried_and_negative_charge_ignored():
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"data": {"symbols": []}}, headers={"X-Credits-Remaining": "5000"})

    from nqbot.clients.skylit import SkylitClient

    clock = FakeClock()
    c = SkylitClient(Secret(KEY), symbols_map=SYMBOLS_MAP, transport=httpx.MockTransport(handler), mono=clock.mono,
                     wall=clock.wall, sleep=clock.sleep, rng=lambda: 0.5)
    c.credits.remaining = 100  # then the monthly reset raises the balance to 5,000
    _, rec = c.get("/v1/symbols")
    assert len(attempts) == 2 and rec.charged is None and c.credits.remaining == 5000


def test_historical_and_vol_status_endpoints():
    fake = FakeSkylit()
    fake.routes["/v1/historical"] = lambda r: {"data": {"symbols": []}, "meta": {}}
    fake.routes["/v1/vol/status"] = lambda r: {"data": {"frozen": True}, "meta": {}}
    c = _make_client(fake)
    _, rec = c.historical(["QQQ", "SPX"], datetime(2026, 3, 5, 15, 0, tzinfo=UTC))
    q = fake.calls[-1].url.params
    assert q["symbols"] == "QQQ,SPXW" and q["at"] == "2026-03-05T15:00:00.000Z" and q["maxExpirations"] == "1"
    assert c.vol_status()["data"]["frozen"] is True


def test_dark_pool_page_failure_still_logs_paid_pages(env, monkeypatch):
    import nqbot.clients.skylit as sk

    monkeypatch.setattr(sk, "DP_PAGE_LIMIT", 1)
    env.fake.dp_prints = [{"timestamp": "2026-01-05T15:00:00.000Z", "ticker": "QQQ", "price": 1.0, "size": 1,
                           "notional": 2e6}] * 1
    env.fake.queue["/v1/dark-pool/trades"] = [
        (200, {"data": env.fake.dp_prints, "meta": {"limit": 1, "count": 1, "nextCursor": "1"}}, {}),
        (400, {"error": {"code": "INVALID_PARAMETER", "message": "bad cursor"}}, {}),
    ]
    with pytest.raises(SkylitError):
        step7.pull_dp_date(env, date(2026, 1, 5))
    paid = [r for r in env.store.ledger() if r["endpoint"] == "/v1/dark-pool/trades"]
    assert paid[0]["status"] == 200 and paid[0]["charged"] == 5 and paid[0]["dp_date"] == "2026-01-05"
    assert not env.store.dp_trades_path(date(2026, 1, 5)).exists()  # nothing half-cached


# --- 2. dark-pool no-data handling ------------------------------------------------------------------------

def test_dark_pool_404_handling(env):
    env.fake.queue["/v1/dark-pool/trades"] = [(404, {"error": {"code": "SYMBOL_NOT_FOUND", "message": "x"}}, {})]
    with pytest.raises(SkylitError, match="SYMBOL_NOT_FOUND"):
        step7.pull_dp_date(env, date(2026, 1, 5))
    env.now.t = datetime(2026, 10, 6, 8, 0, tzinfo=ET)
    env.fake.queue["/v1/dark-pool/trades"] = [(404, {"error": {"code": "NOT_FOUND", "message": "none yet"}}, {})]
    with pytest.raises(step7.CheckRefused, match="not cached"):
        step7.pull_dp_date(env, date(2026, 10, 5))  # yesterday's prints may not be published yet
    assert not env.store.dp_trades_path(date(2026, 10, 5)).exists()
    env.fake.queue["/v1/dark-pool/trades"] = [(404, {"error": {"code": "NOT_FOUND", "message": "none"}}, {})]
    path = step7.pull_dp_date(env, date(2026, 1, 5))  # a settled date with no prints: cached as empty
    meta = json.loads(pa.parquet.read_schema(path).metadata[b"nqbot"])
    assert meta["provisional"] is False and meta["pulled_at"].startswith("2026-10-06")


def test_recent_dates_are_marked_provisional(env):
    env.now.t = datetime(2026, 10, 6, 8, 0, tzinfo=ET)
    env.fake.dp_prints = [{"timestamp": "2026-10-05T15:00:00.000Z", "ticker": "QQQ", "price": 1.0, "size": 1, "notional": 2e6}]
    meta = json.loads(pa.parquet.read_schema(step7.pull_dp_date(env, date(2026, 10, 5))).metadata[b"nqbot"])
    assert meta["provisional"] is True


# --- 4/5. late prints survive a drive-letter change and count each morning once --------------------------

def _dp(day, hour, notional):
    return {"timestamp": z(datetime.combine(day, time(hour), tzinfo=ET)), "ticker": "QQQ", "price": 600.0,
            "size": int(notional / 600), "notional": notional, "venue": "FINN", "pctAvgVol": 1.0}


def test_late_prints_relative_paths_and_dedupe(env, tmp_path, monkeypatch, settings):
    prev = date(2026, 3, 13)
    env.fake.dp_prints = [_dp(prev - timedelta(days=i), 11, 2e6 + i) for i in range(40) if (prev - timedelta(days=i)).weekday() < 5]
    env.now.t = datetime(2026, 3, 16, 8, 0, tzinfo=ET)
    step7.check_late_prints_morning(env)
    env.now.t = datetime(2026, 3, 16, 8, 5, tzinfo=ET)
    step7.check_late_prints_morning(env)  # run twice the same morning
    # the SSD comes back under another drive letter
    new_drive = tmp_path / "other_letter"
    shutil.copytree(env.paths.drive, new_drive)
    monkeypatch.setattr(storage, "candidate_drives", lambda: [new_drive])
    env2 = step7.Env(settings=env.settings, features=env.features, paths=storage.resolve_paths(settings), log=env.log,
                     client_factory=env.client_factory, now=Now(datetime(2026, 3, 20, 18, 0, tzinfo=ET)), echo=lambda s: None)
    rep = step7.check_late_prints_compare(env2)
    assert rep["mornings_saved"] == 1 and len(rep["compared"]) == 1
    assert rep["compared"][0]["added"] == 0 and not any(rep["compared"][0]["top_changed"].values())


def test_read_prints_refuses_missing_files(tmp_path):
    with pytest.raises(FileNotFoundError):
        step7.read_prints([tmp_path / "nope.parquet"])


# --- 8. the dark-pool check pulls the whole F4 lookback for a Monday ---------------------------------------

def test_darkpool_pull_covers_f4_lookback_on_a_monday(env):
    env.fake.routes["/v1/dark-pool/top-prints/QQQ"] = lambda r: {"data": [], "meta": {}}
    env.fake.routes["/v1/dark-pool/top-prints/SPY"] = lambda r: {"data": [], "meta": {}}
    monday = date(2026, 3, 16)
    step7.check_darkpool(env, monday, [], depth_probe=False)
    pulled = sorted(r.url.params["date"] for r in env.fake.calls if r.url.path == "/v1/dark-pool/trades"
              and "date" in r.url.params)  # one-date calls (the 31-day comparison call is apart)
    f4_lower = (datetime(2026, 3, 13, 16, 0, tzinfo=ET) - timedelta(days=45)).date()  # Friday's cutoff - 45 days
    assert pulled[0] <= f4_lower.isoformat()


def test_depth_probe_skips_holidays(env):
    env.fake.routes["/v1/dark-pool/top-prints/QQQ"] = lambda r: {"data": [], "meta": {}}
    env.fake.routes["/v1/dark-pool/top-prints/SPY"] = lambda r: {"data": [], "meta": {}}
    env.cal = {"holidays": [{"date": date(2025, 8, 18)}], "early_closes": []}
    rep = step7.check_darkpool(env, date(2026, 3, 16), [])
    assert rep["depth_probe"]["date"] == "2025-08-19"


# --- 7. sample-day credits exclude the full-chain call ----------------------------------------------------

def test_sample_day_credits_not_inflated_by_full_chain(env):
    day = date(2026, 3, 2)
    step7.check_strike_window(env, date(2026, 1, 15), [day])
    pull_session(env.client, env.store, day, log=env.log)
    rep = step7.sample_days_report(env, [day])
    assert rep["per_day"]["2026-03-02"]["credits"] == 650


def test_raised_window_repull_goes_to_its_own_folder(env, settings, clock, monkeypatch):
    monkeypatch.setattr(sp.cal_mod, "is_trading_day", lambda d, cal=None: d.weekday() < 5)
    monkeypatch.setattr(sp.cal_mod, "early_close_et", lambda d, cal=None: None)
    days = [date(2026, 3, 2 + i) for i in range(5)]
    step7.plan_sample_days(env, days, approved=None)
    job = SkylitPullJob("skylit_sample_days", client_factory=lambda s, store, lg: client_for(env.fake, store))
    kw = dict(until=clock.now() + timedelta(hours=8), log=env.log, now=clock.now, sleep=clock.sleep,
              is_trading_day=lambda d: d.weekday() < 5)
    assert run_job(job, settings, env.paths, **kw).status == "complete"
    first = env.store.range_paths(days[0], "0930")[0].read_bytes()
    env.settings["skylit_max_strikes"] = 300
    step7.plan_sample_days(env, days, approved=None, raised=True)
    # D4: the raised re-pull is its own job, so the first pull's state.json never marks it done
    job2 = SkylitPullJob(step7.SAMPLE_RAISE_JOB, client_factory=lambda s, store, lg: client_for(env.fake, store))
    assert run_job(job2, settings, env.paths, **kw).status == "complete"
    assert env.store.range_paths(days[0], "0930")[0].read_bytes() == first
    raised = env.store.root / "repull" / "strike_window_raise" / "max300" / "range" / days[0].isoformat()
    assert len(list(raised.glob("*_boards.parquet"))) == 26
    assert env.fake.calls[-1].url.params["maxStrikes"] == "300"
    rep = step7.sample_days_report(env, days, raised=True)  # reads the raised copies, not the first pull
    assert rep["raised"] and rep["skylit_max_strikes"] == 300 and rep["per_day"][days[0].isoformat()]["windows"] == 26
    # a second raise pulls again too (its chunk ids carry the new value)
    env.settings["skylit_max_strikes"] = 350
    step7.plan_sample_days(env, days, approved=None, raised=True)
    job3 = SkylitPullJob(step7.SAMPLE_RAISE_JOB, client_factory=lambda s, store, lg: client_for(env.fake, store))
    assert run_job(job3, settings, env.paths, **kw).status == "complete"
    assert env.fake.calls[-1].url.params["maxStrikes"] == "350"


# --- 3. Tempest timing -------------------------------------------------------------------------------------

def _vol_routes(env, through_by_poll, field="historyThrough"):
    polls = []

    def vol_symbols(r):
        polls.append(1)
        h = through_by_poll(len(polls))
        return {"data": {"symbols": [{"symbol": "QQQ", field: h}, {"symbol": "SPY", field: h}], "missing": []}}

    def hist(r):
        syms = r.url.params["symbols"].split(",")
        return {"data": {"from": r.url.params["from"], "to": r.url.params["to"], "missing": [],
                         "symbols": [{"symbol": s, "dates": [r.url.params["from"]], "columns": {"svx30": [20.0]}} for s in syms]},
                "meta": {}}

    env.fake.routes["/v1/vol/symbols"] = vol_symbols
    env.fake.routes["/v1/vol/history"] = hist


def test_tempest_row_seen_the_evening_before_is_on_time(env):
    _vol_routes(env, lambda n: "2026-03-13" if n >= 2 else "2026-03-12")
    minutes = iter(range(0, 10**4, 5))
    env.now = lambda: datetime(2026, 3, 13, 17, 0, tzinfo=ET) + timedelta(minutes=next(minutes))  # Friday evening
    rep = step7.check_tempest_poll(env, sleep=lambda s: None)
    assert rep["prev_session"] == "2026-03-13" and rep["session_using_row"] == "2026-03-16"
    assert rep["by_09_00"] == {"QQQ": True, "SPY": True} and rep["present_at_start"] == {"QQQ": False, "SPY": False}


def test_tempest_present_at_start_and_session_date_fallback(env):
    _vol_routes(env, lambda n: "2026-03-13", field="sessionDate")
    env.now = Now(datetime(2026, 3, 16, 6, 0, tzinfo=ET))
    rep = step7.check_tempest_poll(env, sleep=lambda s: None)
    assert rep["present_at_start"] == {"QQQ": True, "SPY": True} and rep["field_used"] == "sessionDate"
    n = len(env.fake.calls)
    step7.check_tempest_poll(env, sleep=lambda s: None)  # the same row again: not pulled twice
    assert all(r.url.path != "/v1/vol/history" for r in env.fake.calls[n:])
    with pytest.raises(step7.CheckRefused, match="re-check"):
        step7.check_tempest_compare(env)  # needs one session to pass first
    env.now = Now(datetime(2026, 3, 17, 7, 0, tzinfo=ET))
    assert step7.check_tempest_compare(env)["changed_cells"] == 0
    with pytest.raises(step7.CheckRefused):
        step7.check_tempest_compare(env)  # already re-checked: no second re-pull


def test_tempest_lag_recommendation(env):
    d = step7.reports_dir(env.paths)
    assert step7.tempest_lag_recommendation(env.paths)["recommend_tempest_lag_sessions"] is None
    for day, ok in (("2026-03-16", True), ("2026-03-17", True)):
        step7.atomic_write_json(d / f"tempest_poll_{day}_x.json", {"session_using_row": day, "by_09_00": {"QQQ": ok}})
    # owner 2026-10-09: no decision until the finality check has compared the pre-open copies
    assert step7.tempest_lag_recommendation(env.paths)["recommend_tempest_lag_sessions"] is None
    step7.atomic_write_json(d / "tempest_finality_1.json", {"any_changed": False, "days": [{"day": "2026-03-16"}]})
    assert step7.tempest_lag_recommendation(env.paths)["recommend_tempest_lag_sessions"] == 1
    step7.atomic_write_json(d / "tempest_finality_2.json", {"any_changed": True, "days": [{"day": "2026-03-16"}]})
    assert step7.tempest_lag_recommendation(env.paths)["recommend_tempest_lag_sessions"] == 2
    step7.atomic_write_json(d / "tempest_finality_3.json", {"any_changed": False, "days": [{"day": "2026-03-16"}]})
    step7.atomic_write_json(d / "tempest_poll_2026-03-18_x.json", {"session_using_row": "2026-03-18", "by_09_00": {"QQQ": False}})
    assert step7.tempest_lag_recommendation(env.paths)["recommend_tempest_lag_sessions"] == 2


def test_tempest_depth(env):
    _vol_routes(env, lambda n: "2026-10-05")
    rep = step7.check_tempest_depth(env)
    assert rep["needed_from"] < "2025-10-01" and rep["covered"]["QQQ"] is True


# --- 9. the month-1 merge --------------------------------------------------------------------------------

def test_month_ledger_merge():
    s = {"months": {1: ["2025-10", "2025-11"], **{i: f"2025-{10 + i:02d}" for i in (2,)}, 3: "2026-01"}}
    assert months.month_index(date(2025, 11, 20), s) == 1 and months.month_index(date(2025, 12, 1), s) == 2
    assert len(months.days_of(1, s)) == 31 + 30 and months.span(s) == (date(2025, 10, 1), date(2026, 1, 31))
    with pytest.raises(months.NotDevelopmentMonth):
        months.require_development(date(2026, 2, 2), s)


# --- 10. analysis at real scale ----------------------------------------------------------------------------

def test_grouping_is_fast_at_scale():
    n_boards, per = 10_000, 200  # 2 million strike rows
    t = np.repeat(np.arange(n_boards, dtype=np.int64) * 1000 + 1_772_721_000_000, per)
    strikes = pa.table({"symbol": pa.array(["QQQ"] * len(t)), "as_of": pa.array(t).cast(pa.timestamp("ms", tz="UTC")),
                        "strike": pa.array(np.tile(np.arange(per, dtype=np.float32), n_boards)),
                        "value": pa.array(np.ones(len(t), dtype=np.float32))})
    t0 = _time.perf_counter()
    groups = an._strikes_by_board(strikes)
    assert len(groups) == n_boards and _time.perf_counter() - t0 < 10.0


# --- 18. stream shape, ping gap and clock offset recorded ----------------------------------------------------

def test_stream_record_reports_shape_ping_and_offset(env):
    t0 = datetime(2026, 10, 6, 14, 30, tzinfo=UTC)
    snap = {"symbol": "QQQ", "spot": 600.0, "previousClose": 599.0, "priceChange": 1.0, "priceChangePercent": 0.1,
            "expirations": ["2026-10-06"], "lagged": False, "strikes": [{"strike": 600, "value": 1e6, "nodeType": "king"}]}
    body = sse(("connected", {"creditsRemaining": 1000}, None), ("ping", None, None),
               *[("snapshot", {**snap, "asOf": z(t0 + timedelta(seconds=i))}, f"QQQ:{i}") for i in range(3)],
               ("ping", None, None), ("snapshot", {**snap, "asOf": z(t0 + timedelta(seconds=9))}, "QQQ:9"))
    replies = [body, body]
    ticks = iter(range(0, 10**6, 5))  # one event every 5 s

    async def nosleep(s):
        pass

    env.stream_factory = lambda: SkylitStream(
        Secret(KEY), symbols_map=SYMBOLS_MAP, maps=["QQQ", "NDXP", "SPY", "SPX"], metric="gamma", max_expirations=1,
        max_strikes=200, transport=httpx.MockTransport(lambda r: httpx.Response(200, content=replies.pop(0))),
        clock_ns=lambda: int((t0.timestamp() + next(ticks)) * 1e9), sleep=nosleep)
    rep = step7.check_stream_record(env, minutes=0.5, force=True)  # reconnect at 15 s, stop at 30 s
    assert rep["clock_offset_ms"] == [1.5, 1.5]
    assert rep["stream_shape"]["QQQ"] == {"max_strikes_per_board": 1, "expiration_sets": ["2026-10-06"], "single_expiration": True}
    assert rep["ping_gap_within_stale"] is True
    assert rep["resent_boards_dropped"] == 1  # the resumed connection re-sent a board already received
    assert rep["events_summary"]["ping_gap_max"] == 20.0


# --- stream retries the reviewer listed -------------------------------------------------------------------

def test_stream_429_retry_after_symbol_unavailable_and_plain_end():
    responses = [(429, b"", {"Retry-After": "2"}),
                 (200, sse(("symbol_unavailable", {"symbol": "SPXW", "reason": "no_live_board"}, None)), {}),
                 (200, sse(("ping", None, None)), {})]
    slept = []

    async def sleep(s):
        slept.append(s)

    def handler(request):
        status, body, headers = responses.pop(0)
        return httpx.Response(status, content=body, headers=headers)

    s = SkylitStream(Secret(KEY), symbols_map=SYMBOLS_MAP, maps=["QQQ", "SPX"], metric="gamma", max_expirations=1,
                     max_strikes=200, sleep=sleep, rng=lambda: 0.0, transport=httpx.MockTransport(handler))

    async def run():
        out = []
        async for ev in s.events():
            out.append(ev)
            if len(out) == 2:
                break
        await s.aclose()
        return out

    evs = asyncio.run(run())
    assert [e.kind for e in evs] == ["symbol_unavailable", "ping"]
    assert slept[0] >= 2  # Retry-After honoured; the stream that ended without `reconnect` was reopened
    assert evs[1].connection == 2


# --- CLI ------------------------------------------------------------------------------------------------

def test_cli_step7_refusals_and_lock(settings, drive, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    fake = FakeSkylit()
    monkeypatch.setattr(sp, "make_client", lambda s, store, log, **kw: client_for(fake, store))
    assert cli.main(["step7", "historical-instants"]) == cli.EXIT_REFUSED  # no month ledger yet
    assert "step 4b" in capsys.readouterr().err
    settings["months"] = dict(MONTHS)
    assert cli.main(["step7", "darkpool", "--date", "2026-04-15"]) == cli.EXIT_REFUSED  # month 7
    assert "development months" in capsys.readouterr().err
    paths = storage.resolve_paths(settings)
    lock_path(paths.root, DATABENTO).write_text(json.dumps({"host": "elsewhere", "pid": 1, "job": "databento_backfill"}))
    assert cli.main(["step7", "symbols"]) == 0  # the Databento backfill never blocks a step-7 check
    lock_path(paths.root, SKYLIT).write_text(json.dumps({"host": "elsewhere", "pid": 1, "job": "skylit_backfill"}))
    assert cli.main(["step7", "symbols"]) == cli.EXIT_LOCKED  # another process holds the Skylit job lock
    lock_path(paths.root, SKYLIT).unlink()
    assert cli.main(["step7", "symbols"]) == 0 and not lock_path(paths.root, SKYLIT).exists()
    assert cli.main(["step7", "report"]) == 0
    assert (step7.reports_dir(paths) / "parameter_check.md").exists()


def test_real_make_client_wires_the_ledger(paths, monkeypatch, settings):
    monkeypatch.setattr(sp, "get_secret", lambda name: Secret(KEY))
    fake = FakeSkylit()
    store = SkylitStore(paths)
    settings["months"] = dict(MONTHS)
    c = sp.make_client(settings, store, step7.log_default, transport=fake.transport(), sleep=lambda s: None)
    assert c.credits.remaining == fake.credits and c.limiter.rpm == 120
    c.historical_range(["QQQ"], datetime(2026, 3, 5, 14, 30, tzinfo=UTC), datetime(2026, 3, 5, 14, 31, tzinfo=UTC))
    assert store.ledger()[-1]["charged"] == 25


# --- SNTP ---------------------------------------------------------------------------------------------------

def test_ntp_offset(monkeypatch):
    from nqbot import clock

    now = 1_790_000_000.0
    server = now + 0.250  # server 250 ms ahead

    def ntp_bytes(t):
        sec = int(t) + clock.NTP_EPOCH_OFFSET
        return struct.pack("!II", sec, int((t - int(t)) * 2**32))

    class FakeSocket:
        def __init__(self, *a):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def settimeout(self, t):
            pass

        def sendto(self, data, addr):
            assert addr == ("192.0.2.1", 123)

        def recvfrom(self, n):
            return b"\0" * 32 + ntp_bytes(server) + ntp_bytes(server), None

    monkeypatch.setattr(clock.socket, "socket", FakeSocket)
    monkeypatch.setattr(clock.socket, "getaddrinfo", lambda *a: [(2, 2, 17, "", ("192.0.2.1", 123))])
    monkeypatch.setattr(clock.time, "time", lambda: now)
    assert clock.ntp_offset_ms("time.example") == pytest.approx(250.0, abs=0.01)


def _fake_ntp(monkeypatch, delays, *, dns_sec=0.0, true_offset=0.060):
    """A fake clock and NTP server `true_offset` s ahead; each exchange's (out, back) network delay from `delays`
    (None = the packet is lost); the name lookup takes `dns_sec` on the local clock."""
    from nqbot import clock

    now = [1_790_000_000.0]
    sent = []

    def ntp_bytes(t):
        sec = int(t) + clock.NTP_EPOCH_OFFSET
        return struct.pack("!II", sec, int((t - int(t)) * 2**32))

    def getaddrinfo(*a):
        now[0] += dns_sec
        return [(2, 2, 17, "", ("192.0.2.1", 123))]

    class FakeSocket:
        def __init__(self, *a):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def settimeout(self, t):
            pass

        def sendto(self, data, addr):
            assert addr == ("192.0.2.1", 123)  # resolved before, never a name here
            sent.append(addr)

        def recvfrom(self, n):
            d = delays[(len(sent) - 1) % len(delays)]
            if d is None:
                now[0] += 3.0
                raise TimeoutError("timed out")
            out, back = d
            server = now[0] + out + true_offset
            now[0] += out + back
            return b"\0" * 32 + ntp_bytes(server) + ntp_bytes(server), None

    monkeypatch.setattr(clock.socket, "socket", FakeSocket)
    monkeypatch.setattr(clock.socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(clock.time, "time", lambda: now[0])
    return clock, sent


def test_ntp_a_slow_name_lookup_does_not_move_the_offset(monkeypatch):
    """Owner 2026-10-31: a 1-s lookup between t1 and the send read +560 ms on a clock 60 ms behind."""
    clock, sent = _fake_ntp(monkeypatch, [(0.006, 0.006)] * 4, dns_sec=1.0)
    off, rtt = clock.ntp_measure("time.example")
    assert off == pytest.approx(60.0, abs=0.01) and rtt == pytest.approx(12.0, abs=0.01) and len(sent) == 4


def test_ntp_keeps_the_exchange_with_the_shortest_round_trip(monkeypatch):
    """Each reading is off by half its delay asymmetry; the shortest round trip bounds that best. A lost packet is
    skipped; all lost is an error."""
    clock, _ = _fake_ntp(monkeypatch, [(0.400, 0.010), None, (0.005, 0.005), (0.050, 0.150)])
    off, rtt = clock.ntp_measure("time.example")
    assert off == pytest.approx(60.0, abs=0.01) and rtt == pytest.approx(10.0, abs=0.01)
    assert clock.ntp_offset_ms("time.example") == pytest.approx(60.0, abs=0.01)
    clock, _ = _fake_ntp(monkeypatch, [None] * 4)
    with pytest.raises(OSError, match="timed out"):
        clock.ntp_measure("time.example")
