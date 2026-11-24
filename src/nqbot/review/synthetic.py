"""Hand-built synthetic review days and cards (SPEC §13 Q-dryrun13 (12): review-card regression tests are hand-built
synthetic reproductions; tests never read real data).

The pieces (`tape`, `step_level`, `level_event`, `boards`, `decision`, `pair`) build a `cards.DayData` by hand, so a
test can reproduce a card the owner flagged (a detector got it wrong: rule 11) with exact prices and times, and check
what the card shows at its decision time and after the answer. `synthetic_round()` uses them for a small seeded round
that `nqbot ui --synthetic` shows behind the SYNTHETIC DATA label (its answers stay in memory).
"""

from __future__ import annotations

import random
from datetime import date, time
from typing import Any

import numpy as np

from nqbot.features import session_tape as st
from nqbot.features.boards import MapBoards
from nqbot.review import cards as cd

DAY = date(2026, 1, 14)          # a Wednesday in development month 4 of the committed ledger
NS = cd.NS
TICK = 0.25


def et(day: date, hh: int, mm: int, ss: int = 0) -> int:
    return st.et_ns(day, time(hh, mm, ss))


def tape(day: date, t0: int, t1: int, start_px: float, rng: random.Random, *, step_sec: float = 2.0,
         drift: list[tuple[int, float]] | None = None, vol: float = 1.0, symbol: str = "NQH6",
         anchors: list[tuple[int, float]] | None = None) -> st.Tape:
    """A seeded random walk of trades every `step_sec` from t0 to t1 on the tick grid; `drift` = [(from t, points per
    minute)] pieces; `anchors` = [(t, price)] the walk is pulled towards (so a test can place price at a level)."""
    ts, px = [], []
    p = start_px
    t = t0
    drift = sorted(drift or [])
    anchors = sorted(anchors or [])
    while t < t1:
        d = 0.0
        for a, v in drift:
            if t >= a:
                d = v
        target = next(((at, ap) for at, ap in anchors if at >= t), None)
        pull = 0.0
        if target is not None:
            left = max(1.0, (target[0] - t) / NS)
            pull = (target[1] - p) / left * step_sec
        p += d * step_sec / 60 + pull + rng.gauss(0, vol)
        p = round(p / TICK) * TICK
        ts.append(t)
        px.append(p)
        t += int(step_sec * NS)
    n = len(ts)
    side = np.array([b"B" if rng.random() < 0.5 else b"A" for _ in range(n)])
    return st.Tape.from_arrays(day, np.array(ts, dtype=np.int64), np.array(px), np.ones(n), side, symbol=symbol)


def step_level(uid: str, sub_type: str, rtype: str, kind: str, times: list[int], lo: list[float], hi: list[float],
               spans: list[tuple[int, int]], *, instrument: str = "NQ", chain: str | None = None,
               strike: float | None = None) -> dict[str, Any]:
    return {"uid": uid, "level_id": uid, "sub_type": sub_type, "key": sub_type, "rtype": rtype, "instrument": instrument,
            "kind": kind, "chain": chain, "strike": strike, "band_kind": "step", "times": list(times), "lo": list(lo),
            "hi": list(hi), "end_ns": None, "spans": [list(s) for s in spans], "freezes": []}


def level_event(uid: str, t: int, name: str) -> dict[str, Any]:
    return {"uid": uid, "level_id": uid, "sub_type": "", "key": "", "group": "", "t": int(t), "name": name}


def boards(name: str, day: date, t0: int, t1: int, strikes: np.ndarray, value_fn, spot_fn, r_fn,
           every_sec: int = 60) -> MapBoards:
    """A map's applied boards every `every_sec` from t0 to t1: `value_fn(t, strikes)` -> values, `spot_fn(t)`,
    `r_fn(t)` (the R in force)."""
    ts = np.arange(t0, t1, every_sec * NS, dtype=np.int64)
    sk, sv, off = [], [], [0]
    for t in ts:
        v = np.asarray(value_fn(int(t), strikes), dtype=np.float64)
        sk.append(np.asarray(strikes, dtype=np.float64))
        sv.append(v)
        off.append(off[-1] + len(strikes))
    spot = np.array([spot_fn(int(t)) for t in ts])
    r = np.array([r_fn(int(t)) for t in ts])
    return MapBoards(name, day, ts, ts, ts, spot, r, r, np.ones(len(ts), dtype=bool), np.array(off, dtype=np.int64),
                     np.concatenate(sk) if sk else np.zeros(0), np.concatenate(sv) if sv else np.zeros(0))


