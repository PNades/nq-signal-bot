"""Checks on a parsed gameplan (SPEC §3.4: zones ordered, stops beyond zones, targets in the trade direction, each
scenario set adding up to 1).

Every check returns flags for the owner; no value is ever changed here. Two kinds (owner 2026-10-07, SPEC §3.4):
- info (`review: false`): something the gameplan doesn't give (no stop, no targets, acceptance minutes, a zone or a
  named target with no NQ price, a scenario without a probability), or a value kept by a rule the owner set (a stop
  or invalidation inside its zone is kept as written; a contradiction with no version marked final is left empty).
  Never needs the owner's confirmation, so a morning gameplan is never held up by one.
- review (`review: true`): everything else. The model labels each of its own notes info or review by the same
  rule (parser v3).
A gameplan is `ok` with info flags only, `flagged` with any review flag; a flagged one is still written and the owner
fixes or confirms it on the Gameplan page (§9b, from step 14) or in the JSON.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

# An NQ level is in the tens of thousands; a VIX line (~19.5) or a 10Y yield is not. Anything this small stored as
# hvl / hvl0 / regime_line is another instrument's number (glossary §3: the VIX regime line is a gate, not a level).
MIN_NQ_LEVEL = 1000
CONDITIONAL_TRIGGERS = ("acceptance", "breakout", "breakdown", "break-plan", "break_plan")
_PICK_WORDS = re.compile(r"\b(used|took|taken|chose|picked|kept the)\b", re.I)
_TWO_WORDS = re.compile(r"\b(two|both|vs\.?|while|differs?|also (gives|mentions|lists|says)|whereas)\b"
                        r"|\bgives\b.*\bgives\b|\bin the map and\b", re.I)

# Each scenario set's probabilities must add up to 1 (owner 2026-10-07); whole-number percentages may round
# (33/33/33 = 0.99), so a set within this of 1 passes (SPEC §3.4).
PROB_SUM_TOL = 0.015


def _flag(field: str, reason: str, origin: str = "validate", review: bool = True) -> dict[str, Any]:
    return {"field": field, "reason": reason, "origin": origin, "review": review}


def _info(field: str, reason: str) -> dict[str, Any]:
    """A flag that never needs the owner's confirmation (missing in the gameplan, or kept by an owner rule)."""
    return _flag(field, reason, review=False)


def needs_review(flags: list[dict[str, Any]]) -> bool:
    return any(f.get("review", True) for f in flags)


def resolve_date(content: str | None, filename: date | None, given: date | None) -> tuple[date | None, str, list]:
    """The gameplan's date: --date, else the canonical file name, else the content (flagged to confirm, §13)."""
    flags = []
    try:
        from_content = date.fromisoformat(content) if content else None
    except ValueError:
        from_content = None
        flags.append(_flag("date", f"the date read from the content ({content!r}) is not a valid date"))
    if given is not None:
        chosen, source = given, "given"
    elif filename is not None:
        chosen, source = filename, "filename"
    elif from_content is not None:
        chosen, source = from_content, "content"
        flags.append(_flag("date", "date read from the content only: confirm it (§13 step 9)"))
    else:
        return None, "none", flags
    if from_content is not None and from_content != chosen:
        flags.append(_flag("date", f"the content says {from_content} but the {source} date is {chosen}"))
    return chosen, source, flags


def _pair(value: Any, field: str, flags: list) -> tuple[float, float] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 2:
        flags.append(_flag(field, f"wants [low, high], got {value!r}"))
        return None
    lo, hi = value
    if lo > hi:
        flags.append(_flag(field, f"edges out of order: {lo} > {hi}"))
        return hi, lo  # checks below use the ordered edges; the stored value is left as written
    return lo, hi


def _target_edges(t: dict[str, Any]) -> tuple[float, float] | None:
    lo, hi = t.get("low"), t.get("high")
    if lo is None and hi is None:
        return None
    lo, hi = (hi, hi) if lo is None else (lo, lo) if hi is None else (lo, hi)
    return (hi, lo) if lo > hi else (lo, hi)


