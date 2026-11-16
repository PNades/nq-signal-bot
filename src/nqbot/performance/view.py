"""What the Performance page shows for one selection (SPEC §9b item 6), built from the ledger through the guard.

Selection: an account (or all), a phase (all, development, walk_forward, sealed, demo, prop, live) and a date range.
- KPIs and the stats panel: the selection's totals at the "total" level (walk-forward / sealed totals only once their
  gate is recorded).
- Equity, monthly sparklines, the calendar and the trades table: "day" / "trade" level only (development backtest and
  the live journal); the rest is held back with the reason (`notices`).
- Drawdown and Monte Carlo: per account with a drawdown rule, on that account's day-level trades.
- Live vs expected: the demo / live R curve over the band the development backtest predicts for the same number of
  trades (whole backtest days drawn until the trade count is reached, 1,000 paths, 5-95%).
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from .floor import Rule, floor_after, floor_path
from .guard import Guard, apply
from .ledger import Trade
from .stats import days, stats

PHASES = ("all", "development", "walk_forward", "sealed", "demo", "prop", "live")
PHASE_LABEL = {"development": "Development (backtest)", "walk_forward": "Walk-forward 7–9", "sealed": "Sealed",
               "demo": "Demo", "prop": "Prop", "live": "Live", "outside": "Before the window"}
EXPECTED_PATHS = 1000
# the KPI tiles' badge (owner 2026-11-15): the last RECENT_DAYS trading days against the earlier ones
RECENT_DAYS = 5


def phase_of(t: Trade, guard: Guard) -> str:
    return t.source if t.source != "backtest" else guard.phase_of(t)


def select(trades: Iterable[Trade], guard: Guard, *, account: str = "all", phase: str = "all",
           start: date | None = None, end: date | None = None) -> list[Trade]:
    out = []
    for t in trades:
        # the account filter applies to real trades; backtest trades are the strategy's (owner 2026-11-15)
        if account != "all" and t.source != "backtest" and t.account != account:
            continue
        if phase != "all" and phase_of(t, guard) != phase:
            continue
        if (start and t.date < start) or (end and t.date > end):
            continue
        out.append(t)
    return out


def _notices(held: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"phase": p, "label": PHASE_LABEL.get(p, p), **h} for p, h in sorted(held.items())]


def _monthly(day_trades: list[Trade]) -> list[dict[str, Any]]:
    by: dict[str, list[Trade]] = {}
    for t in day_trades:
        by.setdefault(f"{t.date.year:04d}-{t.date.month:02d}", []).append(t)
    out = []
    for ym, ts in sorted(by.items()):
        s = stats(ts)
        out.append({"month": ym, "net_usd": s["net_usd"], "net_r": s["net_r"], "trades": s["trades"],
                    "r_per_trade": s["r_per_trade"], "win_rate": s["win_rate"], "profit_factor": s["profit_factor"],
                    "max_dd_usd": s["max_dd_close_usd"]})
    return out


def _cmp(ts: list[Trade], n_days: int) -> dict[str, Any]:
    s = stats(ts)
    return {"net_per_day_usd": round(s["net_usd"] / n_days, 2), "net_per_day_r": round(s["net_r"] / n_days, 3),
            "trades_per_day": round(s["trades"] / n_days, 3),
            "usd_per_trade": round(s["net_usd"] / s["trades"], 2) if s["trades"] else None, "r_per_trade": s["r_per_trade"],
            "win_rate": s["win_rate"], "profit_factor": s["profit_factor"], "max_dd_usd": s["max_dd_close_usd"]}


def kpi_trend(day_trades: list[Trade]) -> dict[str, Any]:
    """The KPI tiles' sparklines, one point per trading day (owner 2026-11-15; prop accounts rarely last months): net
    to date ($ and R), trades that day, net per trade to date, win rate and profit factor to date, the drawdown from the
    high to date. The badge: the last min(RECENT_DAYS, days - 1) trading days against the earlier ones (None with
    fewer than 2 days)."""
    by: dict[Any, list[Trade]] = {}
    for t in sorted(day_trades, key=lambda t: (t.date, t.entry_time, t.id)):
        by.setdefault(t.date, []).append(t)
    dates = sorted(by)
    series: dict[str, list[float | None]] = {k: [] for k in ("net_usd", "net_r", "trades", "usd_per_trade", "r_per_trade",
                                                              "win_rate", "profit_factor", "dd_usd")}
    seen: list[Trade] = []
    peak = 0.0
    for d in dates:
        seen.extend(by[d])
        s = stats(seen)
        peak = max(peak, s["net_usd"])
        series["net_usd"].append(s["net_usd"]); series["net_r"].append(s["net_r"])
        series["trades"].append(float(len(by[d])))
        series["usd_per_trade"].append(round(s["net_usd"] / s["trades"], 2) if s["trades"] else None)
        series["r_per_trade"].append(s["r_per_trade"]); series["win_rate"].append(s["win_rate"])
        series["profit_factor"].append(s["profit_factor"]); series["dd_usd"].append(round(s["net_usd"] - peak, 2))
    compare = None
    if len(dates) >= 2:
        k = min(RECENT_DAYS, len(dates) - 1)
        recent = [t for d in dates[-k:] for t in by[d]]
        earlier = [t for d in dates[:-k] for t in by[d]]
        compare = {"days": k, "recent": _cmp(recent, k), "earlier": _cmp(earlier, len(dates) - k)}
    return {"dates": [d.isoformat() for d in dates], "series": series, "compare": compare}


def _clock(d: date, hhmmss: str) -> int:
    """A New York wall-clock time as seconds on the chart's time axis (shown as written: the axis is read as UTC)."""
    h, m, s = (int(x) for x in (hhmmss.split(":") + ["0", "0"])[:3])
    return int(datetime(d.year, d.month, d.day, h, m, s, tzinfo=timezone.utc).timestamp())


