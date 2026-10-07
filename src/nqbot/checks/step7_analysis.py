"""Pure analysis for the step-7 checks (SPEC §12 step 7, §3.1). No I/O: arrays and tables in, results out."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import numpy as np
import pyarrow as pa

from nqbot.features.darkpool import same_print

ET = ZoneInfo("America/New_York")
EMPTY_ABS = 50.0  # Skylit API docs (includeEmpty): an interior strike with |value| < 50 may be left out of a board
VALUE_RTOL = 1e-6  # stored values are float32: equal boards agree to float32 precision
SPOT_ATOL = 1e-9
LATENCY_Q = 0.999  # §3.1 Ingest: map_latency_sec = the 99.9th-percentile stream delay
NS = 1_000_000_000


# --- strike window (skylit_max_strikes) ---------------------------------------------------------------

@dataclass
class WindowNeed:
    n: int  # strikes the window must hold for this board
    strike: float | None  # the strike that set it
    king: float


def board_window_need(strikes: np.ndarray, values: np.ndarray, spot: float, drop_pct: float, margin: int) -> WindowNeed:
    """Smallest N such that the N strikes nearest spot keep every strike >= drop_pct x King at least `margin`
    strikes inside the window's edge (§12 step 7). A strike near the end of the listed chain only needs the
    strikes that exist beyond it (the chain itself ends there; nothing is cut off)."""
    order = np.argsort(strikes, kind="stable")
    k, v = np.asarray(strikes, dtype=np.float64)[order], np.asarray(values, dtype=np.float64)[order]
    if len(k) == 0:
        return WindowNeed(0, None, 0.0)
    king = float(np.max(np.abs(v)))
    # rank by distance to spot (ties: the lower strike first), as the API's "N strikes around spot"
    rank = np.empty(len(k), dtype=np.int64)
    rank[np.lexsort((k, np.abs(k - spot)))] = np.arange(len(k))
    need, at = 0, None
    for i in np.flatnonzero(np.abs(v) >= drop_pct * king):
        if k[i] >= spot:
            span = rank[i: min(len(k), i + margin + 1)]
        else:
            span = rank[max(0, i - margin): i + 1]
        n_i = int(span.max()) + 1
        if n_i > need:
            need, at = n_i, float(k[i])
    return WindowNeed(need, at, king)


DRIVERS_LISTED = 10  # §12 step 7 (strike window): a display cap only; the report gives the full count


@dataclass
class StrikeWindowResult:
    per_symbol: dict[str, dict[str, Any]]
    proposed: int
    # The boards that set the proposal (need == proposed) and the window every other board fits in: if one unusual
    # board drives the proposal bigger, the owner sees which and decides (owner 2026-10-06; zero tolerance stays).
    drivers: list[dict[str, Any]] = field(default_factory=list)  # at most DRIVERS_LISTED
    next_smaller: int | None = None
    n_drivers: int = 0  # all boards that set the proposal


def strike_window(boards: pa.Table, strikes: pa.Table, drop_pct: float, margin: int) -> StrikeWindowResult:
    """Every board of full-chain pulls -> the window each symbol needs and the overall proposal (max)."""
    per: dict[str, dict[str, Any]] = {}
    groups = _strikes_by_board(strikes)
    needs: list[tuple[int, str, int, float | None]] = []
    for key, spot in zip(board_keys(boards), boards["spot"].to_pylist()):
        if key not in groups:
            continue
        k, v = groups[key]
        need = board_window_need(k, v, spot, drop_pct, margin)
        needs.append((need.n, key[0], key[1], need.strike))
        rec = per.setdefault(key[0], {"need": 0, "boards": 0, "worst_as_of": None, "worst_strike": None,
                                             "chain_strikes_max": 0})
        rec["boards"] += 1
        rec["chain_strikes_max"] = max(rec["chain_strikes_max"], len(k))
        if need.n > rec["need"]:
            rec.update(need=need.n, worst_as_of=str(np.datetime64(key[1], "ms")), worst_strike=need.strike)
    proposed = max((r["need"] for r in per.values()), default=0)
    drivers = [{"symbol": sym, "as_of": str(np.datetime64(t, "ms")), "strike": strike, "need": n}
               for n, sym, t, strike in needs if n == proposed and proposed > 0]
    smaller = [n for n, *_ in needs if n < proposed]
    return StrikeWindowResult(per, proposed, drivers[:DRIVERS_LISTED], max(smaller) if smaller else None, len(drivers))


def _ms(col: pa.ChunkedArray | pa.Array) -> np.ndarray:
    """A timestamp column as integer ms since the epoch (any unit)."""
    unit = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[col.type.unit]
    raw = col.cast(pa.int64()).to_numpy(zero_copy_only=False)
    return raw * (1000 // unit) if unit <= 1000 else raw // (unit // 1000)


def _ms_of(t: datetime) -> int:
    return int(np.datetime64(t.astimezone(timezone.utc).replace(tzinfo=None), "ms").astype(np.int64))


def board_keys(boards: pa.Table) -> list[tuple[str, int]]:
    return list(zip(boards["symbol"].to_pylist(), _ms(boards["as_of"]).tolist()))


def _strikes_by_board(strikes: pa.Table) -> dict[tuple[str, int], tuple[np.ndarray, np.ndarray]]:
    """Group the per-strike table by board (symbol, as_of ms), vectorized: one sort, then slices.
    A 1-second session is ~19 million strike rows, so no per-row Python (§11 Low memory)."""
    if strikes.num_rows == 0:
        return {}
    sym = strikes["symbol"].to_numpy(zero_copy_only=False).astype(str)
    t = _ms(strikes["as_of"])
    k = strikes["strike"].to_numpy(zero_copy_only=False).astype(np.float64)
    v = strikes["value"].to_numpy(zero_copy_only=False).astype(np.float64)
    order = np.lexsort((k, t, sym))
    sym, t, k, v = sym[order], t[order], k[order], v[order]
    change = np.ones(len(t), dtype=bool)
    change[1:] = (t[1:] != t[:-1]) | (sym[1:] != sym[:-1])
    starts = np.flatnonzero(change)
    ends = np.append(starts[1:], len(t))
    return {(str(sym[s]), int(t[s])): (k[s:e], v[s:e]) for s, e in zip(starts, ends)}


def edge_violations(boards: pa.Table, strikes: pa.Table, drop_pct: float, margin: int) -> dict[str, dict[str, Any]]:
    """Boards pulled with the chosen window: per symbol, boards where a strike >= drop_pct x the board's King
    sits fewer than `margin` strikes from either end of the returned ladder (a sign the window cut nodes off)."""
    groups = _strikes_by_board(strikes)
    out: dict[str, dict[str, Any]] = {}
    for key in board_keys(boards):
        if key not in groups:
            continue
        _, v = groups[key]  # already sorted by strike
        rec = out.setdefault(key[0], {"boards": 0, "violations": 0, "first": None})
        rec["boards"] += 1
        if len(v) == 0:
            continue
        idx = np.flatnonzero(np.abs(v) >= drop_pct * np.max(np.abs(v)))
        if len(idx) and (idx.min() < margin or idx.max() > len(v) - 1 - margin):
            rec["violations"] += 1
            rec["first"] = rec["first"] or str(np.datetime64(key[1], "ms"))
    return out


def nearest_window(strikes: np.ndarray, spot: float, n: int | str) -> set[float]:
    k = np.asarray(strikes, dtype=np.float64)
    if n == "all":  # skylit_max_strikes "all" (step 7, owner 2026-10-06): the whole chain is expected back
        return set(np.round(k, 6).tolist())
    idx = np.lexsort((k, np.abs(k - spot)))[:n]
    return set(np.round(k[idx], 6).tolist())


def window_prediction_mismatches(full_boards: pa.Table, full_strikes: pa.Table, sample_strikes: pa.Table,
                                 n: int | str) -> dict[str, int]:
    """Per symbol: boards (same as_of in both pulls) whose stored strike list differs from the N nearest spot
    in the full chain, ignoring strikes the full chain shows with |value| < EMPTY_ABS (the API may omit them)."""
    full, sample = _strikes_by_board(full_strikes), _strikes_by_board(sample_strikes)
    out: dict[str, int] = {}
    for key, spot in zip(board_keys(full_boards), full_boards["spot"].to_pylist()):
        if key not in full or key not in sample:
            continue
        k, v = full[key]
        predicted = nearest_window(k, spot, n)
        empty = set(np.round(k[np.abs(v) < EMPTY_ABS], 6).tolist())
        got = set(np.round(sample[key][0], 6).tolist())
        out.setdefault(key[0], 0)
        if (predicted ^ got) - empty:
            out[key[0]] += 1
    return out


def zero_dte_misses(boards: pa.Table) -> list[dict[str, Any]]:
    """Boards whose (single) expiration isn't their own ET session date (§3.1: maxExpirations=1 = the 0DTE)."""
    misses: dict[tuple[str, str, str], int] = {}
    for sym, t, exp in zip(boards["symbol"].to_pylist(), boards["as_of"].to_pylist(), boards["expirations"].to_pylist()):
        session = t.astimezone(ET).date().isoformat()
        first = (exp or "").split(",")[0]
        if first != session:
            key = (sym, session, exp or "")
            misses[key] = misses.get(key, 0) + 1
    return [{"symbol": s, "session": d, "expirations": e, "boards": n} for (s, d, e), n in sorted(misses.items())]


