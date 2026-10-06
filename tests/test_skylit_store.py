from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest

from nqbot.clients.skylit_pull import (
    SessionResult, SkylitPullJob, pull_session, pull_window, session_windows, uncached_windows,
)
from nqbot.clients.skylit_store import (
    CacheMismatch, SkylitStore, ms, range_to_tables, read_meta, vol_history_to_table,
)
from nqbot.features.mapping import board_tau_ns, last_price_at_or_before, ratio_for_boards
from nqbot.jobs.runner import StopChunk, run_job
from tests.skylit_fakes import SYMBOLS_MAP, FakeSkylit
from tests.skylit_fakes import make_client as _make_client


def make_client(fake, store=None):
    """Like the real make_client: every call to the ledger, the free /v1/account read first (charges known)."""
    client = _make_client(fake, on_call=store.log_call if store else None)
    client.account()
    return client

ET = ZoneInfo("America/New_York")
ROOT_TO_MAP = {v: k for k, v in SYMBOLS_MAP.items()}
CAL = {"holidays": [{"date": date(2026, 4, 3)}], "early_closes": [{"date": date(2025, 11, 28), "early_close_et": "13:00"}]}

# One hand-built /v1/historical/range answer: QQQ with an axis change mid-window, SPXW one frame.
BODY = {
    "data": {"from": "2026-03-05T14:30:00Z", "to": "2026-03-05T14:44:59.999Z", "symbols": [
        {"symbol": "QQQ",
         "axes": [{"id": 0, "strikes": [510, 512, 515], "expirations": ["2026-03-05"]},
                  {"id": 1, "strikes": [512, 515, 517.5, 520], "expirations": ["2026-03-05"]}],
         "frames": [
             {"asOf": "2026-03-05T14:30:00.000Z", "axis": 0, "previousClose": 510.02, "spot": 512.4,
              "values": [88010, 1500200, 410000]},
             {"asOf": "2026-03-05T14:30:01.000Z", "axis": 1, "previousClose": 510.02, "spot": 512.43,
              "values": [1502113.2, 409877.1, -20000.5, 99.0]}]},
        {"symbol": "SPXW",
         "axes": [{"id": 0, "strikes": [6500, 6505], "expirations": ["2026-03-05"]}],
         "frames": [{"asOf": "2026-03-05T14:30:00.500Z", "axis": 0, "previousClose": 6480.0, "spot": 6502.25,
                     "values": [-3.5e6, 1.25e6]}]}]},
    "meta": {"metric": "gamma", "resolution": "1s", "mode": "historical", "cached": False},
}
W_FROM = datetime(2026, 3, 5, 9, 30, tzinfo=ET)
W_TO = datetime(2026, 3, 5, 9, 44, 59, 999000, tzinfo=ET)


def test_range_to_tables_exact():
    boards, strikes = range_to_tables(BODY, ROOT_TO_MAP, W_FROM, W_TO)
    assert boards.num_rows == 3 and strikes.num_rows == 3 + 4 + 2
    b = boards.to_pylist()
    assert [r["symbol"] for r in b] == ["QQQ", "QQQ", "SPX"] and b[2]["api_symbol"] == "SPXW"
    assert b[1]["axis_id"] == 1 and b[1]["n_strikes"] == 4 and b[0]["expirations"] == "2026-03-05"
    assert b[2]["as_of"] == datetime(2026, 3, 5, 14, 30, 0, 500000, tzinfo=timezone.utc)
    assert b[0]["spot"] == 512.4 and b[0]["resolution"] == "1s"
    s = strikes.to_pylist()
    assert [r["strike"] for r in s[3:7]] == [512, 515, 517.5, 520]  # float32 holds half strikes exactly
    assert s[5]["value"] == np.float32(-20000.5)
    assert {r["symbol"] for r in s[7:]} == {"SPX"}


def test_range_rejects_misaligned_values_and_unknown_symbols():
    bad = {**BODY, "data": {**BODY["data"], "symbols": [{**BODY["data"]["symbols"][1], "frames": [
        {**BODY["data"]["symbols"][1]["frames"][0], "values": [1.0]}]}]}}
    with pytest.raises(ValueError, match="values"):
        range_to_tables(bad, ROOT_TO_MAP, W_FROM, W_TO)
    with pytest.raises(ValueError, match="requested roots"):
        range_to_tables(BODY, {"QQQ": "QQQ"}, W_FROM, W_TO)


def test_ms_is_exact():
    assert ms("2026-03-05T14:30:00.123Z") == 1772721000123
    assert ms("2026-03-05T14:30:00Z") == 1772721000000


