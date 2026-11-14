"""Step 12 visual check on stored data (read-only, $0, no lock; SPEC §12 step 12, §7 rule 2b: development months only).

Commands (`nqbot step12 ...`), every output under `<reports_dir>/step12/`:
- `scan`: which of the five sample days has a 10:30 redraw / an orientation flip (§5.8); if none has one, the earliest
  development day on which the step-11 code draws it (the calendar and the fib rule only, never outcomes).
- `day D`: the day rebuilt exactly as the feature build did (the job's stored state of the previous session, then
  `session_build.build_day`): the reading table for Deepcharts (`levels_<D>.csv`, an empty Deepcharts column), plots
  (PNG and an interactive HTML) with every level, zone, the golden zone's drawings, the Skylit nodes (display only)
  and every event marked with its structure label, and the day report with the event table.
- `lvn D`: every daily and weekly LVN span of the day: first and last qualifying instant, when it stopped, lifetime,
  price (the step-11 smoke run's flicker finding; owner 2026-10-31).
- `nodetype`: the node-type agreement report on the stored step-7 instants as confusion tables (F2.2, F2.5).
- `single-prints`: the single-print count report over months 1-6 with `sp_min_ticks` at 4, 6 and 8 (owner 2026-11-02;
  counts only, no outcomes; the configured value is not changed).
- `compare`: the owner's filled-in Deepcharts values against the bot's, by the §12 step 12 tolerances.
- `all`: scan, each day in its own process, the LVN lists, nodetype, single-prints, and `summary.md`.
Kept out of the feature build's code digest (`feature_build.CODE_PARTS`), so the stored build stays usable.
"""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import numpy as np

from nqbot.checks.step8b import Env
from nqbot.features import golden_zone as gz
from nqbot.features import profile as pf
from nqbot.features import session_tape as st
from nqbot.features.session_tape import ET, MIN_NS, NS
from nqbot.months import DEVELOPMENT, days_of, month_index, require_development
from nqbot.storage import atomic_write_bytes, atomic_write_text

TICK = 0.25
# §12 step 12: the five sample days (owner 2026-10-30, §13 Q-frontroll: 03-17 = the first NQM6 session)
SAMPLE_DAYS = (date(2026, 3, 6), date(2026, 3, 11), date(2026, 3, 17), date(2026, 3, 24), date(2026, 3, 26))
# §12 step 12 LVN lifetime: 2-3 of the five days, picked by the calendar (a late-week weekly, the roll-week restart of
# the weekly profile, a late-week day of a later week)
LVN_DAYS = (date(2026, 3, 6), date(2026, 3, 17), date(2026, 3, 26))
# §12 step 12 readings (the fewest clock times covering every level and the redraw / flip)
READ_FIB = time(9, 46)
READ_ALL = time(10, 1)
READ_REDRAW = time(10, 31)
# §12 step 12 tolerances (owner 2026-10-23), in ticks; "exact" = within the 0.25 rounding
# and single prints within one TPO bin (owner 2026-11-02: bin-edge conventions differ by one bin; set from
# `tpo_tick_pts` by `level_rows`, 4 ticks at 1 pt)
# and the daily / weekly / previous-session POC within ±2 pts (owner 2026-11-07: price chops around the POC rather
# than reacting at an exact price) and VAH / VAL within ±4 ticks (owner 2026-11-07: one Deepcharts row at Manual
# 4 ticks; its value-area edges sit on 1-pt row boundaries, the bot's on single ticks, so up to 3 ticks is rounding)
TOL_TICKS = {"exact": 0, "fib_ratio": 1, "vwap": 1, "vwap_band": 2, "va": 4, "poc": 8, "lvn": 4}
FIB_RATIOS = (0.5, 0.618, 0.79)
LIFETIME_BUCKETS = ((1, 2), (3, 10), (11, 30), (31, 120), (121, None))   # minutes, the step-12 LVN histogram
SP_VARIANTS = (4, 6, 8)   # owner 2026-11-02: the single-print report's sp_min_ticks values (config stays as is)
PLOT_STEP_NS = MIN_NS     # the plots' sampling grid
NODE_GROUPS = frozenset({"qqq_node", "ndxp_node", "qqq_king", "ndxp_king", "zone"})   # the PNGs' second panel
NO_LABEL = frozenset({"retested", "zone"})   # too many to label one by one (the legend names them)
NODE_SAMPLE_MIN = 5       # node types for the King track, sampled every 5 minutes (display only)
OPEN_ZOOM = (time(9, 30), time(10, 45))
CSV_COLS = ("day", "reading_et", "group", "level", "level_id", "kind", "bot_value", "bot_lo", "bot_hi",
            "tolerance_ticks", "deepcharts_value", "diff_ticks", "within_tol", "note")
NOT_PRESENT = "none"      # what the owner writes when Deepcharts has no such level (an LVN the bot has)


class Refused(Exception):
    """The check can't run (a day outside the development months, nothing stored, a build that doesn't match)."""


def out_dir(env: Env) -> Path:
    return env.paths.reports_dir / "step12"


def day_dir(env: Env, day: date) -> Path:
    return out_dir(env) / day.isoformat()


def et(t_ns: int | None, fmt: str = "%H:%M:%S") -> str:
    if t_ns is None:
        return "—"
    return datetime.fromtimestamp(int(t_ns) / NS, ET).strftime(fmt)


def _num(x: Any) -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return ""
    if isinstance(x, float):
        return f"{x:.4f}".rstrip("0").rstrip(".")
    return str(x)


# --- readings ------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Reading:
    at: time
    scope: str        # all / fib / ib_fib
    why: str


def reading_plan(redrawn: bool, flips_at: list[int], added_for: str | None = None) -> list[Reading]:
    """§12 step 12: 09:46 (fib) and 10:01 (every level) on a sample day; 10:31 (IB and fib) on a redraw day; the
    first minute after the first flip candle settles (fib) on a flip day. An added redraw / flip day is read for the
    IB and the fib only (09:46, 10:01, and 10:31 or the flip minute)."""
    out = [Reading(READ_FIB, "fib", "fib at its 09:45 start"),
           Reading(READ_ALL, "ib_fib" if added_for else "all", "every level" if not added_for else "IB and fib")]
    if redrawn and added_for in (None, "redraw"):
        out.append(Reading(READ_REDRAW, "ib_fib", "IB and fib after the 10:30 redraw"))
    if flips_at and added_for in (None, "flip"):
        tf = datetime.fromtimestamp(flips_at[0] / NS, ET).time()
        if tf not in {r.at for r in out}:
            out.append(Reading(tf, "fib", "fib after the first flip candle settled"))
    return sorted(out, key=lambda r: r.at)


def sp_tol_ticks(features: dict) -> float:
    """Single prints: within one TPO bin (owner 2026-11-02)."""
    return float(features["tpo_tick_pts"]) / TICK


def _row(day: date, at: time, group: str, level: str, level_id: str, value: Any, tol: str | float,
         kind: str = "price", lo: float | None = None, hi: float | None = None, note: str = "") -> dict[str, Any]:
    tol_v = tol if isinstance(tol, (int, float)) else TOL_TICKS[tol]
    return {"day": day.isoformat(), "reading_et": at.strftime("%H:%M"), "group": group, "level": level,
            "level_id": level_id, "kind": kind, "bot_value": value, "bot_lo": lo, "bot_hi": hi,
            "tolerance_ticks": "text" if kind == "text" else tol_v, "deepcharts_value": "", "diff_ticks": "",
            "within_tol": "", "note": note}


def fib_rows(day: date, at: time, d) -> list[dict[str, Any]]:
    if d is None:
        return [_row(day, at, "fib", "no fib drawn", "", "", "exact", kind="text")]
    up = d.orientation == gz.LONG
    rows = [_row(day, at, "fib", "fib orientation", "", f"{d.orientation} ({'low→high, zone below' if up else 'high→low, zone above'})",
                 "exact", kind="text", note=f"drawing {d.source}, flips {d.flips}"),
            _row(day, at, "fib", "fib anchor high", "", d.high, "exact"),
            _row(day, at, "fib", "fib anchor low", "", d.low, "exact")]
    for x in FIB_RATIOS:
        rows.append(_row(day, at, "fib", f"fib {x:g}", "", d.level(x), "fib_ratio",
                         note="within the 0.25 rounding"))
    return rows


def ib_rows(day: date, at: time, orr, t: int) -> list[dict[str, Any]]:
    rows = []
    for lv in sorted(orr.ib_at(t), key=lambda x: x.side):
        rows.append(_row(day, at, "ib", f"IB {lv.side}", lv.level_id, lv.price, "exact"))
    return rows


STATIC_SUBTYPES = {"pdh": "PDH (prior CME day high)", "pdl": "PDL (prior CME day low)",
                   "prior_rth_high": "prior RTH high", "prior_rth_low": "prior RTH low",
                   "on_high": "overnight high", "on_low": "overnight low",
                   "prior_week_high": "prior-week high", "prior_week_low": "prior-week low"}


def level_rows(cs, day: date, at: time, scope: str) -> list[dict[str, Any]]:
    """Every value of one reading, from data <= the reading instant (each engine's own causal query)."""
    t = st.et_ns(day, at)
    rows: list[dict[str, Any]] = []
    orr = cs.opening
    if scope in ("all", "ib_fib") and orr is not None:
        rows += ib_rows(day, at, orr, t)
    if orr is not None:
        rows += fib_rows(day, at, orr.drawing_at(t))
    if scope != "all":
        return rows
    for anchor, name in (("vwap18", "VWAP 18:00"), ("vwap0930", "VWAP 09:30")):
        a = cs.vwap[anchor].at(t)
        if a is None:
            continue
        v, s = a
        rows.append(_row(day, at, "vwap", name, f"{anchor}-{day}", v, "vwap"))
        for k in (1, 2):
            rows.append(_row(day, at, "vwap", f"{name} +{k}σ", f"{anchor}_up{k}-{day}", v + k * s, "vwap_band"))
            rows.append(_row(day, at, "vwap", f"{name} −{k}σ", f"{anchor}_dn{k}-{day}", v - k * s, "vwap_band"))
    for prof, name in ((cs.daily, "daily"), (cs.weekly, "weekly")):
        snap = prof.at(t) if prof is not None else None
        if snap is None:
            continue
        note = f"profile instant {et(snap.t_ns, '%H:%M')}"
        for k in ("poc", "vah", "val"):
            rows.append(_row(day, at, f"profile_{name}", f"{name} {k.upper()}", "", getattr(snap, k),
                             "poc" if k == "poc" else "va", note=note))
        for ident, price in snap.lvns:
            rows.append(_row(day, at, f"lvn_{name}", f"{name} LVN", f"lvn_{name}-{ident:g}", price, "lvn",
                             note="same LVN within 4 ticks"))
    if cs.prev_prof is not None:
        for k in ("poc", "vah", "val"):
            rows.append(_row(day, at, "prev_profile", f"previous session {k.upper()}", "", cs.prev_prof.get(k),
                             "poc" if k == "poc" else "va", note=f"anchor {cs.f['prev_profile_anchor']}"))
    reg = cs.registry_at(t)
    for x in sorted((x for x in reg if x["sub_type"] in STATIC_SUBTYPES), key=lambda x: x["sub_type"]):
        lo, hi = x["band"]
        rows.append(_row(day, at, "session_hl", STATIC_SUBTYPES[x["sub_type"]], x["level_id"], (lo + hi) / 2, "exact"))
    price = cs.tape.last_price_at(t)
    sps = [x for x in reg if x["sub_type"] == "single_print"]
    for x in sorted(sps, key=lambda x: abs((x["band"][0] + x["band"][1]) / 2 - price) if price else 0):
        lo, hi = x["band"]
        formed = x["level_id"][3:13]
        dist = None if price is None else min(abs(lo - price), abs(hi - price)) if not lo <= price <= hi else 0.0
        note = f"formed {formed}; {dist:.0f} pts from price" if dist is not None else f"formed {formed}"
        tol = sp_tol_ticks(cs.f)
        rows.append(_row(day, at, "single_print", "single print low edge", x["level_id"], lo, tol,
                         lo=lo, hi=hi, note=note + "; band = the unfilled bins; within one TPO bin"))
        rows.append(_row(day, at, "single_print", "single print high edge", x["level_id"], hi, tol,
                         lo=lo, hi=hi, note=note))
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]], cols=CSV_COLS) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(cols), extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: _num(r.get(k)) if not isinstance(r.get(k), str) else r.get(k) for k in cols})
    atomic_write_bytes(path, buf.getvalue().encode("utf-8-sig"))   # Excel opens UTF-8 with a BOM correctly


def read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


# --- compare ---------------------------------------------------------------------------------------------------------

def _f(x: str | None) -> float | None:
    try:
        return float(str(x).replace(",", "")) if x not in (None, "") else None
    except ValueError:
        return None


POC_LEVELS = frozenset({"daily POC", "weekly POC", "previous session POC"})
VA_LEVELS = frozenset(f"{p} {k}" for p in ("daily", "weekly", "previous session") for k in ("VAH", "VAL"))


