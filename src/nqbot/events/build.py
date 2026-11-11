"""The engine stage of step 13b (inside the feature-build key; pass 2 of `feature_build` after its end-state check;
SPEC §12 step 13, §13 Q-step13b).

Per session, beside touches/ and outcomes/ (which stay as they are):
- `events/<D>.parquet`: one row per event: identity, the setup tags, the structure label (`hold_reaction_pts`) and the
  pair cut (`pair_end_hold_pts`), the level-type groups of its members at the touch, `post_cross_retest`,
  `n_firings_c` and `damage_drop`. Its features are the touch row (touches/, joined by `event_id`);
- `decisions/<D>.parquet`: one row per decision point (event x style x firing; style (d) at the touch, style (c) at
  each completed audit or sweep reclaim, F6.4), only where `pair_allowed` (Q-step11h (1)) and the event isn't dropped
  for damage: the snapshot (`snapshot.py`) and the pair-only columns (F6.5); float64 (prices);
- `decision_rows/<D>.parquet`: the pair scorer's inputs of each style-(c) decision point, `row(event, decision_ts)`
  (style (d)'s are the touch rows);
- `level_events/<D>.parquet`: every NQ level's and gameplan zone's state transitions (cross, reclaim, flip, ...) with
  its level-type group, for the flip checkpoint (Q-flipretest).
**Damage (SPEC §3.2 rule 3, as read here):** an event is dropped (`damage_drop`) when its structure-label window, from
the touch to the window end, comes within `databento_damage_window_pad_min` of a damaged RTH gap, or the whole RTH is
dropped; a dropped event builds no decision point (its pairs would carry the damage).
"""

from __future__ import annotations

import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from nqbot.events import snapshot as snap
from nqbot.events import triggers as trig
from nqbot.events import zone_compare
from nqbot.events.common import GAMEPLAN, GROUPS, decision_id, group_of_level, write_table
from nqbot.features import structure_label as slab
from nqbot.features.session_tape import MIN_NS


def _ns(t: datetime) -> int:
    return int(t.timestamp()) * 10**9


def damage_drop(sf, e, damage_day, pad_min: float) -> bool:
    if damage_day is None:
        return False
    if damage_day.rth_dropped:
        return True
    t_end = slab.window_end(e.t, float(sf.f["hold_window_min"]), sf.flat_ns, sf.end_ns)
    pad = int(pad_min * MIN_NS)
    for g0, g1 in damage_day.rth_gap_minutes:
        if _ns(g0) - pad <= t_end and _ns(g1) + pad >= e.t:
            return True
    return False


def _groups(sf, e) -> dict[str, bool]:
    out = {f"has_{g}": False for g in GROUPS}
    out[f"has_{GAMEPLAN}"] = e.kind == "gameplan"
    if e.kind == "level":
        for m in e.members:
            g = group_of_level(sf.nq_levels[m])
            if g is not None:
                out[f"has_{g}"] = True
    return out


