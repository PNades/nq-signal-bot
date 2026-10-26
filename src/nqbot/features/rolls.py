"""F2.11 rolling ceilings and floors (owner 2026-10-05, revised 2026-10-25: SPEC §5.2, feature_spec F2.11).

Per map and session, one `RollSide` for the ceiling (sign +1) and one for the floor (sign -1); the floor is the mirror.
- **Candidates** on a board = strikes above the board's spot (F2.1 two clocks), except that the confirmed ceiling stays
  a candidate (and the ceiling) while the futures price (NQ for Nasdaq maps, ES for S&P) has not traded beyond its
  band's upper edge, even if the board spot is above its strike; a crossed node is excluded until a trade below its
  lower edge.
- **Contests over value** use `ConfirmedNode` (W = `roll_switch_min_sec`). A switch to a closer strike is a roll-down,
  to a farther one with an episode open a roll-back (full at or beyond the anchor, else partial), but only if the new
  strike was a candidate for the whole window W; otherwise (and a farther switch with no episode) a price switch.
- **Price moves change the ceiling at once:** the first futures trade beyond the confirmed ceiling's upper edge pauses
  an active episode (that node is the break node) and re-chooses the ceiling as the current board's largest |value|
  among candidates, the crossed node excluded: a price switch, never a roll. The first trade back below the excluded
  node's lower edge lifts the exclusion (the episode resumes if it was the break node) and re-chooses the same way.
- A new roll-down while paused replaces the episode (anchor = the confirmed ceiling just before it).
- During a mapping-dislocation block (F1.8) the futures-vs-band checks wait; `block_end` judges them at once.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nqbot.features.node_switch import ONE_MINUTE_SEC, ConfirmedNode, leader, shift_positions


@dataclass
class RollEvent:
    t: float
    kind: str  # roll | rollback | partial_rollback | price_switch | pause | resume
    old: float | None = None
    new: float | None = None
    old_shrink_pct: float | None = None
    new_growth_pct: float | None = None


@dataclass
class RollSide:
    sign: int  # +1 ceiling (strikes above spot), -1 floor (below)
    switch: ConfirmedNode
    anchor: float | None = None
    episode: bool = False
    paused: bool = False
    break_node: float | None = None
    excluded: set = field(default_factory=set)
    events: list = field(default_factory=list)
    rolls_today: int = 0
    rollbacks_today: int = 0
    partial_rollbacks_today: int = 0
    roll_breaks_today: int = 0
    _values: dict = field(default_factory=dict)
    _bands: dict = field(default_factory=dict)
    _spot: float = 0.0
    _cand_since: dict = field(default_factory=dict)
    _led_values: dict = field(default_factory=dict)  # the last board on which the confirmed node still led (Q7)
    _one_minute: bool = False

    @property
    def confirmed(self) -> float | None:
        return self.switch.confirmed

    def _beyond(self, price: float, k: float) -> bool:
        lo, hi = self._bands[k]
        return price > hi if self.sign > 0 else price < lo

    def _back(self, price: float, k: float) -> bool:
        lo, hi = self._bands[k]
        return price < lo if self.sign > 0 else price > hi

    def _candidates(self) -> set:
        c = {k for k in self._values if self.sign * (k - self._spot) > 0}
        if self.confirmed in self._values:
            c.add(self.confirmed)  # uncrossed by the futures: stays the ceiling whatever the board spot says
        return c - self.excluded

    def on_board(self, t: float, spot: float, values: dict, bands: dict, one_minute: bool = False) -> RollEvent | None:
        """One applied board: strike -> value, strike -> (lo, hi) band in futures points (R in force)."""
        self._values, self._bands, self._spot, self._one_minute = dict(values), dict(bands), spot, one_minute
        cand = self._candidates()
        self._cand_since = {k: self._cand_since.get(k, t) for k in cand}
        lead = leader(self._values, spot, cand)
        sw = self.switch.on_board(t, lead, cand, one_minute, spot)
        ev = self._classify(t, sw.old, sw.new) if sw else None
        if lead is not None and lead == self.confirmed:
            self._led_values = dict(self._values)
        return ev

    def _classify(self, t: float, old: float | None, new: float) -> RollEvent:
        w = self.switch.window_sec
        w = max(w, ONE_MINUTE_SEC) if self._one_minute else w
        whole_window = self._cand_since.get(new, t) <= t - w
        closer = old is not None and self.sign * (new - old) < 0
        base = self._led_values
        shrink = growth = None
        if old is not None and base.get(old) and old in self._values:
            shrink = (abs(base[old]) - abs(self._values[old])) / abs(base[old])
        if base.get(new) and new in self._values:
            growth = (abs(self._values[new]) - abs(base[new])) / abs(base[new])
        if whole_window and closer:
            if not self.episode or self.paused:
                self.anchor, self.episode, self.paused, self.break_node = old, True, False, None
            self.rolls_today += 1
            kind = "roll"
        elif whole_window and self.episode and not closer:
            if self.sign * (new - self.anchor) >= 0:
                self.episode, self.paused, self.break_node, self.anchor = False, False, None, None
                self.rollbacks_today += 1
                kind = "rollback"
            else:
                self.partial_rollbacks_today += 1
                kind = "partial_rollback"
        else:
            kind = "price_switch"
        ev = RollEvent(t, kind, old, new, shrink, growth)
        self.events.append(ev)
        return ev

    def _rechoose(self, t: float) -> None:
        old = self.confirmed
        new = leader(self._values, self._spot, self._candidates())
        if new != old:
            self.switch.reset(t, new)
            self.events.append(RollEvent(t, "price_switch", old, new))

    def on_trade(self, t: float, price: float, blocked: bool = False) -> None:
        """One futures trade (NQ for Nasdaq maps, ES for S&P) against the bands of the latest applied board."""
        if blocked:
            return  # judged at the block end (F1.8)
        c = self.confirmed
        while c is not None and c in self._bands and self._beyond(price, c):  # one trade may pass several
            if self.episode and not self.paused:
                self.paused, self.break_node = True, c
                self.roll_breaks_today += 1
                self.events.append(RollEvent(t, "pause", c))
            self.excluded.add(c)
            self._rechoose(t)
            c = self.confirmed
        for x in sorted(self.excluded):
            if x in self._bands and self._back(price, x):
                self.excluded.discard(x)
                if self.paused and x == self.break_node:
                    self.paused, self.break_node = False, None
                    self.events.append(RollEvent(t, "resume", x))
                self._rechoose(t)

    def block_end(self, t: float, last_price: float) -> None:
        """The chain's dislocation block ended at t: the paused checks are judged at once against the bands now."""
        self.on_trade(t, last_price)

    @property
    def rolling(self) -> bool:
        return self.episode and not self.paused

    def roll_steps(self) -> int | None:
        """Signed strike positions (F0.10) from the anchor to the confirmed node; + = toward price (Q8)."""
        if not self.episode or self.confirmed is None:
            return None
        return -self.sign * shift_positions(sorted(self._values), self.anchor, self.confirmed)
