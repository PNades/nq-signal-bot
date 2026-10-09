"""Strike -> futures mapping ratio R = F / S (SPEC §5.1, feature_spec F1). Step 7 started it; the step-8b gate adds
outlier rejection and re-acceptance (F1.2), the open skip (F1.3), % of King (F2.3), the precision metric (F1.6), the
spot-lag cross-correlation and the trailing realized vol used to split the 8b report. Step 11 adds band conversion
and rounding.

S is the board's own `spot`; F is the last NQ/ES trade at or before the board's effective time τ:
τ = asOf for 1-second boards, asOf + 60 s for 1-minute boards until the spot-lag test clears them (§3.1 Ingest).
Timestamps are integer nanoseconds since the epoch (UTC), so "at or before" is exact.
"""

from __future__ import annotations

import numpy as np

NS = 1_000_000_000
ONE_MINUTE_TAU_SHIFT_NS = 60 * NS  # §3.1 Ingest: uncleared 1-minute boards hold the minute's end state
RATIO_MEDIAN_N = 5  # §5.1 / F1.2: median of the last 5 accepted R
OPEN_SKIP_MAPS = ("SPX", "NDXP")  # F1.3: index maps skip the opening seconds
SPOT_LAG_MAX_S = 120  # §12 step 8b: cross-correlate over +-120 s
RV_WINDOW_MIN = 15  # step-8b volatility split: trailing 15 minutes of 1-minute returns (owner 2026-10-05)


def board_tau_ns(as_of_ns: np.ndarray, resolution: np.ndarray | str, *, spot_lag_cleared: bool = False) -> np.ndarray:
    """Each board's effective time τ (ns)."""
    as_of_ns = np.asarray(as_of_ns, dtype=np.int64)
    res = np.broadcast_to(np.asarray(resolution), as_of_ns.shape)
    shift = np.where((res == "1m") & (not spot_lag_cleared), ONE_MINUTE_TAU_SHIFT_NS, 0)
    return as_of_ns + shift


def last_price_at_or_before(trade_ts_ns: np.ndarray, trade_px: np.ndarray, at_ns: np.ndarray) -> np.ndarray:
    """The last trade price with timestamp <= at (NaN before the first trade). trade_ts_ns must be sorted."""
    ts = np.asarray(trade_ts_ns, dtype=np.int64)
    if len(ts) and np.any(np.diff(ts) < 0):
        raise ValueError("trade timestamps must be sorted")
    idx = np.searchsorted(ts, np.asarray(at_ns, dtype=np.int64), side="right") - 1
    px = np.asarray(trade_px, dtype=np.float64)
    out = np.full(idx.shape, np.nan)
    ok = idx >= 0
    out[ok] = px[idx[ok]]
    return out


def ratio_for_boards(as_of_ns: np.ndarray, spot: np.ndarray, resolution: np.ndarray | str,
                     trade_ts_ns: np.ndarray, trade_px: np.ndarray, *, spot_lag_cleared: bool = False) -> np.ndarray:
    """R = F(τ) / spot per board (NaN when no trade precedes τ)."""
    tau = board_tau_ns(as_of_ns, resolution, spot_lag_cleared=spot_lag_cleared)
    return last_price_at_or_before(trade_ts_ns, trade_px, tau) / np.asarray(spot, dtype=np.float64)


def accept_ratios(r: np.ndarray, outlier_bps: float, reaccept_n: int) -> tuple[np.ndarray, int, bool]:
    """F1.2 for one map's boards of one session, in order: (accepted mask, rejected count, ratio_shift_day).

    A NaN R (no futures price at τ) is neither accepted nor rejected. A new R is rejected if it is more than
    outlier_bps from the median of the last RATIO_MEDIAN_N accepted values (the first finite R is always accepted).
    If reaccept_n rejected updates in a row lie within outlier_bps of each other (relative to their own median),
    the latest is accepted, the history resets to it and the day is flagged."""
    thr = outlier_bps * 1e-4
    r = np.asarray(r, dtype=np.float64)
    accepted = np.zeros(len(r), dtype=bool)
    hist: list[float] = []
    run: list[float] = []
    rejected, shifted = 0, False
    for i, x in enumerate(r):
        if not np.isfinite(x):
            continue
        if hist:
            med = float(np.median(hist[-RATIO_MEDIAN_N:]))
            if abs(x - med) / med > thr:
                run.append(x)
                last = run[-reaccept_n:]
                if len(last) < reaccept_n or (max(last) - min(last)) / float(np.median(last)) > thr:
                    rejected += 1
                    continue
                hist, run, shifted = [], [], True
        accepted[i] = True
        hist.append(x)
        run = []
    return accepted, rejected, shifted


def open_skip_mask(as_of_ns: np.ndarray, map_name: str, open_ns: int, skip_sec: float) -> np.ndarray:
    """Boards that may be applied: asOf >= 09:30:00 (F0.1), and for SPX/NDXP >= 09:30:00 + skip_sec (F1.3)."""
    as_of_ns = np.asarray(as_of_ns, dtype=np.int64)
    first = open_ns + (int(skip_sec * NS) if map_name in OPEN_SKIP_MAPS else 0)
    return as_of_ns >= first


