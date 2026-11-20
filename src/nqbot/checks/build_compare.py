"""`nqbot step13 compare-builds A B`: two builds' files compared session by session (owner 2026-11-19, the pre-13d
proof of SPEC §13 Q-review-stack (b): recording the counted stack members changes no feature value).

Read-only, no lock, development months only (§7 rule 2b: only counts of equal / different are reported, never
values). For every session build B has finished (`days/<D>.json` with pass 2), which build A must also have:
- each per-session parquet file under B's folder (touches, day types, outcomes, levels, events, decisions, decision
  rows, level events and any other folder of `<D>.parquet` files; the labeler's folders `labels`, `pairs`, `fills`,
  `paths` and `logs` are not build files and are skipped) has A's twin, the same rows in the same order, and every
  column of A equal in B (nulls equal, NaN equal); B may add only the columns named by `allow_new`; the file
  metadata (it names the build key) is not compared;
- the pass-1 checkpoint `state/state_<D>.json` is equal;
- the day summary `days/<D>.json` is equal without its timings (every `*_sec` field).
The report (`<reports_dir>/step13/compare_builds_<A>_<B>.md`) lists, per folder, the files compared and those that
differ (first examples by column), and for each allowed new column the files and rows that carry it; the check
passes only if nothing differs and every allowed new column was seen.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from nqbot import months as months_mod
from nqbot.checks.step8b import Env
from nqbot.storage import atomic_write_text

SKIP_DIRS = frozenset({"labels", "pairs", "fills", "paths", "logs", "state", "days"})
MAX_EXAMPLES = 5


class CompareRefused(Exception):
    pass


def build_folder(env: Env, key: str) -> Path:
    """A build's folder by its key, whatever its features_version (`v<n>_<key>`)."""
    root = env.paths.data_dir / "processed" / "features"
    found = sorted(root.glob(f"v*_{key}")) if root.exists() else []
    if len(found) != 1:
        raise CompareRefused(f"build {key}: {'no folder' if not found else f'{len(found)} folders'} under {root}")
    return found[0]


def finished_days(folder: Path, settings: dict | None = None) -> list[date]:
    """The sessions with pass 2 done; with `settings`, any session outside the development months refuses before a
    file is opened (§7 rule 2b)."""
    files = sorted((folder / "days").glob("*.json"))
    if settings is not None:
        for p in files:
            months_mod.require_development(date.fromisoformat(p.stem), settings)
    return _finished_among(folder, [date.fromisoformat(p.stem) for p in files])


