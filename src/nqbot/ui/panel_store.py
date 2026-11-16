"""The panel's own files on the SSD (SPEC §9b, §6.7; owner 2026-11-15): `<data_dir>/panel/`
    accounts.yaml   the accounts (the §6.7 fields; config/accounts.yaml is only the committed example)
    presets.yaml    the owner's saved presets (Save as preset; the built-in ones are config/presets.yaml)
    ui.yaml         panel settings (Effects)
Rules:
- never a secret (the schema refuses URLs, keys and logins; only the webhook's .env variable NAME is kept);
- every write: under the panel lock, after re-checking the file's hash against the one the panel loaded (a mismatch is
  refused: the page reloads and shows the newer file), the previous version copied to `panel/history/`, then an atomic
  replace; every change appended to `<log_dir>/ui_changes.jsonl` with a timestamp (which settings were active when);
- while a session's engine is down mid-session, edits are refused (KILL and Flatten still work); from step 22 every
  write goes through the engine while it runs (§9b). Before step 22 there is no engine: the panel writes directly;
- the first time the folder has no accounts file, it is created with the owner's draft accounts (mffur1 = MFFU Rapid
  EOD 25k evaluation, mffub1 = MFFU Builder 25k evaluation), `draft: true` until he checks and saves them.
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from nqbot import storage
from nqbot.accounts.presets import Preset, apply_preset, builtin_presets
from nqbot.jobs.lock import JobLock, LockHeld
from nqbot.storage import Paths

PANEL_DIR = "panel"
FILES = ("accounts.yaml", "presets.yaml", "ui.yaml")
CHANGE_LOG = "ui_changes.jsonl"
LOCK_FILE = ".panel.lock"
HISTORY_KEEP = 200  # versions kept per file in panel/history/
# The owner's accounts, pre-filled from the §6.7 presets for him to check (owner 2026-11-15)
DRAFTS = (
    ("mffur1", "mffu_rapid_eod_25k_evaluation", "TP_WEBHOOK_MFFUR1"),
    ("mffub1", "mffu_builder_25k_evaluation", "TP_WEBHOOK_MFFUB1"),
)
HEADER = {
    "accounts.yaml": "# CreamFlow panel: the owner's accounts (SPEC §6.7, §9b). Written by the Accounts and Settings pages;\n"
                     "# never holds a secret (only the webhook's .env variable name). Previous versions: panel/history/.\n",
    "presets.yaml": "# CreamFlow panel: the owner's saved presets (Save as preset, §9b). Built-in presets: config/presets.yaml.\n",
    "ui.yaml": "# CreamFlow panel settings (Effects).\n",
}


class PanelUnavailable(RuntimeError):
    """The SSD isn't found, or edits are refused right now (engine down mid-session)."""


class Conflict(RuntimeError):
    """The file changed since the panel loaded it."""


def _yaml() -> YAML:
    y = YAML(typ="safe")
    y.default_flow_style = False
    y.sort_base_mapping_type_on_output = False  # type: ignore[attr-defined]
    return y


def dump(data: Any) -> str:
    buf = io.StringIO()
    _yaml().dump(data, buf)
    return buf.getvalue()


def file_hash(text: str | None) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


@dataclass
class Loaded:
    data: Any
    hash: str


def draft_accounts(presets: list[Preset]) -> list[dict[str, Any]]:
    by_id = {p.id: p for p in presets}
    out = []
    for name, pid, env in DRAFTS:
        acc = apply_preset(by_id[pid], {"name": name, "active": True, "draft": True, "demo": False,
                                        "traderspost_webhook_env": env, "copied_accounts": 0, "payouts": []})
        out.append(acc)
    return out


