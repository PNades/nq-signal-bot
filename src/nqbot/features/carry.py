"""Multi-day state carried from one session to the next (SPEC §3.2 Roll days, §11 Expected run time: the pass-1
checkpoint; feature_spec F0.4).

Per instrument root (NQ, ES): the completed RTH sessions (ATR, the EM close, IB widths), the previous CME day's and RTH
highs/lows, the week's running and the prior week's highs/lows, the weekly profile (one contract only), the previous
session's profile (both `prev_profile_anchor` windows, so the step-13 variant needs no recompute), unfilled single
prints and retest candidates. JSON-serialisable (`to_json` / `from_json`).

- **Roll day** (F0.4): every carried level and statistic is shifted by `roll_spread` (new − old contract); the weekly
  profile restarts on the new contract (owner 2026-10-24: one contract only); the previous-session profile is shifted.
- **Damage** (§3.2, `damage.py`): a session the rule drops hands no prior-day high/low, prior RTH high/low or
  previous-session profile to the next session.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from nqbot.features.session_tape import TICK

RTH_KEEP = 40   # completed RTH sessions kept (>= atr_period + 1 and ib_avg_sessions; a storage bound, not a lookback)


@dataclass
class CarryState:
    root: str
    session: str | None = None           # the last session folded in (ISO date)
    contract: str | None = None
    rth: list = field(default_factory=list)            # [{'day', 'high', 'low', 'close', 'ib_width'}], oldest first
    prev_day: dict | None = None         # {'day', 'high', 'low'} of the last session's CME day
    prev_rth: dict | None = None         # {'day', 'high', 'low'}
    week_start_ns: int | None = None
    week: dict | None = None             # running {'high', 'low'} of the current week (through `session`)
    prev_week: dict | None = None        # {'high', 'low'} of the previous week
    weekly_anchor_ns: int | None = None  # Sunday 18:00, or the roll day's 18:00
    weekly_volume: dict = field(default_factory=dict)  # tick (str) -> volume, current week, current contract
    prev_profile: dict | None = None     # {'day', 'globex_1800_1700': {...}, 'rth_0930_1600': {...}}
    single_prints: list = field(default_factory=list)  # tpo.Run.to_json()
    retest_candidates: list = field(default_factory=list)  # [{'price', 'type', 'session'}]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> CarryState:
        return cls(**d)

    # --- the roll ----------------------------------------------------------------------------------------------
    def shift(self, spread: float) -> None:
        """F0.4: shift every carried price by the roll spread (new − old). The weekly profile is restarted by the
        caller (one contract only); its volume is not merged across contracts."""
        if spread == 0:
            return
        for r in self.rth:
            for k in ("high", "low", "close"):
                r[k] += spread
        for d in (self.prev_day, self.prev_rth, self.week, self.prev_week):
            if d is not None:
                d["high"] += spread
                d["low"] += spread
        steps = round(spread / TICK)
        if abs(steps * TICK - spread) > 1e-9:
            raise ValueError(f"roll spread {spread} is not a whole number of ticks")
        if self.prev_profile is not None:
            for anchor, prof in self.prev_profile.items():
                if isinstance(prof, dict) and "volume" in prof:
                    prof["volume"] = {str(int(k) + steps): v for k, v in prof["volume"].items()}
                    for k in ("poc", "vah", "val", "high", "low"):
                        if prof.get(k) is not None:
                            prof[k] += spread
        for sp in self.single_prints:
            sp["bin_lo"] = [x + spread for x in sp["bin_lo"]]
        for c in self.retest_candidates:
            c["price"] += spread

    def drop_handed_levels(self) -> None:
        """§3.2: a dropped session hands no prior-day / prior-RTH levels or previous-session profile."""
        self.prev_day = self.prev_rth = self.prev_profile = None

    def rth_sessions(self) -> list[dict]:
        return list(self.rth)

    def prev_close(self) -> float | None:
        return self.rth[-1]["close"] if self.rth else None

    def ib_widths(self, n: int) -> list[float]:
        w = [r["ib_width"] for r in self.rth if r.get("ib_width") is not None]
        return w[-n:]

    def session_date(self) -> date | None:
        return None if self.session is None else date.fromisoformat(self.session)
