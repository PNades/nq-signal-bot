"""F3.2 volume profile and POC, F3.3 value area and VAH/VAL zones, F3.4 LVNs (SPEC §5.3; feature_spec F3.2-F3.4).

- Volume per 0.25 price (side N trades count, F0.1). **Daily** profile from 18:00 ET of the CME day; **weekly** from
  Sunday 18:00 ET, one contract only: in a roll week only the new contract's days, from the roll day (owner
  2026-10-24). Both develop; they are recomputed at clock-aligned instants every `profile_update_sec` counted from the
  anchor, from trades with ts_event <= the instant, and are known at that instant (F0.1). Between instants the last
  instant's values hold.
- **POC** = the price with the largest volume; ties go to the price nearest the midpoint of the profile's high-low
  range, then to the price nearer the volume median (the price with half the volume at or below it), then the lower
  price (owner 2026-10-05).
- **Value area** = from the POC outward until >= 70% of the volume is included, one tick at a time: add whichever of the
  next price above / below has the larger volume; on a tie add both; once one side reaches the profile edge keep adding
  the other (owner 2026-10-05, FQ-08, §13 Q40). VAH / VAL = its highest / lowest price. **VAH zone** = [VAH, high],
  **VAL zone** = [low, VAL]: context and order-flow areas, not levels.
- **LVN** = a local minimum m of the Gaussian-smoothed profile (σ = `lvn_smooth_ticks` / 2 ticks, truncated at
  ±`lvn_gauss_trunc_sigma` σ) with s(m) <= `lvn_max_pct` × min(peak_left, peak_right), each peak = the highest smoothed
  volume between m and the next LVN (or the profile edge) on that side; found by iteration (drop every failing
  candidate, recompute, repeat). Identity = (profile, round(price / `lvn_merge_pts`) × `lvn_merge_pts`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from nqbot.features.session_tape import NS, TICK, Tape

VALUE_AREA_SHARE = 0.70   # owner's fixed definitional constant (feature_spec "Values"; F3.3)


def to_tick(price) -> np.ndarray:
    return np.rint(np.asarray(price, dtype=np.float64) / TICK).astype(np.int64)


# --- one profile (dense volume over [lo_tick, lo_tick + len)) --------------------------------------------------

def poc_index(vol: np.ndarray) -> int:
    """F3.2 POC with the owner's tie rules; `vol` dense over the profile's high-low range (index 0 = the low)."""
    vmax = vol.max()
    cand = np.flatnonzero(vol == vmax)
    if len(cand) == 1:
        return int(cand[0])
    mid = (len(vol) - 1) / 2.0
    d = np.abs(cand - mid)
    cand = cand[d == d.min()]
    if len(cand) == 1:
        return int(cand[0])
    cum = np.cumsum(vol)
    median = int(np.searchsorted(cum, cum[-1] / 2.0, side="left"))   # half the volume at or below it
    d = np.abs(cand - median)
    cand = cand[d == d.min()]
    return int(cand.min())


def value_area(vol: np.ndarray, poc: int, share: float = VALUE_AREA_SHARE) -> tuple[int, int]:
    """(VAL index, VAH index): expanded from the POC one tick at a time (F3.3)."""
    total = float(vol.sum())
    lo = hi = poc
    inc = float(vol[poc])
    n = len(vol)
    target = share * total
    while inc < target and (lo > 0 or hi < n - 1):
        up = vol[hi + 1] if hi < n - 1 else None
        dn = vol[lo - 1] if lo > 0 else None
        if up is not None and (dn is None or up > dn):
            hi += 1
            inc += up
        elif dn is not None and (up is None or dn > up):
            lo -= 1
            inc += dn
        else:   # tie: add both
            hi += 1
            lo -= 1
            inc += up + dn
    return lo, hi


def gaussian_kernel(smooth_ticks: float, trunc_sigma: float) -> np.ndarray:
    sigma = smooth_ticks / 2.0
    r = int(np.floor(trunc_sigma * sigma))
    x = np.arange(-r, r + 1, dtype=float)
    k = np.exp(-0.5 * (x / sigma) ** 2)
    return k / k.sum()


def smooth(vol: np.ndarray, smooth_ticks: float, trunc_sigma: float) -> np.ndarray:
    """Truncated Gaussian smoothing; zero volume outside the profile's range."""
    return np.convolve(vol, gaussian_kernel(smooth_ticks, trunc_sigma), mode="same")


def lvn_indices(s: np.ndarray, max_pct: float) -> list[int]:
    """F3.4 qualifying LVNs of a smoothed profile, by iteration. Interior local minima only (an edge minimum has no
    peak on one side); a plateau counts once, at its left end."""
    n = len(s)
    cand = [m for m in range(1, n - 1)
            if s[m] < s[m - 1] and (s[m] < s[m + 1] or (s[m] == s[m + 1] and _plateau_rises(s, m)))]
    while cand:
        keep = []
        for j, m in enumerate(cand):
            left_from = cand[j - 1] + 1 if j > 0 else 0
            right_to = cand[j + 1] if j + 1 < len(cand) else n
            pl = s[left_from:m].max() if m > left_from else 0.0
            pr = s[m + 1:right_to].max() if right_to > m + 1 else 0.0
            if s[m] <= max_pct * min(pl, pr):
                keep.append(m)
        if len(keep) == len(cand):
            break
        cand = keep
    return cand


def _plateau_rises(s: np.ndarray, m: int) -> bool:
    j = m
    while j + 1 < len(s) and s[j + 1] == s[m]:
        j += 1
    return j + 1 < len(s) and s[j + 1] > s[m]


