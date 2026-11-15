"""The SPA build: where it lives, whether it is current, and the page the server serves from it (SPEC §9b).

`nqbot ui build` writes `ui/web/dist/` and, beside it, `dist/.creamflow_build.json` with the digest of the sources it
was built from. `nqbot ui` serves the SPA only when that digest equals the current sources' digest; a missing, stale
or failed build is refused with its reason, and `/` then shows that reason with the kill/flat/pause controls inline.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from nqbot.ui.security import script_hash

REPO_ROOT = Path(__file__).resolve().parents[3]
WEB_DIR = REPO_ROOT / "ui" / "web"
BUILD_INFO = ".creamflow_build.json"
TOKEN_PLACEHOLDER = "__CF_TOKEN__"
NONCE_PLACEHOLDER = "__CF_NONCE__"
# The sources a build is made from (SPEC §9b: "recording the sources' digest beside it")
SOURCE_FILES = ("index.html", "package.json", "package-lock.json", "vite.config.ts", "tsconfig.json")
SOURCE_DIRS = ("src", "public")
_WATCHDOG = re.compile(r'<script id="cf-watchdog">(.*?)</script>', re.S)


def source_digest(web_dir: Path = WEB_DIR) -> str:
    """sha256 over the relative path and bytes of every source file, in sorted order (line endings normalised, so a
    checkout with CRLF and one with LF agree)."""
    h = hashlib.sha256()
    files: list[Path] = [web_dir / f for f in SOURCE_FILES if (web_dir / f).is_file()]
    for d in SOURCE_DIRS:
        base = web_dir / d
        if base.is_dir():
            files.extend(p for p in base.rglob("*") if p.is_file())
    for p in sorted(files, key=lambda q: q.relative_to(web_dir).as_posix()):
        rel = p.relative_to(web_dir).as_posix()
        data = p.read_bytes().replace(b"\r\n", b"\n")
        h.update(rel.encode() + b"\0" + hashlib.sha256(data).digest())
    return h.hexdigest()


@dataclass(frozen=True)
class SpaState:
    ok: bool
    reason: str
    dist: Path


def spa_state(web_dir: Path = WEB_DIR) -> SpaState:
    dist = web_dir / "dist"
    info_path = dist / BUILD_INFO
    if not (dist / "index.html").is_file() or not info_path.is_file():
        return SpaState(False, "The panel's app isn't built on this machine. Run `uv run nqbot ui build`.", dist)
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return SpaState(False, f"The app's build record can't be read ({e}). Run `uv run nqbot ui build`.", dist)
    if not info.get("ok"):
        return SpaState(False, f"The last app build failed: {info.get('error', 'no reason recorded')}. Fix it and run `uv run nqbot ui build`.", dist)
    current = source_digest(web_dir)
    if info.get("digest") != current:
        return SpaState(False, "The app's build is older than its sources (stale). Run `uv run nqbot ui build`.", dist)
    return SpaState(True, "", dist)


def watchdog_hashes(index_html: str) -> list[str]:
    return [script_hash(m.group(1)) for m in _WATCHDOG.finditer(index_html)]


def page_html(index_html: str, token: str, nonce: str = "") -> str:
    """The SPA's page with this launch's token in its <meta name="cf-token"> and its style nonce in
    <meta name="cf-nonce">."""
    return index_html.replace(TOKEN_PLACEHOLDER, token).replace(NONCE_PLACEHOLDER, nonce)
