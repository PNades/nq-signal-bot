"""The provisional structure model's input columns (SPEC §7.1 structure model, §12 step 13: "the §7.1 structure inputs
minus the day-type input"; owner 2026-11-14, SPEC §13 Q-step13c).

The touch rows (`touches/<D>.parquet`, `SessionFeatures.row(event, touch)`) hold every structure-model input plus
identity, time and price columns. The model uses every column of the rows except:
- identity and time: the ids, the day, `ts` / `t`, the touching trade's index, the founding members and `tradeable`
  (a filter, not an input);
- absolute prices (not comparable across days; the rows carry the same information as distances): the touch and last
  price, the zone's and the golden zone's edges, the fib prices, the VWAP values and each map's King strike;
- `dt_*`: the day-type model's own inputs at t (the structure model gets the day type only as P(trend), left out at
  step 13);
- `gp_*`: the gameplan features (off until the step-20 gameplan check, §5.6);
- columns that are null on every row (no information).
String columns are categorical inputs; the founding-member list is never an input. No outcome column is in the rows:
labels live in `outcomes/` and `events/` and are joined by `event_id` only for the label.
"""

from __future__ import annotations

from typing import Iterable

ID_COLS = frozenset({"event_id", "zone_id", "touch_group_id", "day", "ts", "t", "touch_idx", "founding_level_ids",
                     "tradeable"})
PRICE_COLS = frozenset({"touch_price", "price", "touch_zone_lo", "touch_zone_hi", "zone_lo", "zone_hi", "vwap18",
                        "vwap0930", "or_shallow_band_lo", "or_shallow_band_hi", "or_deep_band_lo", "or_deep_band_hi",
                        "or_shallow_band", "or_deep_band", "or_fib_prices"})
PRICE_SUFFIXES = ("_king_strike",)
PRICE_PREFIXES = ("or_fib_prices_",)
EXCLUDED_PREFIXES = ("dt_", "gp_")
# columns of outcomes/ and events/ that describe what happened after the touch: never inputs (tested)
OUTCOME_COLS = frozenset({"label", "t_resolved", "how", "extreme", "run_pts", "t_pair_end", "pair_end_label",
                          "damage_drop", "n_firings_c"})


def excluded(name: str) -> bool:
    return (name in ID_COLS or name in PRICE_COLS or name in OUTCOME_COLS or name.endswith(PRICE_SUFFIXES)
            or name.startswith(PRICE_PREFIXES) or name.startswith(EXCLUDED_PREFIXES))


def input_columns(schema: Iterable[tuple[str, str]]) -> tuple[list[str], list[str]]:
    """(numeric inputs, categorical inputs) from (name, kind) pairs, kind one of numeric / bool / string / list / null,
    in schema order."""
    num, cat = [], []
    for name, kind in schema:
        if excluded(name) or kind in ("list", "null"):
            continue
        (cat if kind == "string" else num).append(name)
    return num, cat
