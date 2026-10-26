"""Confirmed King / ceiling / floor by lead time (owner 2026-10-25, SPEC §5.2, feature_spec F2.11 and F2.16).

Every applied board has a **leader** (King: the largest |value| on the board; ceiling / floor: the largest |value| among
that side's candidates; ties nearer spot, then the lower strike, F2.3). A board's leader leads from its `t_apply` until
the next applied board's `t_apply`. The **confirmed** node is the first board's leader; no switch until W of board time
has passed. At each applied board the confirmed node switches when (a) it led less than `node_switch_incumbent_max_share`
(0.25, owner rule, never tuned) of the trailing W and (b) it is not the leader on the current board; it switches to the
strike with the most lead time in that window (ties to the current board's leader; only strikes eligible on the current
board). After a switch the next one needs a full new window. A strike missing from a board counts as not leading.
A board gap longer than `ratio_gap_reset_sec` on a 1-second day restarts the window and keeps the confirmed node. On a
1-minute-resolution day each board leads for its minute, W under 60 s behaves as 60 s, and the challenger must also
lead the current board. Known at the board's `t_apply`, never back-dated.

W = `king_switch_min_sec` for the King, `roll_switch_min_sec` for ceilings and floors (both start 60, tuned at step 13).
"""

from __future__ import annotations

from dataclasses import dataclass, field

ONE_MINUTE_SEC = 60.0  # a 1-minute-resolution board leads for its minute (§3.1 Ingest)


def leader(values: dict[float, float], spot: float, eligible=None) -> float | None:
    """The strike with the largest |value| among `eligible` (default: every strike); ties nearer spot, then lower (F2.3)."""
    keys = [k for k in values if eligible is None or k in eligible]
    if not keys:
        return None
    return min(keys, key=lambda k: (-abs(values[k]), abs(k - spot), k))


@dataclass
class Switch:
    t: float
    old: float
    new: float


@dataclass
class ConfirmedNode:
    """One map, one session, one role (King, ceiling or floor). Times in seconds (any epoch, one clock)."""
    window_sec: float
    incumbent_max_share: float
    gap_reset_sec: float
    confirmed: float | None = None
    window_start: float | None = None
    _segs: list = field(default_factory=list)  # [t_start, t_end or None, leader]
    _last_t: float | None = None

    def reset(self, t: float, node: float | None) -> None:
        """A price switch (F2.11) or a session start: the node is confirmed at once and the window restarts."""
        self.confirmed = node
        self.window_start = t

    def on_board(self, t: float, lead: float | None, eligible, one_minute: bool = False,
                 spot: float = 0.0) -> Switch | None:
        """One applied board at `t` with its leader and the strikes eligible as a switch target on it. A tie that does
        not include the current leader goes to the strike nearer `spot`, then the lower (the F2.3 order)."""
        if self._segs and self._segs[-1][1] is None:
            self._segs[-1][1] = t
        if self.confirmed is None:
            self.reset(t, lead)  # the session's first board, or no node yet (e.g. no ceiling candidate): at once
        elif not one_minute and self._last_t is not None and t - self._last_t > self.gap_reset_sec:
            self.window_start = t
        self._last_t = t
        self._segs.append([t, None, lead])
        w = max(self.window_sec, ONE_MINUTE_SEC) if one_minute else self.window_sec
        self._segs = [s for s in self._segs if s[1] is None or s[1] > t - w]
        if lead is None or lead == self.confirmed or t - self.window_start < w:
            return None
        lead_time = self.lead_times(t, w)
        if lead_time.get(self.confirmed, 0.0) >= self.incumbent_max_share * w:
            return None
        targets = [k for k in eligible if k != self.confirmed]
        if not targets:
            return None
        best = max(lead_time.get(k, 0.0) for k in targets)
        tied = [k for k in targets if lead_time.get(k, 0.0) == best]
        new = lead if lead in tied else min(tied, key=lambda k: (abs(k - spot), k))
        if one_minute and new != lead:
            return None  # on a 1-minute day the challenger must lead the full last minute and the current board
        old = self.confirmed
        self.reset(t, new)
        return Switch(t, old, new)

    def lead_times(self, t: float, w: float) -> dict[float, float]:
        """Seconds each strike led within [t - w, t) (the current board has led 0 s at its own t_apply)."""
        out: dict[float, float] = {}
        lo = t - w
        for t0, t1, k in self._segs:
            end = t if t1 is None else t1
            d = min(end, t) - max(t0, lo)
            if k is not None and d > 0:
                out[k] = out.get(k, 0.0) + d
        return out


def shift_positions(strikes, old: float, new: float) -> int:
    """Signed strike positions (F0.10) from `old` to `new` in the board's sorted strike list (+ = moved up)."""
    if new == old:
        return 0
    lo, hi = sorted((old, new))
    n = sum(1 for s in strikes if lo < s <= hi) if new > old else sum(1 for s in strikes if lo <= s < hi)
    return n if new > old else -n


@dataclass
class KingMigration:
    """F2.16 migration features from confirmed King switches (owner 2026-10-25, Q10/Q28)."""
    changes_today: int = 0
    last_shift_strikes: int | None = None
    stairstep_count: int = 0  # 0 before any change; the first change is 1; a direction change resets it to 1
    last_change_t: float | None = None

    def on_switch(self, sw: Switch, strikes) -> None:
        shift = shift_positions(strikes, sw.old, sw.new)
        same_way = self.last_shift_strikes is not None and (shift > 0) == (self.last_shift_strikes > 0)
        self.stairstep_count = self.stairstep_count + 1 if same_way else 1
        self.changes_today += 1
        self.last_shift_strikes = shift
        self.last_change_t = sw.t


def node_types(values: dict[float, float], relevant: set, spot: float, confirmed_king: float | None) -> dict:
    """F2.5 types against the confirmed King (Q28): king / gatekeeper (relevant, strictly between spot and the confirmed
    King) / significant (any other relevant node) / normal."""
    out = {}
    for k in values:
        if k == confirmed_king:
            out[k] = "king"
        elif k in relevant and confirmed_king is not None and min(spot, confirmed_king) < k < max(spot, confirmed_king):
            out[k] = "gatekeeper"
        elif k in relevant:
            out[k] = "significant"
        else:
            out[k] = "normal"
    return out


def from_config(features: dict, role: str) -> ConfirmedNode:
    """A tracker for `role` ('king', 'ceiling' or 'floor') from features.yaml: the King reads `king_switch_min_sec`,
    ceilings and floors `roll_switch_min_sec` (Q29: two keys, tuned apart at step 13)."""
    key = "king_switch_min_sec" if role == "king" else "roll_switch_min_sec"
    return ConfirmedNode(window_sec=float(features[key]),
                         incumbent_max_share=float(features["node_switch_incumbent_max_share"]),
                         gap_reset_sec=float(features["mapping"]["ratio_gap_reset_sec"]))
