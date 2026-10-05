"""config/calendar.yaml: news releases, CME equity-index holidays and early closes (SPEC §1.2, §9).

`events` is the official list (owner's 13 releases with blackout: true, plus their extra lines with
blackout: false) and is used by backtest, labels and live alike. `live_extra` holds other US events
ForexFactory rated High at the 08:30 pre-open: live only, never read by the backtest or labels (§9).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from nqbot.config import CONFIG_DIR, load_settings, load_yaml

ET = ZoneInfo("America/New_York")
SECTIONS = ("meta", "sources", "holidays", "early_closes", "events", "live_extra", "schedule_changes",
            "opex", "quarter_ends", "owner_events")


class CalendarError(Exception):
    pass


@dataclass(frozen=True)
class Release:
    day: date
    time_et: time
    name: str
    blackout: bool

    @property
    def at(self) -> datetime:
        return datetime.combine(self.day, self.time_et, tzinfo=ET)


def _time(text: Any, where: str) -> time:
    try:
        return time.fromisoformat(str(text))
    except ValueError:
        raise CalendarError(f"{where}: bad time {text!r} (want HH:MM)") from None


def _date(value: Any, where: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    raise CalendarError(f"{where}: bad date {value!r} (want an unquoted YYYY-MM-DD)")


def validate(cal: dict[str, Any], title_map: dict[str, dict[str, list[str]]]) -> None:
    """Every entry dated, timed, named from news_title_map and sourced; raises CalendarError."""
    missing = [s for s in SECTIONS if s not in cal]
    if missing:
        raise CalendarError(f"calendar.yaml lacks sections {missing}")
    sources = cal["sources"] or {}
    blackout_names = set(title_map.get("blackout", {}))
    other_names = set(title_map.get("no_blackout", {}))
    errors: list[str] = []

    def check(section: str, i: int, entry: dict, need_source: bool = True) -> None:
        where = f"{section}[{i}]"
        try:
            _date(entry.get("date"), where)
        except CalendarError as e:
            errors.append(str(e))
        if need_source and entry.get("source") not in sources:
            errors.append(f"{where}: source {entry.get('source')!r} not in sources")

    for i, e in enumerate(cal["events"] or []):
        check("events", i, e)
        try:
            _time(e.get("time_et"), f"events[{i}]")
        except CalendarError as err:
            errors.append(str(err))
        names = blackout_names if e.get("blackout") is True else other_names
        if e.get("blackout") not in (True, False):
            errors.append(f"events[{i}]: blackout must be true or false")
        elif e.get("name") not in names:
            errors.append(f"events[{i}]: name {e.get('name')!r} not in news_title_map "
                          f"{'blackout' if e.get('blackout') else 'no_blackout'}")
    for i, h in enumerate(cal["holidays"] or []):
        check("holidays", i, h)
    for i, ec in enumerate(cal["early_closes"] or []):
        check("early_closes", i, ec)
        try:
            _time(ec.get("early_close_et"), f"early_closes[{i}]")
        except CalendarError as err:
            errors.append(str(err))
    for i, x in enumerate(cal["live_extra"] or []):
        where = f"live_extra[{i}]"
        try:
            _date(x.get("date"), where)
            _time(x.get("time_et"), where)
        except CalendarError as err:
            errors.append(str(err))
        if not x.get("ff_title") or not x.get("seen_at"):
            errors.append(f"{where}: needs ff_title and seen_at")
    for section in ("opex", "quarter_ends"):
        for i, d in enumerate(cal[section] or []):
            try:
                _date(d, f"{section}[{i}]")
            except CalendarError as err:
                errors.append(str(err))
    for i, o in enumerate(cal["owner_events"] or []):
        try:
            _date(o.get("date") if isinstance(o, dict) else None, f"owner_events[{i}]")
        except CalendarError as err:
            errors.append(str(err))
    for i, c in enumerate(cal["schedule_changes"] or []):
        check("schedule_changes", i, {"date": c.get("scheduled"), "source": c.get("source")})
        try:
            _date(c.get("announced"), f"schedule_changes[{i}].announced")
        except CalendarError as err:
            errors.append(str(err))
    if errors:
        raise CalendarError("calendar.yaml:\n  " + "\n  ".join(errors))


@lru_cache(maxsize=4)
def _load_validated(path: Path) -> dict[str, Any]:
    cal = load_yaml(path)
    validate(cal, load_settings()["news_title_map"])
    return cal


def load(path: Path = CONFIG_DIR / "calendar.yaml") -> dict[str, Any]:
    """The validated calendar (a fresh copy each call, so callers can't change the cached one)."""
    return copy.deepcopy(_load_validated(path))


def releases(cal: dict[str, Any], *, live: bool = False) -> list[Release]:
    """Official releases; with live=True also the live-only red-folder extras (never in backtest)."""
    out = [Release(e["date"], _time(e["time_et"], "events"), e["name"], e["blackout"])
           for e in cal["events"] or []]
    if live:
        out += [Release(e["date"], _time(e["time_et"], "live_extra"), e["ff_title"], True)
                for e in cal["live_extra"] or []]
    return sorted(out, key=lambda r: r.at)


def blackout_windows(cal: dict[str, Any], day: date, *, before_min: int, after_min: int, lead_sec: int,
                     live: bool = False) -> list[tuple[datetime, datetime]]:
    """[release - before_min - lead_sec, release + after_min) per blackout release on `day`, merged (§9)."""
    spans = sorted(
        (r.at - timedelta(minutes=before_min, seconds=lead_sec), r.at + timedelta(minutes=after_min))
        for r in releases(cal, live=live) if r.blackout and r.day == day
    )
    merged: list[tuple[datetime, datetime]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def holidays(cal: dict[str, Any] | None = None) -> set[date]:
    cal = cal if cal is not None else _load_validated(CONFIG_DIR / "calendar.yaml")  # read-only use
    return {h["date"] for h in cal["holidays"] or []}


def early_close_et(day: date, cal: dict[str, Any] | None = None) -> time | None:
    cal = cal if cal is not None else _load_validated(CONFIG_DIR / "calendar.yaml")  # read-only use
    for ec in cal["early_closes"] or []:
        if ec["date"] == day:
            return _time(ec["early_close_et"], "early_closes")
    return None


def is_trading_day(day: date, cal: dict[str, Any] | None = None) -> bool:
    return day.weekday() < 5 and day not in holidays(cal)
