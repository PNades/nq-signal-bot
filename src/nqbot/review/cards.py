"""One review card from the stored build (SPEC §8, §9b item 5; owner's plan 2026-11-15, stage 4).

A card is one decision point and its bootstrap trade (`sampler`). Its payload has two halves:
- **pre**: everything known at the decision time T and nothing after it (§8: the outcome stays hidden until the owner
  answers, so the chart stops at T): the NQ and ES 1-minute bars from the stored trades (the bar in progress at T built
  from the trades up to T only), every level's band and role over the day, the zones as they stood at each decision
  point, the earlier touches with the outcomes already known by T, the four maps once a minute over the replay span
  and the confirmed King's switches;
- **post**: the same from T's minute to the reveal end, the trade's fill and exit and the event's structure label.
The server sends `post` only after the owner's answer is stored (`store.py`, `ui/api_review.py`).

Everything is read from the stored build (`levels/`, `level_events/`, `events/`, `decisions/`, `decision_rows/`, the
labels' `pairs/`, `fills/`, `paths/`), the stored Databento trades and the stored Skylit boards, through the bot's own
code (`features/boards.py` for the applied boards and the R in force, `features/node_switch.py` for the confirmed King),
so the chart shows what the bot saw. Display-only choices (named here): the chart's start, the replay span, the zones
drawn from the decision points' zone books, the strikes kept per map snapshot.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from nqbot.features import node_switch as ns
from nqbot.features import session_tape as st
from nqbot.review import roles

ET = ZoneInfo("America/New_York")
NS = 1_000_000_000
MIN_NS = 60 * NS
# the chart starts at the session's open, 18:00 ET the evening before (owner 2026-11-16, stage-4 review (7)); the maps
# (RTH only, §3.1) start at 09:30 and are sampled once a minute over the whole session up to the cut
REPLAY_LOOKBACK_MIN = 30           # the replay slider covers T - 30 min .. T (owner 2026-11-16, round 5; candles from 18:00)
REVEAL_AFTER_EXIT_MIN = 15         # after the answer the path is shown to the exit (or the label) + 15 min
MAP_STRIKES_KEPT = 40              # per map snapshot: the largest |value| strikes kept for the radial
MAP_MIN_SHARE_OF_KING = 0.05       # ... and only those at >= 5% of the board's largest |value|
PRICE_DECIMALS = 2
MAPS = ("QQQ", "NDXP", "SPY", "SPX")
CHAIN_ROOT = {"QQQ": "NQ", "NDXP": "NQ", "SPY": "ES", "SPX": "ES"}

SUB_LABEL = {
    "vwap18": "VWAP", "vwap18_up1": "VWAP +1σ", "vwap18_up2": "VWAP +2σ", "vwap18_dn1": "VWAP −1σ",
    "vwap18_dn2": "VWAP −2σ", "vwap0930": "RTH VWAP", "vwap0930_up1": "RTH VWAP +1σ", "vwap0930_up2": "RTH VWAP +2σ",
    "vwap0930_dn1": "RTH VWAP −1σ", "vwap0930_dn2": "RTH VWAP −2σ", "poc_daily": "Daily POC", "poc_weekly": "Weekly POC",
    "lvn_daily": "Daily LVN", "lvn_weekly": "Weekly LVN", "prev_poc": "Prev POC", "prev_vah": "Prev VAH",
    "prev_val": "Prev VAL", "single_print": "Single print", "pdh": "PDH", "pdl": "PDL", "on_high": "ON high",
    "on_low": "ON low", "ib_high": "IB high", "ib_low": "IB low", "prior_rth_high": "Prior RTH high",
    "prior_rth_low": "Prior RTH low", "prior_week_high": "Prior week high", "prior_week_low": "Prior week low",
    "retested_extreme": "Retested extreme", "em_up1": "EM +1", "em_up2": "EM +2", "em_dn1": "EM −1", "em_dn2": "EM −2",
    "qqq_node": "QQQ", "ndxp_node": "NDXP", "spy_node": "SPY", "spx_node": "SPX", "darkpool": "Dark pool",
}
GROUP_OF = {"gex_node": "nodes", "vwap": "vwap", "volume_profile": "profile", "session_hl": "highs/lows",
            "opening_range": "highs/lows", "expected_move": "expected move"}


def level_label(row: dict[str, Any]) -> str:
    base = SUB_LABEL.get(row.get("sub_type") or "", row.get("sub_type") or "level")
    if row.get("rtype") == "gex_node" and row.get("strike") is not None:
        s = float(row["strike"])
        return f"{base} {s:g}"
    return base


def et_offset_sec(t_ns: int) -> int:
    """ET − UTC in seconds at `t_ns` (the chart draws ET wall time on a UTC axis)."""
    dt = datetime.fromtimestamp(t_ns / NS, tz=timezone.utc).astimezone(ET)
    off = dt.utcoffset()
    return int(off.total_seconds()) if off is not None else 0


def et_clock(t_ns: int) -> str:
    return datetime.fromtimestamp(t_ns / NS, tz=timezone.utc).astimezone(ET).strftime("%H:%M:%S")


def floor_min(t_ns: int) -> int:
    return int(t_ns) - int(t_ns) % MIN_NS


def last_known_minute(t_ns: int, tape: st.Tape | None = None) -> int:
    """The start of the last 1-minute bar with anything known at t: t's own minute, or the one before when t is exactly
    on a minute boundary with no trade stamped at t (a style-(c) decision fires at a bar close, e.g. 12:05:00.000: the
    12:05 bar has nothing in it yet)."""
    m = floor_min(t_ns)
    if m != int(t_ns):
        return m
    if tape is not None and len(tape) and tape.idx_at(t_ns) > tape.idx_before(t_ns):
        return m
    return m - MIN_NS


def sec(t_ns: int) -> int:
    return int(int(t_ns) // NS)


def rnd(x: float | None, d: int = PRICE_DECIMALS) -> float | int | None:
    """Rounded to `d` decimals; a whole number as an int (a card holds hundreds of thousands of numbers: `612` not `612.0`)."""
    if x is None:
        return None
    x = float(x)
    if math.isnan(x) or math.isinf(x):
        return None
    r = round(x, d)
    return int(r) if r.is_integer() else r


# ── the day's stored data ──────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class DayData:
    """One session's stored data a card reads (built by `load_day`, or by hand in `synthetic.py`)."""
    day: date
    open_ns: int                                   # 09:30 ET
    close_ns: int                                  # the RTH close (early closes included)
    tapes: dict[str, st.Tape]                      # "NQ", "ES"
    levels: list[dict[str, Any]]                   # `levels/<D>.parquet` rows (times / lo / hi / spans as lists)
    level_events: list[dict[str, Any]]             # `level_events/<D>.parquet` rows
    events: list[dict[str, Any]]
    decisions: list[dict[str, Any]]
    rows: dict[str, dict[str, Any]]                # decision_rows by decision_id
    pairs: dict[str, dict[str, Any]]
    fills: dict[str, dict[str, Any]]
    paths: dict[str, dict[str, Any]]
    maps: dict[str, Any] = field(default_factory=dict)   # name -> boards.MapBoards
    symbols: dict[str, str] = field(default_factory=dict)
    features: dict[str, Any] = field(default_factory=dict)
    _kings: dict[str, list[tuple[int, float]]] | None = None
    _touch_price: dict[str, float] | None = None
    _level_tuples: dict[str, list[tuple]] | None = None

    def level_tuples(self) -> dict[str, list[tuple]]:
        if self._level_tuples is None:
            self._level_tuples = level_event_tuples(self)
        return self._level_tuples

    def kings(self) -> dict[str, list[tuple[int, float]]]:
        """Per map, the confirmed King (F2.11, `node_switch.ConfirmedNode`) from each switch on: [(t_apply, strike)]."""
        if self._kings is None:
            self._kings = {m: confirmed_kings(mb, self.features) for m, mb in self.maps.items()}
        return self._kings

    def touch_price(self) -> dict[str, float]:
        if self._touch_price is None:
            self._touch_price = {}
            for d in self.decisions:
                self._touch_price.setdefault(d["event_id"], float(d["touch_price"]))
        return self._touch_price


