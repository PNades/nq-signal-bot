"""The step-13-0 diagnostic switches (owner 2026-11-09; SPEC §13 Q-step13-0).

Five unparameterised step-13 corrections each have a module constant, so the step-13-0 check (`checks/step13.py`) can
switch them off one at a time and prove that the rest of the code reproduces the stored `features_version` 36 build:

- `zones.STEP_TIME_CANDIDATES` (Q-step12a (1): pairs also checked at the partner's step times),
- `bands.SPLIT_JUMPS` (Q-step12b (a): level jumps start a new level),
- `touches.OWN_REARM` (Q-step12b (c): a crossed member's own re-arm),
- `profile.PROFILE_ROW_TICKS` (Q-step12 (4): POC / VAH / VAL on 1-pt rows),
- `freshness.MERGE_KEEPS_COUNT` (Q-step12b (b): a merge keeps the larger touch count).

They are never configuration: every feature-build session checks they are at their defaults and refuses otherwise
(`require_defaults`, called by `checks.session_build.build_day`); only the step-13 check harness may change them, via
`switched` and `build_day(..., allow_diagnostic_switches=True)`.
"""

from __future__ import annotations

import importlib
from contextlib import contextmanager
from typing import Any, Iterator

DEFAULTS: dict[str, Any] = {
    "nqbot.features.zones:STEP_TIME_CANDIDATES": True,
    "nqbot.features.bands:SPLIT_JUMPS": True,
    "nqbot.features.touches:OWN_REARM": True,
    "nqbot.features.profile:PROFILE_ROW_TICKS": 4,
    "nqbot.features.freshness:MERGE_KEEPS_COUNT": True,
}
# the legacy value of each (features_version <= 36), by short name, for the check harness
LEGACY: dict[str, tuple[str, Any]] = {
    "cand": ("nqbot.features.zones:STEP_TIME_CANDIDATES", False),
    "jump": ("nqbot.features.bands:SPLIT_JUMPS", False),
    "rearm": ("nqbot.features.touches:OWN_REARM", False),
    "rows": ("nqbot.features.profile:PROFILE_ROW_TICKS", 1),
    "mergecount": ("nqbot.features.freshness:MERGE_KEEPS_COUNT", False),
}


class DiagnosticSwitches(Exception):
    """A diagnostic switch is not at its default in a feature build."""


def _get(name: str) -> Any:
    mod, attr = name.split(":")
    return getattr(importlib.import_module(mod), attr)


def _set(name: str, value: Any) -> None:
    mod, attr = name.split(":")
    setattr(importlib.import_module(mod), attr, value)


def current() -> dict[str, Any]:
    return {k: _get(k) for k in DEFAULTS}


def changed() -> dict[str, Any]:
    return {k: v for k, v in current().items() if v != DEFAULTS[k]}


def require_defaults() -> None:
    bad = changed()
    if bad:
        raise DiagnosticSwitches("diagnostic switches not at their defaults (only the step-13 check harness may change "
                                 f"them): {bad}")


@contextmanager
def switched(values: dict[str, Any]) -> Iterator[None]:
    """Set switches (full names) for the duration; restored afterwards."""
    unknown = set(values) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown diagnostic switches: {sorted(unknown)}")
    old = {k: _get(k) for k in values}
    try:
        for k, v in values.items():
            _set(k, v)
        yield
    finally:
        for k, v in old.items():
            _set(k, v)
