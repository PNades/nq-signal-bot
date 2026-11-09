"""Step-13 variant `node_relevance_top_n` (owner 2026-11-06, SPEC §13 Q-step12 (2) with both follow-ups; feature_spec
F2.4): which relevant QQQ / NDXP nodes become NQ levels.

With `node_relevance_top_n` = n (null = off), on each applied board the nodes relevant by F2.4 (with the debounce,
`nodes.relevance`) are ranked by |value| (ties: nearer the board's spot, then the lower strike; recorded as built,
SPEC §13 Q-step13-0); a node is **ranked** once it is in the top n and stays ranked until it falls below
`node_relevance_top_n_leave_rank` (7th); the rank changes go through the same board-time debounce as the % test: a rank
join counts at once at >= `node_relevance_fast_pct` of the King, otherwise once it has been in the top n for
`node_relevance_min_sec` of board time; a rank leave only once it has been below the leave rank that long. A node that
is not relevant is not ranked (its relevance leave is already debounced). The confirmed King (F2.16) and the confirmed
ceiling and floor (F2.11) at the board are levels at any rank. **Level-relevant** = relevant and (ranked or protected).

Follow-up (i): this decides only which nodes are NQ zone-book members (and so events); the map state and every map
feature keep the full 25% / 20% set, so the protected King, ceiling and floor are chosen as now (no circularity).
"""

from __future__ import annotations

import numpy as np

from nqbot.features.nodes import _run_qualified


def ranks(board_idx: np.ndarray, strikes: np.ndarray, values: np.ndarray, relevant: np.ndarray,
          spot: np.ndarray) -> np.ndarray:
    """Per row, the node's rank (1 = largest |value|) among the relevant rows of its board; 0 where not relevant."""
    b = np.asarray(board_idx, dtype=np.int64)
    k = np.asarray(strikes, dtype=np.float64)
    a = np.abs(np.asarray(values, dtype=np.float64))
    rel = np.asarray(relevant, dtype=bool)
    out = np.zeros(len(b), dtype=np.int64)
    idx = np.flatnonzero(rel)
    if not len(idx):
        return out
    dist = np.abs(k[idx] - np.asarray(spot, dtype=np.float64)[b[idx]])
    o = idx[np.lexsort((k[idx], dist, -a[idx], b[idx]))]     # by board, then |value| desc, nearer spot, lower strike
    bo = b[o]
    first = np.r_[True, bo[1:] != bo[:-1]]
    start = np.maximum.accumulate(np.where(first, np.arange(len(o)), 0))
    out[o] = np.arange(len(o)) - start + 1
    return out


def level_relevant(board_idx, strikes, values, pct, relevant, spot, board_t, protected, top_n: int,
                   leave_rank: int, min_sec: float, fast: float | None) -> np.ndarray:
    """Per row: relevant and (ranked by the debounced top-n rule, or protected).
    `protected`: per row, the strike is the board's confirmed King, ceiling or floor."""
    b = np.asarray(board_idx, dtype=np.int64)
    k = np.asarray(strikes, dtype=np.float64)
    p = np.asarray(pct, dtype=np.float64)
    rel = np.asarray(relevant, dtype=bool)
    rk = ranks(b, k, values, rel, spot)
    out = np.zeros(len(b), dtype=bool)
    if not len(b):
        return out
    o = np.lexsort((b, k))                                    # by strike, then board (time)
    ko, to = k[o], np.asarray(board_t, dtype=np.int64)[b[o]]
    first = np.r_[True, ko[1:] != ko[:-1]]
    r_o, rel_o = rk[o], rel[o]
    need = int(round(float(min_sec) * 1_000_000_000))
    in_top = rel_o & (r_o >= 1) & (r_o <= top_n)
    below = rel_o & (r_o > leave_rank)
    with np.errstate(invalid="ignore"):
        join = _run_qualified(in_top, first, to, need)
        if fast is not None:
            join |= in_top & (p[o] >= fast)
    leave = _run_qualified(below, first, to, need) | ~rel_o   # not relevant: not ranked, at once
    decisive = first | join | leave
    last = np.maximum.accumulate(np.where(decisive, np.arange(len(o)), 0))
    ranked = np.where(join, True, False)[last]
    out[o] = rel_o & (ranked | np.asarray(protected, dtype=bool)[o])
    return out
