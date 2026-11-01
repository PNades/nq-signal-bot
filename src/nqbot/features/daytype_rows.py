"""F3.10 day-type inputs, the day-type rows and the F11.6 EOD label (SPEC §7.1 Day type; feature_spec F3.10, F11.6,
F11.7).

Everything on RTH from 09:30 on the session's front-contract NQ trades, each known at t:
- `ib_width_ratio` = the locked IB width (the 09:30–10:00 range) ÷ the mean of the last `ib_avg_sessions` widths
  (null before the 10:00 lock or with fewer widths);
- `vwap_crosses` / `poc_crosses`: settled clock-aligned `daytype_cross_bar_minutes` bars on the other side of the 09:30
  VWAP / the developing daily POC (`daytype.crosses`, known at each bar's close; `poc_crosses` is a LightGBM
  challenger candidate only, never in the label);
- `rotations`: a zigzag on trades (dedupe of equal consecutive prices changes nothing). The first move of
  `rotation_min_pts` × ATR from the first RTH trade sets the swing direction (it is not a rotation); then each move of
  that size against the current swing extreme is one rotation, the direction reverses and the new swing starts from that
  extreme;
- `time_above_vwap_frac` = the share of RTH seconds since the first RTH trade with the last price above the 09:30 VWAP
  (the VWAP after that trade); `vwap_side_share` = max(f, 1 − f);
- `new_highs` / `new_lows` = closed RTH 1-minute bars whose high (low) exceeded every earlier RTH bar's (the first bar
  sets the start and counts for neither); `new_extreme_skew` = |`new_highs` − `new_lows`|;
- `range_vs_em` = the RTH range so far ÷ the expected move (F0.8);
- `regime_*` (F2.12, per map) and the vol context (F2.21) come from the Skylit engine and the daily context;
- Q-er (owner 2026-10-28; LightGBM day-type challenger candidates only): `er_so_far` and `er_<w>` (w in
  `er_windows_min`), Kaufman's Efficiency Ratio on clock-aligned `er_bar_minutes` bars, and `vr_prior`, the
  Lo–MacKinlay variance ratio at q = `vr_q` over the prior `vr_sessions` full RTH sessions (completed only, known at
  the open; `carry.rth5`).
Rows: one per `daytype_snapshot_min` from 10:00 while before the RTH close. The EOD label (`daytype.eod_label`, the
current rule: VWAP crosses <= `trend_max_vwap_crosses`, the close in the outer `trend_close_pct` on the side of
`new_highs` − `new_lows`) is an outcome, stored apart from the rows.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from nqbot.features import daytype as dt
from nqbot.features.session_tape import MIN_NS, NS, Tape, bars


def zigzag_times(ts: np.ndarray, p: np.ndarray, thr: float) -> list[int]:
    """Rotation times (the trade that completes each reversal)."""
    if not len(p) or not thr or thr <= 0:
        return []
    keep = np.r_[True, p[1:] != p[:-1]]
    ts, p = ts[keep], p[keep]
    out: list[int] = []
    start = float(p[0])
    hi = lo = start
    d = 0
    ext = start
    for t, x in zip(ts.tolist(), p.tolist()):
        if d == 0:
            hi, lo = max(hi, x), min(lo, x)
            if x - start >= thr:
                d, ext = 1, hi
            elif start - x >= thr:
                d, ext = -1, lo
            continue
        if d > 0:
            if x > ext:
                ext = x
            elif ext - x >= thr:
                out.append(int(t))
                d, ext = -1, x
        else:
            if x < ext:
                ext = x
            elif x - ext >= thr:
                out.append(int(t))
                d, ext = 1, x
    return out


@dataclass
class DayType:
    """Running F3.10 inputs over one session's NQ chart session (`chart_levels.ChartSession`)."""
    cs: Any
    features: dict
    ib_widths: list

    def __post_init__(self):
        cs, f = self.cs, self.features
        tape: Tape = cs.tape
        self.open_ns, self.close_ns = cs.open_ns, cs.close_ns
        self.lock_ns = self.open_ns + int(f["ib_lock_minute"]) * MIN_NS
        i0, i1 = tape.idx_before(self.open_ns), tape.idx_before(self.close_ns)
        self.ts, self.p = tape.ts[i0:i1], tape.price[i0:i1]
        self.hi_run = np.maximum.accumulate(self.p) if len(self.p) else self.p
        self.lo_run = np.minimum.accumulate(self.p) if len(self.p) else self.p
        vw = cs.vwap["vwap0930"].vwap[i0:i1]
        above = self.p > vw
        dur = np.diff(self.ts) / NS
        self.above = above
        self.cum_above = np.r_[0.0, np.cumsum(np.where(above[:-1], dur, 0.0))] if len(self.p) else np.zeros(0)
        # new RTH highs / lows by closed 1-minute bars
        b = cs.bars_1m
        sel = (b.start >= self.open_ns) & (b.start + b.length_ns <= self.close_ns)
        hs, ls_, ends = b.high[sel], b.low[sel], b.start[sel] + b.length_ns
        self.nh_t, self.nl_t = [], []
        if len(hs):
            prev_h = np.maximum.accumulate(hs)[:-1]
            prev_l = np.minimum.accumulate(ls_)[:-1]
            self.nh_t = ends[1:][hs[1:] > prev_h].tolist()
            self.nl_t = ends[1:][ls_[1:] < prev_l].tolist()
        # settled-bar crosses of the 09:30 VWAP and the developing daily POC
        bm = int(f["daytype_cross_bar_minutes"])
        b5 = bars(tape, bm)
        sel5 = (b5.start >= self.open_ns) & (b5.start + b5.length_ns <= self.close_ns)
        v = cs.vwap["vwap0930"]
        self.vx_t = self._cross_times(b5, sel5, lambda t: (lambda a: None if a is None else a[0])(v.at(t)))
        self.px_t = self._cross_times(b5, sel5, lambda t: (lambda s: None if s is None else s.poc)(cs.daily.at(t)))
        thr = None if cs.atr is None else float(f["rotation_min_pts"]) * cs.atr
        self.rot_t = zigzag_times(self.ts, self.p, thr) if thr else []
        self.rot_ok = thr is not None
        # Q-er: Efficiency Ratio on clock-aligned 5-minute bars from the RTH open; vr_prior from completed sessions
        be = bars(tape, int(f["er_bar_minutes"]))
        sel_e = (be.start >= self.open_ns) & (be.start + be.length_ns <= self.close_ns)
        self.er_ends = (be.start[sel_e] + be.length_ns).astype(np.int64)
        closes = be.close[sel_e]
        self.er_c = np.r_[float(self.p[0]), closes] if len(self.p) else np.zeros(0)
        self.er_cum = np.r_[0.0, np.cumsum(np.abs(np.diff(self.er_c)))] if len(self.er_c) else np.zeros(0)
        carry = getattr(cs, "carry", None)
        self.vr_prior = variance_ratio([x["r"] for x in (getattr(carry, "rth5", None) or []) if x.get("full")]
                                       [-int(f["vr_sessions"]):], int(f["vr_q"]), int(f["vr_sessions"]))

    @staticmethod
    def _cross_times(b, sel, level_at: Callable[[int], float | None]) -> list[int]:
        side, out = None, []
        for k in np.flatnonzero(sel):
            t0 = int(b.start[k])
            t1 = t0 + b.length_ns
            s = dt.settled_side(dt.LevelBar(float(b.open[k]), float(b.close[k]), level_at(t0), level_at(t1)))
            if s is None:
                continue
            if side is not None and s != side:
                out.append(t1)
            side = s
        return out

    # --- queries ---------------------------------------------------------------------------------------------
    def counts_at(self, t: int) -> tuple[int, int]:
        return bisect_right(self.nh_t, t), bisect_right(self.nl_t, t)

    def at(self, t: int) -> dict[str, Any]:
        f = self.features
        out: dict[str, Any] = {k: None for k in INPUTS}
        out.update({f"er_{w}": None for w in f["er_windows_min"]})
        out["vr_prior"] = self.vr_prior
        out["minutes_since_open"] = (t - self.open_ns) / MIN_NS
        if t < self.open_ns:
            return out
        n = int(np.searchsorted(self.ts, t, side="right"))
        nh, nl = self.counts_at(t)
        out.update(new_highs=nh, new_lows=nl, new_extreme_skew=abs(nh - nl),
                   vwap_crosses=bisect_right(self.vx_t, t), poc_crosses=bisect_right(self.px_t, t))
        if self.rot_ok:
            out["rotations"] = bisect_right(self.rot_t, t)
        if n:
            secs = (t - int(self.ts[0])) / NS
            above = self.cum_above[n - 1] + ((t - int(self.ts[n - 1])) / NS if self.above[n - 1] else 0.0)
            if secs > 0:
                frac = above / secs
                out["time_above_vwap_frac"] = frac
                out["vwap_side_share"] = max(frac, 1.0 - frac)
            rng = float(self.hi_run[n - 1] - self.lo_run[n - 1])
            em = self.cs.em.points
            out["rth_range_pts"] = rng
            out["range_vs_em"] = None if not em else rng / em
        out.update(self.er_at(t))
        if t >= self.lock_ns:
            p = self.p[(self.ts < self.lock_ns)]
            w = self.ib_widths[-int(f["ib_avg_sessions"]):]
            if len(p) and len(w) == int(f["ib_avg_sessions"]) and sum(w) > 0:
                out["ib_width_ratio"] = float(p.max() - p.min()) / (sum(w) / len(w))
        return out

    def er_at(self, t: int) -> dict[str, Any]:
        """`er_so_far` = |c_last − RTH open| ÷ Σ |5-minute close-to-close moves| from the open (bars closed by t);
        `er_<w>` = the same over the last w minutes of closed bars (from the last close at or before e_last − w, the
        RTH open if that is the open); null while the move sum is 0 or before w minutes of bars exist (Q-er)."""
        out: dict[str, Any] = {"er_so_far": None, "vr_prior": self.vr_prior,
                               **{f"er_{w}": None for w in self.features["er_windows_min"]}}
        k = int(np.searchsorted(self.er_ends, t, side="right"))
        if k == 0:
            return out
        c, cum = self.er_c, self.er_cum
        if cum[k] > 0:
            out["er_so_far"] = abs(c[k] - c[0]) / cum[k]
        e_last = int(self.er_ends[k - 1])
        for w in self.features["er_windows_min"]:
            ref_t = e_last - int(w) * MIN_NS
            if ref_t < self.open_ns:
                continue
            j = int(np.searchsorted(self.er_ends[:k], ref_t, side="right"))   # closes 1..j end at or before ref_t
            moves = cum[k] - cum[j]
            if moves > 0:
                out[f"er_{w}"] = abs(c[k] - c[j]) / moves
        return out

    def snapshot_times(self) -> list[int]:
        step = int(self.features["daytype_snapshot_min"]) * MIN_NS
        return list(range(self.lock_ns, self.close_ns, step))

    def eod(self) -> dict[str, Any] | None:
        if not len(self.p):
            return None
        f = self.features
        t = self.close_ns
        nh, nl = self.counts_at(t)
        vx = bisect_right(self.vx_t, t)
        close, hi, lo = float(self.p[-1]), float(self.hi_run[-1]), float(self.lo_run[-1])
        lab = dt.eod_label(vx, close, hi, lo, nh, nl, int(f["trend_max_vwap_crosses"]), float(f["trend_close_pct"]))
        return {"day_type": lab, "vwap_crosses": vx, "poc_crosses": bisect_right(self.px_t, t), "new_highs": nh,
                "new_lows": nl, "close": close, "rth_high": hi, "rth_low": lo, "trend_side": dt.trend_side(nh, nl)}