def decision(decision_id: str, event_id: str, t: int, style: str, direction: int, role: str, zone: tuple[float, float],
             price: float, *, zone_id: str = "z1", book: list[tuple[str, float, float]] | None = None,
             tradeable: bool = True, touch_group_id: str | None = None, event_type: str = "level") -> dict[str, Any]:
    lo, hi = zone
    book = book or []
    return {"decision_id": decision_id, "event_id": event_id, "touch_group_id": touch_group_id or event_id,
            "style": style, "firing_n": 1, "decision_ts": int(t), "touch_ts": int(t), "touch_price": price,
            "direction": direction, "role": role, "event_type": event_type, "golden_zone_part": None,
            "zone_id": zone_id, "tradeable": tradeable, "zone_lo": lo, "zone_hi": hi, "zone_center": (lo + hi) / 2,
            "near_edge": hi if direction > 0 else lo, "price_at_decision": price, "trigger_kind": None,
            "z_id": [b[0] for b in book], "z_lo": [b[1] for b in book], "z_hi": [b[2] for b in book]}


def event(event_id: str, t: int, direction: int, role: str, label: str, t_resolved: int) -> dict[str, Any]:
    return {"event_id": event_id, "ts": int(t), "direction": direction, "role": role, "label": label,
            "t_resolved": int(t_resolved), "how": label, "tradeable": True, "event_type": "level"}


def pair(pair_id: str, decision_id: str, style: str, order_type: str, entry: float, stop: float, target: float,
         out: str, r: float, t_exit: int, *, t_fill: int) -> tuple[dict[str, Any], dict[str, Any]]:
    fill_id = pair_id + "-f"
    p = {"pair_id": pair_id, "fill_id": fill_id, "decision_id": decision_id, "style": style, "order_type": order_type,
         "stop": stop, "target": target, "stop_pts": abs(entry - stop), "target_pts": abs(target - entry),
         "net_rr": abs(target - entry) / max(abs(entry - stop), TICK), "ref": entry, "target_kind": "zone_near",
         "target_zone_kind": "level", "out_none": out, "r_none": r, "t_exit_none": float(t_exit),
         "timeout_reason": "flat_by" if out == "timeout" else None, "fill_status": "filled"}
    f = {"fill_id": fill_id, "limit": entry, "fill_px": entry, "t_fill": int(t_fill), "size_nq": 1, "micros": 3}
    return p, f


# ── the --synthetic round ──────────────────────────────────────────────────────────────────────────────────────────

def _node_values(king_switch_t: int):
    def fn(t: int, strikes: np.ndarray) -> np.ndarray:
        king = 612.0 if t < king_switch_t else 615.0
        wave = np.cos((strikes - 600) * 1.7) * 0.35
        base = np.where(strikes % 5 == 0, 1.0, 0.45) * (1 + wave)
        sign = np.where((strikes.astype(int) % 3) == 1, -1.0, 1.0)
        v = sign * base * 9e6
        v = v + np.where(strikes == king, 6.2e7, 0.0)
        return v
    return fn


