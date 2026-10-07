"""Owner overrides on top of a parsed gameplan (owner 2026-10-07, SPEC §3.4).

processed/gameplans/overrides/<date>.json holds the owner's choices apart from the parsed file, so a re-parse never
loses them; `effective()` applies them on top of processed/gameplans/<date>.json, and everything downstream (the
step-9 report, step 11) reads the effective gameplan.

  {"date": "2026-08-18",
   "owner_confirmed": {"at": "...", "note": "..."} | null,
   "ops": [{"path": ["zones", {"id": "S2-B-29650"}, "zone"], "value": [29650, 29700], "reason": "...", "at": "..."}]}

A path step is a dict key, or a {key: value} selector picking the first list item whose fields all match (a zone by
its id, a flag by field + reason), so an override follows its target, not a list position. An op that no longer
matches (a re-parse renamed the zone) becomes a review flag on the effective gameplan; it is never dropped silently.
"""

from __future__ import annotations

import copy
import json
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


def _step(node: Any, step: Any) -> tuple[Any, Any] | None:
    """(container, key) for one path step, or None if it doesn't match."""
    if isinstance(step, dict):
        if not isinstance(node, list):
            return None
        for i, item in enumerate(node):
            if isinstance(item, dict) and all(item.get(k) == v for k, v in step.items()):
                return node, i
        return None
    if isinstance(node, dict) and step in node:
        return node, step
    return None


def _apply_op(doc: dict[str, Any], op: dict[str, Any]) -> bool:
    node: Any = doc
    *walk, last = op["path"]
    for step in walk:
        hit = _step(node, step)
        if hit is None:
            return False
        node = hit[0][hit[1]]
    hit = _step(node, last)
    if hit is None:
        return False
    hit[0][hit[1]] = copy.deepcopy(op["value"])
    return True


def apply(doc: dict[str, Any], ov: dict[str, Any]) -> dict[str, Any]:
    """The effective gameplan: `doc` with the owner's ops applied and `owner_confirmed` set. `status` is recomputed
    from the flags after the ops: ok = no review flag."""
    out = copy.deepcopy(doc)
    applied = []
    for op in ov.get("ops") or []:
        if _apply_op(out, op):
            applied.append(op)
        else:
            out.setdefault("flags", []).append({
                "field": "owner_override", "origin": "owner", "review": True,
                "reason": f"override at {json.dumps(op['path'])} no longer matches the parsed gameplan "
                          f"(owner's value {json.dumps(op['value'])}: {op['reason']})"})
    out["owner_overrides"] = applied
    out["owner_confirmed"] = ov.get("owner_confirmed")
    out["status"] = "flagged" if any(f.get("review", True) for f in out.get("flags") or []) else "ok"
    return out
