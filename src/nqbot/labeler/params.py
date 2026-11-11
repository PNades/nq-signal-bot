"""The labeler's settings and its key (owner 2026-11-10, SPEC §13 Q-step13b (1)).

`params()` is the only place the labeler reads configuration; it reads exactly the keys below, so the labeler key
(the feature-build key + these values + the labeler's code) covers everything it uses (`tests/test_labeler.py` records
the reads). Run-time overrides (13c: e.g. one `stop_buffer` or `entry_ttl_sec` value of a grid) are applied on top and
are part of the key.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nqbot.labeler.costs import Costs

SETTING_KEYS = ("order_latency_sec", "default_risk_usd", "max_entry_slip_ticks", "exit_slippage_ticks",
                "default_commission_per_micro_rt", "entry_ttl_sec", "stop_buffer", "stop_buffer_floor",
                "max_target_candidates", "pair_build_min_rr", "pair_ext_min_rr", "pair_ext_max_zones", "baseline_rr",
                "be_trigger", "be_offset_ticks")
FEATURE_KEYS = ("stop_merge_ticks", "tune_grids_13")   # tune_grids_13: only execution.entry_order_type
BE_NAMES = {"none": "none", "first_structure": "fs"}


def be_name(rule) -> str:
    if isinstance(rule, dict):
        return f"r{float(rule['r_multiple']):g}"
    return BE_NAMES[rule]


@dataclass(frozen=True)
class LabelParams:
    latency_ns: int
    costs: Costs
    ttl_sec: dict
    stop_buffer: float
    stop_floor: dict
    max_targets: int
    min_rr: float
    ext_min_rr: float
    ext_max_zones: int
    baseline_rr: float
    be_rules: tuple
    be_offset_ticks: float
    stop_merge_ticks: float
    order_types: dict
    extension: bool = True
    raw: dict = field(default_factory=dict, compare=False)


def params(settings: dict, features: dict, overrides: dict | None = None, extension: bool = True) -> LabelParams:
    s = {k: copy.deepcopy(settings[k]) for k in SETTING_KEYS}
    ot = copy.deepcopy(features["tune_grids_13"]["execution"]["entry_order_type"])
    raw = {**s, "stop_merge_ticks": features["stop_merge_ticks"], "entry_order_types": ot}
    for k, v in (overrides or {}).items():
        top, _, sub = k.partition(".")     # `a.b=v` sets one entry of a mapping (e.g. entry_ttl_sec.c=30)
        if top not in raw or (sub and not (isinstance(raw[top], dict) and sub in raw[top])):
            raise KeyError(f"labeler override {k!r} is not a labeler setting")
        if sub:
            raw[top] = {**raw[top], sub: v}
        else:
            raw[top] = v
    costs = Costs(float(raw["default_commission_per_micro_rt"]), float(raw["exit_slippage_ticks"]),
                  float(raw["max_entry_slip_ticks"]), float(raw["default_risk_usd"]))
    return LabelParams(int(round(float(raw["order_latency_sec"]) * 1e9)), costs, dict(raw["entry_ttl_sec"]),
                       float(raw["stop_buffer"]), dict(raw["stop_buffer_floor"]), int(raw["max_target_candidates"]),
                       float(raw["pair_build_min_rr"]), float(raw["pair_ext_min_rr"]), int(raw["pair_ext_max_zones"]),
                       float(raw["baseline_rr"]), tuple(raw["be_trigger"]), float(raw["be_offset_ticks"]),
                       float(raw["stop_merge_ticks"]), {k: list(v) for k, v in raw["entry_order_types"].items()},
                       extension, raw)


def code_digest() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for f in sorted(root.glob("*.py")) + [root.parent / "clients" / "mbp1.py"]:
        h.update(f.name.encode())
        h.update(f.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()


def label_key(build_key: str, p: LabelParams) -> str:
    blob = json.dumps({"build": build_key, "params": p.raw, "code": code_digest()}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def describe(p: LabelParams) -> dict[str, Any]:
    return dict(p.raw)
