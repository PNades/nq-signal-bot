"""GEX nodes on the futures: state machines, Trinity role tracks, beach balls and rolling ceilings / floors (step 11
part 2; SPEC §5.2; feature_spec F2.6, F2.6b, F2.7, F2.11, F1.8).

Per map and session (`MapState` + the map's instrument tape: NQ for QQQ / NDXP, ES for SPY / SPX, F0.9):
- **Node levels (F2.6, F2.6b):** every strike that is ever relevant gets one `level_state.LevelState` of the `node`
  kind, created at its first relevant board. Its band is a `StepBand` with one row per applied board: (strike ±
  `node_band`) × the R in force (F1.4), unknown (NaN) on a board that doesn't carry the strike (F2.1). It is frozen
  (`freezes`) while not relevant or missing (F2.4), paused in its chain's dislocation blocks (`blocks`, F1.8; NDXP
  takes QQQ's), and carries `node_series` for `xb_node_pctking_chg` / `xb_node_sign_flipped`. `flip_margin` keys:
  QQQ / SPY `qqq_node`, NDXP / SPX `ndxp_node`; on ES × ES ÷ NQ at the open (F0.11, Q-step11d (1)).
- **Role tracks (F2.19 Q18):** `trinity.RoleTrack` per node from the machine's transitions: the role it holds
  (confirmed or provisional; `BROKEN_BACK` while a provisional role is crossed back, which counts in no role and is not
  "no role yet" for Q19) and every cross against a role.
- **Beach balls (F2.7):** each cross -> reclaim episode: depth, seconds to the first trade back, seconds to the reclaim.
- **Rolls (F2.11):** one `rolls.RollSide` for the ceiling and one for the floor, fed every applied board and, within
  each board segment, only the trades that can change it (beyond the confirmed node's band or back past an excluded
  node's), found with numpy; the same result as feeding every trade (`tests/test_node_levels.py`). Inside a block
  trades wait; `block_end` judges them at the block end.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from nqbot.features import level_state as ls
from nqbot.features import node_switch as nsw
from nqbot.features import rolls as rl
from nqbot.features.bands import StepBand
from nqbot.features.map_state import MapState
from nqbot.features.session_tape import MIN_NS, NS, Bars, Tape
from nqbot.features.trinity import RoleTrack

FLIP_KEY = {"QQQ": "qqq_node", "SPY": "qqq_node", "NDXP": "ndxp_node", "SPX": "ndxp_node"}
SP_MAPS = ("SPY", "SPX")
BEACH_OUTPUTS = ("beach_ball_count_today", "bb_overshoot_depth", "bb_overshoot_depth_atr", "bb_overshoot_depth_em",
                 "bb_seconds_beyond", "bb_seconds_to_reclaim", "bb_minutes_since_reclaim")
BROKEN_BACK = "broken_back"   # a provisional role crossed back (F2.6 step 5): holds no role, never Q19's "no role yet"


@dataclass
class Scale:
    """ES ÷ NQ at the open for S&P maps (F0.11): the previous session's before `switch_ns`, today's from it."""
    prev: float = 1.0
    today: float = 1.0
    switch_ns: int | None = None


@dataclass
class NodeLevel:
    strike: float
    state: ls.LevelState
    track: RoleTrack
    beach: list = field(default_factory=list)   # [{'t_cross', 't_reclaim', 'depth', 'sec_beyond'}]
    not_relevant: list = field(default_factory=list)  # [(start, end)]: not relevant (a missing board keeps the state)

    def relevant_since(self, t_ns: int) -> int:
        """The start of the relevant stretch holding t (F2.4 `minutes_since_relevant`)."""
        start = int(self.state.band.start_ns)
        for a, z in self.not_relevant:
            if z <= t_ns:
                start = max(start, z)
        return start


def spans_from_mask(t: np.ndarray, frozen: np.ndarray, end_ns: int) -> list[tuple[int, int]]:
    """[(start, end)] of the runs of `frozen` boards: each from its first board's t_apply to the next board's."""
    out = []
    n = len(frozen)
    i = 0
    while i < n:
        if not frozen[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and frozen[j + 1]:
            j += 1
        out.append((int(t[i]), int(t[j + 1]) if j + 1 < n else int(end_ns)))
        i = j + 1
    return out


class MapNodes:
    """The node levels and rolls of one map and session."""

    def __init__(self, ms: MapState, tape: Tape, features: dict, bars_1m: Bars, bars_flip: Bars, *,
                 blocks: list | None = None, normals: Any = None, end_ns: int | None = None,
                 scale: Scale | None = None):
        self.ms, self.tape, self.f = ms, tape, features
        self.name = ms.name
        self.blocks = sorted(blocks or [])
        self.end_ns = end_ns if end_ns is not None else (int(tape.ts[-1]) + 1 if len(tape) else 0)
        if self.name in SP_MAPS and scale is None:
            raise ValueError(f"{self.name}: the ES ÷ NQ ratio at the open is needed for the ES parameters (F0.11)")
        self.scale = scale if scale is not None else Scale()
        self.half = float(features["node_band"][self.name])
        self.levels: dict[float, NodeLevel] = {}
        self._build_levels(bars_1m, bars_flip, normals)
        self.ceiling = MapRolls(self, +1)
        self.floor = MapRolls(self, -1)

    # --- node levels --------------------------------------------------------------------------------------------
    def _params(self):
        key = FLIP_KEY[self.name]
        if self.name not in SP_MAPS:
            return ls.Params.from_features(self.f, key), []
        sc = self.scale
        first = ls.Params.from_features(self.f, key, sc.prev)
        later = [(sc.switch_ns, ls.Params.from_features(self.f, key, sc.today))] if sc.switch_ns is not None else []
        return first, later

    def _build_levels(self, bars_1m: Bars, bars_flip: Bars, normals: Any) -> None:
        ms, mb = self.ms, self.ms.mb
        n = len(mb)
        if n == 0:
            return
        b, k = mb.s_board, mb.s_strike
        rel = ms.relevant
        params, params_from = self._params()
        for strike in np.unique(k[rel]):
            rows = np.flatnonzero(k == strike)
            boards = b[rows]
            first = int(boards[rel[rows]].min())
            lo = np.full(n - first, np.nan)
            hi = np.full(n - first, np.nan)
            pct = np.full(n - first, np.nan)
            sign = np.full(n - first, np.nan)
            on = np.zeros(n - first, dtype=bool)
            keep = boards >= first
            bi = boards[keep] - first
            rr = mb.r[boards[keep]]
            lo[bi] = (strike - self.half) * rr
            hi[bi] = (strike + self.half) * rr
            pct[bi] = ms.pct[rows[keep]]
            sign[bi] = ms.sign[rows[keep]]
            on[bi] = rel[rows[keep]]
            present = np.zeros(n - first, dtype=bool)
            present[bi] = True
            # F2.1 / F2.4: a board that doesn't carry the strike keeps its relevance state
            idx = np.where(present, np.arange(n - first), 0)
            np.maximum.accumulate(idx, out=idx)
            carried = on[idx]
            t = mb.t_apply[first:]
            band = StepBand(self.tape, t.astype(np.int64), lo, hi)
            freezes = spans_from_mask(t, ~on, self.end_ns)
            lv = ls.LevelState(f"{self.name}-{strike:g}", ls.NODE, self.tape, band, params, bars_1m, bars_flip,
                               normals=normals, blocks=list(self.blocks), end_ns=self.end_ns, params_from=params_from,
                               freezes=freezes, node_series=(t.astype(np.int64), pct, sign)).run()
            self.levels[float(strike)] = NodeLevel(float(strike), lv, _track(lv), _beach(lv, self.tape),
                                                   spans_from_mask(t, ~carried, self.end_ns))

    def node_at(self, strike: float, t_ns: int, atr: float | None = None, em: float | None = None) -> dict | None:
        nl = self.levels.get(float(strike))
        if nl is None or nl.state.band.start_ns > t_ns:
            return None
        band = nl.state.band.at(t_ns)
        out = {"strike": float(strike), "band": band, **nl.state.at(t_ns, atr, em)}
        if self.blocked(t_ns):   # F1.8: R-dependent outputs (F2.6, F2.6b, F2.7) null inside the block
            out.update({k: None for k in ls.OUTPUTS})
            out.update({k: None for k in BEACH_OUTPUTS})
            return out
        done = [e for e in nl.beach if e["t_reclaim"] <= t_ns]
        out["beach_ball_count_today"] = len(done)
        last = done[-1] if done else None
        out["bb_overshoot_depth"] = None if last is None else last["depth"]
        out["bb_overshoot_depth_atr"] = None if last is None or not atr else last["depth"] / atr
        out["bb_overshoot_depth_em"] = None if last is None or not em else last["depth"] / em
        out["bb_seconds_beyond"] = None if last is None else last["sec_beyond"]
        out["bb_seconds_to_reclaim"] = None if last is None else (last["t_reclaim"] - last["t_cross"]) / NS
        out["bb_minutes_since_reclaim"] = None if last is None else (t_ns - last["t_reclaim"]) / MIN_NS
        return out

    def in_block(self, t_ns: int) -> bool:
        """Processing: trades in [a, z) wait, the block end is judged at z (as `level_state`)."""
        return any(a <= t_ns < z for a, z in self.blocks)

    def blocked(self, t_ns: int) -> bool:
        """Outputs: null on the closed span [a, z], the guard's own convention (`disloc_guard.in_block`)."""
        return any(a <= t_ns <= z for a, z in self.blocks)


