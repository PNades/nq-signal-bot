"""Board ingest and the R in force per map (step 11 part 2; SPEC §3.1 Ingest, §5.1; feature_spec F0.1, F1.1-F1.3, F1.7).

One function for history and live (rule 4): `MapBoards.build` takes one map's boards of one session (asOf, spot,
resolution, per-strike values; live also the arrival times) and the mapped futures' trades, and returns the
**applied** boards in apply-time order with
- τ = floor(`asOf`) + 1 s (+ 60 s for an uncleared 1-minute board) and `t_apply` = τ + `map_latency_sec` (live:
  max(arrival, that)), made non-decreasing (F0.1, `mapping.apply_times_ns`);
- the board's content time c = floor(`asOf`) + `board_content_offset_ms`[era][map] and its own ratio r = F(c) ÷ spot,
  F = the last trade of the mapped futures at or before c (F1.1);
- the R in force after it (F1.7, `mapping.ratio_in_force`: QQQ / SPY trailing 60-s median, SPX per board with F1.2).
Applied = `asOf` from 09:30 (SPX / NDXP from 09:30 + `ratio_open_skip_sec`, F1.3), before the RTH close, spot > 0 and
a futures price at c. The step-8b gate's convention: apply times over the applied boards only.

**NDXP** (owner 2026-10-31, SPEC §13 Q-step11d (4)-(5)): its R = the QQQ R in force at the NDXP board's own `t_apply`
÷ k (QQQ × k, N = `ndxp_k_sessions`, `ndxp.qqq_k`), and its spot for every per-board rule = that QQQ board's spot × k
(history's NDXP `spot` is an NQ futures price, Q8b-1); no QQQ board applied within min(`skylit_map_stale_sec`,
`ratio_gap_reset_sec`) of it → the NDXP board is not applied. k comes from the stored free closes
(`<raw>/ndx/<fetch date>/`, `clients/ndx_sources`); nothing is fetched here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from nqbot.clients import ndx_sources
from nqbot.features import mapping as mp
from nqbot.features import ndxp

NS = mp.NS
CHAIN_ROOT = {"QQQ": "NQ", "NDXP": "NQ", "SPY": "ES", "SPX": "ES"}
MAPS = ("QQQ", "NDXP", "SPY", "SPX")


class BoardsRefused(ValueError):
    """A map can't be built as the frozen rule says (a missing offset or window, no k for NDXP)."""