def board_leader(strikes: np.ndarray, values: np.ndarray, spot: float) -> float | None:
    """F2.3: the largest |value|; ties nearer spot, then the lower strike (`node_switch.leader`, vectorised)."""
    if len(strikes) == 0:
        return None
    a = np.abs(values)
    top = np.flatnonzero(a == a.max())
    if len(top) == 1:
        return float(strikes[top[0]])
    k = strikes[top]
    order = np.lexsort((k, np.abs(k - spot)))
    return float(k[order[0]])


def confirmed_kings(mb, features: dict[str, Any]) -> list[tuple[int, float]]:
    """The confirmed King over the session (switch instants), every applied board in apply-time order."""
    if mb is None or len(mb) == 0 or not features:
        return []
    tracker = ns.from_config(features, "king")
    out: list[tuple[int, float]] = []
    for i in range(len(mb)):
        k, v = mb.strikes(i)
        lead = board_leader(k, v, float(mb.spot[i]))
        t = int(mb.t_apply[i])
        before = tracker.confirmed
        tracker.on_board(t / NS, lead, k, one_minute=bool(mb.one_minute[i]), spot=float(mb.spot[i]))
        if tracker.confirmed is not None and tracker.confirmed != before:
            out.append((t, float(tracker.confirmed)))
    return out


def king_at(kings: list[tuple[int, float]], t_ns: int) -> float | None:
    best = None
    for t, k in kings:
        if t <= t_ns:
            best = k
        else:
            break
    return best


# ── pieces of a card ───────────────────────────────────────────────────────────────────────────────────────────────

def bars(tape: st.Tape | None, t0_ns: int, t1_ns: int) -> list[list[float]]:
    """1-minute bars of the trades with t0 <= ts <= t1 (the last bar is partial when t1 falls inside it): rows
    [bar start (s), open, high, low, close, volume]."""
    if tape is None or len(tape) == 0:
        return []
    cut = tape.cut(t1_ns)
    i = cut.idx_before(t0_ns)
    part = st.Tape(cut.day, cut.symbol, cut.ts[i:], cut.price[i:], cut.size[i:], cut.side[i:])
    b = st.bars(part, 1)
    return [[sec(b.start[j]), rnd(b.open[j]), rnd(b.high[j]), rnd(b.low[j]), rnd(b.close[j]), int(b.volume[j])]
            for j in range(len(b))]


