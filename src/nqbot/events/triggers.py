"""Style (c) firings: the failed audit / audited hold and the sweep reclaim (SPEC §5.5; feature_spec F6.1, F6.2, F6.4,
F6.5; owner 2026-11-10, SPEC §13 Q-step13b (2): every completion is a decision point, `firing_n` = 1, 2, ...).

For an event touched at t0 with approach side s (short: the zone is above, s = -1; long: below, s = +1):
- a **completion** = the close of a 1-minute bar that closes outside the zone's band (the band itself, as of the bar's
  close) on the approach side (short: close < lo; long: close > hi), after >= 1 trade inside the band since the touch
  (the first firing) or since the previous completion's bar close (later firings). That bar is the trigger bar;
  `decision_ts` = its close (F6.4);
- there is no depth or time limit; a completion counts only before the zone re-arms (a re-arm during the trigger bar
  doesn't cancel it: the bar must open before the re-arm) and before the event's own pair cut `t_pair_end` (Q-step11h
  (1)), which also comes no later than the zone's cross (the audit dies with the zone, F5.4);
- a **sweep reclaim** (F6.2) at a high/low is the same completion (the sweep trades through the band, and the reclaim
  is the first close back on the range side within `sweep_reclaim_bars`; a later close is a break, which crosses the
  level); it is tagged `trigger_kind` = sweep_reclaim when a sweep-eligible member swept since the touch or the previous
  completion, else audit.
Pair-only columns (F6.5, as of `decision_ts`): `audit_depth` (the deepest point into or past the band from its near
edge, pts) and `audit_depth_frac` (÷ the width), `audit_seconds_inside`, `swept`, `sweep_depth`,
`sweep_seconds_beyond`, `fib_level_reached` (golden zones, F10).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from nqbot.events.snapshot import edges_many
from nqbot.features import level_state as ls
from nqbot.features.session_tape import NS


@dataclass
class Firing:
    n: int
    decision_ts: int
    bar_start: int
    bar_open: float
    bar_high: float
    bar_low: float
    bar_close: float
    trigger_kind: str          # audit / sweep_reclaim
    audit_depth: float | None
    audit_depth_frac: float | None
    audit_seconds_inside: float
    swept: bool
    sweep_depth: float | None
    sweep_seconds_beyond: float | None
    fib_level_reached: float | None = None

    def pair_columns(self) -> dict:
        return {"trigger_kind": self.trigger_kind, "trigger_bar_start": self.bar_start, "trigger_bar_open": self.bar_open,
                "trigger_bar_high": self.bar_high, "trigger_bar_low": self.bar_low,
                "trigger_bar_close": self.bar_close, "audit_depth": self.audit_depth,
                "audit_depth_frac": self.audit_depth_frac, "audit_seconds_inside": self.audit_seconds_inside,
                "swept": self.swept, "sweep_depth": self.sweep_depth, "sweep_seconds_beyond": self.sweep_seconds_beyond,
                "fib_level_reached": self.fib_level_reached}


def rearm_time(sf, e) -> int | None:
    """The zone's first arm after the touch (its own id, or the zone that absorbed it once merged)."""
    ids = {e.zone_id}
    if e.kind == "level":
        z = e.zone_id
        for _ in range(64):
            m = sf.book.merged_into.get(z)
            if m is None:
                break
            z = m[1]
            ids.add(z)
    times = [t for zid in ids for t in sf.arms.get(zid, ()) if t > e.t]
    return min(times) if times else None


def style_c(sf, e, t_pair_end: int, fib_levels=None) -> list[Firing]:
    tape, bars = sf.nq, sf.c.cs_nq.bars_1m
    t_rearm = rearm_time(sf, e)
    i0, i1 = int(e.idx), tape.idx_before(int(t_pair_end))
    if i1 <= i0 or not len(bars):
        return []
    ts, p = tape.ts[i0:i1], tape.price[i0:i1]
    lo, hi = edges_many(sf, e, ts)
    with np.errstate(invalid="ignore"):
        inside = (p >= lo) & (p <= hi)
    L = bars.length_ns
    k0 = int(np.searchsorted(bars.start, int(e.t) - int(e.t) % L, side="left"))
    ks = []
    for k in range(k0, len(bars)):
        st = int(bars.start[k])
        if st + L >= t_pair_end:
            break
        if t_rearm is not None and st >= t_rearm:
            break
        ks.append(k)
    if not ks:
        return []
    closes = np.array([int(bars.start[k]) + L for k in ks], dtype=np.int64)
    clo, chi = edges_many(sf, e, closes)
    out: list[Firing] = []
    last = i0 - 1           # trade index (absolute) of the previous completion's last trade
    prev_t = int(e.t)
    for n_k, k in enumerate(ks):
        b_last = int(bars.last[k])
        if b_last < i0:
            continue
        a, z = max(last + 1, i0) - i0, min(b_last, i1 - 1) - i0 + 1
        if z <= a or not inside[a:z].any():
            continue
        c, blo, bhi = float(bars.close[k]), clo[n_k], chi[n_k]
        if np.isnan(blo):
            continue
        done = c < blo if e.direction < 0 else c > bhi
        if not done:
            continue
        dts = int(closes[n_k])
        out.append(_firing(sf, e, len(out) + 1, dts, k, ts, p, inside, lo, hi, prev_t, fib_levels))
        last, prev_t = b_last, dts
    return out


def _firing(sf, e, n, dts, k, ts, p, inside, lo, hi, prev_t, fib_levels) -> Firing:
    bars = sf.c.cs_nq.bars_1m
    j = int(np.searchsorted(ts, dts, side="left"))      # trades of [touch, dts)
    pp = p[:j]
    lo_d, hi_d = lo[j - 1], hi[j - 1]
    width = hi_d - lo_d if np.isfinite(lo_d) else np.nan
    if e.direction < 0:
        depth = float(np.nanmax(pp) - lo_d) if len(pp) and np.isfinite(lo_d) else None
    else:
        depth = float(hi_d - np.nanmin(pp)) if len(pp) and np.isfinite(hi_d) else None
    nxt = np.r_[ts[1:j], dts] if j else np.zeros(0, dtype=np.int64)
    secs = float(((nxt - ts[:j]) * inside[:j]).sum()) / NS if j else 0.0
    swept, s_depth, s_sec, recent = False, None, None, False
    for m in sf.members_at(e, dts):
        if m.kind != ls.SWEEP:
            continue
        st = m.state.at(dts)
        t_sw = st.get("last_sweep_t")
        if t_sw is None or t_sw <= e.t:
            continue
        swept = True
        s_depth = max(s_depth or 0.0, float(st.get("sweep_depth") or 0.0))
        s_sec = max(s_sec or 0.0, float(st.get("sweep_seconds_beyond") or 0.0))
        recent = recent or t_sw > prev_t
    fib = None
    if e.kind == "golden" and fib_levels is not None:
        fib = sf.c.cs_nq.opening.fib_level_reached(int(e.t), dts, sf.nq, fib_levels)
    return Firing(n, dts, int(bars.start[k]), float(bars.open[k]), float(bars.high[k]), float(bars.low[k]),
                  float(bars.close[k]), "sweep_reclaim" if recent else "audit", depth,
                  (depth / width) if depth is not None and width and np.isfinite(width) and width > 0 else None,
                  secs, swept, s_depth, s_sec, fib)

