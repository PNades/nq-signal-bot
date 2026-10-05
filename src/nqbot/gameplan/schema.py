"""The JSON Schema the Claude API fills in for one gameplan (structured output, SPEC §5.6, §3.4).

The §5.6 fields (date, hvl, regime_line, zones, scenarios) plus audit fields the model reports about its own
reading: where the date came from, which version it used, what it dropped and what it is unsure of.

Only keywords structured outputs accept: no numeric/string/array-length constraints (validate.py checks
those), every object has `additionalProperties: false` and lists every property as required (a value that
isn't in the gameplan is null, never left out).
"""

from __future__ import annotations

from typing import Any

SIDES = ("buy", "sell")
BIASES = ("up", "down", "neutral")


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _object(properties: dict[str, Any], description: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
    if description:
        out["description"] = description
    return out


_NUMBER = {"type": "number"}
_STRING = {"type": "string"}
_PAIR = {"type": "array", "items": _NUMBER, "description": "[low, high] in NQ points"}

TRIGGER = _object({
    "type": _nullable({**_STRING, "description": "the gameplan's own trigger name, e.g. failed_audit, reclaim"}),
    "es_below": _nullable({**_NUMBER, "description": "ES gate: the zone is valid only while ES trades below this"}),
    "es_above": _nullable({**_NUMBER, "description": "ES gate: the zone is valid only while ES trades above this"}),
    "note": _nullable({**_STRING, "description": "any other trigger condition, verbatim"}),
})

INVALIDATION = _object({
    "settled_1m_above": _nullable({**_NUMBER, "description": "NQ 1-minute close above this invalidates the zone"}),
    "settled_1m_below": _nullable({**_NUMBER, "description": "NQ 1-minute close below this invalidates the zone"}),
    "note": _nullable({**_STRING, "description": "any other invalidation wording, verbatim"}),
})

ZONE = _object({
    "id": {**_STRING, "description": "the gameplan's label, e.g. E1"},
    "side": {"type": "string", "enum": list(SIDES)},
    "zone": _nullable(_PAIR),  # null: the gameplan gives no NQ price for it (flagged)
    "trigger": TRIGGER,
    "stop": _nullable({**_NUMBER, "description": "NQ stop price as written"}),
    "invalidation": INVALIDATION,
    "targets": {"type": "array", "items": _NUMBER, "description": "NQ targets as written, T1 first"},
    "source_quote": {**_STRING, "description": "the gameplan line(s) this zone was read from, verbatim"},
})

SCENARIO = _object({
    "id": _STRING,
    "prob": _nullable({**_NUMBER, "description": "probability as a fraction 0-1 (50% -> 0.5); null if not given"}),
    "bias": {"type": "string", "enum": list(BIASES)},
    "desc": _STRING,
})

UNCERTAIN = _object({
    "field": {**_STRING, "description": "path of the uncertain value, e.g. zones[E3].stop or hvl"},
    "reason": _STRING,
})

GAMEPLAN_SCHEMA: dict[str, Any] = _object({
    "date": _nullable({"type": "string", "format": "date",
                       "description": "the trading day the gameplan is for (YYYY-MM-DD); null if not stated"}),
    "date_evidence": {**_STRING, "description": "the text the date was read from, verbatim"},
    "hvl": _nullable({**_NUMBER, "description": "HVL (high volume level / gamma flip) in NQ points"}),
    "regime_line": _nullable(_PAIR),
    "zones": {"type": "array", "items": ZONE},
    "scenarios": {"type": "array", "items": SCENARIO},
    "version_used": _nullable({**_STRING, "description": "the version label used, e.g. 'RTH v2'"}),
    "dropped": {"type": "array", "items": _STRING,
                "description": "sections or notes left out (Observed RTH action, older versions, blackout notes)"},
    "uncertain": {"type": "array", "items": UNCERTAIN},
})

# The §5.6 shape written to processed/gameplans/<date>.json (audit fields go under `parse`).
SPEC_FIELDS = ("date", "hvl", "regime_line", "zones", "scenarios")
