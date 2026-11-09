"""Zone merge, edges and identity (SPEC §5.4 Zone object; feature_spec F5.3, F5.4, F5.5), and the session's zone book.

**Merge (F5.3)** — `merge(levels, prev, ...)` is a pure function of the active levels at one timestamp (their bands)
and the zones in force just before it:
1. two levels qualify when their bands overlap (touching counts) or their centres are <= `confluence_tol` apart;
2. (a) existing zones keep their still-active members; (b) a zone of two or more members keeps a member only while
   every member qualifies with another kept member and the zone width (the union of the bands) is <= `max_zone_width`;
   otherwise the member farthest from the zone's founding anchors (the centre of the founding members' bands; if none
   of them is active any more, the centre of the zone's current members) is released, and the check repeats;
   (c) released and new levels, in ascending band-centre price (ties: `level_id`), each join the nearest qualifying
   zone (zone centre to level centre) if the merged width stays <= `max_zone_width` (ties: the zone holding the larger
   % of King, then the older zone), else found a new zone; (d) zones that now qualify with each other (some pair of
   members qualifies) merge if the width allows, nearest pair first (ties: the older pair), the older `zone_id`
   surviving with its founding anchors;
3. the result depends only on the set of levels, never on their order (tested).
**Hysteresis (step 13, owner 2026-11-04, SPEC §13 Q-step12a (2)):** `zone_join_buffer` (jb): in (c) and (d) a join or a
merge needs the merged width <= `max_zone_width` - jb; `zone_qualify_buffer` (qb): in (b) a kept member stays while it
qualifies with another kept member within qb more (bands <= qb apart, or centres <= `confluence_tol` + qb). The release
rule and the hard cap `max_zone_width` are unchanged, so no zone is ever wider than it. Both 0 = the rule above.
Containers (golden zone, gameplan zones) never merge and are not passed here (F5.9).

**Edges (F5.4)** — the union of the active members' bands at t. A member that is crossed by its own rule is no longer
active (`level_state`: active = not crossed, or crossed and armed in its provisional role, F2.6 step 5), so it leaves
the zone; re-armed in its provisional role it is active again and merges like any level.

**Identity (F5.5)** — a zone gets `z<n>` (unique per session) when founded, with its founding anchors (`level_id`s);
members joining or leaving don't change it. A zone absorbed by an older one ends (`merged_into`).

**ZoneBook** replays one session. The merge result can only change when the set of active levels changes, a band moves
(a board's `t_apply`, a profile instant, a level created, ended, crossed, armed) or — for the VWAP lines, whose bands
move on every trade — when a pairwise relation the merge reads (qualify; pair hull <= `max_zone_width`) flips. Those
are the candidate times; at each the relations are recomputed (as of t: after every update with time <= t) and the
merge runs only if the active set or a relation changed (the merge's result is a function of the relations given the
previous zones, and its output is a fixed point). Pair hulls are enough for the width test: the hull of a set is the
largest hull of any pair in it. The flip times of moving bands are found per pair over the trades, vectorised, and
(step 13, owner 2026-11-04, Q-step12a (1)) also at the partner's own step times between trades: a relation that flips at
a band step and back at the next trade makes that next trade a candidate (2026-03-17 09:35:09). A parity test checks the
book against recomputing at every trade (`tests/test_zones.py`).
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Iterable

import numpy as np

from nqbot.features.bands import TradeBand, values_at

EPS = 1e-9
BLOCK = 2048   # candidate instants evaluated at once (memory: levels x BLOCK floats per array)
# Q-step12a (1) (owner 2026-11-04): pairs also checked at the partner's step times. A diagnostic switch of the
# step-13-0 check only (False = features_version <= 36); every feature build refuses another value
# (`features.diagnostics.require_defaults`).
STEP_TIME_CANDIDATES = True


# --- the pure merge ----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Geom:
    """One active level at the merge instant."""
    level_id: str
    lo: float
    hi: float
    pct_of_king: float | None = None

    @property
    def centre(self) -> float:
        return (self.lo + self.hi) / 2.0


@dataclass
class Zone:
    zone_id: str
    members: list            # level_ids
    anchors: tuple           # founding members' level_ids
    seq: int                 # creation order (smaller = older)


def qualify(a: Geom, b: Geom, tol: float, buf: float = 0.0) -> bool:
    """F5.3 rule 1 (bands overlap, touching counts, or centres <= tol apart); `buf` widens both tests (the qualify
    buffer for kept members, Q-step12a (2))."""
    return (a.lo <= b.hi + buf + EPS and b.lo <= a.hi + buf + EPS) or abs(a.centre - b.centre) <= tol + buf + EPS


def _width(gs: Iterable[Geom]) -> float:
    gs = list(gs)
    return max(g.hi for g in gs) - min(g.lo for g in gs)


def _centre(gs: Iterable[Geom]) -> float:
    gs = list(gs)
    return (max(g.hi for g in gs) + min(g.lo for g in gs)) / 2.0


def _pct(gs: Iterable[Geom]) -> float:
    return max((g.pct_of_king for g in gs if g.pct_of_king is not None), default=-1.0)


class IdSource:
    def __init__(self, start: int = 0):
        self.n = start

    def next(self) -> tuple[str, int]:
        self.n += 1
        return f"z{self.n}", self.n


def merge(levels: list[Geom], prev: list[Zone], tol: float, max_width: float, ids: IdSource,
          join_buf: float = 0.0, qual_buf: float = 0.0) -> tuple[list[Zone], dict[str, str]]:
    """F5.3 at one timestamp. Returns (zones, absorbed: {younger zone_id: survivor zone_id})."""
    join_width = max_width - join_buf
    by_id = {g.level_id: g for g in levels}
    zones: list[Zone] = []
    placed: set[str] = set()
    released: list[str] = []
    for z in sorted(prev, key=lambda z: z.seq):
        kept = [m for m in z.members if m in by_id and m not in placed]
        while len(kept) >= 2:
            gs = [by_id[m] for m in kept]
            ok_width = _width(gs) <= max_width + EPS
            ok_q = all(any(qualify(by_id[a], by_id[b], tol, qual_buf) for b in kept if b != a) for a in kept)
            if ok_width and ok_q:
                break
            founders = [by_id[a] for a in z.anchors if a in by_id and a in kept]
            ref = _centre(founders) if founders else _centre(gs)
            far = max(kept, key=lambda m: (abs(by_id[m].centre - ref), m))
            kept.remove(far)
            released.append(far)
        if kept:
            zones.append(Zone(z.zone_id, kept, z.anchors, z.seq))
            placed.update(kept)
    pending = sorted({*released, *(g.level_id for g in levels if g.level_id not in placed)} - placed,
                     key=lambda m: (by_id[m].centre, m))
    for m in pending:
        g = by_id[m]
        best = None
        for z in zones:
            gs = [by_id[x] for x in z.members]
            if not any(qualify(g, h, tol) for h in gs):
                continue
            if _width(gs + [g]) > join_width + EPS:
                continue
            key = (abs(_centre(gs) - g.centre), -_pct(gs), z.seq)
            if best is None or key < best[0]:
                best = (key, z)
        if best is not None:
            best[1].members.append(m)
        else:
            zid, seq = ids.next()
            zones.append(Zone(zid, [m], (m,), seq))
    absorbed: dict[str, str] = {}
    while True:
        cand = None
        for i, a in enumerate(zones):
            ga = [by_id[x] for x in a.members]
            for b in zones[i + 1:]:
                gb = [by_id[x] for x in b.members]
                if not any(qualify(x, y, tol) for x in ga for y in gb):
                    continue
                if _width(ga + gb) > join_width + EPS:
                    continue
                key = (abs(_centre(ga) - _centre(gb)), min(a.seq, b.seq), max(a.seq, b.seq))
                if cand is None or key < cand[0]:
                    cand = (key, a, b)
        if cand is None:
            break
        _, a, b = cand
        old, young = (a, b) if a.seq < b.seq else (b, a)
        old.members = old.members + young.members
        absorbed[young.zone_id] = old.zone_id
        zones.remove(young)
    for z in zones:   # canonical member order
        z.members = sorted(z.members, key=lambda m: (by_id[m].centre, m))
    return sorted(zones, key=lambda z: z.seq), absorbed


# --- levels the book reads ---------------------------------------------------------------------------------------

@dataclass
class BookLevel:
    """What the zone book needs of a registry level: its band over time and when it is active."""
    level_id: str
    band: Any                          # bands.StepBand / bands.TradeBand
    spans: list                        # [(start_ns, end_ns)) active
    pct_of_king: Callable[[int], float | None] | None = None
    change_times: np.ndarray | None = None   # instants the band moves (StepBand.times); TradeBand: every trade
    pct_series: tuple | None = None          # (times, values) behind pct_of_king, for evaluating many instants at once

    def __post_init__(self):
        self.spans = _normalise(self.spans)
        self._edges = [x for s in self.spans for x in s]

    def active(self, t: int) -> bool:
        return bisect_right(self._edges, t) % 2 == 1

    def bounds(self, a: int, z: int) -> tuple[float, float] | None:
        """The lowest lo and highest hi the band takes on [a, z) (None if it never exists there)."""
        b = self.band
        if isinstance(b, TradeBand):
            i0, i1 = b.tape.idx_before(a), b.tape.idx_before(z)
            c = b.center[i0:i1]
            c = c[np.isfinite(c)]
            return None if not len(c) else (float(c.min()) - b.half_width, float(c.max()) + b.half_width)
        k0 = max(int(np.searchsorted(b.times, a, side="right")) - 1, 0)
        k1 = int(np.searchsorted(b.times, z, side="left"))
        lo, hi = b.lo[k0:max(k1, k0 + 1)], b.hi[k0:max(k1, k0 + 1)]
        ok = np.isfinite(lo)
        return None if not ok.any() else (float(lo[ok].min()), float(hi[ok].max()))

    @property
    def moving(self) -> bool:
        return isinstance(self.band, TradeBand)


# --- the session book --------------------------------------------------------------------------------------------

@dataclass
class ZoneBook:
    levels: list                         # BookLevel
    tape: Any                            # the levels' instrument tape (trades = the moving bands' clock)
    params: Callable[[int], tuple]       # t -> (confluence_tol, max_zone_width[, zone_join_buffer, zone_qualify_buffer])
                                         # in force (ES: × ES ÷ NQ, F0.11); the buffers default to 0
    param_times: tuple = ()              # instants the params change
    end_ns: int | None = None
    times: list = field(default_factory=list)          # snapshot instants (membership changed)
    snaps: list = field(default_factory=list)          # [{zone_id: tuple(member ids)}]
    created: dict = field(default_factory=dict)        # zone_id -> t
    ended: dict = field(default_factory=dict)          # zone_id -> t (no members left, or absorbed)
    merged_into: dict = field(default_factory=dict)    # zone_id -> (t, survivor)
    anchors: dict = field(default_factory=dict)        # zone_id -> founding level_ids
    recomputes: int = 0
    candidates: int = 0
    _segs: dict | None = None

    def _p(self, t: int) -> tuple[float, float, float, float]:
        p = tuple(self.params(t))
        return (p + (0.0, 0.0))[:4] if len(p) < 4 else p[:4]

    def run(self, every_trade: bool = False) -> ZoneBook:
        """`every_trade`: recompute at every trade too (the parity test's reference).
        Every level's activity, band and % of King are evaluated at a block of candidate instants at once (the same
        values `active`, `band.at` and `pct_of_king` give one instant at a time); the merge runs only where the
        active set or a relation changed."""
        by_id = {x.level_id: x for x in self.levels}
        lv = [by_id[k] for k in sorted(by_id)]          # the active set in level_id order
        names = [x.level_id for x in lv]
        times = self._candidate_times(every_trade)
        self.candidates = len(times)
        ids = IdSource()
        prev: list[Zone] = []
        last_key = None
        cur: dict[str, tuple] = {}
        tarr = np.asarray(times, dtype=np.int64)
        for b0 in range(0, len(tarr), BLOCK):
            tb = tarr[b0:b0 + BLOCK]
            ok, lo, hi, pk = _evaluate(lv, tb)
            for j, t in enumerate(tb.tolist()):
                act = np.flatnonzero(ok[j])
                tol, maxw, jb, qb = self._p(t)
                key = (act.tobytes(), _relations_arr(lo[j, act], hi[j, act], tol, maxw, jb, qb), tol, maxw, jb, qb)
                if key == last_key:
                    continue
                last_key = key
                self.recomputes += 1
                geoms = [Geom(names[i], float(lo[j, i]), float(hi[j, i]),
                              None if lv[i].pct_of_king is None or np.isnan(pk[j, i]) else float(pk[j, i]))
                         for i in act.tolist()]
                zones, absorbed = merge(geoms, prev, tol, maxw, ids, jb, qb)
                new = {z.zone_id: tuple(z.members) for z in zones}
                for z in zones:
                    if z.zone_id not in self.created:
                        self.created[z.zone_id] = t
                        self.anchors[z.zone_id] = z.anchors
                for young, old in absorbed.items():
                    self.merged_into[young] = (t, old)
                for zid in cur:
                    if zid not in new and zid not in self.ended:
                        self.ended[zid] = t
                if new != cur:
                    self.times.append(t)
                    self.snaps.append(new)
                    cur = new
                prev = zones
        return self

    def _candidate_times(self, every_trade: bool) -> list[int]:
        parts = []
        end = self.end_ns
        for x in self.levels:
            for a, z in x.spans:
                parts.append(np.array([a, z], dtype=np.int64))
            if x.change_times is not None:
                parts.append(np.asarray(x.change_times, dtype=np.int64))
        parts.append(np.asarray(self.param_times, dtype=np.int64))
        parts.append(self._flip_times())
        if every_trade:
            parts.append(self.tape.ts)
        t = np.unique(np.concatenate(parts)) if parts else np.zeros(0, dtype=np.int64)
        if end is not None:
            t = t[t < end]
        return t.tolist()

    def _flip_times(self) -> np.ndarray:
        """Trades at which a pairwise relation involving a moving band (VWAP) flips."""
        moving = [x for x in self.levels if x.moving]
        if not moving:
            return np.zeros(0, dtype=np.int64)
        tape = self.tape
        out = [np.zeros(0, dtype=np.int64)]
        done = set()
        for m in moving:
            for o in self.levels:
                if o is m or (o.level_id, m.level_id) in done:
                    continue
                done.add((m.level_id, o.level_id))
                for a, z in _split_at(_overlap_spans(m.spans, o.spans), self.param_times):
                    i0, i1 = tape.idx_before(a), tape.idx_before(z)
                    # start one trade earlier: the state at a is the line after the last trade before a, so a flip at
                    # the first trade of [a, z) is a change from it (an extra candidate time is harmless)
                    s0 = i0 - 1 if i0 > 0 else i0
                    if i1 - s0 < 2:
                        continue
                    a0 = int(tape.ts[s0]) if s0 < i0 else a
                    bm, bo = m.bounds(a0, z), o.bounds(a0, z)
                    tol, maxw, jb, qb = self._p(a)
                    pad = max(tol + qb, maxw) + 1.0
                    if bm is None or bo is None or bm[1] < bo[0] - pad or bo[1] < bm[0] - pad:
                        continue   # never within a zone width of each other on [a, z): no relation can flip
                    lo1, hi1 = m.band.at_trades(s0, i1)
                    lo2, hi2 = o.band.at_trades(s0, i1)
                    ts = tape.ts[s0:i1]
                    steps = o.change_times if (STEP_TIME_CANDIDATES and o.change_times is not None) else None
                    if steps is not None:
                        steps = np.asarray(steps, dtype=np.int64)
                        steps = steps[(steps > ts[0]) & (steps < z)]
                    if steps is not None and len(steps):
                        # the partner's step times between trades (Q-step12a (1)): the bands as of each step (after
                        # every update <= it) join the trade sequence, a step placed after the trades of its timestamp
                        s_lo1, s_hi1 = values_at(m.band, steps)
                        s_lo2, s_hi2 = values_at(o.band, steps)
                        order = np.lexsort((np.r_[np.zeros(len(ts)), np.ones(len(steps))], np.r_[ts, steps]))
                        ts = np.r_[ts, steps][order]
                        lo1, hi1 = np.r_[lo1, s_lo1][order], np.r_[hi1, s_hi1][order]
                        lo2, hi2 = np.r_[lo2, s_lo2][order], np.r_[hi2, s_hi2][order]
                    code = _pair_codes(lo1, hi1, lo2, hi2, tol, maxw, jb, qb)
                    ch = np.flatnonzero(code[1:] != code[:-1]) + 1
                    if len(ch):
                        out.append(ts[ch])
        return np.unique(np.concatenate(out))

    # --- queries -------------------------------------------------------------------------------------------------
    def zones_at(self, t: int) -> dict[str, tuple]:
        k = bisect_right(self.times, t) - 1
        return self.snaps[k] if k >= 0 else {}

    def members(self, zone_id: str, t: int) -> tuple:
        return self.zones_at(t).get(zone_id, ())

    def follow(self, zone_id: str, t: int) -> str | None:
        """The zone carrying `zone_id`'s members at t (itself, or the older zone that absorbed it)."""
        z = zone_id
        for _ in range(64):
            if z in self.zones_at(t):
                return z
            m = self.merged_into.get(z)
            if m is None or m[0] > t:
                return None
            z = m[1]
        return None

    def edges(self, zone_id: str, t: int, bands: dict) -> tuple[float, float] | None:
        bs = [bands[m].at(t) for m in self.members(zone_id, t)]
        bs = [b for b in bs if b is not None]
        if not bs:
            return None
        return min(b[0] for b in bs), max(b[1] for b in bs)

    def segments(self, zone_id: str) -> list[tuple[int, int, tuple]]:
        """[(start, end, members)) of `zone_id` itself (not its survivor)."""
        if self._segs is None:
            segs: dict[str, list] = {}
            for k, (t, snap) in enumerate(zip(self.times, self.snaps)):
                end = self.times[k + 1] if k + 1 < len(self.times) else (self.end_ns or t + 1)
                for zid, mem in snap.items():
                    out = segs.setdefault(zid, [])
                    if out and out[-1][1] == t and out[-1][2] == mem:
                        out[-1] = (out[-1][0], end, mem)
                    else:
                        out.append((t, end, mem))
            self._segs = segs
        return self._segs.get(zone_id, [])

    def zone_ids(self) -> list[str]:
        return sorted(self.created, key=lambda z: int(z[1:]))