# --- stream vs history parity and latency ----------------------------------------------------------------

@dataclass
class ParityResult:
    matched: int = 0
    only_stream: list[tuple[str, str]] = field(default_factory=list)
    only_history: list[tuple[str, str]] = field(default_factory=list)
    revised: list[dict[str, Any]] = field(default_factory=list)  # boards present in both that differ

    @property
    def stop(self) -> bool:
        """§3.1 Ingest: any differing board -> stop and ask the owner."""
        return bool(self.revised)


def compare_boards(stream_boards: pa.Table, stream_strikes: pa.Table, hist_boards: pa.Table, hist_strikes: pa.Table,
                   start: datetime | None = None, end: datetime | None = None) -> ParityResult:
    """Board-by-board comparison by (symbol, asOf), optionally inside [start, end]."""
    lo = None if start is None else _ms_of(start)
    hi = None if end is None else _ms_of(end)

    def boards_of(tbl: pa.Table) -> dict[tuple[str, int], float]:
        return {key: spot for key, spot in zip(board_keys(tbl), tbl["spot"].to_pylist())
                if (lo is None or key[1] >= lo) and (hi is None or key[1] <= hi)}

    sb, hb = boards_of(stream_boards), boards_of(hist_boards)
    ss, hs = _strikes_by_board(stream_strikes), _strikes_by_board(hist_strikes)
    none = (np.array([]), np.array([]))
    res = ParityResult()
    for key in sorted(set(sb) | set(hb)):
        label = (key[0], str(np.datetime64(key[1], "ms")))
        if key not in hb:
            res.only_stream.append(label)
            continue
        if key not in sb:
            res.only_history.append(label)
            continue
        diffs = []
        if abs(sb[key] - hb[key]) > SPOT_ATOL:
            diffs.append("spot differs")
        a = dict(zip(*(x.tolist() for x in ss.get(key, none))))
        b = dict(zip(*(x.tolist() for x in hs.get(key, none))))
        for strike in sorted(set(a) | set(b)):
            va, vb = a.get(strike), b.get(strike)
            if va is None or vb is None:
                present = va if vb is None else vb
                if abs(present) >= EMPTY_ABS:
                    side = "stream" if vb is None else "history"
                    diffs.append(f"strike {strike} only in {side}")
            elif not math.isclose(va, vb, rel_tol=VALUE_RTOL, abs_tol=0.0):
                rel = (vb - va) / abs(va) if va else float("inf")
                diffs.append(f"strike {strike}: relative change {rel:+.2e}")
        if diffs:
            res.revised.append({"symbol": key[0], "as_of": label[1], "diffs": diffs[:10], "n_diffs": len(diffs)})
        else:
            res.matched += 1
    return res