def minute_grid(t0_ns: int, t1_ns: int, tape: st.Tape | None = None) -> list[tuple[int, int]]:
    """(bar start, sample time) for each minute from t0's minute to t1's: a minute is read at its close, the minute
    holding t1 at t1 itself. A minute starting at t1 itself has nothing known yet, unless a trade is stamped exactly at
    t1 (`last_known_minute`; the chart draws that minute's bar then, owner 2026-11-16, round 10): it is read at t1."""
    out = []
    m = floor_min(t0_ns)
    while m < t1_ns:
        out.append((m, min(m + MIN_NS, t1_ns)))
        m += MIN_NS
    if m == int(t1_ns) and last_known_minute(t1_ns, tape) == m and m >= floor_min(t0_ns):
        out.append((m, m))
    return out


def _in_spans(spans: list, t: int) -> bool:
    return any(int(a) <= t < int(z) for a, z in spans) if spans else False


def band_at(row: dict[str, Any], t: int) -> tuple[float, float] | None:
    """The level's band in force at t (None: no band, not active, or ended)."""
    end = row.get("end_ns")
    if end is not None and not (isinstance(end, float) and math.isnan(end)) and t >= int(end):
        return None
    if not _in_spans(row.get("spans") or [], t):
        return None
    times = row["_times"]
    i = int(np.searchsorted(times, t, side="right")) - 1
    if i < 0:
        return None
    lo, hi = float(row["_lo"][i]), float(row["_hi"][i])
    if math.isnan(lo) or math.isnan(hi):
        return None
    return lo, hi


def _prep(row: dict[str, Any]) -> dict[str, Any]:
    if "_times" not in row:
        row["_times"] = np.asarray(row.get("times") or [], dtype=np.int64)
        row["_lo"] = np.asarray(row.get("lo") or [], dtype=np.float64)
        row["_hi"] = np.asarray(row.get("hi") or [], dtype=np.float64)
    return row


def level_segments(row: dict[str, Any], grid: list[tuple[int, int]]) -> list[list[float]]:
    """[[from (s), to (s), lo, hi]]: the band per minute, merged while it doesn't change."""
    _prep(row)
    segs: list[list[float]] = []
    for m, s in grid:
        b = band_at(row, s)
        if b is None:
            continue
        lo, hi = rnd(b[0]), rnd(b[1])
        if segs and segs[-1][1] == sec(m) and segs[-1][2] == lo and segs[-1][3] == hi:
            segs[-1][1] = sec(m) + 60
        else:
            segs.append([sec(m), sec(m) + 60, lo, hi])
    return segs


PRICE_MARGIN_FRAC = 0.01   # levels and zones kept within the price range seen so far ± 1% (≈ 250 NQ points)


def price_window(rows: list[list[float]]) -> tuple[float, float] | None:
    """The traded range of `rows` (bars) ± PRICE_MARGIN_FRAC: the levels and zones a card keeps (from the bars shown
    so far only, so the pruning says nothing about later prices)."""
    if not rows:
        return None
    lo, hi = min(r[3] for r in rows), max(r[2] for r in rows)
    m = PRICE_MARGIN_FRAC * hi
    return lo - m, hi + m


def _overlaps(lo: float, hi: float, win: tuple[float, float] | None) -> bool:
    return win is None or (hi >= win[0] and lo <= win[1])


def level_event_tuples(dd: DayData) -> dict[str, list[tuple]]:
    """Each level's (t, name[, role]) events; for the roles set from price's side (`roles.ROLE_FROM_PRICE`) the role is
    read from the last trade at t against the band at t (above: floor, below: ceiling)."""
    rows = {r["uid"]: _prep(r) for r in dd.levels}
    out: dict[str, list[tuple]] = {}
    for e in dd.level_events:
        t, name = int(e["t"]), str(e["name"])
        item: tuple = (t, name)
        if name in roles.ROLE_FROM_PRICE:
            row = rows.get(e["uid"])
            tape = dd.tapes.get((row or {}).get("instrument") or "NQ")
            p = tape.last_price_at(t) if tape is not None else None
            i = int(np.searchsorted(row["_times"], t, side="right")) - 1 if row is not None else -1
            if p is not None and i >= 0:
                lo, hi = float(row["_lo"][i]), float(row["_hi"][i])
                item = (t, name, roles.FLOOR if p > (lo + hi) / 2 else roles.CEILING)
        out.setdefault(e["uid"], []).append(item)
    return out


def levels_payload(dd: DayData, instrument: str, grid: list[tuple[int, int]], t1_ns: int,
                   win: tuple[float, float] | None = None) -> list[dict[str, Any]]:
    by_uid = {u: [e for e in evs if e[0] <= t1_ns] for u, evs in dd.level_tuples().items()}
    out = []
    for row in dd.levels:
        if row.get("instrument") != instrument:
            continue
        segs = level_segments(row, grid)
        if not segs or not any(_overlaps(g[2], g[3], win) for g in segs):
            continue
        evs = by_uid.get(row["uid"], [])
        steps = roles.timeline(evs)
        out.append({
            "uid": row["uid"], "level_id": row["level_id"], "label": level_label(row), "sub_type": row.get("sub_type"),
            "group": GROUP_OF.get(row.get("rtype") or "", "other") if row.get("kind") != "single_print" else "profile",
            "node": row.get("rtype") == "gex_node", "chain": row.get("chain"), "strike": rnd(row.get("strike"), 4),
            "segs": segs,
            "roles": [[sec(s.t), s.role, s.provisional, s.broken_back, s.flips] for s in steps],
            "marks": [[sec(t), n] for t, n in roles.markers(evs)],
        })
    return out