def _before(d: date) -> date:
    """The weekday before `d` (the curve's $0 start point on a day axis)."""
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def equity(day_trades: list[Trade], guard: Guard, *, per_trade: bool = False) -> dict[str, Any]:
    """The cumulative curve with each point's phase, and where each phase starts (owner 2026-11-16): it starts at $0 / 0R
    at the start of the period or the account, so one trade still draws a line. Backtest phases: one point per trading
    day (`time` = the date). Real-trades phases (`per_trade`): one point per trade at its exit time (`time` = seconds,
    New York wall clock), the trading days labelled along the bottom by the chart."""
    if not day_trades:
        return {"points": [], "phase_starts": [], "per_trade": per_trade}
    usd = r = peak = 0.0
    points: list[dict[str, Any]] = []
    starts: list[dict[str, Any]] = []
    last = None
    ordered = sorted(day_trades, key=lambda t: (t.date, t.exit_time or t.entry_time, t.id))
    first = ordered[0]
    ph0 = phase_of(first, guard)
    if per_trade:
        t0 = _clock(first.date, first.entry_time)
        points.append({"time": t0, "date": first.date.isoformat(), "clock": first.entry_time[:5], "usd": 0.0, "r": 0.0, "dd_usd": 0.0,
                       "day_usd": 0.0, "day_r": 0.0, "trades": 0, "phase": ph0, "start": True})
        for t in ordered:
            usd, r = usd + t.net_usd, r + t.r
            peak = max(peak, usd)
            ph = phase_of(t, guard)
            tm = max(_clock(t.date, t.exit_time or t.entry_time), points[-1]["time"] + 1)   # strictly increasing
            if ph != last:
                starts.append({"time": tm, "date": t.date.isoformat(), "phase": ph, "label": PHASE_LABEL.get(ph, ph)})
                last = ph
            points.append({"time": tm, "date": t.date.isoformat(), "clock": (t.exit_time or t.entry_time)[:5], "usd": round(usd, 2),
                           "r": round(r, 3), "dd_usd": round(usd - peak, 2), "day_usd": t.net_usd, "day_r": t.r, "trades": 1,
                           "phase": ph, "trade": t.id})
        return {"points": points, "phase_starts": starts, "per_trade": True}
    by_phase: dict[Any, str] = {}
    for t in day_trades:
        by_phase.setdefault(t.date, phase_of(t, guard))
    d0 = _before(first.date).isoformat()
    points.append({"time": d0, "date": d0, "usd": 0.0, "r": 0.0, "dd_usd": 0.0, "day_usd": 0.0, "day_r": 0.0, "trades": 0,
                   "phase": ph0, "start": True})
    for d in days(day_trades):
        usd, r = usd + d.usd, r + d.r
        peak = max(peak, usd)
        ph = by_phase[d.date]
        if ph != last:
            starts.append({"time": d.date.isoformat(), "date": d.date.isoformat(), "phase": ph, "label": PHASE_LABEL.get(ph, ph)})
            last = ph
        points.append({"time": d.date.isoformat(), "date": d.date.isoformat(), "usd": round(usd, 2), "r": round(r, 3),
                       "dd_usd": round(usd - peak, 2), "day_usd": d.usd, "day_r": d.r, "trades": d.trades, "phase": ph})
    return {"points": points, "phase_starts": starts, "per_trade": False}