def quantile(values: list[float] | np.ndarray, q: float) -> float | None:
    arr = np.sort(np.asarray(values, dtype=np.float64))
    if len(arr) == 0:
        return None
    return float(arr[max(0, math.ceil(q * len(arr)) - 1)])  # nearest rank (no interpolation upward)


def latency_report(stream_boards: pa.Table) -> dict[str, Any]:
    """arrival - asOf per board, leaving out each connection's first board per symbol (a connect or resume
    snapshot can be an old, unchanged board). Also the gap between consecutive boards per symbol."""
    rows = sorted(stream_boards.to_pylist(), key=lambda r: (r["symbol"], r["arrival"]))
    seen: set[tuple[str, int]] = set()
    delays: dict[str, list[float]] = {}
    gaps: dict[str, list[float]] = {}
    last: dict[str, Any] = {}
    for r in rows:
        key = (r["symbol"], r["connection"])
        sym = r["symbol"]
        if sym in last:
            gaps.setdefault(sym, []).append((r["as_of"] - last[sym]).total_seconds())
        last[sym] = r["as_of"]
        if key in seen:
            delays.setdefault(sym, []).append((r["arrival"] - r["as_of"]).total_seconds())
        seen.add(key)
    every = [d for v in delays.values() for d in v]

    def stats(v: list[float]) -> dict[str, Any]:
        return {"n": len(v), "p50": quantile(v, 0.5), "p99": quantile(v, 0.99), "p999": quantile(v, LATENCY_Q),
                "max": max(v) if v else None}

    p999 = quantile(every, LATENCY_Q)
    return {
        "delay_sec": {s: stats(v) for s, v in sorted(delays.items())},
        "delay_all": stats(every),
        "gap_sec": {s: {"n": len(v), "p50": quantile(v, 0.5), "p90": quantile(v, 0.9), "max": max(v) if v else None}
                    for s, v in sorted(gaps.items())},
        "lagged": int(sum(1 for r in rows if r["lagged"])),
        "proposed_map_latency_sec": None if p999 is None else math.ceil(p999 * 100) / 100,
    }


