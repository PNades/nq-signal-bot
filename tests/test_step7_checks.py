import io
import json
from pathlib import Path
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

# the fakes date their prints in 2025: before the real dp_history_start (step 7, 2026-10-06)
def _features():
    return {**load_features(), "dp_history_start": "2025-01-01"}


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
    assert an.nearest_window(np.arange(1.0, 12.0), 6.0, "all") == set(np.arange(1.0, 12.0).tolist())
    whole = _strikes([("QQQ", t, float(a), 1.0) for a in range(1, 12) if a != 7])
    assert an.window_prediction_mismatches(full_b, full_s, whole, "all") == {"QQQ": 0}
    assert an.window_prediction_mismatches(full_b, full_s, good, "all") == {"QQQ": 1}
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


def test_compare_boards_pairs_stream_seconds_with_history_milliseconds():
    """Step 7 (2026-10-08): the stream's asOf is whole seconds, history keeps the ms of the same board."""
    t = ts("2026-03-05T14:30:01+00:00")
    t_ms = t + timedelta(milliseconds=191)
    sb = _boards([{"symbol": "QQQ", "as_of": t, "spot": 512.4}])
    hb = _boards([{"symbol": "QQQ", "as_of": t_ms, "spot": 512.4}])
    res = an.compare_boards(sb, _strikes([("QQQ", t, 510.0, 1e6)]), hb, _strikes([("QQQ", t_ms, 510.0, 1e6)]))
    assert res.matched == 1 and not res.only_stream and not res.only_history and not res.stop
    bad = an.compare_boards(sb, _strikes([("QQQ", t, 510.0, 1e6)]), hb, _strikes([("QQQ", t_ms, 510.0, 2e6)]))
    assert bad.stop and bad.revised[0]["as_of"].endswith(".191")  # a revised board still stops, labelled by history
    nxt = _boards([{"symbol": "QQQ", "as_of": t + timedelta(seconds=1), "spot": 512.4}])  # the next second: no pair
    other = an.compare_boards(nxt, _strikes([("QQQ", t + timedelta(seconds=1), 510.0, 1e6)]), hb,
                              _strikes([("QQQ", t_ms, 510.0, 1e6)]))
    assert other.matched == 0 and len(other.only_stream) == 1 and len(other.only_history) == 1
    two = _boards([{"symbol": "QQQ", "as_of": t_ms, "spot": 512.4},
                   {"symbol": "QQQ", "as_of": t + timedelta(milliseconds=900), "spot": 512.4}])
    assert an.compare_boards(sb, _strikes([("QQQ", t, 510.0, 1e6)]), two, _strikes([])).same_second == 1


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
    # owner 2026-10-19: the proposal covers the delay after tau = floor(asOf) + 1 s (here 0.2-0.5 s after asOf: none)
    assert rep["delay_after_tau"]["max"] == pytest.approx(-0.5) and rep["proposed_map_latency_sec"] == 0.0
    assert rep["lagged"] == 1
    late = [dict(r, arrival=r["arrival"] + timedelta(seconds=1.25)) for r in rows]
    assert an.latency_report(_boards(late))["proposed_map_latency_sec"] == 0.75
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
    # step 7 (2026-10-07): top-prints timestamps are whole seconds (cut); an exact notional tie may come in either order
    ms = {**p1, "timestamp": a + timedelta(milliseconds=700)}
    assert an.top_matches_api([ms], [{**p1, "timestamp": a}])
    assert not an.top_matches_api([ms], [{**p1, "timestamp": a + timedelta(seconds=1)}])
    tie = {**p1, "timestamp": a + timedelta(hours=3)}
    assert an.top_matches_api([tie, p1, late], [p1, tie, late])  # same prints, tie order differs
    assert not an.top_matches_api([late, p1, tie], [p1, tie, late])  # the ranking itself differs
    assert not an.top_matches_api([p1, p1], [p1, tie])  # each print matched once


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
    e = step7.Env(settings=settings, features=_features(), paths=paths, log=log, client_factory=factory,
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
    # the 31-day comparison (owner 2026-10-05): one date_start..date_end call over the last 31 days, print by print
    assert rep["range_check"] == {"start": "2026-02-16", "end": "2026-03-18", "range_prints": 2 * 23,
                                  "mismatches": [], "weekend_dates": [], "matches": True}
    assert step7.dp_range_ok(env.paths)
    assert step7.dp_prints_per_date(env.paths) == rep["prints"] / rep["trading_dates_pulled"]  # holidays left out
    n = len(env.fake.calls)
    step7.check_darkpool(env, date(2026, 3, 16), [date(2026, 3, 18)])
    assert env.fake.calls[n:] == []  # every date, the range answer and top list cached


def test_darkpool_range_that_loses_prints_keeps_one_date_calls(env):
    days = [date(2026, 2, 1) + timedelta(days=i) for i in range(50)]
    env.fake.dp_prints = [_dp_print(d, 11, 1e6 * (1 + i % 7), t) for i, d in enumerate(days) if d.weekday() < 5
                          for t in ("QQQ", "SPY")]
    env.fake.routes["/v1/dark-pool/top-prints/QQQ"] = _top_route(env.fake)
    env.fake.routes["/v1/dark-pool/top-prints/SPY"] = _top_route(env.fake)
    env.fake.dp_range_drops = 1  # the range answer is missing its first print
    rep = step7.check_darkpool(env, date(2026, 3, 16), [date(2026, 3, 18)], depth_probe=False)
    assert rep["range_check"]["matches"] is False
    assert rep["range_check"]["mismatches"] == [{"date": "2026-02-16", "single": 2, "range": 1, "only_single": 1,
                                                 "only_range": 0}]
    assert not step7.dp_range_ok(env.paths)  # step 10 then pulls one date per call


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
    assert "## symbols" in text and "pending (runs alongside)" in text and "Step-10 backfill estimate" in text
    assert "Ready for the owner's step-10 OK: NOT READY** (missing: strike_window, sample_days, oldest_window; "            "strike-window check)" in text
    bf = step7.backfill_estimate(env)
    assert bf["window"] == ["2025-10-01", "2026-09-30"]
    # now = 2026-10-06 20:00 ET: 2025-10-01..06 have left the 365-day reach (by timestamp, not date)
    assert bf["lost_days"] == ["2025-10-01", "2025-10-02", "2025-10-03", "2025-10-06"]
    assert bf["estimate"][0][1] > 240 * 20  # range calls
    assert step7.warmup_sessions(env.features) == 21  # the shared §3.2 count (tests/test_warmup.py)
    assert (step7.reports_dir(env.paths) / "parameter_check.md").exists()


# --- presd_fixes 15: stream-record ends on time and caps its reconnects ------------------------------------

def _silent_stream(replies, sleep):
    """A stream whose connections end without any board (a ping, then a clean end)."""
    return SkylitStream(Secret(KEY), symbols_map=SYMBOLS_MAP, maps=["QQQ", "NDXP", "SPY", "SPX"], metric="gamma",
                        max_expirations=1, max_strikes=200, sleep=sleep, max_reconnects=step7.STREAM_MAX_RECONNECTS,
                        transport=httpx.MockTransport(lambda r: (replies.append(r), httpx.Response(
                            200, content=sse(("ping", None, None)), headers={"Content-Type": "text/event-stream"}))[1]))


def test_stream_session_writes_parts_until_the_end(env, monkeypatch):
    """Owner 2026-10-08: a full-session NDXP recording, flushed in parts; a resumed connection's re-sent board is
    kept once; it stops at the end time."""
    import pyarrow.parquet as pq

    t0 = datetime(2026, 10, 9, 14, 0, tzinfo=UTC)
    snap = {"symbol": "NDXP", "spot": 31000.0, "previousClose": 30990.0, "priceChange": 10.0,
            "priceChangePercent": 0.03, "expirations": ["2026-10-09"], "lagged": False,
            "strikes": [{"strike": 31000, "value": 1e6, "nodeType": "king"}]}
    first = sse(("connected", {"creditsRemaining": 1000}, None),
                *[("snapshot", {**snap, "asOf": z(t0 + timedelta(seconds=i))}, f"NDXP:{i}") for i in range(5)])
    second = sse(("resumed", {"gapCovered": True, "stateRestored": True, "symbols": []}, "NDXP:4"),
                 *[("snapshot", {**snap, "asOf": z(t0 + timedelta(seconds=i))}, f"NDXP:{i}") for i in range(4, 12)])
    replies = [first, second]

    def handler(request):
        return httpx.Response(200, content=replies.pop(0) if replies else sse(("ping", None, None)),
                              headers={"Content-Type": "text/event-stream"})

    ticks = iter(range(10**6))
    clock = lambda: int((t0.timestamp() + next(ticks)) * 1e9)  # noqa: E731  one event per second

    async def nosleep(s):
        pass

    monkeypatch.setattr(step7, "SESSION_PART_MIN", 3 / 60)  # a part every 3 seconds
    env.stream_factory = lambda: SkylitStream(Secret(KEY), symbols_map=SYMBOLS_MAP, maps=["NDXP"], metric="gamma",
                                              max_expirations=1, max_strikes="all", sleep=nosleep, clock_ns=clock,
                                              transport=httpx.MockTransport(handler))
    env.now.t = datetime(2026, 10, 9, 10, 0, tzinfo=ET)
    rep = step7.check_stream_session(env, ["NDXP"], end=t0 + timedelta(seconds=9))
    assert len(rep["parts"]) >= 2 and rep["resent_boards_dropped"] == 1
    folder = Path(rep["folder"])
    boards = pq.read_table(sorted(folder.glob("part_*_boards.parquet")))
    assert boards.num_rows == rep["boards"] and set(boards["symbol"].to_pylist()) == {"NDXP"}
    assert boards["as_of"].to_pylist() == sorted(set(boards["as_of"].to_pylist()))  # each board once, in order
    # owner 2026-10-10: the last line says how the session ended and the boards per map
    assert rep["boards_per_map"] == {"NDXP": rep["boards"]}
    # owner 2026-10-14 (§13 Q-mapstale): the per-map freshness blocks are counted in the recording report
    fresh = rep["map_stale_blocks"]
    assert fresh["skylit_map_stale_sec"] == 5 and set(fresh["per_map"]) == {"NDXP"}
    assert fresh["per_map"]["NDXP"]["blocks"] == 0  # a board every second: never stale
    assert any(line.startswith("  map stale blocks") for line in env.lines)
    assert env.lines[-1].startswith("session ended ") and f"NDXP {rep['boards']:,} boards" in env.lines[-1]


def test_stream_session_refusals_and_estimate(env):
    from nqbot.clients import skylit_credits as credits

    with pytest.raises(step7.CheckRefused, match="unknown maps"):
        step7.check_stream_session(env, ["XYZ"])
    env.now.t = datetime(2026, 10, 9, 9, 0, tzinfo=ET)
    with pytest.raises(step7.CheckRefused, match="before 09:30"):
        step7.check_stream_session(env, ["NDXP"])
    env.now.t = datetime(2026, 10, 9, 16, 5, tzinfo=ET)
    with pytest.raises(step7.CheckRefused, match="before the close"):
        step7.check_stream_session(env, ["NDXP"])
    est = credits.stream_estimate("x", 1, 393, reconnects=step7.SESSION_MAX_RECONNECTS)  # 09:27 -> 16:00, one map
    assert est.total == 1 * (1 + 12) + 1 * (393 + 12) < 5_000  # no approval needed, printed first


def test_stream_record_refuses_a_start_that_runs_past_the_close(env):
    env.now.t = datetime(2026, 10, 6, 15, 50, tzinfo=ET)
    with pytest.raises(step7.CheckRefused, match="past today's close.*start by 15:44"):
        step7.check_stream_record(env)
    env.now.t = datetime(2026, 11, 27, 12, 50, tzinfo=ET)  # day after Thanksgiving: 13:00 close
    with pytest.raises(step7.CheckRefused, match=r"close \(13:00 ET\)"):
        step7.check_stream_record(env)


def test_stream_record_caps_reconnects_and_keeps_what_it_got(env):
    seen = []

    async def nosleep(s):
        pass

    env.stream_factory = lambda: _silent_stream(seen, nosleep)
    rep = step7.check_stream_record(env, minutes=0.1, force=True)
    assert len(seen) == 1 + step7.STREAM_MAX_RECONNECTS  # never an endless reconnect loop (4 credits each)
    assert rep["complete"] is False and rep["ended"].startswith("stopped: stream reconnected")


def test_stream_record_has_a_hard_wall_clock_end(env, monkeypatch):
    import asyncio

    seen = []

    async def slow(s):  # the backoff between reconnects never returns in time
        await asyncio.sleep(3600)

    monkeypatch.setattr(step7, "STREAM_GRACE_SEC", 0)
    env.stream_factory = lambda: _silent_stream(seen, slow)
    with pytest.raises(step7.CheckRefused, match="no board"):  # never an endless wait, nothing to write
        asyncio.run(step7._record(env.stream_factory(), 0.001, grace_sec=0))
    assert len(seen) == 1


def test_stream_record_estimate_covers_the_worst_case(env):
    from nqbot.clients import skylit_credits as credits

    est = credits.stream_estimate("x", 4, 15 + step7.STREAM_GRACE_SEC / 60, reconnects=step7.STREAM_MAX_RECONNECTS)
    assert est.total == 4 * (1 + 3) + 4 * (16 + 3)


def test_darkpool_range_call_refused_is_recorded_not_fatal(env):
    days = [date(2026, 2, 1) + timedelta(days=i) for i in range(50)]
    env.fake.dp_prints = [_dp_print(d, 11, 1e6, t) for d in days if d.weekday() < 5 for t in ("QQQ", "SPY")]
    env.fake.routes["/v1/dark-pool/top-prints/QQQ"] = _top_route(env.fake)
    env.fake.routes["/v1/dark-pool/top-prints/SPY"] = _top_route(env.fake)
    real = env.fake.handler

    def refuse_ranges(request):
        if request.url.path == "/v1/dark-pool/trades" and "date_start" in request.url.params:
            env.fake.calls.append(request)
            return httpx.Response(400, json={"error": {"code": "INVALID_PARAMETER", "message": "no ranges"}})
        return real(request)

    env.fake.handler = refuse_ranges
    rep = step7.check_darkpool(env, date(2026, 3, 16), [date(2026, 3, 18)], depth_probe=False)
    assert rep["range_check"]["matches"] is False and "400" in rep["range_check"]["error"]
    assert rep["all_match"] and not step7.dp_range_ok(env.paths)  # the report is written; step 10 one date per call


def test_dp_history_start_reads_a_date_or_a_string():
    class E:
        features = {"dp_history_start": "2026-04-01"}
    assert step7.dp_history_start(E) == date(2026, 4, 1)
    E.features = {"dp_history_start": date(2026, 4, 1)}
    assert step7.dp_history_start(E) == date(2026, 4, 1)


def test_darkpool_walk_forward_reports_matches_only(env):
    """Owner 2026-10-06: Skylit's dark-pool history starts in walk-forward month 7, so the re-check runs there and
    its report keeps match / no-match and counts only, never a price (§7 rule 2b)."""
    from nqbot.months import NotDevelopmentMonth

    env.settings["months"] = {i + 1: f"{2025 + (8 + i) // 12}-{(8 + i) % 12 + 1:02d}" for i in range(12)}  # 2026-03 = 7
    days = [date(2026, 2, 1) + timedelta(days=i) for i in range(50)]
    env.fake.dp_prints = [_dp_print(d, 11, 1e6 * (1 + i % 7), t) for i, d in enumerate(days) if d.weekday() < 5
                          for t in ("QQQ", "SPY")]
    env.fake.routes["/v1/dark-pool/top-prints/QQQ"] = _top_route(env.fake)
    env.fake.routes["/v1/dark-pool/top-prints/SPY"] = _top_route(env.fake)
    with pytest.raises(NotDevelopmentMonth):
        step7.check_darkpool(env, date(2026, 3, 16), [], depth_probe=False)
    rep = step7.check_darkpool(env, date(2026, 3, 16), [date(2026, 3, 18)], walk_forward=True)
    assert rep["walk_forward"] and rep["all_match"] and rep["range_check"]["matches"]
    assert all(set(c) == {"as_of", "ticker", "match"} for c in rep["top_prints_checks"])
    assert "f4_top" not in rep and rep["depth_probe"] is None
    assert "price" not in json.dumps(rep) and "notional" not in json.dumps(rep)
    assert step7.dp_range_ok(env.paths)


def test_dp_start_probe(env):
    """Owner 2026-10-07: the 5-credit re-probe of dp_history_start; a moved start is found day by day."""
    env.features["dp_history_start"] = "2026-03-02"
    env.fake.dp_prints = [_dp_print(date(2026, 3, d), 11, 2e6, t) for d in (2, 3, 4) for t in ("QQQ", "SPY")]
    rep = step7.check_dp_start_probe(env)
    assert rep["still_there"] and rep["new_start"] is None and len(rep["probes"]) == 1
    assert rep["probes"][0] == {"date": "2026-03-02", "QQQ": 1, "SPY": 1}
    env.fake.dp_prints = [p for p in env.fake.dp_prints if not p["timestamp"].startswith("2026-03-02")]
    env.fake.dp_prints = [p for p in env.fake.dp_prints if not (p["timestamp"].startswith("2026-03-03")
                                                                 and p["ticker"] == "SPY")]
    rep = step7.check_dp_start_probe(env)
    assert not rep["still_there"] and rep["new_start"] == "2026-03-04" and len(rep["probes"]) == 3
    assert "price" not in json.dumps(rep)


def test_tempest_poll_today_times_the_publish_and_diffs_a_pre_close_row(env):
    """Owner 2026-10-07: started during the session, --today waits for today's row (each minute); a row out before
    the close is pulled then and again after the close, and the changed cells are counted (no values)."""
    clock = {"t": datetime(2026, 3, 16, 15, 0, tzinfo=ET)}
    env.now = lambda: clock["t"]

    def sleep(sec):
        clock["t"] += timedelta(seconds=sec)

    def vol_symbols(r):  # today's row appears at 15:30, before the 16:00 close
        through = "2026-03-16" if clock["t"] >= datetime(2026, 3, 16, 15, 30, tzinfo=ET) else "2026-03-13"
        return {"data": {"symbols": [{"symbol": "QQQ", "historyThrough": through},
                                     {"symbol": "SPY", "historyThrough": through}], "missing": []}}

    def hist(r):  # QQQ's svx30 is revised once the session has closed
        syms = r.url.params["symbols"].split(",")
        late = clock["t"] >= datetime(2026, 3, 16, 16, 0, tzinfo=ET)
        val = lambda s: 21.0 if s == "QQQ" and late else 20.0  # noqa: E731
        return {"data": {"from": r.url.params["from"], "to": r.url.params["to"], "missing": [],
                         "symbols": [{"symbol": s, "dates": ["2026-03-16"], "columns": {"svx30": [val(s)]}} for s in syms]},
                "meta": {}}

    env.fake.routes["/v1/vol/symbols"] = vol_symbols
    env.fake.routes["/v1/vol/history"] = hist
    rep = step7.check_tempest_poll(env, sleep=sleep, today=True)
    tm = rep["today_mode"]
    assert rep["prev_session"] == "2026-03-16" and rep["session_using_row"] == "2026-03-17"
    assert rep["first_seen"]["QQQ"].startswith("2026-03-16T15:30") and tm["seen_before_close"] == {"QQQ": True, "SPY": True}
    assert tm["intraday_copy"] and "tempest_intraday" in tm["intraday_copy"]
    assert tm["changed_cells_after_close"] == 1 and tm["changed_fields"] == ["svx30"]
    assert clock["t"] >= datetime(2026, 3, 16, 16, 10, tzinfo=ET)  # waited until 10 minutes after the close
    assert "21.0" not in json.dumps(rep) and "20.0" not in json.dumps(rep)
    # owner 2026-10-09: the 16:10 copy is a draft, kept apart and never loaded
    assert "tempest_draft" in rep["stored"] and env.store.vol_files() == []
    clock["t"] = datetime(2026, 3, 17, 8, 45, tzinfo=ET)  # next morning, before the open: the 16:10 copy is re-checked
    cmp_ = step7.check_tempest_compare(env)
    assert cmp_["first"] == Path(rep["stored"]).name and "tempest_intraday" not in cmp_["first"]
    assert cmp_["first_pulled_at"].startswith("2026-03-16T16:10") and cmp_["repulled_before_open_of"] == "2026-03-17"
    assert cmp_["changed_cells"] == 0
    # the 08:45 re-pull is a pre-open copy: it becomes the stored row (no extra call)
    assert env.store.vol_files() == [Path(cmp_["stored_as_final"])]
    clock["t"] = datetime(2026, 3, 16, 16, 30, tzinfo=ET)
    with pytest.raises(step7.CheckRefused):
        step7.check_tempest_poll(env, sleep=sleep, today=True)  # after the close: the plain mode is the one


def _tempest_routes(env, values):
    """/v1/vol/symbols shows each day's row; /v1/vol/history answers svx30 = values[day] (changed by the test)."""
    env.fake.routes["/v1/vol/symbols"] = lambda r: {"data": {"symbols": [
        {"symbol": s, "historyThrough": max(values)} for s in ("QQQ", "SPY")], "missing": []}}

    def hist(r):
        d, syms = r.url.params["from"], r.url.params["symbols"].split(",")
        return {"data": {"from": d, "to": r.url.params["to"], "missing": [],
                         "symbols": [{"symbol": s, "dates": [d], "columns": {"svx30": [values[d]]}} for s in syms]},
                "meta": {}}

    env.fake.routes["/v1/vol/history"] = hist


def test_tempest_morning_then_finality(env):
    """Owner 2026-10-09: only a copy pulled at the pre-open of a later day is stored in vol/; the finality check
    re-pulls it on a later day and recommends tempest_lag_sessions 1 if nothing changed, else 2."""
    values = {"2026-03-12": 20.0, "2026-03-13": 30.0}
    _tempest_routes(env, values)
    env.now = Now(datetime(2026, 3, 13, 8, 0, tzinfo=ET))  # Friday before the pre-open: too early for Thursday's row
    with pytest.raises(step7.CheckRefused, match="08:30"):
        step7.check_tempest_morning(env)
    env.now = Now(datetime(2026, 3, 13, 8, 40, tzinfo=ET))
    rep = step7.check_tempest_morning(env)
    assert rep["day"] == "2026-03-12" and env.store.vol_files(date(2026, 3, 12), date(2026, 3, 12))
    with pytest.raises(step7.CheckRefused, match="already stored"):
        step7.check_tempest_morning(env)  # never pulled twice
    env.now = Now(datetime(2026, 3, 13, 9, 31, tzinfo=ET))
    with pytest.raises(step7.CheckRefused, match="open"):
        step7.check_tempest_morning(env, date(2026, 3, 12))  # after the open of the session using it
    env.now = Now(datetime(2026, 3, 14, 8, 47, tzinfo=ET))  # Saturday morning: Friday's row, before Monday's open
    assert step7.check_tempest_morning(env)["day"] == "2026-03-13"
    with pytest.raises(step7.CheckRefused, match="later day"):
        step7.check_tempest_finality(env, [date(2026, 3, 13)])  # pulled today: nothing to compare yet
    with pytest.raises(step7.CheckRefused, match="tempest-morning"):
        step7.check_tempest_finality(env, [date(2026, 3, 11)])
    assert step7.tempest_lag_recommendation(env.paths)["recommend_tempest_lag_sessions"] is None
    env.now = Now(datetime(2026, 3, 16, 9, 0, tzinfo=ET))  # Monday before the open
    fin = step7.check_tempest_finality(env, [date(2026, 3, 12), date(2026, 3, 13)])
    assert fin["any_changed"] is False and fin["recommend_tempest_lag_sessions"] == 1
    assert [r["changed_cells"] for r in fin["days"]] == [0, 0] and "30.0" not in json.dumps(fin)
    assert len(env.store.vol_files(date(2026, 3, 13), date(2026, 3, 13))) == 1  # the re-pull is kept apart
    values["2026-03-13"] = 31.0  # revised after the pre-open copy was taken
    fin = step7.check_tempest_finality(env, [date(2026, 3, 12), date(2026, 3, 13)])
    assert fin["any_changed"] and fin["days"][1]["changed_fields"] == ["svx30"]
    assert fin["recommend_tempest_lag_sessions"] == 2


def test_tempest_poll_after_the_close_keeps_a_draft_only(env):
    """Owner 2026-10-09: the row seen just after the close changes overnight: tempest-poll keeps it apart."""
    _tempest_routes(env, {"2026-03-13": 30.0})
    env.now = Now(datetime(2026, 3, 13, 16, 7, tzinfo=ET))
    rep = step7.check_tempest_poll(env, sleep=lambda s: None)
    assert "tempest_draft" in rep["stored"] and env.store.vol_files() == []
    n = len(env.fake.calls)
    step7.check_tempest_poll(env, sleep=lambda s: None)  # the draft counts as pulled: not pulled again
    assert all(r.url.path != "/v1/vol/history" for r in env.fake.calls[n:])


def test_stream_session_switches_to_the_internal_drive_when_the_ssd_drops(env, monkeypatch, tmp_path):
    """Owner 2026-10-11: a part the SSD can't take (after the storage retry) and every later part go to the same
    folder on the internal drive; no board is lost or counted twice, and fallback-move puts them back."""
    import errno

    import pyarrow.parquet as pq

    from nqbot import fallback
    from nqbot.storage import Paths

    monkeypatch.setattr("nqbot.storage.SSD_EVENT_LOG", tmp_path / "events.log")
    t0 = datetime(2026, 10, 9, 14, 0, tzinfo=UTC)
    snap = {"symbol": "NDXP", "spot": 31000.0, "previousClose": 30990.0, "priceChange": 10.0,
            "priceChangePercent": 0.03, "expirations": ["2026-10-09"], "lagged": False,
            "strikes": [{"strike": 31000, "value": 1e6, "nodeType": "king"}]}
    body = sse(("connected", {"creditsRemaining": 1000}, None),
               *[("snapshot", {**snap, "asOf": z(t0 + timedelta(seconds=i))}, f"NDXP:{i}") for i in range(12)])
    replies = [body]

    def handler(request):
        return httpx.Response(200, content=replies.pop(0) if replies else sse(("ping", None, None)),
                              headers={"Content-Type": "text/event-stream"})

    ticks = iter(range(10**6))
    clock = lambda: int((t0.timestamp() + next(ticks)) * 1e9)  # noqa: E731

    async def nosleep(s):
        pass

    monkeypatch.setattr(step7, "SESSION_PART_MIN", 3 / 60)
    env.stream_factory = lambda: SkylitStream(Secret(KEY), symbols_map=SYMBOLS_MAP, maps=["NDXP"], metric="gamma",
                                              max_expirations=1, max_strikes="all", sleep=nosleep, clock_ns=clock,
                                              transport=httpx.MockTransport(handler))
    ssd_root = env.paths.root
    real = step7.write_parquet

    def flaky(path, table, meta):  # the SSD is gone from part 1 on
        if Path(path).is_relative_to(ssd_root) and meta["part"] >= 1:
            raise OSError(errno.EBADF, "Bad file descriptor")
        real(path, table, meta)

    monkeypatch.setattr(step7, "write_parquet", flaky)
    local = Paths.under(tmp_path / "internal", "nqbot")
    env.now.t = datetime(2026, 10, 9, 10, 0, tzinfo=ET)
    rep = step7.check_stream_session(env, ["NDXP"], end=t0 + timedelta(seconds=11), fallback=local)
    assert rep["fallback"]["from_part"] == 1 and str(local.root) in rep["fallback"]["folder"]
    ssd_parts = sorted(Path(rep["folder"]).glob("part_*_boards.parquet"))
    local_parts = sorted(Path(rep["fallback"]["folder"]).glob("part_*_boards.parquet"))
    assert [p.name for p in ssd_parts] == ["part_000_boards.parquet"] and local_parts
    n = sum(pq.read_table(p).num_rows for p in ssd_parts + local_parts)
    assert n == rep["boards"] == rep["boards_per_map"]["NDXP"] and rep["resent_boards_dropped"] == 0
    fallback.move_fallback(local, env.paths, echo=lambda s: None)
    moved = sorted(Path(rep["folder"]).glob("part_*_boards.parquet"))
    assert len(moved) == 1 + len(local_parts) and sum(pq.read_table(p).num_rows for p in moved) == n


def test_stream_session_survives_the_ssd_pulled_for_longer_than_the_wait(env, monkeypatch, tmp_path):
    """Owner 2026-10-16 (the 10-15 recording): the SSD, which also holds the task's console log, is pulled after part
    0 and stays out for 45 s, past the 30-s storage wait. The real write path waits, gives up, switches to the
    internal drive, the console carries on in the fallback log, and the session records to the end: every board
    kept once, the 'session ended' line written."""
    import errno
    import sys

    import pyarrow.parquet as pq

    from nqbot import console, storage
    from nqbot.storage import Paths

    monkeypatch.setattr(storage, "SSD_EVENT_LOG", tmp_path / "events.log")
    t0 = datetime(2026, 10, 15, 18, 40, tzinfo=UTC)
    snap = {"symbol": "NDXP", "spot": 31000.0, "previousClose": 30990.0, "priceChange": 10.0,
            "priceChangePercent": 0.03, "expirations": ["2026-10-15"], "lagged": False,
            "strikes": [{"strike": 31000, "value": 1e6, "nodeType": "king"}]}
    body = sse(("connected", {"creditsRemaining": 1000}, None),
               *[("snapshot", {**snap, "asOf": z(t0 + timedelta(seconds=i))}, f"NDXP:{i}") for i in range(15)])
    replies = [body]

    def handler(request):
        return httpx.Response(200, content=replies.pop(0) if replies else sse(("ping", None, None)),
                              headers={"Content-Type": "text/event-stream"})

    ticks = iter(range(10**6))
    clock = lambda: int((t0.timestamp() + next(ticks)) * 1e9)  # noqa: E731

    async def nosleep(s):
        pass

    monkeypatch.setattr(step7, "SESSION_PART_MIN", 3 / 60)
    env.stream_factory = lambda: SkylitStream(Secret(KEY), symbols_map=SYMBOLS_MAP, maps=["NDXP"], metric="gamma",
                                              max_expirations=1, max_strikes="all", sleep=nosleep, clock_ns=clock,
                                              transport=httpx.MockTransport(handler))
    ssd_root = Path(env.paths.root)
    plug = {"out_at": None, "now": 0.0, "out_for": 45.0}  # seconds of fake storage-wait time

    def unplugged() -> bool:
        return plug["out_at"] is not None and plug["now"] - plug["out_at"] < plug["out_for"]

    def under_ssd(p) -> bool:
        try:
            return Path(p).resolve().is_relative_to(ssd_root.resolve())
        except OSError:
            return False

    gone = lambda p: FileNotFoundError(errno.ENOENT, "The system cannot find the path specified", str(p))  # noqa: E731
    real_mkdir, real_write_bytes = Path.mkdir, Path.write_bytes
    real_anchor, real_probe = storage._anchor_ok, storage._probe

    def mkdir(self, *a, **k):
        if unplugged() and under_ssd(self):
            raise gone(self)
        return real_mkdir(self, *a, **k)

    def write_bytes(self, data):
        if unplugged() and under_ssd(self):
            raise gone(self)
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.setattr(Path, "write_bytes", write_bytes)
    monkeypatch.setattr(storage, "_anchor_ok", lambda p: False if unplugged() and under_ssd(p) else real_anchor(p))
    monkeypatch.setattr(storage, "_probe", lambda p: False if unplugged() and under_ssd(p) else real_probe(p))
    monkeypatch.setattr(storage, "_sleep", lambda s: plug.update(now=plug["now"] + s))

    task_log = _VanishingConsole(unplugged)  # the task's console log lives on the SSD too
    real_write_part = step7.write_parquet

    def write_parquet(path, table, meta):  # pull the SSD as part 1 is about to be written
        if meta["part"] == 1 and plug["out_at"] is None:
            plug["out_at"] = plug["now"]
        real_write_part(path, table, meta)

    monkeypatch.setattr(step7, "write_parquet", write_parquet)
    monkeypatch.setattr(sys, "stdout", task_log)
    monkeypatch.setattr(sys, "stderr", task_log)
    monkeypatch.setenv(console.TASK_LOG_ENV, str(ssd_root / "logs" / "stream_session_20261015.txt"))
    env.echo = print
    local = Paths.under(tmp_path / "internal", "nqbot")
    env.now.t = datetime(2026, 10, 15, 14, 39, tzinfo=ET)
    with console.resilient_console(log_dir=tmp_path / "fb_logs"):
        rep = step7.check_stream_session(env, ["NDXP"], end=t0 + timedelta(seconds=14), fallback=local)
    assert plug["now"] - plug["out_at"] > storage.SSD_DROP_WAIT_SEC  # the wait ran out: a real >30-s unplug
    events = (tmp_path / "events.log").read_text(encoding="utf-8")
    assert "drive not back after 30 s: giving up" in events and "recording to" in events
    assert rep["fallback"]["from_part"] == 1
    ssd_parts = sorted(Path(rep["folder"]).glob("part_*_boards.parquet"))
    local_parts = sorted(Path(rep["fallback"]["folder"]).glob("part_*_boards.parquet"))
    assert [p.name for p in ssd_parts] == ["part_000_boards.parquet"] and len(local_parts) >= 2
    as_of = sorted(t for p in ssd_parts + local_parts for t in pq.read_table(p)["as_of"].cast("int64").to_pylist())
    n = len(as_of)
    assert n == rep["boards"] == rep["boards_per_map"]["NDXP"] >= 10 and rep["resent_boards_dropped"] == 0
    assert as_of == [int((t0 + timedelta(seconds=i)).timestamp() * 1000) for i in range(n)]  # none lost, none twice
    # the console: the SSD log stops at the drop, the rest (the switch line to 'session ended') is in the fallback log
    assert "SSD write failed" not in task_log.getvalue()
    fb = (tmp_path / "fb_logs" / "stream_session_20261015.txt").read_text(encoding="utf-8")
    assert "SSD write failed" in fb and "session ended" in fb.splitlines()[-1] and f"NDXP {n} boards" in fb


class _VanishingConsole(io.StringIO):
    """A console log file on the SSD: every write fails while `unplugged()` (like the 10-15 task log)."""

    def __init__(self, unplugged):
        super().__init__()
        self._unplugged = unplugged

    def write(self, s):
        if self._unplugged():
            raise OSError(9, "Bad file descriptor")
        return super().write(s)

    def flush(self):
        if self._unplugged():
            raise OSError(9, "Bad file descriptor")


def test_sigma_check_against_the_stored_row(env):
    """§13 Q-sigma (owner 2026-10-14): Tempest's live `day` lines vs the bot's close ± k x em_1d from the stored
    pre-open row of the previous session; the row is pulled first if missing, the cones answer is kept apart."""
    env.fake.routes["/v1/vol/symbols"] = lambda r: {"data": {"symbols": [
        {"symbol": s, "historyThrough": "2026-03-12"} for s in ("QQQ", "SPY")], "missing": []}}
    rowcols = {"QQQ": {"close": [600.0], "em_1d_pct": [0.8], "em_1d": [4.8]},
               "SPY": {"close": [500.0], "em_1d_pct": [0.6], "em_1d": [3.0]}}
    env.fake.routes["/v1/vol/history"] = lambda r: {"data": {"from": r.url.params["from"], "to": r.url.params["to"],
        "missing": [], "symbols": [{"symbol": s, "dates": [r.url.params["from"]], "columns": rowcols[s]}
                                   for s in r.url.params["symbols"].split(",")]}, "meta": {}}

    def cones(r):
        def day(c, pct, skew=0.0):
            em = c * pct / 100
            return {"levels": [{"name": "day", "anchor": c, "em1_pct": pct, "hi1": c + em, "lo1": c - em,
                                "hi2": c + 2 * em + skew, "lo2": c - 2 * em}]}
        return {"data": {"symbols": [{"symbol": "QQQ", "sessionDate": "2026-03-13", "cones": day(600.0, 0.8)},
                                     {"symbol": "SPY", "sessionDate": "2026-03-13", "cones": day(500.0, 0.6005, 0.5)}],
                         "missing": []}, "meta": {}}

    env.fake.routes["/v1/vol/cones"] = cones
    env.now = Now(datetime(2026, 3, 13, 8, 0, tzinfo=ET))
    with pytest.raises(step7.CheckRefused, match="08:30"):
        step7.check_sigma(env)
    env.now = Now(datetime(2026, 3, 13, 9, 5, tzinfo=ET))
    rep = step7.check_sigma(env)
    assert env.fake.paths().count("/v1/vol/history") == 1 and env.store.vol_files(date(2026, 3, 12), date(2026, 3, 12))
    q, s = rep["symbols"]["QQQ"], rep["symbols"]["SPY"]
    assert q["ok"] and q["two_sigma_symmetric"] and q["anchor_matches_close"] and q["bot_lines"]["hi2"] == 609.6
    assert s["ok"] is True and s["em1_pct_diff"] == 0.0005  # within 0.001 percentage points
    assert not s["two_sigma_symmetric"]  # a skew-shaped 2σ is reported, the owner decides
    assert list((env.store.root / "tempest_cones" / "2026-03-13").glob("cones_*.json"))
    env.now = Now(datetime(2026, 3, 13, 12, 0, tzinfo=ET))  # the midday run reuses the stored row
    step7.check_sigma(env)
    assert env.fake.paths().count("/v1/vol/history") == 1 and env.fake.paths().count("/v1/vol/cones") == 2


def test_compare_sigma_flags_a_mismatch():
    row = {"date": "2026-03-12", "close": 600.0, "em_1d_pct": 0.8}
    bad = {"levels": [{"name": "day", "em1_pct": 0.9, "hi1": 605.4, "lo1": 594.6, "hi2": 610.8, "lo2": 589.2}]}
    assert step7.compare_sigma(row, bad)["ok"] is False
    assert step7.compare_sigma(row, {"levels": []})["ok"] is None


def test_stream_drops_refusals_and_board_comparison(env):
    """Owner 2026-10-14 (§13 Q-mapstale c): history is pulled from the next session's pre-open, for a recorded session."""
    env.now.t = datetime(2026, 10, 12, 16, 30, tzinfo=ET)
    with pytest.raises(step7.CheckRefused, match="pre-open"):
        step7.check_stream_drops(env, date(2026, 10, 12))
    env.now.t = datetime(2026, 10, 13, 9, 0, tzinfo=ET)
    with pytest.raises(step7.CheckRefused, match="no session recording"):
        step7.check_stream_drops(env, date(2026, 10, 12))
    a = (31000.0, {31000.0: 1e6, 31010.0: -2e5})
    assert step7._same_board(a, (31000.0, {31000.0: 1e6, 31010.0: -2e5}))
    assert not step7._same_board(a, (31000.0, {31000.0: 1.01e6, 31010.0: -2e5}))  # a strike changed
    assert not step7._same_board(a, (31000.5, a[1])) and not step7._same_board(a, None)
