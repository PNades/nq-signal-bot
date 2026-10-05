"""Load the YAML configs in config/ (SPEC §11 "Where settings live").

config/local.yaml (uncommitted) overrides settings.yaml on one machine, but only for machine
resources: nothing that changes results may go there (§11 One machine).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
LOCAL_KEYS = frozenset({"max_workers", "max_fold_mem_gb", "low_memory"})


class ConfigError(Exception):
    pass


def load_yaml(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return YAML(typ="safe").load(f)


def load_settings(config_dir: Path | None = None) -> dict[str, Any]:
    """settings.yaml, with config/local.yaml's machine-resource keys applied on top."""
    config_dir = config_dir or CONFIG_DIR
    settings = load_yaml(config_dir / "settings.yaml") or {}
    local_path = config_dir / "local.yaml"
    if local_path.exists():
        local = load_yaml(local_path) or {}
        unknown = sorted(set(local) - LOCAL_KEYS)
        if unknown:
            raise ConfigError(
                f"{local_path}: only {sorted(LOCAL_KEYS)} may be overridden locally, found {unknown}"
            )
        settings.update(local)
    return settings


def load_features(config_dir: Path | None = None) -> dict[str, Any]:
    return load_yaml((config_dir or CONFIG_DIR) / "features.yaml") or {}


def load_accounts(config_dir: Path | None = None) -> list[dict[str, Any]]:
    data = load_yaml((config_dir or CONFIG_DIR) / "accounts.yaml") or {}
    return data.get("accounts") or []