def test_r_rebuilt_from_stored_sample(paths):
    """SPEC §11 Storage: rebuild R = F / spot from the stored per-board table (τ = asOf; asOf + 60 s on 1m boards)."""
    store = SkylitStore(paths)
    boards, strikes = range_to_tables(BODY, ROOT_TO_MAP, W_FROM, W_TO)
    store.write_range(date(2026, 3, 5), "0930", boards, strikes, {"params": {"x": 1}})
    stored, _ = store.read_range(date(2026, 3, 5))
    q = stored.filter(pc.equal(stored["symbol"], "QQQ"))
    as_of_ns = q["as_of"].cast("int64").to_numpy() * 1_000_000
    t0 = as_of_ns[0]
    # NQ trades: one at exactly τ of board 0, one 1 ns after it, one at τ of board 1
    trade_ts = np.array([t0 - 5_000_000_000, t0, t0 + 1, as_of_ns[1]], dtype=np.int64)
    trade_px = np.array([20990.0, 21000.0, 21000.25, 21001.0])
    r = ratio_for_boards(as_of_ns, q["spot"].to_numpy(), q["resolution"].to_numpy(zero_copy_only=False), trade_ts, trade_px)
    assert r[0] == pytest.approx(21000.0 / 512.4, rel=1e-15)  # the trade at τ counts, the one 1 ns later doesn't
    assert r[1] == pytest.approx(21001.0 / 512.43, rel=1e-15)
    # the same board as an uncleared 1-minute board: F is taken 60 s later
    trade_ts2 = np.append(trade_ts, t0 + 60_000_000_000)
    trade_px2 = np.append(trade_px, 21010.0)
    r1m = ratio_for_boards(as_of_ns[:1], q["spot"].to_numpy()[:1], "1m", trade_ts2, trade_px2)
    assert r1m[0] == pytest.approx(21010.0 / 512.4)
    assert ratio_for_boards(as_of_ns[:1], q["spot"].to_numpy()[:1], "1m", trade_ts2, trade_px2, spot_lag_cleared=True)[0] \
        == pytest.approx(21000.0 / 512.4)


def test_tau_and_last_price_edges():
    assert list(board_tau_ns(np.array([0, 0]), np.array(["1s", "1m"]))) == [0, 60_000_000_000]
    px = last_price_at_or_before(np.array([10, 20]), np.array([1.0, 2.0]), np.array([9, 10, 19, 20, 99]))
    assert np.isnan(px[0]) and list(px[1:]) == [1.0, 1.0, 2.0, 2.0]
    with pytest.raises(ValueError):
        last_price_at_or_before(np.array([20, 10]), np.array([1.0, 2.0]), np.array([15]))


def test_parquet_is_zstd_with_metadata(paths):
    store = SkylitStore(paths)
    boards, strikes = range_to_tables(BODY, ROOT_TO_MAP, W_FROM, W_TO)
    bpath, spath = store.write_range(date(2026, 3, 5), "0930", boards, strikes, {"params": {"maxStrikes": 200}, "charged": 25})
    assert pq.ParquetFile(spath).metadata.row_group(0).column(0).compression == "ZSTD"
    assert read_meta(bpath)["charged"] == 25 and str(bpath).startswith(str(paths.raw_dir / "skylit"))


# --- window planner -------------------------------------------------------------------------------------

def test_normal_day_has_26_inclusive_windows():
    ws = session_windows(date(2026, 3, 5), CAL)
    assert len(ws) == 26 and not any(w.probe for w in ws)
    assert ws[0].start.time() == time(9, 30) and ws[0].end == datetime(2026, 3, 5, 9, 44, 59, 999000, tzinfo=ET)
    assert ws[-1].end.time() == time(16, 0)
    for a, b in zip(ws, ws[1:]):
        assert b.start - a.end == timedelta(milliseconds=1)


def test_dst_days_map_to_the_right_utc_times():
    winter, summer = session_windows(date(2026, 3, 6), CAL)[0], session_windows(date(2026, 3, 9), CAL)[0]
    assert winter.start.astimezone(timezone.utc).hour == 14 and summer.start.astimezone(timezone.utc).hour == 13


def test_early_close_and_holiday_probes():
    ec = session_windows(date(2025, 11, 28), CAL)
    assert len(ec) == 14 + 1 and ec[-1].probe and ec[-2].end.time() == time(13, 0)
    hol = session_windows(date(2026, 4, 3), CAL)
    assert len(hol) == 1 and hol[0].probe
    assert session_windows(date(2026, 3, 7), CAL) == []  # Saturday


# --- cache ----------------------------------------------------------------------------------------------

