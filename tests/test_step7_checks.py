import json
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pyarrow as pa
import pytest

from nqbot.checks import step7
from nqbot.checks import step7_analysis as an
from nqbot.clients.skylit_store import stream_snapshots_to_tables, write_parquet
from nqbot.clients.skylit_stream import SkylitStream
from nqbot.config import load_features
from nqbot.secrets import Secret
from nqbot.storage import atomic_write_text
from tests.skylit_fakes import KEY, SYMBOLS_MAP, FakeSkylit, parse_z, range_body, sse, z
from tests.skylit_fakes import make_client as _make_client

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
MONTHS = {i + 1: f"{2025 + (9 + i) // 12}-{(9 + i) % 12 + 1:02d}" for i in range(12)}  # 1 = 2025-10 ... 12 = 2026-09


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(UTC)


# --- analysis: strike window ----------------------------------------------------------------------------

def test_board_window_need_hand_example():
    """Strikes 1..11, spot 6, King 1000 at 8, a -300 node at 3 (>= 20% of King), margin 2.
    Ranks by distance (ties: lower strike first): 6:0 5:1 7:2 4:3 8:4 3:5 9:6 2:7 10:8 1:9 11:10.
    King 8 needs 8,9,10 inside -> rank 8 -> 9 strikes; node 3 needs 3,2,1 -> rank 9 -> 10 strikes."""
    k = np.arange(1.0, 12.0)
    v = np.full(11, 10.0)
    v[7], v[2] = 1000.0, -300.0
    need = an.board_window_need(k, v, 6.0, 0.20, 2)
    assert (need.n, need.strike, need.king) == (10, 3.0, 1000.0)
    assert an.board_window_need(k, v, 6.0, 0.50, 2).n == 9  # the -300 node no longer counts
    assert an.nearest_window(k, 6.0, 3) == {5.0, 6.0, 7.0}


def test_chain_end_needs_only_strikes_that_exist():
    k = np.arange(1.0, 6.0)
    v = np.array([1.0, 1.0, 1.0, 1.0, 1000.0])  # King on the last listed strike
    assert an.board_window_need(k, v, 3.0, 0.2, 5).n == 5


def _boards(rows):
    return pa.Table.from_pylist(rows)


def _strikes(rows):
    return pa.Table.from_pylist([{"symbol": s, "as_of": t, "strike": k, "value": v} for s, t, k, v in rows])


def test_strike_window_over_boards_and_edge_check():
    t = ts("2026-03-05T14:30:00+00:00")
    k = list(np.arange(1.0, 12.0))
    vals = [10.0] * 11
    vals[7], vals[2] = 1000.0, -300.0
    boards = _boards([{"symbol": "QQQ", "as_of": t, "spot": 6.0}, {"symbol": "SPY", "as_of": t, "spot": 6.0}])
    strikes = _strikes([("QQQ", t, a, b) for a, b in zip(k, vals)] + [("SPY", t, a, 10.0 if a != 6 else 99.0) for a in k])
    res = an.strike_window(boards, strikes, 0.2, 2)
    assert res.per_symbol["QQQ"]["need"] == 10 and res.per_symbol["SPY"]["need"] == 5 and res.proposed == 10
    ev = an.edge_violations(boards, strikes, 0.2, 2)
    assert ev["QQQ"]["violations"] == 0
    ev3 = an.edge_violations(boards, strikes, 0.2, 3)  # the -300 node at index 2 is now too near the edge
    assert ev3["QQQ"]["violations"] == 1


