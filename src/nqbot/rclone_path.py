"""Which rclone to run (owner 2026-10-30): `nqbot status-push` failed with "[WinError 2]" in a PowerShell opened
before winget installed rclone (winget adds rclone to the user PATH, which a shell or task started earlier doesn't
have yet).

Order: `rclone_path` in config/local.yaml (per machine, uncommitted), else `backup.rclone_exe` found on PATH
(shutil.which), else rclone.exe in the WinGet Links folder, else in winget's own package folder (on the PC winget
put it in WinGet\\Packages\\Rclone.Rclone_<source>\\rclone-v<version>-windows-amd64\\ and added that to PATH, with
no Links shim; the highest version wins). The callers print the path used; none found -> RcloneNotFound with the
fix, never WinError 2."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable

PACKAGE_GLOB = "Rclone.Rclone_*/rclone-v*/rclone.exe"


class RcloneNotFound(Exception):
    pass


def winget_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Microsoft" / "WinGet"


def _version(exe: Path) -> tuple[int, ...]:
    m = re.match(r"rclone-v(\d+(?:\.\d+)*)", exe.parent.name)
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def find_rclone(settings: dict[str, Any], *, which: Callable[[str], str | None] | None = None,
                winget: Path | None = None) -> tuple[str, str]:
    """(path, where it came from). Raises RcloneNotFound with the fix."""
    local = settings.get("rclone_path")
    if local:
        if not Path(local).is_file():
            raise RcloneNotFound(f"rclone not found at rclone_path {local} (config/local.yaml): fix the path, or "
                                 "remove the line to use PATH or winget's folders")
        return str(local), "from config/local.yaml rclone_path"
    name = settings["backup"]["rclone_exe"]
    on_path = (which or shutil.which)(name)
    if on_path:
        return on_path, "found on PATH"
    winget = winget or winget_dir()
    links = winget / "Links" / "rclone.exe"
    if links.is_file():
        return str(links), "WinGet Links folder; not on this shell's PATH"
    packages = sorted((winget / "Packages").glob(PACKAGE_GLOB), key=_version)
    if packages:
        return str(packages[-1]), "winget package folder; not on this shell's PATH"
    raise RcloneNotFound(f"rclone not found: rclone_path isn't set in config/local.yaml, {name!r} isn't on this "
                         f"shell's PATH, and there's no rclone.exe in {winget / 'Links'} or "
                         f"{winget / 'Packages'}. Fix: install it (winget install Rclone.Rclone) and open a new "
                         "terminal, or set rclone_path: <full path to rclone.exe> in config/local.yaml")


def rclone_line(settings: dict[str, Any], **kw: Any) -> tuple[bool, str]:
    """For preflight and keys-check: (found, 'rclone: <path> (<where>)' or the not-found message)."""
    try:
        path, how = find_rclone(settings, **kw)
    except RcloneNotFound as e:
        return False, str(e)
    return True, f"rclone: {path} ({how})"