def test_second_pull_makes_no_call_and_params_must_match(paths, log):
    fake = FakeSkylit()
    store = SkylitStore(paths)
    client = make_client(fake, store)
    w = session_windows(date(2026, 3, 5), CAL)[0]
    pull_window(client, store, w, result=SessionResult(w.day), log=log)
    n = len(fake.calls)
    res = SessionResult(w.day)
    pull_window(client, store, w, result=res, log=log)
    assert len(fake.calls) == n and res.cached == ["0930"]
    with pytest.raises(CacheMismatch):
        pull_window(client, store, w, max_strikes=300, result=SessionResult(w.day), log=log)
    assert len(fake.calls) == n
    rng = [r for r in store.ledger() if r["endpoint"] == "/v1/historical/range"]
    assert rng[0]["charged"] == 25 and rng[0]["window"] == "2026-03-05 0930"


def test_partial_write_is_pulled_again_with_reason(paths, log):
    fake = FakeSkylit()
    store = SkylitStore(paths)
    client = make_client(fake, store)
    w = session_windows(date(2026, 3, 5), CAL)[0]
    pull_window(client, store, w, result=SessionResult(w.day), log=log)
    store.range_paths(w.day, w.hhmm)[0].unlink()  # boards file lost (crash between the two renames)
    assert store.range_partial(w.day, w.hhmm)
    orphan = store.range_paths(w.day, w.hhmm)[1].read_bytes()
    pull_window(client, store, w, result=SessionResult(w.day), log=log)
    assert store.ledger()[-1]["repull"] == "partial_write" and not store.range_partial(w.day, w.hhmm)
    moved = list((store.root / "repull" / "partial_write").rglob("0930_strikes.parquet"))
    assert len(moved) == 1 and moved[0].read_bytes() == orphan  # the orphan is kept, never overwritten


def test_repull_goes_to_its_own_tree(paths, log):
    fake = FakeSkylit()
    client = make_client(fake)
    store = SkylitStore(paths)
    w = session_windows(date(2026, 3, 5), CAL)[0]
    pull_window(client, store, w, result=SessionResult(w.day), log=log)
    original = store.range_paths(w.day, w.hhmm)[0].read_bytes()
    base = store.base("parity", pulled="t1")
    pull_window(client, store, w, result=SessionResult(w.day), log=log, base=base, repull="parity")
    assert store.range_paths(w.day, w.hhmm)[0].read_bytes() == original
    assert store.range_paths(w.day, w.hhmm, base)[0].exists() and "repull" in str(base)
    with pytest.raises(ValueError, match="re-pull reason"):
        store.base("because")


def test_pull_session_and_holiday_probe_without_data(paths, log):
    fake = FakeSkylit()
    client = make_client(fake)
    store = SkylitStore(paths)
    res = pull_session(client, store, date(2026, 3, 5), log=log, cal=CAL)
    assert len(res.pulled) == 26 and res.charged == 26 * 25 and res.resolutions["0930"] == "1s"
    fake.queue["/v1/historical/range"] = [(404, {"error": {"code": "no_data", "message": "holiday"}}, {})]
    hol = pull_session(client, store, date(2026, 4, 3), log=log, cal=CAL)
    assert hol.empty == ["0930"] and hol.pulled == []
    assert uncached_windows(store, client, [date(2026, 3, 5), date(2026, 3, 6)], cal=CAL) == 26


def test_holiday_probe_with_boards_pulls_the_whole_day(paths, log):
    client = make_client(FakeSkylit())
    res = pull_session(client, SkylitStore(paths), date(2026, 4, 3), log=log, cal=CAL)
    assert len(res.pulled) == 26


def test_session_stops_between_windows_and_keeps_what_it_pulled(paths, log):
    client = make_client(FakeSkylit())
    store = SkylitStore(paths)
    calls = iter([None, None, "clock"])
    with pytest.raises(StopChunk):
        pull_session(client, store, date(2026, 3, 5), log=log, cal=CAL, should_stop=lambda: next(calls))
    assert store.stored_windows(date(2026, 3, 5)) == ["0930", "0945"]