class PanelStore:
    def __init__(self, paths_fn: Callable[[], Paths | None], *, config_dir: Path | None = None,
                 edits_allowed: Callable[[], str | None] = lambda: None):
        self.paths_fn = paths_fn
        self.config_dir = config_dir
        # returns a reason when edits are refused right now (engine down mid-session, from step 22), else None
        self.edits_allowed = edits_allowed

    # ── paths ──
    def _paths(self) -> Paths:
        p = self.paths_fn()
        if p is None:
            raise PanelUnavailable("the SSD isn't found: the panel's files live on it (KILL and FLAT ALL still work)")
        return p

    def folder(self) -> Path:
        return self._paths().data_dir / PANEL_DIR

    # ── reading ──
    def _read(self, name: str) -> Loaded:
        path = self.folder() / name
        if not path.exists():
            if name == "accounts.yaml":
                return self._seed_accounts()
            return Loaded({} if name != "presets.yaml" else {"presets": []}, file_hash(None))
        text = path.read_text(encoding="utf-8")
        return Loaded(_yaml().load(text) or {}, file_hash(text))

    def _seed_accounts(self) -> Loaded:
        data = {"accounts": draft_accounts(builtin_presets(self.config_dir))}
        self._write("accounts.yaml", data, expected=file_hash(None), what="seeded the draft accounts mffur1, mffub1 "
                                                                        "from the §6.7 presets")
        return self._read("accounts.yaml")

    def accounts(self) -> Loaded:
        loaded = self._read("accounts.yaml")
        loaded.data = loaded.data if isinstance(loaded.data, dict) else {}
        loaded.data.setdefault("accounts", [])
        return loaded

    def saved_presets(self) -> Loaded:
        loaded = self._read("presets.yaml")
        loaded.data = loaded.data if isinstance(loaded.data, dict) else {}
        loaded.data.setdefault("presets", [])
        return loaded

    def ui(self) -> Loaded:
        return self._read("ui.yaml")

    def all_presets(self) -> list[Preset]:
        own = [Preset(id=p["id"], label=p["label"], firm=p["firm"], stage=p["stage"], fields=p["fields"], builtin=False)
               for p in self.saved_presets().data["presets"]]
        return builtin_presets(self.config_dir) + own

    # ── writing ──
    def save(self, name: str, data: Any, expected: str, what: str, detail: dict[str, Any] | None = None) -> str:
        reason = self.edits_allowed()
        if reason:
            raise PanelUnavailable(reason)
        return self._write(name, data, expected, what, detail)

    def _write(self, name: str, data: Any, expected: str, what: str, detail: dict[str, Any] | None = None) -> str:
        if name not in FILES:
            raise ValueError(name)
        from nqbot.accounts.schema import refuse_secrets

        refuse_secrets(data)
        paths = self._paths()
        folder = paths.data_dir / PANEL_DIR
        folder.mkdir(parents=True, exist_ok=True)
        lock = JobLock(folder / LOCK_FILE, "panel", unlock_cmd="delete the panel lock file only if no panel runs")
        try:
            lock.acquire()
        except LockHeld as e:
            raise Conflict(f"another panel is saving right now ({e}); try again") from e
        try:
            path = folder / name
            current = path.read_text(encoding="utf-8") if path.exists() else None
            if file_hash(current) != expected:
                raise Conflict(f"{name} changed since this page loaded it; the page reloads with the newer file")
            now = datetime.now(timezone.utc)
            if current is not None:
                hist = folder / "history"
                hist.mkdir(exist_ok=True)
                stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
                storage.atomic_write_text(hist / f"{path.stem}.{stamp}.yaml", current)
                olds = sorted(hist.glob(f"{path.stem}.*.yaml"))
                for old in olds[:-HISTORY_KEEP]:
                    old.unlink(missing_ok=True)
            text = HEADER.get(name, "") + dump(data)
            storage.atomic_write_text(path, text)
            storage.append_jsonl(paths.log_dir / CHANGE_LOG, {"at_utc": now.isoformat(timespec="seconds"), "file": f"{PANEL_DIR}/{name}",
                                                              "what": what, **({"detail": detail} if detail else {})})
            return file_hash(text)
        finally:
            lock.release()

