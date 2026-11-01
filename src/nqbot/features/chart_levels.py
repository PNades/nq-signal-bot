"""The per-session chart-level engine (step 11 part 1; SPEC §5.3, §5.4; feature_spec F3, F5.1, F5.2, F5.14).

`ChartSession` builds every chart-level registry record of one CME day on its front contract, runs each level's state
machine (`level_state.LevelState`) over the session from the level's creation (owner 2026-10-31), and answers:
- `registry_at(t)`: the chart levels that exist at t (band, type, sub-type, F5.14 key, state);
- `level_at(level_id, t)`: one level's band, state and F2.6b cross features;
- `features_at(t, direction)`: the session-wide chart features (VWAP, VAH/VAL areas per profile, F3.12 value context,
  expected-move position, F10 opening range, `rvol_today`, `minutes_since_open`, ATR / EM units).
NQ levels: VWAP lines and bands (18:00, 09:30), daily / weekly POC and LVNs, single prints, the previous session's
POC / VAH / VAL, ON high/low, PDH/PDL, prior RTH and week highs/lows, IB high/low, expected-move lines, retested
extremes. ES cross-market levels (F5.1): ES ON high/low, ES PDH/PDL and the ES expected-move lines, with the NQ
parameters × ES ÷ NQ at the open (F0.11; before 09:30 the previous session's ratio, the open's not known yet).
Zones, touches and events are part 3; GEX nodes part 2. `start_session` / `fold_session` move the carried state
(`carry.CarryState`) from one session to the next, roll-shifted on a roll day (F0.4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any

import numpy as np

from nqbot.features import expected_move as emod
from nqbot.features import level_state as ls
from nqbot.features import opening_range as orm
from nqbot.features import prev_profile as pp
from nqbot.features import prev_session as ps
from nqbot.features import profile as pf
from nqbot.features import rvol
from nqbot.features import session_levels as sl
from nqbot.features import tpo
from nqbot.features import vwap as vw
from nqbot.features.bands import StepBand
from nqbot.features.carry import RTH_KEEP, CarryState
from nqbot.features.session_tape import (ET, MIN_NS, NS, Tape, bars, cme_end_ns, cme_start_ns, et_ns, rth_close_ns,
                                         rth_open_ns, week_start_ns)
from nqbot.features.sweep import HIGHS_LOWS

# F5.14: registry sub-type -> level-type key (chart_level_half_width / flip_margin)
SUBTYPE_KEY = {
    **{vw.sub_type(a, k): vw.key_of(k) for a in vw.ANCHORS for k in vw.LINES},
    **{emod.sub_type(k): "em_level" for k in (-2, -1, 1, 2)},
    "poc_daily": "poc", "poc_weekly": "poc", "prev_poc": "poc", "prev_vah": "va_edge", "prev_val": "va_edge",
    "lvn_daily": "lvn", "lvn_weekly": "lvn", "single_print": "single_print", "on_high": "on_extreme",
    "on_low": "on_extreme", "pdh": "pdh_pdl", "pdl": "pdh_pdl", "prior_rth_high": "prior_rth",
    "prior_rth_low": "prior_rth", "prior_week_high": "prior_week", "prior_week_low": "prior_week", "ib_high": "ib",
    "ib_low": "ib", "retested_extreme": "retested_extreme",
}
REGISTRY_TYPE = {"vwap": "vwap", "vwap_band": "vwap", "em_level": "expected_move", "poc": "volume_profile",
                 "va_edge": "volume_profile", "lvn": "volume_profile", "single_print": "volume_profile",
                 "on_extreme": "session_hl", "pdh_pdl": "session_hl", "prior_rth": "session_hl",
                 "prior_week": "session_hl", "retested_extreme": "session_hl", "ib": "opening_range"}


# Q-step11c (1), owner 2026-10-31: a high starts as a ceiling and a low as a floor when price is on the range side as
# the level appears (otherwise the approach rule); the IB gets its roles at the lock unconditionally (F3.6)
NATURAL_ROLE = {"on_high": "ceiling", "on_low": "floor", "pdh": "ceiling", "pdl": "floor",
                "prior_rth_high": "ceiling", "prior_rth_low": "floor", "prior_week_high": "ceiling",
                "prior_week_low": "floor"}


def kind_of(key: str, features: dict) -> str:
    if key == "single_print":
        return ls.SINGLE_PRINT
    if key in HIGHS_LOWS or (key == "va_edge" and bool(features["va_edge_sweep"])):
        return ls.SWEEP
    return ls.CHART


@dataclass
class LevelRecord:
    level_id: str
    sub_type: str
    key: str
    instrument: str
    multi_day: bool
    state: ls.LevelState

    @property
    def type(self) -> str:
        return REGISTRY_TYPE[self.key]


@dataclass
class SessionInputs:
    """Everything a session needs besides its tape (all known by the session start, F0.1)."""
    features: dict
    settings: dict
    cal: dict | None
    normals: np.ndarray | None            # F9.10 N(m) (None with fewer than 20 baseline sessions)
    tempest_row: dict | None              # the pre-open row of session T − tempest_lag_sessions (QQQ / SPY)
    em_calendar_days: int | None = None   # for tempest_lag_sessions = 2 (F3.11)
    eth_empty: bool = False               # §3.2: today's overnight is damaged
    retested: list = field(default_factory=list)   # session_levels.Retested in force today (from part 3's labels)
    scale_today: float = 1.0              # ES ÷ NQ at today's open (ES only, F0.11)
    scale_prev: float = 1.0               # ... at the previous session's open (used before today's is known)
    scale_from_ns: int | None = None      # when today's ratio is known: the later of the first RTH trades of NQ and ES


def preopen_ns(day: date, settings: dict) -> int:
    h, m = (int(x) for x in str(settings["preopen_start"]).split(":"))
    return et_ns(day, time(h, m))


class ChartSession:
    def __init__(self, tape: Tape, carry: CarryState, inp: SessionInputs, root: str = "NQ"):
        self.tape, self.carry, self.inp, self.root, self.day = tape, carry, inp, root, tape.day
        f = inp.features
        self.f = f
        self.levels: dict[str, list[LevelRecord]] = {}
        self.start_ns, self.end_ns = cme_start_ns(self.day), cme_end_ns(self.day)
        self.open_ns, self.close_ns = rth_open_ns(self.day), rth_close_ns(self.day, inp.cal)
        self.bars_1m = bars(tape, 1)
        self.bars_flip = bars(tape, f["flip_bar_minutes"])
        self.atr = emod.atr(carry.rth_sessions(), f["atr_period"])
        lag = f["tempest_lag_sessions"]
        self.em = emod.ExpectedMove(carry.prev_close(), emod.em_fraction(inp.tempest_row, lag, inp.em_calendar_days),
                                    tuple(f["em_level_sigmas"]))
        self.normals = None if inp.normals is None else rvol.RthNormals.of(self.day, inp.normals, inp.cal)
        self.volume = rvol.SessionVolume.of(tape, inp.normals, f, inp.cal)
        need = max(f["atr_period"] + 1, f["ib_avg_sessions"])
        if RTH_KEEP < need:   # the carry keeps RTH_KEEP sessions; a longer lookback would be cut silently
            raise ValueError(f"carry.RTH_KEEP {RTH_KEEP} < the lookbacks need ({need}): raise RTH_KEEP")
        self.on = sl.on_levels(tape, inp.eth_empty)
        self.vwap: dict[str, vw.Vwap] = {}
        self.daily = self.weekly = None
        self.sp = None
        self.opening = None
        self.prev_prof = None
        self.prev_naked_at_start = False
        if root == "NQ":
            self._build_nq()
        else:
            self._build_es()

    # --- helpers ---------------------------------------------------------------------------------------------
    def _hw(self, key: str) -> float:
        return float(self.f["chart_level_half_width"][key])

    def _add(self, level_id: str, sub_type: str, band, multi_day: bool, end_ns: int | None = None,
             params_from: list | None = None, params: ls.Params | None = None, initial_role: str | None = None) -> None:
        key = SUBTYPE_KEY[sub_type]
        # a level that ends (an LVN span, a moved IB, a filled single print) stops its machine there
        stop = end_ns or getattr(band, "end_ns", None) or self.end_ns
        st = ls.LevelState(level_id, kind_of(key, self.f), self.tape, band,
                           params or ls.Params.from_features(self.f, key), self.bars_1m, self.bars_flip,
                           normals=self.normals, end_ns=min(stop, self.end_ns), params_from=params_from or [],
                           initial_role=initial_role,
                           natural_role=None if initial_role else NATURAL_ROLE.get(sub_type))
        self.levels.setdefault(level_id, []).append(LevelRecord(level_id, sub_type, key, self.root, multi_day, st.run()))

    def _const(self, level_id: str, sub_type: str, price: float | None, start_ns: int, multi_day: bool,
               end_ns: int | None = None, initial_role: str | None = None) -> None:
        if price is None:
            return
        hw = self._hw(SUBTYPE_KEY[sub_type])
        self._add(level_id, sub_type, StepBand.const(self.tape, start_ns, price - hw, price + hw, end_ns), multi_day,
                  initial_role=initial_role)

    # --- NQ --------------------------------------------------------------------------------------------------
    def _build_nq(self) -> None:
        f, d, tape, c = self.f, self.day, self.tape, self.carry
        ds = d.isoformat()
        # F3.1 VWAP lines and bands
        for anchor, start in (("vwap18", self.start_ns), ("vwap0930", self.open_ns)):
            v = self.vwap[anchor] = vw.Vwap.build(tape, start)
            for k in vw.LINES:
                st_ = vw.sub_type(anchor, k)
                self._add(f"{st_}-{ds}", st_, v.band(k, self._hw(vw.key_of(k))), multi_day=False)
        # F3.2-F3.4 developing daily and weekly profiles
        lvn = {k: f[k] for k in ("lvn_smooth_ticks", "lvn_gauss_trunc_sigma", "lvn_max_pct", "lvn_merge_pts")}
        upd = f["profile_update_sec"]
        self.daily = pf.DevelopingProfile.build("daily", tape, self.start_ns, self.start_ns, self.end_ns, upd, lvn)
        carried = {int(k): v for k, v in c.weekly_volume.items()} or None
        anchor = c.weekly_anchor_ns if c.weekly_anchor_ns is not None else self.start_ns
        self.weekly = pf.DevelopingProfile.build("weekly", tape, anchor, self.start_ns, self.end_ns, upd, lvn, carried)
        week_id = datetime.fromtimestamp(anchor / NS, ET).date().isoformat()
        for prof, name, ident in ((self.daily, "daily", ds), (self.weekly, "weekly", week_id)):
            self._profile_levels(prof, name, ident)
        # F3.5 single prints (carried runs first)
        self.sp = tpo.SinglePrints(f["tpo_tick_pts"], f["sp_min_ticks"], f["max_zone_width"]).run_session(
            tape, d, [tpo.Run.from_json(r) for r in c.single_prints], end_ns=self.end_ns)
        hw = self._hw("single_print")
        for lv in self.sp.levels.values():
            band = StepBand(tape, np.asarray(lv.times, dtype=np.int64), np.asarray(lv.lo) - hw,
                            np.asarray(lv.hi) + hw, lv.end_ns)
            self._add(f"sp-{lv.level_id}", "single_print", band, multi_day=True)
        # F3.2b previous session's profile
        anchor_name = f["prev_profile_anchor"]
        if c.prev_profile is not None and c.prev_profile.get(anchor_name):
            self.prev_prof = c.prev_profile[anchor_name]
            self.prev_naked_at_start = bool(self.prev_prof.get("naked_at_start", True))
            pd = c.prev_profile["day"]
            for st_, k in (("prev_poc", "poc"), ("prev_vah", "vah"), ("prev_val", "val")):
                self._const(f"{st_}-{pd}", st_, self.prev_prof[k], self.start_ns, multi_day=True)
        # F3.7 session highs/lows
        if self.on is not None:
            self._const(f"on_high-{ds}", "on_high", self.on[0], self.open_ns, False)
            self._const(f"on_low-{ds}", "on_low", self.on[1], self.open_ns, False)
        if c.prev_day:
            self._const(f"pdh-{c.prev_day['day']}", "pdh", c.prev_day["high"], self.start_ns, True)
            self._const(f"pdl-{c.prev_day['day']}", "pdl", c.prev_day["low"], self.start_ns, True)
        if c.prev_rth:
            self._const(f"prior_rth_high-{c.prev_rth['day']}", "prior_rth_high", c.prev_rth["high"], self.start_ns, True)
            self._const(f"prior_rth_low-{c.prev_rth['day']}", "prior_rth_low", c.prev_rth["low"], self.start_ns, True)
        if c.prev_week:
            wk = c.prev_week.get("week", "")
            self._const(f"prior_week_high-{wk}", "prior_week_high", c.prev_week["high"], self.start_ns, True)
            self._const(f"prior_week_low-{wk}", "prior_week_low", c.prev_week["low"], self.start_ns, True)
        # F3.6 / F10 opening range, IB high/low
        self.opening = orm.OpeningRange.build(tape, self.bars_1m, f, self.inp.cal)
        for lv in self.opening.ib_levels:
            # F3.6: before a break the IB high is a ceiling and the IB low a floor (the 10:00 lock, price inside the
            # range); the 10:30 replacement is a fresh level whose role comes from the approach rule
            role = None if lv.level_id.endswith("-1030") else (ls.CEILING if lv.side == "high" else ls.FLOOR)
            self._const(lv.level_id, f"ib_{lv.side}", lv.price, lv.created_ns, False, end_ns=lv.end_ns,
                        initial_role=role)
        # F3.11 expected-move lines, known at the pre-open
        pre = preopen_ns(d, self.inp.settings)
        for k, price in self.em.lines().items():
            st_ = emod.sub_type(k)
            self._const(f"{st_}-{ds}", st_, price, pre, False)
        # F3.8 retested extremes in force today (created by part 3's held labels)
        for r in self.inp.retested:
            self._const(r.level_id, "retested_extreme", r.price, max(r.created_ns, self.start_ns), True)

    def _profile_levels(self, prof: pf.DevelopingProfile, name: str, ident: str) -> None:
        if not len(prof.times):
            return
        hw_poc, hw_lvn = self._hw("poc"), self._hw("lvn")
        poc = np.array([s.poc for s in prof.snaps])
        self._add(f"poc_{name}-{ident}", f"poc_{name}", StepBand(self.tape, prof.times, poc - hw_poc, poc + hw_poc),
                  multi_day=name == "weekly")
        # LVNs: one level per identity and contiguous span of instants it qualifies at
        spans: dict[float, list] = {}
        open_: dict[float, list] = {}
        for t, s in zip(prof.times.tolist(), prof.snaps):
            now = dict(s.lvns)
            for ident_p in list(open_):
                if ident_p not in now:
                    spans.setdefault(ident_p, []).append((open_.pop(ident_p), t))
            for ident_p, price in now.items():
                open_.setdefault(ident_p, []).append((t, price))
        for ident_p, steps in open_.items():
            spans.setdefault(ident_p, []).append((steps, None))
        for ident_p, runs in spans.items():
            for steps, end in runs:
                ts = np.array([x[0] for x in steps], dtype=np.int64)
                px = np.array([x[1] for x in steps])
                band = StepBand(self.tape, ts, px - hw_lvn, px + hw_lvn, end)
                self._add(f"lvn_{name}-{ident}-{ident_p:g}", f"lvn_{name}", band, multi_day=name == "weekly")

    # --- ES (cross-market levels only, F5.1) ----------------------------------------------------------------
    def _build_es(self) -> None:
        f, c, ds = self.f, self.carry, self.day.isoformat()
        sc_prev, sc_now = self.inp.scale_prev, self.inp.scale_today
        # today's ratio is known from the later of the first RTH trades of NQ and ES (F0.11)
        switch = max(self.open_ns, self.inp.scale_from_ns or self.open_ns)

        def const_es(level_id, sub_type, price, start_ns):
            if price is None:
                return
            key = SUBTYPE_KEY[sub_type]
            hw = self._hw(key)
            times = np.array([start_ns] + ([switch] if start_ns < switch else []), dtype=np.int64)
            scales = [sc_prev, sc_now] if start_ns < switch else [sc_now]
            band = StepBand(self.tape, times, np.array([price - hw * s for s in scales]),
                            np.array([price + hw * s for s in scales]))
            first = ls.Params.from_features(f, key, scales[0])
            later = [(switch, ls.Params.from_features(f, key, sc_now))] if len(scales) > 1 else []
            self._add(level_id, sub_type, band, multi_day=sub_type in ("pdh", "pdl"), params=first, params_from=later)

        if self.on is not None:
            const_es(f"es_on_high-{ds}", "on_high", self.on[0], self.open_ns)
            const_es(f"es_on_low-{ds}", "on_low", self.on[1], self.open_ns)
        if c.prev_day:
            const_es(f"es_pdh-{c.prev_day['day']}", "pdh", c.prev_day["high"], self.start_ns)
            const_es(f"es_pdl-{c.prev_day['day']}", "pdl", c.prev_day["low"], self.start_ns)
        pre = preopen_ns(self.day, self.inp.settings)
        for k, price in self.em.lines().items():
            const_es(f"es_{emod.sub_type(k)}-{ds}", emod.sub_type(k), price, pre)

    # --- queries ---------------------------------------------------------------------------------------------
    def _record_at(self, level_id: str, t: int) -> LevelRecord | None:
        recs = [r for r in self.levels.get(level_id, []) if r.state.band.start_ns <= t]
        for r in reversed(recs):
            if r.state.band.at(t) is not None:
                return r
        return None

    def registry_at(self, t: int) -> list[dict[str, Any]]:
        out = []
        for lid in self.levels:
            r = self._record_at(lid, t)
            if r is not None:
                out.append(self.level_at(lid, t, r))
        return out

    def level_at(self, level_id: str, t: int, rec: LevelRecord | None = None) -> dict[str, Any] | None:
        r = rec or self._record_at(level_id, t)
        if r is None:
            return None
        band = r.state.band.at(t)
        em = self.em.points if t >= preopen_ns(self.day, self.inp.settings) else None   # known at the pre-open
        return {"level_id": level_id, "type": r.type, "sub_type": r.sub_type, "key": r.key,
                "instrument": r.instrument, "multi_day": r.multi_day, "kind": r.state.kind,
                "created_ns": int(r.state.band.start_ns), "band": band, **r.state.at(t, self.atr, em)}

    def features_at(self, t: int, direction: int | None = None) -> dict[str, Any]:
        """Session-wide chart features as of t (each from data with known time <= t)."""
        price = self.tape.last_price_at(t)
        em_pts = self.em.points if t >= preopen_ns(self.day, self.inp.settings) else None
        out: dict[str, Any] = {"price": price, "atr": self.atr, "em_pts": em_pts,
                               "minutes_since_open": self.volume.minutes_since_open(t),
                               "rvol_today": self.volume.rvol_today(t)}
        if self.root != "NQ" or price is None:
            return out
        for name, v in self.vwap.items():
            a = v.at(t)
            out[f"{name}"] = None if a is None else a[0]
            out[f"{name}_sigma"] = None if a is None else a[1]
        for name, prof in (("daily", self.daily), ("weekly", self.weekly)):
            snap = prof.at(t) if prof is not None else None
            va = pf.va_features(snap, price, direction)
            for k, v in va.items():
                out[f"{name}_{k}"] = v
            for side in ("vah", "val"):
                _, a, e = emod.dist3(va[f"dist_{side}"], self.atr, em_pts)
                out[f"{name}_dist_{side}_atr"], out[f"{name}_dist_{side}_em"] = a, e
        # F3.2b / F3.12
        prof = self.prev_prof
        out["prev_poc_naked"] = None if prof is None else ps.naked_at(self.tape, prof, self.prev_naked_at_start, t)
        first_k = int(np.searchsorted(self.daily.times, self.open_ns)) if self.daily is not None else 0
        dev_first = self.daily.snaps[first_k] if self.daily is not None and first_k < len(self.daily.snaps) and \
            self.daily.times[first_k] <= t else None
        out.update(ps.context(price, prof, em_pts, self.daily.at(t) if self.daily else None, dev_first, direction))
        open_px = self.tape.price[self.tape.idx_before(self.open_ns)] if t >= self.open_ns and \
            self.tape.idx_before(self.open_ns) < self.tape.idx_at(t) else None
        open_px = None if open_px is None else float(open_px)
        out["open_vs_prev_va"] = ps.open_vs_prev_va(open_px, prof)
        out["open_outside_prev_va_em"] = ps.open_outside_prev_va_em(open_px, prof, em_pts)
        # F3.11 position against the expected-move lines (known from the pre-open)
        if em_pts is not None:
            out["running_sigma"] = self.em.running_sigma(price)
            above, below = self.em.nearest(price)
            for name, kv in (("above", above), ("below", below)):
                d = None if kv is None else abs(kv[1] - price)
                p_, a_, e_ = emod.dist3(d, self.atr, em_pts)
                out[f"em_line_{name}_k"] = None if kv is None else kv[0]
                out[f"em_line_{name}_pts"], out[f"em_line_{name}_atr"], out[f"em_line_{name}_em"] = p_, a_, e_
        # F10 opening range
        ib_avg = None
        widths = self.carry.ib_widths(self.f["ib_avg_sessions"])
        if len(widths) == self.f["ib_avg_sessions"]:
            ib_avg = sum(widths) / len(widths)
        on_hi, on_lo = self.on if self.on is not None else (None, None)
        for k, v in self.opening.features_at(t, self.tape, on_hi, on_lo, self.atr, em_pts, ib_avg).items():
            out[f"or_{k}"] = v
        return out


# --- moving the carried state from session to session ------------------------------------------------------

class RollSpreadMissing(ValueError):
    """A contract change with no measured roll spread: carried levels can't be shifted (F0.4)."""


