"""Multi-expiration context from the stored 09:25 / 10:00 snapshots (step 11 part 2; SPEC §3.1, §5.2, §13 Q-multiexp;
feature_spec F2.23; owner 2026-10-31 Q-step11d (2)).

A snapshot (`clients/skylit_multiexp`, `layout=matrix`: per root a strikes × expirations matrix) is **in force** from its
instant + `mx_snapshot_delay_sec`; each decision reads the latest one in force (09:25 until 10:00 + delay, then 10:00),
flag `mx_snapshot`. Cleaning: expirations before the session date are dropped; a root whose `asOf` is before the
previous session's RTH close − 1 minute (15:59; 12:59 after a 13:00 early close; owner 2026-10-31, Q-step11d
(9)) is null for that snapshot. Buckets by calendar days to expiry: 0DTE (the session date),
1–7, 8–45 (`mx_bucket_days`); later ones are left out. Roots are compared only through signs and shares.
Per side (Nasdaq: QQQ, NDXP, NDX; S&P: SPY, SPXW, SPX):
- n_r = Σ value ÷ Σ |value| over the root's 1–45-day cells; z_r the same over its 0DTE cells (QQQ, NDXP, SPY, SPXW);
- `mx_net_sign_<side>` = sign(mean n_r); `mx_0dte_agree_<side>` = share of the side's 0DTE roots with sign(z_r) equal
  to it; `mx_0dte_share_<side>` = mean of Σ|0DTE| ÷ Σ|all three buckets| (LightGBM day-type challenger list only);
- walls: w_rk = |Σ value at strike k over 1–45 days| ÷ Σ |value| over the root's 1–45-day cells. **Re-chosen at every
  t** (Q-step11d (2)): the strikes of the snapshot in force at t, mapped with the R at t (QQQ: QQQ's; NDXP and NDX:
  NDXP's; SPY: SPY's; SPXW and SPX: SPX's), above / below the price at t and within `mx_wall_reach_em` EM of it; the
  largest w_rk on each side gives `mx_wall_above_dist_em` / `mx_wall_below_dist_em` (|mapped wall − price| in EM);
  null when none qualifies; null while any chain that maps a root of that side is in a dislocation block (F1.8: the
  wall is re-chosen at t, so with a blocked chain the wall can't be known; the caller's `blocked_of`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

import numpy as np

from nqbot.features.session_tape import NS

ET = ZoneInfo("America/New_York")
SIDES = {"nasdaq": ("QQQ", "NDXP", "NDX"), "sp": ("SPY", "SPXW", "SPX")}
ODTE_ROOTS = ("QQQ", "NDXP", "SPY", "SPXW")      # NDX and SPX have no 0DTE (AM-settled monthlies only)
R_MAP = {"QQQ": "QQQ", "NDXP": "NDXP", "NDX": "NDXP", "SPY": "SPY", "SPXW": "SPX", "SPX": "SPX"}
RTH_CLOSE = time(16, 0)
STALE_BEFORE_CLOSE = timedelta(minutes=1)         # F2.23 / Q-step11d (9): stale if stamped before the close − 1 min
TIER_ORDER = ("0925", "1000")


@dataclass
class Root:
    strikes: np.ndarray
    b0: np.ndarray        # Σ value per strike over the 0DTE bucket
    b1: np.ndarray        # 1–7 days
    b2: np.ndarray        # 8–45 days
    n_cells_abs: float    # Σ |value| over the 1–45-day cells
    n: float | None       # n_r
    z: float | None       # z_r
    share0: float | None  # Σ|0DTE| ÷ Σ|all three buckets|
    w: np.ndarray         # w_rk per strike


@dataclass
class Snapshot:
    tier: str
    in_force_ns: int
    roots: dict[str, Root | None]


def parse(doc: dict, tier: str, day: date, prev_session: date, features: dict, prev_close: time | None = None
          ) -> Snapshot:
    """One stored snapshot (`skylit_multiexp.read_snapshot`) for session `day`; `prev_close` = the previous
    session's RTH close (its `early_close_et`, else 16:00)."""
    h, m = int(tier[:2]), int(tier[2:])
    in_force = int(datetime.combine(day, time(h, m), tzinfo=ET).timestamp()) * NS + \
        int(round(float(features["mx_snapshot_delay_sec"]) * NS))
    lo_days, hi_days = (int(x) for x in features["mx_bucket_days"])
    stale_before = datetime.combine(prev_session, prev_close or RTH_CLOSE, tzinfo=ET) - STALE_BEFORE_CLOSE
    roots: dict[str, Root | None] = {}
    for s in doc["body"]["data"]["symbols"]:
        sym = s["symbol"]
        as_of = datetime.fromisoformat(s["asOf"].replace("Z", "+00:00"))
        if as_of < stale_before:
            roots[sym] = None
            continue
        exps = [date.fromisoformat(e) for e in s["expirations"]]
        days = np.array([(e - day).days for e in exps])
        mat = np.asarray(s["matrix"], dtype=np.float64)
        if mat.ndim != 2 or mat.shape[1] != len(exps):
            roots[sym] = None
            continue
        mat = np.nan_to_num(mat)
        strikes = np.array([float(x["strike"]) for x in s["strikes"]])
        c0, c1, c2 = days == 0, (days >= 1) & (days <= lo_days), (days > lo_days) & (days <= hi_days)
        b0, b1, b2 = mat[:, c0].sum(axis=1), mat[:, c1].sum(axis=1), mat[:, c2].sum(axis=1)
        a12 = float(np.abs(mat[:, c1 | c2]).sum())
        a0 = float(np.abs(mat[:, c0]).sum())
        n = float((b1 + b2).sum()) / a12 if a12 > 0 else None
        z = float(b0.sum()) / a0 if a0 > 0 and sym in ODTE_ROOTS else None
        tot = a0 + a12
        share0 = a0 / tot if tot > 0 and sym in ODTE_ROOTS else None
        w = np.abs(b1 + b2) / a12 if a12 > 0 else np.zeros(len(strikes))
        roots[sym] = Root(strikes, b0, b1, b2, a12, n, z, share0, w)
    return Snapshot(tier, in_force, roots)


