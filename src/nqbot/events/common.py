"""Shared constants and helpers of the step-13b event builder and labeler.

- Level-type groups (F16.4, `tune_grids_13.detectors.flip_margin.groups`; the build may not read the tuning grids,
  Q-dryrun13 (6), so they are named here and `tests/test_events_common.py` checks they equal the config's).
- Order-price rounding (SPEC §5.1 Rounding): stops away from entry, targets toward entry, limit entries passive.
- Decision ids: `<event_id>-<style><n>` (the order id's prefix, §5.4).
- Price tables are written as float64 (prices, not features; `write_table`).
"""

from __future__ import annotations

import io
import json
import math
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from nqbot.features.chart_levels import SUBTYPE_KEY
from nqbot.features.session_tape import TICK
from nqbot.storage import atomic_write_bytes

GEX_NODES, CHART_LINES, HIGHS_LOWS, GAMEPLAN = "gex_nodes", "chart_lines", "highs_lows", "gameplan"
# F16.4 flip_margin groups (= config tune_grids_13.detectors.flip_margin.groups; tested) plus the gameplan zones
GROUPS: dict[str, tuple[str, ...]] = {
    GEX_NODES: ("qqq_node", "ndxp_node"),
    CHART_LINES: ("vwap", "vwap_band", "poc", "lvn", "single_print", "dark_pool", "em_level", "va_edge"),
    HIGHS_LOWS: ("on_extreme", "pdh_pdl", "prior_rth", "prior_week", "ib", "retested_extreme"),
}
GROUP_OF_KEY = {k: g for g, keys in GROUPS.items() for k in keys}
GROUP_OF_KEY["gameplan"] = GAMEPLAN

STYLES = ("c", "d")          # Phase D entry styles (§5.5): (c) audit / sweep reclaim, (d) plain at the touch
CHART_SOURCE = "chart"       # stop_buffer_floor source of a chart level or container (F11.1)
EPS = 1e-9


def group_of_subtype(sub_type: str | None) -> str | None:
    if sub_type is None:
        return None
    if sub_type in ("qqq_node", "ndxp_node"):
        return GEX_NODES
    return GROUP_OF_KEY.get(SUBTYPE_KEY.get(sub_type, sub_type))


def group_of_level(lv) -> str | None:
    return GROUP_OF_KEY.get(lv.key)


def source_of(lv) -> str:
    """The `stop_buffer_floor` source of a level (F11.1): its map for a QQQ / NDXP node, else chart."""
    if lv is not None and getattr(lv, "rtype", None) == "gex_node" and lv.chain in ("QQQ", "NDXP"):
        return lv.chain
    return CHART_SOURCE


def decision_id(event_id: str, style: str, n: int) -> str:
    return f"{event_id}-{style}{n}"


# --- rounding (SPEC §5.1) ----------------------------------------------------------------------------------------

def _floor(x: float) -> float:
    return math.floor(x / TICK + EPS) * TICK


def _ceil(x: float) -> float:
    return math.ceil(x / TICK - EPS) * TICK


def round_stop(price: float, direction: int) -> float:
    """Away from entry: a long's stop (below) down, a short's (above) up."""
    return _floor(price) if direction > 0 else _ceil(price)


def round_target(price: float, direction: int) -> float:
    """Toward entry: a long's target (above) down, a short's (below) up."""
    return _floor(price) if direction > 0 else _ceil(price)


def round_limit(price: float, direction: int) -> float:
    """A passive limit entry: a buy rounds down, a sell up."""
    return _floor(price) if direction > 0 else _ceil(price)


def round_trigger(price: float, direction: int) -> float:
    """A stop-limit trigger beyond price in the trade direction: a buy stop up, a sell stop down."""
    return _ceil(price) if direction > 0 else _floor(price)


# --- tables ------------------------------------------------------------------------------------------------------

def write_table(path: Path, rows: list[dict], meta: dict[str, Any]) -> None:
    """zstd parquet, float64 kept (prices), the union of the rows' columns (missing = null), written atomically."""
    keys: dict[str, None] = {}
    for r in rows:
        keys.update(dict.fromkeys(r))
    table = pa.Table.from_pylist([{k: r.get(k) for k in keys} for r in rows]) if rows else pa.table({})
    table = table.replace_schema_metadata({b"nqbot": json.dumps(meta, sort_keys=True, default=str).encode()})
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd")
    atomic_write_bytes(path, buf.getvalue())


def read_meta(path: Path) -> dict[str, Any]:
    md = pq.read_schema(path).metadata or {}
    return json.loads(md.get(b"nqbot", b"{}"))