@dataclass(frozen=True)
class ProfileStats:
    t_ns: int           # the profile instant
    low: float          # profile low / high (min / max traded price)
    high: float
    poc: float
    val: float
    vah: float
    lvns: tuple         # ((identity, price), ...) qualifying at this instant
    volume: float


def stats(vol: np.ndarray, lo_tick: int, t_ns: int, lvn_params: dict | None) -> ProfileStats:
    p = poc_index(vol)
    a, b = value_area(vol, p)
    lvns: tuple = ()
    if lvn_params is not None:
        s = smooth(vol, lvn_params["lvn_smooth_ticks"], lvn_params["lvn_gauss_trunc_sigma"])
        found: dict[float, tuple[float, float]] = {}
        merge = lvn_params["lvn_merge_pts"]
        for m in lvn_indices(s, lvn_params["lvn_max_pct"]):
            price = (lo_tick + m) * TICK
            ident = round(price / merge) * merge
            if ident not in found or s[m] < found[ident][1]:   # two minima on one identity: the deeper one
                found[ident] = (price, s[m])
        lvns = tuple(sorted((k, v[0]) for k, v in found.items()))
    return ProfileStats(t_ns, lo_tick * TICK, (lo_tick + len(vol) - 1) * TICK, (lo_tick + p) * TICK,
                        (lo_tick + a) * TICK, (lo_tick + b) * TICK, lvns, float(vol.sum()))


# --- a developing profile over one session -------------------------------------------------------------------

@dataclass
class DevelopingProfile:
    """The profile's stats at every instant of the session (anchor-aligned), known at the instant."""
    name: str                       # 'daily' | 'weekly'
    times: np.ndarray               # int64 instants with a non-empty profile
    snaps: list = field(default_factory=list)   # ProfileStats per instant
    end_volume: dict = field(default_factory=dict)  # tick -> volume at the last instant (the weekly carry)

    def at(self, t_ns: int) -> ProfileStats | None:
        k = int(np.searchsorted(self.times, t_ns, side="right")) - 1
        return self.snaps[k] if k >= 0 else None

    @classmethod
    def build(cls, name: str, tape: Tape, anchor_ns: int, first_ns: int, end_ns: int, update_sec: int,
              lvn_params: dict | None, carried: dict | None = None) -> DevelopingProfile:
        """Instants anchor + k·update_sec in [first_ns, end_ns]; `carried` = {tick: volume} from earlier days (weekly)."""
        step = update_sec * NS
        k0 = -(-(first_ns - anchor_ns) // step)
        instants = anchor_ns + step * np.arange(max(k0, 0), (end_ns - anchor_ns) // step + 1, dtype=np.int64)
        i_from = tape.idx_before(anchor_ns)
        ticks = to_tick(tape.price)
        keys = list((carried or {}).keys())
        lows = ([int(ticks[i_from:].min())] if len(ticks) > i_from else []) + ([min(keys)] if keys else [])
        highs = ([int(ticks[i_from:].max())] if len(ticks) > i_from else []) + ([max(keys)] if keys else [])
        lo_all, hi_all = min(lows, default=None), max(highs, default=None)
        out = cls(name, np.zeros(0, dtype=np.int64))
        if lo_all is None:
            return out
        vol = np.zeros(hi_all - lo_all + 1)
        cur_lo = cur_hi = None
        if carried:
            for tk, v in carried.items():
                vol[tk - lo_all] += v
            cur_lo, cur_hi = min(keys) - lo_all, max(keys) - lo_all
        times, snaps = [], []
        i = i_from
        for t in instants:
            j = tape.idx_at(int(t))
            if j > i:
                idx = ticks[i:j] - lo_all
                np.add.at(vol, idx, tape.size[i:j])
                a, b = int(idx.min()), int(idx.max())
                cur_lo = a if cur_lo is None else min(cur_lo, a)
                cur_hi = b if cur_hi is None else max(cur_hi, b)
                i = j
            if cur_lo is None:
                continue
            times.append(int(t))
            snaps.append(stats(vol[cur_lo:cur_hi + 1], lo_all + cur_lo, int(t), lvn_params))
        out.times = np.asarray(times, dtype=np.int64)
        out.snaps = snaps
        if cur_lo is not None:
            nz = np.flatnonzero(vol)
            out.end_volume = {int(lo_all + k): float(vol[k]) for k in nz}
        return out


# --- VAH / VAL zone features (F3.3) ----------------------------------------------------------------------------

def va_features(st: ProfileStats | None, price: float, direction: int | None = None) -> dict:
    """`in_vah_zone`, `in_val_zone`, `va_depth` (0 at the line, 1 at the extreme), signed distances to the VAH and VAL
    lines (pts), `va_direction_fit` (short in the VAH zone or long in the VAL zone; None without a direction)."""
    if st is None:
        return {"in_vah_zone": None, "in_val_zone": None, "va_depth": None, "dist_vah": None, "dist_val": None,
                "va_direction_fit": None}
    in_vah = st.vah <= price <= st.high
    in_val = st.low <= price <= st.val
    depth = None
    if in_vah:
        depth = (price - st.vah) / (st.high - st.vah) if st.high > st.vah else 0.0
    elif in_val:
        depth = (st.val - price) / (st.val - st.low) if st.val > st.low else 0.0
    fit = None if direction is None else bool((direction < 0 and in_vah) or (direction > 0 and in_val))
    return {"in_vah_zone": in_vah, "in_val_zone": in_val, "va_depth": depth, "dist_vah": price - st.vah,
            "dist_val": price - st.val, "va_direction_fit": fit}