def account_run(trades: list[Trade], account: dict[str, Any]) -> dict[str, Any]:
    """One account's end-of-day balances under its own floor rule, the closest approach to it marked."""
    rule = Rule.of(account)
    ds = days(trades)
    bal, b = [], rule.start_usd
    for d in ds:
        b += d.usd
        bal.append(round(b, 2))
    fp = floor_path(rule, bal)
    # room to the floor in force at each session end (the floor set at the previous one)
    prev = [rule.start_usd - rule.drawdown_usd] + fp.floor[:-1]
    room = [round(x - f, 2) for x, f in zip(bal, prev)]
    closest = min(range(len(room)), key=lambda i: room[i]) if room else None
    return {"account": account.get("name"), "rule": rule.text(), "rule_type": rule.type, "start_usd": rule.start_usd,
            "drawdown_usd": rule.drawdown_usd, "dates": [d.date.isoformat() for d in ds], "balance": bal,
            "floor": [round(x, 2) for x in fp.floor], "high": [round(x, 2) for x in fp.high], "room": room,
            "locked_at": fp.locked_at, "breach_at": fp.breach_at, "closest_at": closest}


def account_trade_run(trades: list[Trade], account: dict[str, Any], horizon: int | None = None) -> dict[str, Any]:
    """One account's balance after each trade, at its exit time (owner 2026-11-16: real-trades phases, as the equity
    curve), from a start point before the first shown trade; the floor in force during each session (set at the
    previous session end, §6.7); the last `horizon` trading days (None = all)."""
    rule = Rule.of(account)
    by_day: dict[date, list[Trade]] = {}
    for t in sorted(trades, key=lambda t: (t.date, t.exit_time or t.entry_time, t.id)):
        by_day.setdefault(t.date, []).append(t)
    days_all = sorted(by_day)
    keep_from = days_all[-horizon] if (horizon and len(days_all) > horizon) else (days_all[0] if days_all else None)
    bal = hw = rule.start_usd
    floor = rule.start_usd - rule.drawdown_usd
    out: dict[str, list[Any]] = {k: [] for k in ("times", "dates", "clocks", "balance", "floor", "high", "room", "pnl")}
    locked_at = breach_at = None
    day_first: list[int] = []
    # the closest approach: the least room after any trade (the floor in force then) or after a session end (the new floor)
    closest: tuple[float, int] | None = None

    def put(tm: int, d: date, clock: str, pnl: float | None) -> None:
        if out["times"] and tm <= out["times"][-1]:
            tm = out["times"][-1] + 1
        out["times"].append(tm); out["dates"].append(d.isoformat()); out["clocks"].append(clock)
        out["balance"].append(round(bal, 2)); out["floor"].append(round(floor, 2)); out["high"].append(round(hw, 2))
        out["room"].append(round(bal - floor, 2)); out["pnl"].append(pnl)

    for d in days_all:
        shown = keep_from is not None and d >= keep_from
        if d == keep_from:
            put(_clock(d, by_day[d][0].entry_time), d, by_day[d][0].entry_time[:5], None)
        if shown:
            day_first.append(len(out["times"]) - (1 if d == keep_from else 0))
        for t in by_day[d]:
            bal += t.net_usd
            if shown:
                put(_clock(d, t.exit_time or t.entry_time), d, (t.exit_time or t.entry_time)[:5], round(t.net_usd, 2))
        if bal <= floor and breach_at is None and shown:
            breach_at = len(out["times"]) - 1
        hw = max(hw, bal)
        floor = floor_after(rule, hw)
        if shown:
            last_i = len(out["times"]) - 1
            for i in range(day_first[-1] if day_first else 0, last_i + 1):
                if out["pnl"][i] is not None and (closest is None or out["room"][i] < closest[0]):
                    closest = (out["room"][i], i)
            if closest is None or bal - floor < closest[0]:
                closest = (round(bal - floor, 2), last_i)
        if shown and locked_at is None and rule.lock_usd is not None and floor == rule.lock_usd:
            locked_at = len(out["times"]) - 1
    return {"account": account.get("name"), "rule": rule.text(), "rule_type": rule.type, "start_usd": rule.start_usd,
            "drawdown_usd": rule.drawdown_usd, **out, "day_first": day_first, "locked_at": locked_at,
            "breach_at": breach_at, "closest_at": closest[1] if closest else None,
            "closest_room": closest[0] if closest else None, "per_trade": True,
            # the floor in force now: set at the last session's close (the points carry the floor during each session)
            "floor_now": round(floor, 2), "high_now": round(hw, 2),
            "lock_usd": rule.lock_usd, "lock_trigger": rule.lock_trigger, "start_floor": rule.start_usd - rule.drawdown_usd}


