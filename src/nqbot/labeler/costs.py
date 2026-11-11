"""Costs, net RR and the default size at default costs (SPEC §6.1, §6.3, §6.6, §5.5b).

Every label is costed per micro at the default micro commission (the costliest mix, §6.6):
- `risk_per_micro` = (stop_pts + entry_slip_ticks × 0.25) × $2 + commission + `exit_slippage_ticks` × $0.50 (§6.1),
  `stop_pts` from the order's reference price; `entry_slip_ticks` = 0 for a passive limit, `max_entry_slip_ticks` for a
  marketable or stop-limit entry;
- net RR = reward ÷ risk, reward = (|target − reference| − entry_slip_ticks × 0.25) × $2 − commission (§6.3; a target is
  a limit, no exit slippage);
- the fill simulation's size (§5.5b): micros = floor(`default_risk_usd` ÷ risk_per_micro), in NQ-equivalent contracts
  minis + micros ÷ `MICRO_MINI_RATIO`, rounded up (at least 1; a stop too wide for one micro is flagged `too_wide`);
- realized R = (exit − fill) × direction × $2 − commission, ÷ risk_per_micro (per-trade R for the scorer, §6.1); a stop
  or breakeven exit fills at the worse of its price ∓ `exit_slippage_ticks` (§5.5b) and the first trade at or beyond it
  (owner 2026-11-10, SPEC §13 Q-step13b (b) (vii): a gap through the stop never fills better than the actual trade).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from nqbot.features.session_tape import TICK

MNQ_USD_PT = 2.0          # §6.1: MNQ = $2/pt ($0.50/tick)
MNQ_USD_TICK = 0.5
MICRO_MINI_RATIO = 10     # §6.1: 10 MNQ = 1 NQ in risk (the NQ-equivalent size, §5.5b)
PASSIVE = ("limit", "limit_center")


@dataclass(frozen=True)
class Costs:
    commission_micro: float
    exit_slip_ticks: float
    max_entry_slip_ticks: float
    risk_usd: float

    def entry_slip(self, order_type: str) -> float:
        return 0.0 if order_type in PASSIVE else float(self.max_entry_slip_ticks)

    def risk_per_micro(self, stop_pts: float, order_type: str) -> float:
        return (stop_pts + self.entry_slip(order_type) * TICK) * MNQ_USD_PT + self.commission_micro \
            + self.exit_slip_ticks * MNQ_USD_TICK

    def reward_per_micro(self, target_pts: float, order_type: str) -> float:
        return (target_pts - self.entry_slip(order_type) * TICK) * MNQ_USD_PT - self.commission_micro

    def net_rr(self, stop_pts: float, target_pts: float, order_type: str) -> float:
        return self.reward_per_micro(target_pts, order_type) / self.risk_per_micro(stop_pts, order_type)

    def size(self, stop_pts: float, order_type: str) -> tuple[int, int, bool]:
        """(micros, NQ-equivalent contracts for the fill simulation, too_wide)."""
        micros = int(math.floor(self.risk_usd / self.risk_per_micro(stop_pts, order_type) + 1e-9))
        nq = max(1, int(math.ceil(micros / MICRO_MINI_RATIO))) if micros >= 1 else 1
        return micros, nq, micros < 1

    def realized_r(self, fill: float, exit_px: float, direction: int, rpm: float) -> float:
        return ((exit_px - fill) * direction * MNQ_USD_PT - self.commission_micro) / rpm

    def stop_fill(self, stop: float, direction: int, trade_px: float | None = None) -> float:
        """A stop (long: a sell) fills at the stop ∓ `exit_slippage_ticks` against the trade, or at the triggering trade
        (the first at or beyond the stop) if that is worse: a gap never fills better than the actual trade (owner
        2026-11-10, SPEC §13 Q-step13b (b) (vii))."""
        px = stop - direction * self.exit_slip_ticks * TICK
        if trade_px is None:
            return px
        return min(px, trade_px) if direction > 0 else max(px, trade_px)


def be_ticks(commission_micro: float, be_offset_ticks: float) -> int:
    """§6.6 / §3.5 test 2: the MNQ breakeven offset = ceil(commission ÷ tick value) + `be_offset_ticks`."""
    return int(math.ceil(commission_micro / MNQ_USD_TICK - 1e-9) + be_offset_ticks)