def compare_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Fill `diff_ticks` / `within_tol` from the owner's Deepcharts column (§12 step 12 tolerances).
    - a price row: diff = (Deepcharts − bot) in ticks, within if |diff| <= the tolerance;
    - a text row (the fib orientation): within if Deepcharts' value starts with the bot's word (long / short);
    - an LVN row: the bot's LVN is matched if the owner's Deepcharts LVN price is within 4 ticks; `none` = Deepcharts
      has no LVN there (a miss); a row the owner added with no bot value (a Deepcharts LVN) is matched against the
      nearest bot LVN of the same profile and reading;
    - an empty Deepcharts cell = not read (reported as such);
    - a daily / weekly / previous-session POC row uses TOL_TICKS["poc"] (±2 pts) and a VAH / VAL row TOL_TICKS["va"]
      (±4 ticks; both owner 2026-11-07), also when its table was written with the earlier 2 ticks."""
    out = []
    bot_lvns: dict[tuple, list[float]] = {}
    for r in rows:
        if r["group"].startswith("lvn_") and _f(r["bot_value"]) is not None:
            bot_lvns.setdefault((r["day"], r["reading_et"], r["group"]), []).append(_f(r["bot_value"]))
    for r in rows:
        r = dict(r)
        dc = (r.get("deepcharts_value") or "").strip()
        bot = _f(r.get("bot_value"))
        if r.get("level") in POC_LEVELS:
            r["tolerance_ticks"] = TOL_TICKS["poc"]
        elif r.get("level") in VA_LEVELS:
            r["tolerance_ticks"] = TOL_TICKS["va"]
        tol = r.get("tolerance_ticks")
        r["diff_ticks"], r["within_tol"] = "", ""
        if not dc:
            r["status"] = "not read"
        elif r.get("kind") == "text":
            ok = dc.lower().split()[0] == str(r["bot_value"]).lower().split()[0]
            r["within_tol"], r["status"] = ("yes" if ok else "no"), ("ok" if ok else "miss")
        elif dc.lower() == NOT_PRESENT:
            r["within_tol"], r["status"] = "no", "miss: not in Deepcharts"
        elif bot is None and r["group"].startswith("lvn_"):
            v = _f(dc)
            near = bot_lvns.get((r["day"], r["reading_et"], r["group"]), [])
            if v is None:
                r["status"] = "unreadable Deepcharts value"
            elif near:
                d = min(near, key=lambda b: abs(b - v))
                r["diff_ticks"] = round((v - d) / TICK, 2)
                ok = abs(v - d) / TICK <= TOL_TICKS["lvn"] + 1e-9
                r["within_tol"], r["status"] = ("yes" if ok else "no"), ("ok" if ok else "miss: no bot LVN near")
            else:
                r["within_tol"], r["status"] = "no", "miss: no bot LVN near"
        else:
            v = _f(dc)
            if v is None or bot is None:
                r["status"] = "unreadable Deepcharts value"
            else:
                diff = (v - bot) / TICK
                r["diff_ticks"] = round(diff, 2)
                ok = abs(diff) <= float(tol) + 1e-9
                r["within_tol"], r["status"] = ("yes" if ok else "no"), ("ok" if ok else "miss")
        out.append(r)
    return out


# --- POC near-ties and row size (owner 2026-11-07: POC/VA consistent; a POC that differs because two prices have
# near-equal volume is accepted, measured at Deepcharts' 1-pt rows) -------------------------------------------------

NEAR_TIE_PCT = 90.0     # owner 2026-11-07: a POC miss is a near-tie when the bot's volume in the 1-pt row holding
                        # Deepcharts' POC is >= 90% of the bot's largest 1-pt row
ROW_TICKS = 4           # owner 2026-11-07: Deepcharts Manual grouping, 4 ticks (1 pt), for the gate-12 pass
WEEKLY_ROW_TICKS_2 = 8  # the weekly POC is also reported at 2-pt rows (never marked from it)
ROW_OFFSET_TICKS = 0    # owner 2026-11-08: rows start at whole points (2-pt rows at even points), price floored to the
                        # row size; the other 1-tick offsets are reported beside it
PROFILE_LEVELS = {f"{name} {k.upper()}": (src, k) for name, src in (("daily", "daily"), ("weekly", "weekly"),
                                                                    ("previous session", "prev"))
                  for k in ("poc", "vah", "val")}
DEEPCHARTS_GROUPING_NOTE = (
    "Deepcharts grouping (owner 2026-11-07): the first Deepcharts readings were taken with the volume profile's row "
    "size on Automatic (Tick Grouping: Auto grouping = Automatic), so it changed with the range and the zoom; "
    "\"Shifted Poc Tick Grouping = 4\" is the POC-shift alert only. For the gate-12 pass the owner switches Deepcharts "
    "to Manual grouping, 4 ticks (1 pt), and every value in the tables is a Manual 4-tick reading, on all five days. "
    "Owner 2026-11-08: the 03-06 daily POC 24690 and weekly POC 25030 now in the table are Manual readings and count "
    "(they replace the Automatic readings 24688.5 and 25029.75).")
ROW_ALIGNMENT_NOTE = (
    "Row alignment (owner 2026-11-08): rows start at whole points. The readings can't settle the alignment: Deepcharts "
    "shows the price under the cursor, and where the bot's price looked closer than a tick-level reading allowed, the "
    "owner typed the bot's price.")


def row_day(s: str) -> date:
    """The CSV's day: ISO as written, or M/D/YYYY after Excel re-saved the file."""
    s = s.strip()
    try:
        return date.fromisoformat(s)
    except ValueError:
        return datetime.strptime(s, "%m/%d/%Y").date()


