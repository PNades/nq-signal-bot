"""The JSON Schema the Claude API fills in for one gameplan (structured output, SPEC §5.6, §3.4), and its conversion
to the stored §5.6 shape.

The §5.6 fields (date, hvl, hvl0, regime_line, zones, scenario_sets) plus audit fields the model reports about its
own reading: where the date came from, which version it used, what it dropped and what it is unsure of.

Parser version 2 (owner 2026-10-07): the parser records what the gameplan says and never chooses or guesses. Every
price level is a [low, high] range (one price -> [p, p]); targets, invalidations and ES gates keep their words as
written next to the numbers; both probability sets (NY AM, session) are kept, each labelled.
Parser version 3 (owner 2026-10-07): a conditional "acceptance through / breakout / breakdown" entry is its own zone
with an empty `zone`, its NQ trigger level in `trigger.nq_price` and the acceptance condition in `trigger.note`; each
model note carries `kind` info/review (same rule as the checks); `other_conditions` (per entry and per gameplan)
keeps word for word what §5.6 has no field for (VIX/10Y gates, which test to take, no-trade gaps, size).

Wire format vs stored format: structured outputs compile the schema into a grammar, and every nullable (`anyOf`)
field, above all inside arrays of objects, multiplies its size (the API refuses a grammar that is too large, and
allows at most 16 union-type parameters per request). So the model writes a missing value as an empty list or an
empty string and one number as a one-item list; the schema has no union at all (parser v3 still went over
the grammar limit with `date` as the last union, 2026-10-07). `from_wire` turns that into the stored
§5.6 shape (null, numbers, low/high) before any check; a list with more values than the field takes is left
empty and flagged, never trimmed.

Only keywords structured outputs accept: no numeric/string/array-length constraints (validate.py checks
those), every object has `additionalProperties: false` and lists every property as required.
"""

from __future__ import annotations

from typing import Any

SIDES = ("buy", "sell")
BIASES = ("up", "down", "neutral", "mixed")
FRAMES = ("ny_am", "session", "unspecified")
DIRECTIONS = ("above", "below", "unclear")
TIMEFRAMES = ("1m_close", "settled_bar", "acceptance", "not_stated", "other")
# On the wire an invalidation's instrument rides in its direction (es_above, ...): one enum instead of two keeps the
# compiled grammar under the API's limit (2026-10-07); from_wire splits it back into instrument + direction.
WIRE_INV_DIRECTIONS = DIRECTIONS + tuple(f"es_{d}" for d in DIRECTIONS)


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


def _numbers(description: str) -> dict[str, Any]:
    return {"type": "array", "items": {"type": "number"}, "description": description}