def synthetic_day(seed: int = 7, day: date = DAY) -> tuple[cd.DayData, list[dict[str, Any]]]:
    """A seeded synthetic session and its card specs [{id, decision_id, pair_id, stratum}]."""
    rng = random.Random(seed)
    open_ns, close_ns = st.rth_open_ns(day), st.rth_close_ns(day)
    t0 = st.cme_start_ns(day)          # the session's open, 18:00 ET the evening before
    pdh, pdl, onh = 21512.0, 21288.0, 21460.0
    nq = tape(day, t0, close_ns, 21390.0, rng, vol=1.6, anchors=[
        (t0 + 3 * 3600 * NS, 21452.0), (et(day, 2, 0), 21338.0), (et(day, 6, 0), 21405.0), (et(day, 9, 30), 21430.0), (et(day, 10, 12), onh + 3), (et(day, 10, 40), 21402.0), (et(day, 11, 25), 21356.0),
        (et(day, 12, 10), 21366.0), (et(day, 13, 20), 21318.0), (et(day, 14, 5), 21294.0), (et(day, 14, 50), 21352.0),
        (et(day, 15, 30), 21384.0), (et(day, 16, 0), 21376.0)])
    es = tape(day, t0, close_ns, 5822.0, random.Random(seed + 1), vol=0.4, symbol="ESH6", anchors=[
        (et(day, 6, 0), 5826.0), (et(day, 10, 12), 5836.0), (et(day, 11, 25), 5812.0), (et(day, 14, 5), 5797.0), (et(day, 16, 0), 5815.0)])
    r_q = 21370.0 / 610.0

    def flat(uid, sub, rtype, kind, px, hw, start=t0):
        return step_level(uid, sub, rtype, kind, [start], [px - hw], [px + hw], [(start, close_ns)])

    vw_t = list(range(t0, close_ns, 60 * NS))
    vw_c = [float(np.mean(nq.price[: max(1, int(np.searchsorted(nq.ts, t)))])) for t in vw_t]
    levels = [
        flat("pdh", "pdh", "session_hl", "sweep", pdh, 8.5), flat("pdl", "pdl", "session_hl", "sweep", pdl, 8.5),
        flat("on_high", "on_high", "session_hl", "sweep", onh, 9.0),
        flat("poc_daily", "poc_daily", "volume_profile", "chart", 21366.0, 12.0, et(day, 9, 30)),
        flat("lvn_daily", "lvn_daily", "volume_profile", "chart", 21318.0, 12.0),
        step_level("vwap18", "vwap18", "vwap", "chart", vw_t, [c - 12 for c in vw_c], [c + 12 for c in vw_c],
                   [(t0, close_ns)]),
    ]
    for k, a, b in ((610.0, open_ns, close_ns), (612.0, open_ns, close_ns), (605.0, et(day, 11, 0), close_ns),
                    (615.0, et(day, 12, 0), close_ns)):
        levels.append(step_level(f"qqq_node-{k:g}", "qqq_node", "gex_node", "node", [a], [k * r_q - 8], [k * r_q + 8],
                                 [(a, b)], chain="QQQ", strike=k))
    level_events = [level_event("pdh", et(day, 8, 0), "role_ceiling"), level_event("pdl", et(day, 8, 0), "role_floor"),
                    level_event("on_high", et(day, 8, 0), "role_ceiling"),
                    level_event("poc_daily", et(day, 9, 31), "role_floor"),
                    level_event("poc_daily", et(day, 11, 20), "cross"), level_event("poc_daily", et(day, 11, 25), "flip"),
                    level_event("lvn_daily", et(day, 8, 0), "role_floor"),
                    level_event("vwap18", et(day, 8, 0), "role_floor"), level_event("vwap18", et(day, 10, 45), "cross"),
                    level_event("vwap18", et(day, 12, 30), "reclaim")]
    for k in (610.0, 612.0, 605.0, 615.0):
        level_events.append(level_event(f"qqq_node-{k:g}", open_ns + 60 * NS, "role_floor" if k * r_q < 21420 else "role_ceiling"))
    specs = [  # (id, time, style, dir, role, zone, out, r, minutes to exit, label, stratum)
        ("c01", et(day, 10, 12, 40), "c", -1, "ceiling", (onh - 9, onh + 9), "target", 1.62, 24, "held", "spread"),
        ("c02", et(day, 11, 25, 10), "d", 1, "floor", (21354.0, 21378.0), "stop", -1.0, 9, "broke", "lost"),
        ("c03", et(day, 12, 10, 5), "d", -1, "ceiling", (21354.0, 21378.0), "timeout", 0.31, 60, "timeout", "spread"),
        ("c04", et(day, 14, 5, 20), "c", 1, "floor", (pdl - 8.5, 21306.0), "target", 2.05, 31, "held", "spread"),
        ("c05", et(day, 14, 51, 0), "d", 1, "floor", (21340.0, 21364.0), "stop", -1.0, 6, "broke", "never take"),
    ]
    events, decisions, pairs, fills, paths, cardspecs = [], [], {}, {}, {}, []
    book = [("z_on", onh - 9, onh + 9), ("z_poc", 21354.0, 21378.0), ("z_pdl", pdl - 8.5, 21306.0),
            ("z_pdh", pdh - 8.5, pdh + 8.5)]
    for i, (cid, t, style, d, role, zone, out, r, mins, label, stratum) in enumerate(specs):
        eid, did, pid = f"e{i}", f"e{i}-{style}1", f"e{i}-{style}1-p"
        price = float(nq.last_price_at(t))
        zid = next((b[0] for b in book if b[1] == zone[0]), f"z{i}")
        events.append(event(eid, t, d, role, label, t + 25 * 60 * NS))
        decisions.append(decision(did, eid, t, style, d, role, zone, price, zone_id=zid,
                                  book=[b for b in book if b[0] != zid]))
        entry = zone[1] if d > 0 else zone[0]
        stop = (zone[0] - 5) if d > 0 else (zone[1] + 5)
        target = entry + d * abs(entry - stop) * (2.0 if out == "target" else 1.6)
        p, f = pair(pid, did, style, "stop_limit" if style == "c" else "limit", entry, stop, target, out, r,
                    t + mins * 60 * NS, t_fill=t + 20 * NS)
        pairs[pid], fills[f["fill_id"]] = p, f
        paths[pid] = {"pair_id": pid, "mae": abs(entry - stop) * (1.0 if out == "stop" else 0.4),
                      "mfe": abs(target - entry) * (1.0 if out == "target" else 0.5)}
        cardspecs.append({"id": cid, "decision_id": did, "pair_id": pid, "stratum": stratum})
    rows = {d["decision_id"]: {"stack_count": 2, "n_poc_daily": 1, "n_vwap18": 1, "zone_width": d["zone_hi"] - d["zone_lo"],
                               "distance_to_near_edge": 3.0, "trinity_count": 1, "es_at_level": True,
                               "king_present": False, "gatekeeper_present": True, "largest_pct_of_king": 0.41,
                               "n_node_pos": 1, "n_node_neg": 0, "any_rug": False, "any_reverse_rug": False,
                               "any_pika_member": True, "qqq_king_strike": 612.0, "qqq_regime_net": 0.42,
                               "zone_tests_so_far": 1, "holds_so_far_mean": 1.0, "primary_flip_count": 0,
                               "primary_provisional_role": False, "dt_minutes_since_open": (d["decision_ts"] - open_ns) / 60e9,
                               "dt_range_vs_em": 0.55, "dt_vwap_crosses": 3, "dt_er_so_far": 0.21,
                               "with_day_direction": True, "approach_move_15": -18.5,
                               "founding_level_ids": ["on_high"], "primary_member_type": "on_high"} for d in decisions}
    sw = et(day, 12, 40)
    qqq = boards("QQQ", day, open_ns, close_ns, np.arange(598.0, 627.0), _node_values(sw),
                 lambda t: float(nq.last_price_at(t) or 21400) / r_q, lambda t: r_q)
    ndxp_k = 41.2
    ndxp = boards("NDXP", day, open_ns, close_ns, np.arange(598.0, 627.0) * ndxp_k // 10 * 10,
                  lambda t, s: _node_values(sw)(t, np.round(s / ndxp_k)) * 0.01,
                  lambda t: float(nq.last_price_at(t) or 21400) / (r_q / ndxp_k), lambda t: r_q / ndxp_k)
    r_s = 5820.0 / 581.0
    spy = boards("SPY", day, open_ns, close_ns, np.arange(570.0, 595.0), lambda t, s: _node_values(sw)(t, s + 32) * 1.3,
                 lambda t: float(es.last_price_at(t) or 5820) / r_s, lambda t: r_s)
    spx = boards("SPX", day, open_ns, close_ns, np.arange(5760.0, 5890.0, 5.0),
                 lambda t, s: _node_values(sw)(t, np.round(s / 10 + 32)) * 0.4,
                 lambda t: float(es.last_price_at(t) or 5820) / 1.0007, lambda t: 1.0007)
    feats = {"king_switch_min_sec": 60, "node_switch_incumbent_max_share": 0.25,
             "mapping": {"ratio_gap_reset_sec": 300}}
    dd = cd.DayData(day, open_ns, close_ns, {"NQ": nq, "ES": es}, levels, level_events, events, decisions, rows, pairs,
                    fills, paths, {"QQQ": qqq, "NDXP": ndxp, "SPY": spy, "SPX": spx}, {"NQ": "NQH6", "ES": "ESH6"}, feats)
    return dd, cardspecs


