"""Load the YAML configs in config/ (SPEC §11 "Where settings live").

config/local.yaml (uncommitted) overrides settings.yaml on one machine, but only for machine
resources: nothing that changes results may go there (§11 One machine).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
# rclone_path, test_parallel: owner 2026-10-30
LOCAL_KEYS = frozenset({"max_workers", "max_fold_mem_gb", "low_memory", "rclone_path", "test_parallel"})


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
        if "test_parallel" in local and not isinstance(local["test_parallel"], bool):
            raise ConfigError(f"{local_path}: test_parallel must be true or false, found {local['test_parallel']!r}")
        settings.update(local)
    if settings.get("months"):  # the ledger written at step 4b: all 12 months, strictly formatted (§7 rule 2b)
        from nqbot.months import LedgerInvalid, validate

        try:
            validate(settings["months"], full=True)
        except LedgerInvalid as e:
            raise ConfigError(str(e)) from None
    return settings


def load_features(config_dir: Path | None = None) -> dict[str, Any]:
    return load_yaml((config_dir or CONFIG_DIR) / "features.yaml") or {}


def load_accounts(config_dir: Path | None = None) -> list[dict[str, Any]]:
    from nqbot import risk  # §6.1: refuse a risk fraction outside 1/2 .. 1/4 (owner 2026-10-14)

    data = load_yaml((config_dir or CONFIG_DIR) / "accounts.yaml") or {}
    accounts = data.get("accounts") or []
    for a in accounts:
        try:
            risk.divisor_of(a)
        except risk.RiskConfigError as e:
            raise ConfigError(f"accounts.yaml, account {a.get('name')!r}: {e}") from e
    return accounts
