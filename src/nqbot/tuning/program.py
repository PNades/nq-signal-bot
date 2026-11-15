"""The step-13c rows as data (owner 2026-11-08, SPEC §13 Q-step13plan (1); §7.1 Tuning detector parameters;
feature_spec F16.4 search method: one coordinate pass in the listed order, each row at the earlier rows' winners and
the later rows' start values, every grid containing its start value).

Rows 1-11 change the events (each challenger is a months 1-4 build), rows 12-13 the labels only (labeler runs on the
frozen events), row 14 the feature rows only (builds scored by log loss, or as event-changing rows if
`node_relevance_top_n` = 5 won row 9). The grids come from `tune_grids_13` (features.yaml), the start values from the
committed configuration.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

BATCH = 2   # owner 2026-11-12/14: two builds at once (build slots 1 and 2)


@dataclass(frozen=True)
class Row:
    id: str
    name: str
    kind: str                 # build | exec_ttl | exec_stop | logloss
    values: tuple             # the grid, start value included, in grid order
    start: Any
    rule: str                 # best (Q-step13plan (8)) | keep (keep-current)
    mode: str = "combined"    # the scored run: combined (c)+(d) | c (style (c) only)
    subset: str | None = None
    keys: tuple = ()          # the features.yaml keys a value sets (flip_margin: one entry per level type)
    info_modes: tuple = ()    # other style sets reported for information
    note: str = ""
    extra: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def challengers(self) -> list[Any]:
        return [v for v in self.values if v != self.start]

    def batches(self) -> list[list[Any]]:
        ch = self.challengers
        return [ch[i:i + BATCH] for i in range(0, len(ch), BATCH)]

    def build_overrides(self, value: Any) -> dict[str, Any]:
        """The features.yaml overrides that value sets (none for the start value)."""
        if self.kind not in ("build", "logloss") or value == self.start:
            return {}
        if self.name == "flip_margin":
            return {k: value for k in self.keys}
        if self.name == "golden_zone_variants":
            return {"golden_zone_flip": value[0], "golden_zone_redraw_1030": value[1]}
        return {self.keys[0]: value}


def rows(features: dict[str, Any], settings: dict[str, Any]) -> list[Row]:
    g = features["tune_grids_13"]
    det = g["detectors"]
    out: list[Row] = []
    for i, name in enumerate(("zone_proximity", "confluence_tol", "sweep_max_depth"), start=1):
        out.append(Row(f"r{i:02d}", name, "build", tuple(det[name]), features[name], "best", keys=(name,)))
    out.append(Row("r04", "sweep_reclaim_bars", "build", tuple(det["sweep_reclaim_bars"]),
                   features["sweep_reclaim_bars"], "best", mode="c", keys=("sweep_reclaim_bars",),
                   info_modes=("d", "combined"),
                   note="scored on style (c) trades only (§7.1; owner 2026-11-14: a (c)-only run; (d)-only and "
                        "combined reported, flagged if the winner makes (d) clearly worse)"))
    fm = det["flip_margin"]
    for suffix, (grp, types) in zip("abc", fm["groups"].items()):
        start = features["flip_margin"][types[0]]
        if any(features["flip_margin"][t] != start for t in types):
            raise ValueError(f"flip_margin group {grp}: its level types start at different values")
        out.append(Row(f"r05{suffix}", "flip_margin", "build", tuple(fm["values"]), start, "best", keys=tuple(
            f"flip_margin.{t}" for t in types), note=f"group {grp}: {', '.join(types)}", extra={"group": grp}))
    gz = g["golden_zone_variants"]
    out.append(Row("r06", "golden_zone_variants", "build",
                   tuple(itertools.product(gz["golden_zone_flip"], gz["golden_zone_redraw_1030"])),
                   (features["golden_zone_flip"], features["golden_zone_redraw_1030"]), "keep", subset="golden",
                   keys=("golden_zone_flip", "golden_zone_redraw_1030")))
    va = g["vaedge_variants"]
    out.append(Row("r07", "va_edge_sweep", "build", tuple(va["va_edge_sweep"]), features["va_edge_sweep"], "keep",
                   subset="va_edge", keys=("va_edge_sweep",)))
    out.append(Row("r08", "prev_profile_anchor", "build", tuple(va["prev_profile_anchor"]),
                   features["prev_profile_anchor"], "keep", subset="prev_profile", keys=("prev_profile_anchor",)))
    nv = g["node_variants"]
    out.append(Row("r09", "node_relevance_top_n", "build", tuple(nv["node_relevance_top_n"]),
                   features["node_relevance_top_n"], "keep", subset="node", keys=("node_relevance_top_n",)))
    out.append(Row("r10", "node_role_at_formation", "build", tuple(nv["node_role_at_formation"]),
                   features["node_role_at_formation"], "keep", subset="node", keys=("node_role_at_formation",)))
    out.append(Row("r11", "retest_lookback_sessions", "build", tuple(g["retest_variants"]["retest_lookback_sessions"]),
                   features["retest_lookback_sessions"], "keep", subset="retest", keys=("retest_lookback_sessions",)))
    ex = g["execution"]
    ttl = tuple(ex["entry_ttl_sec"])
    start_ttl = {s: settings["entry_ttl_sec"][s] for s in ("c", "d")}
    if start_ttl["c"] != start_ttl["d"]:
        raise ValueError("row 12 labels both styles at one ttl per labeler run: the start ttls of (c) and (d) differ")
    out.append(Row("r12", "entry_order_type x entry_ttl_sec", "exec_ttl", ttl, start_ttl["c"], "best",
                   extra={"order_types": {s: list(ex["entry_order_type"][s]) for s in ("c", "d")},
                          "start_order": {s: settings["entry_order_type"][s] for s in ("c", "d")}},
                   note="each style on its own single-style policy; ties: the start order type, then the ttl "
                        "closest to the start (owner 2026-11-14 reading)"))
    out.append(Row("r13", "stop_buffer x stop floor", "exec_stop", tuple(ex["stop_buffer"]), settings["stop_buffer"],
                   "best", extra={"floors": ["fixed", "vol"]},
                   note="the full 5 x 2 grid (owner 2026-11-14): the best buffer per floor, then vol vs fixed by the "
                        "keep-current rule; scored on the (c) and (d) single-style policies summed"))
    fl = g["features_logloss"]
    out.append(Row("r14a", "roll_switch_min_sec", "logloss", tuple(fl["roll_switch_min_sec"]),
                   features["roll_switch_min_sec"], "keep", keys=("roll_switch_min_sec",)))
    out.append(Row("r14b", "king_switch_min_sec", "logloss", tuple(fl["king_switch_min_sec"]),
                   features["king_switch_min_sec"], "keep", keys=("king_switch_min_sec",)))
    return out


PRE = ["r00:relabel", "r00:check", "r00:selftest", "r00:volfloor"]
END = ["end:summary"]


def chunk_ids(program: list[Row]) -> list[str]:
    out = list(PRE)
    for r in program:
        if r.kind in ("build", "logloss"):
            out += [f"{r.id}:b{i + 1}" for i in range(len(r.batches()))] + [f"{r.id}:labels", f"{r.id}:score"]
        else:
            out += [f"{r.id}:labels", f"{r.id}:score"]
    return out + END


def row_of(program: list[Row], row_id: str) -> Row:
    for r in program:
        if r.id == row_id:
            return r
    raise KeyError(row_id)


def before(program: list[Row], row_id: str) -> list[Row]:
    out = []
    for r in program:
        if r.id == row_id:
            return out
        out.append(r)
    raise KeyError(row_id)


def jsonable(v: Any) -> Any:
    return list(v) if isinstance(v, tuple) else v


def from_json(row: Row, v: Any) -> Any:
    return tuple(v) if isinstance(row.start, tuple) and isinstance(v, list) else v
