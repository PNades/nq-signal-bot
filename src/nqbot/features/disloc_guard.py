"""Mapping-dislocation guard (owner 2026-10-14, SPEC §5.1, §9, §13 Q-dislguard; feature_spec F1.8).

A data-quality guard, not a trading rule (CLAUDE.md rule 6): it reads only the mapping inputs (the futures price and the
board's spot), and its thresholds are fixed from data quality, never tuned on trade results. Two triggers per chain,
either one starts a block:

- **per board (the faster trigger):** each board's own R (F1.1, F at its content time ÷ its spot), timed at its apply
  time. A level counts only when two boards within `disl_guard_confirm_sec` both reached it: for board k,
  held_high_k = min(r_k, max r of the boards in [t_k − confirm, t_k)) and held_low_k = max(r_k, min of those). Board k
  is flagged when, over the trailing `disl_guard_window_sec` ((t_k − window, t_k]), max held_high − min held_low exceeds
  `disl_guard_pct` × the median r of those boards. A single board at either end (a lone spike or dip, or a jump measured
  from one) never makes a held level, so it cannot fire.
- **the bot's R (backstop):** the R in force (F1.7) at each apply time; flagged when its trailing-window range
  ((max − min) ÷ median, the `step8b dislocations` definition) exceeds `disl_guard_pct`.

Both windows reset after a gap between boards longer than `ratio_gap_reset_sec`, and at each session start (a new
`ChainGuard`). The chain is blocked from a flagged board until `disl_guard_hold_sec` after the latest flagged board of
either trigger (trailing only: a block ends `hold` after the last flag, never filled in afterwards). One code path: the
history check, labels, both simulators and live feed boards to `ChainGuard.on_board` in apply-time order (rule 4).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

NS = 1_000_000_000
TRIGGER_BOARD = "per_board"
TRIGGER_BOT = "bot_r"
TRIGGERS = (TRIGGER_BOARD, TRIGGER_BOT)


@dataclass(frozen=True)
class GuardParams:
    pct: float
    window_s: float
    confirm_s: float
    hold_s: float
    gap_s: float

    @classmethod
    def from_features(cls, features: dict[str, Any]) -> GuardParams:
        m = features["mapping"]
        return cls(pct=float(m["disl_guard_pct"]), window_s=float(m["disl_guard_window_sec"]),
                   confirm_s=float(m["disl_guard_confirm_sec"]), hold_s=float(m["disl_guard_hold_sec"]),
                   gap_s=float(m["ratio_gap_reset_sec"]))


@dataclass
class Fire:
    """One fire of one trigger on one chain: logged with its trigger, chain and size (and a Discord note live)."""
    chain: str
    trigger: str
    start_ns: int
    size: float  # the range at the first flagged board, as a fraction of R
    peak: float
    last_flag_ns: int
    hold_ns: int = field(repr=False)

    @property
    def end_ns(self) -> int:
        return self.last_flag_ns + self.hold_ns

    def note(self, fmt_time=None) -> str:
        """The log line / Discord note: trigger, chain and size."""
        t = fmt_time(self.start_ns) if fmt_time else str(self.start_ns)
        name = "per-board R" if self.trigger == TRIGGER_BOARD else "bot's R"
        return (f"mapping guard: {self.chain} {name} moved {self.size * 100:.3f}% within the window at {t}; "
                f"new entries on {self.chain} levels blocked, pending entries there removed")


class ChainGuard:
    """The guard for one chain and one session. Feed every applied board in apply-time order."""

    def __init__(self, chain: str, params: GuardParams):
        self.chain = chain
        self.p = params
        self._board: deque[tuple[int, float, float, float]] = deque()  # (t, r, held_high, held_low)
        self._bot: deque[tuple[int, float]] = deque()
        self._last_t: dict[str, int | None] = {TRIGGER_BOARD: None, TRIGGER_BOT: None}
        self._open: dict[str, Fire | None] = {TRIGGER_BOARD: None, TRIGGER_BOT: None}
        self.fires: list[Fire] = []
        self.blocked_until_ns: int | None = None

    def _gap(self, which: str, t: int) -> bool:
        last = self._last_t[which]
        return last is not None and t - last > self.p.gap_s * NS

    def board_range(self, t: int, r: float) -> float:
        if self._gap(TRIGGER_BOARD, t):
            self._board.clear()
        self._last_t[TRIGGER_BOARD] = t
        c0 = t - int(self.p.confirm_s * NS)
        prev = [x[1] for x in self._board if x[0] >= c0]
        hh, hl = (min(r, max(prev)), max(r, min(prev))) if prev else (np.nan, np.nan)
        self._board.append((t, r, hh, hl))
        w0 = t - int(self.p.window_s * NS)
        while self._board and self._board[0][0] <= w0:
            self._board.popleft()
        highs = [x[2] for x in self._board if x[2] == x[2]]
        lows = [x[3] for x in self._board if x[3] == x[3]]
        if not highs:
            return 0.0
        return (max(highs) - min(lows)) / float(np.median([x[1] for x in self._board]))

    def bot_range(self, t: int, r_in: float) -> float:
        if self._gap(TRIGGER_BOT, t):
            self._bot.clear()
        self._last_t[TRIGGER_BOT] = t
        self._bot.append((t, r_in))
        w0 = t - int(self.p.window_s * NS)
        while self._bot and self._bot[0][0] <= w0:
            self._bot.popleft()
        v = [x[1] for x in self._bot]
        return (max(v) - min(v)) / float(np.median(v))

    def _flag(self, trigger: str, t: int, rng: float) -> Fire | None:
        hold = int(self.p.hold_s * NS)
        self.blocked_until_ns = max(self.blocked_until_ns or t + hold, t + hold)
        cur = self._open[trigger]
        if cur is not None and t <= cur.end_ns:
            cur.last_flag_ns, cur.peak = t, max(cur.peak, rng)
            return None
        f = Fire(self.chain, trigger, t, rng, rng, t, hold)
        self._open[trigger] = f
        self.fires.append(f)
        return f

    def on_board(self, t_apply_ns: int, r_board: float, r_in_force: float) -> list[Fire]:
        """Process one applied board; returns the fires it starts (each to be logged)."""
        t = int(t_apply_ns)
        new: list[Fire] = []
        if np.isfinite(r_board):
            rng = self.board_range(t, float(r_board))
            if rng > self.p.pct and (f := self._flag(TRIGGER_BOARD, t, rng)):
                new.append(f)
        if np.isfinite(r_in_force):
            rng = self.bot_range(t, float(r_in_force))
            if rng > self.p.pct and (f := self._flag(TRIGGER_BOT, t, rng)):
                new.append(f)
        return new

    def blocked(self, t_ns: int) -> bool:
        """Whether new entries on this chain's levels are blocked at t (t at or after the last board fed)."""
        return self.blocked_until_ns is not None and t_ns <= self.blocked_until_ns


def run_session(chain: str, params: GuardParams, t_apply_ns: np.ndarray, r_board: np.ndarray,
                r_in_force: np.ndarray) -> list[Fire]:
    """History / labels / simulators: feed one session's boards (apply-time order) through the same ChainGuard."""
    g = ChainGuard(chain, params)
    for t, rb, ri in zip(np.asarray(t_apply_ns, dtype=np.int64), r_board, r_in_force):
        g.on_board(int(t), float(rb), float(ri))
    return g.fires


def block_spans(fires: list[Fire]) -> list[tuple[int, int]]:
    """The blocked spans [start, last flag + hold] of a session, overlapping fires of either trigger merged."""
    spans = sorted((f.start_ns, f.end_ns) for f in fires)
    out: list[list[int]] = []
    for a, b in spans:
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def in_block(spans: list[tuple[int, int]], t_ns: np.ndarray | int) -> np.ndarray:
    """True where t falls inside a blocked span (labels: a pair whose decision_ts is inside one is not tradeable)."""
    t = np.atleast_1d(np.asarray(t_ns, dtype=np.int64))
    out = np.zeros(len(t), dtype=bool)
    for a, b in spans:
        out |= (t >= a) & (t <= b)
    return out