def test_window_prediction_and_zero_dte():
    t = ts("2026-03-05T14:30:00+00:00")
    full_b = _boards([{"symbol": "QQQ", "as_of": t, "spot": 6.0}])
    full_s = _strikes([("QQQ", t, float(a), 1000.0 if a != 7 else 10.0) for a in range(1, 12)])
    good = _strikes([("QQQ", t, float(a), 1.0) for a in (4, 5, 6, 8)])  # strike 7 (|v| < 50) left out: fine
    bad = _strikes([("QQQ", t, float(a), 1.0) for a in (3, 4, 5, 6)])
    assert an.window_prediction_mismatches(full_b, full_s, good, 5) == {"QQQ": 0}
    assert an.window_prediction_mismatches(full_b, full_s, bad, 5) == {"QQQ": 1}
    b = _boards([{"symbol": "SPX", "as_of": t, "expirations": "2026-03-05"},
                 {"symbol": "SPX", "as_of": ts("2026-03-06T14:30:00+00:00"), "expirations": "2026-03-20"}])
    assert an.zero_dte_misses(b) == [{"symbol": "SPX", "session": "2026-03-06", "expirations": "2026-03-20", "boards": 1}]


# --- analysis: parity and latency ----------------------------------------------------------------------

def _pair(stream_vals, hist_vals, spot_s=512.4, spot_h=512.4):
    t = ts("2026-03-05T14:30:01+00:00")
    sb = _boards([{"symbol": "QQQ", "as_of": t, "spot": spot_s}])
    hb = _boards([{"symbol": "QQQ", "as_of": t, "spot": spot_h}])
    return (sb, _strikes([("QQQ", t, k, v) for k, v in stream_vals.items()]),
            hb, _strikes([("QQQ", t, k, v) for k, v in hist_vals.items()]))


def test_compare_boards_identical_and_empty_strikes():
    res = an.compare_boards(*_pair({510.0: 1e6, 511.0: 20.0}, {510.0: 1e6}))  # 511 (|v| < 50) only in stream
    assert res.matched == 1 and not res.stop


def test_compare_boards_revised_means_stop():
    res = an.compare_boards(*_pair({510.0: 1e6}, {510.0: 1.1e6}))
    assert res.stop and "510.0" in res.revised[0]["diffs"][0]
    assert an.compare_boards(*_pair({510.0: 1e6}, {510.0: 1e6}, spot_h=512.41)).stop
    assert an.compare_boards(*_pair({510.0: 1e6}, {510.0: 1e6, 515.0: 400.0})).stop  # a real strike only in history


def test_compare_boards_only_one_side_and_extra_history():
    t1, t2 = ts("2026-03-05T14:30:01+00:00"), ts("2026-03-05T14:30:02+00:00")
    sb = _boards([{"symbol": "QQQ", "as_of": t1, "spot": 1.0}])
    hb = _boards([{"symbol": "QQQ", "as_of": t1, "spot": 1.0}, {"symbol": "QQQ", "as_of": t2, "spot": 1.0}])
    s = _strikes([("QQQ", t1, 1.0, 5.0)])
    h = _strikes([("QQQ", t1, 1.0, 5.0), ("QQQ", t2, 1.0, 6.0)])
    res = an.compare_boards(sb, s, hb, h)
    assert res.matched == 1 and len(res.only_history) == 1 and not res.stop
    extra = an.extra_history_boards(sb, hb, t1, t2)
    q = extra["QQQ"]
    assert (q["history"], q["stream"], q["extra_in_history"]) == (2, 1, 1)
    assert q["extra_sec_to_nearest_streamed"]["max"] == 1.0 and q["extra_same_second_as_streamed"] == 0


def test_latency_report_skips_connect_snapshots():
    t0 = ts("2026-03-05T14:30:00+00:00")
    rows = []
    for c in (0, 1):
        connect = t0 + timedelta(seconds=10 * c)
        # each connection's first board arrives first but is an older, unchanged board (asOf 5 s back)
        rows.append({"symbol": "QQQ", "as_of": connect - timedelta(seconds=5), "arrival": connect, "connection": c,
                     "lagged": False})
        for i in range(1, 5):
            as_of = connect + timedelta(seconds=i)
            rows.append({"symbol": "QQQ", "as_of": as_of, "arrival": as_of + timedelta(seconds=0.1 * (i + 1)),
                         "connection": c, "lagged": i == 4 and c == 1})
    rep = an.latency_report(_boards(rows))
    assert rep["delay_all"]["n"] == 8 and rep["delay_all"]["max"] == pytest.approx(0.5)
    assert rep["proposed_map_latency_sec"] == 0.5 and rep["lagged"] == 1
    assert rep["gap_sec"]["QQQ"]["max"] == pytest.approx(6.0)


