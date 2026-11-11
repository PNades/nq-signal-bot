"""Stop and target candidates of a decision point (SPEC §6.2, §6.3; feature_spec F11.1, F11.2; owner 2026-11-10, SPEC §13
Q-step13b (4), Q-flexfarm (1)).

Stops (F11.1), all from the decision snapshot (bands as of `decision_ts`), each rounded away from entry (§5.1):
1. the swing extreme from the zone's last re-arm to `decision_ts` + `stop_buffer`;
2. the entry zone's far edge + max(`stop_buffer`, the floor of the member setting that edge);
3. the far edge of the next level zone beyond the entry zone in the stop direction + max(`stop_buffer`, its floor);
6. golden zones: the fib 1.0 level + `stop_buffer`.
(4, the gameplan's stop: no gameplans in months 1-4; 5, the absorption range: step 19.) A candidate not beyond the
reference price is dropped; candidates within `stop_merge_ticks` of a kept one (in the order 1, 2, 3, 6) merge into it.

Targets (F11.2, the counting rule approved 2026-11-10): every other zone in the trade direction gives its near edge and
its centre (rounded toward entry, at least one tick beyond the reference price); a zone counts toward
`max_target_candidates` if one of its prices has net RR >= `pair_build_min_rr` (only those prices are built), nearest
first; golden events add the fib 0 / -0.272 / -0.618 levels with net RR >= `pair_build_min_rr`, and every stop gets
the fixed baseline at `baseline_rr` x its distance (any RR), both outside the count. **Low-RR extension (Q-flexfarm
(1)):** on top of that unchanged set, up to `pair_ext_max_zones` of the nearest zones not in it with prices in
[`pair_ext_min_rr`, `pair_build_min_rr`), flagged `rr_ext`. Equal prices are built once (the nearer zone's).
"""

from __future__ import annotations

from typing import Any

from nqbot.events.common import round_stop, round_target
from nqbot.features.session_tape import TICK

EPS = 1e-9


def _floor(p, src: str) -> float:
    return max(p.stop_buffer, float(p.stop_floor.get(src, 0.0)))


def stops(dp: dict, ref: float, direction: int, p) -> list[tuple[float, str]]:
    d = direction
    raw: list[tuple[str, float]] = []
    if d > 0:
        if dp.get("swing_low") is not None:
            raw.append(("1", dp["swing_low"] - p.stop_buffer))
        raw.append(("2", dp["zone_lo"] - _floor(p, dp["zone_lo_src"])))
    else:
        if dp.get("swing_high") is not None:
            raw.append(("1", dp["swing_high"] + p.stop_buffer))
        raw.append(("2", dp["zone_hi"] + _floor(p, dp["zone_hi_src"])))
    nz = next_zone(dp, d)
    if nz is not None:
        raw.append(("3", nz["lo"] - _floor(p, nz["lo_src"]) if d > 0 else nz["hi"] + _floor(p, nz["hi_src"])))
    if dp.get("event_type") == "golden" and dp.get("fib_1") is not None:
        raw.append(("6", dp["fib_1"] - p.stop_buffer if d > 0 else dp["fib_1"] + p.stop_buffer))
    kept: list[list] = []
    for typ, px in raw:
        s = round_stop(px, d)
        if (ref - s) * d < TICK - EPS:
            continue
        for k in kept:
            if abs(k[0] - s) <= p.stop_merge_ticks * TICK + EPS:
                k[1] += f",{typ}"
                break
        else:
            kept.append([s, typ])
    return [(s, t) for s, t in kept]


def zones(dp: dict) -> list[dict[str, Any]]:
    return [{"id": i, "kind": k, "lo": lo, "hi": hi, "lo_src": ls_, "hi_src": hs_}
            for i, k, lo, hi, ls_, hs_ in zip(dp["z_id"], dp["z_kind"], dp["z_lo"], dp["z_hi"], dp["z_lo_src"],
                                               dp["z_hi_src"])]