def _pair_codes(lo1, hi1, lo2, hi2, tol: float, maxw: float, jb: float = 0.0, qb: float = 0.0) -> np.ndarray:
    """Per instant, the relations of one pair the merge reads, packed in one code (-1 where a band is missing):
    qualify, hull <= max width, and with the hysteresis (Q-step12a (2)) qualify within qb and hull <= max width - jb."""
    ok = np.isfinite(lo1) & np.isfinite(lo2)
    c1, c2 = (lo1 + hi1) / 2, (lo2 + hi2) / 2
    with np.errstate(invalid="ignore"):
        q = ((lo1 <= hi2 + EPS) & (lo2 <= hi1 + EPS)) | (np.abs(c1 - c2) <= tol + EPS)
        hull = np.maximum(hi1, hi2) - np.minimum(lo1, lo2)
        w = hull <= maxw + EPS
        code = q.astype(np.int8) * 2 + w.astype(np.int8)
        if jb or qb:
            qq = ((lo1 <= hi2 + qb + EPS) & (lo2 <= hi1 + qb + EPS)) | (np.abs(c1 - c2) <= tol + qb + EPS)
            wj = hull <= maxw - jb + EPS
            code = code + qq.astype(np.int8) * 8 + wj.astype(np.int8) * 4
    return np.where(ok, code, -1)


