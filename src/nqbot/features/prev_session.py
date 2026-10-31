"""F3.2b previous-session profile levels and F3.12 previous-session value context (owner 2026-10-24, SPEC §5.3,
§13 Q-amt; the `prev_profile_anchor` variant, owner 2026-10-26, Q-vaedge).

- The previous session's completed profile (its own front contract, F0.4), with the F3.2 POC and F3.3 value-area
  rules, from the window `prev_profile.profile_window` gives (`globex_1800_1700` or `rth_0930_1600`).
- Levels (registry sub-types): `prev_poc` (key `poc`), `prev_vah` and `prev_val` (key `va_edge`), chart levels for the
  session (sweep-eligible only with `va_edge_sweep`), roll-shifted on a roll day (`carry.py`).
- `prev_poc_naked` = no trade at the POC price since the profile completed (17:00, or the RTH close with the RTH
  anchor: the trades from then to 17:00 count): false from the first trade there.
- F3.12 (structure model and pair scorer): `open_vs_prev_va`, `dist_prev_poc_em` / `_vah_em` / `_val_em`,
  `prev_va_overlap`, `dpoc_drift_em`, `poc_migration_em` (signed to the trade); the day-type challenger list adds
  `open_outside_prev_va_em` and |`poc_migration_em`|.
"""

from __future__ import annotations

from datetime import time
from typing import Any

import numpy as np

from nqbot.features import prev_profile as pp
from nqbot.features import profile as pf
from nqbot.features.session_tape import NS, TICK, Tape

ABOVE, INSIDE, BELOW = "above", "inside", "below"


def profile_from_trades(tape: Tape, anchor: str, early_close: time | None = None) -> dict[str, Any] | None:
    """The completed profile of `tape`'s session in the anchor's window: volume by tick and its POC / VAH / VAL."""
    a, b = pp.profile_window(tape.day, anchor, early_close)
    sl = tape.window(int(a.timestamp()) * NS, int(b.timestamp()) * NS)
    return profile_from_volume(_ticks(tape.price[sl], tape.size[sl]))


def _ticks(price: np.ndarray, size: np.ndarray) -> dict[str, float]:
    t = pf.to_tick(price)
    out: dict[int, float] = {}
    for k, v in zip(t.tolist(), size.tolist()):
        out[k] = out.get(k, 0.0) + v
    return {str(k): v for k, v in out.items()}


def profile_from_volume(volume: dict[str, float]) -> dict[str, Any] | None:
    if not volume:
        return None
    keys = sorted(int(k) for k in volume)
    lo, hi = keys[0], keys[-1]
    dense = np.zeros(hi - lo + 1)
    for k, v in volume.items():
        dense[int(k) - lo] = v
    s = pf.stats(dense, lo, 0, None)
    return {"volume": volume, "poc": s.poc, "vah": s.vah, "val": s.val, "high": s.high, "low": s.low}


def naked_after(prof: dict[str, Any], prices_since_completion: np.ndarray) -> bool:
    """`prev_poc_naked` over the trades from the profile's completion: no trade at the POC price."""
    return not bool(np.any(np.abs(prices_since_completion - prof["poc"]) < TICK / 2))


def naked_at(tape: Tape, prof: dict[str, Any], naked_at_start: bool, t_ns: int) -> bool:
    """The flag as of t: the carried value at the session start, false from the first trade at the POC today."""
    if not naked_at_start:
        return False
    n = tape.idx_at(t_ns)
    return not bool(np.any(np.abs(tape.price[:n] - prof["poc"]) < TICK / 2))


def open_vs_prev_va(open_price: float | None, prof: dict | None) -> str | None:
    if open_price is None or prof is None:
        return None
    return ABOVE if open_price > prof["vah"] else BELOW if open_price < prof["val"] else INSIDE


def open_outside_prev_va_em(open_price: float | None, prof: dict | None, em: float | None) -> float | None:
    if open_price is None or prof is None or not em:
        return None
    return max(open_price - prof["vah"], prof["val"] - open_price, 0.0) / em


def va_overlap(today_val: float, today_vah: float, prev_val: float, prev_vah: float) -> float | None:
    """`prev_va_overlap`: overlap of the two value areas ÷ the narrower width, in [0, 1]."""
    narrower = min(today_vah - today_val, prev_vah - prev_val)
    if narrower <= 0:
        return None
    return max(0.0, min(today_vah, prev_vah) - max(today_val, prev_val)) / narrower


def context(price: float, prof: dict | None, em: float | None, dev_now: pf.ProfileStats | None,
            dev_first: pf.ProfileStats | None, direction: int | None = None) -> dict[str, Any]:
    """F3.12 at t (the developing daily profile's stats at the latest instant <= t, and at the first instant
    >= 09:30)."""
    out: dict[str, Any] = {"dist_prev_poc_em": None, "dist_prev_vah_em": None, "dist_prev_val_em": None,
                           "prev_va_overlap": None, "dpoc_drift_em": None, "poc_migration_em": None,
                           "poc_migration_em_abs": None}
    if prof is not None and em:
        out.update(dist_prev_poc_em=(price - prof["poc"]) / em, dist_prev_vah_em=(price - prof["vah"]) / em,
                   dist_prev_val_em=(price - prof["val"]) / em)
        if dev_now is not None:
            mig = (dev_now.poc - prof["poc"]) / em
            out["poc_migration_em_abs"] = abs(mig)
            out["poc_migration_em"] = None if direction is None else mig * direction
    if prof is not None and dev_now is not None:
        out["prev_va_overlap"] = va_overlap(dev_now.val, dev_now.vah, prof["val"], prof["vah"])
    if em and dev_now is not None and dev_first is not None:
        out["dpoc_drift_em"] = (dev_now.poc - dev_first.poc) / em
    return out
