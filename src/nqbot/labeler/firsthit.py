"""First-hit and range queries on a session's NQ trades in exchange sequence (§5.4b: every trade checked against every
price, in order; the tape is sorted by ts_event then sequence, `session_tape.Tape`).

Block maxima / minima make each query a few short numpy scans; the results are the trade-by-trade scan's, exactly
(`tests/test_labeler.py` compares them with a plain loop)."""

from __future__ import annotations

import numpy as np

BLOCK = 256
EPS = 1e-9


class PathIndex:
    def __init__(self, price: np.ndarray, block: int = BLOCK):
        self.p = np.asarray(price, dtype=np.float64)
        self.n = len(self.p)
        self.B = block
        nb = (self.n + block - 1) // block
        pad = nb * block - self.n
        q = np.r_[self.p, np.full(pad, np.nan)].reshape(nb, block) if nb else np.zeros((0, block))
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            self.bmax = np.nanmax(q, axis=1) if nb else np.zeros(0)
            self.bmin = np.nanmin(q, axis=1) if nb else np.zeros(0)

    def _first(self, i: int, j: int | None, test, btest) -> int | None:
        j = self.n if j is None else min(j, self.n)
        if i < 0:
            i = 0
        if i >= j:
            return None
        B = self.B
        b0, b1 = i // B, (j - 1) // B
        end0 = min((b0 + 1) * B, j)
        k = np.flatnonzero(test(self.p[i:end0]))
        if len(k):
            return i + int(k[0])
        if b1 > b0 + 1:
            kk = np.flatnonzero(btest(b0 + 1, b1))
            if len(kk):
                blk = b0 + 1 + int(kk[0])
                k = np.flatnonzero(test(self.p[blk * B:(blk + 1) * B]))
                return blk * B + int(k[0])
        if b1 > b0:
            k = np.flatnonzero(test(self.p[b1 * B:j]))
            if len(k):
                return b1 * B + int(k[0])
        return None

    def first_ge(self, i: int, x: float, j: int | None = None) -> int | None:
        """The first trade index in [i, j) with price >= x."""
        return self._first(i, j, lambda a: a >= x - EPS, lambda a, b: self.bmax[a:b] >= x - EPS)

    def first_le(self, i: int, x: float, j: int | None = None) -> int | None:
        return self._first(i, j, lambda a: a <= x + EPS, lambda a, b: self.bmin[a:b] <= x + EPS)

    def first_beyond(self, i: int, x: float, direction: int, j: int | None = None) -> int | None:
        """direction +1: the first price >= x; -1: <= x."""
        return self.first_ge(i, x, j) if direction > 0 else self.first_le(i, x, j)

    def extreme(self, i: int, j: int, direction: int) -> tuple[float, int] | None:
        """(max, its first index) over [i, j) for direction +1, (min, its first index) for -1."""
        j = min(j, self.n)
        i = max(i, 0)
        if i >= j:
            return None
        B = self.B
        b0, b1 = i // B, (j - 1) // B
        if b1 - b0 <= 2:
            seg = self.p[i:j]
            k = int(np.argmax(seg)) if direction > 0 else int(np.argmin(seg))
            return float(seg[k]), i + k
        parts = [(i, (b0 + 1) * B), (b1 * B, j)]
        cands = []
        for a, z in parts:
            seg = self.p[a:z]
            k = int(np.argmax(seg)) if direction > 0 else int(np.argmin(seg))
            cands.append((float(seg[k]), a + k))
        blk = self.bmax[b0 + 1:b1] if direction > 0 else self.bmin[b0 + 1:b1]
        kb = int(np.argmax(blk)) if direction > 0 else int(np.argmin(blk))
        bb = b0 + 1 + kb
        seg = self.p[bb * B:(bb + 1) * B]
        k = int(np.argmax(seg)) if direction > 0 else int(np.argmin(seg))
        cands.append((float(seg[k]), bb * B + k))
        # the best value; on a tie the earliest index
        if direction > 0:
            best = max(c[0] for c in cands)
        else:
            best = min(c[0] for c in cands)
        return best, min(c[1] for c in cands if c[0] == best)