@dataclass
class MapBoards:
    """One map's applied boards of one session, in apply-time order, with their strikes (sorted per board)."""

    name: str
    day: date
    as_of: np.ndarray        # ns
    tau: np.ndarray          # ns (F0.1 effective time)
    t_apply: np.ndarray      # ns, non-decreasing
    spot: np.ndarray         # in the underlying's units (NDXP: QQQ spot × k)
    r_board: np.ndarray      # the board's own ratio (NaN for NDXP)
    r: np.ndarray            # the R in force after the board
    one_minute: np.ndarray   # bool: a 1-minute-resolution board (`map_resolution_1m`)
    offsets: np.ndarray      # board i's strikes are rows offsets[i]:offsets[i + 1]
    s_strike: np.ndarray
    s_value: np.ndarray
    rejected: int = 0        # F1.2 rejections (SPX)
    note: str = ""

    def __len__(self) -> int:
        return len(self.t_apply)

    @property
    def s_board(self) -> np.ndarray:
        return np.repeat(np.arange(len(self)), np.diff(self.offsets))

    def strikes(self, i: int) -> tuple[np.ndarray, np.ndarray]:
        a, b = int(self.offsets[i]), int(self.offsets[i + 1])
        return self.s_strike[a:b], self.s_value[a:b]

    def index_at(self, t_ns: int) -> int:
        """The last board applied at or before t (-1: none yet)."""
        return int(np.searchsorted(self.t_apply, int(t_ns), side="right")) - 1

    @classmethod
    def empty(cls, name: str, day: date, note: str = "") -> MapBoards:
        z = np.zeros(0, dtype=np.int64)
        f = np.zeros(0, dtype=np.float64)
        return cls(name, day, z, z, z, f, f, f, np.zeros(0, dtype=bool), np.zeros(1, dtype=np.int64), f, f, 0, note)

    @classmethod
    def build(cls, name: str, day: date, as_of: np.ndarray, spot: np.ndarray, resolution, s_as_of: np.ndarray,
              s_strike: np.ndarray, s_value: np.ndarray, fut_ts: np.ndarray | None, fut_px: np.ndarray | None,
              features: dict, settings: dict, open_ns: int, close_ns: int, *, live: bool = False,
              arrival_ns: np.ndarray | None = None, qqq: MapBoards | None = None, k: float | None = None
              ) -> MapBoards:
        cfg = features["mapping"]
        as_of = np.asarray(as_of, dtype=np.int64)
        order = np.argsort(as_of, kind="stable")
        as_of = as_of[order]
        spot = np.asarray(spot, dtype=np.float64)[order]
        res = np.asarray(resolution, dtype=object)
        res = np.full(len(as_of), res.item(), dtype=object) if res.ndim == 0 else res[order]
        arr = None if arrival_ns is None else np.asarray(arrival_ns, dtype=np.int64)[order]
        ok = (mp.open_skip_mask(as_of, name, open_ns, cfg["ratio_open_skip_sec"]) & (as_of < close_ns))
        latency = settings.get("map_latency_sec")
        if name == "NDXP":
            if qqq is None or k is None or len(qqq) == 0:
                return cls.empty(name, day, "NDXP: no QQQ boards or no k (QQQ × k) for this session")
            A = np.flatnonzero(ok)
            t_apply = mp.apply_times_ns(as_of[A], res[A], latency, None if arr is None else arr[A])
            no_carry = int(min(float(settings["skylit_map_stale_sec"]), float(cfg["ratio_gap_reset_sec"])) * NS)
            j = mp.in_force_index(qqq.t_apply, t_apply)
            jj = np.clip(j, 0, None)
            keep = (j >= 0) & (t_apply - qqq.t_apply[jj] <= no_carry) & np.isfinite(qqq.r[jj])
            A, t_apply, jj = A[keep], t_apply[keep], jj[keep]
            r = qqq.r[jj] / k
            spot_u = qqq.spot[jj] * k
            r_board = np.full(len(A), np.nan)
            tau = mp.effective_tau_ns(as_of[A], res[A])
            rejected = 0
        else:
            era = mp.ERA_LIVE if live else mp.board_era(day, cfg["skylit_stamp_change_date"])
            off, window = mp.content_offset_ms(cfg, name, era), cfg["ratio_window_sec"].get(name)
            if off is None or window is None:
                raise BoardsRefused(f"{name}: board_content_offset_ms[{era}] or ratio_window_sec is not set")
            tc = mp.content_time_ns(as_of, off)
            with np.errstate(divide="ignore", invalid="ignore"):
                f = mp.last_price_at_or_before(fut_ts, fut_px, tc) if fut_ts is not None and len(fut_ts) else \
                    np.full(len(as_of), np.nan)
                rb = f / spot
            ok &= np.isfinite(rb) & (spot > 0)
            A = np.flatnonzero(ok)
            t_apply = mp.apply_times_ns(as_of[A], res[A], latency, None if arr is None else arr[A])
            r_board = rb[A]
            r = mp.ratio_in_force(tc[A], r_board, window, cfg["ratio_gap_reset_sec"], cfg["ratio_outlier_bps"],
                                  cfg["ratio_reaccept_n"])
            rejected = (int(mp.accept_ratios(r_board, cfg["ratio_outlier_bps"], cfg["ratio_reaccept_n"])[1])
                        if window == 0 and len(A) else 0)
            spot_u = spot[A]
            tau = mp.effective_tau_ns(as_of[A], res[A])
        # strikes of the applied boards, joined on asOf, sorted (board, strike)
        s_as_of = np.asarray(s_as_of, dtype=np.int64)
        kept = as_of[A]
        if len(kept) and len(s_as_of):
            idx = np.clip(np.searchsorted(kept, s_as_of), 0, len(kept) - 1)
            hit = kept[idx] == s_as_of
        else:
            idx, hit = np.zeros(len(s_as_of), dtype=np.int64), np.zeros(len(s_as_of), dtype=bool)
        b = idx[hit]
        sk = np.asarray(s_strike, dtype=np.float64)[hit]
        sv = np.asarray(s_value, dtype=np.float64)[hit]
        o = np.lexsort((sk, b))
        b, sk, sv = b[o], sk[o], sv[o]
        offsets = np.searchsorted(b, np.arange(len(A) + 1)).astype(np.int64)
        one_min = np.array([x == "1m" for x in res[A]], dtype=bool)
        return cls(name, day, kept, tau, t_apply, spot_u, r_board, np.asarray(r, dtype=np.float64), one_min, offsets,
                   sk, sv, rejected)