def board_kings(board_idx: np.ndarray, values: np.ndarray, n_boards: int) -> np.ndarray:
    """|value| of each board's King (F2.3: the largest |value| on the board; NaN for a board without strikes).
    board_idx: each strike row's board number (0..n_boards-1)."""
    king = np.full(n_boards, np.nan)
    if len(values):
        np.fmax.at(king, np.asarray(board_idx), np.abs(np.asarray(values, dtype=np.float64)))
    return king


def pct_of_king(board_idx: np.ndarray, values: np.ndarray, n_boards: int) -> np.ndarray:
    """F2.3 pct_of_king for every strike row (|value| / |King value| of its own board)."""
    king = board_kings(board_idx, values, n_boards)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.abs(np.asarray(values, dtype=np.float64)) / king[np.asarray(board_idx)]


def prev_accepted(accepted: np.ndarray) -> np.ndarray:
    """For each board, the index of the last accepted board before it (-1 if none): the R in force just before."""
    idx = np.where(accepted, np.arange(len(accepted)), -1)
    last = np.maximum.accumulate(np.concatenate([[-1], idx[:-1]])) if len(idx) else idx
    return last


def step_errors(r: np.ndarray, accepted: np.ndarray, applied: np.ndarray, board_idx: np.ndarray,
                strikes: np.ndarray, relevant: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """F1.6: at each applied board with an R in force before it, |k*R_prev - k*R_cur| for each relevant node, where
    R_cur is the board's own R if accepted, else (a rejected update, F1.2) the R in force, so its error is 0.
    Returns (errors, their board numbers)."""
    r = np.asarray(r, dtype=np.float64)
    prev = prev_accepted(accepted)
    r_prev = np.where(prev >= 0, r[np.clip(prev, 0, None)], np.nan) if len(r) else r
    r_cur = np.where(accepted, r, r_prev)
    b = np.asarray(board_idx)
    ok = np.asarray(relevant, dtype=bool) & np.asarray(applied, dtype=bool)[b] & (prev[b] >= 0)
    rows = b[ok]
    err = np.abs(np.asarray(strikes, dtype=np.float64)[ok]) * np.abs(r_prev[rows] - r_cur[rows])
    return err, rows


def last_on_grid(ts_ns: np.ndarray, values: np.ndarray, grid_ns: np.ndarray) -> np.ndarray:
    """The last value at or before each grid instant (NaN before the first). ts_ns sorted."""
    return last_price_at_or_before(ts_ns, values, grid_ns)


def spot_lag(spot_ts_ns: np.ndarray, spot: np.ndarray, fut_ts_ns: np.ndarray, fut_px: np.ndarray,
             start_ns: int, end_ns: int, max_lag_s: int = SPOT_LAG_MAX_S) -> dict[str, float | int | None]:
    """§12 step 8b: cross-correlate 1-second changes of the board `spot` with 1-second changes of the futures price
    over lags -max_lag_s..+max_lag_s. A positive peak lag L means spot moves L seconds after the futures (spot lags);
    0-1 s passes on 1-second boards."""
    grid = np.arange(start_ns, end_ns + 1, NS, dtype=np.int64)
    ds = np.diff(last_on_grid(spot_ts_ns, spot, grid))
    df = np.diff(last_on_grid(fut_ts_ns, fut_px, grid))
    best: tuple[float, int] | None = None
    n = len(ds)
    for lag in range(-max_lag_s, max_lag_s + 1):
        a, b = (ds[lag:], df[:n - lag]) if lag >= 0 else (ds[:n + lag], df[-lag:])
        ok = np.isfinite(a) & np.isfinite(b)
        if ok.sum() < 3:
            continue
        a, b = a[ok], b[ok]
        if a.std() == 0 or b.std() == 0:
            continue
        c = float(np.corrcoef(a, b)[0, 1])
        if best is None or c > best[0]:
            best = (c, lag)
    return {"peak_lag_s": best[1] if best else None, "corr": round(best[0], 4) if best else None,
            "seconds": n}


def trailing_rv(fut_ts_ns: np.ndarray, fut_px: np.ndarray, at_ns: np.ndarray, start_ns: int, end_ns: int,
                minutes: int = RV_WINDOW_MIN) -> np.ndarray:
    """Realized vol (sqrt of summed squared 1-minute log returns) over the `minutes` whole minutes before each
    instant, from the last trade of each minute (known-at-time: only minutes ended by the instant)."""
    marks = np.arange(start_ns - minutes * 60 * NS, end_ns + 60 * NS, 60 * NS, dtype=np.int64)
    closes = last_on_grid(fut_ts_ns, fut_px, marks)
    lr = np.diff(np.log(closes))
    sq = np.where(np.isfinite(lr), lr * lr, np.nan)
    csum = np.concatenate([[0.0], np.cumsum(np.nan_to_num(sq))])
    cnt = np.concatenate([[0], np.cumsum(np.isfinite(sq))])
    k = np.searchsorted(marks, np.asarray(at_ns, dtype=np.int64), side="right") - 1  # last mark <= instant
    lo = np.clip(k - minutes, 0, None)
    total, have = csum[np.clip(k, 0, None)] - csum[lo], cnt[np.clip(k, 0, None)] - cnt[lo]
    out = np.sqrt(total)
    out[(k < minutes) | (have < minutes)] = np.nan
    return out


# --- step-8b answers (owner 2026-10-08): content time, effective time, trailing R (§5.1, F0.1, F1.1, F1.7) ---------

ONE_SECOND_TAU_NS = NS  # F0.1: τ = floor(asOf) + 1 s, every era and live (owner 2026-10-08)
ERA_BEFORE, ERA_FROM, ERA_LIVE = "before_stamp_change", "from_stamp_change", "live"


def floor_second_ns(as_of_ns: np.ndarray) -> np.ndarray:
    a = np.asarray(as_of_ns, dtype=np.int64)
    return (a // NS) * NS


def effective_tau_ns(as_of_ns: np.ndarray, resolution: np.ndarray | str, *, spot_lag_cleared: bool = False
                     ) -> np.ndarray:
    """F0.1 (owner 2026-10-08): τ = floor(asOf) + 1 s; floor(asOf) + 60 s for an uncleared 1-minute board."""
    fl = floor_second_ns(as_of_ns)
    res = np.broadcast_to(np.asarray(resolution), fl.shape)
    return fl + np.where((res == "1m") & (not spot_lag_cleared), ONE_MINUTE_TAU_SHIFT_NS, ONE_SECOND_TAU_NS)


def board_era(day, stamp_change_date, live: bool = False) -> str:
    """Which `board_content_offset_ms` row applies: live stream, or history before / from the stamp change."""
    if live:
        return ERA_LIVE
    return ERA_BEFORE if day < stamp_change_date else ERA_FROM


def content_offset_ms(mapping_cfg: dict, map_name: str, era: str) -> int | None:
    """F1.1: the measured median offset of a board's content from floor(asOf) (None: pending, owner)."""
    return mapping_cfg["board_content_offset_ms"][era][map_name]


def content_time_ns(as_of_ns: np.ndarray, offset_ms: int) -> np.ndarray:
    """F1.1: c = floor(asOf) + offset; F for R is read at c."""
    return floor_second_ns(as_of_ns) + int(offset_ms) * 1_000_000


def gap_segments(t_ns: np.ndarray, gap_s: float) -> np.ndarray:
    """F1.7 reset: a new segment after a gap between consecutive boards longer than gap_s (t_ns sorted)."""
    t = np.asarray(t_ns, dtype=np.int64)
    if len(t) == 0:
        return np.zeros(0, dtype=np.int64)
    return np.concatenate([[0], np.cumsum(np.diff(t) > gap_s * NS)]).astype(np.int64)


def trailing_median(t_ns: np.ndarray, r: np.ndarray, seg: np.ndarray, window_s: float) -> np.ndarray:
    """F1.7: for each board, the median of r over the boards of its segment with t in (t_i - window, t_i]
    (t_ns sorted; trailing only, the board itself included; the first board alone right after a reset)."""
    t = np.asarray(t_ns, dtype=np.int64)
    r = np.asarray(r, dtype=np.float64)
    seg = np.asarray(seg)
    out = np.full(len(t), np.nan)
    seg_start = np.zeros(len(t), dtype=np.int64)
    if len(t):
        starts = np.concatenate([[0], np.flatnonzero(np.diff(seg) != 0) + 1])
        seg_start = starts[np.searchsorted(starts, np.arange(len(t)), side="right") - 1]
    lo = np.maximum(np.searchsorted(t, t - int(window_s * NS), side="right"), seg_start)
    for i in range(len(t)):
        w = r[lo[i]:i + 1]
        w = w[np.isfinite(w)]
        if len(w):
            out[i] = np.median(w)
    return out


def ratio_in_force(t_ns: np.ndarray, r: np.ndarray, window_s: float, gap_s: float, outlier_bps: float,
                   reaccept_n: int) -> np.ndarray:
    """F1.7: the R in force after each applied board of one map and session (boards in content-time order).
    window_s > 0: trailing median of the board ratios (unfiltered); 0: the board's own R with F1.2."""
    seg = gap_segments(t_ns, gap_s)
    r = np.asarray(r, dtype=np.float64)
    if window_s > 0:
        return trailing_median(t_ns, r, seg, window_s)
    out = np.full(len(r), np.nan)
    for g in np.unique(seg):
        idx = np.flatnonzero(seg == g)
        acc, _, _ = accept_ratios(r[idx], outlier_bps, reaccept_n)
        prev = prev_accepted(acc)
        out[idx] = np.where(acc, r[idx], np.where(prev >= 0, r[idx][np.clip(prev, 0, None)], np.nan))
    return out
