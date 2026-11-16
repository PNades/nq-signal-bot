"""The P&L calendar (SPEC §9b item 6): Mon-Fri weeks plus Week, a $0 day uncoloured, win rate, combined R, NY dates."""
from datetime import date

from nqbot.performance.ledger import Trade
from nqbot.performance.pnl_calendar import day_trades, month


def t(d: date, usd: float, r: float, acc: str = "a", tid: str = "") -> Trade:
    return Trade(id=tid or f"{d}-{usd}-{acc}", account=acc, source="backtest", date=d, entry_time="10:00:00", exit_time="10:30:00",
                 direction="long", style="d", minis=2, micros=0, entry=1.0, exit=2.0, net_usd=usd, r=r, commission_usd=9.0)


TRADES = [
    t(date(2026, 1, 5), 400.0, 1.2, tid="T1"), t(date(2026, 1, 5), -300.0, -1.0, "b", "T2"),   # Mon: +100, 1 W / 1 L
    t(date(2026, 1, 7), -333.0, -1.0),                                          # Wed: red
    t(date(2026, 1, 8), 9.0, 0.0), t(date(2026, 1, 8), -9.0, 0.0, "b"),         # Thu: exactly $0 → no colour
    t(date(2026, 2, 2), 999.0, 3.0),                                            # another month: ignored
]


def test_month_grid_and_header():
    m = month(TRADES, "2026-01")
    assert all(len(w["days"]) == 5 for w in m["weeks"])                       # Mon-Fri
    week2 = m["weeks"][1]                                                       # 2026-01-05 .. 01-09
    days = {c["date"]: c for c in week2["days"]}
    assert days["2026-01-05"]["value"] == 100.0 and days["2026-01-05"]["sign"] == 1
    assert days["2026-01-07"]["sign"] == -1
    assert days["2026-01-08"]["value"] == 0.0 and days["2026-01-08"]["sign"] == 0
    assert days["2026-01-06"]["value"] is None and days["2026-01-06"]["sign"] is None   # no trades: blank
    assert week2["week"] == {"value": -233.0, "days_traded": 3, "trades": 5, "wins": 2, "losses": 3, "win_rate": 0.4}
    h = m["header"]
    assert (h["value"], h["trading_days"], h["green_days"], h["red_days"], h["trades"], h["wins"], h["losses"]) == \
        (-233.0, 3, 1, 1, 5, 2, 3)
    assert h["best"] == {"date": "2026-01-05", "value": 100.0} and h["worst"] == {"date": "2026-01-07", "value": -333.0}


def test_combined_r_is_the_sum_of_the_accounts_r():
    m = month(TRADES, "2026-01", "r")
    assert m["header"]["value"] == round(1.2 - 1.0 - 1.0 + 0.0 + 0.0, 3)


def test_day_trades():
    # by entry time, then by id
    rows = day_trades(TRADES, date(2026, 1, 5))
    assert [r["account"] for r in rows] == ["a", "b"] and rows[0]["size"] == "2 NQ"