def test_quantile_nearest_rank():
    assert an.quantile(list(range(1, 1001)), 0.999) == 999.0
    assert an.quantile([], 0.5) is None


def test_event_report():
    ev = [{"kind": "connected", "arrival_ns": 0, "connection": 0, "data": {"creditsRemaining": 1000}},
          {"kind": "ping", "arrival_ns": 15 * 10**9, "connection": 0, "data": None},
          {"kind": "ping", "arrival_ns": 30 * 10**9, "connection": 0, "data": None},
          {"kind": "resumed", "arrival_ns": 31 * 10**9, "connection": 1, "data": {"gapCovered": False}},
          {"kind": "credits", "arrival_ns": 60 * 10**9, "connection": 1, "data": {"remaining": 932}}]
    rep = an.event_report(ev)
    assert rep["ping_gap_p50"] == 15.0 and rep["connections"] == 2 and rep["reconnect_gap_sec"] == [1.0]
    assert rep["credits_used"] == 68 and rep["resumed"] == [{"gapCovered": False}]


# --- analysis: dark pools, Tempest, instants -----------------------------------------------------------------

def test_print_set_diff_and_top_compare():
    a = ts("2026-03-05T15:00:00+00:00")
    p1 = {"ticker": "QQQ", "timestamp": a, "price": 600.0, "size": 10000, "notional": 6e6}
    p2 = {**p1, "timestamp": a + timedelta(seconds=1)}
    late = {**p1, "timestamp": a + timedelta(hours=1), "notional": 9e6}
    d = an.compare_print_sets([p1, p2], [p1, {**p2, "size": 20000, "notional": 1.2e7}, late])
    assert len(d["added"]) == 1 and d["removed"] == [] and len(d["changed"]) == 1
    assert an.same_top([p1], [p1]) and not an.same_top([p1], [late])
    assert an.top_matches_api([p1], [{"timestamp": a, "price": 600.0, "notional": 6e6, "size": 10000}])
    assert not an.top_matches_api([p1], [])


def test_vol_table_diff():
    a = pa.Table.from_pylist([{"symbol": "QQQ", "date": date(2026, 3, 2), "svx30": 20.0, "close": None}])
    b = pa.Table.from_pylist([{"symbol": "QQQ", "date": date(2026, 3, 2), "svx30": 20.5, "close": None}])
    assert an.compare_vol_tables(a, a) == []
    assert an.compare_vol_tables(a, b) == [{"symbol": "QQQ", "date": "2026-03-02", "field": "svx30"}]  # no values (2b)


def test_pick_instants_deterministic_inside_rth():
    days = [date(2026, 3, 2) + timedelta(days=i) for i in range(5)]
    a = an.pick_instants(days, 40, 7, close_of=lambda d: time(16, 0))
    assert a == an.pick_instants(days, 40, 7, close_of=lambda d: time(16, 0))
    assert all(time(9, 30) < t.time() < time(16, 0) for t in a) and len(a) > 30


# --- the check commands, end to end against the fake API ---------------------------------------------------

class Now:
    def __init__(self, t: datetime):
        self.t = t

    def __call__(self) -> datetime:
        return self.t


@pytest.fixture
def env(paths, settings, log):
    settings["months"] = MONTHS
    fake = FakeSkylit()

    def factory(store):
        c = _make_client(fake, on_call=store.log_call)
        c.account()
        return c

    lines: list[str] = []
    e = step7.Env(settings=settings, features=load_features(), paths=paths, log=log, client_factory=factory,
                  now=Now(datetime(2026, 10, 6, 20, 0, tzinfo=ET)), echo=lines.append)
    e.fake, e.lines = fake, lines
    return e