def _text(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


_RANGE = "[low, high]; one price p -> [p, p]; [] if the gameplan gives none"
_ONE = "[value] as written; [] if the gameplan gives none"

ES_GATE = _object({
    "direction": _enum(DIRECTIONS, "above: above, over, >, holding, reclaiming; below: below, under, <, losing, "
                                   "rejecting, failing; any other wording (stalling under, ...) is unclear"),
    "price": _numbers("ES price as written, " + _RANGE),
    "as_written": _text("the gate's words, verbatim"),
})

INVALIDATION = _object({
    "direction": _enum(WIRE_INV_DIRECTIONS, "the side NQ (above/below/unclear) or ES (es_above/es_below/es_unclear) "
                                            "must reach to invalidate the zone"),
    "price": _numbers("the price, " + _ONE),
    "timeframe": _enum(TIMEFRAMES, "1m_close: a 1-minute close/settle is stated; settled_bar: 'settled bar' with no "
                                   "timeframe; acceptance: time held beyond the price; not_stated: just a price "
                                   "(e.g. 'INVALID >29,520'); other: anything else"),
    "acceptance_minutes": _numbers("for acceptance: the minutes written (15+ -> [15]); [] otherwise"),
    "as_written": _text("the invalidation's words, verbatim"),
})

TARGET = _object({
    "price": _numbers("one price p -> [p, p]; a range -> [low, high]; a name with no price in this gameplan -> []"),
    "as_written": _text("the target's words, verbatim (e.g. '29,500-545', 'home')"),
})

ZONE = _object({
    "id": _text("the gameplan's label, e.g. E1"),
    "side": _enum(SIDES),
    "zone": _numbers("NQ zone " + _RANGE),
    # The trigger's fields sit on the zone on the wire (one object level less for the grammar); stored as `trigger`.
    "trigger_type": _text("the gameplan's own trigger name, e.g. failed_audit, reclaim, acceptance; '' if none"),
    "trigger_level": _numbers("NQ level a conditional (acceptance / breakout / breakdown) entry must get through, "
                              + _RANGE),
    "es_gates": {"type": "array", "items": ES_GATE, "description": "every ES price the entry is gated on"},
    "trigger_note": _text("any other trigger condition, verbatim; '' if none"),
    "stop": _numbers("NQ stop price, " + _ONE),
    "invalidation": {"type": "array", "items": INVALIDATION},
    "targets": {"type": "array", "items": TARGET, "description": "targets as written, T1 first"},
    "source_quote": _text("the gameplan line(s) this zone was read from, verbatim"),
})

SCENARIO = _object({
    "id": {"type": "string"},
    "prob": _numbers("probability as a fraction 0-1 (50% -> [0.5]); [] if not given"),
    "bias": _enum(BIASES, "up/down: the scenario's stated direction; neutral: range/rotation with no direction; "
                          "mixed: both directions"),
    "desc": {"type": "string"},
})

SCENARIO_SET = _object({
    "frame": _enum(FRAMES, "ny_am: the NY morning (e.g. 09:30-12:00); session: the whole session; unspecified: "
                           "the gameplan doesn't say"),
    "label_as_written": _text("the set's own heading, verbatim; '' if none"),
    "scenarios": {"type": "array", "items": SCENARIO},
})

UNCERTAIN = _object({
    "kind": _enum(("info", "review"), "info: only says something is missing, or is covered by an owner rule "
                                      "(dates, stops/invalidations kept as written, the version marked final); "
                                      "review: anything else"),
    "field": _text("path of the uncertain value, e.g. zones[E3].stop or hvl"),
    "reason": {"type": "string"},
})

GAMEPLAN_SCHEMA: dict[str, Any] = _object({
    "date": _text("the trading day the gameplan is for, YYYY-MM-DD; '' if not stated"),
    "date_evidence": _text("the text the date was read from, verbatim"),
    "hvl": _numbers("the NQ level the gameplan labels HVL, " + _RANGE),
    "hvl0": _numbers("the NQ level the gameplan labels HVL0 / HVL 0DTE, " + _RANGE),
    "regime_line": _numbers("only a level the gameplan labels 'regime line', " + _RANGE),
    "zones": {"type": "array", "items": ZONE},
    "scenario_sets": {"type": "array", "items": SCENARIO_SET},
    "version_used": _text("the version label used, e.g. 'RTH v2'; '' if none"),
    "dropped": {"type": "array", "items": {"type": "string"},
                "description": "sections or notes left out (Observed RTH action, older versions, blackout notes)"},
    "other_conditions": {"type": "array", "items": {"type": "string"},
                         "description": "conditions §5.6 has no field for (VIX/10Y gates, no-trade gaps, which "
                                        "test to take, size, ...), each word for word; one that belongs to an entry "
                                        "starts with that entry's id and ': ' (e.g. 'E5: VIX over 17.72')"},
    "uncertain": {"type": "array", "items": UNCERTAIN},
})

# The §5.6 shape written to processed/gameplans/<date>.json (audit fields go under `parse`).
SPEC_FIELDS = ("date", "hvl", "hvl0", "regime_line", "zones", "scenario_sets", "other_conditions")


# --- wire -> stored §5.6 shape ----------------------------------------------------------------------------------

def _flag(field: str, reason: str) -> dict[str, Any]:
    return {"field": field, "reason": reason, "origin": "validate", "review": True}


def _range(value: list | None, field: str, flags: list) -> list | None:
    """[] -> None, [p] -> [p, p], [lo, hi] as written; more values -> None, flagged."""
    if not value:
        return None
    if len(value) == 1:
        return [value[0], value[0]]
    if len(value) == 2:
        return list(value)
    flags.append(_flag(field, f"wants [low, high], got {value!r}: left empty"))
    return None


def _one(value: list | None, field: str, flags: list) -> float | None:
    """[] -> None, [v] -> v; more values -> None, flagged."""
    if not value:
        return None
    if len(value) == 1:
        return value[0]
    flags.append(_flag(field, f"one value expected, got {value!r}: left empty"))
    return None


def _text_or_none(value: str | None) -> str | None:
    return value if value else None


def from_wire(wire: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """The model's answer -> the stored §5.6 shape (plus the audit fields), and flags for values it can't hold."""
    flags: list[dict[str, str]] = []
    zone_conditions, plan_conditions = _split_conditions(wire)
    zones = []
    for z in wire.get("zones") or []:
        f = f"zones[{z.get('id')}]"
        zones.append({
            "id": z.get("id"),
            "side": z.get("side"),
            "zone": _range(z.get("zone"), f + ".zone", flags),
            "trigger": {
                "type": _text_or_none(z.get("trigger_type")),
                "es_gates": [{"direction": g.get("direction"),
                              "price": _range(g.get("price"), f + ".trigger.es_gates", flags),
                              "as_written": g.get("as_written")} for g in z.get("es_gates") or []],
                "nq_price": _range(z.get("trigger_level"), f + ".trigger.nq_price", flags),
                "note": _text_or_none(z.get("trigger_note")),
            },
            "stop": _one(z.get("stop"), f + ".stop", flags),
            "invalidation": [{**_instrument_direction(c.get("direction")),
                              "price": _one(c.get("price"), f + ".invalidation", flags),
                              "timeframe": c.get("timeframe"),
                              "acceptance_minutes": _one(c.get("acceptance_minutes"), f + ".invalidation", flags),
                              "as_written": c.get("as_written")} for c in z.get("invalidation") or []],
            "targets": [_target(t, f + ".targets", flags) for t in z.get("targets") or []],
            "other_conditions": zone_conditions.get(z.get("id"), []),
            "source_quote": z.get("source_quote"),
        })
    sets = []
    for s in wire.get("scenario_sets") or []:
        f = f"scenario_sets[{s.get('frame')}]"
        sets.append({
            "frame": s.get("frame"),
            "label_as_written": _text_or_none(s.get("label_as_written")),
            "scenarios": [{"id": x.get("id"), "prob": _one(x.get("prob"), f"{f}[{x.get('id')}].prob", flags),
                           "bias": x.get("bias"), "desc": x.get("desc")} for x in s.get("scenarios") or []],
        })
    doc = {
        "date": _text_or_none(wire.get("date")),
        "date_evidence": wire.get("date_evidence"),
        "hvl": _range(wire.get("hvl"), "hvl", flags),
        "hvl0": _range(wire.get("hvl0"), "hvl0", flags),
        "regime_line": _range(wire.get("regime_line"), "regime_line", flags),
        "zones": zones,
        "scenario_sets": sets,
        "version_used": _text_or_none(wire.get("version_used")),
        "dropped": wire.get("dropped") or [],
        "other_conditions": plan_conditions,
        "uncertain": wire.get("uncertain") or [],
    }
    return doc, flags


def _instrument_direction(wire_direction: str | None) -> dict[str, Any]:
    """'es_below' -> ES below; 'above' -> NQ above."""
    d = wire_direction or "unclear"
    return {"instrument": "ES", "direction": d[3:]} if d.startswith("es_") else {"instrument": "NQ", "direction": d}


def _split_conditions(wire: dict[str, Any]) -> tuple[dict[str, list[str]], list[str]]:
    """The wire's one other_conditions list -> per entry (an item starting '<entry id>: ', the prefix removed) and
    plan-wide (the rest, as written). The longest matching id wins ('E5b: ...' is E5b's, not E5's)."""
    ids = sorted({z.get("id") for z in wire.get("zones") or [] if z.get("id")}, key=len, reverse=True)
    per_zone: dict[str, list[str]] = {}
    plan: list[str] = []
    for item in wire.get("other_conditions") or []:
        owner = next((i for i in ids if item.startswith(f"{i}: ")), None)
        if owner is None:
            plan.append(item)
        else:
            per_zone.setdefault(owner, []).append(item[len(owner) + 2:])
    return per_zone, plan


def _target(t: dict[str, Any], field: str, flags: list) -> dict[str, Any]:
    price = _range(t.get("price"), field, flags)
    low, high = price if price else (None, None)
    return {"low": low, "high": high, "as_written": t.get("as_written")}
