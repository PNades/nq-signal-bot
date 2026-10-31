"""One state machine for every level type: crossed / flipped / reclaimed, post-cross retests and the F2.6b cross
features (SPEC §5.2, §5.3, §5.4; feature_spec F2.6, F2.6b, F3.9, F5.2, F5.9, F0.5).

Rule kinds (how a level is crossed, and how a provisional role is crossed back):
- `node`        GEX nodes: the first trade beyond the full band (F2.6 step 2); paused in a mapping-dislocation block
                (F1.8, F2.6 step 6): no transition inside, at the block end the last trade at or before it is judged
                at once against the band then in force (as `block_state.NodeCross`).
- `container`   gameplan zones: a trade past the far edge, cross back past the other edge (F5.9); flip margin 0.
- `chart`       VWAP lines and bands, POC, LVNs, previous VAH/VAL, EM lines, dark pools: a settled 1-minute bar
                (F5.2, F0.5 margin 0: open beyond the band as of the bar's open, close beyond as of its close).
- `single_print` as `chart`, against the live band (the band frozen at a touch is the event's, part 3).
- `sweep`       highs/lows and IB high/low (F3.9): from the first trade beyond, a break at once on a trade more than
                `sweep_max_depth` beyond; swept (held) when a 1-minute bar inside the window (the break bar, then
                `sweep_reclaim_bars` clock minutes) closes not beyond; else a break at the close of the window's last
                minute. While unresolved the far side neither re-arms nor changes the approach side (`unresolved`).
Common steps (F2.6):
1. no role until price is >= `rearm_dist` outside the band: below -> ceiling, above -> floor;
2. crossed by the kind's rule -> a cross episode opens and the level holds the opposite role provisionally (step 5);
   armed in it once price is >= `rearm_dist` beyond the band on the crossed side;
3. flipped: while crossed, a clock-aligned `flip_bar_minutes` bar settles beyond the band on the crossed side by
   >= `flip_margin` (F0.5): the provisional role is confirmed, `flip_count` + 1; a later cross of the new role is a new
   cross (a new episode);
4. reclaimed: while crossed (not flipped), once the cross back is confirmed by the kind's own rule (step 5), price
   trades >= `rearm_dist` beyond the original-side edge (in the confirming bar or sweep window, known at its close):
   the original role returns, `reclaimed`, the episode closes (Q8, owner 2026-10-31);
5. a cross back of the provisional role by the kind's own rule ends it (that retest's break); the level then creates no
   events until a reclaim (4) or a move beyond the band on the crossed side confirmed by the same rule (a trade for
   nodes and gameplan zones, a settled 1-minute bar for chart levels, an unswept break for highs/lows), which resumes
   the same episode (Q8).
Owner 2026-10-31 (SPEC §13 Q-step11a): the machine runs from the level's creation (overnight included).
Cross features (F2.6b), per episode from the cross, trades on the level's own tape, band as of each trade:
`xb_depth` (deepest beyond the crossed-side edge), `xb_vol_rel` / `xb_delta_rel` (volume / buy - sell traded beyond
÷ the time-of-day normal over the seconds spent beyond, RTH part only, owner 2026-10-31), `xb_sec_beyond`,
`xb_settled_1m`, `xb_settled_5m` (by >= `flip_margin`), `xb_reentries`, `cross_count_today`, `minutes_since_cross`.
They are reported only while the level is in a post-cross role (provisional or flipped).

The whole session runs in one causal pass; `at(t)` reads the last snapshot <= t and rolls the accumulators forward
over the trades in between (no transition happens between snapshots by construction).
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from nqbot.features.session_tape import MIN_NS, NS, Bars, Tape

CEILING, FLOOR = "ceiling", "floor"
NODE, CONTAINER, CHART, SINGLE_PRINT, SWEEP = "node", "container", "chart", "single_print", "sweep"
KINDS = (NODE, CONTAINER, CHART, SINGLE_PRINT, SWEEP)
TRADE_CROSS = (NODE, CONTAINER)
BAR_CROSS = (CHART, SINGLE_PRINT)
MAX_EVENTS_PER_TRADE = 8    # a guard: one trade can at most open, arm, cross back and reclaim


def up(role: str) -> int:
    """The breaking direction of a role: a ceiling breaks upward (+1), a floor downward (-1)."""
    return 1 if role == CEILING else -1


def opposite(role: str) -> str:
    return FLOOR if role == CEILING else CEILING


def beyond(price, lo, hi, d: int):
    """How far past the band in direction d (> 0 = beyond; F0.3: strictly above the upper edge / below the lower)."""
    return price - hi if d > 0 else lo - price


@dataclass(frozen=True)
class Params:
    rearm_dist: float
    flip_margin: float
    flip_bar_minutes: int
    sweep_max_depth: float
    sweep_reclaim_bars: int

    @classmethod
    def from_features(cls, f: dict, key: str, scale: float = 1.0) -> Params:
        """NQ values, or ES equivalents with scale = ES ÷ NQ at the open (F0.11)."""
        return cls(f["rearm_dist"] * scale, f["flip_margin"].get(key, 0) * scale, f["flip_bar_minutes"],
                   f["sweep_max_depth"] * scale, f["sweep_reclaim_bars"])


@dataclass
class Acc:
    """Trade-to-trade stretches beyond in direction d, with volume, delta and normal (RTH part) while beyond."""
    d: int
    last_ts: int
    last_beyond: bool
    depth: float = 0.0
    sec: float = 0.0
    vol_rth: float = 0.0
    delta_rth: float = 0.0
    normal_rth: float = 0.0
    reentries: int = 0
    settled_1m: int = 0
    settled_5m: int = 0


@dataclass
class State:
    role: str | None = None
    orig_role: str | None = None       # the role before the open episode
    crossed: bool = False              # crossed and not yet flipped or reclaimed
    broken_back: bool = False          # the provisional role was crossed back (step 5)
    armed: bool = False                # armed in its current confirmed role (after a flip: once rearm_dist away)
    prov_armed: bool = False           # armed in the provisional role
    flip_count: int = 0
    reclaimed: bool = False
    cross_count: int = 0
    t_cross: int | None = None
    t_flip: int | None = None
    episode: Acc | None = None         # the open cross episode
    unresolved: dict | None = None     # sweep kind: {'d', 't0', 'minute0', 'acc'}
    last_sweep: dict | None = None     # {'t', 'depth', 'sec'}
    in_block: bool = False

    def copy(self) -> State:
        return replace(self, episode=None if self.episode is None else replace(self.episode),
                       unresolved=None if self.unresolved is None else
                       {**self.unresolved, "acc": replace(self.unresolved["acc"])},
                       last_sweep=None if self.last_sweep is None else dict(self.last_sweep))


@dataclass
class LevelState:
    level_id: str
    kind: str
    tape: Tape
    band: Any                           # bands.StepBand / bands.TradeBand
    params: Params
    bars_1m: Bars
    bars_flip: Bars
    normals: Any = None                 # rvol.RthNormals (for xb_vol_rel / xb_delta_rel), or None
    blocks: list = field(default_factory=list)   # [(start_ns, end_ns)] mapping-dislocation blocks (node kind)
    end_ns: int | None = None           # the session end (default: the tape's last trade + 1 ns)
    params_from: list = field(default_factory=list)  # [(t_ns, Params)]: params in force from t (ES levels before the
                                                     # open use the previous session's ES/NQ ratio, F0.11)
    events: list = field(default_factory=list)   # (t_ns, name) transitions, for reports and tests
    _snaps_t: list = field(default_factory=list)
    _snaps: list = field(default_factory=list)

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unknown level kind {self.kind!r}")
        self._base_params = self.params

    def _set_params(self, t: int) -> None:
        p = self._base_params
        for t0, q in self.params_from:
            if t0 <= t:
                p = q
        self.params = p

    # --- the pass ----------------------------------------------------------------------------------------------
    def run(self) -> LevelState:
        tape = self.tape
        start = int(self.band.start_ns)
        end = self.end_ns if self.end_ns is not None else (int(tape.ts[-1]) + 1 if len(tape) else start)
        s = State(in_block=self._blocked(start))
        i = tape.idx_before(start)
        self._snap(start, i, s)
        cuts = sorted({b for blk in self.blocks for b in blk if start < b < end})
        m = start - start % MIN_NS + MIN_NS
        boundaries = sorted(set(range(m, end, MIN_NS)) | set(cuts) | {end})
        for b in boundaries:
            j = tape.idx_before(b)
            if self.params_from:
                self._set_params(int(tape.ts[i]) if j > i else b - 1)
            i = self._trades(s, i, j)
            if b in cuts:
                self._block_edge(s, b, i)
            if b % MIN_NS == 0:
                if self.params_from:
                    self._set_params(b - 1)        # a bar is judged with the params in force while it formed
                self._bar_close(s, b)
            self._snap(b, i, s)
        return self

    def _blocked(self, t: int) -> bool:
        return self.kind == NODE and any(a <= t < z for a, z in self.blocks)

    def _snap(self, t: int, i: int, s: State) -> None:
        self._snaps_t.append(t)
        self._snaps.append((i, s.copy()))

    def _event(self, s: State, t: int, i: int, name: str) -> None:
        self.events.append((t, name))
        self._snap(t, i, s)

    # --- trades ------------------------------------------------------------------------------------------------
    def _trades(self, s: State, i: int, j: int) -> int:
        """Process trades [i, j); returns j. Inside a dislocation block nothing changes (F2.6 step 6)."""
        if j <= i or s.in_block:
            return j
        tape = self.tape
        lo, hi = self.band.at_trades(i, j)
        p = tape.price[i:j]
        k, same = 0, 0
        n = j - i
        while k < n:
            e, name = self._first_event(s, p[k:], lo[k:], hi[k:])
            stop = n if e is None else k + e
            self._accumulate(s, i + k, i + stop, lo[k:stop], hi[k:stop])
            if e is None:
                break
            same = same + 1 if e == 0 else 0
            if same > MAX_EVENTS_PER_TRADE:
                raise RuntimeError(f"{self.level_id}: no progress at trade {i + stop}")
            idx = i + stop
            self._apply(s, name, int(tape.ts[idx]), idx, float(p[stop]), float(lo[stop]), float(hi[stop]))
            self._event(s, int(tape.ts[idx]), idx, name)
            k = stop
        return j

    def _first_event(self, s: State, p, lo, hi) -> tuple[int | None, str | None]:
        """The first trade (index into p) at which a trade-driven transition happens, and which one."""
        rd = self.params.rearm_dist
        cands: list[tuple[np.ndarray, str]] = []
        ok = ~np.isnan(lo)
        if s.role is None:
            cands += [(p >= hi + rd, "role_floor"), (p <= lo - rd, "role_ceiling")]
        elif not s.crossed:
            d = up(s.role)
            if self.kind in TRADE_CROSS:
                cands.append((beyond(p, lo, hi, d) > 0, "cross"))
            elif self.kind == SWEEP:
                if s.unresolved is None:
                    cands.append((beyond(p, lo, hi, d) > 0, "break_start"))
                else:
                    cands.append((beyond(p, lo, hi, d) > self.params.sweep_max_depth, "cross"))
            if not s.armed:
                cands.append((beyond(p, lo, hi, -d) >= rd, "armed"))
        else:
            d = up(s.orig_role)    # the crossed side
            if s.broken_back:
                # Q8 (owner 2026-10-31): a reclaim only once the cross back is confirmed by the kind's own rule
                cands.append((beyond(p, lo, hi, -d) >= rd, "reclaim"))
                if self.kind in TRADE_CROSS:      # resume by the same rule on the crossed side
                    cands.append((beyond(p, lo, hi, d) > 0, "resume"))
                elif self.kind == SWEEP:
                    if s.unresolved is None:
                        cands.append((beyond(p, lo, hi, d) > 0, "resume_start"))
                    else:
                        cands.append((beyond(p, lo, hi, d) > self.params.sweep_max_depth, "resume"))
                # chart levels and single prints resume on a settled 1-minute bar (bar close)
            else:
                if self.kind in TRADE_CROSS:
                    cands.append((beyond(p, lo, hi, -d) > 0, "cross_back"))
                elif self.kind == SWEEP:
                    if s.unresolved is None:
                        cands.append((beyond(p, lo, hi, -d) > 0, "back_start"))
                    else:
                        cands.append((beyond(p, lo, hi, -d) > self.params.sweep_max_depth, "cross_back"))
                if not s.prov_armed:
                    cands.append((beyond(p, lo, hi, d) >= rd, "prov_armed"))
        best, name = None, None
        for mask, nm in cands:
            hit = np.flatnonzero(mask & ok)
            if len(hit) and (best is None or hit[0] < best):
                best, name = int(hit[0]), nm
        return best, name

    def _apply(self, s: State, name: str, t: int, idx: int, price: float, lo: float, hi: float) -> None:
        if name in ("role_floor", "role_ceiling"):
            s.role = FLOOR if name == "role_floor" else CEILING
            s.orig_role, s.armed = s.role, True
        elif name == "armed":
            s.armed = True
        elif name == "cross":
            self._open_episode(s, t, idx, at_trade=True)
        elif name == "break_start":
            s.unresolved = self._unresolved(up(s.role), t, idx)
        elif name == "back_start":
            s.unresolved = self._unresolved(-up(s.orig_role), t, idx)
        elif name == "cross_back":
            s.broken_back, s.prov_armed, s.unresolved = True, False, None
        elif name == "resume_start":
            s.unresolved = self._unresolved(up(s.orig_role), t, idx)
        elif name == "resume":
            s.broken_back, s.unresolved = False, None
        elif name == "prov_armed":
            s.prov_armed = True
        elif name == "reclaim":
            s.role = s.orig_role
            s.crossed = s.broken_back = s.prov_armed = False
            s.reclaimed, s.armed, s.episode, s.unresolved = True, True, None, None
        else:
            raise ValueError(name)

    def _unresolved(self, d: int, t: int, idx: int) -> dict:
        return {"d": d, "t0": t, "minute0": t - t % MIN_NS, "acc": Acc(d, t, False)}

    def _open_episode(self, s: State, t: int, idx: int | None, at_trade: bool) -> None:
        """A cross of the current role: a new episode in the opposite (provisional) role."""
        s.orig_role = s.role
        s.role = opposite(s.role)
        s.crossed, s.broken_back, s.prov_armed, s.armed, s.reclaimed = True, False, False, False, False
        s.cross_count += 1
        s.t_cross, s.unresolved = t, None
        last = None if at_trade else self.tape.last_price_at(t)
        b = self.band.at(t)
        lb = (not at_trade and last is not None and b is not None and beyond(last, *b, up(s.orig_role)) > 0)
        s.episode = Acc(up(s.orig_role), t, bool(lb))

    def _accumulate(self, s: State, i: int, j: int, lo: np.ndarray, hi: np.ndarray) -> None:
        if j <= i:
            return
        ts = self.tape.ts[i:j]
        for acc, track_depth in ((s.episode, True), (s.unresolved["acc"] if s.unresolved else None, True)):
            if acc is None:
                continue
            p = self.tape.price[i:j]
            bd = beyond(p, lo, hi, acc.d)
            bmask = np.nan_to_num(bd, nan=-1.0) > 0
            prev_ts = np.r_[acc.last_ts, ts[:-1]]
            prev_b = np.r_[acc.last_beyond, bmask[:-1]]
            dur = (ts - prev_ts) / NS
            acc.sec += float(dur[prev_b].sum())
            if self.normals is not None:
                acc.normal_rth += float(self.normals.between(prev_ts[prev_b], ts[prev_b]).sum())
                rth = self.normals.in_rth(ts) & bmask
                acc.vol_rth += float(self.tape.size[i:j][rth].sum())
                acc.delta_rth += float((self.tape.side[i:j] * self.tape.size[i:j])[rth].sum()) * acc.d
            acc.reentries += int((prev_b & ~bmask).sum())
            if track_depth and bmask.any():
                acc.depth = max(acc.depth, float(bd[bmask].max()))
            acc.last_ts, acc.last_beyond = int(ts[-1]), bool(bmask[-1])

    # --- bar closes --------------------------------------------------------------------------------------------
    def _settled(self, bars: Bars, k: int, d: int, margin: float) -> bool:
        """F0.5: bar k's open beyond the band (as of its open) and its close beyond (as of its close), by margin."""
        t_open, t_close = int(bars.start[k]), int(bars.start[k]) + bars.length_ns
        b0, b1 = self.band.at(t_open), self.band.at(t_close)
        if b0 is None or b1 is None:
            return False
        return beyond(bars.open[k], *b0, d) > margin and beyond(bars.close[k], *b1, d) > margin

    def _bar_close(self, s: State, t: int) -> None:
        if s.in_block:
            return
        k = self._bar_ending(self.bars_1m, t)
        i = self.tape.idx_before(t)
        if k is not None:
            if s.episode is not None and t > s.t_cross and self._settled(self.bars_1m, k, s.episode.d, 0.0):
                s.episode.settled_1m += 1
            if s.role is not None and not s.crossed and self.kind in BAR_CROSS and self._settled(
                    self.bars_1m, k, up(s.role), 0.0):
                self._open_episode(s, t, None, at_trade=False)
                self._event(s, t, i, "cross")
            elif s.crossed and not s.broken_back and self.kind in BAR_CROSS and self._settled(
                    self.bars_1m, k, -up(s.orig_role), 0.0):
                s.broken_back, s.prov_armed = True, False
                self._event(s, t, i, "cross_back")
                # the rearm_dist move may come in the confirming bar itself (Q8): then the reclaim is known at its close
                if self._reached(int(self.bars_1m.first[k]), int(self.bars_1m.last[k]) + 1, -up(s.orig_role)):
                    self._apply(s, "reclaim", t, i, 0.0, 0.0, 0.0)
                    self._event(s, t, i, "reclaim")
            elif s.crossed and s.broken_back and self.kind in BAR_CROSS and self._settled(
                    self.bars_1m, k, up(s.orig_role), 0.0):
                s.broken_back = False
                self._event(s, t, i, "resume")
        if self.kind == SWEEP and s.unresolved is not None:
            self._sweep_close(s, t, k, i)
        kf = self._bar_ending(self.bars_flip, t)
        if kf is not None and s.episode is not None and t > s.t_cross:   # bars closing after the cross
            m = self.params.flip_margin
            if self._settled(self.bars_flip, kf, s.episode.d, m):
                s.episode.settled_5m += 1
                if s.crossed:
                    s.crossed, s.broken_back, s.prov_armed, s.armed = False, False, False, False
                    s.flip_count, s.t_flip, s.unresolved = s.flip_count + 1, t, None
                    self._event(s, t, i, "flip")

    def _sweep_close(self, s: State, t: int, k: int | None, i: int) -> None:
        u = s.unresolved
        n_min = (t - u["minute0"]) // MIN_NS - 1     # 0 = the break bar's minute
        if n_min < 0:
            return
        b = self.band.at(t)
        resuming = s.crossed and s.broken_back      # a crossed-side move of a broken-back level (Q8)
        if k is not None and b is not None and beyond(self.bars_1m.close[k], *b, u["d"]) <= 0:
            if not resuming:
                s.last_sweep = {"t": t, "depth": u["acc"].depth, "sec": u["acc"].sec + (
                    (t - u["acc"].last_ts) / NS if u["acc"].last_beyond else 0.0)}
            s.unresolved = None
            self._event(s, t, i, "resume_swept" if resuming else "swept_back" if s.crossed else "swept")
            return
        if n_min >= self.params.sweep_reclaim_bars:
            s.unresolved = None
            if not s.crossed:
                self._open_episode(s, t, None, at_trade=False)
                self._event(s, t, i, "cross")
            elif resuming:
                s.broken_back = False
                self._event(s, t, i, "resume")
            else:
                s.broken_back, s.prov_armed = True, False
                self._event(s, t, i, "cross_back")
                if u["acc"].depth >= self.params.rearm_dist:   # the rearm_dist move inside the window (Q8)
                    self._apply(s, "reclaim", t, i, 0.0, 0.0, 0.0)
                    self._event(s, t, i, "reclaim")

    def _reached(self, i: int, j: int, d: int) -> bool:
        """Did a trade in [i, j) reach `rearm_dist` beyond the band in direction d (band as of each trade)?"""
        if j <= i:
            return False
        lo, hi = self.band.at_trades(i, j)
        bd = np.nan_to_num(beyond(self.tape.price[i:j], lo, hi, d), nan=-np.inf)
        return bool(bd.max() >= self.params.rearm_dist)

    @staticmethod
    def _bar_ending(bars: Bars, t: int) -> int | None:
        """The bar (with trades) whose close is exactly t."""
        k = int(np.searchsorted(bars.start, t - bars.length_ns))
        return k if k < len(bars) and int(bars.start[k]) == t - bars.length_ns else None

    # --- blocks (node kind) ------------------------------------------------------------------------------------
    def _block_edge(self, s: State, b: int, i: int) -> None:
        starting = any(a == b for a, _ in self.blocks)
        ending = any(z == b for _, z in self.blocks)
        if ending and not any(a <= b < z for a, z in self.blocks):
            s.in_block = False
            last, band = self.tape.last_price_at(b), self.band.at(b)
            if last is not None and band is not None and s.role is not None:
                p, lo, hi = np.array([last]), np.array([band[0]]), np.array([band[1]])
                for _ in range(MAX_EVENTS_PER_TRADE):
                    e, name = self._first_event(s, p, lo, hi)
                    if e is None:
                        break
                    self._apply(s, name, b, i, last, band[0], band[1])
                    self._event(s, b, i, name + "_at_block_end")
            for acc in (s.episode, s.unresolved["acc"] if s.unresolved else None):
                if acc is not None:   # nothing accumulates inside the block
                    acc.last_ts = b
                    acc.last_beyond = bool(last is not None and band is not None and beyond(last, *band, acc.d) > 0)
        if starting:
            s.in_block = True

    # --- queries -----------------------------------------------------------------------------------------------
    def state_at(self, t_ns: int) -> State:
        k = bisect_right(self._snaps_t, t_ns) - 1
        if k < 0:
            return State()
        i, snap = self._snaps[k]
        s = snap.copy()
        j = self.tape.idx_at(t_ns)
        if j > i and not s.in_block:
            lo, hi = self.band.at_trades(i, j)
            self._accumulate(s, i, j, lo, hi)
        return s

    def at(self, t_ns: int, atr: float | None = None, em: float | None = None) -> dict:
        """The level's state and cross features as of t (F2.6, F2.6b); all null inside a dislocation block."""
        s = self.state_at(t_ns)
        out: dict[str, Any] = {k: None for k in OUTPUTS}
        if s.in_block or self.band.at(t_ns) is None:
            return out
        last = self.tape.last_price_at(t_ns)
        band = self.band.at(t_ns)
        unresolved = s.unresolved is not None
        if self.kind in BAR_CROSS and last is not None and s.role is not None:
            d = up(s.role) if not s.crossed else (-up(s.orig_role) if not s.broken_back else up(s.orig_role))
            unresolved = d is not None and beyond(last, *band, d) > 0
        out.update(role=s.role, crossed=s.crossed, provisional_role=s.crossed, flip_count=s.flip_count,
                   reclaimed=s.reclaimed, cross_count_today=s.cross_count, armed=s.armed or (s.crossed and s.prov_armed),
                   unresolved_break=unresolved,
                   active=s.role is not None and (not s.crossed or (s.prov_armed and not s.broken_back)),
                   minutes_since_cross=None if s.t_cross is None else (t_ns - s.t_cross) / MIN_NS,
                   minutes_since_flip=None if s.t_flip is None else (t_ns - s.t_flip) / MIN_NS)
        if s.last_sweep is not None:
            out.update(last_sweep_t=s.last_sweep["t"], sweep_depth=s.last_sweep["depth"],
                       sweep_seconds_beyond=s.last_sweep["sec"])
        a = s.episode
        if a is not None and (s.crossed or s.flip_count > 0):
            open_sec = (t_ns - a.last_ts) / NS if a.last_beyond else 0.0
            normal = a.normal_rth
            if self.normals is not None and a.last_beyond:
                normal += float(self.normals.between(np.array([a.last_ts]), np.array([t_ns])).sum())
            out.update(xb_depth=a.depth, xb_sec_beyond=a.sec + open_sec, xb_settled_1m=a.settled_1m,
                       xb_settled_5m=a.settled_5m, xb_reentries=a.reentries,
                       xb_vol_rel=a.vol_rth / normal if self.normals is not None and normal > 0 else None,
                       xb_delta_rel=a.delta_rth / normal if self.normals is not None and normal > 0 else None)
            out["xb_depth_atr"] = a.depth / atr if atr else None
            out["xb_depth_em"] = a.depth / em if em else None
        return out


OUTPUTS = ("role", "crossed", "provisional_role", "flip_count", "reclaimed", "cross_count_today", "armed",
           "unresolved_break", "active", "minutes_since_cross", "minutes_since_flip", "last_sweep_t", "sweep_depth",
           "sweep_seconds_beyond", "xb_depth", "xb_depth_atr", "xb_depth_em", "xb_vol_rel", "xb_delta_rel",
           "xb_sec_beyond", "xb_settled_1m", "xb_settled_5m", "xb_reentries", "xb_node_pctking_chg",
           "xb_node_sign_flipped")
