"""The JSON Schema the Claude API fills in for one gameplan (structured output, SPEC §5.6, §3.4).

The §5.6 fields (date, hvl, hvl0, regime_line, zones, scenario_sets) plus audit fields the model reports about its
own reading: where the date came from, which version it used, what it dropped and what it is unsure of.

Parser version 2 (owner 2026-10-07): the parser records what the gameplan says and never chooses or guesses. Every
price level is a [low, high] range (one price -> [p, p]); targets, invalidations and ES gates keep their words as
written next to the numbers; both probability sets (NY AM, session) are kept, each labelled.

Only keywords structured outputs accept: no numeric/string/array-length constraints (validate.py checks
those), every object has `additionalProperties: false` and lists every property as required (a value that
isn't in the gameplan is null, never left out).
"""

from __future__ import annotations

from typing import Any

SIDES = ("buy", "sell")
BIASES = ("up", "down", "neutral", "mixed")
FRAMES = ("ny_am", "session", "unspecified")
DIRECTIONS = ("above", "below", "unclear")
INSTRUMENTS = ("NQ", "ES")
TIMEFRAMES = ("1m_close", "settled_bar", "acceptance", "not_stated", "other")


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


def _enum(values: tuple[str, ...], description: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "string", "enum": list(values)}
    if description:
        out["description"] = description
    return out


_NUMBER = {"type": "number"}
_STRING = {"type": "string"}
_PAIR = {"type": "array", "items": _NUMBER, "description": "[low, high] in points; one price p -> [p, p]"}

ES_GATE = _object({
    "direction": _enum(DIRECTIONS, "above: above, over, >, holding, reclaiming; below: below, under, <, losing, "
                                   "rejecting, failing; any other wording (stalling under, ...) is unclear"),
    "price": {**_PAIR, "description": "ES price as written, [low, high]; one price p -> [p, p]"},
    "as_written": {**_STRING, "description": "the gate's words, verbatim"},
})

TRIGGER = _object({
    "type": _nullable({**_STRING, "description": "the gameplan's own trigger name, e.g. failed_audit, reclaim"}),
    "es_gates": {"type": "array", "items": ES_GATE, "description": "every ES price the entry is gated on"},
    "note": _nullable({**_STRING, "description": "any other trigger condition, verbatim"}),
})

INVALIDATION = _object({
    "instrument": _enum(INSTRUMENTS),
    "direction": _enum(DIRECTIONS, "the side price must reach to invalidate the zone"),
    "price": _nullable({**_NUMBER, "description": "the price as written; null if the gameplan gives none"}),
    "timeframe": _enum(TIMEFRAMES, "1m_close: a 1-minute close/settle is stated; settled_bar: 'settled bar' with no "
                                   "timeframe; acceptance: time held beyond the price; not_stated: just a price "
                                   "(e.g. 'INVALID >29,520'); other: anything else"),
    "acceptance_minutes": _nullable({**_NUMBER, "description": "for acceptance: the minutes written (15+ -> 15)"}),
    "as_written": {**_STRING, "description": "the invalidation's words, verbatim"},
})

TARGET = _object({
    "low": _nullable(_NUMBER),
    "high": _nullable(_NUMBER),
    "as_written": {**_STRING, "description": "the target's words, verbatim (e.g. '29,500-545', 'home')"},
}, description="one target: one price -> low = high; a range -> its two ends; a name with no price in this "
               "gameplan -> both null")

ZONE = _object({
    "id": {**_STRING, "description": "the gameplan's label, e.g. E1"},
    "side": _enum(SIDES),
    "zone": _nullable(_PAIR),  # null: the gameplan gives no NQ price for it (flagged)
    "trigger": TRIGGER,
    "stop": _nullable({**_NUMBER, "description": "NQ stop price as written"}),
    "invalidation": {"type": "array", "items": INVALIDATION},
    "targets": {"type": "array", "items": TARGET, "description": "targets as written, T1 first"},
    "source_quote": {**_STRING, "description": "the gameplan line(s) this zone was read from, verbatim"},
})

SCENARIO = _object({
    "id": _STRING,
    "prob": _nullable({**_NUMBER, "description": "probability as a fraction 0-1 (50% -> 0.5); null if not given"}),
    "bias": _enum(BIASES, "up/down: the scenario's stated direction; neutral: range/rotation with no direction; "
                          "mixed: both directions"),
    "desc": _STRING,
})

SCENARIO_SET = _object({
    "frame": _enum(FRAMES, "ny_am: the NY morning (e.g. 09:30-12:00); session: the whole session; unspecified: "
                           "the gameplan doesn't say"),
    "label_as_written": _nullable({**_STRING, "description": "the set's own heading, verbatim"}),
    "scenarios": {"type": "array", "items": SCENARIO},
})

UNCERTAIN = _object({
    "field": {**_STRING, "description": "path of the uncertain value, e.g. zones[E3].stop or hvl"},
    "reason": _STRING,
})

GAMEPLAN_SCHEMA: dict[str, Any] = _object({
    "date": _nullable({"type": "string", "format": "date",
                       "description": "the trading day the gameplan is for (YYYY-MM-DD); null if not stated"}),
    "date_evidence": {**_STRING, "description": "the text the date was read from, verbatim"},
    "hvl": _nullable({**_PAIR, "description": "the NQ level the gameplan labels HVL, [low, high]"}),
    "hvl0": _nullable({**_PAIR, "description": "the NQ level the gameplan labels HVL0 / HVL 0DTE, [low, high]"}),
    "regime_line": _nullable({**_PAIR, "description": "only a level the gameplan labels 'regime line'"}),
    "zones": {"type": "array", "items": ZONE},
    "scenario_sets": {"type": "array", "items": SCENARIO_SET},
    "version_used": _nullable({**_STRING, "description": "the version label used, e.g. 'RTH v2'"}),
    "dropped": {"type": "array", "items": _STRING,
                "description": "sections or notes left out (Observed RTH action, older versions, blackout notes)"},
    "uncertain": {"type": "array", "items": UNCERTAIN},
})

# The §5.6 shape written to processed/gameplans/<date>.json (audit fields go under `parse`).
SPEC_FIELDS = ("date", "hvl", "hvl0", "regime_line", "zones", "scenario_sets")