def stored_closes(raw_dir: Path) -> dict[str, Any] | None:
    """The latest stored free-closes fetch (`<raw>/ndx/<YYYY-MM-DD>/`), parsed; None if none is stored. Read only."""
    base = Path(raw_dir) / "ndx"
    if not base.exists():
        return None
    folders = []
    for p in base.iterdir():
        try:
            folders.append((date.fromisoformat(p.name), p))
        except ValueError:
            continue
    for _, p in sorted(folders, reverse=True):
        files = {k: p / v for k, v in ndx_sources.FILES.items()}
        if all(f.exists() for f in files.values()):
            return {"ndx": ndx_sources.parse_fred(files["fred"].read_bytes()),
                    "qqq": ndx_sources.parse_qqq_closes(files["qqq"].read_bytes()),
                    "div": ndx_sources.parse_qqq_dividends(files["div"].read_bytes()), "folder": str(p)}
    return None


def ndxp_k(day: date, closes: dict[str, Any] | None, features: dict) -> float | None:
    """k for `day` (known before its open) by the frozen method (`ndxp_r_method` qqq_k, `ndxp_k_sessions`)."""
    cfg = features["mapping"]
    if cfg.get("ndxp_r_method") != "qqq_k":
        raise BoardsRefused(f"ndxp_r_method {cfg.get('ndxp_r_method')!r}: only qqq_k is built (frozen 2026-10-19)")
    if closes is None:
        return None
    return ndxp.qqq_k(day, closes["ndx"], closes["qqq"], closes["div"], int(cfg["ndxp_k_sessions"]))


def from_tables(name: str, day: date, boards, strikes, fut_ts, fut_px, features: dict, settings: dict, open_ns: int,
                close_ns: int, **kw) -> MapBoards:
    """`MapBoards.build` from the stored pyarrow tables (`SkylitStore.read_range`), filtered to the map."""
    import pyarrow as pa
    import pyarrow.compute as pc

    def ns(col):
        return pc.cast(col, pa.timestamp("ns", "UTC")).cast(pa.int64()).to_numpy()

    b = boards.filter(pc.equal(boards["symbol"], name))
    s = strikes.filter(pc.equal(strikes["symbol"], name))
    if b.num_rows == 0:
        return MapBoards.empty(name, day, f"no {name} boards stored")
    return MapBoards.build(name, day, ns(b["as_of"]), b["spot"].to_numpy(), np.array(b["resolution"].to_pylist(), dtype=object),
                           ns(s["as_of"]), s["strike"].to_numpy(), s["value"].to_numpy(), fut_ts, fut_px, features,
                           settings, open_ns, close_ns, **kw)