def _finished_among(folder: Path, days: list[date]) -> list[date]:
    out = []
    for d in days:
        try:
            doc = json.loads((folder / "days" / f"{d.isoformat()}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if doc.get("pass2"):
            out.append(d)
    return out


def strip_timings(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: strip_timings(v) for k, v in x.items() if not str(k).endswith("_sec")}
    if isinstance(x, list):
        return [strip_timings(v) for v in x]
    return x


def same_value(a: Any, b: Any) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return a == b or (math.isnan(a) and math.isnan(b))
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same_value(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same_value(a[k], b[k]) for k in a)
    return a == b


def compare_tables(a, b, allow_new: frozenset[str]) -> dict[str, Any]:
    """{'diff': [reasons], 'new': {column: rows carrying a value}} for two pyarrow tables."""
    diff: list[str] = []
    if a.num_rows != b.num_rows:
        diff.append(f"rows {a.num_rows} vs {b.num_rows}")
    missing = [c for c in a.column_names if c not in b.column_names]
    if missing:
        diff.append(f"columns missing in B: {', '.join(missing[:MAX_EXAMPLES])}")
    extra = [c for c in b.column_names if c not in a.column_names]
    bad = [c for c in extra if c not in allow_new]
    if bad:
        diff.append(f"new columns not allowed: {', '.join(bad[:MAX_EXAMPLES])}")
    new = {c: sum(1 for v in b.column(c).to_pylist() if v is not None) for c in extra if c in allow_new}
    if a.num_rows == b.num_rows:
        for c in a.column_names:
            if c not in b.column_names:
                continue
            ca, cb = a.column(c), b.column(c)
            if ca.type == cb.type and ca.equals(cb):
                continue
            va, vb = ca.to_pylist(), cb.to_pylist()
            n = sum(1 for x, y in zip(va, vb) if not same_value(x, y))
            if n:
                diff.append(f"column {c}: {n} rows differ")
    return {"diff": diff, "new": new}


def compare(env: Env, key_a: str, key_b: str, allow_new: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    import pyarrow.parquet as pq

    allow = frozenset(allow_new)
    fa, fb_ = build_folder(env, key_a), build_folder(env, key_b)
    days = finished_days(fb_, env.settings)
    if not days:
        raise CompareRefused(f"build {key_b} has no finished session")
    have_a = set(_finished_among(fa, days))     # only B's sessions of A are opened
    absent = [d for d in days if d not in have_a]
    if absent:
        raise CompareRefused(f"build {key_a} lacks {len(absent)} of build {key_b}'s sessions (first {absent[0]})")
    folders = sorted({p.name for f in (fa, fb_) for p in f.iterdir() if p.is_dir() and p.name not in SKIP_DIRS})
    per: dict[str, Counter] = {}
    examples: list[str] = []
    new_seen: Counter = Counter()
    new_rows: Counter = Counter()
    for name in folders:
        c = per.setdefault(name, Counter())
        for d in days:
            iso = d.isoformat()
            pb, pa_ = fb_ / name / f"{iso}.parquet", fa / name / f"{iso}.parquet"
            if not pb.exists() and not pa_.exists():
                continue
            c["files"] += 1
            if pb.exists() != pa_.exists():
                c["differ"] += 1
                examples.append(f"{name}/{iso}: only in {'B' if pb.exists() else 'A'}")
                continue
            res = compare_tables(pq.read_table(pa_), pq.read_table(pb), allow)
            for col, n in res["new"].items():
                new_seen[(name, col)] += 1
                new_rows[(name, col)] += n
            if res["diff"]:
                c["differ"] += 1
                examples.append(f"{name}/{iso}: {'; '.join(res['diff'])}")
    for name, sub, norm in (("state", "state_{}.json", lambda x: x), ("days", "{}.json", strip_timings)):
        c = per.setdefault(name, Counter())
        for d in days:
            pa_, pb = fa / name / sub.format(d.isoformat()), fb_ / name / sub.format(d.isoformat())
            c["files"] += 1
            if not (pa_.exists() and pb.exists()):
                c["differ"] += 1
                examples.append(f"{name}/{pb.name}: missing in {'A' if not pa_.exists() else 'B'}")
                continue
            xa = norm(json.loads(pa_.read_text(encoding="utf-8")))
            xb = norm(json.loads(pb.read_text(encoding="utf-8")))
            if not same_value(xa, xb):
                c["differ"] += 1
                examples.append(f"{name}/{pb.name}: differs")
    unseen = [col for col in allow if not any(k[1] == col for k in new_seen)]
    ok = not examples and not unseen
    return {"a": key_a, "b": key_b, "days": days, "per": per, "examples": examples, "new_seen": new_seen,
            "new_rows": new_rows, "unseen": unseen, "allow_new": sorted(allow), "ok": ok}


def report(env: Env, key_a: str, key_b: str, allow_new: list[str] | tuple[str, ...] = ()) -> tuple[Path, bool]:
    res = compare(env, key_a, key_b, allow_new)
    days = res["days"]
    lines = [f"# Build files compared: A {key_a} vs B {key_b}", "",
             f"{len(days)} sessions ({days[0]} to {days[-1]}), every session build B finished. B may add: "
             f"{', '.join(res['allow_new']) or 'nothing'}. Result: **{'PASS' if res['ok'] else 'FAIL'}**.", "",
             "| folder | files | differ |", "|---|---:|---:|"]
    for name, c in res["per"].items():
        lines.append(f"| {name} | {c['files']} | {c['differ']} |")
    if res["new_seen"]:
        lines += ["", "New columns in B (allowed):", ""]
        for (name, col), n in sorted(res["new_seen"].items()):
            lines.append(f"- {name}: `{col}` in {n} files, {res['new_rows'][(name, col)]:,} rows with a value")
    for col in res["unseen"]:
        lines.append(f"- allowed new column `{col}` was never seen in B: the check fails")
    if res["examples"]:
        lines += ["", f"Differences ({len(res['examples'])}, first {MAX_EXAMPLES * 4}):", ""]
        lines += [f"- {x}" for x in res["examples"][:MAX_EXAMPLES * 4]]
    path = env.paths.reports_dir / "step13" / f"compare_builds_{key_a}_{key_b}.md"
    atomic_write_text(path, "\n".join(lines) + "\n")
    return path, res["ok"]
