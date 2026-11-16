"""The P&L calendar (SPEC §9b item 6, owner 2026-11-04): a month as Mon-Fri weeks plus a Week column.

Each day: the date, net P&L, trade count, wins / losses, and its sign (+1 green, -1 red, 0 a $0 day: no colour;
None: no trades, blank). Week: its P&L, days traded, trades, wins / losses, win rate. Header: the month's P&L, trading
days (green / red counts), trades (W / L), best and worst day with their dates. Always net of commissions; days are New
York trading dates; `unit` "usd" or "r" (combined R = the sum of the accounts' R). Win = net > 0, loss < 0,
win rate = wins / (wins + losses).
"""
from __future__ import annotations

from calendar import Calendar
from datetime import date
from typing import Any, Iterable, Literal

from .ledger import Trade


def _val(ts: list[Trade], unit: str) -> float:
    return round(sum(t.net_usd for t in ts), 2) if unit == "usd" else round(sum(t.r for t in ts), 3)


def _rate(w: int, lo: int) -> float | None:
    return round(w / (w + lo), 4) if w + lo else None


def month(trades: Iterable[Trade], ym: str, unit: Literal["usd", "r"] = "usd") -> dict[str, Any]:
    y, m = (int(x) for x in ym.split("-"))
    by: dict[date, list[Trade]] = {}
    for t in trades:
        if t.date.year == y and t.date.month == m:
            by.setdefault(t.date, []).append(t)

    def cell(d: date) -> dict[str, Any]:
        ts = by.get(d, [])
        if not ts:
            return {"date": d.isoformat(), "in_month": d.month == m, "value": None, "trades": 0, "wins": 0, "losses": 0, "sign": None}
        v = _val(ts, unit)
        return {"date": d.isoformat(), "in_month": True, "value": v, "trades": len(ts),
                "wins": sum(t.net_usd > 0 for t in ts), "losses": sum(t.net_usd < 0 for t in ts),
                "sign": (v > 0) - (v < 0)}

    weeks = []
    for wk in Calendar(firstweekday=0).monthdatescalendar(y, m):
        days = [cell(d) for d in wk[:5]]                       # Mon-Fri
        traded = [c for c in days if c["value"] is not None and c["in_month"]]
        wins, losses = sum(c["wins"] for c in traded), sum(c["losses"] for c in traded)
        weeks.append({"days": days, "week": {
            "value": round(sum(c["value"] for c in traded), 3 if unit == "r" else 2) if traded else None,
            "days_traded": len(traded), "trades": sum(c["trades"] for c in traded), "wins": wins, "losses": losses,
            "win_rate": _rate(wins, losses)}})
    month_days = [cell(d) for d in sorted(by)]
    all_ts = [t for ts in by.values() for t in ts]
    wins, losses = sum(t.net_usd > 0 for t in all_ts), sum(t.net_usd < 0 for t in all_ts)
    best = max(month_days, key=lambda c: c["value"], default=None)
    worst = min(month_days, key=lambda c: c["value"], default=None)
    return {
        "month": ym, "unit": unit, "weeks": weeks,
        "header": {"value": _val(all_ts, unit) if all_ts else None, "trading_days": len(month_days),
                   "green_days": sum(c["sign"] == 1 for c in month_days), "red_days": sum(c["sign"] == -1 for c in month_days),
                   "trades": len(all_ts), "wins": wins, "losses": losses, "win_rate": _rate(wins, losses),
                   "best": {"date": best["date"], "value": best["value"]} if best else None,
                   "worst": {"date": worst["date"], "value": worst["value"]} if worst else None},
    }


def day_trades(trades: Iterable[Trade], day: date) -> list[dict[str, Any]]:
    """Clicking a day: its trades (time, account, direction, entry style, size, entry / exit, net $ and R)."""
    return [{"id": t.id, "time": t.entry_time, "account": t.account, "direction": t.direction, "style": t.style,
             "size": t.size, "entry": t.entry, "exit": t.exit, "net_usd": t.net_usd, "r": t.r, "source": t.source}
            for t in sorted(trades, key=lambda t: (t.entry_time, t.id)) if t.date == day]