def expected_band(backtest: list[Trade], n_trades: int, seed: int = 11) -> list[dict[str, float]]:
    """5/50/95% of the cumulative R after k = 0..n trades, from whole development days drawn at random."""
    by_day: dict[Any, list[float]] = {}
    for t in sorted(backtest, key=lambda t: (t.date, t.entry_time, t.id)):
        by_day.setdefault(t.date, []).append(t.r)
    blocks = list(by_day.values())
    if not blocks or n_trades == 0:
        return []
    rnd = random.Random(seed)
    cols: list[list[float]] = [[] for _ in range(n_trades + 1)]
    for _ in range(EXPECTED_PATHS):
        seq: list[float] = []
        while len(seq) < n_trades:
            seq.extend(blocks[rnd.randrange(len(blocks))])
        total = 0.0
        cols[0].append(0.0)
        for k in range(n_trades):
            total += seq[k]
            cols[k + 1].append(total)
    out = []
    for c in cols:
        c.sort()
        out.append({"p5": round(c[int(0.05 * (len(c) - 1))], 3), "p50": round(c[int(0.5 * (len(c) - 1))], 3),
                    "p95": round(c[int(0.95 * (len(c) - 1))], 3)})
    return out


def page(trades: list[Trade], guard: Guard, accounts: list[dict[str, Any]], *, account: str = "all",
         phase: str = "all", start: date | None = None, end: date | None = None) -> dict[str, Any]:
    sel = select(trades, guard, account=account, phase=phase, start=start, end=end)
    total = apply(sel, "total", guard)
    day = apply(sel, "day", guard)
    phases_present = sorted({phase_of(t, guard) for t in trades})
    # totals per phase (walk-forward / sealed as totals only once their gate is recorded)
    per_phase = {}
    for ph in phases_present:
        ts = [t for t in total.trades if phase_of(t, guard) == ph]
        if ts:
            per_phase[ph] = {"label": PHASE_LABEL.get(ph, ph), **stats(ts)}
    rules = {a["name"]: a for a in accounts if a.get("drawdown_type")}
    runs = []
    for name, acc in rules.items():
        if account not in ("all", name):
            continue
        mine = [t for t in day.trades if t.account == name and t.source == "backtest"]
        if mine:
            runs.append(account_run(mine, acc))
    live_like = [t for t in day.trades if t.source in ("demo", "prop", "live")]
    backtest_dev = [t for t in day.trades if t.source == "backtest"]
    live_r, total_r = [0.0], 0.0
    for t in sorted(live_like, key=lambda t: (t.date, t.entry_time, t.id)):
        total_r += t.r
        live_r.append(round(total_r, 3))
    return {
        "selection": {"account": account, "phase": phase, "start": start.isoformat() if start else None,
                      "end": end.isoformat() if end else None},
        "kpis": stats(total.trades),
        "per_phase": per_phase,
        "phases_present": phases_present,
        # what is held back and why: the total-level reason (a missing gate) wins over the day-level one
        "notices": _notices({**day.held, **total.held}),
        "totals_held": _notices(total.held),
        "equity": equity(day.trades, guard, per_trade=phase in ("demo", "prop", "live")),
        "monthly": _monthly(day.trades),
        "kpi_trend": kpi_trend(day.trades),
        "drawdown": runs,
        "live_vs_expected": {"live_r": live_r, "band": expected_band(backtest_dev, len(live_r) - 1),
                             "trades": len(live_r) - 1},
    }