def _track(lv: ls.LevelState) -> RoleTrack:
    """F2.19 Q18: the role held over time (None before the first role, `BROKEN_BACK` while a provisional role is
    crossed back) and every cross against a role."""
    tr = RoleTrack()
    cur = object()
    for t, name in [(int(lv.band.start_ns), "start")] + list(lv.events):
        s = lv.state_at(t)
        role = BROKEN_BACK if (s.crossed and s.broken_back) else s.role
        if role != cur:
            tr.roles.append((t, role))
            cur = role
        base = name.removesuffix("_at_block_end")
        if base == "cross":
            tr.crosses.append((t, s.orig_role))
        elif base == "cross_back":
            tr.crosses.append((t, ls.opposite(s.orig_role)))
    return tr


def _beach(lv: ls.LevelState, tape: Tape) -> list[dict]:
    """F2.7: every cross -> reclaim episode (a cross that ends in a flip is not one)."""
    out = []
    t_cross = None
    for t, name in lv.events:
        base = name.removesuffix("_at_block_end")
        if base == "cross":
            t_cross = t
        elif base == "flip":
            t_cross = None
        elif base == "reclaim" and t_cross is not None:
            ep = lv.state_at(t - 1).episode
            s = lv.state_at(t_cross)
            d = ls.up(s.orig_role)
            i0, i1 = tape.idx_at(t_cross), tape.idx_at(t)
            lo, hi = lv.band.at_trades(i0, i1)
            bd = ls.beyond(tape.price[i0:i1], lo, hi, d)
            with np.errstate(invalid="ignore"):
                back = np.flatnonzero(np.isfinite(bd) & (bd <= 0))   # a trade with the band unknown is skipped
            t_back = int(tape.ts[i0 + back[0]]) if len(back) else t
            out.append({"t_cross": t_cross, "t_reclaim": t, "depth": 0.0 if ep is None else ep.depth,
                        "sec_beyond": (t_back - t_cross) / NS})
            t_cross = None
    return out