def event_report(events: list[dict[str, Any]]) -> dict[str, Any]:
    """From the recorder's events.jsonl: ping cadence, reconnect gap, credits."""
    pings = sorted(e["arrival_ns"] for e in events if e["kind"] == "ping")
    ping_gaps = [(b - a) / NS for a, b in zip(pings, pings[1:])]
    by_conn: dict[int, list[int]] = {}
    for e in events:
        by_conn.setdefault(e["connection"], []).append(e["arrival_ns"])
    conns = sorted(by_conn)
    reconnect_gaps = [(min(by_conn[b]) - max(by_conn[a])) / NS for a, b in zip(conns, conns[1:])]
    credits = [e["data"]["remaining"] for e in events if e["kind"] == "credits" and e.get("data")]
    connected = [e["data"].get("creditsRemaining") for e in events if e["kind"] == "connected" and e.get("data")]
    resumed = [e["data"] for e in events if e["kind"] == "resumed" and e.get("data")]
    start = connected[0] if connected else None
    end = credits[-1] if credits else None
    return {
        "pings": len(pings), "ping_gap_p50": quantile(ping_gaps, 0.5), "ping_gap_max": max(ping_gaps) if ping_gaps else None,
        "connections": len(conns), "reconnect_gap_sec": reconnect_gaps, "resumed": resumed,
        "credits_start": start, "credits_end": end,
        "credits_used": (start - end) if start is not None and end is not None else None,
    }