def test_symbols_check(env):
    env.fake.routes["/v1/symbols"] = lambda r: {"data": {"symbols": [
        {"symbol": s, "isIndex": s != "QQQ", "metrics": ["gamma"], "history": {"from": "2023-03-28", "to": "2026-10-06"}}
        for s in ("QQQ", "NDXP", "SPY", "SPX", "SPXW", "AAPL")]}}
    env.fake.routes["/v1/vol/symbols"] = lambda r: {"data": {"symbols": [{"symbol": "QQQ", "historyThrough": "2026-10-05"},
                                                                         {"symbol": "VIX"}], "missing": []}, "meta": {}}
    rep = step7.check_symbols(env)
    assert rep["missing_roots"] == [] and {s["symbol"] for s in rep["symbols"]} == {"QQQ", "NDXP", "SPY", "SPX", "SPXW"}
    assert rep["tempest_vix_served"] and step7.latest_report(env.paths, "symbols")["check"] == "symbols"


def test_strike_window_check(env):
    rep = step7.check_strike_window(env, date(2026, 1, 15), [date(2026, 3, 2), date(2026, 3, 3)])
    assert set(rep["per_symbol"]) == {"QQQ", "NDXP", "SPY", "SPX"} and 0 < rep["proposed_skylit_max_strikes"] <= 11
    assert all(r.url.params["maxStrikes"] == "all" for r in env.fake.calls if r.url.path == "/v1/historical/range")
    assert env.store.stored_windows(date(2026, 3, 2), env.store.fullchain) == ["0930"]
    assert env.store.stored_windows(date(2026, 3, 2)) == []  # never mixed into the normal cache
    with pytest.raises(ValueError, match="development"):
        step7.check_strike_window(env, date(2026, 1, 15), [date(2026, 4, 1)])  # month 7


def test_sample_days_plan_and_report(env):
    with pytest.raises(step7.CheckRefused, match="5 sample days"):
        step7.plan_sample_days(env, [date(2026, 3, 2)], None)
    with pytest.raises(ValueError, match="development"):
        step7.plan_sample_days(env, [date(2026, 2, 2 + i) for i in range(5)], None)  # month 5, not 6
    days = [date(2026, 3, 2 + i) for i in range(5)]
    step7.plan_sample_days(env, days, None)
    from nqbot.clients.skylit_pull import pull_session

    for d in days[:2]:
        pull_session(env.client, env.store, d, log=env.log)
    rep = step7.sample_days_report(env, days[:2])
    assert rep["per_day"]["2026-03-02"]["windows"] == 26 and rep["per_day"]["2026-03-02"]["credits"] == 650
    assert rep["zero_dte_misses"] == []


def test_historical_instants_check(env):
    def hist(r):
        t = parse_z(r.url.params["at"]).replace(microsecond=0)
        return {"data": {"symbols": [{"symbol": s, "asOf": z(t), "spot": 1.0, "previousClose": 1.0, "priceChange": 0.0,
                                      "priceChangePercent": 0.0, "expirations": [t.astimezone(ET).date().isoformat()],
                                      "strikes": [{"strike": 1.0, "value": 5.0, "nodeType": "king"}]}
                                     for s in r.url.params["symbols"].split(",")]},
                "meta": {"metric": "gamma", "resolution": "1s", "mode": "historical", "cached": False}}

    env.fake.routes["/v1/historical"] = hist
    rep = step7.check_historical_instants(env)
    assert len(rep["instants"]) <= step7.INSTANTS and rep["node_types"]["king"] == 4 * len(rep["instants"])
    for t in rep["instants"]:
        assert step7.months_mod.month_index(datetime.fromisoformat(t).date(), env.settings) in range(1, 7)
    n = len(env.fake.calls)
    step7.check_historical_instants(env)  # cached: no new calls
    assert env.fake.calls[n:] == []


