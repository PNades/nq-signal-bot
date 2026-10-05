"""Checks on a parsed gameplan (SPEC §3.4: zones ordered, stops beyond zones, targets in the trade direction).

Every check returns flags for the owner; no value is ever changed here. A flagged gameplan is still written
(its `status` is "flagged"); the owner fixes it on the Gameplan page (§9b, from step 14) or in the JSON.
"""

from __future__ import annotations

from datetime import date
from typing import Any

# Scenario probabilities may be written as rounded percentages: their sum may exceed 1 only by float noise.
PROB_SUM_EPS = 1e-6


def _flag(field: str, reason: str, origin: str = "validate") -> dict[str, str]:
    return {"field": field, "reason": reason, "origin": origin}


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


def _zone(z: dict[str, Any], flags: list) -> None:
    f = f"zones[{z.get('id')}]"
    if z.get("zone") is None:
        flags.append(_flag(f + ".zone", "no NQ price range for this zone"))
        return
    edges = _pair(z.get("zone"), f + ".zone", flags)
    side = z.get("side")
    if edges is None:
        return
    lo, hi = edges
    sell = side == "sell"

    stop = z.get("stop")
    if stop is None:
        flags.append(_flag(f + ".stop", "no stop in the gameplan"))
    elif sell and stop <= hi:
        flags.append(_flag(f + ".stop", f"sell zone stop {stop} is not above the zone high {hi}"))
    elif not sell and stop >= lo:
        flags.append(_flag(f + ".stop", f"buy zone stop {stop} is not below the zone low {lo}"))

    targets = z.get("targets") or []
    if not targets:
        flags.append(_flag(f + ".targets", "no targets in the gameplan"))
    for i, t in enumerate(targets, start=1):
        if (sell and t >= lo) or (not sell and t <= hi):
            flags.append(_flag(f + ".targets", f"T{i} {t} is not {'below the zone low' if sell else 'above the zone high'}"))
    steps = list(zip(targets, targets[1:]))
    if any((b >= a) if sell else (b <= a) for a, b in steps):
        flags.append(_flag(f + ".targets", f"targets {targets} are not in order away from the zone"))

    inv = z.get("invalidation") or {}
    above, below = inv.get("settled_1m_above"), inv.get("settled_1m_below")
    if sell and below is not None:
        flags.append(_flag(f + ".invalidation", "a sell zone invalidated by a close below: wrong side"))
    if not sell and above is not None:
        flags.append(_flag(f + ".invalidation", "a buy zone invalidated by a close above: wrong side"))
    if sell and above is not None and above < hi:
        flags.append(_flag(f + ".invalidation", f"close-above level {above} is inside the zone (high {hi})"))
    if not sell and below is not None and below > lo:
        flags.append(_flag(f + ".invalidation", f"close-below level {below} is inside the zone (low {lo})"))

    trig = z.get("trigger") or {}
    es_below, es_above = trig.get("es_below"), trig.get("es_above")
    if es_below is not None and es_above is not None and es_above >= es_below:
        flags.append(_flag(f + ".trigger", f"ES gate can't hold: above {es_above} and below {es_below}"))


def check(parsed: dict[str, Any]) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    _pair(parsed.get("regime_line"), "regime_line", flags)

    zones = parsed.get("zones") or []
    ids = [z.get("id") for z in zones]
    for dup in sorted({i for i in ids if ids.count(i) > 1}, key=str):
        flags.append(_flag("zones", f"zone id {dup!r} appears more than once"))
    for z in zones:
        _zone(z, flags)

    scenarios = parsed.get("scenarios") or []
    sids = [s.get("id") for s in scenarios]
    for dup in sorted({i for i in sids if sids.count(i) > 1}, key=str):
        flags.append(_flag("scenarios", f"scenario id {dup!r} appears more than once"))
    probs = [s.get("prob") for s in scenarios if s.get("prob") is not None]
    for s in scenarios:
        p = s.get("prob")
        if p is not None and not 0 <= p <= 1:
            flags.append(_flag(f"scenarios[{s.get('id')}].prob", f"{p} is not a fraction between 0 and 1"))
    if sum(probs) > 1 + PROB_SUM_EPS:
        flags.append(_flag("scenarios", f"probabilities add up to {sum(probs):g} (> 1)"))

    for u in parsed.get("uncertain") or []:
        flags.append(_flag(u.get("field", "?"), u.get("reason", ""), origin="model"))
    return flags
