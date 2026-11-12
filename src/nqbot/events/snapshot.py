"""A decision point's frozen view at its `decision_ts` (step 13b; SPEC §6.2, §6.3, §6.4, §5.5b; feature_spec F7, F11.1-F11.3;
owner 2026-11-10, SPEC §13 Q-step13b (1)).

Everything here is known at `decision_ts` (the lookahead test, `tests/test_events_snapshot.py`) except the **band
track**: the entry zone's removal times over the next `TRACK_HORIZON_SEC`, read only to remove a pending entry (F7: the
zone crossed, vanished, or its reference point moved more than `entry_reprice_tol` from where it was at `decision_ts`),
exactly as live watches the zone while the order rests.

Zone edges are read through `SessionFeatures.world` (Q-step11g (2), Q-step11h (2)): from the moment the event's touch
group made a retested extreme, the session as it would be without it.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from nqbot.events.common import CHART_SOURCE, source_of
from nqbot.features import golden_zone as gz
from nqbot.features.bands import values_at
from nqbot.features.session_tape import NS

TRACK_HORIZON_SEC = 300    # F16.4: the largest `tune_grids_13.execution.entry_ttl_sec` (tested); the band track's span
FIB_TARGETS = (0.0, -0.272, -0.618)   # F10: level 0 and the extensions are golden-zone target candidates
FIB_STOP = 1.0                        # F10: level 1.0 is stop candidate 6


def own_time(sf, e) -> int | None:
    """When the event's touch group first made a retested extreme (`world` switches there), else None."""
    grp = sf.group_of(e)
    return min((r.created_ns for r in sf.retested_today if r.source in grp), default=None)


def group_horizon(sf, e) -> int:
    """The latest instant the engine stage reads `e`'s world at (owner 2026-11-12, SPEC §13 Q-step13b (e)): every
    decision point of the touch group is before its event's `t_pair_end` (Q-step11h (1)), the style-(c) scans stop
    there, and the band track reads `TRACK_HORIZON_SEC` past a decision point."""
    return max(int(sf.pair_end[x].t_resolved) for x in sf.group_of(e)) + TRACK_HORIZON_SEC * NS


def edges_many(sf, e, ts) -> tuple[np.ndarray, np.ndarray]:
    """The event zone's (lo, hi) at each sorted instant (NaN where it has no edges), the values `zone_edges` gives."""
    ts = np.asarray(ts, dtype=np.int64)
    ot = own_time(sf, e)
    if ot is not None and len(ts) and int(ts[-1]) >= ot:
        k = int(np.searchsorted(ts, ot, side="left"))
        a = _edges_self(sf, e, ts[:k])
        b = _edges_after(sf, e, ot, ts[k:])
        return np.r_[a[0], b[0]], np.r_[a[1], b[1]]
    return _edges_self(sf, e, ts)