def _snapshots_from(body):
    """The history frames of a fake range answer, as the stream would have sent them."""
    out = []
    for sym in body["data"]["symbols"]:
        ax = sym["axes"][0]
        for f in sym["frames"]:
            out.append({"symbol": sym["symbol"], "asOf": f["asOf"], "spot": f["spot"], "previousClose": f["previousClose"],
                        "priceChange": f["spot"] - f["previousClose"], "priceChangePercent": 0.0,
                        "expirations": ax["expirations"], "lagged": False,
                        "strikes": [{"strike": k, "value": v, "nodeType": "normal"} for k, v in zip(ax["strikes"], f["values"])]})
    return sorted(out, key=lambda s: s["asOf"])


def _write_recording(env, session, start, snaps, tamper=None):
    store = env.store
    recs, events = [], [{"kind": "connected", "arrival_ns": int(start.timestamp() * 1e9), "connection": 0,
                         "data": {"creditsRemaining": 1000}, "event_id": None}]
    for i, s in enumerate(snaps):
        if tamper and i == 1:
            s = {**s, "strikes": [{**s["strikes"][0], "value": s["strikes"][0]["value"] + 1e5}] + s["strikes"][1:]}
        arr = int((parse_z(s["asOf"]).timestamp() + 0.25) * 1e9)
        recs.append({"payload": s, "arrival_ns": arr, "event_id": f"{s['symbol']}:{i}", "connection": 0})
        events.append({"kind": "snapshot", "arrival_ns": arr, "connection": 0, "data": None, "event_id": None})
    boards, strikes = stream_snapshots_to_tables(recs, {v: k for k, v in SYMBOLS_MAP.items()})
    folder = store.stream_dir(session)
    meta = {"params": {"maxStrikes": 200}}
    write_parquet(folder / "143000_boards.parquet", boards, meta)
    write_parquet(folder / "143000_strikes.parquet", strikes, meta)
    atomic_write_text(folder / "143000_events.jsonl", "".join(json.dumps(e) + "\n" for e in events))


@pytest.mark.parametrize("tamper", [False, True])
def test_parity_check(env, tamper):
    session = date(2026, 10, 5)
    start = datetime(2026, 10, 5, 10, 30, tzinfo=ET)
    end = start + timedelta(minutes=15) - timedelta(milliseconds=1)
    body = range_body(["QQQ", "SPXW"], start, end, env.fake.step_sec)
    _write_recording(env, session, start, _snapshots_from(body), tamper)
    env.now.t = datetime(2026, 10, 6, 8, 0, tzinfo=ET)
    with pytest.raises(step7.CheckRefused, match="pre-open"):
        step7.check_parity(env, session)
    env.now.t = datetime(2026, 10, 6, 8, 45, tzinfo=ET)
    rep = step7.check_parity(env, session)
    assert rep["STOP_ask_owner"] is tamper
    assert rep["n_only_stream"] == 0 and rep["n_only_history"] == 3 * 2  # NDXP/SPY boards the recording lacks
    assert rep["matched"] == (6 - 1 if tamper else 6)
    assert "repull" in str(env.store.base("parity", pulled="x"))
    led = env.store.ledger()[-1]
    assert led["repull"] == "parity"