def _relations(geoms: list[Geom], tol: float, maxw: float, jb: float = 0.0, qb: float = 0.0) -> bytes:
    return _relations_arr(np.array([g.lo for g in geoms]), np.array([g.hi for g in geoms]), tol, maxw, jb, qb)


@lru_cache(maxsize=512)
def _triu(n: int):
    return np.triu_indices(n, 1)


def _relations_arr(lo: np.ndarray, hi: np.ndarray, tol: float, maxw: float, jb: float = 0.0, qb: float = 0.0) -> bytes:
    """The pairwise relations the merge reads (qualify; pair hull <= max width; with the hysteresis also qualify within
    qb and hull <= max width - jb), packed."""
    n = len(lo)
    if n < 2:
        return b""
    c = (lo + hi) / 2
    q = ((lo[:, None] <= hi[None, :] + EPS) & (lo[None, :] <= hi[:, None] + EPS)) | \
        (np.abs(c[:, None] - c[None, :]) <= tol + EPS)
    w = (np.maximum(hi[:, None], hi[None, :]) - np.minimum(lo[:, None], lo[None, :])) <= maxw + EPS
    iu = _triu(n)
    parts = [q[iu], w[iu]]
    if jb or qb:
        qq = ((lo[:, None] <= hi[None, :] + qb + EPS) & (lo[None, :] <= hi[:, None] + qb + EPS)) | \
            (np.abs(c[:, None] - c[None, :]) <= tol + qb + EPS)
        wj = (np.maximum(hi[:, None], hi[None, :]) - np.minimum(lo[:, None], lo[None, :])) <= maxw - jb + EPS
        parts += [qq[iu], wj[iu]]
    return np.packbits(np.concatenate(parts)).tobytes()