def start_session(carry: CarryState, day: date, contract: str, spread: float | None) -> bool:
    """Before a session: roll shift (F0.4), a new week, the weekly profile's anchor. Returns True on a roll day.
    `spread` = the measured roll spread of `day` (None if none is recorded): a roll without one is refused, never
    left unshifted."""
    roll = carry.contract is not None and contract != carry.contract
    if roll:
        if spread is None:
            raise RollSpreadMissing(f"{carry.root} {day}: {carry.contract} -> {contract} but no roll spread is "
                                    f"recorded (processed/rolls/); carried levels would be off by the spread")
        carry.shift(spread)
    ws = week_start_ns(day)
    if carry.week_start_ns != ws:
        if carry.week is not None:
            wk = datetime.fromtimestamp(carry.week_start_ns / NS, ET).date().isoformat()
            carry.prev_week = {**carry.week, "week": wk}
        carry.week, carry.week_start_ns = None, ws
        carry.weekly_volume, carry.weekly_anchor_ns = {}, ws
    if roll:   # one contract only: the weekly profile restarts on the new contract from the roll day (owner 2026-10-24)
        carry.weekly_volume, carry.weekly_anchor_ns = {}, cme_start_ns(day)
    carry.contract = contract
    return roll


def roll_spread_of(root: str, day: date, rolls: list[dict]) -> float | None:
    """The recorded roll spread of `day` (None if no roll is recorded that day)."""
    for r in rolls:
        if r["root"] == root and r["roll_day"] == day.isoformat():
            return float(r["spread"])
    return None


