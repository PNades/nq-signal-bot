"""F2.4 relevant nodes with hysteresis (SPEC §5.2 Relevant nodes and Node identity; feature_spec F2.4).

A node is tracked by (map, strike). It becomes relevant at the first applied board where its % of King (F2.3: |value| /
|King value| of that same board, over the strikes the board carries) is >= `min_node_pct_of_king` (0.25) and stops
being relevant at the first applied board where it is < `node_drop_pct` (0.20); in between it keeps its state. A strike
missing from a board keeps its state until it reappears (F2.1). The state starts fresh (not relevant) at each session's
first applied board (owner 2026-10-11: history is RTH-only and the 0DTE chain is a new expiration every day).

One function for the step-8b checks and the step-11 features.

**Debounce (step 13, owner 2026-11-04, SPEC §13 Q-step12a (3); every map and the pika cloud, owner 2026-11-09):** with
`min_sec` > 0 (`node_relevance_min_sec` 60) and `fast` (`node_relevance_fast_pct` 0.40), a node at >= `fast` of the King
becomes relevant at once (at that board); a marginal join (>= `enter`, < `fast`) counts only once the node has been
>= `enter` on every one of its boards for `min_sec` of board time (`board_t`, the boards' apply times), at the first
board that completes it; a leave (< `drop`) likewise only once it has been < `drop` for `min_sec`. A board that doesn't
carry the strike neither breaks nor extends a run (F2.1). Boards up to t only. `min_sec` 0 = the rule above, exactly.
The step-8b gate code calls it without the debounce.
"""

from __future__ import annotations

import numpy as np


def relevance(board_idx: np.ndarray, strikes: np.ndarray, pct: np.ndarray, board_ok: np.ndarray,
              enter: float, drop: float, board_t: np.ndarray | None = None, min_sec: float = 0.0,
              fast: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """For the strike rows of one map and one session (board numbers in time order, `board_ok` per board = applied):
    (relevant, held) per row. `held` = relevant only because of the drop hold (drop <= pct < enter). Rows on boards
    that are not applied are neither, and do not change the state. `board_t`, `min_sec`, `fast`: the debounce."""
    if min_sec and min_sec > 0:
        if board_t is None:
            raise ValueError("the relevance debounce needs the boards' apply times")
        return _debounced(board_idx, strikes, pct, board_ok, enter, drop, np.asarray(board_t, dtype=np.int64),
                          int(round(float(min_sec) * 1_000_000_000)), fast)
    b = np.asarray(board_idx, dtype=np.int64)
    k = np.asarray(strikes, dtype=np.float64)
    p = np.asarray(pct, dtype=np.float64)
    rel = np.zeros(len(b), dtype=bool)
    idx = np.flatnonzero(np.asarray(board_ok, dtype=bool)[b]) if len(b) else np.zeros(0, dtype=np.int64)
    if len(idx) == 0:
        return rel, rel.copy()
    o = idx[np.lexsort((b[idx], k[idx]))]  # by strike, then board (time)
    ko, po = k[o], p[o]
    n = len(o)
    first = np.concatenate([[True], ko[1:] != ko[:-1]])  # the strike's first applied board this session
    with np.errstate(invalid="ignore"):
        decisive = first | (po >= enter) | (po < drop)  # NaN % of King (no King) keeps the state
        last = np.maximum.accumulate(np.where(decisive, np.arange(n), 0))  # never before the strike's first row
        rel[o] = po[last] >= enter
        held = rel & ~(p >= enter)
    return rel, held


def _run_qualified(cond: np.ndarray, first: np.ndarray, t: np.ndarray, need_ns: int) -> np.ndarray:
    """Rows (in strike-then-time order) inside a run of `cond` that has lasted >= need_ns since the run's first row."""
    n = len(cond)
    start = cond & (first | ~np.r_[False, cond[:-1]])
    last_start = np.maximum.accumulate(np.where(start, np.arange(n), -1))
    ok = cond & (last_start >= 0)
    held_ns = np.where(ok, t - t[np.maximum(last_start, 0)], -1)
    return ok & (held_ns >= need_ns)


def _debounced(board_idx, strikes, pct, board_ok, enter, drop, board_t, need_ns, fast):
    b = np.asarray(board_idx, dtype=np.int64)
    k = np.asarray(strikes, dtype=np.float64)
    p = np.asarray(pct, dtype=np.float64)
    rel = np.zeros(len(b), dtype=bool)
    idx = np.flatnonzero(np.asarray(board_ok, dtype=bool)[b]) if len(b) else np.zeros(0, dtype=np.int64)
    if len(idx) == 0:
        return rel, rel.copy()
    o = idx[np.lexsort((b[idx], k[idx]))]  # by strike, then board (time)
    ko, po, to = k[o], p[o], board_t[b[o]]
    n = len(o)
    first = np.concatenate([[True], ko[1:] != ko[:-1]])
    with np.errstate(invalid="ignore"):
        j_cond, l_cond = po >= enter, po < drop          # NaN % of King: neither (keeps the state, breaks a run)
        join = _run_qualified(j_cond, first, to, need_ns)
        if fast is not None:
            join |= po >= fast
        leave = _run_qualified(l_cond, first, to, need_ns)
    decisive = first | join | leave
    last = np.maximum.accumulate(np.where(decisive, np.arange(n), 0))
    val = np.where(join, True, np.where(leave, False, False))   # the strike's first row: relevant only by a join
    rel[o] = val[last]
    with np.errstate(invalid="ignore"):
        held = rel & ~(p >= enter)
    return rel, held
