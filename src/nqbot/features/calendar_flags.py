"""Calendar flags and session clocks (step 11 part 2; SPEC §1.2, §5.2 Flags, §9 News blackouts; feature_spec F2.22).

From `config/calendar.yaml` as known at `preopen_start` (the `events` list, the same one the backtest's blackouts use;
`tests/test_calendar_flags.py` checks that every rescheduled release was announced before its scheduled day, so the list
equals the calendar known at that pre-open). Hedge nodes are dropped (F2.22: not defined in `docs/skylit/`).
- `opex_week`: the session's week (Mon–Fri) holds a monthly equity option expiration (calendar `opex`, already moved to
  the trading day before a holiday); `days_to_monthly_expiry`: trading days from T to the next one (0 on it);
- `quarter_end_day`: T is the last trading day of a quarter (`quarter_ends`); `days_to_quarter_end` likewise;
- `cpi_day`, `fomc_day` (the rate decision), `nfp_day`, `ppi_day`, `retail_sales_day`: the release is listed on T;
- `half_day`: T has an `early_close_et`;
- `minutes_to_close`, `minutes_since_open`, `minutes_to_horizon_end` (from t to the earlier of the session flat-by,
  min(`session.flat_by`, early close − 1 min), and the next blackout-window start; 0 inside a window), and
  `in_session_release_today` (a `blackout: true` window overlaps [09:30, entry_end), entry_end = flat-by −
  `min_minutes_before_flat`).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from nqbot import calendar as cal_mod
from nqbot.features.session_tape import ET, MIN_NS, NS, et_ns

RELEASE_FLAGS = {"cpi_day": "CPI", "fomc_day": "FOMC Rate Decision", "nfp_day": "NFP", "ppi_day": "PPI",
                 "retail_sales_day": "Retail Sales"}
RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)


def _trading_days_between(a: date, b: date, cal: dict) -> int:
    """Trading days d with a < d <= b."""
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if cal_mod.is_trading_day(d, cal):
            n += 1
    return n


def _last_trading_on_or_before(d: date, cal: dict) -> date:
    while not cal_mod.is_trading_day(d, cal):
        d -= timedelta(days=1)
    return d


def flat_by(day: date, cal: dict, settings: dict) -> datetime:
    h, m = (int(x) for x in str(settings["session"]["flat_by"]).split(":"))
    fb = datetime.combine(day, time(h, m), tzinfo=ET)
    ec = cal_mod.early_close_et(day, cal)
    if ec is not None:
        fb = min(fb, datetime.combine(day, ec, tzinfo=ET) - timedelta(minutes=1))
    return fb


def entry_end(day: date, cal: dict, settings: dict) -> datetime:
    return flat_by(day, cal, settings) - timedelta(minutes=int(settings["min_minutes_before_flat"]))


def blackout_spans(day: date, cal: dict, settings: dict) -> list[tuple[datetime, datetime]]:
    return cal_mod.blackout_windows(cal, day, before_min=settings["blackout_before_min"],
                                    after_min=settings["blackout_after_min"], lead_sec=settings["news_flatten_lead_sec"])


def day_flags(day: date, cal: dict, settings: dict) -> dict[str, Any]:
    """The flags known at the session start."""
    out: dict[str, Any] = {}
    opex = sorted(cal.get("opex") or [])
    nxt = next((o for o in opex if o >= day), None)
    week0 = day - timedelta(days=day.weekday())
    out["opex_week"] = any(week0 <= o <= week0 + timedelta(days=4) for o in opex)
    out["days_to_monthly_expiry"] = None if nxt is None else _trading_days_between(day, nxt, cal)
    qe = sorted(_last_trading_on_or_before(q, cal) for q in (cal.get("quarter_ends") or []))
    nq = next((q for q in qe if q >= day), None)
    out["quarter_end_day"] = day in qe
    out["days_to_quarter_end"] = None if nq is None else _trading_days_between(day, nq, cal)
    names = {r.name for r in cal_mod.releases(cal) if r.day == day}
    for flag, name in RELEASE_FLAGS.items():
        out[flag] = name in names
    out["half_day"] = cal_mod.early_close_et(day, cal) is not None
    o = datetime.combine(day, RTH_OPEN, tzinfo=ET)
    end = entry_end(day, cal, settings)
    out["in_session_release_today"] = any(a < end and b > o for a, b in blackout_spans(day, cal, settings))
    return out


def clocks_at(t_ns: int, day: date, cal: dict, settings: dict) -> dict[str, float]:
    ec = cal_mod.early_close_et(day, cal)
    close_ns = et_ns(day, ec or RTH_CLOSE)
    out = {"minutes_to_close": (close_ns - t_ns) / MIN_NS, "minutes_since_open": (t_ns - et_ns(day, RTH_OPEN)) / MIN_NS}
    fb = int(flat_by(day, cal, settings).timestamp()) * NS
    spans = [(int(a.timestamp()) * NS, int(b.timestamp()) * NS) for a, b in blackout_spans(day, cal, settings)]
    if any(a <= t_ns < b for a, b in spans):
        out["minutes_to_horizon_end"] = 0.0
    else:
        ends = [fb] + [a for a, _ in spans if a > t_ns]
        out["minutes_to_horizon_end"] = max(0.0, (min(ends) - t_ns) / MIN_NS)
    return out
