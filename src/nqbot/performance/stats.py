"""The stats panel and the day series (SPEC §9b item 6 Stats panel). A win = a trade with net P&L > 0, a loss < 0 (a
$0 trade is neither and breaks a streak); win rate = wins / (wins + losses); profit factor = gross wins / gross losses.
Max drawdown close-to-close: the deepest fall of the trade-by-trade equity from its running peak; intraday: the same
with each trade's worst open loss (`mae_usd`) added at its point, only when every trade stores it."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from .ledger import Trade


@dataclass(frozen=True)
class Day:
    date: date
    usd: float
    r: float
    trades: int
    wins: int
    losses: int


def days(trades: Iterable[Trade]) -> list[Day]:
    """One entry per trading date that has trades, in date order."""
    by: dict[date, list[Trade]] = {}
    for t in trades:
        by.setdefault(t.date, []).append(t)
    return [Day(d, round(sum(t.net_usd for t in ts), 2), round(sum(t.r for t in ts), 4), len(ts),
                sum(t.net_usd > 0 for t in ts), sum(t.net_usd < 0 for t in ts)) for d, ts in sorted(by.items())]


def _streaks(trades: list[Trade]) -> tuple[list[int], list[int]]:
    wins: list[int] = []
    losses: list[int] = []
    run, kind = 0, 0
    for t in trades:
        k = 1 if t.net_usd > 0 else -1 if t.net_usd < 0 else 0
        if k == kind and k != 0:
            run += 1
            continue
        if kind == 1:
            wins.append(run)
        elif kind == -1:
            losses.append(run)
        run, kind = (1, k) if k else (0, 0)
    if kind == 1:
        wins.append(run)
    elif kind == -1:
        losses.append(run)
    return wins, losses


def max_drawdown(values: Iterable[float]) -> float:
    """The deepest fall (≤ 0) of a running total from its running peak (the start counts as 0)."""
    total = peak = worst = 0.0
    for v in values:
        total += v
        peak = max(peak, total)
        worst = min(worst, total - peak)
    return round(worst, 2)


def stats(trades: Iterable[Trade]) -> dict[str, Any]:
    ts = sorted(trades, key=lambda t: (t.date, t.entry_time, t.id))
    n = len(ts)
    w = [t for t in ts if t.net_usd > 0]
    lo = [t for t in ts if t.net_usd < 0]
    gross_w, gross_l = sum(t.net_usd for t in w), -sum(t.net_usd for t in lo)
    net, net_r = sum(t.net_usd for t in ts), sum(t.r for t in ts)
    sw, sl = _streaks(ts)
    intraday = None
    if ts and all(t.mae_usd is not None for t in ts):
        total = peak = worst = 0.0
        for t in ts:
            worst = min(worst, total + min(0.0, t.mae_usd or 0.0) - peak)   # the open loss at its worst point
            total += t.net_usd
            peak = max(peak, total)
            worst = min(worst, total - peak)
        intraday = round(worst, 2)
    r2 = lambda x: round(x, 2)  # noqa: E731
    return {
        "trades": n, "wins": len(w), "losses": len(lo),
        "net_usd": r2(net), "net_r": round(net_r, 3), "r_per_trade": round(net_r / n, 4) if n else None,
        "win_rate": round(len(w) / (len(w) + len(lo)), 4) if (w or lo) else None,
        "avg_win_usd": r2(gross_w / len(w)) if w else None, "avg_loss_usd": r2(-gross_l / len(lo)) if lo else None,
        "largest_win_usd": r2(max(t.net_usd for t in w)) if w else None,
        "largest_loss_usd": r2(min(t.net_usd for t in lo)) if lo else None,
        "profit_factor": round(gross_w / gross_l, 3) if gross_l > 0 else None,
        "max_dd_close_usd": max_drawdown(t.net_usd for t in ts), "max_dd_intraday_usd": intraday,
        "longest_win_streak": max(sw, default=0), "longest_loss_streak": max(sl, default=0),
        "avg_win_streak": round(sum(sw) / len(sw), 2) if sw else None,
        "avg_loss_streak": round(sum(sl) / len(sl), 2) if sl else None,
    }
