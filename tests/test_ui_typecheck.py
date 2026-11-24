"""The Control Panel's TypeScript type check runs with the tests (owner 2026-11-24): a type error in `ui/web/src` fails
`nqbot test`. The Vite build strips types without checking them (a broken type-only file still builds; 31c5e2b's
`types.ts` did), so this is the check.

It runs the project's own `typescript` from `ui/web/node_modules` (`npm ci`, done by `uv run nqbot ui build` on each
machine) with `tsconfig.json` (`noEmit`: nothing is written). A machine without Node or those modules fails here with
the fix, rather than skipping: a skipped check would let a type error through."""

import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "ui" / "web"
TSC = WEB / "node_modules" / "typescript" / "bin" / "tsc"
TIMEOUT_SEC = 300


def test_the_panel_has_no_typescript_errors():
    node = shutil.which("node")
    if node is None:
        pytest.fail("Node isn't on PATH: install Node, then `uv run nqbot ui build` (the type check needs it)")
    if not TSC.is_file():
        pytest.fail(f"{TSC} is missing: run `uv run nqbot ui build` (npm ci) first")
    r = subprocess.run([node, str(TSC), "--noEmit", "-p", str(WEB / "tsconfig.json")], cwd=WEB,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT_SEC)
    assert r.returncode == 0, f"tsc --noEmit failed ({r.returncode}):\n{r.stdout}{r.stderr}"