def _targets(f: str, targets: list[dict[str, Any]], lo: float, hi: float, sell: bool, flags: list) -> None:
    if not targets:
        flags.append(_info(f, "no targets in the gameplan"))
    priced = []
    for i, t in enumerate(targets, start=1):
        edges = _target_edges(t)
        if edges is None:
            flags.append(_info(f, f"T{i} {t.get('as_written')!r} has no price in the gameplan"))
            continue
        if t.get("low") is not None and t.get("high") is not None and t["low"] > t["high"]:
            flags.append(_flag(f, f"T{i} edges out of order: {t['low']} > {t['high']}"))
        near = edges[1] if sell else edges[0]  # the target's edge nearest the zone
        if (sell and near >= lo) or (not sell and near <= hi):
            flags.append(_flag(f, f"T{i} {t.get('as_written')!r} is not "
                                  f"{'below the zone low' if sell else 'above the zone high'}"))
        priced.append((edges[0] + edges[1]) / 2)
    # A range target is ordered by its middle, so a range never counts against itself.
    if any((b >= a) if sell else (b <= a) for a, b in zip(priced, priced[1:])):
        flags.append(_flag(f, "targets " + ", ".join(repr(t.get("as_written")) for t in targets)
                           + " are not in order away from the zone"))


def _invalidation(f: str, conditions: list[dict[str, Any]], lo: float, hi: float, sell: bool, flags: list) -> None:
    for c in conditions:
        if c.get("timeframe") == "acceptance" and c.get("acceptance_minutes") is None:
            flags.append(_info(f, f"acceptance with no minutes: {c.get('as_written')!r}"))
        price, direction = c.get("price"), c.get("direction")
        if c.get("instrument") != "NQ" or price is None or direction not in ("above", "below"):
            continue
        if sell and direction == "below":
            flags.append(_flag(f, "a sell zone invalidated below: wrong side"))
        elif not sell and direction == "above":
            flags.append(_flag(f, "a buy zone invalidated above: wrong side"))
        elif sell and price < hi:
            flags.append(_info(f, f"above-level {price} is inside the zone (high {hi})"))
        elif not sell and price > lo:
            flags.append(_info(f, f"below-level {price} is inside the zone (low {lo})"))


def _zone(z: dict[str, Any], flags: list) -> None:
    """A conditional entry (empty zone, parser v3) is checked against its NQ trigger level instead."""
    f = f"zones[{z.get('id')}]"
    level = (z.get("trigger") or {}).get("nq_price")
    if z.get("zone") is None and level is None:
        flags.append(_info(f + ".zone", "no NQ price range for this zone"))
        return
    edges = _pair(z.get("zone") if z.get("zone") is not None else level,
                  f + (".zone" if z.get("zone") is not None else ".trigger.nq_price"), flags)
    if edges is None:
        return
    lo, hi = edges
    sell = z.get("side") == "sell"

    stop = z.get("stop")
    if stop is None:
        flags.append(_info(f + ".stop", "no stop in the gameplan"))
    elif sell and stop <= hi:
        flags.append(_info(f + ".stop", f"sell zone stop {stop} is not above the zone high {hi}"))
    elif not sell and stop >= lo:
        flags.append(_info(f + ".stop", f"buy zone stop {stop} is not below the zone low {lo}"))

    _targets(f + ".targets", z.get("targets") or [], lo, hi, sell, flags)
    _invalidation(f + ".invalidation", z.get("invalidation") or [], lo, hi, sell, flags)

    gates = (z.get("trigger") or {}).get("es_gates") or []
    for g in gates:
        _pair(g.get("price"), f + ".trigger.es_gates", flags)
    above = [min(g["price"]) for g in gates if g.get("direction") == "above" and g.get("price")]
    below = [max(g["price"]) for g in gates if g.get("direction") == "below" and g.get("price")]
    if above and below and max(above) >= min(below):
        flags.append(_flag(f + ".trigger", f"ES gate can't hold: above {max(above)} and below {min(below)}"))


def _scenario_set(s: dict[str, Any], flags: list) -> None:
    f = f"scenario_sets[{s.get('frame')}]"
    scenarios = s.get("scenarios") or []
    ids = [x.get("id") for x in scenarios]
    for dup in sorted({x for x in ids if ids.count(x) > 1}, key=str):
        flags.append(_flag(f, f"scenario id {dup!r} appears more than once"))
    probs = [x.get("prob") for x in scenarios]
    for x in scenarios:
        p = x.get("prob")
        if p is not None and not 0 <= p <= 1:
            flags.append(_flag(f"{f}[{x.get('id')}].prob", f"{p} is not a fraction between 0 and 1"))
    if scenarios and all(p is None for p in probs):
        flags.append(_info(f, "no probabilities in this set"))
    elif any(p is None for p in probs):
        flags.append(_info(f, "some scenarios have no probability"))
    elif scenarios and abs(sum(probs) - 1) > PROB_SUM_TOL:
        flags.append(_flag(f, f"probabilities add up to {sum(probs):g}, not 1"))


