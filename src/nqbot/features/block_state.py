"""Features during a mapping-dislocation block (owner 2026-10-17, SPEC §5.1, §13 Q-dislguard-2 (a); feature_spec F1.8).

While a chain is blocked (F1.8, `ChainGuard`), its R may be wrong, so every feature that converts that chain's strikes with
its R is **null** (F0.7: unknown, never the last value carried) for decisions inside the block, and its node-state
machines (F2.6 crossed / flipped / reclaimed, F2.11 ceiling / floor pause / resume) **pause**: no transition is taken from
trades judged against a band mapped with that R. At the block end the state is judged **at once** against the band then
in force, as a level check, not a transition: a level that price is now beyond is marked crossed at the block end, so a
real cross made during the block is not missed waiting for a fresh trade through the band. A cross that price undid
before the block ended leaves nothing (the band it crossed was not known). The same code runs in labels, both simulators
and live (rule 4): history calls `resume` at each block's end with the last trade at or before it; live calls it when the
clock passes `ChainGuard.blocked_until_ns`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from nqbot.features.disloc_guard import in_block


def null_in_blocks(values: np.ndarray, t_ns: np.ndarray, spans: list[tuple[int, int]]) -> np.ndarray:
    """A chain's R-dependent feature column with every decision inside one of its blocks set to null (NaN)."""
    out = np.asarray(values, dtype=float).copy()
    out[in_block(spans, t_ns)] = np.nan
    return out


@dataclass
class NodeCross:
    """F2.6 crossed state of one node level (GEX nodes: crossed when price trades past the band), paused in a block.

    side +1: price was above the band (the band is support; crossed below its low); -1: below (crossed above its high).
    """
    side: int
    crossed_ns: int | None = None
    paused: bool = False

    def beyond(self, price: float, lo: float, hi: float) -> bool:
        return price < lo if self.side > 0 else price > hi

    def on_trade(self, t_ns: int, price: float, lo: float, hi: float, blocked: bool) -> bool:
        """One trade against the band in force at t; inside the chain's block nothing changes. Returns crossed."""
        if self.crossed_ns is None:
            if blocked:
                self.paused = True
            else:
                self.paused = False
                if self.beyond(price, lo, hi):
                    self.crossed_ns = int(t_ns)
        return self.crossed_ns is not None

    def resume(self, t_end_ns: int, last_price: float, lo: float, hi: float) -> bool:
        """The block ended at t_end: judge the last price against the band in force then, at once. Returns crossed."""
        self.paused = False
        if self.crossed_ns is None and self.beyond(last_price, lo, hi):
            self.crossed_ns = int(t_end_ns)
        return self.crossed_ns is not None


def replay_cross(state: NodeCross, t_ns: np.ndarray, price: np.ndarray,
                 band_at: Callable[[int], tuple[float, float]], spans: list[tuple[int, int]]) -> NodeCross:
    """History / labels / simulators: trades in time order through `state`, `resume` at once at each block end."""
    ends = sorted(b for _, b in spans)
    k, last = 0, None
    blocked = in_block(spans, t_ns)
    for t, p, bl in zip(np.asarray(t_ns, dtype=np.int64), price, blocked):
        while k < len(ends) and ends[k] < t:
            if last is not None:
                state.resume(ends[k], last, *band_at(ends[k]))
            k += 1
        state.on_trade(int(t), float(p), *band_at(int(t)), bool(bl))
        last = float(p)
    return state
