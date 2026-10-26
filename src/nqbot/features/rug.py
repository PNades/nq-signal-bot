"""F0.10 strike step and F2.13 rug / reverse rug (owner 2026-10-05; clarified 2026-10-25, Q12/Q13/Q27).

- A map's **strike step** on a board = the most common gap between consecutive listed strikes, both within
  +-`regime_band`% of spot, per board (mixed 5/10 spacing can give 5 on one board and 10 on the next). A tie between gaps
  goes to the smaller gap (owner 2026-10-26, SPEC §13 Q-strikestep-tie).
- **Rug** at a relevant positive node P with spot below it: the negative strikes below P within `rug_gap_strikes` x step,
  any size (a value of exactly 0 is not negative), plus the negative strikes directly below them in adjacent strike-list
  positions (F0.10). **Reverse rug:** the mirror above P with spot above P.
"""

from __future__ import annotations

from collections import Counter

GAP_DECIMALS = 6  # float gaps are compared rounded (strikes are quoted to the cent)


def strike_step(strikes, spot: float, band_pct: float) -> float | None:
    near = sorted(k for k in strikes if abs(k - spot) <= band_pct / 100.0 * spot)
    gaps = Counter(round(b - a, GAP_DECIMALS) for a, b in zip(near, near[1:]))
    if not gaps:
        return None
    top = max(gaps.values())
    return min(g for g, n in gaps.items() if n == top)


def rug_partners(values: dict, p: float, spot: float, step: float | None, gap_strikes: float,
                 relevant: bool) -> list:
    """Partner strikes of a rug (spot < P, partners below) or reverse rug (spot > P, above) at P; [] if none."""
    if not relevant or values.get(p, 0) <= 0 or step is None or spot == p:
        return []
    ks = sorted(values)
    i = ks.index(p)
    side = ks[:i][::-1] if spot < p else ks[i + 1:]  # walk away from P, nearest first
    reach = gap_strikes * step + 10 ** -GAP_DECIMALS
    near = [k for k in side if abs(k - p) <= reach and values[k] < 0]
    if not near:
        return []
    out = list(near)
    j = side.index(near[-1]) + 1
    while j < len(side) and values[side[j]] < 0:  # contiguous negatives beyond the last one in reach
        out.append(side[j])
        j += 1
    return out
