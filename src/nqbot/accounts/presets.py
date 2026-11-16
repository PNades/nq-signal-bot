"""Account presets (SPEC §6.7 preset table, §9b item 1 "preset picker" and "Save as preset").

Built-in presets: the committed `config/presets.yaml` (every §6.7 stage: Rapid EOD 25k and Builder 25k × evaluation /
funded / after first payout; Pro 50k and Lucid Flex 25k × evaluation / funded). The owner's own presets (Save as
preset) live in the panel file `<data_dir>/panel/presets.yaml` and appear in the same picker, marked "saved".
A preset fills every account field except the account's own: its name, active / draft / demo, the webhook variable,
the copied-account count and the payout record.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nqbot.config import CONFIG_DIR, load_yaml

PRESETS_FILE = "presets.yaml"
# fields that belong to the account, never to a preset (§9b: a preset "fills every field" of the rules)
ACCOUNT_OWN_FIELDS = frozenset({"name", "active", "draft", "demo", "traderspost_webhook_env", "copied_accounts",
                                "payouts", "first_funded_trade_date", "pending_risk", "preset"})


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    firm: str
    stage: str
    fields: dict[str, Any]
    builtin: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "firm": self.firm, "stage": self.stage, "fields": self.fields,
                "builtin": self.builtin}


def builtin_presets(config_dir: Path | None = None) -> list[Preset]:
    data = load_yaml((config_dir or CONFIG_DIR) / PRESETS_FILE) or {}
    common = dict(data.get("common") or {})
    out = []
    for p in data.get("presets") or []:
        fields = {**common, **(p.get("fields") or {})}
        out.append(Preset(id=p["id"], label=p["label"], firm=p["firm"], stage=p["stage"], fields=fields, builtin=True))
    return out


def preset_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """An account's rule fields only (what Save as preset keeps)."""
    return {k: v for k, v in fields.items() if k not in ACCOUNT_OWN_FIELDS}


def apply_preset(preset: Preset, account: dict[str, Any] | None = None) -> dict[str, Any]:
    """The account's own fields kept, every rule field from the preset; `preset` records which stage filled it."""
    own = {k: v for k, v in (account or {}).items() if k in ACCOUNT_OWN_FIELDS}
    return {**own, **preset_fields(preset.fields), "preset": preset.id}  # the account's own fields (its name) first
