"""What the step-13 tuning reads from a months 1-4 build (owner 2026-11-14, SPEC §13 Q-step13c): the events with their
touch rows (`touches/`, `events/`) and, per labels key, each decision point's bootstrap pair (`pairs/`, `fills/`).
Read-only; every table in session order (the plan's sessions), so two builds line up day by day.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from nqbot.tuning import policy as pol
from nqbot.tuning import structure_inputs as si

EVENT_COLS = ("event_id", "touch_group_id", "day", "event_type", "tradeable", "label", "damage_drop",
              "has_qqq_ndxp_node", "has_retested_extreme")
MATCH_COLS = ("event_id", "touch_idx", "direction", "founding_level_ids", "in_golden_zone", "touch_zone_lo",
              "touch_zone_hi")
SUBSET_NAMES = ("golden", "va_edge", "prev_profile", "node", "retest")   # the rows' reported subsets (§7.1 Tuning)
PAIR_COLS = ("pair_id", "fill_id", "decision_id", "event_id", "touch_group_id", "style", "firing_n", "order_type",
             "decision_ts", "tradeable", "stop_types", "target_kind", "net_rr", "rr_ext", "target_pts", "fill_status",
             "t_exit_none", "r_none", "t_exit_fs", "r_fs")


@dataclass
class Build:
    key: str
    base: Path
    days: list[str]
    month_of_day: dict[str, int]
    events: list[dict[str, Any]]            # one per event, in (day, ts, event_id) order
    match_rows: dict[str, list[dict]]       # day -> the touch rows' matching columns
    X: np.ndarray                           # model inputs, row i = events[i]
    feature_names: list[str]
    cat_idx: list[int]
    index: dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        self.index = {e["event_id"]: i for i, e in enumerate(self.events)}


def _kind(dtype) -> str:
    import polars as pl

    if dtype == pl.Null:
        return "null"
    if dtype == pl.String or dtype == pl.Utf8 or dtype == pl.Categorical:
        return "string"
    if isinstance(dtype, pl.List) or dtype == pl.List:
        return "list"
    if dtype == pl.Boolean:
        return "bool"
    return "numeric"


def load_build(env, key: str, days: list[date], month_of: dict[str, int]) -> Build:
    """The events of build `key` over `days` with their model inputs (polars, then numpy float64)."""
    import polars as pl

    from nqbot.checks import feature_build as fb

    base = fb.out_dir(env, key)
    t_frames, e_frames = [], []
    for d in days:
        iso = d.isoformat()
        t_frames.append(pl.read_parquet(base / "touches" / f"{iso}.parquet"))
        e_frames.append(pl.read_parquet(base / "events" / f"{iso}.parquet", columns=list(EVENT_COLS)))
    tch = pl.concat(t_frames, how="diagonal_relaxed")
    evs = pl.concat(e_frames, how="diagonal_relaxed")
    tch = tch.sort(["day", "ts", "event_id"])
    evs = evs.join(tch.select(["event_id", "ts", "in_golden_zone", "n_prev_poc", "n_prev_vah", "n_prev_val"]),
                   on="event_id", how="inner").sort(["day", "ts", "event_id"])
    if evs.height != tch.height:
        raise ValueError(f"build {key}: {tch.height} touch rows but {evs.height} events match them")
    schema = [(c, _kind(tch.schema[c])) for c in tch.columns]
    num, cat = si.input_columns(schema)
    num = [c for c in num if tch[c].null_count() < tch.height]
    cat = [c for c in cat if tch[c].null_count() < tch.height]
    cols = []
    for c in num:
        cols.append(tch[c].cast(pl.Float64).to_numpy())
    for c in cat:   # codes over the build's sorted values (feature values only, never labels)
        vals = sorted(v for v in tch[c].unique().to_list() if v is not None)
        code = {v: float(i) for i, v in enumerate(vals)}
        cols.append(np.array([code.get(v, np.nan) if v is not None else np.nan for v in tch[c].to_list()]))
    X = np.column_stack(cols) if cols else np.zeros((tch.height, 0))
    events = []
    for r in evs.iter_rows(named=True):
        va = (r.get("n_prev_vah") or 0) + (r.get("n_prev_val") or 0)
        events.append({"event_id": r["event_id"], "day": r["day"], "touch_group_id": r["touch_group_id"],
                       "month": month_of[r["day"]], "tradeable": bool(r["tradeable"]),
                       "damaged": bool(r["damage_drop"]), "held": r["label"] == "held",
                       "labelled": r["label"] is not None,
                       "subset": {"golden": bool(r["in_golden_zone"]), "va_edge": va > 0,
                                  "prev_profile": va + (r.get("n_prev_poc") or 0) > 0,
                                  "node": bool(r["has_qqq_ndxp_node"]), "retest": bool(r["has_retested_extreme"])}})
    match_rows: dict[str, list[dict]] = {}
    for r in tch.select(list(MATCH_COLS) + ["day"]).iter_rows(named=True):
        match_rows.setdefault(r["day"], []).append(r)
    return Build(key, base, [d.isoformat() for d in days], dict(month_of), events, match_rows, X, num + cat,
                 list(range(len(num), len(num) + len(cat))))


def load_picks(env, key: str, lkey: str, days: list[date], cache: Path | None = None) -> list[dict[str, Any]]:
    """Every decision point's bootstrap pair per order type under labels `lkey` of build `key`, with its fill's
    removal time (cached as JSON lines: the picks never change for a (build, labels) pair)."""
    import polars as pl

    from nqbot.checks import feature_build as fb

    if cache is not None and cache.exists():
        return [json.loads(x) for x in cache.read_text(encoding="utf-8").splitlines() if x]
    base = fb.out_dir(env, key)
    out: list[dict[str, Any]] = []
    for d in days:
        iso = d.isoformat()
        p = pl.read_parquet(base / "pairs" / lkey / f"{iso}.parquet", columns=list(PAIR_COLS))
        p = p.filter(pl.col("stop_types").str.split(",").list.contains(pol.BOOTSTRAP_STOP) & ~pl.col("rr_ext")
                     & (pl.col("target_kind") != "baseline") & (pl.col("net_rr") >= pol.MIN_RR - pol.EPS))
        f = pl.read_parquet(base / "fills" / lkey / f"{iso}.parquet", columns=["fill_id", "removal_t"])
        p = p.join(f, on="fill_id", how="left")
        for r in pol.pick_bootstrap(p.iter_rows(named=True)).values():
            out.append({**r, "day": iso})
    out.sort(key=lambda r: (r["day"], int(r["decision_ts"]), r["decision_id"], r["order_type"]))
    if cache is not None:
        from nqbot.storage import atomic_write_text

        atomic_write_text(cache, "\n".join(json.dumps(r, default=str) for r in out) + "\n")
    return out