def _centres_at(dd: DayData, t: int, steps_by_uid: dict[str, list[roles.RoleStep]]) -> tuple[np.ndarray, np.ndarray]:
    """The band centres of the NQ levels active at t, and whether each is provisional at t (steps up to t only)."""
    cs, pv = [], []
    for row in dd.levels:
        if row.get("instrument") != "NQ":
            continue
        _prep(row)
        b = band_at(row, t)
        if b is None:
            continue
        cs.append((b[0] + b[1]) / 2)
        s = roles.role_at(steps_by_uid.get(row["uid"], []), t)
        pv.append(bool(s and s.provisional))
    return np.asarray(cs, dtype=np.float64), np.asarray(pv, dtype=bool)


def _steps_by_uid(dd: DayData) -> dict[str, list[roles.RoleStep]]:
    return {u: roles.timeline(v) for u, v in dd.level_tuples().items()}


def zones_payload(dd: DayData, t0_ns: int, t1_ns: int, own_zone: str | None,
                  win: tuple[float, float] | None = None) -> list[dict[str, Any]]:
    """The zones as they stood at each decision point in [t0, t1] (the zone book of `decisions/`; display: a zone keeps
    its shape until the next decision point). Each point: [t (s), lo, hi, members, role, provisional]."""
    snaps: dict[int, dict[str, Any]] = {}
    for d in dd.decisions:
        t = int(d["decision_ts"])
        if t > t1_ns or t < t0_ns or t in snaps:
            continue
        book = {d["zone_id"]: (float(d["zone_lo"]), float(d["zone_hi"]))}
        for z, lo, hi in zip(d.get("z_id") or [], d.get("z_lo") or [], d.get("z_hi") or []):
            book.setdefault(str(z), (float(lo), float(hi)))
        snaps[t] = {"price": float(d["price_at_decision"]), "book": book}
    tracks: dict[str, list[list[Any]]] = {}
    seen: dict[str, list[int]] = {}      # every snapshot that held the zone (its end is the next one without it)
    steps = _steps_by_uid(dd)
    order = sorted(snaps)
    last_i: dict[str, int] = {}
    for i, t in enumerate(order):
        price = snaps[t]["price"]
        cs, pv = _centres_at(dd, t, steps)
        for z, (lo, hi) in snaps[t]["book"].items():
            if z != own_zone and not _overlaps(lo, hi, win):
                continue
            inside = (cs >= lo - 1e-9) & (cs <= hi + 1e-9)
            n, prov = int(inside.sum()), bool(pv[inside].any())
            role = roles.FLOOR if price > hi else roles.CEILING if price < lo else None
            pt = [sec(t), rnd(lo), rnd(hi), n, role, prov]
            tr = tracks.setdefault(z, [])
            if z in last_i and last_i[z] < i - 1:
                tr.append([sec(order[last_i[z] + 1]), None, None, 0, None, False])   # it vanished, then came back
            last_i[z] = i
            if tr and tr[-1][1:] == pt[1:]:
                seen.setdefault(z, []).append(sec(t))   # unchanged: the earlier point stands
                continue
            tr.append(pt)
            seen.setdefault(z, []).append(sec(t))
    times = [sec(t) for t in sorted(snaps)]
    out = []
    for z, pts in tracks.items():
        # a zone missing from a later snapshot ended there: close it at that snapshot
        last = seen[z][-1]
        nxt = next((x for x in times if x > last), None)
        out.append({"id": z, "own": z == own_zone, "pts": pts, "end": nxt})
    return out


def events_payload(dd: DayData, t0_ns: int, t1_ns: int, own_event: str, *, reveal: bool) -> list[dict[str, Any]]:
    """Touches in [t0, t1] with the structure label only once it was known (t_resolved <= t1; the card's own event
    only when `reveal`)."""
    tp = dd.touch_price()
    out = []
    for e in dd.events:
        t = int(e["ts"])
        if t < t0_ns or t > t1_ns:
            continue
        own = e["event_id"] == own_event
        known = (e.get("t_resolved") is not None and int(e["t_resolved"]) <= t1_ns) and (reveal or not own)
        out.append({"t": sec(t), "price": rnd(tp.get(e["event_id"])), "dir": int(e["direction"]), "role": e["role"],
                    "own": own, "label": e["label"] if known else None})
    return out