def ib_width(tape: Tape, features: dict) -> float | None:
    """The locked IB width: the 09:30 - lock range (F3.10 `ib_width_ratio`, F10 range ÷ its average)."""
    o = rth_open_ns(tape.day)
    p = tape.price[tape.window(o, o + features["ib_lock_minute"] * MIN_NS)]
    return float(p.max() - p.min()) if len(p) else None


def fold_session(carry: CarryState, tape: Tape, features: dict, cal: dict | None, *, dropped: bool = False,
                 single_prints: list | None = None, weekly_end_volume: dict | None = None,
                 retest_candidates: list | None = None) -> None:
    """After a session: hand its levels and statistics to the next one."""
    d = tape.day
    s = sl.day_stats(tape, cal)
    if s["rth_close"] is not None:
        o = rth_open_ns(d)
        i = tape.idx_before(o)
        carry.rth.append({"day": d.isoformat(), "open": float(tape.price[i]) if i < len(tape) else None,
                          "high": s["rth_high"], "low": s["rth_low"], "close": s["rth_close"],
                          "ib_width": ib_width(tape, features)})
        carry.rth = carry.rth[-RTH_KEEP:]
    if s["high"] is not None:
        carry.prev_day = {"day": d.isoformat(), "high": s["high"], "low": s["low"]}
        carry.week = ({"high": s["high"], "low": s["low"]} if carry.week is None else
                      {"high": max(carry.week["high"], s["high"]), "low": min(carry.week["low"], s["low"])})
    carry.prev_rth = None if s["rth_high"] is None else {"day": d.isoformat(), "high": s["rth_high"], "low": s["rth_low"]}
    if weekly_end_volume is None:
        weekly_end_volume = dict(carry.weekly_volume)
        for k, v in ps._ticks(tape.price, tape.size).items():
            weekly_end_volume[k] = weekly_end_volume.get(k, 0.0) + v
    carry.weekly_volume = {str(k): float(v) for k, v in weekly_end_volume.items()}
    prev = {"day": d.isoformat()}
    for anchor in pp.ANCHORS:
        prof = ps.profile_from_trades(tape, anchor, cal_early_close(d, cal))
        if prof is not None:
            a, b = pp.profile_window(d, anchor, cal_early_close(d, cal))
            after = tape.price[tape.idx_before(int(b.timestamp()) * NS):]
            prof["naked_at_start"] = ps.naked_after(prof, after)
        prev[anchor] = prof
    carry.prev_profile = prev
    if single_prints is not None:
        carry.single_prints = [r.to_json() for r in single_prints]
    if retest_candidates is not None:
        carry.retest_candidates = retest_candidates
    r5 = rth_returns(tape, features, cal)
    if r5 is not None:   # Q-er vr_prior: a full session (no early close) the damage rule didn't drop
        from nqbot.features.rvol import is_full_session
        carry.rth5 = (carry.rth5 + [{"day": d.isoformat(), "full": bool(is_full_session(d, cal) and not dropped),
                                     "r": r5}])[-rth5_keep(features):]
    if dropped:
        carry.drop_handed_levels()
    carry.session = d.isoformat()
    carry.contract = tape.symbol