class MapRolls:
    """F2.11 for one side of one map: `rolls.RollSide` driven by the boards and the trades that can change it."""

    def __init__(self, nodes: MapNodes, sign: int):
        self.nodes, self.sign = nodes, sign
        self.side = rl.RollSide(sign, nsw.from_config(nodes.f, "ceiling" if sign > 0 else "floor"))
        self.snaps_t: list[int] = []
        self.snaps: list[dict] = []
        self.episode_start: int | None = None
        self.episode_board: int | None = None
        self._run()

    def _restricted(self, ks: np.ndarray, vs: np.ndarray, spot: float, r: float) -> tuple[dict, dict]:
        """The whole board's values, and the bands of this side's half of the strike list (plus the confirmed,
        anchor and excluded nodes): only the confirmed and excluded nodes' bands are ever read."""
        s = self.side
        keep_from = [spot] + [x for x in (s.confirmed, s.anchor) if x is not None] + list(s.excluded)
        h = self.nodes.half
        if self.sign > 0:
            m = ks >= min(keep_from)
        else:
            m = ks <= max(keep_from)
        kk = ks[m].tolist()
        return dict(zip(ks.tolist(), vs.tolist())), {k: ((k - h) * r, (k + h) * r) for k in kk}

    def _triggers(self, p: np.ndarray) -> np.ndarray:
        """Trades that change the side: beyond the confirmed node's band, or back past an excluded node's."""
        s = self.side
        hit = np.zeros(len(p), dtype=bool)
        c = s.confirmed
        if c is not None and c in s._bands:
            lo, hi = s._bands[c]
            hit |= (p > hi) if self.sign > 0 else (p < lo)
        for x in s.excluded:
            if x in s._bands:
                lo, hi = s._bands[x]
                hit |= (p < lo) if self.sign > 0 else (p > hi)
        return hit

    def _snap(self, t: int, board: int) -> None:
        s = self.side
        was = self.snaps[-1]["episode"] if self.snaps else False
        if s.episode and (not was or (self.snaps and self.snaps[-1]["anchor"] != s.anchor)):
            self.episode_start, self.episode_board = t, board
        if not s.episode:
            self.episode_start = self.episode_board = None
        last_roll = next((e for e in reversed(s.events) if e.kind == "roll"), None)
        self.snaps_t.append(t)
        self.snaps.append({"confirmed": s.confirmed, "episode": s.episode, "paused": s.paused, "anchor": s.anchor,
                           "roll_steps": s.roll_steps(), "rolls_today": s.rolls_today,
                           "rollbacks_today": s.rollbacks_today, "partial_rollbacks_today": s.partial_rollbacks_today,
                           "roll_breaks_today": s.roll_breaks_today,
                           "last_roll_shrink_pct": None if last_roll is None else last_roll.old_shrink_pct,
                           "last_roll_growth_pct": None if last_roll is None else last_roll.new_growth_pct,
                           "episode_start": self.episode_start, "episode_board": self.episode_board,
                           "board": board})

    def _run(self) -> None:
        nodes = self.nodes
        mb, tape = nodes.ms.mb, nodes.tape
        n = len(mb)
        ends = sorted({z for _, z in nodes.blocks})
        edges = sorted({b for blk in nodes.blocks for b in blk})
        for i in range(n):
            t0 = int(mb.t_apply[i])
            t1 = int(mb.t_apply[i + 1]) if i + 1 < n else nodes.end_ns
            ks, vs = mb.strikes(i)
            values, bands = self._restricted(ks, vs, float(mb.spot[i]), float(mb.r[i]))
            self.side.on_board(t0 / NS, float(mb.spot[i]), values, bands, bool(mb.one_minute[i]))
            self._snap(t0, i)
            cuts = [t0] + [e for e in edges if t0 < e < t1] + [t1]
            for a, z in zip(cuts, cuts[1:]):
                if a in ends and not nodes.in_block(a):   # a block ended here: judge the paused checks at once
                    last = tape.last_price_at(a)
                    if last is not None:
                        self.side.block_end(a / NS, last)
                        self._snap(a, i)
                if nodes.in_block(a):
                    continue
                j, jz = tape.idx_before(a), tape.idx_before(z)
                while j < jz:
                    hit = np.flatnonzero(self._triggers(tape.price[j:jz]))
                    if not len(hit):
                        break
                    j += int(hit[0])
                    self.side.on_trade(int(tape.ts[j]) / NS, float(tape.price[j]))
                    self._snap(int(tape.ts[j]), i)
                    j += 1

    def at(self, t_ns: int) -> dict[str, Any] | None:
        k = bisect_right(self.snaps_t, t_ns) - 1
        if k < 0:
            return None
        s = dict(self.snaps[k])
        s["rolling"] = bool(s["episode"] and not s["paused"])
        s["minutes_since_episode_began"] = None if s["episode_start"] is None else \
            (t_ns - s["episode_start"]) / MIN_NS
        return s
