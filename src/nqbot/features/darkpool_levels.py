"""Dark-pool levels (step 11 part 2; SPEC §3.1 Dark pools, §5.4; feature_spec F4).

**Owner 2026-10-06: not in the first model** (`dp_in_model: false`): development months 1–6 have no dark-pool data, so
no dark-pool level enters the registry or any feature until a later retrain; the code is built and tested now.
Per ticker (QQQ on NQ, SPY on ES, F0.9) and session T: the `dp_top_n` prints by notional of `darkpool.top_prints`
(trade timestamp <= 16:00 ET of T − `dp_lag_sessions`, within `dp_lookback_days`), each a level with identity
(ticker, print timestamp, price), band = price ± `dp_band` (the ticker's $) mapped with the map's R in force (one
row per applied board, like a strike, F1.1), and the chart-level state machine (crossed only on a settled 1-minute
candle, F5.2; `level_state` `chart` kind). Outputs: `dp_notional_rank`, `dp_age_days`, `dp_pct_avg_vol`, `dp_partial`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np

from nqbot.features import darkpool as dpm
from nqbot.features import level_state as ls
from nqbot.features.bands import StepBand
from nqbot.features.boards import MapBoards
from nqbot.features.darkpool import ET
from nqbot.features.session_tape import Bars, Tape

ROOT_OF = {"QQQ": "NQ", "SPY": "ES"}


@dataclass
class DarkPoolLevel:
    level_id: str
    ticker: str
    price: float
    rank: int
    age_days: int
    pct_avg_vol: float | None
    partial: bool
    state: ls.LevelState

    def at(self, t_ns: int, atr: float | None = None, em: float | None = None) -> dict[str, Any]:
        return {"level_id": self.level_id, "type": "dark_pool", "sub_type": f"dp_{self.ticker.lower()}",
                "key": "dark_pool", "instrument": ROOT_OF[self.ticker], "band": self.state.band.at(t_ns),
                "dp_notional_rank": self.rank, "dp_age_days": self.age_days, "dp_pct_avg_vol": self.pct_avg_vol,
                "dp_partial": self.partial, **self.state.at(t_ns, atr, em)}


def build(prints: list[dict], ticker: str, day: date, mb: MapBoards, tape: Tape, features: dict, bars_1m: Bars,
          bars_flip: Bars, is_trading_day, *, end_ns: int, scale: float = 1.0,
          history_start: date | None = None) -> list[DarkPoolLevel]:
    """The session's dark-pool levels of one ticker (QQQ with QQQ's boards, SPY with SPY's)."""
    top = dpm.top_prints(prints, ticker, day, top_n=int(features["dp_top_n"]),
                         lookback_days=int(features["dp_lookback_days"]), lag_sessions=int(features["dp_lag_sessions"]),
                         is_trading_day=is_trading_day, history_start=history_start)
    if len(mb) == 0:
        return []
    half = float(features["dp_band"])
    params = ls.Params.from_features(features, "dark_pool", scale)
    out = []
    for rank, p in enumerate(top.prints, start=1):
        price = float(p["price"])
        lo = (price - half) * mb.r
        hi = (price + half) * mb.r
        band = StepBand(tape, mb.t_apply.astype(np.int64), lo.astype(float), hi.astype(float))
        ts = p["timestamp"]
        lid = f"dp-{ticker}-{ts.astimezone(ET).strftime('%Y%m%dT%H%M%S')}-{price:g}"
        st = ls.LevelState(lid, ls.CHART, tape, band, params, bars_1m, bars_flip, end_ns=end_ns).run()
        pav = p.get("pct_avg_vol")
        out.append(DarkPoolLevel(lid, ticker, price, rank, (day - ts.astimezone(ET).date()).days,
                                 None if pav is None else float(pav), top.dp_partial, st))
    return out
