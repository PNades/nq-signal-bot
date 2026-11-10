"""Level timelines (step 13a; owner 2026-11-09, SPEC §13 Q-dryrun13 (3), §12 step 13, feature_spec F5.14).

One row per registry level of the session (NQ: chart levels and the QQQ / NDXP nodes; ES: the SPY / SPX nodes; the
ES chart levels are not needed, F5.14: their widths are the NQ value × ES ÷ NQ), with the band it had over the
session, its active spans (F5.3), its frozen intervals (a node while not relevant, a profile level while away) and the
session's unit values (EM, ES ÷ NQ). The reaction-distance report (`checks/step13a.py`) reads these files with the
trades: they hold where every level was, never an outcome.

The band is stored as a step function (`times`, `lo`, `hi`: the band from each instant until the next; NaN = no band):
- a `StepBand` exactly (its own change instants, and `end_ns`);
- a `TradeBand` (the VWAP lines and bands, which move on every trade) **sampled once a second**: the band after the
  last trade of each second, from that trade's time. A trade inside a second is judged against the band at the end of
  the previous second, so the line is off by at most one second of VWAP drift (a few hundredths of a point in RTH);
  storing the band of every trade would be ≈ 40 MB a session.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from nqbot.features.bands import StepBand, TradeBand

SECOND_NS = 10**9
SCHEMA = 1   # the file layout; the reaction report checks it


def band_steps(band) -> tuple[np.ndarray, np.ndarray, np.ndarray, int | None]:
    """(times, lo, hi, end_ns) of `band` as a step function (see the module note)."""
    if isinstance(band, StepBand):
        return (np.asarray(band.times, dtype=np.int64), np.asarray(band.lo, float), np.asarray(band.hi, float),
                None if band.end_ns is None else int(band.end_ns))
    if isinstance(band, TradeBand):
        ts = np.asarray(band.tape.ts, dtype=np.int64)
        c = np.asarray(band.center, float)
        if not len(ts):
            return np.zeros(0, np.int64), np.zeros(0), np.zeros(0), band.end_ns
        sec = ts // SECOND_NS
        last = np.flatnonzero(np.r_[sec[1:] != sec[:-1], True])   # the last trade of each second
        ok = ts[last] >= int(band.start_ns)
        if band.end_ns is not None:
            ok &= ts[last] < int(band.end_ns)
        last = last[ok]
        v = c[last]
        # keep only the samples where the band changes (or starts / stops existing)
        keep = np.r_[True, (v[1:] != v[:-1]) & ~(np.isnan(v[1:]) & np.isnan(v[:-1]))] if len(v) else np.zeros(0, bool)
        last, v = last[keep], v[keep]
        hw = float(band.half_width)
        return ts[last], v - hw, v + hw, None if band.end_ns is None else int(band.end_ns)
    raise TypeError(f"unknown band type {type(band).__name__}")


def level_rows(levels: list, instrument_filter=None) -> list[dict[str, Any]]:
    """One row per `registry.RegLevel` (in the given order)."""
    out = []
    for x in levels:
        if instrument_filter is not None and not instrument_filter(x):
            continue
        times, lo, hi, end = band_steps(x.band)
        out.append({
            "uid": x.uid, "level_id": x.level_id, "sub_type": x.sub_type, "key": x.key, "rtype": x.rtype,
            "instrument": x.instrument, "kind": x.kind, "chain": x.chain, "strike": x.strike,
            "band_kind": "trade" if isinstance(x.band, TradeBand) else "step",
            "times": times.tolist(), "lo": lo.tolist(), "hi": hi.tolist(), "end_ns": end,
            "spans": [[int(a), int(z)] for a, z in x.spans],
            "freezes": [[int(a), int(z)] for a, z in x.state.freezes],
        })
    return out


def session_rows(sf) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The session's timeline rows (NQ levels, ES node levels) and its unit values."""
    from nqbot.features.registry import GEX_NODE

    rows = level_rows(list(sf.nq_levels.values()))
    rows += level_rows(list(sf.es_levels.values()), lambda x: x.rtype == GEX_NODE)
    c = sf.c
    meta = {
        "schema": SCHEMA, "open_ns": int(sf.open_ns), "end_ns": int(sf.end_ns),
        "start_ns": int(c.cs_nq.start_ns), "close_ns": int(c.cs_nq.close_ns),
        "em_nq": c.cs_nq.em.points, "em_es": c.cs_es.em.points,
        "es_ratio_prev": float(c.cs_es.inp.scale_prev), "es_ratio_today": float(c.cs_es.inp.scale_today),
        "es_switch_ns": int(sf._es_switch()),
        "nq_symbol": sf.nq.symbol, "es_symbol": sf.es.symbol,
        "chart_level_half_width": dict(sf.f["chart_level_half_width"]), "node_band": dict(sf.f["node_band"]),
    }
    return rows, meta