def session_outputs(sf, rows: list[dict], damage_day, settings: dict) -> dict[str, Any]:
    t0 = time.monotonic()
    f = sf.f
    tol = float(f["entry_reprice_tol"])
    fib_levels = f["golden_fib_levels"]
    pad = float(settings["databento_damage_window_pad_min"])
    by_row = {r["event_id"]: r for r in rows}
    events, decisions, drows = [], [], []
    firings = Counter()
    t_c = 0.0
    for e in sf.events:
        lab, pend = sf.labels[e.event_id], sf.pair_end[e.event_id]
        r = by_row[e.event_id]
        drop = damage_drop(sf, e, damage_day, pad)
        row = {"event_id": e.event_id, "zone_id": e.zone_id, "touch_group_id": r["touch_group_id"], "day": r["day"],
               "ts": int(e.t), "touch_idx": int(e.idx), "t_armed": e.t_armed, "direction": e.direction,
               "role": e.role, "event_type": e.kind, "golden_zone_part": e.part,
               "or_fib_flips": r.get("or_fib_flips"), "or_fib_drawing": r.get("or_fib_drawing"),
               "tradeable": r["tradeable"], "label": lab.label, "t_resolved": int(lab.t_resolved), "how": lab.how,
               "t_pair_end": int(pend.t_resolved), "pair_end_label": pend.label,
               "post_cross_retest": bool(r.get("primary_provisional_role")),
               "primary_member_type": r.get("primary_member_type"), "primary_flip_count": r.get("primary_flip_count"),
               "has_retested_extreme": bool(r.get("n_retested_extreme")),
               "has_qqq_ndxp_node": bool(r.get("n_qqq_node") or r.get("n_ndxp_node")),
               **_groups(sf, e), "damage_drop": drop}
        nc = 0
        if not drop:
            tpe = int(pend.t_resolved)
            if slab.pair_allowed(e.t, tpe):
                d = _decision(sf, e, "d", 1, int(e.t), tol, r, tpe)
                if d is not None:
                    decisions.append(d)
            c0 = time.monotonic()
            for fi in trig.style_c(sf, e, tpe, fib_levels):
                d = _decision(sf, e, "c", fi.n, fi.decision_ts, tol, None, tpe)
                if d is None:
                    continue
                d.update(fi.pair_columns())
                decisions.append(d)
                fr = sf.row(e, fi.decision_ts)
                drows.append({"decision_id": d["decision_id"], "style": "c", "firing_n": fi.n,
                              "decision_ts": fi.decision_ts, **fr})
                nc += 1
            t_c += time.monotonic() - c0
        firings[nc] += 1
        row["n_firings_c"] = nc
        events.append(row)
    levels = []
    for lv in list(sf.nq_levels.values()) + list(sf.containers):
        g = group_of_level(lv) or (GAMEPLAN if lv.key == "gameplan" else None)
        for t, name in lv.state.events:
            levels.append({"uid": lv.uid, "level_id": lv.level_id, "sub_type": lv.sub_type, "key": lv.key,
                           "group": g, "t": int(t), "name": name})
    zc = zone_compare.compare(sf)
    info = {"decisions": len(decisions), "decisions_d": sum(1 for d in decisions if d["style"] == "d"),
            "decisions_c": sum(1 for d in decisions if d["style"] == "c"),
            "firings_c": {str(k): v for k, v in sorted(firings.items())}, "firings_c_max": max(firings, default=0),
            "damage_dropped": sum(1 for x in events if x["damage_drop"]), "level_events": len(levels),
            "style_c_sec": round(t_c, 1), "events_stage_sec": round(time.monotonic() - t0, 1), **zc}
    return {"events": events, "decisions": decisions, "decision_rows": drows, "level_events": levels, "info": info}


def _decision(sf, e, style: str, n: int, dts: int, tol: float, touch_row: dict | None, t_pair_end: int) -> dict | None:
    s = snap.snapshot(sf, e, dts, tol)
    if s is None:
        return None
    members = sf.members_at(e, dts)
    tradeable = touch_row["tradeable"] if touch_row is not None else sf._tradeable(e, dts, members)
    out = {"decision_id": decision_id(e.event_id, style, n), "event_id": e.event_id, "touch_group_id":
           f"{sf.day}-{e.group}", "style": style, "firing_n": n, "decision_ts": int(dts), "touch_ts": int(e.t),
           "touch_price": float(e.price), "direction": e.direction, "role": e.role, "event_type": e.kind,
           "golden_zone_part": e.part, "zone_id": e.zone_id, "t_pair_end": int(t_pair_end), "tradeable": tradeable,
           "member_chains": sorted({m.chain for m in members if m.chain}), **s}
    for k in ("trigger_kind", "trigger_bar_start", "trigger_bar_open", "trigger_bar_high", "trigger_bar_low",
              "trigger_bar_close", "audit_depth", "audit_depth_frac", "audit_seconds_inside", "swept", "sweep_depth",
              "sweep_seconds_beyond", "fib_level_reached"):
        out.setdefault(k, None)
    return out


def session_meta(sf) -> dict[str, Any]:
    """What the labeler needs of the session clock (in the build key): flat-by, `entry_end`, the blackout spans (the
    news flatten = a span's start, SPEC §9), the RTH open."""
    from nqbot.features.session_tape import rth_close_ns

    return {"day": sf.day.isoformat(), "flat_ns": int(sf.flat_ns), "entry_end_ns": int(sf.entry_end_ns),
            "close_ns": int(rth_close_ns(sf.day, sf.c.cal)),
            "open_ns": int(sf.open_ns), "end_ns": int(sf.end_ns), "blackouts": [[int(a), int(b)] for a, b in sf.blackouts],
            "nq_symbol": sf.nq.symbol}


def write_session(base: Path, sf, rows: list[dict], damage_day, settings: dict, key: str) -> dict[str, Any]:
    out = session_outputs(sf, rows, damage_day, settings)
    iso = sf.day.isoformat()
    meta = {"day": iso, "key": key, **session_meta(sf)}
    write_table(base / "events" / f"{iso}.parquet", out["events"], meta)
    write_table(base / "decisions" / f"{iso}.parquet", out["decisions"], meta)
    from nqbot.checks.feature_build import write_rows   # float32 features, like touches/
    write_rows(base / "decision_rows" / f"{iso}.parquet", out["decision_rows"], {"day": iso, "key": key})
    write_table(base / "level_events" / f"{iso}.parquet", out["level_events"], {"day": iso, "key": key})
    return out["info"]
