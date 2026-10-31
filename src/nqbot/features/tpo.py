"""F3.5 single prints (TPO), carried across sessions until filled (SPEC §5.3; feature_spec F3.5).

- TPO periods are 30-minute blocks aligned to 18:00 ET of the CME day. Bin b = floor(price / `tpo_tick_pts`) spans
  [b·w, (b + 1)·w) (owner 2026-10-31: a band's upper edge is its top bin's upper bound).
- At the close of each period, a **run** = a maximal run of >= `sp_min_ticks` consecutive bins each printed in exactly
  one completed period of the CME day, all from that same period (owner 2026-10-05, FQ-11). Only the period that just
  closed can form runs (a bin becomes single only at the close of the period that printed it).
- The **unfilled part** = the bins with no trade since the run formed (trades in the current, incomplete period fill).
  Each contiguous unfilled sub-run is a level, band = [its lowest bin low, its highest bin high] ± the single-print
  half-width; a sub-run wider than `max_zone_width` is split into pieces of <= `max_zone_width` from the bottom up.
- Identity: `level_id` = run id (formation period + the run's lowest bin) + the sub-run's lowest bin **when the
  sub-run is created** (at formation, or when a fill splits it: the lower part keeps its id, the upper part gets a new
  one); a sub-run keeps its id while it shrinks ("survives however short it becomes").
- The run ends when every bin has traded (`filled`). Unfilled runs carry to later sessions, roll-shifted (each bin
  keeps its own price interval, so a shift that isn't a multiple of the bin width stays exact), with `sp_age_sessions`.
- `sp_sessions_stacked` = the number of different formation sessions among unfilled single-print levels whose band
  overlaps a given band at t (owner 2026-10-24).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

from nqbot.features.session_tape import MIN_NS, Tape, cme_start_ns

PERIOD_NS = 30 * MIN_NS        # owner's fixed TPO period (feature_spec "Values")


@dataclass
class Run:
    run_id: str
    session: date                  # formation session (CME day)
    t_formed: int
    bin_ids: list[int]             # the original bin numbers (for ids)
    bin_lo: list[float]            # each bin's current lower price (after roll shifts)
    width: float
    filled: list[bool]
    piece_of: list[str] = field(default_factory=list)   # the sub-run level id each unfilled bin belongs to

    def to_json(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "session": self.session.isoformat(), "t_formed": self.t_formed,
                "bin_ids": self.bin_ids, "bin_lo": self.bin_lo, "width": self.width, "filled": self.filled,
                "piece_of": self.piece_of}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Run:
        return cls(d["run_id"], date.fromisoformat(d["session"]), d["t_formed"], list(d["bin_ids"]),
                   list(d["bin_lo"]), d["width"], list(d["filled"]), list(d["piece_of"]))

    def shift(self, spread: float) -> None:
        self.bin_lo = [x + spread for x in self.bin_lo]

    @property
    def done(self) -> bool:
        return all(self.filled)


@dataclass
class SpLevel:
    level_id: str
    run_id: str
    session: date
    created_ns: int
    end_ns: int | None
    times: list[int]
    lo: list[float]                # band edges without the half-width (the unfilled bins' span)
    hi: list[float]


def runs_in_period(printed_counts: dict[int, int], period_bins: set[int], min_ticks: int) -> list[list[int]]:
    """Maximal runs of consecutive bins printed only in this period (count 1 over the completed periods)."""
    single = sorted(b for b in period_bins if printed_counts.get(b, 0) == 1)
    runs, cur = [], []
    for b in single:
        if cur and b == cur[-1] + 1:
            cur.append(b)
        else:
            if len(cur) >= min_ticks:
                runs.append(cur)
            cur = [b]
    if len(cur) >= min_ticks:
        runs.append(cur)
    return runs


def _pieces(run: Run, max_width: float) -> list[tuple[int, int]]:
    """(start, end) index spans of the contiguous unfilled sub-runs."""
    out, k, n = [], 0, len(run.filled)
    while k < n:
        if run.filled[k]:
            k += 1
            continue
        j = k
        while j + 1 < n and not run.filled[j + 1]:
            j += 1
        out.append((k, j))
        k = j + 1
    return out


@dataclass
class SinglePrints:
    """One session's pass: carried runs first, then runs formed today; level timelines for the registry."""
    tick_pts: float
    min_ticks: int
    max_zone_width: float
    runs: list[Run] = field(default_factory=list)
    levels: dict[str, SpLevel] = field(default_factory=dict)

    def _spans(self, run: Run) -> list[tuple[int, int]]:
        """Contiguous unfilled sub-runs, each split into pieces of <= max_zone_width from the bottom up."""
        out = []
        for a, b in _pieces(run, self.max_zone_width):
            k = a
            while k <= b:
                j = k
                while j + 1 <= b and run.bin_lo[j + 1] + run.width - run.bin_lo[k] <= self.max_zone_width:
                    j += 1
                out.append((k, j))
                k = j + 1
        return out

    def _assign(self, run: Run, t: int) -> None:
        """(Re)build the pieces at formation or after a fill. A piece keeps the id its lowest bin had unless a lower
        piece already kept it (a split's upper part, a new max_zone_width piece): then it gets run id + its lowest
        bin. Pieces that vanished end at t; changed bands get a new step at t."""
        old = run.piece_of or [""] * len(run.filled)
        new = [""] * len(run.filled)
        used: set[str] = set()
        for k, j in self._spans(run):
            pid = old[k] if old[k] and old[k] not in used else f"{run.run_id}-{run.bin_ids[k]}"
            used.add(pid)
            for x in range(k, j + 1):
                new[x] = pid
        for pid in set(old) - {""} - used:
            lv = self.levels.get(pid)
            if lv is not None and lv.end_ns is None:
                lv.end_ns = t
        run.piece_of = new
        for pid in used:
            idx = [x for x, q in enumerate(new) if q == pid]
            lo, hi = run.bin_lo[idx[0]], run.bin_lo[idx[-1]] + run.width
            lv = self.levels.get(pid)
            if lv is None or lv.end_ns is not None:
                self.levels[pid] = SpLevel(pid, run.run_id, run.session, t, None, [t], [lo], [hi])
            elif (lv.lo[-1], lv.hi[-1]) != (lo, hi):
                lv.times.append(t)
                lv.lo.append(lo)
                lv.hi.append(hi)

    def start_session(self, carried: list[Run], t0: int) -> None:
        self.runs = [r for r in carried if not r.done]
        for r in self.runs:   # keep the carried piece ids (Q-step11b (3)): levels are recreated under them
            self._assign(r, t0)

    def _fill(self, tape: Tape, i: int, j: int) -> None:
        """Trades [i, j) fill the bins they print in, each at its own first trade, applied in time order (no
        lookahead: a bin filled later in the chunk is still unfilled before its trade)."""
        if j <= i:
            return
        p = tape.price[i:j]
        pmin, pmax = float(p.min()), float(p.max())
        for r in self.runs:
            fills: dict[int, list[int]] = {}
            for k, (lo, done) in enumerate(zip(r.bin_lo, r.filled)):
                if done or lo > pmax or lo + r.width <= pmin:
                    continue
                hits = np.flatnonzero((p >= lo) & (p < lo + r.width))
                if len(hits):
                    fills.setdefault(int(tape.ts[i + hits[0]]), []).append(k)
            for t in sorted(fills):
                for k in fills[t]:
                    r.filled[k] = True
                self._assign(r, t)

    def run_session(self, tape: Tape, day: date, carried: list[Run], end_ns: int | None = None) -> SinglePrints:
        start = cme_start_ns(day)
        end = end_ns if end_ns is not None else start + 23 * 3600 * 10 ** 9
        self.start_session(carried, start)
        counts: dict[int, int] = {}
        i = tape.idx_before(start)
        k = 0
        while start + (k + 1) * PERIOD_NS <= end:
            close = start + (k + 1) * PERIOD_NS
            # fills: minute by minute so a fill's time (the first trade in the bin) is exact
            for m in range(start + k * PERIOD_NS + MIN_NS, close + 1, MIN_NS):
                j = tape.idx_before(m)
                self._fill(tape, i, j)
                i = j
            sl = tape.window(start + k * PERIOD_NS, close)
            bins = set(np.floor(tape.price[sl] / self.tick_pts).astype(np.int64).tolist())
            for b in bins:
                counts[b] = counts.get(b, 0) + 1
            for run_bins in runs_in_period(counts, bins, self.min_ticks):
                rid = f"{day.isoformat()}T{k:02d}-{run_bins[0]}"
                r = Run(rid, day, close, run_bins, [b * self.tick_pts for b in run_bins], self.tick_pts,
                        [False] * len(run_bins))
                self.runs.append(r)
                self._assign(r, close)
            k += 1
        self.runs = [r for r in self.runs if not r.done]
        return self

    def carry(self) -> list[Run]:
        return [r for r in self.runs if not r.done]

    def stacked(self, lo: float, hi: float, t: int, half_width: float = 0.0) -> int:
        """`sp_sessions_stacked`: different formation sessions among unfilled single-print levels overlapping
        [lo, hi] at t."""
        sessions = set()
        for lv in self.levels.values():
            if lv.created_ns > t or (lv.end_ns is not None and lv.end_ns <= t):
                continue
            q = int(np.searchsorted(np.asarray(lv.times), t, side="right")) - 1
            if q < 0:
                continue
            if lv.lo[q] - half_width <= hi and lv.hi[q] + half_width >= lo:
                sessions.add(lv.session)
        return len(sessions)


def age_sessions(formed: date, today: date, sessions: list[date]) -> int:
    """`sp_age_sessions`: sessions from the formation session to today (0 = formed today)."""
    return sum(1 for d in sessions if formed < d <= today)