def _resolve(parsed: dict[str, Any], field: str) -> tuple[bool, Any]:
    """(found, value) for a note's field path like `hvl`, `zones[E3].stop`, `zones[E2].trigger.nq_price`."""
    m = re.fullmatch(r"zones\[([^\]]+)\]((?:\.\w+)*)", field)
    if m:
        zone = next((z for z in parsed.get("zones") or [] if z.get("id") == m.group(1)), None)
        if zone is None:
            return False, None
        node: Any = zone
        aliases = {"trigger_level": ("trigger", "nq_price"), "trigger_type": ("trigger", "type"),
                   "trigger_note": ("trigger", "note"), "es_gates": ("trigger", "es_gates")}
        for part in [p for p in m.group(2).split(".") if p]:
            for key in aliases.get(part, (part,)):
                if not isinstance(node, dict) or key not in node:
                    return False, None
                node = node[key]
        return True, node
    if field in parsed:
        return True, parsed[field]
    return False, None


def _filled(value: Any) -> bool:
    return value not in (None, [], "", {})


def _glossary_checks(parsed: dict[str, Any], source_text: str | None, flags: list) -> None:
    """Owner 2026-10-07: the glossary rules the code can test (docs/gameplan_glossary.md §3, §7)."""
    for z in parsed.get("zones") or []:
        trig = z.get("trigger") or {}
        conditional = trig.get("nq_price") is not None or (trig.get("type") or "").lower() in CONDITIONAL_TRIGGERS
        if conditional and z.get("zone") is not None:
            flags.append(_flag(f"zones[{z.get('id')}].zone", "a conditional entry has a zone: the zone stays empty "
                                                             "and the level goes in trigger.nq_price (glossary §7)"))
    for u in parsed.get("uncertain") or []:
        two = u.get("kind") == "two_versions" or (
            _PICK_WORDS.search(u.get("reason", "")) and _TWO_WORDS.search(u.get("reason", "")))
        found, value = _resolve(parsed, u.get("field", ""))
        if two and found and _filled(value):
            flags.append(_flag(u.get("field", "?"), "a value is filled in although the note says it is written two "
                                                    "ways with none marked FINAL: it must stay empty (glossary §7)"))
    levels = {k: parsed.get(k) for k in ("hvl", "hvl0", "regime_line")}
    for k, v in levels.items():
        if v and min(v) < MIN_NQ_LEVEL:
            flags.append(_flag(k, f"{v} is not an NQ price (a VIX or yield line?): a VIX regime line is a gate in "
                                  "other_conditions, never a level (glossary §3)"))
    hvl_pair = bool(source_text and re.search(r"HVL pair", source_text, re.I))
    names = list(levels)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if levels[a] and levels[a] == levels[b] and not ({a, b} == {"hvl", "hvl0"} and hvl_pair):
                flags.append(_flag(f"{a},{b}", f"{a} and {b} hold the same value {levels[a]}: each is filled only "
                                               "from its own words, unless the gameplan says 'the HVL pair' "
                                               "(glossary §3, §8)"))


def check(parsed: dict[str, Any], source_text: str | None = None) -> list[dict[str, str]]:
    """`source_text`: the gameplan's text (Markdown), for the checks that need its words ('the HVL pair')."""
    flags: list[dict[str, str]] = []
    for name in ("hvl", "hvl0", "regime_line"):
        _pair(parsed.get(name), name, flags)
    _glossary_checks(parsed, source_text, flags)

    zones = parsed.get("zones") or []
    ids = [z.get("id") for z in zones]
    for dup in sorted({i for i in ids if ids.count(i) > 1}, key=str):
        flags.append(_flag("zones", f"zone id {dup!r} appears more than once"))
    for z in zones:
        _zone(z, flags)

    sets = parsed.get("scenario_sets") or []
    frames = [s.get("frame") for s in sets]
    for dup in sorted({x for x in frames if frames.count(x) > 1}, key=str):
        flags.append(_flag("scenario_sets", f"more than one {dup} set"))
    for s in sets:
        _scenario_set(s, flags)

    for u in parsed.get("uncertain") or []:
        flags.append(_flag(u.get("field", "?"), u.get("reason", ""), origin="model", review=u.get("kind") != "info"))
    return flags