def extra_history_boards(stream_boards: pa.Table, hist_boards: pa.Table, start: datetime, end: datetime) -> dict[str, Any]:
    """Input for the §3.1 thinning rule when history holds boards the stream never sent: per symbol, how many,
    how far each sits from the nearest streamed board, and how many share a clock second with one."""
    lo, hi = _ms_of(start), _ms_of(end)

    def times(tbl: pa.Table) -> dict[str, np.ndarray]:
        out: dict[str, list[int]] = {}
        for sym, t in board_keys(tbl):
            if lo <= t <= hi:
                out.setdefault(sym, []).append(t)
        return {k: np.unique(np.asarray(v, dtype=np.int64)) for k, v in out.items()}

    st, ht = times(stream_boards), times(hist_boards)
    out = {}
    for sym, hist in ht.items():
        streamed = st.get(sym, np.array([], dtype=np.int64))
        extra = hist[~np.isin(hist, streamed)]
        rec: dict[str, Any] = {"history": int(len(hist)), "stream": int(len(streamed)), "extra_in_history": int(len(extra))}
        if len(extra) and len(streamed):
            right = np.minimum(np.searchsorted(streamed, extra), len(streamed) - 1)
            left = np.maximum(right - 1, 0)
            nearest = np.minimum(np.abs(extra - streamed[left]), np.abs(extra - streamed[right])) / 1000.0
            rec.update(extra_sec_to_nearest_streamed={"p50": quantile(nearest, 0.5), "max": float(nearest.max())},
                       extra_same_second_as_streamed=int(np.isin(extra // 1000, streamed // 1000).sum()),
                       history_gap_p50_sec=quantile(np.diff(hist) / 1000.0, 0.5) if len(hist) > 1 else None,
                       stream_gap_p50_sec=quantile(np.diff(streamed) / 1000.0, 0.5) if len(streamed) > 1 else None)
        out[sym] = rec
    return out


def stream_shape(boards: pa.Table, strikes: pa.Table) -> dict[str, Any]:
    """Did the stream honour maxStrikes and maxExpirations=1: strikes per board and expiration sets per symbol."""
    groups = _strikes_by_board(strikes)
    acc: dict[str, dict[str, Any]] = {}
    for (sym, t), exp in zip(board_keys(boards), boards["expirations"].to_pylist()):
        rec = acc.setdefault(sym, {"max": 0, "sets": set()})
        rec["max"] = max(rec["max"], len(groups.get((sym, t), ((), ()))[0]))
        rec["sets"].add(exp or "")
    return {s: {"max_strikes_per_board": r["max"], "expiration_sets": sorted(r["sets"]),
                "single_expiration": all("," not in e for e in r["sets"])} for s, r in acc.items()}


# --- dark pools ---------------------------------------------------------------------------------------

def compare_print_sets(first: list[dict[str, Any]], second: list[dict[str, Any]]) -> dict[str, Any]:
    """Prints added or removed between two pulls of the same dates (identity = ticker + timestamp + price),
    and same-identity prints whose size or notional changed."""
    def key(p: dict[str, Any]) -> tuple[Any, ...]:
        return (p["ticker"], p["timestamp"], round(float(p["price"]), 6))

    a = {}
    for p in first:
        a.setdefault(key(p), []).append(p)
    b = {}
    for p in second:
        b.setdefault(key(p), []).append(p)
    added = [p for k, ps in b.items() for p in ps[len(a.get(k, [])):]]
    removed = [p for k, ps in a.items() for p in ps[len(b.get(k, [])):]]
    changed = [{"first": a[k][0], "second": b[k][0]} for k in set(a) & set(b)
               if (a[k][0]["size"], a[k][0]["notional"]) != (b[k][0]["size"], b[k][0]["notional"])]
    return {"added": added, "removed": removed, "changed": changed}


def same_top(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> bool:
    return len(a) == len(b) and all(same_print(x, y) for x, y in zip(a, b))


def top_matches_api(bot: list[dict[str, Any]], api: list[dict[str, Any]], price_tol: float = 1e-6) -> bool:
    """Skylit's top-prints rows carry no ticker: the same prints (timestamp, price, notional) with the same notional
    ranking. Step 7 (2026-10-07): top-prints timestamps are whole seconds (cut, not rounded) while /trades keeps
    milliseconds, and Skylit orders an exact notional tie earlier-first where F4 puts the later print first; neither
    changes the list, so timestamps are compared to the second and order only between different notionals."""
    if len(bot) != len(api):
        return False

    def sec(t: Any) -> Any:
        return t.replace(microsecond=0)

    def same(x: dict[str, Any], y: dict[str, Any]) -> bool:
        return (sec(x["timestamp"]) == sec(y["timestamp"]) and abs(float(x["price"]) - float(y["price"])) <= price_tol
                and math.isclose(float(x["notional"]), float(y["notional"]), rel_tol=1e-9))

    if not all(math.isclose(float(x["notional"]), float(y["notional"]), rel_tol=1e-9) for x, y in zip(bot, api)):
        return False  # the ranking itself differs
    left = list(api)
    for x in bot:  # within a tie any order: match each print once
        hit = next((i for i, y in enumerate(left) if same(x, y)), None)
        if hit is None:
            return False
        left.pop(hit)
    return True


# --- Tempest -----------------------------------------------------------------------------------------

def compare_vol_tables(first: pa.Table, second: pa.Table, rtol: float = 1e-9) -> list[dict[str, Any]]:
    """Cells that changed between two pulls of the same Tempest rows (rows present in both)."""
    def rows(t: pa.Table) -> dict[tuple[str, Any], dict[str, Any]]:
        return {(r["symbol"], r["date"]): r for r in t.to_pylist()}

    ra, rb = rows(first), rows(second)
    diffs = []
    for key in sorted(set(ra) & set(rb), key=str):
        for f in sorted((set(ra[key]) | set(rb[key])) - {"symbol", "date"}):
            va, vb = ra[key].get(f), rb[key].get(f)
            same = (va is None and vb is None) or (va is not None and vb is not None and math.isclose(va, vb, rel_tol=rtol))
            if not same:
                diffs.append({"symbol": key[0], "date": str(key[1]), "field": f})  # no values: §7 rule 2b
    return diffs


# --- historical instants ----------------------------------------------------------------------------------

def pick_instants(days: list[date], n: int, seed: int, *, close_of: Callable[[date], time]) -> list[datetime]:
    """n instants spread over the given trading days (deterministic): days evenly spaced through the list,
    a random second between 09:30 and the day's close for each."""
    if not days:
        return []
    rng = random.Random(seed)
    picks = [days[round(i * (len(days) - 1) / max(1, n - 1))] for i in range(n)] if len(days) >= 1 else []
    out = []
    for d in picks:
        open_dt = datetime.combine(d, time(9, 30), tzinfo=ET)
        span = int((datetime.combine(d, close_of(d), tzinfo=ET) - open_dt).total_seconds())
        out.append(open_dt + timedelta(seconds=rng.randrange(1, span)))
    return sorted(set(out))