RTH5_KEEP = 20   # sessions of RTH 5-minute returns kept at least (room for half days and dropped sessions)


def rth5_keep(features: dict) -> int:
    """Sessions of RTH 5-minute returns kept: enough for `vr_sessions` full ones (twice it, at least RTH5_KEEP)."""
    return max(RTH5_KEEP, 2 * int(features.get("vr_sessions", 0)))


def rth_returns(tape: Tape, features: dict, cal: dict | None) -> list[float] | None:
    """The session's RTH log returns on clock-aligned `er_bar_minutes` bars, the first from the RTH open (the first
    RTH trade) to the first bar's close (Q-er; within the session only, no overnight return)."""
    o, c = rth_open_ns(tape.day), rth_close_ns(tape.day, cal)
    i0 = tape.idx_before(o)
    if i0 >= tape.idx_before(c):
        return None
    b = bars(tape, int(features["er_bar_minutes"]))
    sel = (b.start >= o) & (b.start + b.length_ns <= c)
    closes = np.r_[float(tape.price[i0]), b.close[sel]]
    if len(closes) < 2:
        return None
    return np.diff(np.log(closes)).tolist()


def cal_early_close(day: date, cal: dict | None) -> time | None:
    from nqbot import calendar as cal_mod

    return cal_mod.early_close_et(day, cal)