def _evaluate(levels: list, ts: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(ok, lo, hi, pct) of every level at each instant of `ts`, shaped (len(ts), levels): ok = active and the band
    exists (`BookLevel.active`, `band.at`), pct = `pct_of_king` (NaN for None), the same values one instant at a time
    gives."""
    n, m = len(ts), len(levels)
    ok = np.zeros((n, m), dtype=bool)
    lo = np.full((n, m), np.nan)
    hi = np.full((n, m), np.nan)
    pk = np.full((n, m), np.nan)
    for i, x in enumerate(levels):
        act = (np.searchsorted(np.asarray(x._edges, dtype=np.int64), ts, side="right") % 2) == 1
        if not act.any():
            continue
        l_, h_ = values_at(x.band, ts)
        good = act & ~np.isnan(l_)
        ok[:, i] = good
        lo[:, i] = np.where(good, l_, np.nan)
        hi[:, i] = np.where(good, h_, np.nan)
        if x.pct_of_king is not None:
            if x.pct_series is not None:
                st, sv = x.pct_series
                k = np.searchsorted(st, ts, side="right") - 1
                v = np.where(k >= 0, np.asarray(sv, float)[np.maximum(k, 0)], np.nan)
                pk[:, i] = np.where(good & np.isfinite(v), v, np.nan)
            else:
                pk[:, i] = [np.nan if not g else (np.nan if (p := x.pct_of_king(int(t))) is None else p)
                            for g, t in zip(good, ts)]
    return ok, lo, hi, pk


def _normalise(spans) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for a, z in sorted((int(a), int(z)) for a, z in spans if z > a):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], z)
        else:
            out.append([a, z])
    return [(a, z) for a, z in out]


def _split_at(spans: list, cuts) -> list[tuple[int, int]]:
    """The spans cut at every instant of `cuts` inside them (each piece has one parameter set)."""
    cs = sorted(int(c) for c in cuts)
    out = []
    for a, z in spans:
        for c in cs:
            if a < c < z:
                out.append((a, c))
                a = c
        out.append((a, z))
    return out


def _overlap_spans(a: list, b: list) -> list[tuple[int, int]]:
    out = []
    for a0, a1 in a:
        for b0, b1 in b:
            s, e = max(a0, b0), min(a1, b1)
            if s < e:
                out.append((s, e))
    return out
