"""Secrets from the environment or the repo's .env (CLAUDE.md rule 9: secrets live only in .env).

A secret is never logged or printed: `Secret` hides its value in repr/str.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class MissingSecret(Exception):
    pass


class Secret:
    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(***)"

    __str__ = __repr__


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines; blank lines and # comments skipped; surrounding quotes removed."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[key] = value
    return out


def get_secret(name: str, env_file: Path | None = None) -> Secret:
    value = os.environ.get(name) or read_env_file(env_file or REPO_ROOT / ".env").get(name)
    if not value:
        raise MissingSecret(f"{name} is not set (add it to .env)")
    return Secret(value)