def test_stream_record_check(env, monkeypatch):
    t0 = datetime(2026, 10, 6, 14, 30, tzinfo=UTC)
    snap = {"symbol": "QQQ", "spot": 600.0, "previousClose": 599.0, "priceChange": 1.0, "priceChangePercent": 0.1,
            "expirations": ["2026-10-06"], "lagged": False, "strikes": [{"strike": 600, "value": 1e6, "nodeType": "king"}]}
    first = sse(("connected", {"creditsRemaining": 1000}, None),
                *[("snapshot", {**snap, "asOf": z(t0 + timedelta(seconds=i))}, f"QQQ:{i}") for i in range(4)])
    second = sse(("resumed", {"gapCovered": True, "stateRestored": True, "symbols": []}, "QQQ:3"),
                 *[("snapshot", {**snap, "asOf": z(t0 + timedelta(seconds=i))}, f"QQQ:{i}") for i in range(4, 9)],
                 ("credits", {"remaining": 940, "charged": 4}, None))
    replies = [first, second]
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=replies.pop(0), headers={"Content-Type": "text/event-stream"})

    ticks = iter(range(10**6))
    clock = lambda: int((t0.timestamp() + 0.2 + next(ticks)) * 1e9)  # noqa: E731  one event per second

    async def nosleep(s):
        pass

    env.stream_factory = lambda: SkylitStream(Secret(KEY), symbols_map=SYMBOLS_MAP, maps=["QQQ", "NDXP", "SPY", "SPX"],
                                              metric="gamma", max_expirations=1, max_strikes=200,
                                              transport=httpx.MockTransport(handler), clock_ns=clock, sleep=nosleep)
    env.now.t = datetime(2026, 10, 6, 7, 0, tzinfo=ET)
    with pytest.raises(step7.CheckRefused, match="live session"):
        step7.check_stream_record(env, minutes=0.1)
    rep = step7.check_stream_record(env, minutes=0.1, force=True)
    assert rep["boards"] >= 5 and len(seen) == 2 and seen[1].headers["Last-Event-ID"]
    assert rep["events_summary"]["connections"] == 2
    folder = env.store.stream_dir(env.now.t.date())
    assert (folder / f"{rep['tag']}_events.jsonl").exists()


def _dp_print(day: date, hour: int, notional: float, ticker="QQQ"):
    t = datetime.combine(day, time(hour, 0), tzinfo=ET).astimezone(UTC)
    return {"timestamp": z(t), "ticker": ticker, "price": 600.0, "size": int(notional / 600), "notional": notional,
            "venue": "FINN", "pctAvgVol": 1.0}


def _top_route(fake):
    def route(r):
        ticker = r.url.path.rsplit("/", 1)[-1]
        as_of = date.fromisoformat(r.url.params["as_of_date"])
        first = as_of - timedelta(days=int(r.url.params["lookback_days"]))
        rows = [p for p in fake.dp_prints if p["ticker"] == ticker
                and first <= parse_z(p["timestamp"]).astimezone(ET).date() <= as_of]
        rows.sort(key=lambda p: (p["notional"], p["timestamp"]), reverse=True)
        return {"data": [{k: p[k] for k in ("timestamp", "price", "notional", "size")} for p in rows[:3]], "meta": {}}

    return route


def test_darkpool_check(env):
    days = [date(2026, 2, 1) + timedelta(days=i) for i in range(50)]
    env.fake.dp_prints = [_dp_print(d, 11, 1e6 * (1 + i % 7), t) for i, d in enumerate(days) if d.weekday() < 5
                          for t in ("QQQ", "SPY")]
    env.fake.routes["/v1/dark-pool/top-prints/QQQ"] = _top_route(env.fake)
    env.fake.routes["/v1/dark-pool/top-prints/SPY"] = _top_route(env.fake)
    rep = step7.check_darkpool(env, date(2026, 3, 16), [date(2026, 3, 18)])
    assert rep["all_match"] and len(rep["top_prints_checks"]) == 4
    assert rep["depth_probe"]["date"] == "2025-08-18" and rep["depth_probe"]["has_prints"] is False
    assert rep["credits_per_page"] == [5]
    n = len(env.fake.calls)
    step7.check_darkpool(env, date(2026, 3, 16), [date(2026, 3, 18)])
    assert env.fake.calls[n:] == []  # every date and top list cached