def next_zone(dp: dict, d: int) -> dict | None:
    """The nearest level zone wholly beyond the entry zone's far edge in the stop direction."""
    best = None
    for z in zones(dp):
        if z["kind"] != "level":
            continue
        if d > 0 and z["hi"] < dp["zone_lo"] - EPS:
            if best is None or z["hi"] > best["hi"]:
                best = z
        if d < 0 and z["lo"] > dp["zone_hi"] + EPS:
            if best is None or z["lo"] < best["lo"]:
                best = z
    return best


def ahead(dp: dict, ref: float, d: int) -> list[dict]:
    """The zones in the trade direction (near edge beyond the reference price), nearest first."""
    out = [z for z in zones(dp) if (z["lo"] - ref if d > 0 else ref - z["hi"]) > EPS]
    out.sort(key=lambda z: (z["lo"] - ref) if d > 0 else (ref - z["hi"]))
    return out


def first_structure(dp: dict, ref: float, target: float, d: int) -> dict | None:
    """F11.3: the first level zone strictly between entry and target (containers excluded), as of `decision_ts`."""
    for z in ahead(dp, ref, d):
        if z["kind"] != "level":
            continue
        if (d > 0 and z["hi"] < target - EPS) or (d < 0 and z["lo"] > target + EPS):
            return z
        return None
    return None


def targets(dp: dict, ref: float, stop: float, d: int, order_type: str, p) -> list[dict[str, Any]]:
    c = p.costs
    stop_pts = abs(ref - stop)
    seen: set[float] = set()

    def priced(z):
        out = []
        for kind, raw in (("near", z["lo"] if d > 0 else z["hi"]), ("center", (z["lo"] + z["hi"]) / 2)):
            px = round_target(raw, d)
            if (px - ref) * d < TICK - EPS:
                continue
            out.append((kind, px, c.net_rr(stop_pts, abs(px - ref), order_type)))
        return out

    base, ext = [], []
    n_base = 0
    for z in ahead(dp, ref, d):
        prices = priced(z)
        good = [x for x in prices if x[2] >= p.min_rr - EPS]
        if good and n_base < p.max_targets:
            n_base += 1
            for kind, px, rr in good:
                if px not in seen:
                    seen.add(px)
                    base.append({"target_kind": f"zone_{kind}", "target_zone": z["id"], "target_zone_kind": z["kind"],
                                 "target": px, "net_rr": rr, "rr_ext": False})
    if dp.get("event_type") == "golden":
        for x in (0.0, -0.272, -0.618):
            v = dp.get(f"fib_{x:g}")
            if v is None:
                continue
            px = round_target(v, d)
            if (px - ref) * d < TICK - EPS or px in seen:
                continue
            rr = c.net_rr(stop_pts, abs(px - ref), order_type)
            if rr >= p.min_rr - EPS:
                seen.add(px)
                base.append({"target_kind": f"fib_{x:g}", "target_zone": None, "target_zone_kind": None,
                             "target": px, "net_rr": rr, "rr_ext": False})
    bpx = round_target(ref + d * p.baseline_rr * stop_pts, d)
    base.append({"target_kind": "baseline", "target_zone": None, "target_zone_kind": None, "target": bpx,
                 "net_rr": c.net_rr(stop_pts, abs(bpx - ref), order_type), "rr_ext": False})
    if p.extension:
        in_base = {t["target_zone"] for t in base if t["target_zone"] is not None}
        n_ext = 0
        for z in ahead(dp, ref, d):
            if n_ext >= p.ext_max_zones:
                break
            if z["id"] in in_base:
                continue
            low = [x for x in priced(z) if p.ext_min_rr - EPS <= x[2] < p.min_rr - EPS]
            low = [x for x in low if x[1] not in seen]
            if not low:
                continue
            n_ext += 1
            for kind, px, rr in low:
                seen.add(px)
                ext.append({"target_kind": f"zone_{kind}", "target_zone": z["id"], "target_zone_kind": z["kind"],
                            "target": px, "net_rr": rr, "rr_ext": True})
    return base + ext


def rr_band(rr: float, ext_min: float, min_rr: float) -> str:
    if rr < 0.5 - EPS:
        return "<0.5"
    if rr < min_rr - EPS:
        return "0.5-0.8"
    return ">=0.8"
