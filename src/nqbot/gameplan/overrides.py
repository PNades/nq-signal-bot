"""Owner overrides on top of a parsed gameplan (owner 2026-10-07, SPEC §3.4).

processed/gameplans/overrides/<date>.json holds the owner's choices apart from the parsed file, so a re-parse never
loses them; `effective()` applies them on top of processed/gameplans/<date>.json, and everything downstream (the
step-9 report, step 11) reads the effective gameplan.

  {"date": "2026-08-18",
   "owner_confirmed": {"at": "...", "note": "..."} | null,
   "ops": [{"path": ["zones", {"id": "S2-B-29650"}, "zone"], "value": [29650, 29700], "reason": "...", "at": "..."}]}

A path step is a dict key, or a {key: value} selector picking the one list item whose fields all match (keys may be
dotted, e.g. "trigger.nq_price"). Entries are selected with `entry_selector` (owner 2026-10-07): by their label only
when the gameplan labels its entries (E1, E2, ...); otherwise by side plus the level or zone the owner approved,
because the model names unlabelled entries afresh on every parse (08-17's Z6 came back as L5-ACC). An op that
matches nothing, or more than one item, becomes a review flag on the effective gameplan; it is never dropped
silently.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from nqbot.storage import atomic_write_json


def path_for(processed_dir: Path, day: date) -> Path:
    return processed_dir / "overrides" / f"{day.isoformat()}.json"


def load(processed_dir: Path, day: date) -> dict[str, Any]:
    p = path_for(processed_dir, day)
    if not p.exists():
        return {"date": day.isoformat(), "owner_confirmed": None, "ops": []}
    return json.loads(p.read_text(encoding="utf-8"))


def save(processed_dir: Path, day: date, ov: dict[str, Any]) -> None:
    atomic_write_json(path_for(processed_dir, day), ov)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_value(processed_dir: Path, day: date, path: list, value: Any, reason: str) -> None:
    """Record (or replace) the owner's value at `path`."""
    ov = load(processed_dir, day)
    ov["ops"] = [o for o in ov["ops"] if o["path"] != path]
    ov["ops"].append({"path": path, "value": value, "reason": reason, "at": _now()})
    save(processed_dir, day, ov)


def confirm(processed_dir: Path, day: date, note: str) -> None:
    ov = load(processed_dir, day)
    ov["owner_confirmed"] = {"at": _now(), "note": note}
    save(processed_dir, day, ov)


LABELLED_ID = re.compile(r"^E\d+[a-z]?$")  # the gameplan's own entry labels (E1, E5b, ...)


def entry_selector(zone: dict[str, Any], zones: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """How an override finds this entry again after a re-parse. With `zones` (the gameplan's entries), a side +
    level/zone selector that would match more than one entry also names the entry, so it can never hit the wrong one
    (a re-parse that renames it then shows as a review flag)."""
    if LABELLED_ID.match(zone.get("id") or ""):
        return {"id": zone["id"]}
    level = (zone.get("trigger") or {}).get("nq_price")
    sel = {"side": zone.get("side"), "trigger.nq_price": level} if level is not None \
        else {"side": zone.get("side"), "zone": zone.get("zone")}
    if zones is not None and sum(all(_get(z, k) == v for k, v in sel.items()) for z in zones) > 1:
        sel["id"] = zone.get("id")
    return sel


def _get(item: dict[str, Any], dotted: str) -> Any:
    node: Any = item
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


_MISSING = object()


def _step(node: Any, step: Any) -> tuple[Any, Any] | None:
    """(container, key) for one path step, or None if it matches nothing or more than one list item."""
    if isinstance(step, dict):
        if not isinstance(node, list):
            return None
        hits = [i for i, item in enumerate(node)
                if isinstance(item, dict) and all(_get(item, k) == v for k, v in step.items())]
        return (node, hits[0]) if len(hits) == 1 else None
    if isinstance(node, dict) and step in node:
        return node, step
    return None


def _apply_op(doc: dict[str, Any], op: dict[str, Any]) -> str | None:
    """Apply one op; returns the flag-field label of what it set (e.g. "zones[E1].targets", "regime_line",
    "scenarios[S2]"), or None if it matches nothing."""
    node: Any = doc
    *walk, last = op["path"]
    label: list[str] = []
    for step in walk:
        hit = _step(node, step)
        if hit is None:
            return None
        item = hit[0][hit[1]]
        if isinstance(step, dict):
            label[-1:] = [f"{label[-1] if label else ''}[{item.get('id')}]"]
        else:
            label.append(str(step))
        node = item
    hit = _step(node, last)
    if hit is None:
        return None
    hit[0][hit[1]] = copy.deepcopy(op["value"])
    label.append(str(last))
    if label[0].startswith("scenario_sets"):  # flags name scenarios as "...scenarios[S2].prob"
        return next((p for p in label if p.startswith("scenarios[")), "scenario_sets")
    return ".".join(label[:2]) if label[0].startswith("zones[") else label[0]


def _resolved(field: str, labels: list[str]) -> bool:
    """A review flag on a field the owner has set by override is settled (e.g. "zones[E1].targets",
    "hvl,regime_line" after regime_line was set)."""
    parts = [field] if "[" in field else field.split(",")  # "hvl,regime_line" pairs; entry names may hold commas
    for lab in labels:
        if lab.startswith("scenarios["):
            if lab in field:
                return True
        elif any(p == lab or p.startswith(lab + ".") or p.startswith(lab + "[") for p in parts):
            return True
    return False


def apply(doc: dict[str, Any], ov: dict[str, Any]) -> dict[str, Any]:
    """The effective gameplan: `doc` with the owner's ops applied and `owner_confirmed` set. A review flag on a field
    an override sets becomes info ("resolved by owner override"); `status` is recomputed: ok = no review flag."""
    out = copy.deepcopy(doc)
    applied, labels = [], []
    for op in ov.get("ops") or []:
        lab = _apply_op(out, op)
        if lab is not None:
            applied.append(op)
            labels.append(lab)
        else:
            out.setdefault("flags", []).append({
                "field": "owner_override", "origin": "owner", "review": True,
                "reason": f"override at {json.dumps(op['path'])} matches no single item of the parsed gameplan "
                          f"(owner's value {json.dumps(op['value'])}: {op['reason']})"})
    for f in out.get("flags") or []:
        if f.get("review", True) and f.get("origin") != "owner" and _resolved(f.get("field", ""), labels):
            f["review"] = False
            f["reason"] += " [resolved by owner override]"
    out["owner_overrides"] = applied
    out["owner_confirmed"] = ov.get("owner_confirmed")
    out["status"] = "flagged" if any(f.get("review", True) for f in out.get("flags") or []) else "ok"
    return out