def profile_volume(tape, anchor_ns: int, t_ns: int, update_sec: int, carried: dict | None = None) -> dict[int, float]:
    """Volume by tick of a developing profile at its last instant <= t, exactly as `DevelopingProfile.build` sums it:
    the carried volume plus today's trades from the anchor through the instant (ts_event <= the instant)."""
    step = update_sec * NS
    inst = anchor_ns + ((t_ns - anchor_ns) // step) * step
    out = {int(k): float(v) for k, v in (carried or {}).items()}
    i, j = tape.idx_before(anchor_ns), tape.idx_at(int(inst))
    if j > i:
        ticks = pf.to_tick(tape.price[i:j])
        for k, v in zip(ticks.tolist(), np.asarray(tape.size[i:j], float).tolist()):
            out[k] = out.get(k, 0.0) + v
    return out


def row_start(tick: int, row_ticks: int, offset: int = 0) -> int:
    """The first tick of the row holding `tick`: rows of `row_ticks` ticks starting at ticks = offset (mod row_ticks)."""
    return ((tick - offset) // row_ticks) * row_ticks + offset


def group_rows(volume: dict[int, float], row_ticks: int, offset: int = 0) -> dict[int, float]:
    out: dict[int, float] = {}
    for k, v in volume.items():
        r = row_start(k, row_ticks, offset)
        out[r] = out.get(r, 0.0) + v
    return out


def row_tie_pct(volume: dict[int, float], dc_price: float, row_ticks: int, offset: int = 0) -> float | None:
    """The bot's volume in the row holding Deepcharts' POC price as a % of the bot's largest row."""
    g = group_rows(volume, row_ticks, offset)
    if not g:
        return None
    return 100.0 * g.get(row_start(int(round(dc_price / TICK)), row_ticks, offset), 0.0) / max(g.values())


def row_profile(volume: dict[int, float], row_ticks: int, offset: int = 0) -> dict[str, tuple[float, float]] | None:
    """POC / VAH / VAL by the F3.2 / F3.3 rules (ties, one row at a time) on rows of `row_ticks` ticks; each as the
    row's (lowest, highest) price. `row_ticks` 1 = the bot's own profile."""
    g = group_rows(volume, row_ticks, offset)
    if not g:
        return None
    lo = min(g)
    dense = np.zeros((max(g) - lo) // row_ticks + 1)
    for r, v in g.items():
        dense[(r - lo) // row_ticks] = v
    p = pf.poc_index(dense)
    a, b = pf.value_area(dense, p)

    def span(i: int) -> tuple[float, float]:
        return (lo + i * row_ticks) * TICK, (lo + i * row_ticks + row_ticks - 1) * TICK

    return {"poc": span(p), "val": span(a), "vah": span(b)}


def reproduces(volume: dict[int, float], key: str, bot_value: float | None) -> bool:
    """The volume gives the bot's stored value at single prices (so a wrong profile is never reported)."""
    prof = row_profile(volume, 1)
    return prof is not None and bot_value is not None and abs(prof[key][0] - bot_value) <= 1e-9


def poc_volumes(env: Env, day: date) -> dict[str, dict[int, float]]:
    """The bot's daily / weekly profile volume at the 10:01 reading and the previous session's completed profile, from
    the feature build's stored start state and the day's NQ tape (the session start applied: roll shift, new week)."""
    from nqbot.checks import session_build as sb
    from nqbot.features import chart_levels as cl
    from nqbot.features.carry import CarryState

    state, _ = start_state(env, day)
    carry = CarryState.from_json(state.nq)
    tape = st.load_tape(env.db, sb.plan(env), "NQ", day)
    if tape is None:
        raise Refused(f"NQ {day}: no stored trades")
    cl.start_session(carry, day, tape.symbol, cl.roll_spread_of("NQ", day, env.db.rolls()))
    t = st.et_ns(day, READ_ALL)
    upd = int(env.features["profile_update_sec"])
    start = st.cme_start_ns(day)
    wk_anchor = carry.weekly_anchor_ns if carry.weekly_anchor_ns is not None else start
    out = {"daily": profile_volume(tape, start, t, upd),
           "weekly": profile_volume(tape, wk_anchor, t, upd, carry.weekly_volume)}
    prev = (carry.prev_profile or {}).get(env.features["prev_profile_anchor"])
    out["prev"] = {int(k): float(v) for k, v in prev["volume"].items()} if prev else {}
    return out


def cached(volumes_of):
    """One `poc_volumes` per day (each costs a state read and a tape load)."""
    memo: dict[date, dict] = {}

    def get(d: date) -> dict:
        if d not in memo:
            memo[d] = volumes_of(d)
        return memo[d]
    return get


TIE_COLS = ("dc_row_pct_1pt", "dc_row_pct_1pt_other_offsets", "dc_row_pct_2pt")


def _is_1001(r: dict) -> bool:
    return r.get("reading_et") == READ_ALL.strftime("%H:%M")


def mark_poc_ties(res: list[dict[str, Any]], volumes_of) -> dict[str, int]:
    """For each POC row outside its tolerance: `dc_row_pct_1pt` = the bot's volume in the 1-pt row holding Deepcharts'
    POC as a % of the bot's largest 1-pt row (alignment ROW_OFFSET_TICKS); "miss: POC near-tie" at >= NEAR_TIE_PCT; the
    other alignments beside it (the status says if one of them would change the call); the weekly POC also at 2-pt rows
    (reported only). `volumes_of(day)` -> poc_volumes."""
    counts = {"near-tie": 0, "other": 0, "unchecked": 0, "alignment-sensitive": 0}
    get = cached(volumes_of)
    for r in res:
        for c in TIE_COLS:
            r.setdefault(c, "")
        src = PROFILE_LEVELS.get(r.get("level", ""))
        if src is None or src[1] != "poc" or r["status"] != "miss" or not _is_1001(r):
            continue
        vol = get(row_day(r["day"])).get(src[0]) or {}
        dc = float(_f(r["deepcharts_value"]))
        if not reproduces(vol, "poc", _f(r["bot_value"])):
            r["status"] = "miss: POC (bot volume not reproduced, tie not checked)"
            counts["unchecked"] += 1
            continue
        pct = row_tie_pct(vol, dc, ROW_TICKS, ROW_OFFSET_TICKS)
        near = pct >= NEAR_TIE_PCT - 1e-9
        others = {o: row_tie_pct(vol, dc, ROW_TICKS, o) for o in range(ROW_TICKS) if o != ROW_OFFSET_TICKS}
        r["dc_row_pct_1pt"] = round(pct, 1)
        r["dc_row_pct_1pt_other_offsets"] = "; ".join(f"+{o} tick {x:.1f}" for o, x in others.items())
        if src[0] == "weekly":
            r["dc_row_pct_2pt"] = round(row_tie_pct(vol, dc, WEEKLY_ROW_TICKS_2, ROW_OFFSET_TICKS), 1)
        r["status"] = "miss: POC near-tie" if near else "miss: POC"
        flip = [o for o, x in others.items() if (x >= NEAR_TIE_PCT - 1e-9) != near]
        if flip:
            r["status"] += (f" ({'not ' if near else ''}a near-tie with rows offset "
                            f"{', '.join(f'+{o}' for o in flip)} tick)")
            counts["alignment-sensitive"] += 1
        counts["near-tie" if near else "other"] += 1
    return counts


def _span(s: tuple[float, float] | None) -> str:
    return "" if s is None else f"{_num(s[0])}–{_num(s[1])}"


def _outside_ticks(s: tuple[float, float] | None, x: float | None) -> str:
    """Ticks from Deepcharts' value to the row (0 = inside it)."""
    if s is None or x is None:
        return ""
    return _num(max(s[0] - x, x - s[1], 0.0) / TICK)


def row_evidence(res: list[dict[str, Any]], volumes_of) -> list[dict[str, Any]]:
    """Read-only (owner 2026-11-07, evidence for a possible step-13 row-size correction; no decision): on every day with
    a Deepcharts POC / VAH / VAL filled in, the POC / VAH / VAL the bot would give at 1-pt rows (and 2-pt for the
    weekly) beside its current value and Deepcharts'."""
    days = sorted({row_day(r["day"]) for r in res
                   if r.get("level") in PROFILE_LEVELS and _is_1001(r) and (r.get("deepcharts_value") or "").strip()})
    get = cached(volumes_of)
    out = []
    for d in days:
        for r in res:
            src = PROFILE_LEVELS.get(r.get("level", ""))
            if src is None or not _is_1001(r) or row_day(r["day"]) != d:
                continue
            vol = get(d).get(src[0]) or {}
            bot, dc = _f(r["bot_value"]), _f(r.get("deepcharts_value"))
            one = row_profile(vol, ROW_TICKS, ROW_OFFSET_TICKS)
            two = row_profile(vol, WEEKLY_ROW_TICKS_2, ROW_OFFSET_TICKS) if src[0] == "weekly" else None
            s1 = one[src[1]] if one else None
            s2 = two[src[1]] if two else None
            tol = TOL_TICKS["poc" if src[1] == "poc" else "va"]
            out_1 = None if s1 is None or dc is None else max(s1[0] - dc, dc - s1[1], 0.0) / TICK
            out.append({"day": d.isoformat(), "level": r["level"], "bot_now": r["bot_value"], "tol": tol,
                        "pass_now": r.get("within_tol") == "yes",
                        "pass_1pt": out_1 is not None and out_1 <= tol + 1e-9,
                        "in_1pt_row": out_1 is not None and out_1 <= 1e-9, "filled": dc is not None and bot is not None,
                        "roll_day": r.get("status", "").startswith(ROLL_DAY_STATUS),
                        "reproduced": "yes" if reproduces(vol, src[1], bot) else "NO",
                        "bot_1pt": _span(s1), "bot_2pt": _span(s2),
                        "deepcharts": r.get("deepcharts_value") or "not read",
                        "dc_vs_now_ticks": "" if dc is None or bot is None else _num((dc - bot) / TICK),
                        "dc_outside_1pt_ticks": _outside_ticks(s1, dc), "dc_outside_2pt_ticks": _outside_ticks(s2, dc)})
    return out


def row_size_summary(evidence: list[dict[str, Any]]) -> str:
    """Read-only (owner 2026-11-07): of the filled POC / VAH / VAL values, how many pass their tolerance at the bot's
    current single ticks vs at 1-pt rows (Deepcharts within the tolerance of the bot's 1-pt row)."""
    roll = sum(1 for e in evidence if e["filled"] and e.get("roll_day"))
    ok = [e for e in evidence if e["filled"] and not e.get("roll_day") and e["reproduced"] == "yes" and e["bot_1pt"]]
    left_out = sum(1 for e in evidence if e["filled"] and not e.get("roll_day")) - len(ok)

    def part(es: list[dict]) -> str:
        return (f"{sum(e['pass_now'] for e in es)} vs {sum(e['pass_1pt'] for e in es)} "
                f"({sum(e['in_1pt_row'] for e in es)} inside the row)")

    by = {k: [e for e in ok if e["level"].endswith(k)] for k in ("POC", "VAH", "VAL")}
    return (f"Row size (read-only evidence for the step-13 correction, SPEC §13 Q-step12 (4)): of {len(ok)} "
            f"filled POC / VAH / VAL values, pass at the bot's current single ticks vs at 1-pt rows: {part(ok)}; "
            + "; ".join(f"{k} {part(v)} of {len(v)}" for k, v in by.items())
            + ". At 1-pt rows a value passes when Deepcharts is within the same tolerance (POC 8, VAH / VAL 4 ticks) "
            "of the bot's 1-pt row (0 = inside it)"
            + (f"; {left_out} left out (the rebuilt volume didn't reproduce the bot's value)" if left_out else "")
            + (f"; the roll day's {roll} roll-day-definition values left out" if roll else "")
            + ".")


# owner 2026-11-08 (SPEC §13 Q-step12 (3)): on roll days Deepcharts appears to use the new contract's own week and its
# own previous session; the bot keeps its definitions, so these misses are a known definition difference (the weekly
# LVNs too: same cause, the roll-week weekly drops pre-roll days)
ROLL_DAY_STATUS = "miss: roll-day definition"
ROLL_DAY_DEFINITION = {date(2026, 3, 17): frozenset({"weekly POC", "weekly VAH", "weekly VAL", "weekly LVN",
                                                     "previous session POC", "previous session VAH",
                                                     "previous session VAL"})}


def mark_roll_day(res: list[dict[str, Any]]) -> int:
    """Mark the roll day's weekly POC / VAH / VAL / LVN and previous-session POC / VAH / VAL misses 'roll-day
    definition' (any miss status: an LVN miss may say 'not in Deepcharts' or 'no bot LVN near')."""
    n = 0
    for r in res:
        if r.get("status", "").startswith("miss") and \
                r.get("level") in ROLL_DAY_DEFINITION.get(row_day(r["day"]), ()):
            r["status"] = ROLL_DAY_STATUS
            n += 1
    return n


def compare(env: Env) -> Path:
    files = sorted(out_dir(env).glob("*/levels_*.csv"))
    if not files:
        raise Refused("no reading tables yet: run `nqbot step12 day D` (or `all`) first")
    rows = [r for p in files for r in read_csv(p)]
    res = compare_rows(rows)
    n_roll = mark_roll_day(res)
    vols = cached(lambda d: poc_volumes(env, d))
    ties = mark_poc_ties(res, vols)
    evidence = row_evidence(res, vols)
    write_csv(out_dir(env) / "compare.csv", res, CSV_COLS + ("status",) + TIE_COLS)
    counts: dict[str, int] = {}
    for r in res:
        counts[r["status"].split(":")[0]] = counts.get(r["status"].split(":")[0], 0) + 1
    off = f"rows start at whole points (offset {ROW_OFFSET_TICKS} ticks; 2-pt rows at even points)"
    lines = ["# Step 12: bot vs Deepcharts", "",
             f"{len(res)} values from {len(files)} tables; " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())),
             "", DEEPCHARTS_GROUPING_NOTE, "", ROW_ALIGNMENT_NOTE, "",
             "Tolerances (§12 step 12, owner 2026-10-23): IB, fib anchors, PD / overnight / week levels exact; fib "
             "ratios within the 0.25 rounding; VWAP 1 tick, its bands 2; LVNs the same LVN within 4 "
             "ticks; single prints within one TPO bin (owner 2026-11-02). The daily, weekly and previous-session POC "
             "within ±2 pts (8 ticks; owner 2026-11-07: price chops around the POC rather than reacting at an exact "
             "price); VAH / VAL within ±4 ticks (one Deepcharts row at Manual 4 ticks; owner 2026-11-07). A miss is a Deepcharts setting or a "
             "definition difference (the latter goes to §13).", "",
             f"Roll day (owner 2026-11-08, SPEC §13 Q-step12 (3)): {n_roll} misses marked \"roll-day definition\" "
             "(" + ", ".join(f"{d.isoformat()}: {', '.join(sorted(v))}" for d, v in ROLL_DAY_DEFINITION.items())
             + "). On roll days Deepcharts appears to use the new contract's own week and its own previous session; "
             "the bot keeps its definitions (the roll-week weekly drops pre-roll days, the previous session as built) "
             "because the new contract's own pre-roll history is thin. They are left out of the POC near-tie check and "
             "the row-size summary.", "",
             f"POC misses beyond ±2 pts: {ties['near-tie']} near-tie, {ties['other']} other"
             + (f", {ties['unchecked']} not checked (the bot's volume didn't reproduce its POC)" if ties["unchecked"]
                else "")
             + (f"; {ties['alignment-sensitive']} would change with another row alignment"
                if ties["alignment-sensitive"] else "; no call changes with another row alignment")
             + f". Near-tie (owner 2026-11-07) = the bot's volume in the 1-pt (4-tick) row holding Deepcharts' POC is "
             f">= {NEAR_TIE_PCT:g}% of the bot's largest 1-pt row; alignment: {off}, price floored to the "
             "row size (owner 2026-11-08); the other three 1-tick offsets are shown beside it. The weekly "
             "POC is also shown at 2-pt rows (not used for the call). Daily / weekly at the 10:01 profile instant, the "
             "previous session's completed profile.", "", row_size_summary(evidence), "",
             "## Misses", "",
             "| day | reading | level | bot | Deepcharts | diff (ticks) | tolerance | status |",
             "|---|---|---|---|---|---|---|---|"]
    for r in res:
        if r["status"].startswith("miss") or r["status"].startswith("unreadable"):
            lines.append(f"| {r['day']} | {r['reading_et']} | {r['level']} {r['level_id']} | {r['bot_value']} | "
                         f"{r['deepcharts_value']} | {r['diff_ticks']} | {r['tolerance_ticks']} | {r['status']} |")
    poc_miss = [r for r in res if r["status"].startswith("miss: POC")]
    lines += ["", "## POC misses beyond ±2 pts, at 1-pt rows", ""]
    if poc_miss:
        lines += ["| day | level | bot | Deepcharts | DC row % of the largest 1-pt row | other alignments | weekly: "
                  "at 2-pt rows | status |", "|---|---|---|---|---|---|---|---|"]
        lines += [f"| {r['day']} | {r['level']} | {r['bot_value']} | {r['deepcharts_value']} | {r['dc_row_pct_1pt']} | "
                  f"{r['dc_row_pct_1pt_other_offsets']} | {r['dc_row_pct_2pt']} | {r['status']} |" for r in poc_miss]
    else:
        lines.append("None.")
    lines += ["", "## Profile at 1-pt rows (read-only evidence for the step-13 row-size correction, SPEC §13 Q-step12 (4))",
              "", f"Days with a Deepcharts POC / VAH / VAL filled in. The bot's POC / VAH / VAL at 1-pt rows (and 2-pt "
              f"for the weekly), each the row's price range, by the F3.2 / F3.3 rules applied to rows ({off}); "
              "\"reproduced\" = the rebuilt volume gives the bot's current value at single prices. \"outside\" = ticks "
              "from Deepcharts' value to the row (0 = inside).", ""]
    if evidence:
        lines += ["| day | level | bot now | reproduced | bot 1-pt row | bot 2-pt row | Deepcharts | DC − now (ticks) | "
                  "DC outside 1-pt row | DC outside 2-pt row |", "|---|---|---|---|---|---|---|---|---|---|"]
        lines += [f"| {e['day']} | {e['level']} | {e['bot_now']} | {e['reproduced']} | {e['bot_1pt']} | {e['bot_2pt']} "
                  f"| {e['deepcharts']} | {e['dc_vs_now_ticks']} | {e['dc_outside_1pt_ticks']} | "
                  f"{e['dc_outside_2pt_ticks']} |" for e in evidence]
    else:
        lines.append("No day filled in yet.")
    path = out_dir(env) / "compare.md"
    atomic_write_text(path, "\n".join(lines) + "\n")
    env.echo(f"  {', '.join(f'{k} {v}' for k, v in sorted(counts.items()))}; POC misses: {ties['near-tie']} "
             f"near-tie, {ties['other']} other; report {path}")
    return path


# --- LVN lifetimes -----------------------------------------------------------------------------------------------

def lvn_spans(levels: dict, open_ns: int, close_ns: int, session_end_ns: int) -> list[dict[str, Any]]:
    """One row per LVN span (`ChartSession.levels`: one record per identity and contiguous qualifying span; the band's
    `times` are the instants it qualified at, `end_ns` the first instant it no longer did, None if still at the end)."""
    out = []
    for lid, recs in levels.items():
        for r in recs:
            if r.sub_type not in ("lvn_daily", "lvn_weekly"):
                continue
            b = r.state.band
            ts = np.asarray(b.times, dtype=np.int64)
            px = (np.asarray(b.lo, float) + np.asarray(b.hi, float)) / 2
            stop = b.end_ns
            end = int(stop) if stop is not None else int(session_end_ns)
            out.append({"profile": r.sub_type.split("_")[1], "level_id": lid, "identity": lid.rsplit("-", 1)[-1],
                        "price_first": float(px[0]), "price_min": float(px.min()), "price_max": float(px.max()),
                        "first_ns": int(ts[0]), "last_ns": int(ts[-1]), "stop_ns": None if stop is None else int(stop),
                        "lifetime_min": (end - int(ts[0])) / MIN_NS, "instants": len(ts),
                        "rth": bool(int(ts[0]) < close_ns and end > open_ns)})
    return sorted(out, key=lambda x: (x["profile"], x["first_ns"], x["price_first"]))


def lifetime_histogram(spans: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for prof in ("daily", "weekly"):
        h = {}
        for a, b in LIFETIME_BUCKETS:
            lab = f"{a}–{b} min" if b else f"> {a - 1} min"
            h[lab] = sum(1 for s in spans if s["profile"] == prof and s["lifetime_min"] >= a - 1e-9
                         and (b is None or s["lifetime_min"] < b + 1 - 1e-9))
        out[prof] = h
    return out


# --- node types (F2.2, F2.5) -------------------------------------------------------------------------------------

def classify_board(values: dict[float, float], spot: float, min_pct: float) -> dict[float, str]:
    """The bot's types on one board alone (F2.5; one board, so the confirmed King = the board's King): king; gatekeeper
    = relevant (>= min_pct of King, fresh) and strictly between spot and the King; significant = other relevant;
    normal."""
    from nqbot.features import node_switch as nsw

    king = nsw.leader(values, spot)
    kabs = abs(values[king]) if king is not None else 0.0
    lo, hi = (min(spot, king), max(spot, king)) if king is not None else (0.0, 0.0)
    out = {}
    for k, v in values.items():
        rel = kabs > 0 and abs(v) / kabs >= min_pct
        if k == king:
            out[k] = "king"
        elif rel and lo < k < hi:
            out[k] = "gatekeeper"
        elif rel:
            out[k] = "significant"
        else:
            out[k] = "normal"
    return out


@dataclass
class NodeCounts:
    full: dict = field(default_factory=dict)        # sym -> {(skylit type, bot type): n}
    king_2x2: dict = field(default_factory=dict)    # sym -> {(skylit king?, bot king?): n}
    rel_2x2: dict = field(default_factory=dict)     # sym -> {(skylit gatekeeper?, bot gk ∪ significant?): n}, non-King
    king_ok: dict = field(default_factory=dict)     # sym -> [1/0 per instant]
    sign_bad: list = field(default_factory=list)
    instants: int = 0
    files: int = 0
    max_strikes: set = field(default_factory=set)
    dates: set = field(default_factory=set)


def count_board(nc: NodeCounts, sym: str, rows: list[dict], min_pct: float, label: str = "") -> None:
    values = {float(r["strike"]): float(r["value"]) for r in rows}
    spot = float(rows[0]["spot"])
    bot = classify_board(values, spot, min_pct)
    full, k2, r2 = (nc.full.setdefault(sym, {}), nc.king_2x2.setdefault(sym, {}), nc.rel_2x2.setdefault(sym, {}))
    for r in rows:
        k, v, t = float(r["strike"]), float(r["value"]), r["node_type"]
        if (t == "pika" and not v > 0) or (t == "barney" and not v < 0):
            nc.sign_bad.append(f"{label} {sym} {k:g} {t} {v:g}")
        b = bot[k]
        full[(t, b)] = full.get((t, b), 0) + 1
        kk = (t == "king", b == "king")
        k2[kk] = k2.get(kk, 0) + 1
        if not (t == "king" or b == "king"):
            rr = (t == "gatekeeper", b in ("gatekeeper", "significant"))
            r2[rr] = r2.get(rr, 0) + 1
    sk = [float(r["strike"]) for r in rows if r["node_type"] == "king"]
    bk = [k for k, x in bot.items() if x == "king"]
    nc.king_ok.setdefault(sym, []).append(int(len(sk) == 1 and sk == bk))
    nc.instants += 1


def node_counts(env: Env) -> NodeCounts:
    import pyarrow.parquet as pq

    files = sorted((env.paths.raw_dir / "skylit" / "historical").glob("*.parquet"))
    if not files:
        raise Refused("no stored /v1/historical instants (step 7 historical-instants writes them)")
    nc = NodeCounts(files=len(files))
    for p in files:
        tab = pq.read_table(p)
        meta = json.loads((tab.schema.metadata or {}).get(b"nqbot", b"{}"))
        nc.max_strikes.add(str((meta.get("params") or {}).get("maxStrikes")))
        rows = tab.replace_schema_metadata(None).to_pylist()
        by: dict[tuple, list[dict]] = {}
        for r in rows:
            by.setdefault((r["symbol"], r["as_of"]), []).append(r)
        for (sym, as_of), rs in by.items():
            d = _as_of_date(as_of)
            require_development(d, env.settings)   # §7 rule 2b: development months only
            nc.dates.add(d)
            count_board(nc, sym, rs, float(env.features["min_node_pct_of_king"]), p.name)
    return nc


def _as_of_date(as_of: Any) -> date:
    if isinstance(as_of, datetime):
        return as_of.astimezone(ET).date()
    if isinstance(as_of, (int, np.integer)):
        return datetime.fromtimestamp(int(as_of) / (NS if int(as_of) > 10**14 else 1), ET).date()
    return datetime.fromisoformat(str(as_of).replace("Z", "+00:00")).astimezone(ET).date()


def _table_2x2(c: dict, row_lab: str, col_lab: str) -> list[str]:
    return [f"| Skylit {row_lab} \\ bot {col_lab} | yes | no |", "|---|---|---|",
            f"| yes | {c.get((True, True), 0)} | {c.get((True, False), 0)} |",
            f"| no | {c.get((False, True), 0)} | {c.get((False, False), 0)} |"]


def nodetype_report(env: Env) -> int:
    nc = node_counts(env)
    syms = sorted(nc.full)
    pooled = NodeCounts()
    for s in syms:
        for name in ("full", "king_2x2", "rel_2x2"):
            dst = getattr(pooled, name).setdefault("all maps", {})
            for k, v in getattr(nc, name)[s].items():
                dst[k] = dst.get(k, 0) + v
        pooled.king_ok.setdefault("all maps", []).extend(nc.king_ok[s])
    f = env.features
    lines = ["# Step 12 node-type agreement (F2.2, F2.5)", "",
             f"{nc.files} stored `/v1/historical` instants (step 7), {min(nc.dates)} … {max(nc.dates)}, all in "
             f"development months 1-6; `maxStrikes` {', '.join(sorted(nc.max_strikes))}. Each instant's strikes are "
             f"classified on that board alone: fresh relevance (at least {f['min_node_pct_of_king']} of the board's "
             "King, no 20% hysteresis), the confirmed King = the board's King (one board), Gatekeeper = relevant and "
             "strictly between spot and the King (F2.5), significant = the other relevant nodes.", "",
             f"**Sign check (F2.2): {'PASS' if not nc.sign_bad else 'FAIL'}**: Skylit pika strikes positive, barney "
             f"strikes negative ({len(nc.sign_bad)} violations).", ""]
    lines += [f"- {x}" for x in nc.sign_bad[:20]]
    for s, src in [(s, nc) for s in syms] + [("all maps", pooled)]:
        ok = src.king_ok[s]
        lines += ["", f"## {s}", "", f"**King agreement: {sum(ok)}/{len(ok)} instants** (exactly one Skylit king and it "
                  "is the bot's King).", "", "King, every strike:", ""]
        lines += _table_2x2(src.king_2x2[s], "king", "King")
        lines += ["", "Gatekeeper, every non-King strike (neither side's King): Skylit gatekeeper vs the bot's "
                  "Gatekeeper ∪ significant (relevant non-King):", ""]
        lines += _table_2x2(src.rel_2x2[s], "gatekeeper", "Gatekeeper ∪ significant")
        bots = ("king", "gatekeeper", "significant", "normal")
        lines += ["", "Full table (rows Skylit `nodeType`, columns the bot's type):", "",
                  "| Skylit nodeType | " + " | ".join(bots) + " |", "|---|" + "---|" * len(bots)]
        for t in sorted({t for t, _ in src.full[s]}):
            lines.append(f"| {t} | " + " | ".join(str(src.full[s].get((t, b), 0)) for b in bots) + " |")
    lines += ["", "Expected under the owner's Q3 rule (F2.5): Skylit king ↔ bot King; Skylit gatekeeper (size-only: "
              "any non-King strike at 25% of the King or more) ↔ bot Gatekeeper ∪ significant. Skylit's pika / barney "
              "are compared only for their sign. The step-12 gate needs this report reviewed by the owner."]
    path = out_dir(env) / "nodetype_agreement.md"
    atomic_write_text(path, "\n".join(lines) + "\n")
    env.echo(f"  sign check {'PASS' if not nc.sign_bad else 'FAIL'}; King "
             f"{sum(pooled.king_ok['all maps'])}/{len(pooled.king_ok['all maps'])}; report {path}")
    return 0 if not nc.sign_bad else 1


# --- scan for a redraw / flip day --------------------------------------------------------------------------------

def first_with(days: list[date], probe, want: str) -> date | None:
    """The earliest day (calendar order) whose probe has `want` (redraw / flip); probe(d) -> {'redraw', 'flip'}."""
    for d in sorted(days):
        if want in probe(d):
            return d
    return None


def _probe_factory(env: Env):
    from nqbot.checks import session_build as sb
    from nqbot.features import opening_range as orm

    pl = sb.plan(env)
    cache: dict[date, dict] = {}

    def probe(d: date) -> set:
        if d not in cache:
            tape = st.load_tape(env.db, pl, "NQ", d)
            if tape is None:
                cache[d] = {"have": set(), "flips": [], "redrawn": False, "note": "no stored trades"}
            else:
                orr = orm.OpeningRange.build(tape, st.bars(tape, 1), env.features, env.cal)
                have = ({"redraw"} if orr.redrawn_1030 else set()) | ({"flip"} if orr.flips_at else set())
                cache[d] = {"have": have, "flips": list(orr.flips_at), "redrawn": bool(orr.redrawn_1030), "note": ""}
                del tape
        return cache[d]["have"]
    return probe, cache


def _damaged_open(dd) -> bool:
    if dd is None:
        return False
    for a, b in dd.rth_gap_minutes:
        if a.time() < READ_REDRAW and b.time() > time(9, 30):
            return True
    return dd.rth_dropped


def scan(env: Env) -> dict[str, Any]:
    from nqbot.checks import session_build as sb

    for d in SAMPLE_DAYS:
        require_development(d, env.settings)
    pl = sb.plan(env)
    planned = sb.planned_sessions(pl, env.cal)
    dev = sorted(d for d in planned if month_index(d, env.settings) in DEVELOPMENT)
    probe, cache = _probe_factory(env)
    for d in SAMPLE_DAYS:
        probe(d)
    damage = sb.load_damage(env)
    added: dict[str, str | None] = {}
    for want in ("redraw", "flip"):
        if any(want in cache[d]["have"] for d in SAMPLE_DAYS):
            added[want] = None
        else:
            hit = first_with(dev, probe, want)
            added[want] = None if hit is None else hit.isoformat()
    doc = {"sample": {d.isoformat(): {"redraw": cache[d]["redrawn"], "flips_et": [et(x, "%H:%M") for x in
                                                                                   cache[d]["flips"]]}
                      for d in SAMPLE_DAYS}, "added": added}
    lines = ["# Step 12 scan: 10:30 redraw and orientation flip (§5.8)", "",
             "The calendar and the fib rule only (`OpeningRange` on the stored trades), never outcomes.", "",
             "| day | 10:30 redraw | flips (minute after the settle, ET) |", "|---|---|---|"]
    for d in SAMPLE_DAYS:
        c = cache[d]
        lines.append(f"| {d} | {'yes' if c['redrawn'] else 'no'} | "
                     f"{', '.join(et(x, '%H:%M') for x in c['flips']) or '—'} |")
    lines.append("")
    for want in ("redraw", "flip"):
        a = added[want]
        if a is None and any(want in cache[d]["have"] for d in SAMPLE_DAYS):
            lines.append(f"- {want}: on a sample day, no day added.")
        elif a is None:
            lines.append(f"- {want}: no development day has one.")
        else:
            dd = damage.get(date.fromisoformat(a))
            flag = " **(damaged minutes 09:30-10:31: owner to decide whether to use it)**" if _damaged_open(dd) else ""
            lines.append(f"- {want}: none of the five has one; added the earliest development day with one: {a}{flag}.")
    atomic_write_text(out_dir(env) / "scan.md", "\n".join(lines) + "\n")
    atomic_write_text(out_dir(env) / "scan.json", json.dumps(doc, indent=1) + "\n")
    env.echo(f"  scan: added {added}; report {out_dir(env) / 'scan.md'}")
    return doc


def _scan_doc(env: Env) -> dict[str, Any]:
    p = out_dir(env) / "scan.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else scan(env)


def added_for(env: Env, day: date) -> str | None:
    if day in SAMPLE_DAYS:
        return None
    doc = _scan_doc(env)
    for want, d in doc["added"].items():
        if d == day.isoformat():
            return want
    raise Refused(f"{day} is neither a step-12 sample day nor a day the scan added ({doc['added']})")


# --- the day ---------------------------------------------------------------------------------------------------

def start_state(env: Env, day: date):
    """The feature build's stored state `day` starts from (the state after the previous planned session), its key."""
    from nqbot.checks import feature_build as fb
    from nqbot.checks import session_build as sb

    require_development(day, env.settings)
    doc = fb.load_plan(env)
    key = doc["key"]
    days = [date.fromisoformat(x) for x in doc["sessions"]]
    if day not in days:
        raise Refused(f"{day} is not a session of the feature build")
    src = fb.start_file(env, key, days, day)
    if not src.exists():
        raise Refused(f"{day}: the stored state {src} is missing")
    return sb.DayState.from_json(json.loads(src.read_text(encoding="utf-8"))), key


def build_from_state(env: Env, day: date):
    """`day` rebuilt exactly as the feature build did: the job's stored state after the previous planned session."""
    from nqbot.checks import feature_build as fb
    from nqbot.checks import session_build as sb

    state, key = start_state(env, day)
    pl = sb.plan(env)
    planned = sb.planned_sessions(pl, env.cal)
    built = sb.build_day(env, day, state, pl, planned, sb.load_damage(env))
    return built, fb.out_dir(env, key), key


def stored_events(base: Path, day: date) -> list[dict[str, Any]]:
    """The job's touch rows joined to its outcome rows (the structure labels) by event id."""
    import pyarrow.parquet as pq

    iso = day.isoformat()
    t = pq.read_table(base / "touches" / f"{iso}.parquet")
    o = pq.read_table(base / "outcomes" / f"{iso}.parquet").to_pylist()
    lab = {r["event_id"]: r for r in o if r.get("how") != "eod_day_type"}
    ncols = [c for c in t.column_names if c.startswith("n_") and c not in ("n_post_cross",)
             and not c.startswith("n_node_")]
    keep = ["event_id", "zone_id", "ts", "direction", "role", "price", "zone_lo", "zone_hi", "in_golden_zone",
            "golden_zone_part"] + ncols
    rows = t.select([c for c in keep if c in t.column_names]).to_pylist()
    out = []
    for r in rows:
        x = lab.get(r["event_id"])
        if x is None:
            raise Refused(f"{iso}: event {r['event_id']} has no stored label")
        types = [c[2:] for c in ncols if (r.get(c) or 0) > 0]
        if r.get("in_golden_zone"):
            types.append(f"golden_{r.get('golden_zone_part')}")
        out.append({**{k: r.get(k) for k in keep[:8]}, "types": types, "label": x["label"],
                    "t_resolved": x["t_resolved"], "how": x["how"], "run_pts": x.get("run_pts")})
    if len(out) != len(lab):
        raise Refused(f"{iso}: {len(out)} stored touch rows but {len(lab)} stored labels")
    return sorted(out, key=lambda r: r["ts"])


def check_events_match(built_ids: list[str], stored: list[dict[str, Any]], built_labels: dict[str, str]) -> None:
    s_ids = [r["event_id"] for r in stored]
    if sorted(built_ids) != sorted(s_ids):
        miss, extra = set(s_ids) - set(built_ids), set(built_ids) - set(s_ids)
        raise Refused(f"the rebuilt day's events differ from the stored build ({len(miss)} only stored, {len(extra)} "
                      f"only rebuilt): the build or the data changed since")
    bad = [r["event_id"] for r in stored if built_labels.get(r["event_id"]) != r["label"]]
    if bad:
        raise Refused(f"{len(bad)} rebuilt labels differ from the stored ones (e.g. {bad[0]})")


def event_hover(e: dict[str, Any]) -> str:
    return (f"{e['event_id']}<br>zone {e['zone_id']} [{e['zone_lo']:.2f}, {e['zone_hi']:.2f}]<br>"
            f"{', '.join(e['types']) or '—'}<br>{e['role']} ({'long' if e['direction'] > 0 else 'short'}) at "
            f"{e['price']:.2f}, {et(e['ts'])}<br>label <b>{e['label']}</b> ({e['how']}) resolved {et(e['t_resolved'])}")


LABEL_COLOR = {"held": "#1a9850", "broke": "#d73027", "timeout": "#7f7f7f"}


# --- plot data ---------------------------------------------------------------------------------------------------

@dataclass
class Line:
    name: str
    group: str
    t: np.ndarray        # ns
    y_on: np.ndarray     # NaN where not an active zone member
    y_off: np.ndarray    # NaN where active or not existing (crossed / frozen: drawn faint)


@dataclass
class Band:
    name: str
    group: str
    t: np.ndarray
    lo: np.ndarray
    hi: np.ndarray


@dataclass
class DayPlot:
    day: date
    symbol: str
    bars: dict                       # t, open, high, low, close (1-minute)
    lines: list = field(default_factory=list)
    bands: list = field(default_factory=list)
    marks: list = field(default_factory=list)      # (t_ns, label)
    events: list = field(default_factory=list)     # stored_events rows
    readings: list = field(default_factory=list)   # t_ns


GROUP_STYLE = {   # group -> (colour, line width, line style)
    "vwap18": ("#1f77b4", 1.6, "-"), "vwap18_band": ("#1f77b4", 0.8, ":"),
    "vwap0930": ("#ff7f0e", 1.6, "--"), "vwap0930_band": ("#ff7f0e", 0.8, ":"),
    "poc_daily": ("#9467bd", 1.4, "-"), "poc_weekly": ("#4b0082", 1.6, "-."),
    "va_daily": ("#9467bd", 0.9, "--"), "va_weekly": ("#4b0082", 0.9, "--"),
    "prev_profile": ("#8c564b", 1.3, "-."), "lvn_daily": ("#17becf", 0.8, "-"), "lvn_weekly": ("#0b6e78", 0.9, "-"),
    "session_hl": ("#2ca02c", 1.2, "-"), "ib": ("#e377c2", 1.6, "-"), "em": ("#bcbd22", 1.0, "--"),
    "retested": ("#b0b0b0", 0.7, "-."), "qqq_node": ("#3a7bd5", 1.0, "-"), "ndxp_node": ("#c2185b", 1.0, "-"),
    "qqq_king": ("#3a7bd5", 3.0, "-"), "ndxp_king": ("#c2185b", 3.0, "-"), "other": ("#555555", 0.8, "-"),
}
BAND_STYLE = {"single_print": ("#41ab5d", 0.28), "golden_shallow": ("#ffd700", 0.22), "golden_deep": ("#ff8c00", 0.22),
              "zone": ("#000000", 0.0), "qqq_node": ("#3a7bd5", 0.08), "ndxp_node": ("#c2185b", 0.08)}


def group_of(sub_type: str) -> str:
    if sub_type.startswith("vwap18"):
        return "vwap18" if sub_type == "vwap18" else "vwap18_band"
    if sub_type.startswith("vwap0930"):
        return "vwap0930" if sub_type == "vwap0930" else "vwap0930_band"
    if sub_type in ("prev_poc", "prev_vah", "prev_val"):
        return "prev_profile"
    if sub_type in STATIC_SUBTYPES:
        return "session_hl"
    if sub_type in ("ib_high", "ib_low"):
        return "ib"
    if sub_type.startswith("em_"):
        return "em"
    if sub_type == "retested_extreme":
        return "retested"
    return sub_type if sub_type in GROUP_STYLE or sub_type in BAND_STYLE else "other"


def _spans_mask(grid: np.ndarray, spans: list) -> np.ndarray:
    m = np.zeros(len(grid), bool)
    for a, z in spans:
        m |= (grid >= a) & (grid < z)
    return m


def plot_data(built, stored: list[dict[str, Any]], day: date, readings: list[Reading]) -> DayPlot:
    from nqbot.features.bands import values_at

    sf = built.sf
    cs = sf.c.cs_nq
    grid = np.arange(cs.start_ns, cs.end_ns + 1, PLOT_STEP_NS, dtype=np.int64)
    b = cs.bars_1m
    dp = DayPlot(day, cs.tape.symbol, {"t": b.start, "open": b.open, "high": b.high, "low": b.low, "close": b.close})
    for uid, x in sf.nq_levels.items():
        lo, hi = values_at(x.band, grid)
        c = (lo + hi) / 2
        on = _spans_mask(grid, x.spans) & np.isfinite(c)
        g = group_of(x.sub_type)
        if g == "single_print":
            dp.bands.append(Band(uid, g, grid, np.where(np.isfinite(lo), lo, np.nan), hi))
            continue
        if g in ("qqq_node", "ndxp_node"):
            dp.bands.append(Band(uid, g, grid, np.where(on, lo, np.nan), np.where(on, hi, np.nan)))
        dp.lines.append(Line(uid, g, grid, np.where(on, c, np.nan), np.where(~on & np.isfinite(c), c, np.nan)))
    for prof, name in ((cs.daily, "daily"), (cs.weekly, "weekly")):
        if prof is None or not len(prof.times):
            continue
        k = np.searchsorted(prof.times, grid, side="right") - 1
        ok = k >= 0
        for side in ("vah", "val"):
            v = np.array([getattr(s, side) for s in prof.snaps], float)
            y = np.where(ok, v[np.maximum(k, 0)], np.nan)
            dp.lines.append(Line(f"{name} {side.upper()}", f"va_{name}", grid, y, np.full(len(grid), np.nan)))
    sky = sf.c.sky
    if sky is not None:   # the confirmed King's band per map (display only), sampled every NODE_SAMPLE_MIN minutes
        samp = grid[::NODE_SAMPLE_MIN]
        for m in ("QQQ", "NDXP"):
            y = np.full(len(grid), np.nan)
            for j, t in enumerate(samp):
                ks = [x for x in sky.registry_at(int(t)) if x["map"] == m and x["node_type"] == "king"]
                if ks:
                    y[j * NODE_SAMPLE_MIN:(j + 1) * NODE_SAMPLE_MIN] = (ks[0]["band"][0] + ks[0]["band"][1]) / 2
            dp.lines.append(Line(f"{m} King", f"{m.lower()}_king", grid, y, np.full(len(grid), np.nan)))
    # merged zones (ZoneBook), outlines
    for zid in sf.book.zone_ids():
        lo = np.full(len(grid), np.nan)
        hi = np.full(len(grid), np.nan)
        bands = {u: x.band for u, x in sf.nq_levels.items()}
        for a, z, _mem in sf.book.segments(zid):
            for j in np.flatnonzero((grid >= a) & (grid < z)):
                e = sf.book.edges(zid, int(grid[j]), bands)
                if e is not None:
                    lo[j], hi[j] = e
        if np.isfinite(lo).any():
            dp.bands.append(Band(zid, "zone", grid, lo, hi))
    # golden zone along its drawing timeline
    orr = cs.opening
    if orr is not None:
        sh = [np.full(len(grid), np.nan) for _ in range(4)]
        for j, t in enumerate(grid):
            if t < orr.open_ns or t > orr.close_ns:
                continue
            d = orr.drawing_at(int(t))
            if d is None:
                continue
            (sh[0][j], sh[1][j]) = d.band(*orr.fib.fibs(gz.SHALLOW))
            (sh[2][j], sh[3][j]) = d.band(*orr.fib.fibs(gz.DEEP))
        dp.bands.append(Band("golden shallow 0.5–0.618", "golden_shallow", grid, sh[0], sh[1]))
        dp.bands.append(Band("golden deep 0.618–0.79", "golden_deep", grid, sh[2], sh[3]))
        o = orr.open_ns
        dp.marks.append((o + int(orr.fib.t_start * NS), "09:45 fib start"))
        dp.marks.append((o + int(orr.fib.t_lock * NS), "10:00 lock"))
        if orr.redrawn_1030:
            dp.marks.append((o + int(orr.fib.t_redraw * NS), "10:30 redraw"))
        for k, t in enumerate(orr.flips_at):
            dp.marks.append((int(t), f"flip {k + 1}"))
    dp.events = stored
    dp.readings = [st.et_ns(day, r.at) for r in readings]
    return dp


# --- rendering -----------------------------------------------------------------------------------------------------

def _to_dt(t: np.ndarray):
    return np.array([datetime.fromtimestamp(int(x) / NS, ET).replace(tzinfo=None) for x in t], dtype="datetime64[ns]")


def render_png(dp: DayPlot, path: Path, t0: int, t1: int, title: str, nodes: bool = False) -> None:
    """One panel: the chart levels, golden zone and single prints (nodes=False), or the QQQ / NDXP nodes, their
    Kings and the merged zones (nodes=True); price and the events on both."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sel = (dp.bars["t"] >= t0) & (dp.bars["t"] < t1)
    if not sel.any():
        return
    fig, ax = plt.subplots(figsize=(22, 12), dpi=110)
    bt = _to_dt(dp.bars["t"][sel])
    o, h, l_, c = (dp.bars[k][sel] for k in ("open", "high", "low", "close"))
    width = np.timedelta64(40, "s")
    up = c >= o
    ax.vlines(bt, l_, h, color=np.where(up, "#26a69a", "#ef5350"), linewidth=0.7, zorder=3)
    ax.bar(bt, np.where(np.abs(c - o) < 0.25, 0.25, c - o), bottom=o, width=width,
           color=np.where(up, "#26a69a", "#ef5350"), zorder=3)
    ylo, yhi = float(l_.min()), float(h.max())
    pad = (yhi - ylo) * 0.25 + 10
    ylo, yhi = ylo - pad, yhi + pad
    def keep(g: str) -> bool:
        return (g in NODE_GROUPS) == nodes

    for b in dp.bands:
        w = (b.t >= t0) & (b.t < t1)
        if not keep(b.group) or not w.any() or not np.isfinite(b.lo[w]).any():
            continue
        col, alpha = BAND_STYLE.get(b.group, ("#999999", 0.1))
        x = _to_dt(b.t[w])
        if b.group == "zone":
            ax.plot(x, b.lo[w], color="#000000", lw=0.5, alpha=0.6, drawstyle="steps-post", zorder=2)
            ax.plot(x, b.hi[w], color="#000000", lw=0.5, alpha=0.6, drawstyle="steps-post", zorder=2)
        else:
            ax.fill_between(x, b.lo[w], b.hi[w], step="post", color=col, alpha=alpha, lw=0, zorder=1)
    labelled = set()
    for ln in dp.lines:
        w = (ln.t >= t0) & (ln.t < t1)
        if not keep(ln.group) or not w.any():
            continue
        col, lw, ls_ = GROUP_STYLE.get(ln.group, GROUP_STYLE["other"])
        x = _to_dt(ln.t[w])
        for y, alpha in ((ln.y_on[w], 0.95), (ln.y_off[w], 0.25)):
            if not np.isfinite(y).any():
                continue
            yy = np.where((y >= ylo) & (y <= yhi), y, np.nan)
            if not np.isfinite(yy).any():
                continue
            lab = ln.group if (ln.group not in labelled and alpha > 0.5) else None
            labelled.add(ln.group) if lab else None
            ax.plot(x, yy, color=col, lw=lw, ls=ls_, alpha=alpha, drawstyle="steps-post", zorder=2, label=lab)
            if alpha > 0.5 and ln.group not in NO_LABEL:
                k = np.flatnonzero(np.isfinite(yy))[-1]
                txt = ln.name.split("-")[0] if not ln.group.endswith("_node") else ln.name.split("-")[0].upper() + \
                    " " + ln.name.rsplit("-", 1)[-1]
                ax.annotate(txt, (x[k], yy[k]), fontsize=6, color=col, xytext=(2, 0), textcoords="offset points")
    for t, lab in dp.marks:
        if t0 <= t < t1:
            ax.axvline(_to_dt(np.array([t]))[0], color="#e377c2", lw=0.8, ls="--", alpha=0.7)
            ax.annotate(lab, (_to_dt(np.array([t]))[0], yhi), fontsize=7, color="#e377c2", rotation=90, va="top")
    for t in dp.readings:
        if t0 <= t < t1:
            ax.axvline(_to_dt(np.array([t]))[0], color="#000000", lw=1.0, ls=":", alpha=0.6)
    for lab, mk in (("held", "^"), ("broke", "v"), ("timeout", "o")):
        ev = [e for e in dp.events if e["label"] == lab and t0 <= e["ts"] < t1]
        if ev:
            ax.scatter(_to_dt(np.array([e["ts"] for e in ev])), [e["price"] for e in ev], s=26, marker=mk,
                       c=LABEL_COLOR[lab], edgecolors="black", linewidths=0.4, zorder=5,
                       label=f"event {lab} ({len(ev)})")
    ax.set_ylim(ylo, yhi)
    ax.set_title(title)
    ax.grid(alpha=0.2)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.04), fontsize=8, ncol=10, framealpha=0.9)
    import matplotlib.dates as mdates
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    atomic_write_bytes(path, buf.getvalue())


def _compress(t: np.ndarray, y: np.ndarray) -> tuple[list, list]:
    """Step points only where the value changes (plotly line_shape 'hv'), None between runs."""
    xs, ys = [], []
    prev = None
    fin = np.isfinite(y)
    for j in range(len(y)):
        v = float(y[j]) if fin[j] else None
        if v != prev or j == len(y) - 1:
            if v is None and prev is not None:
                xs.append(datetime.fromtimestamp(int(t[j]) / NS, ET).replace(tzinfo=None))
                ys.append(prev)
            xs.append(datetime.fromtimestamp(int(t[j]) / NS, ET).replace(tzinfo=None))
            ys.append(v)
        prev = v
    return xs, ys


def render_html(dp: DayPlot, path: Path, title: str) -> None:
    import plotly.graph_objects as go

    fig = go.Figure()
    bt = [datetime.fromtimestamp(int(x) / NS, ET).replace(tzinfo=None) for x in dp.bars["t"]]
    fig.add_trace(go.Candlestick(x=bt, open=dp.bars["open"], high=dp.bars["high"], low=dp.bars["low"],
                                 close=dp.bars["close"], name=f"{dp.symbol} 1m", legendgroup="price"))
    for b in dp.bands:
        col, alpha = BAND_STYLE.get(b.group, ("#999999", 0.1))
        if not np.isfinite(b.lo).any():
            continue
        xs, lo = _compress(b.t, b.lo)
        _, hi = _compress(b.t, b.hi)
        if b.group == "zone":
            for ys in (lo, hi):
                fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", line=dict(color="black", width=0.6, shape="hv"),
                                         name="zones", legendgroup="zones", showlegend=False, hoverinfo="skip"))
            continue
        fig.add_trace(go.Scatter(x=xs, y=lo, mode="lines", line=dict(width=0, shape="hv"), showlegend=False,
                                 legendgroup=b.group, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=xs, y=hi, mode="lines", line=dict(width=0, shape="hv"), fill="tonexty",
                                 fillcolor=_rgba(col, max(alpha, 0.12)), name=b.group, legendgroup=b.group,
                                 showlegend=False, text=[b.name] * len(xs), hovertemplate="%{text}: %{y:.2f}"))
    shown = set()
    for ln in dp.lines:
        col, lw, ls_ = GROUP_STYLE.get(ln.group, GROUP_STYLE["other"])
        dash = {"-": "solid", "--": "dash", ":": "dot", "-.": "dashdot"}[ls_]
        for y, op in ((ln.y_on, 1.0), (ln.y_off, 0.3)):
            if not np.isfinite(y).any():
                continue
            xs, ys = _compress(ln.t, y)
            fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", opacity=op, name=ln.group, legendgroup=ln.group,
                                     showlegend=ln.group not in shown and op == 1.0,
                                     line=dict(color=col, width=lw, dash=dash, shape="hv"),
                                     text=[ln.name + ("" if op == 1.0 else " (not active)")] * len(xs),
                                     hovertemplate="%{text}: %{y:.2f}<extra></extra>"))
            if op == 1.0:
                shown.add(ln.group)
    for lab, sym in (("held", "triangle-up"), ("broke", "triangle-down"), ("timeout", "circle")):
        ev = [e for e in dp.events if e["label"] == lab]
        if ev:
            fig.add_trace(go.Scatter(
                x=[datetime.fromtimestamp(int(e["ts"]) / NS, ET).replace(tzinfo=None) for e in ev],
                y=[e["price"] for e in ev], mode="markers", name=f"event {lab} ({len(ev)})",
                marker=dict(color=LABEL_COLOR[lab], symbol=sym, size=9, line=dict(color="black", width=0.5)),
                text=[event_hover(e) for e in ev], hovertemplate="%{text}<extra></extra>"))
    for t, lab in dp.marks:
        x = datetime.fromtimestamp(int(t) / NS, ET).replace(tzinfo=None)
        fig.add_vline(x=x, line=dict(color="#e377c2", dash="dash", width=1))
        fig.add_annotation(x=x, y=1, yref="paper", text=lab, showarrow=False, textangle=-90, font=dict(size=9))
    for t in dp.readings:
        fig.add_vline(x=datetime.fromtimestamp(int(t) / NS, ET).replace(tzinfo=None),
                      line=dict(color="black", dash="dot", width=1))
    fig.update_layout(title=title, xaxis_rangeslider_visible=False, height=900, hovermode="closest",
                      template="plotly_white", legend=dict(font=dict(size=10)))
    atomic_write_text(path, fig.to_html(include_plotlyjs="cdn", full_html=True))


def _rgba(hex_col: str, a: float) -> str:
    h = hex_col.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{a})"


# --- day command -------------------------------------------------------------------------------------------------

def day_cmd(env: Env, day: date) -> dict[str, Any]:
    t0 = _time.monotonic()
    require_development(day, env.settings)
    why = added_for(env, day)
    env.echo(f"step 12 {day}: rebuilding from the feature build's stored state (stored data only, $0, no lock)")
    built, base, key = build_from_state(env, day)
    sf = built.sf
    cs = sf.c.cs_nq
    stored = stored_events(base, day)
    check_events_match([e.event_id for e in sf.events], stored, {k: v.label for k, v in sf.structure_labels().items()})
    orr = cs.opening
    plan = reading_plan(bool(orr.redrawn_1030) if orr else False, list(orr.flips_at) if orr else [], why)
    rows = [r for rd in plan for r in level_rows(cs, day, rd.at, rd.scope)]
    dd = day_dir(env, day)
    csv_path = dd / f"levels_{day}.csv"
    write_csv(csv_path, rows)
    dp = plot_data(built, stored, day, plan)
    o, c = st.rth_open_ns(day), st.rth_close_ns(day, env.cal)
    title = f"{day} {dp.symbol}: every level / zone, golden zone, QQQ & NDXP nodes (display only), events by label"
    z0, z1 = st.et_ns(day, OPEN_ZOOM[0]), st.et_ns(day, OPEN_ZOOM[1])
    paths = {"rth": dd / f"plot_{day}_rth.png", "open": dd / f"plot_{day}_open.png",
             "rth_nodes": dd / f"plot_{day}_rth_nodes.png", "open_nodes": dd / f"plot_{day}_open_nodes.png",
             "html": dd / f"plot_{day}.html"}
    lv, nd = " — chart levels, golden zone, single prints", " — QQQ / NDXP nodes (display only), Kings, merged zones"
    render_png(dp, paths["rth"], o, c, f"{day} {dp.symbol} RTH" + lv)
    render_png(dp, paths["open"], z0, z1, f"{day} {dp.symbol} 09:30-10:45" + lv)
    render_png(dp, paths["rth_nodes"], o, c, f"{day} {dp.symbol} RTH" + nd, nodes=True)
    render_png(dp, paths["open_nodes"], z0, z1, f"{day} {dp.symbol} 09:30-10:45" + nd, nodes=True)
    render_html(dp, paths["html"], title)
    report = _day_report(env, day, built, key, plan, rows, stored, paths, csv_path, why)
    if day in LVN_DAYS:   # the LVN list from the same build (no second rebuild)
        lvn_cmd(env, day, built)
    env.echo(f"  {len(rows)} values to read, {len(stored)} events; {_time.monotonic() - t0:.0f} s")
    for p in (csv_path, *paths.values(), report):
        env.echo(f"  {p}")
    return {"rows": len(rows), "events": len(stored), "paths": [str(p) for p in (csv_path, *paths.values(), report)]}


def _day_report(env, day, built, key, plan, rows, stored, paths, csv_path, why) -> Path:
    sf = built.sf
    cs = sf.c.cs_nq
    labs = {k: sum(1 for e in stored if e["label"] == k) for k in ("held", "broke", "timeout")}
    rth = [e for e in stored if st.rth_open_ns(day) <= e["ts"] < st.rth_close_ns(day, env.cal)]
    lines = [f"# Step 12 day {day} ({cs.tape.symbol})", "",
             f"Rebuilt from the feature build's stored state (key {key}); the rebuilt events and labels equal the "
             f"stored ones. {'Sample day.' if why is None else f'Added {why} day (IB and fib only).'}", "",
             f"- Reading table (fill in `deepcharts_value`): `{csv_path}` ({len(rows)} values)",
             f"- Plots: chart levels `{paths['rth']}`, `{paths['open']}`; nodes and zones `{paths['rth_nodes']}`, "
             f"`{paths['open_nodes']}`; everything, interactive (hover for exact prices and event details, click the "
             f"legend to hide a layer) `{paths['html']}`",
             "- Events are marked by structure label: ▲ held, ▼ broke, ● timeout.",
             "- Readings: " + "; ".join(f"{r.at:%H:%M} ({r.why})" for r in plan), "",
             f"## Events: {len(stored)} ({len(rth)} in RTH); held {labs['held']}, broke {labs['broke']}, timeout "
             f"{labs['timeout']}", "",
             "Structure labels (§7.1) for the owner to judge on the chart; development day only.", "",
             "| # | event id | touch ET | zone | lo–hi | level types | dir | label | how | resolved ET | run pts |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, e in enumerate(stored, 1):
        lines.append(f"| {k} | {e['event_id']} | {et(e['ts'])} | {e['zone_id']} | {e['zone_lo']:.2f}–{e['zone_hi']:.2f}"
                     f" | {', '.join(e['types'])} | {'L' if e['direction'] > 0 else 'S'} | {e['label']} | {e['how']} | "
                     f"{et(e['t_resolved'])} | {_num(e.get('run_pts'))} |")
    path = day_dir(env, day) / f"day_{day}.md"
    atomic_write_text(path, "\n".join(lines) + "\n")
    return path


def lvn_cmd(env: Env, day: date, built=None) -> Path:
    require_development(day, env.settings)
    if built is None:
        built, _, _ = build_from_state(env, day)
    cs = built.sf.c.cs_nq
    o, c = st.rth_open_ns(day), st.rth_close_ns(day, env.cal)
    spans = lvn_spans(cs.levels, o, c, cs.end_ns)
    hist = lifetime_histogram(spans)
    f = env.features
    dd = day_dir(env, day)
    rows = [{**s, "first_et": et(s["first_ns"]), "last_et": et(s["last_ns"]),
             "stopped_et": "open at session end" if s["stop_ns"] is None else et(s["stop_ns"])} for s in spans]
    cols = ("profile", "identity", "level_id", "price_first", "price_min", "price_max", "first_et", "last_et",
            "stopped_et", "lifetime_min", "instants", "rth")
    write_csv(dd / f"lvn_{day}.csv", rows, cols)
    lines = [f"# Step 12 LVN lifetimes {day} ({cs.tape.symbol})", "",
             f"Every daily and weekly LVN span of the CME day (18:00 ET of the day before … 17:00), profile instants "
             f"every {f['profile_update_sec']} s; `lvn_smooth_ticks` {f['lvn_smooth_ticks']}, `lvn_max_pct` "
             f"{f['lvn_max_pct']}, identity rounded to {f['lvn_merge_pts']} pts (F3.4). A span = consecutive instants "
             "it qualifies at; stopped = the first instant it no longer does; lifetime = stopped (or the session end) − "
             "first. The weekly profile's spans are per session (one CME day here). Report only: a mismatch with "
             "Deepcharts goes to §13 (owner 2026-10-31).", "",
             "## Lifetime histogram (spans)", "", "| lifetime | daily | weekly |", "|---|---|---|"]
    for lab in hist["daily"]:
        lines.append(f"| {lab} | {hist['daily'][lab]} | {hist['weekly'][lab]} |")
    for prof in ("daily", "weekly"):
        ss = [r for r in rows if r["profile"] == prof]
        lines += ["", f"## {prof} LVNs ({len(ss)} spans)", "",
                  "| identity | price (first) | price range | first | last | stopped | lifetime min | instants | RTH |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for r in ss:
            lines.append(f"| {r['identity']} | {r['price_first']:.2f} | {r['price_min']:.2f}–{r['price_max']:.2f} | "
                         f"{r['first_et']} | {r['last_et']} | {r['stopped_et']} | {r['lifetime_min']:.0f} | "
                         f"{r['instants']} | {'yes' if r['rth'] else ''} |")
    _lvn_png(cs, spans, dd / f"lvn_{day}.png", f"{day} LVN spans (daily / weekly) over NQ")
    path = dd / f"lvn_{day}.md"
    atomic_write_text(path, "\n".join(lines) + "\n")
    env.echo(f"  {len(spans)} LVN spans; {path}")
    return path


def _lvn_png(cs, spans, path: Path, title: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    b = cs.bars_1m
    fig, ax = plt.subplots(figsize=(22, 10), dpi=110)
    ax.plot(_to_dt(b.start), b.close, color="#444444", lw=0.6, label="NQ 1m close")
    for s in spans:
        a = s["first_ns"]
        z = s["stop_ns"] or cs.end_ns
        col = "#17becf" if s["profile"] == "daily" else "#0b6e78"
        lw = 0.6 if s["lifetime_min"] <= 2 else (1.4 if s["lifetime_min"] <= 30 else 2.6)
        ax.plot(_to_dt(np.array([a, z])), [s["price_first"]] * 2, color=col, lw=lw, alpha=0.85)
    ax.set_title(title + " (thin: ≤ 2 min, medium: ≤ 30 min, thick: longer; light = daily, dark = weekly)")
    import matplotlib.dates as mdates
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.grid(alpha=0.2)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    atomic_write_bytes(path, buf.getvalue())


# --- single prints (owner 2026-11-02: counts only) --------------------------------------------------------------

@dataclass
class SpSession:
    day: date
    carried_in: int            # unfilled runs at the session start
    carried_pts: float         # their unfilled points
    new: int                   # runs formed this session
    new_sizes: list            # their sizes in points
    touches: int               # standalone F5.6 touches of the single-print bands (one-member zones)
    touches_rth: int


def new_runs(sp, day: date) -> list[float]:
    """Sizes (points) of the runs formed on `day`: the sum of a run's pieces at its formation."""
    by: dict[str, list] = {}
    for lv in sp.levels.values():
        if lv.session == day:
            by.setdefault(lv.run_id, []).append(lv)
    out = []
    for lvs in by.values():
        t0 = min(lv.created_ns for lv in lvs)
        out.append(float(sum(lv.hi[0] - lv.lo[0] for lv in lvs if lv.created_ns == t0)))
    return out


def sp_touches(tape, sp, features: dict, cal) -> tuple[int, int]:
    """Standalone touches of the single-print bands by the F5.6 rule (each level its own zone; no merging)."""
    from nqbot.features import level_state as ls
    from nqbot.features import registry as rg
    from nqbot.features import touches as tch
    from nqbot.features.bands import StepBand
    from nqbot.features.session_features import FixedBook

    end = st.cme_end_ns(tape.day)
    bars_1m, bars_flip = st.bars(tape, 1), st.bars(tape, features["flip_bar_minutes"])
    hw = float(features["chart_level_half_width"]["single_print"])
    levels = []
    for lv in sp.levels.values():
        band = StepBand(tape, np.asarray(lv.times, dtype=np.int64), np.asarray(lv.lo) - hw, np.asarray(lv.hi) + hw,
                        lv.end_ns)
        stop = min(lv.end_ns or end, end)
        state = ls.LevelState(f"sp-{lv.level_id}", ls.SINGLE_PRINT, tape, band,
                              ls.Params.from_features(features, "single_print"), bars_1m, bars_flip, end_ns=stop).run()
        levels.append(rg.RegLevel(f"sp-{lv.level_id}", f"sp-{lv.level_id}", "single_print", "single_print",
                                  "volume_profile", "NQ", ls.SINGLE_PRINT, True, band, state, None,
                                  spans=rg.active_spans(state, band, end)))
    levels = [x for x in levels if x.spans]
    if not levels:
        return 0, 0
    book = FixedBook(levels, end)
    tt = tch.ZoneTouches(tape, book, {x.uid: x for x in levels}, float(features["zone_proximity"]),
                         float(features["rearm_dist"])).run().touches
    o, c = st.rth_open_ns(tape.day), st.rth_close_ns(tape.day, cal)
    return len(tt), sum(1 for x in tt if o <= x.t < c)


def single_prints(env: Env) -> Path:
    """Single prints over months 1-6 at sp_min_ticks 4 / 6 / 8, chained from the same start as the feature build's
    warm-up (an empty carry `warmup_sessions` sessions before month 1), roll-shifted like the carry (F0.4). The
    configured value reproduces the build's carried single prints at every session (checked against the stored
    states; refuses if not)."""
    import pyarrow.parquet as pq

    from nqbot.checks import feature_build as fb
    from nqbot.checks import session_build as sb
    from nqbot.features import chart_levels as cl
    from nqbot.features import tpo
    from nqbot.warmup import warmup_sessions

    f, cal = env.features, env.cal
    doc = fb.load_plan(env)
    key = doc["key"]
    base = fb.out_dir(env, key)
    pl = sb.plan(env)
    planned = sb.planned_sessions(pl, cal)
    dev = [d for d in planned if month_index(d, env.settings) in DEVELOPMENT]
    first = date.fromisoformat(doc["sessions"][0])
    warm = [d for d in planned if d < first][-warmup_sessions(f):]
    rolls = env.db.rolls()
    cur = int(f["sp_min_ticks"])
    variants = sorted(set(SP_VARIANTS) | {cur})
    carried: dict[int, list] = {n: [] for n in variants}
    contract: str | None = None
    res: dict[int, list[SpSession]] = {n: [] for n in variants}
    build_touch: dict[date, tuple[int, int]] = {}
    t0 = _time.monotonic()
    env.echo(f"step 12 single prints: {len(warm)} warm-up + {len(dev)} development sessions, sp_min_ticks "
             f"{variants} (configured {cur}); stored data only")
    for k, d in enumerate(warm + dev):
        tape = st.load_tape(env.db, pl, "NQ", d)
        if tape is None:
            env.echo(f"  {d}: no stored trades, skipped")
            continue
        if contract is not None and tape.symbol != contract:
            spread = cl.roll_spread_of("NQ", d, rolls)
            if spread is None:
                raise Refused(f"{d}: roll {contract} -> {tape.symbol} without a recorded spread")
            for n in variants:
                for r in carried[n]:
                    r.shift(spread)
        contract = tape.symbol
        in_dev = d in set(dev)
        for n in variants:
            runs = [tpo.Run.from_json(r.to_json()) for r in carried[n]]
            c_in = len(runs)
            c_pts = float(sum(r.width * sum(1 for x in r.filled if not x) for r in runs))
            sp = tpo.SinglePrints(f["tpo_tick_pts"], n, f["max_zone_width"]).run_session(
                tape, d, runs, end_ns=st.cme_end_ns(d))
            carried[n] = sp.carry()
            if in_dev:
                tt, tr = sp_touches(tape, sp, f, cal)
                res[n].append(SpSession(d, c_in, c_pts, len(new_runs(sp, d)), new_runs(sp, d), tt, tr))
        if in_dev:   # the configured value must reproduce the stored build's carry
            sp_path = fb.state_file(env, key, d)
            want = json.loads(sp_path.read_text(encoding="utf-8"))["nq"]["single_prints"]
            got = [r.to_json() for r in carried[cur]]
            if json.dumps(want, sort_keys=True) != json.dumps(got, sort_keys=True):
                raise Refused(f"{d}: sp_min_ticks {cur} does not reproduce the stored build's single prints "
                              f"({len(got)} vs {len(want)} runs): the chain differs from the build")
            tab = pq.read_table(base / "touches" / f"{d.isoformat()}.parquet", columns=["ts", "n_single_print"])
            ts = tab.column("ts").to_numpy()
            ns = np.nan_to_num(tab.column("n_single_print").to_numpy(zero_copy_only=False).astype(float))
            o, c = st.rth_open_ns(d), st.rth_close_ns(d, cal)
            build_touch[d] = (int((ns > 0).sum()), int(((ns > 0) & (ts >= o) & (ts < c)).sum()))
        del tape
        if (k + 1) % 10 == 0:
            env.echo(f"  {k + 1}/{len(warm) + len(dev)} sessions ({_time.monotonic() - t0:.0f} s)")
    return _sp_report(env, res, build_touch, variants, cur)


def _q(xs: list[float], q: float) -> float | None:
    return float(np.quantile(xs, q)) if xs else None


def _sp_report(env: Env, res: dict, build_touch: dict, variants: list[int], cur: int) -> Path:
    f = env.features
    rows = []
    for n in variants:
        for s in res[n]:
            bt = build_touch.get(s.day, (None, None))
            rows.append({"sp_min_ticks": n, "day": s.day.isoformat(), "carried_in": s.carried_in,
                         "carried_unfilled_pts": s.carried_pts, "new": s.new,
                         "new_sizes_pts": " ".join(f"{x:g}" for x in s.new_sizes), "touches_standalone": s.touches,
                         "touches_standalone_rth": s.touches_rth,
                         "build_events_with_sp": bt[0] if n == cur else "", "build_events_with_sp_rth":
                         bt[1] if n == cur else ""})
    write_csv(out_dir(env) / "single_prints.csv", rows, tuple(rows[0]) if rows else ("day",))
    nses = len(res[cur])
    lines = ["# Step 12 single prints (months 1-6, counts only)", "",
             f"{nses} development sessions; TPO periods of 30 min from 18:00 ET, bins of {f['tpo_tick_pts']} pt, a "
             f"run = at least `sp_min_ticks` consecutive single bins of one period (F3.5), `max_zone_width` "
             f"{f['max_zone_width']}. Configured `sp_min_ticks` = {cur} (not changed here, owner 2026-11-02); "
             f"{', '.join(map(str, variants))} side by side, each chained from the build's warm-up start and "
             f"roll-shifted; {cur} reproduces the stored build's carried single prints at every session (checked). "
             "No outcomes or hold rates.", "",
             "Events touching a single-print zone: **build** = the feature build's own events (zones merged with every "
             "other level) with a single-print member; **standalone** = the F5.6 touch rule run on each variant's "
             "single-print bands alone (each its own zone, no merging), so the variants compare like for like. The two "
             "counts differ because in the build a single print is one member of a merged zone (one touch per zone, "
             "the zone's edges and re-arm set by all its members).", "",
             f"**Units:** `sp_min_ticks` counts TPO bins, and a bin is `tpo_tick_pts` = {f['tpo_tick_pts']} pt, so "
             f"{', '.join(map(str, variants))} bins = {', '.join(f'{n * f['tpo_tick_pts']:g}' for n in variants)} "
             f"points ({', '.join(f'{round(n * f['tpo_tick_pts'] / TICK)}' for n in variants)} NQ ticks). Counts "
             "cover the whole CME day (18:00-17:00 ET, 46 periods), not only RTH; sizes are a run's full height at "
             "its formation, before `max_zone_width` splits it into levels.", "",
             "| sp_min_ticks | new per session: median (min–max) | sessions with none | new sizes pts: median / p90 / "
             "max | carried in at session start: median (min–max) | carried unfilled pts: median | standalone touches "
             "per session: median (total) | RTH: median (total) | build events with a single print: median (total) |",
             "|---|---|---|---|---|---|---|---|---|"]
    for n in variants:
        ss = res[n]
        new = [s.new for s in ss]
        sizes = [x for s in ss for x in s.new_sizes]
        car = [s.carried_in for s in ss]
        tt = [s.touches for s in ss]
        tr = [s.touches_rth for s in ss]
        bt = [build_touch[s.day][0] for s in ss] if n == cur else []
        lines.append(f"| {n}{' (configured)' if n == cur else ''} | {np.median(new):g} ({min(new)}–{max(new)}) | "
                     f"{sum(1 for x in new if x == 0)} | {_fmtq(sizes, 0.5)} / {_fmtq(sizes, 0.9)} / "
                     f"{_fmtq(sizes, 1.0)} | {np.median(car):g} ({min(car)}–{max(car)}) | "
                     f"{np.median([s.carried_pts for s in ss]):g} | {np.median(tt):g} ({sum(tt)}) | "
                     f"{np.median(tr):g} ({sum(tr)}) | "
                     + (f"{np.median(bt):g} ({sum(bt)})" if bt else "—") + " |")
    lines += ["", "## Size distribution of new single prints (points)", "",
              "| sp_min_ticks | count | p10 | p25 | median | p75 | p90 | max |", "|---|---|---|---|---|---|---|---|"]
    for n in variants:
        sizes = [x for s in res[n] for x in s.new_sizes]
        lines.append(f"| {n} | {len(sizes)} | " + " | ".join(_fmtq(sizes, q) for q in (0.1, 0.25, 0.5, 0.75, 0.9, 1.0))
                     + " |")
    lines += ["", f"Per-session values: `{out_dir(env) / 'single_prints.csv'}`."]
    path = out_dir(env) / "single_prints.md"
    atomic_write_text(path, "\n".join(lines) + "\n")
    env.echo(f"  report {path}")
    return path


def _fmtq(xs, q) -> str:
    v = _q(xs, q)
    return "—" if v is None else f"{v:g}"


# --- all -------------------------------------------------------------------------------------------------------

def _child(argv: list[str]) -> int:
    code = "import sys; from nqbot.cli import main; sys.exit(main(sys.argv[1:]))"
    return subprocess.run([sys.executable, "-c", code, *argv], env={**os.environ, "PYTHONUTF8": "1"}).returncode


def days_to_read(env: Env) -> list[date]:
    doc = _scan_doc(env)
    extra = sorted({date.fromisoformat(d) for d in doc["added"].values() if d})
    return list(SAMPLE_DAYS) + [d for d in extra if d not in SAMPLE_DAYS]


def all_cmd(env: Env) -> int:
    scan(env)
    days = days_to_read(env)
    failed = []
    for d in days:
        env.echo(f"--- {d}")
        if _child(["step12", "day", d.isoformat()]) != 0:
            failed.append(f"day {d}")
    for d in LVN_DAYS:   # `day` writes the LVN list of these days; only one not read above needs its own run
        if d not in days and _child(["step12", "lvn", d.isoformat()]) != 0:
            failed.append(f"lvn {d}")
    nodetype_report(env)
    if _child(["step12", "single-prints"]) != 0:
        failed.append("single-prints")
    summary(env, days)
    if failed:
        env.echo(f"  FAILED: {', '.join(failed)}")
        return 1
    return 0


def summary(env: Env, days: list[date]) -> Path:
    f = env.features
    lines = ["# Step 12 visual check: outputs", "", "SPEC §12 step 12 (✅ gate). Development months only. For each day "
             "the owner reads Deepcharts at the same ET times, fills `deepcharts_value` in the CSV (an LVN Deepcharts "
             f"doesn't have: `{NOT_PRESENT}`; a Deepcharts LVN the bot lacks: add a row with the group `lvn_daily` / "
             "`lvn_weekly`, the reading time and the price, bot value empty), then `uv run nqbot step12 compare`.", "",
             "## Deepcharts settings to match", "",
             "- Contract: the session's front contract by the volume rule (F0.4): NQH6 on 03-06 and 03-11, NQM6 from "
             "03-17 (the first NQM6 session); the day reports name it.",
             "- Daily profile and the 18:00 VWAP: the CME day from 18:00 ET; the 09:30 VWAP from 09:30 ET; bands ±1σ / "
             "±2σ of the volume-weighted price (F3.1).",
             "- Weekly profile: from Sunday 18:00 ET, one contract only (in the roll week from the roll day's session, "
             "03-16 18:00 ET).",
             f"- Value area {int(round(100 * 0.70))}% (F3.3), 1-tick (0.25) price grouping for the volume profile; TPO "
             f"periods 30 min from 18:00 ET, {f['tpo_tick_pts']}-pt bins for single prints (F3.5).",
             f"- Previous-session profile anchor: `{f['prev_profile_anchor']}` (F3.2b).",
             "- Times: ET. A reading uses data up to that minute (e.g. 10:01 = after the 10:00 bar closed).", "",
             "## Files", ""]
    for d in days:
        dd = day_dir(env, d)
        lines.append(f"- {d}: `{dd / f'levels_{d}.csv'}`, `{dd / f'plot_{d}_rth.png'}`, `{dd / f'plot_{d}_open.png'}`, "
                     f"`{dd / f'plot_{d}.html'}`, `{dd / f'day_{d}.md'}`")
    for d in LVN_DAYS:
        dd = day_dir(env, d)
        lines.append(f"- LVN lifetimes {d}: `{dd / f'lvn_{d}.md'}`, `{dd / f'lvn_{d}.csv'}`, `{dd / f'lvn_{d}.png'}`")
    lines += [f"- Scan: `{out_dir(env) / 'scan.md'}`", f"- Node-type agreement: `{out_dir(env) / 'nodetype_agreement.md'}`",
              f"- Single prints: `{out_dir(env) / 'single_prints.md'}`"]
    path = out_dir(env) / "summary.md"
    atomic_write_text(path, "\n".join(lines) + "\n")
    env.echo(f"  summary {path}")
    return path


# --- events breakdown (owner 2026-11-02: report only, months 1-6, built data, no definition changes) ---------------

LEVEL_GROUPS = {   # zone makeup groups of the breakdown (the touch rows' n_<sub-type> member counts)
    "LVN": ("lvn_daily", "lvn_weekly"),
    "single print": ("single_print",),
    "VWAP band": tuple(f"vwap{a}_{s}{k}" for a in ("18", "0930") for s in ("up", "dn") for k in (1, 2)),
    "VWAP line": ("vwap18", "vwap0930"),
    "GEX node": ("qqq_node", "ndxp_node"),
    "highs/lows": ("on_high", "on_low", "pdh", "pdl", "prior_rth_high", "prior_rth_low", "prior_week_high",
                   "prior_week_low", "ib_high", "ib_low", "retested_extreme"),
    "POC / value area": ("poc_daily", "poc_weekly", "prev_poc", "prev_vah", "prev_val"),
    "expected move": ("em_up1", "em_up2", "em_dn1", "em_dn2"),
}
WIDTH_BUCKETS = (0, 10, 20, 30, 45, 60)       # zone width buckets (pts), the last open-ended
LVN_YOUNG_MIN = 5                             # the owner's "LVN existed < 5 minutes"
LVN_AGE_BUCKETS = ((0, 1), (1, 2), (2, 5), (5, 15), (15, 60), (60, 240), (240, None))   # minutes
HELD_PATH = ("never entered", "stayed inside", "left on the near side", "went past the far edge")
EVENT_COLS = ["event_id", "ts", "direction", "zone_lo", "zone_hi", "in_golden_zone"]


def zone_groups(row: dict[str, Any]) -> set[str]:
    out = {g for g, subs in LEVEL_GROUPS.items() if any((row.get(f"n_{s}") or 0) > 0 for s in subs)}
    if row.get("in_golden_zone"):
        out.add("golden zone")
    return out


def held_path_class(direction: int, lo: float, hi: float, prices) -> str:
    """The path from the touch trade through the trade that resolved the hold, against the zone's edges at the touch:
    never entered the zone; entered and stayed inside [lo, hi]; left back out on the near (approach) side; or traded
    past the far edge at some point after entering (and came back without a settled cross)."""
    p = np.asarray(prices, float)
    if not len(p):
        return HELD_PATH[0]
    near_out = p > hi if direction > 0 else p < lo
    far_out = p < lo if direction > 0 else p > hi
    inside = ~near_out
    if not inside.any():
        return HELD_PATH[0]
    k = int(np.flatnonzero(inside)[0])
    if far_out[k:].any():
        return HELD_PATH[3]
    if near_out[k:].any():
        return HELD_PATH[2]
    return HELD_PATH[1]


def depth_into_zone(direction: int, lo: float, hi: float, extreme: float) -> tuple[float, float | None]:
    """(points, share of the width) price went past the near edge before the label resolved: < 0 never entered,
    0-1 inside, > 1 past the far edge. A long (floor) is approached from above: near edge hi."""
    d = (hi - extreme) if direction > 0 else (extreme - lo)
    w = hi - lo
    return float(d), (float(d / w) if w > 0 else None)


def profile_lvn_spans(prof) -> list[dict[str, Any]]:
    """LVN spans of a DevelopingProfile, grouped as ChartSession._profile_levels does (one per identity and
    contiguous run of qualifying instants; `stop` = the first instant it no longer qualifies, None at the end)."""
    out: list[dict[str, Any]] = []
    open_: dict[float, list] = {}
    for t, s in zip(prof.times.tolist(), prof.snaps):
        now = dict(s.lvns)
        for ident in list(open_):
            if ident not in now:
                steps = open_.pop(ident)
                out.append({"identity": ident, "times": [x[0] for x in steps], "prices": [x[1] for x in steps],
                            "stop": t})
        for ident, price in now.items():
            open_.setdefault(ident, []).append((t, price))
    for ident, steps in open_.items():
        out.append({"identity": ident, "times": [x[0] for x in steps], "prices": [x[1] for x in steps], "stop": None})
    return out


def lvn_ages_at(spans: list[dict[str, Any]], t: int, lo: float, hi: float, hw: float) -> list[float]:
    """Minutes each LVN whose band (its price at the last instant <= t, ± hw) overlaps [lo, hi] at t had been
    qualifying."""
    ages = []
    for s in spans:
        ts = s["times"]
        if ts[0] > t or (s["stop"] is not None and s["stop"] <= t):
            continue
        k = int(np.searchsorted(ts, t, side="right")) - 1
        p = s["prices"][k]
        if p - hw <= hi and p + hw >= lo:
            ages.append((t - ts[0]) / MIN_NS)
    return ages


def _session_lvn_spans(env: Env, d: date, tape, state_json: dict) -> dict[str, list]:
    """The day's daily and weekly LVN spans, from the stored state before it (the build's profile code and inputs)."""
    from nqbot.features import chart_levels as cl
    from nqbot.features import profile as pf
    from nqbot.features.carry import CarryState

    f = env.features
    carry = CarryState.from_json(state_json["nq"])
    cl.start_session(carry, d, tape.symbol, cl.roll_spread_of("NQ", d, env.db.rolls()))
    lvn = {k: f[k] for k in ("lvn_smooth_ticks", "lvn_gauss_trunc_sigma", "lvn_max_pct", "lvn_merge_pts")}
    s, e = st.cme_start_ns(d), st.cme_end_ns(d)
    upd = f["profile_update_sec"]
    daily = pf.DevelopingProfile.build("daily", tape, s, s, e, upd, lvn)
    carried = {int(k): v for k, v in carry.weekly_volume.items()} or None
    anchor = carry.weekly_anchor_ns if carry.weekly_anchor_ns is not None else s
    weekly = pf.DevelopingProfile.build("weekly", tape, anchor, s, e, upd, lvn, carried)
    return {"daily": profile_lvn_spans(daily), "weekly": profile_lvn_spans(weekly)}


def _check_lvn_spans(env: Env, d: date, spans: dict[str, list]) -> bool:
    """Against the full day build's LVN list (`step12 lvn`) where one exists: the same spans (profile, identity,
    first and stop instants). False if there is no list for that day."""
    p = day_dir(env, d) / f"lvn_{d}.csv"
    if not p.exists():
        return False
    want = {(r["profile"], r["identity"], r["first_et"], r["stopped_et"]) for r in read_csv(p)}
    got = {(prof, f"{s['identity']:g}", et(s["times"][0]), "open at session end" if s["stop"] is None else
            et(s["stop"])) for prof, ss in spans.items() for s in ss}
    if want != got:
        raise Refused(f"{d}: the LVN spans rebuilt for the breakdown differ from the day build's ({len(want ^ got)} "
                      "spans): the breakdown's LVN ages would be wrong")
    return True


def event_record(r: dict[str, Any], x: dict[str, Any], rth: bool, prices, spans: dict | None,
                 hw: float) -> dict[str, Any]:
    """One event of the breakdown: its touch row `r`, its outcome row `x`, the trades from the touch through the
    resolution (`prices`, held events only) and the session's LVN spans."""
    lo, hi, dirn, t = float(r["zone_lo"]), float(r["zone_hi"]), int(r["direction"]), int(r["ts"])
    g = zone_groups(r)
    dp, share = depth_into_zone(dirn, lo, hi, float(x["extreme"]))
    e = {"rth": rth, "groups": g, "width": hi - lo, "label": x["label"], "depth": dp, "depth_share": share,
         "path": None, "lvn_ages": None, "lvn_only": g == {"LVN"}}
    if x["label"] == "held":
        e["path"] = held_path_class(dirn, lo, hi, prices)
    if "LVN" in g and spans is not None:
        ages: list[float] = []
        for prof in ("daily", "weekly"):
            if (r.get(f"n_lvn_{prof}") or 0) > 0:
                ages += lvn_ages_at(spans[prof], t, lo, hi, hw)
        e["lvn_ages"] = ages
    return e


def events_breakdown(env: Env) -> Path:
    import pyarrow.parquet as pq

    from nqbot.checks import feature_build as fb
    from nqbot.checks import session_build as sb

    f, cal = env.features, env.cal
    doc = fb.load_plan(env)
    key = doc["key"]
    base = fb.out_dir(env, key)
    days = [date.fromisoformat(x) for x in doc["sessions"]]
    dev = [d for d in days if month_index(d, env.settings) in DEVELOPMENT]
    pl = sb.plan(env)
    hw = float(f["chart_level_half_width"]["lvn"])
    ev: list[dict[str, Any]] = []
    per: dict[date, list[int]] = {}
    checked = []
    t0 = _time.monotonic()
    env.echo(f"step 12 events breakdown: {len(dev)} development sessions (stored build {key}; LVN spans rebuilt per "
             "session from the stored state; stored data only)")
    for k, d in enumerate(dev):
        iso = d.isoformat()
        tp = base / "touches" / f"{iso}.parquet"
        names = pq.read_schema(tp).names
        cols = [c for c in EVENT_COLS if c in names] + [c for c in names if c.startswith("n_")]
        rows = pq.read_table(tp, columns=cols).to_pylist()
        lab = {r["event_id"]: r for r in pq.read_table(base / "outcomes" / f"{iso}.parquet").to_pylist()}
        tape = st.load_tape(env.db, pl, "NQ", d)
        if tape is None:
            raise Refused(f"{d}: no stored NQ trades")
        state = json.loads(fb.start_file(env, key, days, d).read_text(encoding="utf-8"))
        spans = _session_lvn_spans(env, d, tape, state)
        if _check_lvn_spans(env, d, spans):
            checked.append(d)
        o, c = st.rth_open_ns(d), st.rth_close_ns(d, cal)
        per[d] = [0, 0]
        for r in rows:
            x = lab[r["event_id"]]
            t = int(r["ts"])
            rth = o <= t < c
            per[d][0 if rth else 1] += 1
            pr = tape.price[tape.idx_before(t):tape.idx_at(int(x["t_resolved"]))] if x["label"] == "held" else None
            ev.append(event_record(r, x, rth, pr, spans, hw))
        del tape
        if (k + 1) % 10 == 0:
            env.echo(f"  {k + 1}/{len(dev)} sessions ({_time.monotonic() - t0:.0f} s)")
    return _breakdown_report(env, ev, per, dev, checked, key)


def _pct(a: int, b: int) -> str:
    return "—" if not b else f"{100 * a / b:.1f}%"


def _qs(xs: list[float], qs=(0.1, 0.25, 0.5, 0.75, 0.9)) -> str:
    return " / ".join(f"{_q(xs, q):.1f}" for q in qs) if xs else "—"


def _label_counts(s: list[dict]) -> str:
    h = [e for e in s if e["label"] == "held"]
    return (f"{len(s)} | {len(h)} | {sum(1 for e in s if e['label'] == 'broke')} | "
            f"{sum(1 for e in s if e['label'] == 'timeout')} | {_pct(len(h), len(s))} | "
            f"{_pct(sum(1 for e in h if e['path'] == HELD_PATH[1]), len(h))}")


def _breakdown_report(env: Env, ev: list[dict], per: dict, dev: list[date], checked: list[date], key: str) -> Path:
    f = env.features
    n = len(ev)
    rth = [e for e in ev if e["rth"]]
    on = [e for e in ev if not e["rth"]]
    lines = ["# Step 12 events breakdown (months 1-6, report only)", "",
             f"{n} events of {len(dev)} development sessions from the stored feature build (key {key}): the touch rows "
             f"and their structure labels (§7.1, F11.4: held = a move of `hold_reaction_pts` {f['hold_reaction_pts']} "
             "pts in the hold direction from the touch's extreme, with a trade-through, before the zone is crossed; "
             f"timeout after `hold_window_min` {f['hold_window_min']} min). No definition changes. RTH = 09:30 to the "
             "close (early closes from the calendar); overnight = the rest of the CME day (18:00-09:30 and after the "
             "close).", ""]
    # (a)
    rth_n = [per[d][0] for d in dev]
    on_n = [per[d][1] for d in dev]
    lines += ["## (a) Events per session and zone makeup", "",
              "| events per session | median | p10 | p90 | min | max | total |", "|---|---|---|---|---|---|---|"]
    for name, xs in (("RTH", rth_n), ("overnight", on_n), ("whole CME day", [a + b for a, b in zip(rth_n, on_n)])):
        lines.append(f"| {name} | {np.median(xs):g} | {_fmtq(xs, 0.1)} | {_fmtq(xs, 0.9)} | {min(xs)} | {max(xs)} | "
                     f"{sum(xs)} |")
    lines += ["", "Share of events whose zone contains each level type (a zone often holds several, so the shares add "
              "up to more than 100%; for golden-zone events the levels are the ones overlapping the golden zone):", "",
              "| level type in the zone | all | RTH | overnight |", "|---|---|---|---|"]
    for g in ("LVN", "single print", "VWAP band", "GEX node", "highs/lows", "golden zone", "VWAP line",
              "POC / value area", "expected move"):
        lines.append(f"| {g} | " + " | ".join(_pct(sum(1 for e in s if g in e["groups"]), len(s))
                                              for s in (ev, rth, on)) + " |")
    only: dict[str, int] = {}
    for e in ev:
        if len(e["groups"]) == 1:
            g = next(iter(e["groups"]))
            only[g] = only.get(g, 0) + 1
    lines += ["", "Events whose zone holds one level type only: " +
              ", ".join(f"{g} {v} ({_pct(v, n)})" for g, v in sorted(only.items(), key=lambda x: -x[1])) + ".", ""]
    # (b)
    lv = [e for e in ev if e["lvn_ages"] is not None]
    matched = [e for e in lv if e["lvn_ages"]]
    youngest = [min(e["lvn_ages"]) for e in matched]
    oldest = [max(e["lvn_ages"]) for e in matched]
    all_young = sum(1 for x in oldest if x < LVN_YOUNG_MIN)
    any_young = sum(1 for x in youngest if x < LVN_YOUNG_MIN)
    lo_only = [e for e in matched if e["lvn_only"]]
    lo_young = sum(1 for e in lo_only if max(e["lvn_ages"]) < LVN_YOUNG_MIN)
    lines += ["## (b) LVN-driven events", "",
              f"{len(lv)} events ({_pct(len(lv), n)}) have an LVN member. The LVN spans are rebuilt per session from "
              "the stored state (the daily and weekly developing profiles, the build's own code) and matched to the "
              f"zone by band overlap at the touch (LVN price ± {f['chart_level_half_width']['lvn']} pts against the "
              f"zone's edges): {len(matched)} matched, {len(lv) - len(matched)} not. The rebuilt spans equal the full "
              f"day build's LVN lists on {', '.join(map(str, checked)) or 'no day'} (checked). Age = minutes since the "
              f"LVN's span first qualified (profile instants every {f['profile_update_sec']} s).", "",
              "| | events | age (min): p10 / p25 / median / p75 / p90 |", "|---|---|---|",
              f"| youngest LVN member | {len(youngest)} | {_qs(youngest)} |",
              f"| oldest LVN member | {len(oldest)} | {_qs(oldest)} |",
              f"| LVN-only zones (youngest member) | {len(lo_only)} | {_qs([min(e['lvn_ages']) for e in lo_only])} |", "",
              f"- Events whose every LVN member had existed < {LVN_YOUNG_MIN} min: **{all_young}** "
              f"({_pct(all_young, len(matched))} of the matched LVN events, {_pct(all_young, n)} of all events); with "
              f"at least one such member: {any_young} ({_pct(any_young, len(matched))}).",
              f"- LVN-only zones (no other level type) whose every LVN had existed < {LVN_YOUNG_MIN} min: {lo_young} of "
              f"{len(lo_only)} ({_pct(lo_young, len(lo_only))}).", "",
              "| youngest LVN member's age at the touch | events | LVN-only zones |", "|---|---|---|"]
    for a, b in LVN_AGE_BUCKETS:
        k = sum(1 for x in youngest if x >= a and (b is None or x < b))
        k2 = sum(1 for e in lo_only if min(e["lvn_ages"]) >= a and (b is None or min(e["lvn_ages"]) < b))
        lines.append(f"| {a}–{b} min | {k} | {k2} |" if b is not None else f"| ≥ {a} min | {k} | {k2} |")
    lines.append("")
    # (c)
    held = [e for e in ev if e["label"] == "held"]
    lines += ["## (c) Held labels", "",
              f"{len(held)} held of {n} ({_pct(len(held), n)}). The path = the trades from the touch through the trade "
              "that resolved the hold, against the zone's edges at the touch (a zone of moving levels moves after it).",
              "", "| path before the hold resolved | held events | share of held |", "|---|---|---|"]
    for p in HELD_PATH:
        k = sum(1 for e in held if e["path"] == p)
        lines.append(f"| {p} | {k} | {_pct(k, len(held))} |")
    lines += ["", f"\"Stayed inside\" = the {f['hold_reaction_pts']}-pt move happened without price leaving the zone "
              "after it entered (the move fits inside the zone); \"never entered\" = held from within "
              f"`zone_proximity` ({f['zone_proximity']} pts) without trading into the zone.", "",
              "Held share by zone width (the zone's edges at the touch) and by session part:", "",
              "| zone width (pts) | events | held | broke | timeout | held share | held that stayed inside |",
              "|---|---|---|---|---|---|---|"]
    edges = list(WIDTH_BUCKETS) + [None]
    for a, b in zip(edges[:-1], edges[1:]):
        s = [e for e in ev if e["width"] >= a and (b is None or e["width"] < b)]
        lines.append(f"| {f'{a}–{b}' if b is not None else f'≥ {a}'} | {_label_counts(s)} |")
    for name, s in (("RTH, all widths", rth), ("overnight, all widths", on), ("all", ev)):
        lines.append(f"| {name} | {_label_counts(s)} |")
    lines += ["", "How far price went into / through the zone before the label resolved (from the outcome's extreme: "
              "points past the near edge, and as a share of the zone's width; < 0 never entered, 0–1 inside, > 1 past "
              "the far edge):", "",
              "| label | events | points: p10 / p25 / median / p75 / p90 | width share: p10 / p25 / median / p75 / p90 | "
              "never entered | inside | past the far edge |", "|---|---|---|---|---|---|---|"]
    for lab in ("held", "broke", "timeout"):
        s = [e for e in ev if e["label"] == lab]
        sh = [e["depth_share"] for e in s if e["depth_share"] is not None]
        lines.append(f"| {lab} | {len(s)} | {_qs([e['depth'] for e in s])} | {_qs(sh)} | "
                     f"{_pct(sum(1 for e in s if e['depth'] < 0), len(s))} | "
                     f"{_pct(sum(1 for x in sh if 0 <= x <= 1), len(s))} | "
                     f"{_pct(sum(1 for x in sh if x > 1), len(s))} |")
    path = out_dir(env) / "events_breakdown.md"
    atomic_write_text(path, "\n".join(lines) + "\n")
    env.echo(f"  report {path}")
    return path