def variance_ratio(sessions: list, q: int, need: int) -> float | None:
    """Lo–MacKinlay VR(q) of 5-minute log returns pooled over `need` completed full sessions, overlapping q-sums
    within each session only (no overnight return): σ²_c / σ²_a with σ²_a = Σ(r − μ)² ÷ (N − 1) and σ²_c = Σ(S − qμ)²
    ÷ m, m = q·K·(1 − q/N) (the 1988 finite-sample corrections; K = the number of q-sums, N = the number of returns).
    Null with fewer than `need` sessions."""
    if len(sessions) < need:
        return None
    rs = [np.asarray(r, float) for r in sessions]
    allr = np.concatenate(rs) if rs else np.zeros(0)
    n = len(allr)
    if n <= q + 1:
        return None
    mu = float(allr.mean())
    va = float(((allr - mu) ** 2).sum()) / (n - 1)
    sums = [np.convolve(r, np.ones(q), mode="valid") for r in rs if len(r) >= q]
    if not sums or va <= 0:
        return None
    s = np.concatenate(sums)
    m = q * len(s) * (1 - q / n)
    return float(((s - q * mu) ** 2).sum() / m / va)


INPUTS = ("minutes_since_open", "ib_width_ratio", "vwap_crosses", "poc_crosses", "rotations", "time_above_vwap_frac",
          "vwap_side_share", "new_highs", "new_lows", "new_extreme_skew", "range_vs_em", "rth_range_pts",
          "er_so_far", "vr_prior")   # plus er_<w> per `er_windows_min` (Q-er challenger candidates)


def eod_from_tape(cs, features: dict) -> str | None:
    """The EOD label of a session (for the first session's `yesterday_day_type` from the warm-up replay)."""
    e = DayType(cs, features, []).eod()
    return None if e is None else e["day_type"]
