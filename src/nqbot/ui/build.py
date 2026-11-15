"""`nqbot ui build` (SPEC §9b Front end and build, §13 Q-step14a (2), (3)).

`npm ci` only (never `npm install`), from the committed `ui/web/package-lock.json`; then `npm audit`: the build fails
on a high or critical advisory and lists the rest; then the Vite build into the git-ignored `ui/web/dist/`, recording
the sources' digest beside it (`dist/.creamflow_build.json`), which `nqbot ui` checks before it serves the SPA.
Each machine builds its own copy; Node is needed at build time only.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nqbot.storage import atomic_write_json
from nqbot.ui.spa import BUILD_INFO, WEB_DIR, source_digest

FAIL_LEVELS = ("high", "critical")
LEVELS = ("info", "low", "moderate", "high", "critical")

Runner = Callable[[list[str], Path], subprocess.CompletedProcess]


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def audit_summary(audit_json: str) -> tuple[dict[str, int], list[str]]:
    """Counts by severity and one line per advisory from `npm audit --json` (npm 7+ format)."""
    data = json.loads(audit_json or "{}")
    if "error" in data and "metadata" not in data:
        raise ValueError(f"npm audit failed: {data['error'].get('summary') or data['error']}")
    counts = {k: int((data.get("metadata", {}).get("vulnerabilities") or {}).get(k, 0)) for k in LEVELS}
    lines = []
    for name, v in sorted((data.get("vulnerabilities") or {}).items()):
        via = [x.get("title", "") for x in v.get("via", []) if isinstance(x, dict)]
        lines.append(f"{v.get('severity', '?')}: {name} {v.get('range', '')} {'; '.join(t for t in via if t)}".strip())
    return counts, lines


def build(web_dir: Path = WEB_DIR, runner: Runner = _run, npm: str | None = None, out: Callable[[str], None] = print) -> int:
    npm = npm or shutil.which("npm") or shutil.which("npm.cmd")
    info_path = web_dir / "dist" / BUILD_INFO
    if not npm:
        out("npm not found: install Node.js (needed at build time only) and run `uv run nqbot ui build` again.")
        return 2
    if not (web_dir / "package-lock.json").is_file():
        out(f"{web_dir / 'package-lock.json'} is missing: the build installs only from the committed lockfile.")
        return 2

    def fail(msg: str) -> int:
        out(msg)
        try:
            if info_path.parent.is_dir():
                atomic_write_json(info_path, {"ok": False, "error": msg, "built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        except OSError:
            pass
        return 1

    out("npm ci (from the committed package-lock.json) ...")
    r = runner([npm, "ci", "--no-fund", "--no-audit"], web_dir)
    if r.returncode != 0:
        return fail(f"npm ci failed:\n{(r.stderr or r.stdout).strip()[-2000:]}")

    out("npm audit ...")
    r = runner([npm, "audit", "--json"], web_dir)
    try:
        counts, lines = audit_summary(r.stdout)
    except ValueError as e:
        return fail(f"npm audit couldn't run ({e}); the build needs it (SPEC §9b). Check the network and try again.")
    for line in lines:
        out(f"  {line}")
    out("  advisories: " + ", ".join(f"{k} {counts[k]}" for k in LEVELS))
    bad = sum(counts[k] for k in FAIL_LEVELS)
    if bad:
        return fail(f"npm audit found {bad} high or critical advisor{'y' if bad == 1 else 'ies'}: the build stops (SPEC §9b).")

    digest = source_digest(web_dir)
    out("vite build ...")
    r = runner([npm, "run", "build"], web_dir)
    if r.returncode != 0:
        return fail(f"vite build failed:\n{(r.stderr or r.stdout).strip()[-3000:]}")
    if source_digest(web_dir) != digest:
        return fail("the sources changed during the build; run `uv run nqbot ui build` again.")
    info: dict[str, Any] = {"ok": True, "digest": digest, "built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            "audit": counts}
    atomic_write_json(info_path, info)
    out(f"Built {web_dir / 'dist'} (sources {digest[:12]}). Start it with `uv run nqbot ui`.")
    return 0
