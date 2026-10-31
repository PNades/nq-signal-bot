"""The §3.2 Databento damage rule applied to the stored completeness rows (owner 2026-10-09, SPEC §3.2, §13 Q8-degraded).

Input: the rows of `clients/databento_condition.measure_damage` (one per UTC date, part, series; kept in
`<reports_dir>/step8/dataset_condition.json` under `damage`). Completeness only, no prices: fixed before any outcome.
- A part (ETH or RTH of a CME day) is damaged in a series if its empty minutes exceed the normal maximum by more than
  `databento_damage_excess_minutes`, or a gap is longer than both the normal maximum gap and `databento_damage_gap_sec`.
- Its damaged minutes are the minutes inside such gaps plus its excess empty minutes. Over the series they are a
  union: the gap minutes (each gap has a start and length, so the same minute in two series counts once) plus the
  largest excess-empty count of any series (excess empty minutes carry no minute identity; their largest count is the
  smallest union consistent with every series).
- RTH above `databento_damage_session_minutes` damaged minutes: the whole session is dropped (and the levels it hands
  to the next session are empty there); at or below, only events near a damaged minute are dropped (step 13).
- ETH above that many damaged minutes (all ETH parts of the CME day together): the overnight-derived levels (ON
  high/low) of that session are empty.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
_SPAN = re.compile(r"(\d\d)-(\d\d) (\d\d):(\d\d)")


@dataclass
class DayDamage:
    cme_day: date
    rth_minutes: int = 0
    eth_minutes: int = 0
    rth_gap_minutes: list = field(default_factory=list)   # (start ET datetime, end) of RTH gaps, for step 13's padding
    rth_dropped: bool = False
    eth_empty: bool = False


def _span_start(row: dict[str, Any]) -> datetime:
    """The part's start (ET) from its 'MM-DD HH:MM–MM-DD HH:MM ET' span; the year from the CME day."""
    d = date.fromisoformat(row["cme_day"])
    mo, dd, hh, mi = (int(x) for x in _SPAN.search(row["span"]).groups())
    year = d.year - 1 if (mo, d.month) == (12, 1) else d.year
    return datetime(year, mo, dd, hh, mi, tzinfo=ET)


def _gap_start(part_start: datetime, hhmmss: str) -> datetime:
    h, m, s = (int(x) for x in hhmmss.split(":"))
    t = part_start.replace(hour=h, minute=m, second=s)
    return t + timedelta(days=1) if t < part_start else t


def classify(rows: Iterable[dict[str, Any]], settings: dict[str, Any]) -> dict[date, DayDamage]:
    excess_max = settings["databento_damage_excess_minutes"]
    gap_min_sec = settings["databento_damage_gap_sec"]
    session_max = settings["databento_damage_session_minutes"]
    parts: dict[tuple[date, str, str], dict] = {}   # (day, part, span) -> {'gap': set of minute starts, 'excess': max}
    for r in rows:
        if r.get("part") is None or "empty_minutes" not in r or r.get("baseline_empty_max") is None:
            continue
        key = (date.fromisoformat(r["cme_day"]), r["part"], r["span"])
        acc = parts.setdefault(key, {"gap": set(), "excess": 0, "intervals": []})
        excess = r["empty_minutes"] - r["baseline_empty_max"]
        normal_gap = r.get("baseline_gap_max_sec") or 0.0
        start = _span_start(r)
        long_gaps = [g for g in r.get("gaps", []) if g["sec"] > max(normal_gap, gap_min_sec)]
        if excess > excess_max or long_gaps:
            acc["excess"] = max(acc["excess"], max(excess, 0))
            for g in long_gaps:
                g0 = _gap_start(start, g["from"])
                g1 = g0 + timedelta(seconds=g["sec"])
                acc["intervals"].append((g0, g1))
                m0 = math.floor((g0 - start).total_seconds() / 60)
                m1 = math.ceil((g1 - start).total_seconds() / 60)
                acc["gap"].update(range(m0, m1))
    out: dict[date, DayDamage] = {}
    for (day, part, _span), acc in parts.items():
        dd = out.setdefault(day, DayDamage(day))
        n = len(acc["gap"]) + acc["excess"]
        if part == "RTH":
            dd.rth_minutes += n
            dd.rth_gap_minutes += acc["intervals"]
        else:
            dd.eth_minutes += n
    for dd in out.values():
        dd.rth_dropped = dd.rth_minutes > session_max
        dd.eth_empty = dd.eth_minutes > session_max
    return out


def load(reports_dir, settings: dict[str, Any]) -> dict[date, DayDamage]:
    """The classification from the stored step-8 condition file (empty if it isn't there)."""
    import json

    p = reports_dir / "step8" / "dataset_condition.json"
    if not p.exists():
        return {}
    return classify(json.loads(p.read_text(encoding="utf-8")).get("damage", []), settings)