def _edges_after(sf, e, ot: int, ts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Edges at instants at or after the touch group's own retested extreme (`ot`): from the world without it, built
    only as far as the engine stage reads it (`group_horizon`)."""
    w, we = sf.world(e, ot, need=int(ts[-1]), horizon=group_horizon(sf, e))
    return _edges_self(w, we, ts)


def _edges_self(sf, e, ts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = len(ts)
    lo, hi = np.full(n, np.nan), np.full(n, np.nan)
    if not n:
        return lo, hi
    if e.kind == "level":
        bt = np.asarray(sf.book.times, dtype=np.int64)
        kk = np.searchsorted(bt, ts, side="right") - 1
        starts = np.flatnonzero(np.r_[True, kk[1:] != kk[:-1]])
        ends = np.r_[starts[1:], n]
        for s0, s1 in zip(starts.tolist(), ends.tolist()):
            mem = sf.members_at(e, int(ts[s0]))
            if not mem:
                continue
            vals = [values_at(x.band, ts[s0:s1]) for x in mem]
            with np.errstate(all="ignore"):
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    lo[s0:s1] = np.nanmin(np.vstack([v[0] for v in vals]), axis=0)
                    hi[s0:s1] = np.nanmax(np.vstack([v[1] for v in vals]), axis=0)
        return lo, hi
    if e.kind == "gameplan":
        x = next(x for x in sf.containers if x.uid == e.zone_id)
        return values_at(x.band, ts)
    orr = sf.c.cs_nq.opening
    k = np.searchsorted(np.asarray(orr.timeline_t, dtype=np.int64), ts, side="right") - 1
    n_zone = int(e.zone_id[1:])
    for kv in np.unique(k).tolist():
        if kv < 0:
            continue
        d = orr.timeline[kv]
        if d is None or n_zone not in d.zone_ids:
            continue
        a, b = d.band(*orr.fib.fibs(e.part))
        sel = k == kv
        lo[sel], hi[sel] = min(a, b), max(a, b)
    return lo, hi


def _members_edges(levels: list, t: int) -> tuple[float, float, str, str] | None:
    """(lo, hi, source of the member setting lo, source of the member setting hi) at t."""
    best_lo = best_hi = None
    for x in levels:
        b = x.band.at(t)
        if b is None:
            continue
        if best_lo is None or b[0] < best_lo[0]:
            best_lo = (b[0], source_of(x))
        if best_hi is None or b[1] > best_hi[0]:
            best_hi = (b[1], source_of(x))
    if best_lo is None:
        return None
    return float(best_lo[0]), float(best_hi[0]), best_lo[1], best_hi[1]


def entry_zone(sf, e, t: int) -> dict[str, Any] | None:
    w, we = sf.world(e, t, horizon=group_horizon(sf, e))
    if we.kind == "level":
        got = _members_edges(w.members_at(we, t), t)
        if got is None:
            return None
        lo, hi, lo_src, hi_src = got
    else:
        ed = w.zone_edges(we, t)
        if ed is None:
            return None
        (lo, hi), lo_src, hi_src = ed, CHART_SOURCE, CHART_SOURCE
    return {"zone_lo": float(lo), "zone_hi": float(hi), "zone_lo_src": lo_src, "zone_hi_src": hi_src}


def zone_list(sf, e, t: int) -> list[dict[str, Any]]:
    """Every other active NQ zone at t (level zones of the book, gameplan containers, the golden zones of the drawing in
    force): {id, kind, lo, hi, lo_src, hi_src}, sorted by lo. Targets (F11.2), stop candidate 3 (F11.1), the first
    structure (F11.3) and the structures reached (§6.5) are all chosen from it."""
    w, we = sf.world(e, t, horizon=group_horizon(sf, e))
    out = []
    own = w.book.follow(we.zone_id, t) if we.kind == "level" else None
    for zid, mem in w.book.zones_at(t).items():
        if zid == own:
            continue
        got = _members_edges([x for x in (w.nq_levels[m] for m in mem) if not w._own(we, x)], t)
        if got is None:
            continue
        lo, hi, ls_, hs_ = got
        out.append({"id": zid, "kind": "level", "lo": lo, "hi": hi, "lo_src": ls_, "hi_src": hs_})
    for x in w.containers:
        if x.uid == we.zone_id or not x.active(t):
            continue
        b = x.band.at(t)
        if b is not None:
            out.append({"id": x.uid, "kind": "gameplan", "lo": float(b[0]), "hi": float(b[1]),
                        "lo_src": CHART_SOURCE, "hi_src": CHART_SOURCE})
    orr = w.c.cs_nq.opening
    d = orr.drawing_at(t) if orr is not None else None
    if d is not None and d.high > d.low:
        for part in gz.PARTS:
            zid = f"g{d.zone_id(part)}"
            if zid == we.zone_id:
                continue
            a, b = d.band(*orr.fib.fibs(part))
            out.append({"id": zid, "kind": "golden", "lo": float(min(a, b)), "hi": float(max(a, b)),
                        "lo_src": CHART_SOURCE, "hi_src": CHART_SOURCE})
    out.sort(key=lambda z: (z["lo"], z["hi"], z["id"]))
    return out


def swing(sf, e, t: int) -> tuple[float | None, float | None]:
    """(highest, lowest) trade from the zone's last re-arm before the touch (the touch for an unarmed formation touch)
    to t (F11.1 stop candidate 1)."""
    t0 = e.t_armed if e.t_armed is not None else e.t
    tape = sf.nq
    i, j = tape.idx_before(t0), tape.idx_at(t)
    if j <= i:
        return None, None
    p = tape.price[i:j]
    return float(p.max()), float(p.min())


def fib_prices(sf, e, t: int) -> dict[str, float | None]:
    out: dict[str, float | None] = {f"fib_{x:g}": None for x in FIB_TARGETS + (FIB_STOP,)}
    if e.kind != "golden":
        return out
    d = sf.c.cs_nq.opening.drawing_at(t)
    if d is None:
        return out
    for x in FIB_TARGETS + (FIB_STOP,):
        out[f"fib_{x:g}"] = float(d.level(x))
    return out


def band_track(sf, e, dts: int, near: float, centre: float, tol: float) -> dict[str, int | None]:
    """The entry zone's removal times in (dts, dts + TRACK_HORIZON_SEC] (F7): its near edge or its centre more than
    `tol` from its value at dts (`t_move_near`, `t_move_center`), its edges gone (`t_vanish`: no members, a golden
    zone redrawn), and its cross (`t_cross`, the structure label's rule; 'ended' counts as vanished)."""
    tape = sf.nq
    end = dts + TRACK_HORIZON_SEC * NS
    i, j = tape.idx_at(dts), tape.idx_at(end)
    ts = tape.ts[i:j]
    out: dict[str, int | None] = {"t_move_near": None, "t_move_center": None, "t_vanish": None, "t_cross": None}
    names = ("t_move_near", "t_move_center", "t_vanish")

    def first_times(ts_part, lo, hi) -> None:
        """Fill each removal time still missing with its first instant in this part (parts come in time order)."""
        nr = lo if e.direction < 0 else hi
        ctr = (lo + hi) / 2
        with np.errstate(invalid="ignore"):
            gone = np.isnan(lo)
            conds = {"t_move_near": gone | (np.abs(nr - near) > tol + 1e-9),
                     "t_move_center": gone | (np.abs(ctr - centre) > tol + 1e-9), "t_vanish": gone}
        for name in names:
            if out[name] is None:
                k = np.flatnonzero(conds[name])
                out[name] = int(ts_part[k[0]]) if len(k) else None

    # owner 2026-11-12 (Q-step13b (e)): the part of the window before the touch group's own retested extreme comes
    # from this session; the world without it is built only if a removal time is still missing after that part (each
    # time is a first instant, so the later part can't change one already found)
    ot = own_time(sf, e)
    k0 = len(ts) if ot is None else int(np.searchsorted(ts, ot, side="left"))
    first_times(ts[:k0], *_edges_self(sf, e, ts[:k0]))
    if k0 < len(ts) and any(out[n] is None for n in names):
        first_times(ts[k0:], *_edges_after(sf, e, ot, ts[k0:]))
    x = sf.cross(e, end)
    if x is not None and x[0] > dts:
        if x[1] == "broke":
            out["t_cross"] = int(x[0])
        elif out["t_vanish"] is None or x[0] < out["t_vanish"]:
            out["t_vanish"] = int(x[0])
    return out


def snapshot(sf, e, dts: int, tol: float) -> dict[str, Any] | None:
    """The decision point's frozen view (None if the zone has no edges at dts)."""
    ez = entry_zone(sf, e, dts)
    if ez is None:
        return None
    lo, hi = ez["zone_lo"], ez["zone_hi"]
    near = lo if e.direction < 0 else hi
    centre = (lo + hi) / 2
    hi_s, lo_s = swing(sf, e, dts)
    zl = zone_list(sf, e, dts)
    out: dict[str, Any] = {**ez, "zone_center": centre, "near_edge": near, "price_at_decision": sf.nq.last_price_at(dts),
                           "swing_high": hi_s, "swing_low": lo_s, **fib_prices(sf, e, dts),
                           "z_id": [z["id"] for z in zl], "z_kind": [z["kind"] for z in zl],
                           "z_lo": [z["lo"] for z in zl], "z_hi": [z["hi"] for z in zl],
                           "z_lo_src": [z["lo_src"] for z in zl], "z_hi_src": [z["hi_src"] for z in zl]}
    out.update(band_track(sf, e, dts, near, centre, tol))
    return out