def map_snapshots(dd: DayData, grid: list[tuple[int, int]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    kings = dd.kings()
    for m in MAPS:
        mb = dd.maps.get(m)
        snaps = []
        if mb is not None and len(mb):
            for bar, s in grid:
                i = mb.index_at(s)
                if i < 0:
                    continue
                k, v = mb.strikes(i)
                if not len(k):
                    continue
                a = np.abs(v)
                top = float(a.max())
                keep = np.flatnonzero(a >= MAP_MIN_SHARE_OF_KING * top) if top > 0 else np.zeros(0, dtype=np.int64)
                if len(keep) > MAP_STRIKES_KEPT:
                    keep = keep[np.argsort(-a[keep], kind="stable")[:MAP_STRIKES_KEPT]]
                keep = np.sort(keep)
                # "tb": the board's apply time (s), the earliest moment the bot could use it (owner 2026-11-16, round
                # 10): never after the moment the replay shows at this bar (its close, or the half's end inside it)
                snaps.append({"t": sec(bar), "tb": sec(int(mb.t_apply[i])), "spot": rnd(mb.spot[i], 4), "r": rnd(mb.r[i], 8),
                              "king": king_at(kings.get(m, []), s), "max": rnd(top, 0),
                              "s": [[rnd(k[j], 4), rnd(v[j], 0)] for j in keep]})
        out[m] = snaps
    return out


def kings_payload(dd: DayData, t1_ns: int) -> dict[str, list[list[float]]]:
    """Per map, the confirmed King's switches up to t1: [t (s), strike, price on the chart (strike × R)]."""
    out: dict[str, list[list[float]]] = {}
    for m, ks in dd.kings().items():
        mb = dd.maps.get(m)
        rows = []
        for t, k in ks:
            if t > t1_ns:
                break
            i = mb.index_at(t) if mb is not None else -1
            r = float(mb.r[i]) if i >= 0 else float("nan")
            rows.append([sec(t), rnd(k, 4), rnd(k * r)])
        out[m] = rows
    return out


# ── the facts beside the chart ─────────────────────────────────────────────────────────────────────────────────────

def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def _fmt(v: Any, kind: str) -> str:
    if kind == "bool":
        return "—" if v is None else ("yes" if bool(v) else "no")
    x = _f(v)
    if x is None:
        return "—"
    if kind == "int":
        return f"{int(round(x))}"
    if kind == "pts":
        return f"{x:,.2f} pts"
    if kind == "pct":
        return f"{100 * x:.0f}%"
    if kind == "min":
        return f"{x:.0f} min"
    if kind == "em":
        return f"{x:+.2f} EM"
    if kind == "strike":
        return f"{x:g}"
    if kind == "signed":
        return f"{x:+.2f}"
    return f"{x:.2f}"


# (group, decision_rows column, label, kind); §8: node type / freshness, rug / pika / Trinity, regime, confluence,
# the day-type inputs so far (never the end-of-day label); P(trend) comes with step 15, order flow with step 17
READINGS: tuple[tuple[str, str, str, str], ...] = (
    ("Confluence", "stack_count", "Levels stacked", "int"),
    ("Confluence", "zone_width", "Zone width", "pts"),
    ("Confluence", "distance_to_near_edge", "From the near edge", "pts"),
    ("Confluence", "trinity_count", "Trinity maps", "int"),
    ("Confluence", "es_at_level", "ES at a level", "bool"),
    ("Nodes", "king_present", "King in the zone", "bool"),
    ("Nodes", "gatekeeper_present", "Gatekeeper in the zone", "bool"),
    ("Nodes", "largest_pct_of_king", "Largest node, % of King", "pct"),
    ("Nodes", "any_rug", "Rug", "bool"),
    ("Nodes", "any_reverse_rug", "Reverse rug", "bool"),
    ("Nodes", "qqq_king_strike", "QQQ King", "strike"),
    ("Nodes", "ndxp_king_strike", "NDXP King", "strike"),
    ("Regime", "qqq_regime_net", "QQQ regime (net)", "signed"),
    ("Regime", "ndxp_regime_net", "NDXP regime (net)", "signed"),
    ("Freshness", "zone_tests_so_far", "Zone tests so far", "int"),
    ("Freshness", "holds_so_far_mean", "Holds so far (mean)", "num"),
    ("Freshness", "primary_flip_count", "Flips of the main level", "int"),
    ("Freshness", "primary_provisional_role", "Provisional role", "bool"),
    ("Freshness", "primary_minutes_since_cross", "Since its last cross", "min"),
    ("Day so far", "dt_minutes_since_open", "Minutes since the open", "min"),
    ("Day so far", "dt_range_vs_em", "Range vs EM", "num"),
    ("Day so far", "dt_vwap_crosses", "VWAP crosses", "int"),
    ("Day so far", "dt_er_so_far", "Efficiency ratio", "num"),
    ("Day so far", "with_day_direction", "With the day's direction", "bool"),
    ("Day so far", "approach_move_15", "Approach, last 15 min", "pts"),
)
READING_COLUMNS = tuple(dict.fromkeys(c for _, c, _, _ in READINGS))
# round 5 (owner 2026-11-16; display only, the detector still computes them and the scorer still reads them): the Nodes
# lines no longer shown; cards stored before are filtered when served (`store.public_card`)
HIDDEN_READING_LABELS = frozenset({"Pika nodes (+)", "Barney nodes (−)", "Pika member"})
DEFERRED_READINGS = (("Model", "P(trend)", "from step 15"), ("Order flow", "Trigger", "from step 17"))


def readings(row: dict[str, Any]) -> list[dict[str, str]]:
    out = [{"group": g, "label": lab, "value": _fmt(row.get(col), kind)} for g, col, lab, kind in READINGS]
    out += [{"group": g, "label": lab, "value": "—", "note": note} for g, lab, note in DEFERRED_READINGS]
    return out


def stack_payload(dd: DayData, row: dict[str, Any], t: int, lo: float, hi: float, level_zone: bool = True) -> dict[str, Any]:
    """The confluences behind "Levels stacked" (owner 2026-11-16, stage-4 review round 3), as known at the decision time
    t: the scorer's own count per level type at that decision point (`stack_count`, `n_<sub_type>`,
    `SessionFeatures._makeup`), and for each type the level(s) of that type whose band at t lies in, or nearest, the zone
    [lo, hi]: its name, its price (the band centre at t), its band, its role and whether that role was still
    provisional, from its role steps up to t only (no lookahead). A member the chart can't find is listed as missing; a
    member type the counts don't name (e.g. a gameplan zone) as "other".

    Which level of a type (owner 2026-11-16, round 5; SPEC §13 Q-review-stack; display only, the scorer stores counts):
    a level zone's members are the zone book's, and its edges are the union of their bands, so the levels whose band
    lies inside the zone come first, then those overlapping it (a VWAP band sampled once a second can straddle an edge
    by a hair), then the nearest; a container's (golden zone, gameplan zone) members are every level whose band
    overlaps it: overlapping first, then the nearest. Each item says how it fits (`fit`: inside / overlapping /
    outside)."""
    from nqbot.features.session_features import NQ_SUBTYPES

    comp = {st: int(row.get(f"n_{st}") or 0) for st in NQ_SUBTYPES}
    total = int(row.get("stack_count") or 0)
    steps = _steps_by_uid(dd)
    items: list[dict[str, Any]] = []
    for st, n in comp.items():
        if n <= 0:
            continue
        cands = []
        for r in dd.levels:
            if r.get("instrument") != "NQ" or r.get("sub_type") != st:
                continue
            _prep(r)
            b = band_at(r, t)
            if b is None:
                continue
            gap = max(0.0, b[0] - hi, lo - b[1])           # 0 when the band overlaps the zone
            inside = b[0] >= lo - 1e-9 and b[1] <= hi + 1e-9
            fit = "inside" if inside else ("overlapping" if gap == 0 else "outside")
            rank = 0 if (inside and level_zone) else (1 if gap == 0 else 2)
            cands.append((rank, gap, abs((b[0] + b[1]) / 2 - (lo + hi) / 2), r["uid"], r, b, fit))
        cands.sort(key=lambda c: c[:4])
        for _, gap, _, _, r, b, fit in cands[:n]:
            step = roles.role_at(steps.get(r["uid"], []), t)
            items.append({"label": level_label(r), "price": rnd((b[0] + b[1]) / 2), "lo": rnd(b[0]), "hi": rnd(b[1]),
                          "role": step.role if step else None, "provisional": bool(step and step.provisional),
                          "crossed_back": bool(step and step.broken_back), "flips": step.flips if step else 0,
                          "outside_pts": rnd(gap) if gap > 0 else None, "fit": fit})
        for _ in range(max(0, n - len(cands))):
            items.append({"label": SUB_LABEL.get(st, st), "missing": True})
    other = total - sum(comp.values())
    items.sort(key=lambda x: -(x.get("price") or -1e18))
    return {"count": total, "items": items, "other": max(0, other), "rule": "inside first" if level_zone else "overlapping"}


def members_at(dd: DayData, t: int, lo: float, hi: float) -> list[str]:
    """The NQ levels stacked in [lo, hi] at t (band centre inside the zone; display)."""
    out = []
    for row in dd.levels:
        if row.get("instrument") != "NQ":
            continue
        _prep(row)
        b = band_at(row, t)
        if b is not None and lo - 1e-9 <= (b[0] + b[1]) / 2 <= hi + 1e-9:
            out.append(level_label(row))
    return out


def member_labels(dd: DayData, ids: list[str]) -> list[str]:
    by_id = {r["level_id"]: r for r in dd.levels}
    return [level_label(by_id[i]) if i in by_id else i for i in ids]


STOP_NAMES = {"1": "near edge", "2": "far edge + buffer", "3": "next structure", "6": "range extreme"}


def trade_payload(pair: dict[str, Any], fill: dict[str, Any] | None, direction: int) -> dict[str, Any]:
    entry = _f(fill.get("limit")) if fill else None
    if entry is None:
        entry = _f(pair.get("ref"))
    return {"style": pair["style"], "order_type": pair["order_type"], "direction": "long" if direction > 0 else "short",
            "entry": rnd(entry), "stop": rnd(pair["stop"]), "target": rnd(pair["target"]),
            "stop_pts": rnd(pair["stop_pts"]), "target_pts": rnd(pair["target_pts"]), "net_rr": rnd(pair["net_rr"]),
            "stop_rule": STOP_NAMES.get("2"), "target_kind": pair.get("target_kind"),
            "target_zone_kind": pair.get("target_zone_kind"),
            "size": None if not fill else {"minis": int(fill.get("size_nq") or 0), "micros": int(fill.get("micros") or 0)}}


def outcome_payload(dd: DayData, pair: dict[str, Any], fill: dict[str, Any] | None, event: dict[str, Any]) -> dict[str, Any]:
    out = str(pair.get("out_none") or "")
    t_exit = _f(pair.get("t_exit_none"))
    tape = dd.tapes.get("NQ")
    px = None
    if out == "target":
        px = _f(pair["target"])
    elif out == "stop":
        px = _f(pair["stop"])
    elif t_exit is not None and tape is not None:
        px = tape.last_price_at(int(t_exit))
    path = dd.paths.get(pair["pair_id"]) or {}
    return {"result": out, "r": rnd(pair.get("r_none"), 3), "t_exit": None if t_exit is None else sec(int(t_exit)),
            "exit": rnd(px), "t_fill": None if not fill or fill.get("t_fill") is None else sec(int(fill["t_fill"])),
            "fill": rnd(fill.get("fill_px")) if fill else None,
            "mae": rnd(path.get("mae")), "mfe": rnd(path.get("mfe")),
            "structure": event.get("label"), "structure_how": event.get("how"),
            "timeout_reason": pair.get("timeout_reason") if out == "timeout" else None}


# ── the card ───────────────────────────────────────────────────────────────────────────────────────────────────────

def card_payload(dd: DayData, card_id: str, decision_id: str, pair_id: str, stratum: str | None = None,
                 why: str | None = None) -> dict[str, Any]:
    dec = next(d for d in dd.decisions if d["decision_id"] == decision_id)
    pair = dd.pairs[pair_id]
    fill = dd.fills.get(pair.get("fill_id") or "")
    event = next(e for e in dd.events if e["event_id"] == dec["event_id"])
    row = dd.rows.get(decision_id, {})
    T = int(dec["decision_ts"])
    tm = last_known_minute(T, dd.tapes.get("NQ"))
    chart0 = st.cme_start_ns(dd.day)
    replay0 = max(chart0, floor_min(T) - REPLAY_LOOKBACK_MIN * MIN_NS)
    ends = [T]
    if _f(pair.get("t_exit_none")) is not None:
        ends.append(int(float(pair["t_exit_none"])))
    if event.get("t_resolved") is not None:
        ends.append(int(event["t_resolved"]))
    reveal_end = min(dd.close_ns, max(ends) + REVEAL_AFTER_EXIT_MIN * MIN_NS)
    reveal_end = max(reveal_end, min(dd.close_ns, T + MIN_NS))

    def half(t0: int, t1: int, maps0: int, *, reveal: bool) -> dict[str, Any]:
        grid = minute_grid(t0, t1, dd.tapes.get("NQ"))
        mgrid = minute_grid(maps0, t1, dd.tapes.get("NQ"))
        b = {k: bars(dd.tapes.get(k), t0, t1) for k in ("NQ", "ES")}
        # the price range the pruning keeps: the bars shown so far (pre: up to T only)
        seen = {k: price_window(bars(dd.tapes.get(k), chart0, t1)) if reveal else price_window(b[k]) for k in b}
        return {"bars": b,
                "levels": {k: levels_payload(dd, k, grid, t1, seen[k]) for k in ("NQ", "ES")},
                "zones": zones_payload(dd, chart0 if not reveal else tm, t1, dec["zone_id"], seen["NQ"]),
                # the revealed list replaces the first one whole (earlier touches resolved after T get their label)
                "events": events_payload(dd, chart0, t1, dec["event_id"], reveal=reveal),
                "maps": map_snapshots(dd, mgrid),
                "kings": kings_payload(dd, t1)}

    pre = half(chart0, T, max(floor_min(dd.open_ns), chart0), reveal=False)
    post = half(tm, reveal_end, tm, reveal=True)
    members = list(row.get("founding_level_ids") or [])
    return {
        "id": card_id, "day": dd.day.isoformat(), "decision_id": decision_id, "pair_id": pair_id,
        "event_id": dec["event_id"], "stratum": stratum, "why": why,
        "symbols": dd.symbols, "et_offset_sec": et_offset_sec(T),
        "t": {"decision": sec(T), "decision_ns": T, "decision_et": et_clock(T), "chart_start": sec(chart0),
              "replay_start": sec(replay0), "minute": sec(tm), "reveal_end": sec(reveal_end), "open": sec(dd.open_ns),
              "close": sec(dd.close_ns)},
        "setup": {"event_type": dec.get("event_type"), "role": dec.get("role"), "direction": int(dec["direction"]),
                  "golden_zone_part": dec.get("golden_zone_part") if isinstance(dec.get("golden_zone_part"), str) else None,
                  "zone": {"id": dec["zone_id"], "lo": rnd(dec["zone_lo"]), "hi": rnd(dec["zone_hi"]),
                           "center": rnd(dec["zone_center"]), "near_edge": rnd(dec["near_edge"])},
                  "members": members_at(dd, T, float(dec["zone_lo"]), float(dec["zone_hi"])),
                  "founding": member_labels(dd, members), "stack_count": int(row.get("stack_count") or len(members) or 1),
                  "price": rnd(dec["price_at_decision"]), "touch_price": rnd(dec["touch_price"]),
                  "primary_member": SUB_LABEL.get(str(row.get("primary_member_type") or ""), row.get("primary_member_type")),
                  "audit_or_sweep": dec.get("style") == "c", "trigger": dec.get("trigger_kind") if isinstance(dec.get("trigger_kind"), str) else None},
        "trade": trade_payload(pair, fill, int(dec["direction"])),
        "readings": readings(row),
        "stack": stack_payload(dd, row, T, float(dec["zone_lo"]), float(dec["zone_hi"]), dec.get("event_type") == "level"),
        "pre": pre,
        "post": post,
        "outcome": outcome_payload(dd, pair, fill, event),
    }


# ── loading a stored day ───────────────────────────────────────────────────────────────────────────────────────────

def _stack_columns() -> tuple[str, ...]:
    from nqbot.features.session_features import NQ_SUBTYPES

    return ("stack_count", *(f"n_{st}" for st in NQ_SUBTYPES))


FEATURE_COLUMNS = ("founding_level_ids", "primary_member_type", "with_day_direction", *READING_COLUMNS, *_stack_columns())


def feature_rows(build_dir, iso: str, decisions: list[dict[str, Any]], decision_ids: set[str]) -> dict[str, dict[str, Any]]:
    """The pair scorer's inputs of each decision point, by decision_id: a style-(c) point's own row
    (`decision_rows/`, keyed by decision_id), a style-(d) point's = its event's touch row (`touches/`, keyed by
    event_id: (d) decides at the touch, `events/build.py`)."""
    import pyarrow.parquet as pq

    want = {d["decision_id"]: d for d in decisions if d["decision_id"] in decision_ids}
    out: dict[str, dict[str, Any]] = {}
    c_ids = {k for k, d in want.items() if d["style"] != "d"}
    d_events = {d["event_id"]: k for k, d in want.items() if d["style"] == "d"}
    for sub, key, ids in (("decision_rows", "decision_id", c_ids), ("touches", "event_id", set(d_events))):
        if not ids:
            continue
        f = build_dir / sub / f"{iso}.parquet"
        have = set(pq.read_schema(f).names)
        cols = [key] + [c for c in dict.fromkeys(FEATURE_COLUMNS) if c in have and c != key]
        for r in pq.read_table(f, columns=cols).to_pylist():
            if r[key] in ids:
                out[r[key] if key == "decision_id" else d_events[r[key]]] = r
    return out


def _table_rows(path, columns=None, filt=None) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq

    t = pq.read_table(path, columns=columns)
    rows = t.to_pylist()
    return [r for r in rows if filt(r)] if filt else rows


def load_day(env, build_dir, labels_key: str, day: date, decision_ids: set[str], pair_ids: set[str]) -> DayData:
    """One stored session (read-only): the build's files, its labels, the trades and the Skylit boards."""
    import json

    import pyarrow.parquet as pq

    from nqbot.checks import step8b as s8
    from nqbot.clients.databento_store import read_records
    from nqbot.features import boards as bd

    iso = day.isoformat()
    lv_path = build_dir / "levels" / f"{iso}.parquet"
    meta = json.loads((pq.read_metadata(lv_path).metadata or {}).get(b"nqbot", b"{}"))
    levels = _table_rows(lv_path)
    tapes: dict[str, st.Tape] = {}
    symbols = {"NQ": meta.get("nq_symbol"), "ES": meta.get("es_symbol")}
    for root, sym in symbols.items():
        p = env.db.raw_path("trades", sym, day) if sym else None
        if p is not None and p.exists():
            tapes[root] = st.Tape.from_records(day, read_records(p), sym)
    events = _table_rows(build_dir / "events" / f"{iso}.parquet")
    decisions = _table_rows(build_dir / "decisions" / f"{iso}.parquet")
    rows = feature_rows(build_dir, iso, decisions, decision_ids)
    pairs = {r["pair_id"]: r for r in _table_rows(build_dir / "pairs" / labels_key / f"{iso}.parquet",
                                                  filt=lambda r: r["pair_id"] in pair_ids)}
    fill_ids = {p["fill_id"] for p in pairs.values()}
    fills = {r["fill_id"]: r for r in _table_rows(build_dir / "fills" / labels_key / f"{iso}.parquet",
                                                  filt=lambda r: r["fill_id"] in fill_ids)}
    paths_ = {r["pair_id"]: r for r in _table_rows(build_dir / "paths" / labels_key / f"{iso}.parquet",
                                                   filt=lambda r: r["pair_id"] in pair_ids)}
    level_events = _table_rows(build_dir / "level_events" / f"{iso}.parquet")
    open_ns, close_ns = st.rth_open_ns(day), st.rth_close_ns(day, env.cal)
    maps: dict[str, Any] = {}
    base = s8.skylit_base(env, day)
    if env.sky.stored_windows(day, base):
        k = bd.ndxp_k(day, bd.stored_closes(env.paths.raw_dir), env.features)
        for m in ("QQQ", "SPY", "SPX", "NDXP"):
            b, s_ = env.sky.read_range(day, base, symbols=[m])
            fut = tapes.get(CHAIN_ROOT[m])
            kw = {"qqq": maps.get("QQQ"), "k": k} if m == "NDXP" else {}
            maps[m] = bd.from_tables(m, day, b, s_, None if fut is None else fut.ts, None if fut is None else fut.price,
                                     env.features, env.settings, open_ns, close_ns, **kw)
    return DayData(day, open_ns, close_ns, tapes, levels, level_events, events, decisions, rows, pairs, fills, paths_,
                   maps, symbols, env.features)
