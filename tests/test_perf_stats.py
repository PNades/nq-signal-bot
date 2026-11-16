"""The stats panel (SPEC §9b item 6) on a hand-built ledger, and the synthetic ledger's shape."""
from datetime import date

from nqbot.performance import guard, synthetic, view
from nqbot.performance.ledger import Trade
from nqbot.performance.stats import stats

SETTINGS = {"months": {1: "2025-10", 2: "2025-11", 3: "2025-12", 4: "2026-01", 5: "2026-02", 6: "2026-03",
                       7: "2026-04", 8: "2026-05", 9: "2026-06", 10: "2026-07", 11: "2026-08", 12: "2026-09"}}


def t(i: int, usd: float, r: float, mae: float | None = -100.0) -> Trade:
    return Trade(id=f"T{i}", account="a", source="backtest", date=date(2026, 1, 5 + i // 2), entry_time=f"1{i % 2}:00:00",
                 exit_time="11:30:00", direction="long", style="d", minis=2, micros=0, entry=1.0, exit=2.0,
                 net_usd=usd, r=r, commission_usd=9.0, mae_usd=mae)


def test_hand_stats():
    ts = [t(0, 400, 1.2), t(1, 500, 1.5), t(2, -300, -1), t(3, 0, 0), t(4, -300, -1), t(5, -300, -1), t(6, 700, 2.1)]
    s = stats(ts)
    assert (s["trades"], s["wins"], s["losses"]) == (7, 3, 3)
    assert s["net_usd"] == 700 and s["net_r"] == 1.8 and s["win_rate"] == 0.5
    assert s["profit_factor"] == round(1600 / 900, 3)
    assert s["largest_win_usd"] == 700 and s["largest_loss_usd"] == -300
    assert s["longest_win_streak"] == 2 and s["longest_loss_streak"] == 2       # the $0 trade breaks the loss run
    assert s["max_dd_close_usd"] == -900                                         # 900 → 600 → 600 → 300 → 0
    assert s["max_dd_intraday_usd"] == -1000                                     # then the last trade's open −100 at 0


def test_intraday_needs_every_mae():
    assert stats([t(0, 400, 1.2), t(1, -300, -1, None)])["max_dd_intraday_usd"] is None


def test_synthetic_ledger_spans_every_phase_and_the_guard_holds_it_back():
    from nqbot.config import load_settings
    settings = {**load_settings(), **SETTINGS}
    trades = synthetic.ledger(settings)
    assert trades == synthetic.ledger(settings)                                  # seeded, repeatable
    g = guard.Guard(settings, frozenset({"12"}))
    page = view.page(trades, g, synthetic.DEFAULT_ACCOUNTS)
    assert set(page["phases_present"]) == {"development", "walk_forward", "sealed", "demo", "prop"}
    assert {n["phase"] for n in page["notices"]} == {"walk_forward", "sealed"}
    assert {p["phase"] for p in page["equity"]["phase_starts"]} <= {"development", "demo", "prop"}
    assert all(r["breach_at"] is None for r in page["drawdown"])                 # the seed's promise