PROFILE_UPDATE_SEC = 60   # the synthetic day's profile instants (the committed `profile_update_sec`)


def synthetic_extras(dd: cd.DayData, card: dict[str, Any]) -> dict[str, Any]:
    """A synthetic card's sidecar (`cards.card_extras`): the developing daily VAH / VAL from the synthetic tape, and the
    daily value readings from that profile at the decision (F3.3, `profile.va_features`; the weekly ones left null)."""
    from nqbot.features import profile as pf

    tape = dd.tapes["NQ"]
    prof = cd.daily_profile(tape, dd.day, PROFILE_UPDATE_SEC, dd.close_ns)
    T = int(card["t"]["decision_ns"])
    va = pf.va_features(prof.at(T), float(tape.last_price_at(T)), int(card["setup"]["direction"]))
    return cd.card_extras(card, tape, prof, {f"daily_{k}": v for k, v in va.items()})


def synthetic_round(seed: int = 7) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    dd, specs = synthetic_day(seed)
    from nqbot.review import sampler as smp

    why = {"spread": smp.WHY_SPREAD, "lost": smp.WHY_LOST, "never take": smp.WHY_NEVER}
    cards = [cd.card_payload(dd, s["id"], s["decision_id"], s["pair_id"], s["stratum"], why[s["stratum"]]) for s in specs]
    cards = [cd.merge_extras(c, synthetic_extras(dd, c)) for c in cards]
    meta = {"id": "synthetic", "build_key": "synthetic", "labels_key": "synthetic", "synthetic": True,
            "created": None, "months": [4],
            "cards": [{"id": c["id"], "day": c["day"], "decision_id": c["decision_id"], "pair_id": c["pair_id"],
                       "stratum": c["stratum"]} for c in cards]}
    return meta, cards