def _sign(x: float) -> int:
    return 1 if x > 0 else -1 if x < 0 else 0


class MultiExp:
    def __init__(self, snapshots: list[Snapshot]):
        self.snaps = sorted(snapshots, key=lambda s: s.in_force_ns)

    def at(self, t: int) -> Snapshot | None:
        cur = None
        for s in self.snaps:
            if s.in_force_ns <= t:
                cur = s
        return cur

    def features_at(self, t: int, price: dict[str, float | None], em: dict[str, float | None],
                    r_of: Callable[[str, int], float | None], reach_em: float,
                    blocked_of: Callable[[str, int], bool] = lambda m, t: False) -> dict[str, Any]:
        """price / em keyed by side ('nasdaq': NQ, 'sp': ES); r_of(map, t) = the R in force (None: no board yet);
        blocked_of(map, t) = that map's chain is in a dislocation block."""
        snap = self.at(t)
        out: dict[str, Any] = {"mx_snapshot": None if snap is None else snap.tier}
        for side, roots in SIDES.items():
            for k in ("mx_net_sign", "mx_0dte_agree", "mx_0dte_share", "mx_wall_above_dist_em", "mx_wall_below_dist_em"):
                out[f"{k}_{side}"] = None
            if snap is None:
                continue
            rs = [snap.roots.get(r) for r in roots]
            ns = [x.n for x in rs if x is not None and x.n is not None]
            if ns:
                net = _sign(float(np.mean(ns)))
                out[f"mx_net_sign_{side}"] = net
                zs = [x.z for x in rs if x is not None and x.z is not None]
                if zs:
                    out[f"mx_0dte_agree_{side}"] = sum(_sign(z) == net for z in zs) / len(zs)
            shares = [x.share0 for x in rs if x is not None and x.share0 is not None]
            out[f"mx_0dte_share_{side}"] = float(np.mean(shares)) if shares else None
            if not any(blocked_of(R_MAP[r], t) for r in roots):
                out.update(self._walls(snap, side, roots, t, price.get(side), em.get(side), r_of, reach_em))
        return out

    @staticmethod
    def _walls(snap: Snapshot, side: str, roots, t: int, px: float | None, em: float | None, r_of, reach_em: float
               ) -> dict[str, Any]:
        out = {f"mx_wall_above_dist_em_{side}": None, f"mx_wall_below_dist_em_{side}": None}
        if px is None or not em:
            return out
        best = {"above": None, "below": None}   # (w, dist_em, map) of the side's best candidate
        for name in roots:
            root = snap.roots.get(name)
            if root is None:
                continue
            r = r_of(R_MAP[name], t)
            if r is None or not np.isfinite(r):
                continue
            lv = root.strikes * r
            d = (lv - px) / em
            for which, mask in (("above", (d > 0) & (d <= reach_em)), ("below", (d < 0) & (d >= -reach_em))):
                idx = np.flatnonzero(mask & (root.w > 0))
                if not len(idx):
                    continue
                j = idx[np.argmax(root.w[idx])]
                cand = (float(root.w[j]), float(abs(d[j])))
                if best[which] is None or cand[0] > best[which][0]:
                    best[which] = cand
        for which in ("above", "below"):
            if best[which] is not None:
                out[f"mx_wall_{which}_dist_em_{side}"] = best[which][1]
        return out
