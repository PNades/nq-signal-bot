"""F8 gameplan zones and features (SPEC §5.6; feature_spec F8, F5.9).

A gameplan is used only if it is `status: ok` or owner-confirmed (`GameplanStore.load_effective`, owner overrides
applied, §3.4), and never for a session in ledger months 1–9 (held back until step 20, §5.6). It is known from the
RTH open (written before 09:30, §3.4): its zones exist and its features are computed from 09:30.
- **Zones (containers, F5.9):** [`zone`[0], `zone`[1]] with its `side`; a conditional entry (zone null) uses
  `trigger.nq_price` (a single price [p, p] is a zero-width band); with neither it makes no zone. Crossed by a trade
  past the far edge (the container machine); its `invalidation` is a feature only.
- **Features** for an event at t (its touch price, or the price at `decision_ts`), direction d:
  `gp_in_zone`; the zone they describe = the event's own gameplan zone, else among the gameplan zones containing the
  price the first (by id) whose side matches d, else the first; `gp_zone_side_agrees`; `gp_es_gate_ok` (every ES gate
  of that zone holds; null if it has none or any is unclear; a range holds only beyond its whole span);
  `gp_scenario_agrees` / `gp_scenario_prob_agree` (the `ny_am` set from 09:30 to `gameplan_ny_am_end`, the `session` set
  after; a lone set at any time; the matching set missing while the other exists -> null; `neutral` / `mixed` never
  agree; the probability null if any scenario in the set has none); `gp_hvl` / `gp_hvl0` / `gp_regime_line` (signed
  distance price − the nearer edge, 0 inside; pts and ATR) and `gp_above_hvl` / `gp_above_hvl0`;
  `gp_invalidated` (any of the zone's NQ invalidations met between 09:30 and t, per its timeframe; null if none can be
  computed); `gp_member` (the event's zone overlaps or contains a gameplan zone); `gp_stated_flip_agrees` (null until
  the parser records stated flips, step 20).
All of them stay out of the models until the gameplan check passes (§5.6; gating at step 15).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from typing import Any

from nqbot.features.session_tape import MIN_NS, Tape, bars, et_ns, rth_open_ns

BUY, SELL = "buy", "sell"
ABOVE_WORDS = ("holding", "reclaiming")              # F8 (owner 2026-10-07): only these words, else unclear
BELOW_WORDS = ("losing", "rejecting", "failing")
DEFAULT_ACCEPTANCE_MIN = 15   # F8: `acceptance_minutes` 15 if not given


@dataclass
class GpZone:
    gid: str                 # container id, e.g. gp_E1
    item: dict
    lo: float
    hi: float

    @property
    def side(self) -> int | None:
        s = self.item.get("side")
        return 1 if s == BUY else -1 if s == SELL else None


def zones_of(gp: dict | None) -> list[GpZone]:
    out = []
    for k, z in enumerate((gp or {}).get("zones") or []):
        band = z.get("zone")
        if not band:
            band = ((z.get("trigger") or {}).get("nq_price"))
        if not band or band[0] is None or band[1] is None:
            continue
        lo, hi = sorted((float(band[0]), float(band[1])))
        gid = f"gp_{z.get('id') or k}".replace(" ", "_")
        out.append(GpZone(gid, z, lo, hi))
    return out


def gate_direction(g: dict) -> str | None:
    d = g.get("direction")
    if d in ("above", "below"):
        return d
    w = (g.get("as_written") or "").lower()
    if any(x in w for x in ABOVE_WORDS) and not any(x in w for x in BELOW_WORDS):
        return "above"
    if any(x in w for x in BELOW_WORDS) and not any(x in w for x in ABOVE_WORDS):
        return "below"
    return None


def gate_ok(g: dict, es: float | None) -> bool | None:
    d = gate_direction(g)
    p = g.get("price")
    if d is None or not p or p[0] is None or es is None:
        return None
    lo, hi = sorted((float(p[0]), float(p[1] if p[1] is not None else p[0])))
    if d == "above":
        return True if es > hi else False if es < lo else None
    return True if es < lo else False if es > hi else None


def signed_edge_dist(price: float, band) -> float | None:
    if not band or band[0] is None:
        return None
    lo, hi = sorted((float(band[0]), float(band[1] if band[1] is not None else band[0])))
    return price - hi if price > hi else price - lo if price < lo else 0.0


@dataclass
class Gameplan:
    doc: dict | None
    day: Any
    nq: Tape
    es: Tape
    settings: dict
    bars_1m: Any
    zones: list = field(default_factory=list)
    _bars: dict = field(default_factory=dict)

    def __post_init__(self):
        self.zones = zones_of(self.doc)
        self.open_ns = rth_open_ns(self.day)
        h, m = (int(x) for x in str(self.settings["gameplan_ny_am_end"]).split(":"))
        self.ny_am_end = et_ns(self.day, time(h, m))

    @property
    def present(self) -> bool:
        return self.doc is not None

    def _bars_n(self, n: int):
        if n not in self._bars:
            self._bars[n] = self.bars_1m if n == 1 else bars(self.nq, n)
        return self._bars[n]

    # --- pieces ----------------------------------------------------------------------------------------------
    def containing(self, price: float) -> list[GpZone]:
        return [z for z in self.zones if z.lo <= price <= z.hi]

    def scenario(self, t: int, d: int) -> tuple[bool | None, float | None]:
        sets = {s.get("frame"): s for s in (self.doc or {}).get("scenario_sets") or []}
        if not sets:
            return None, None
        if len(sets) == 1:
            use = next(iter(sets.values()))
        else:
            want = "ny_am" if self.open_ns <= t < self.ny_am_end else "session"
            use = sets.get(want)
            if use is None:
                return None, None
        sc = use.get("scenarios") or []
        if not sc:
            return None, None
        want_bias = "up" if d > 0 else "down"
        agree = [s for s in sc if s.get("bias") == want_bias]
        probs = [s.get("prob") for s in sc]
        prob = None if any(p is None for p in probs) else float(sum(s["prob"] for s in agree))
        return bool(agree), prob

    def invalidated(self, z: GpZone, t: int) -> bool | None:
        res = []
        for inv in z.item.get("invalidation") or []:
            if (inv.get("instrument") or "NQ").upper() != "NQ" or inv.get("price") is None:
                continue
            d = inv.get("direction")
            if d not in ("above", "below"):
                continue
            tf = inv.get("timeframe") or "not_stated"
            if tf == "other":
                continue
            price = float(inv["price"])
            sgn = 1 if d == "above" else -1
            if tf == "1m_close":
                b = self._bars_n(1)
                res.append(self._any_close(b, price, sgn, t, settled=False))
            elif tf in ("settled_bar", "not_stated"):
                res.append(self._any_close(self._bars_n(1), price, sgn, t, settled=True))
            elif tf == "acceptance":
                n = int(inv.get("acceptance_minutes") or DEFAULT_ACCEPTANCE_MIN)
                res.append(self._any_close(self._bars_n(n), price, sgn, t, settled=False))
        if not res:
            return None
        return any(res)

    def _any_close(self, b, price: float, sgn: int, t: int, settled: bool) -> bool:
        import numpy as np

        sel = (b.start >= self.open_ns) & (b.start + b.length_ns <= t)
        c = b.close[sel] * sgn > price * sgn
        if settled:
            c &= b.open[sel] * sgn > price * sgn
        return bool(np.any(c))

    # --- the event's features ------------------------------------------------------------------------------
    def features(self, t: int, price: float, d: int, zone: tuple[float, float] | None, own: str | None = None,
                 atr: float | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {k: None for k in OUTPUTS}
        if not self.present or t < self.open_ns:
            return out
        inside = self.containing(price)
        out["gp_in_zone"] = bool(inside)
        gz = next((z for z in self.zones if z.gid == own), None)
        if gz is None and inside:
            gz = next((z for z in inside if z.side == d), inside[0])
        if gz is not None:
            out["gp_zone_side_agrees"] = None if gz.side is None else gz.side == d
            gates = ((gz.item.get("trigger") or {}).get("es_gates")) or []
            es = self.es.last_price_at(t)
            oks = [gate_ok(g, es) for g in gates]
            out["gp_es_gate_ok"] = None if not oks or any(x is None for x in oks) else all(oks)
            out["gp_invalidated"] = self.invalidated(gz, t)
        out["gp_scenario_agrees"], out["gp_scenario_prob_agree"] = self.scenario(t, d)
        doc = self.doc
        for name in ("hvl", "hvl0", "regime_line"):
            dist = signed_edge_dist(price, doc.get(name))
            out[f"gp_{name}"] = dist
            out[f"gp_{name}_atr"] = None if dist is None or not atr else dist / atr
        for name in ("hvl", "hvl0"):
            dist = out[f"gp_{name}"]
            out[f"gp_above_{name}"] = None if dist is None else dist > 0
        if zone is not None:
            out["gp_member"] = any(z.lo <= zone[1] and z.hi >= zone[0] for z in self.zones)
        out["gp_n_zones_containing"] = len(inside)
        return out


OUTPUTS = ("gp_in_zone", "gp_zone_side_agrees", "gp_es_gate_ok", "gp_scenario_agrees", "gp_scenario_prob_agree",
           "gp_hvl", "gp_hvl_atr", "gp_hvl0", "gp_hvl0_atr", "gp_regime_line", "gp_regime_line_atr", "gp_above_hvl",
           "gp_above_hvl0", "gp_invalidated", "gp_member", "gp_stated_flip_agrees", "gp_n_zones_containing")


def usable(doc: dict | None, day, settings: dict) -> dict | None:
    """status ok or owner-confirmed, and not in ledger months 1–9 (§5.6)."""
    from nqbot import months as months_mod

    if doc is None:
        return None
    if not (doc.get("status") == "ok" or doc.get("owner_confirmed")):
        return None
    k = months_mod.month_index(day, settings)   # None outside the window (later live days): usable
    if k is not None and k <= 9:
        return None
    return doc
