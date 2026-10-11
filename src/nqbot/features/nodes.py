"""F2.4 relevant nodes with hysteresis (SPEC §5.2 Relevant nodes and Node identity; feature_spec F2.4).

A node is tracked by (map, strike). It becomes relevant at the first applied board where its % of King (F2.3: |value| /
|King value| of that same board, over the strikes the board carries) is >= `min_node_pct_of_king` (0.25) and stops
being relevant at the first applied board where it is < `node_drop_pct` (0.20); in between it keeps its state. A strike
missing from a board keeps its state until it reappears (F2.1). The state starts fresh (not relevant) at each session's
first applied board (owner 2026-10-11: history is RTH-only and the 0DTE chain is a new expiration every day).

One function for the step-8b checks and the step-11 features.
"""

from __future__ import annotations

import numpy as np


def relevance(board_idx: np.ndarray, strikes: np.ndarray, pct: np.ndarray, board_ok: np.ndarray,
              enter: float, drop: float) -> tuple[np.ndarray, np.ndarray]:
    """For the strike rows of one map and one session (board numbers in time order, `board_ok` per board = applied):
    (relevant, held) per row. `held` = relevant only because of the drop hold (drop <= pct < enter). Rows on boards
    that are not applied are neither, and do not change the state."""
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
