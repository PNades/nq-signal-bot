"""Databento dataset condition: record it for every pulled day, flag non-available days, measure their damage
(SPEC §3.2 Dataset condition, §13 Q8-degraded; owner 2026-10-09).

- `record_conditions`: the free `metadata.get_dataset_condition` for a span of UTC dates, merged into
  `raw/databento/condition/conditions.json` (latest answer per date plus every change). Every Databento pull calls it
  for its own days (step-8 backfill, `step8b recorded-day`, and later the top-ups and the evening archive) and lists
  any date that isn't `available` in its report.
- An upgrade (a date stored while not available that Databento now calls available) is written to `upgrades.jsonl`
  and flagged; it is never re-pulled by itself (CLAUDE.md rule 2 exception, owner 2026-10-09, §13 Q8-degraded-2:
  once per day, only after the owner's OK for that day, cost printed first, old files kept in `repull/degraded/`).
- `measure_damage`: for each non-available UTC date, the ET sessions it touches (overnight/ETH and RTH parts of the
  CME days it overlaps) and, from the stored files only, NQ and ES `trades` and NQ `mbp-1` completeness: minutes with
  no record, the longest gap and gaps over a minute, next to the same part of the nearest normal sessions.
  Completeness only, never prices, levels or outcomes, for every month (§7 rule 2b: months 7–12 and top-ups may
  appear in no other way before step 20).
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

import numpy as np

from nqbot import calendar as cal_mod
from nqbot.clients.databento_client import MBP1, TRADES, DatabentoClient
from nqbot.clients.databento_contracts import cme_day_span
from nqbot.clients.databento_cost import l1_included_from
from nqbot.clients.databento_store import DatabentoStore
from nqbot.storage import append_jsonl, atomic_write_json, read_jsonl

ET = ZoneInfo("America/New_York")
AVAILABLE = "available"
RTH_OPEN, RTH_CLOSE = time(9, 30), time(16, 0)  # §1.2
MINUTE_NS = 60 * 1_000_000_000
GAP_REPORT_SEC = 60  # gaps reported individually from one minute (a reporting cut, not a trading parameter)
MBP1_CHUNK = 1_000_000  # records per read of an mbp-1 file (low memory, §11)
MBP1_RES_NS = 100_000_000  # mbp-1 timestamps kept at 0.1 s resolution (a completeness measure, not a feature)


def condition_dir(store: DatabentoStore):
    return store.root / "condition"


def load_conditions(store: DatabentoStore) -> dict[str, dict[str, Any]]:
    path = condition_dir(store) / "conditions.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def upgrades(store: DatabentoStore) -> list[dict[str, Any]]:
    return read_jsonl(condition_dir(store) / "upgrades.jsonl")


def stored_on(store: DatabentoStore, utc_day: date) -> bool:
    """Any raw Databento file stored for a CME day the UTC date overlaps."""
    names = {f"{d.isoformat()}.dbn.zst" for d in (utc_day, utc_day + timedelta(days=1))}
    return store.root.exists() and any(p.name in names for p in store.root.glob("*/*/*.dbn.zst"))


def record_conditions(client: DatabentoClient, store: DatabentoStore, start: date, end: date, *, today: date,
                      source: str, echo: Callable[[str], None] = print,
                      stored: Callable[[date], bool] | None = None) -> dict[str, Any]:
    """Fetch and merge the condition of UTC dates [start, end]; return {rows, flagged, upgrades} for the caller's
    report. Never pulls data."""
    rows = client.dataset_condition(start, end)
    now = datetime.now(ET).isoformat()
    known = load_conditions(store)
    stored = stored or (lambda d: stored_on(store, d))
    new_upgrades = []
    for r in rows:
        day, cond, mod = r["date"][:10], r["condition"], r.get("last_modified_date")
        prev = known.get(day)
        entry = prev or {"first_seen": now, "history": []}
        if prev is None or prev["condition"] != cond or prev.get("last_modified_date") != mod:
            entry["history"].append({"at": now, "condition": cond, "last_modified_date": mod, "source": source})
        if prev is not None and prev["condition"] != AVAILABLE and cond == AVAILABLE and stored(date.fromisoformat(day)):
            included = date.fromisoformat(day) >= l1_included_from(today)
            up = {"date": day, "at": now, "was": prev["condition"], "last_modified_date": mod, "source": source,
                  "included": included, "status": "ask_owner"}
            append_jsonl(condition_dir(store) / "upgrades.jsonl", up)
            new_upgrades.append(up)
            echo(f"databento {day}: upgraded {prev['condition']} -> available; stored copy kept, NOT re-pulled "
                 f"({'inside' if included else 'outside'} the included window): ask the owner (§3.2, §13 Q8-degraded-2)")
        entry.update(condition=cond, last_modified_date=mod, last_checked=now)
        known[day] = entry
    atomic_write_json(condition_dir(store) / "conditions.json", dict(sorted(known.items())))
    flagged = [r for r in rows if r["condition"] != AVAILABLE]
    for r in flagged:
        echo(f"databento {r['date'][:10]}: condition {r['condition']} (last modified {r.get('last_modified_date')})")
    return {"start": start.isoformat(), "end": end.isoformat(), "rows": rows, "flagged": flagged,
            "upgrades": new_upgrades, "source": source, "checked": now}


def report_lines(result: dict[str, Any]) -> list[str]:
    """The Markdown section every Databento pull report carries."""
    lines = [f"## Dataset condition ({result['start']} … {result['end']} UTC, checked {result['checked'][:16]})", ""]
    if not result["flagged"]:
        lines.append(f"All {len(result['rows'])} dates `available`.")
    else:
        lines.append(f"**{len(result['flagged'])} of {len(result['rows'])} dates not available** (§13 Q8-degraded):")
        lines += [f"- {r['date'][:10]} ({date.fromisoformat(r['date'][:10]):%a}): `{r['condition']}`, last modified "
                  f"{r.get('last_modified_date')}" for r in result["flagged"]]
    for u in result["upgrades"]:
        lines.append(f"- **upgraded to available: {u['date']}** (was {u['was']}): not re-pulled; owner decides")
    return lines + [""]


# --- damage -----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Part:
    cme_day: date
    kind: str  # RTH | ETH
    start: datetime  # ET, inside the UTC date
    end: datetime


def rth_span(day: date, cal: dict[str, Any] | None) -> tuple[datetime, datetime]:
    close = cal_mod.early_close_et(day, cal) or RTH_CLOSE
    return datetime.combine(day, RTH_OPEN, tzinfo=ET), datetime.combine(day, close, tzinfo=ET)


def parts_touched(utc_day: date, cal: dict[str, Any] | None = None) -> list[Part]:
    """The ETH and RTH parts of the CME days (weekdays) a UTC date overlaps, clipped to it."""
    u0 = datetime.combine(utc_day, time(0), tzinfo=timezone.utc).astimezone(ET)
    u1 = u0 + timedelta(days=1)
    out = []
    for d in (utc_day, utc_day + timedelta(days=1)):
        if d.weekday() >= 5:
            continue
        c0, c1 = cme_day_span(d)
        spans = []
        if cal_mod.is_trading_day(d, cal):
            r0, r1 = rth_span(d, cal)
            # on an early-close day Globex closes with RTH (§1.2): nothing trades after it, nothing to measure
            spans = [("ETH", c0, r0), ("RTH", r0, r1)] + ([] if cal_mod.early_close_et(d, cal) else [("ETH", r1, c1)])
        else:
            spans = [("ETH", c0, c1)]  # a holiday Globex day: no session (§1.2)
        for kind, a, b in spans:
            a, b = max(a, u0), min(b, u1)
            if a < b:
                out.append(Part(d, kind, a, b))
    return out


def timestamps(path, schema: str) -> np.ndarray:
    """Sorted ts_event (ns) of a stored file; mbp-1 read in chunks."""
    from databento import DBNStore

    st = DBNStore.from_file(path)
    if schema == MBP1:  # low memory (§11): only distinct MBP1_RES_NS steps are kept (gaps measured to 0.1 s)
        parts = [np.unique(np.asarray(a["ts_event"], dtype=np.int64) // MBP1_RES_NS) * MBP1_RES_NS
                 for a in st.to_ndarray(count=MBP1_CHUNK)]
        ts = np.unique(np.concatenate(parts)) if parts else np.zeros(0, dtype=np.int64)
    else:
        ts = np.asarray(st.to_ndarray().reshape(-1)["ts_event"], dtype=np.int64)
    return np.sort(ts)


def _ns(t: datetime) -> int:
    return int(t.timestamp()) * 1_000_000_000


def completeness(ts: np.ndarray, a: datetime, b: datetime) -> dict[str, Any]:
    """Minutes with no record, longest gap (s, edges included) and gaps over GAP_REPORT_SEC in [a, b)."""
    a_ns, b_ns = _ns(a), _ns(b)
    inside = ts[(ts >= a_ns) & (ts < b_ns)]
    total = (b_ns - a_ns + MINUTE_NS - 1) // MINUTE_NS
    filled = len(np.unique((inside - a_ns) // MINUTE_NS))
    edges = np.concatenate(([a_ns], inside, [b_ns]))
    gaps = np.diff(edges) / 1e9
    big = np.nonzero(gaps > GAP_REPORT_SEC)[0]
    return {"minutes": int(total), "empty_minutes": int(total - filled), "records": int(len(inside)),
            "longest_gap_sec": round(float(gaps.max()), 1) if len(gaps) else None,
            "gaps": [{"from": datetime.fromtimestamp(edges[i] / 1e9, ET).strftime("%H:%M:%S"),
                      "sec": round(float(gaps[i]), 1)} for i in big]}


SERIES = (("NQ", TRADES), ("ES", TRADES), ("NQ", MBP1))


def _symbol(plan: dict[str, Any], root: str, day: date) -> str | None:
    return (plan["front"].get(root) or {}).get(day.isoformat())


def measure_damage(store: DatabentoStore, plan: dict[str, Any], dates: Iterable[date], conditions: dict[str, dict],
                   *, baseline_sessions: int, cal: dict[str, Any] | None = None,
                   month_of: Callable[[date], int | None] = lambda d: None,
                   echo: Callable[[str], None] = print) -> list[dict[str, Any]]:
    """One row per (UTC date, part, series): completeness on the day and on the nearest normal sessions."""
    bad = {d for d, c in conditions.items() if c["condition"] != AVAILABLE}
    sessions = sorted({date.fromisoformat(i["day"]) for i in plan["items"]})
    normal = [d for d in sessions if cal_mod.is_trading_day(d, cal)
              and (d - timedelta(days=1)).isoformat() not in bad and d.isoformat() not in bad]
    cache: dict[tuple[str, str, date], np.ndarray | None] = {}

    def ts_of(root: str, schema: str, day: date) -> np.ndarray | None:
        key = (root, schema, day)
        if key not in cache:
            sym = _symbol(plan, root, day)
            path = store.raw_path(schema, sym, day) if sym else None
            cache[key] = timestamps(path, schema) if path is not None and path.exists() else None
        return cache[key]

    rows = []
    for u in sorted(dates):
        parts = parts_touched(u, cal)
        if not parts:
            rows.append({"utc_date": u.isoformat(), "part": None, "note": "touches no CME session (weekend)"})
            continue
        for p in parts:
            c0 = cme_day_span(p.cme_day)[0]
            for root, schema in SERIES:
                if schema == MBP1 and p.kind != "RTH":
                    continue  # mbp-1 is stored for 09:25–16:05 only (§3.2)
                base = {"utc_date": u.isoformat(), "cme_day": p.cme_day.isoformat(), "month": month_of(p.cme_day),
                        "part": p.kind, "span": f"{p.start:%m-%d %H:%M}–{p.end:%m-%d %H:%M} ET",
                        "series": f"{root} {schema}"}
                ts = ts_of(root, schema, p.cme_day)
                if ts is None:
                    rows.append({**base, "note": "no stored file"})
                    continue
                day = completeness(ts, p.start, p.end)
                ref = []
                for n in sorted(normal, key=lambda d: (abs((d - p.cme_day).days), d)):
                    if len(ref) >= baseline_sessions:
                        break
                    if n == p.cme_day:
                        continue
                    t = ts_of(root, schema, n)
                    if t is None:
                        continue
                    if p.kind == "RTH":
                        a, b = rth_span(n, cal)
                        if (b - a) != (rth_span(p.cme_day, cal)[1] - rth_span(p.cme_day, cal)[0]):
                            continue  # compare full sessions with full sessions
                    else:
                        n0 = cme_day_span(n)[0]
                        a, b = n0 + (p.start - c0), n0 + (p.end - c0)
                    ref.append(completeness(t, a, b))
                empties = [r["empty_minutes"] for r in ref]
                longest = [r["longest_gap_sec"] for r in ref if r["longest_gap_sec"] is not None]
                rows.append({**base, **day, "baseline_n": len(ref),
                             "baseline_empty_median": statistics.median(empties) if empties else None,
                             "baseline_empty_max": max(empties) if empties else None,
                             "baseline_gap_max_sec": max(longest) if longest else None})
                echo(f"  {u} {p.kind} {p.cme_day} {root} {schema}: {day['empty_minutes']}/{day['minutes']} empty "
                     f"minutes, longest gap {day['longest_gap_sec']} s (normal: max {rows[-1]['baseline_empty_max']} "
                     f"empty, gap {rows[-1]['baseline_gap_max_sec']} s, n={len(ref)})")
    return rows


def damage_lines(rows: list[dict[str, Any]]) -> list[str]:
    lines = ["## Damage on the affected sessions (stored files only; completeness only, no prices, §7 rule 2b)", "",
             "Empty = minutes with no record in the part; normal = the same part on the nearest sessions whose UTC "
             "dates are all available (RTH: full sessions of the same length; ETH: the same offset from 18:00 ET).", "",
             "| UTC date | CME day | month | part | span | series | empty min | normal median / max | longest gap s "
             "| normal max gap s | gaps > 60 s |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        month = "" if r.get("part") is None else r["month"] if r.get("month") is not None else "warm-up"
        if r.get("part") is None or "note" in r:
            lines.append(f"| {r['utc_date']} | {r.get('cme_day', '')} | {month} | {r.get('part') or ''} | "
                         f"{r.get('span', '')} | {r.get('series', '')} | {r['note']} | | | | |")
            continue
        gaps = "; ".join(f"{g['from']} {g['sec']:.0f}s" for g in r["gaps"][:6]) + (" …" if len(r["gaps"]) > 6 else "")
        lines.append(f"| {r['utc_date']} | {r['cme_day']} | {month} | {r['part']} | {r['span']} | {r['series']} | "
                     f"{r['empty_minutes']}/{r['minutes']} | {r['baseline_empty_median']} / {r['baseline_empty_max']} | "
                     f"{r['longest_gap_sec']} | {r['baseline_gap_max_sec']} | {gaps or '—'} |")
    return lines + [""]
