"""F3.1 VWAP and σ bands (SPEC §5.3; feature_spec F3.1).

For anchor A ∈ {18:00 ET (the CME-day start), 09:30 ET}, over the session's front-contract trades with ts_event >= A:
VWAP = Σpv ÷ Σv; σ = √(Σvp² ÷ Σv − VWAP²) (volume-weighted standard deviation); bands VWAP ± 1σ and ± 2σ. It
updates on every trade (side N trades count, F0.1 Q-sideN). Each line is its own registry level (F5.1; level-type key
`vwap` for the line, `vwap_band` for the bands, F5.14) with band = line ± `chart_level_half_width[key]`.
Prices are taken relative to the anchor's first trade before the sums, so the σ subtraction keeps its precision.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from nqbot.features.bands import TradeBand
from nqbot.features.session_tape import Tape

ANCHORS = {"vwap18": "18:00", "vwap0930": "09:30"}
LINES = {0: "", 1: "_up1", 2: "_up2", -1: "_dn1", -2: "_dn2"}   # sub-type suffix per σ multiple


def sub_type(anchor: str, k: int) -> str:
    return f"{anchor}{LINES[k]}"


def key_of(k: int) -> str:
    return "vwap" if k == 0 else "vwap_band"


@dataclass
class Vwap:
    tape: Tape
    start_ns: int
    vwap: np.ndarray     # aligned with tape trades; NaN before the anchor
    sigma: np.ndarray

    @classmethod
    def build(cls, tape: Tape, anchor_ns: int) -> Vwap:
        n = len(tape)
        vw = np.full(n, np.nan)
        sg = np.full(n, np.nan)
        i0 = tape.idx_before(anchor_ns)
        if i0 < n:
            p, v = tape.price[i0:], tape.size[i0:]
            ref = p[0]
            q = p - ref
            sv = np.cumsum(v)
            m = np.cumsum(v * q) / sv
            var = np.cumsum(v * q * q) / sv - m * m
            vw[i0:] = m + ref
            sg[i0:] = np.sqrt(np.maximum(var, 0.0))
        return cls(tape, anchor_ns, vw, sg)

    def at(self, t_ns: int) -> tuple[float, float] | None:
        """(VWAP, σ) as of t (after the last trade <= t); None before the first trade after the anchor."""
        i = self.tape.idx_at(t_ns)
        if t_ns < self.start_ns or i == 0 or np.isnan(self.vwap[i - 1]):
            return None
        return float(self.vwap[i - 1]), float(self.sigma[i - 1])

    def line(self, k: int) -> np.ndarray:
        return self.vwap + k * self.sigma

    def line_at(self, k: int, t_ns: int) -> float | None:
        a = self.at(t_ns)
        return None if a is None else a[0] + k * a[1]

    def band(self, k: int, half_width: float) -> TradeBand:
        return TradeBand(self.tape, self.line(k), half_width, self.start_ns)
