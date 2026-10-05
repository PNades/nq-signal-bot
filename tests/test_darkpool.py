from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from nqbot.clients.skylit import SkylitError
from nqbot.features.darkpool import previous_session, top_prints, top_prints_api_window
from tests.skylit_fakes import FakeSkylit, make_client

ET = ZoneInfo("America/New_York")
weekday = lambda d: d.weekday() < 5  # noqa: E731


def p(ts: datetime, notional: float, ticker="QQQ", price=600.0):
    return {"timestamp": ts, "ticker": ticker, "price": price, "size": int(notional / price), "notional": notional}


def test_f4_cutoff_and_lookback():
    """feature_spec F4 tests: a print at 16:00:01 on T-1 is excluded at lag 1; a 46-day-old print is excluded."""
    t = date(2026, 3, 11)  # Wednesday; T-1 = Tuesday 2026-03-10
    cutoff = datetime(2026, 3, 10, 16, 0, tzinfo=ET)
    prints = [p(cutoff, 5e6), p(cutoff + timedelta(seconds=1), 9e9), p(cutoff - timedelta(days=45), 4e6),
              p(cutoff - timedelta(days=46), 8e9), p(cutoff - timedelta(days=1), 3e6, ticker="SPY")]
    top = top_prints(prints, "QQQ", t, top_n=3, lookback_days=45, lag_sessions=1, is_trading_day=weekday)
    assert [x["notional"] for x in top.prints] == [5e6, 4e6]
    assert top.cutoff == cutoff and not top.dp_partial
    lag2 = top_prints(prints, "QQQ", t, top_n=3, lookback_days=45, lag_sessions=2, is_trading_day=weekday)
    # one session earlier: the print 46 days before T-1 is exactly 45 days before T-2, so it counts
    assert lag2.cutoff == datetime(2026, 3, 9, 16, 0, tzinfo=ET) and [x["notional"] for x in lag2.prints] == [8e9, 4e6]


def test_f4_ties_and_partial_and_previous_session_over_weekend():
    t = date(2026, 3, 9)  # Monday -> previous session Friday
    assert previous_session(t, 1, weekday) == date(2026, 3, 6)
    a = datetime(2026, 3, 5, 10, 0, tzinfo=ET)
    prints = [p(a, 5e6), p(a + timedelta(hours=1), 5e6), p(a - timedelta(days=1), 1e6)]
    top = top_prints(prints, "QQQ", t, top_n=2, lookback_days=45, lag_sessions=1, is_trading_day=weekday,
                     history_start=date(2026, 2, 1))
    assert top.prints[0]["timestamp"] == a + timedelta(hours=1)  # tie -> later timestamp first
    assert top.dp_partial  # the 45-day lookback reaches before the first pulled date


def test_api_window_includes_as_of_date():
    x = date(2026, 3, 10)
    prints = [p(datetime(2026, 3, 10, 15, 0, tzinfo=ET), 7e6), p(datetime(2026, 1, 24, 10, 0, tzinfo=ET), 9e6),
              p(datetime(2026, 1, 23, 10, 0, tzinfo=ET), 9e9)]
    assert [x_["notional"] for x_ in top_prints_api_window(prints, "QQQ", x, top_n=3, lookback_days=45)] == [9e6, 7e6]


def _ts(day: str, i: int) -> str:
    return (datetime.fromisoformat(day + "T14:30:00+00:00") + timedelta(seconds=i)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def test_cursor_paging_across_pages(monkeypatch):
    import nqbot.clients.skylit as sk

    monkeypatch.setattr(sk, "DP_PAGE_LIMIT", 2)
    fake = FakeSkylit()
    fake.dp_prints = [{"timestamp": _ts("2026-03-05", i), "ticker": "QQQ", "price": 600.0, "size": 2000,
                       "notional": 1.2e6, "venue": "FINN", "pctAvgVol": None} for i in range(5)]
    pull = make_client(fake).dark_pool_trades(date(2026, 3, 5), ["QQQ", "SPY"], 1_000_000)
    assert len(pull.prints) == 5 and pull.pages == 3
    q = fake.calls[-1].url.params
    assert q["order"] == "asc" and q["sort"] == "time" and q["cursor"] == "4" and q["min_notional"] == "1000000"


def test_paging_fails_loudly_on_disorder_and_missing_cursor(monkeypatch):
    import nqbot.clients.skylit as sk

    monkeypatch.setattr(sk, "DP_PAGE_LIMIT", 2)
    fake = FakeSkylit()
    rows = [{"timestamp": _ts("2026-03-05", i), "ticker": "QQQ", "price": 1.0, "size": 1, "notional": 1.0} for i in (5, 6)]
    fake.queue["/v1/dark-pool/trades"] = [
        (200, {"data": rows, "meta": {"limit": 2, "count": 2, "nextCursor": "x"}}, {}),
        (200, {"data": [{**rows[0], "timestamp": _ts("2026-03-05", 1)}, rows[1]], "meta": {"limit": 2, "count": 2, "nextCursor": "y"}}, {}),
    ]
    with pytest.raises(SkylitError, match="before the last page"):
        make_client(fake).dark_pool_trades(date(2026, 3, 5), ["QQQ"], 1e6)
    fake.queue["/v1/dark-pool/trades"] = [(200, {"data": rows, "meta": {"limit": 2, "count": 2, "nextCursor": None}}, {})]
    with pytest.raises(SkylitError, match="no nextCursor"):
        make_client(fake).dark_pool_trades(date(2026, 3, 5), ["QQQ"], 1e6)


def test_page_cap(monkeypatch):
    import nqbot.clients.skylit as sk

    monkeypatch.setattr(sk, "DP_PAGE_LIMIT", 1)
    monkeypatch.setattr(sk, "DP_MAX_PAGES", 3)
    fake = FakeSkylit()
    fake.dp_prints = [{"timestamp": _ts("2026-03-05", i), "ticker": "QQQ", "price": 1.0, "size": 1, "notional": 1.0}
                      for i in range(10)]
    with pytest.raises(SkylitError, match="3 pages"):
        make_client(fake).dark_pool_trades(date(2026, 3, 5), ["QQQ"], 1e6)