def test_late_prints_morning_then_compare(env):
    prev = date(2026, 3, 13)  # Friday; the morning is Monday 2026-03-16
    env.fake.dp_prints = [_dp_print(prev - timedelta(days=i), 11, 2e6 + i) for i in range(0, 40) if
                          (prev - timedelta(days=i)).weekday() < 5]
    env.now.t = datetime(2026, 3, 16, 10, 0, tzinfo=ET)
    with pytest.raises(step7.CheckRefused, match="before 09:30"):
        step7.check_late_prints_morning(env)
    env.now.t = datetime(2026, 3, 16, 8, 0, tzinfo=ET)
    morning = step7.check_late_prints_morning(env)
    assert morning["prev_session"] == "2026-03-13" and morning["top_sizes"]["QQQ"] == 3
    assert "price" not in json.dumps(morning)  # §7 rule 2b: counts only in the report
    tops = env.store.root / "repull" / "late_print" / "morning-2026-03-16" / "top3.json"
    assert len(json.loads(tops.read_text(encoding="utf-8"))["QQQ"]) == 3  # the lists live in the data tree
    env.fake.dp_prints.append(_dp_print(prev, 14, 9e9))  # a big print reported late
    env.now.t = datetime(2026, 3, 19, 9, 0, tzinfo=ET)
    assert step7.check_late_prints_compare(env)["compared"] == []  # not 5 trading days yet
    env.now.t = datetime(2026, 3, 20, 18, 0, tzinfo=ET)
    rep = step7.check_late_prints_compare(env)
    assert rep["compared"][0]["added"] == 1 and rep["compared"][0]["top_changed"]["QQQ"]
    assert rep["any_top_list_changed"] and rep["recommend_dp_lag_sessions"] == 2


def test_tempest_poll_then_compare(env):
    polls = []

    def vol_symbols(r):  # QQQ's new row shows up on the third poll, SPY's is there from the start
        polls.append(1)
        qqq = "2026-03-13" if len(polls) >= 3 else "2026-03-12"
        return {"data": {"symbols": [{"symbol": "QQQ", "historyThrough": qqq},
                                     {"symbol": "SPY", "historyThrough": "2026-03-13"}], "missing": []}}

    env.fake.routes["/v1/vol/symbols"] = vol_symbols
    pulls = []

    def hist(r):  # the re-pull returns QQQ's svx30 revised from 20.0 to 21.0
        pulls.append(1)
        syms = r.url.params["symbols"].split(",")
        val = lambda s: 21.0 if s == "QQQ" and len(pulls) > 1 else 20.0  # noqa: E731
        return {"data": {"from": r.url.params["from"], "to": r.url.params["to"], "missing": [],
                         "symbols": [{"symbol": s, "dates": ["2026-03-13"], "columns": {"svx30": [val(s)]}} for s in syms]},
                "meta": {}}

    env.fake.routes["/v1/vol/history"] = hist
    minutes = iter(range(0, 10**4, 5))
    env.now = lambda: datetime(2026, 3, 16, 6, 0, tzinfo=ET) + timedelta(minutes=next(minutes))
    rep = step7.check_tempest_poll(env, sleep=lambda s: None)
    assert rep["prev_session"] == "2026-03-13" and rep["by_09_00"] == {"QQQ": True, "SPY": True}
    assert rep["first_seen"]["SPY"] < rep["first_seen"]["QQQ"]  # QQQ's row appeared on a later poll
    env.now = lambda: datetime(2026, 3, 17, 7, 0, tzinfo=ET)
    cmp_ = step7.check_tempest_compare(env)
    assert cmp_["changed_cells"] == 1


def test_report_with_backfill_estimate(env):
    env.fake.routes["/v1/vol/symbols"] = lambda r: {"data": {"symbols": [], "missing": []}}
    step7.check_symbols(env)
    text = step7.check_report(env)
    assert "## symbols" in text and "not run yet" in text and "Step-10 backfill estimate" in text
    bf = step7.backfill_estimate(env)
    assert bf["window"] == ["2025-10-01", "2026-09-30"] and bf["windows"] > 240 * 20
    assert step7.warmup_sessions(env.features) >= 20
    assert (step7.reports_dir(env.paths) / "parameter_check.md").exists()