def test_job_resumes_and_gates_credits(paths, settings, log, clock, monkeypatch):
    import nqbot.clients.skylit_pull as sp

    monkeypatch.setattr(sp.cal_mod, "is_trading_day", lambda d, cal=None: d.weekday() < 5)
    monkeypatch.setattr(sp.cal_mod, "early_close_et", lambda d, cal=None: None)
    fake = FakeSkylit()
    job = SkylitPullJob("skylit_sample_days", client_factory=lambda s, store, lg: make_client(fake))
    days = [date(2026, 3, 2), date(2026, 3, 3)]
    SkylitPullJob.write_plan(paths, "skylit_sample_days", days, approved=None)
    result = run_job(job, settings, paths, until=clock.now() + timedelta(hours=8), log=log, now=clock.now,
                     sleep=clock.sleep, is_trading_day=lambda d: d.weekday() < 5)
    assert result.status == "complete" and result.done == 2
    n = len(fake.calls)
    job2 = SkylitPullJob("skylit_sample_days", client_factory=lambda s, store, lg: make_client(fake))
    SkylitPullJob.write_plan(paths, "skylit_sample_days", days + [date(2026, 3, 4)], approved=None)
    run_job(job2, settings, paths, until=clock.now() + timedelta(hours=8), log=log, now=clock.now, sleep=clock.sleep,
            is_trading_day=lambda d: d.weekday() < 5)
    assert len(fake.calls) - n == 1 + 26  # the free account read, then only the new day


def test_job_refuses_big_unapproved_pull(paths, settings, log, clock, monkeypatch):
    import nqbot.clients.skylit_pull as sp
    from nqbot.clients.skylit_credits import CreditRefused

    monkeypatch.setattr(sp.cal_mod, "is_trading_day", lambda d, cal=None: d.weekday() < 5)
    monkeypatch.setattr(sp.cal_mod, "early_close_et", lambda d, cal=None: None)
    days = [date(2026, 3, 2) + timedelta(days=i) for i in range(10) if (date(2026, 3, 2) + timedelta(days=i)).weekday() < 5]
    SkylitPullJob.write_plan(paths, "skylit_sample_days", days, approved=None)  # 8 days x 650 = 5,200
    job = SkylitPullJob("skylit_sample_days", client_factory=lambda s, store, lg: make_client(FakeSkylit()))
    with pytest.raises(CreditRefused):
        run_job(job, settings, paths, until=clock.now() + timedelta(hours=8), log=log, now=clock.now, sleep=clock.sleep,
                is_trading_day=lambda d: True)


def test_vol_history_table():
    body = {"data": {"from": "2026-03-02", "to": "2026-03-03", "missing": [], "symbols": [
        {"symbol": "QQQ", "dates": ["2026-03-02", "2026-03-03"], "columns": {"svx30": [20.1, None], "close": [600.0, 601.5]}},
        {"symbol": "SPY", "dates": ["2026-03-02"], "columns": {"svx30": [15.0]}}]}, "meta": {}}
    t = vol_history_to_table(body).to_pylist()
    assert t[1] == {"symbol": "QQQ", "date": date(2026, 3, 3), "close": 601.5, "svx30": None}
    assert t[2]["close"] is None and t[2]["svx30"] == 15.0


def test_quarantine_waits_out_an_antivirus_lock(paths, monkeypatch):
    """presd_fixes 17: moving the orphan aside retries while Windows reports the file busy."""
    from nqbot import storage

    store = SkylitStore(paths)
    day = date(2026, 3, 5)
    strikes = store.range_paths(day, "0930")[1]
    strikes.parent.mkdir(parents=True, exist_ok=True)
    strikes.write_bytes(b"orphan")
    real, calls = storage.os.replace, []

    def busy(src, dst):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(13, "Access is denied")
        real(src, dst)

    monkeypatch.setattr(storage.os, "replace", busy)
    monkeypatch.setattr(storage, "REPLACE_RETRY_SEC", 0)
    dest = store.quarantine_partial(day, "0930")
    assert dest is not None and dest.read_bytes() == b"orphan" and not strikes.exists() and len(calls) == 3


def test_job_stops_cleanly_on_402(paths, settings, log, clock, monkeypatch):
    """presd_fixes 17: a 402 mid-pull is a clean "credits" stop, not a traceback; the day re-runs after a top-up."""
    import nqbot.clients.skylit_pull as sp

    monkeypatch.setattr(sp.cal_mod, "is_trading_day", lambda d, cal=None: d.weekday() < 5)
    monkeypatch.setattr(sp.cal_mod, "early_close_et", lambda d, cal=None: None)
    fake = FakeSkylit()
    fake.queue["/v1/historical/range"] = [(402, {"error": {"code": "insufficient_credits", "message": "top up"}}, None)]
    job = SkylitPullJob("skylit_sample_days", client_factory=lambda s, store, lg: make_client(fake))
    SkylitPullJob.write_plan(paths, "skylit_sample_days", [date(2026, 3, 2)], approved=None)
    result = run_job(job, settings, paths, until=clock.now() + timedelta(hours=8), log=log, now=clock.now,
                     sleep=clock.sleep, is_trading_day=lambda d: d.weekday() < 5)
    assert result.status == "credits" and result.done == 0 and "top up" in result.message
