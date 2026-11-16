"""The panel's JSON API for Home, Accounts and Settings (SPEC §9b items 0-2; build stage 2).

Every state-changing route checks the per-launch token (`require_token`) behind the Host / Origin middleware, and every
write goes through `PanelStore` (lock, hash check, history, change log, no secrets).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from nqbot import risk
from nqbot.accounts import derive
from nqbot.accounts.presets import preset_fields
from nqbot.accounts.schema import Account, SecretRefused
from nqbot.ui.panel_store import Conflict, PanelStore, PanelUnavailable

ET = ZoneInfo("America/New_York")
# fields the Settings page edits (§9b item 2); everything else goes through the Accounts page
SETTINGS_FIELDS = frozenset({"risk_profile", "custom_risk_usd", "min_ev_r", "min_target_prob", "auto_protect_within_usd",
                             "lives_mode", "lambda_risk_penalty"})
EFFECTS = ("auto", "full", "calm")


def _err(status: int, msg: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": msg, **extra}, status_code=status)


def windows(settings: dict[str, Any], features: dict[str, Any]) -> dict[str, str]:
    """The owner's fixed session rules (SPEC §1.2, §9): golden zone from 09:30 + ib_fib_start_minute, all setups from
    09:30 + ib_lock_minute, last entry flat_by − min_minutes_before_flat, flat by flat_by."""
    open_ = datetime(2000, 1, 1, 9, 30)
    flat_by = datetime.strptime(settings["session"]["flat_by"], "%H:%M").replace(year=2000)
    return {
        "rth_open": "09:30",
        "golden_zone_from": (open_ + timedelta(minutes=int(features["ib_fib_start_minute"]))).strftime("%H:%M"),
        "all_setups_from": (open_ + timedelta(minutes=int(features["ib_lock_minute"]))).strftime("%H:%M"),
        "last_entry": (flat_by - timedelta(minutes=int(settings["min_minutes_before_flat"]))).strftime("%H:%M"),
        "flat_by": settings["session"]["flat_by"],
    }


def routes(store: PanelStore, settings: dict[str, Any], features: dict[str, Any],
           require_token: Callable[[str | None], Response | None]) -> list[Route]:

    async def guarded(request: Request) -> tuple[dict[str, Any] | None, Response | None]:
        refused = require_token(request.headers.get("x-cf-token"))
        if refused:
            return None, refused
        try:
            body = await request.json()
        except ValueError:
            return None, _err(400, "the request body isn't JSON")
        return (body if isinstance(body, dict) else {}), None

    def accounts_payload() -> dict[str, Any]:
        loaded = store.accounts()
        accts = loaded.data["accounts"]
        rows = []
        for raw in accts:
            others = [o for o in accts if o is not raw]
            rows.append({"account": raw, **derive.report(raw, others, settings)})
        return {"hash": loaded.hash, "presets_hash": store.saved_presets().hash, "accounts": rows,
                "presets": [p.to_dict() for p in store.all_presets()],
                "folder": str(store.folder()), "windows": windows(settings, features)}

    def handle(fn):
        async def run(request: Request) -> Response:
            try:
                return await fn(request)
            except PanelUnavailable as e:
                return _err(503, str(e))
            except Conflict as e:
                return _err(409, str(e), conflict=True)
            except SecretRefused as e:
                return _err(422, str(e))
        return run

    @handle
    async def get_accounts(request: Request) -> Response:
        return JSONResponse(accounts_payload(), headers={"cache-control": "no-store"})

    @handle
    async def check_account(request: Request) -> Response:
        body, refused = await guarded(request)
        if refused:
            return refused
        accts = store.accounts().data["accounts"]
        return JSONResponse(derive.report(body.get("account") or {}, accts, settings, original_name=body.get("original_name")))

    @handle
    async def save_account(request: Request) -> Response:
        body, refused = await guarded(request)
        if refused:
            return refused
        raw = dict(body.get("account") or {})
        original = body.get("original_name")
        loaded = store.accounts()
        accts = list(loaded.data["accounts"])
        acc, issues = derive.validate(raw, accts, settings, original_name=original)
        errors = [i for i in issues if i.level == "error"]
        if acc is None or errors:
            return _err(422, "fix the marked fields first", issues=[i.__dict__ for i in issues])
        old = next((o for o in accts if o.get("name") == original), None) if original else None
        stored = acc.model_dump(mode="json")
        what = f"saved account {acc.name}" if old else f"added account {acc.name}"
        detail: dict[str, Any] = {}
        if old is not None:
            old_n = int(old.get("max_risk_fraction_divisor", risk.DEFAULT_DIVISOR))
            new_n = acc.max_risk_fraction_divisor
            if risk.needs_typed_confirmation(old_n, new_n):
                # raising risk (§9b): typed "<name> 1/N", then it waits for the account's sweep; until then the
                # account keeps trading at its current fraction
                effect = derive.risk_raise_effect(Account.model_validate({**old, "name": acc.name}), new_n, settings)
                if (body.get("confirm") or "").strip() != effect["confirm_text"]:
                    return _err(428, "raising risk needs the typed confirmation", needs_confirmation=True, effect=effect)
                stored["pending_risk"] = {"max_risk_fraction_divisor": new_n, "risk_usd_per_trade": acc.risk_usd_per_trade,
                                          "confirmed_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                stored["max_risk_fraction_divisor"] = old_n
                stored["risk_usd_per_trade"] = min(float(old.get("risk_usd_per_trade") or acc.risk_usd_per_trade),
                                                   risk.risk_cap_usd(acc.drawdown_usd, old_n))
                what += f": risk 1/{old_n} → 1/{new_n} confirmed, waiting for the account's sweep"
            elif risk.risk_change(old_n, new_n) == risk.RISK_DOWN:
                stored["pending_risk"] = None  # lowering risk applies from the next trade and cancels a pending raise
                what += f": risk 1/{old_n} → 1/{new_n} (lower, from the next trade)"
            changed = sorted(k for k in set(stored) | set(old) if stored.get(k) != old.get(k))
            detail = {"fields": changed}
        new_list = [stored if o.get("name") == original else o for o in accts] if old is not None else accts + [stored]
        # the hash the page loaded, not the file's current one: a page showing an older file is refused (409)
        h = store.save("accounts.yaml", {"accounts": new_list}, body.get("hash", ""), what, detail)
        return JSONResponse({"ok": True, "hash": h, "pending_risk": stored.get("pending_risk")})

    @handle
    async def delete_account(request: Request) -> Response:
        body, refused = await guarded(request)
        if refused:
            return refused
        name = body.get("name")
        if (body.get("typed") or "").strip() != name:
            return _err(422, "type the account's name to delete it")
        loaded = store.accounts()
        accts = loaded.data["accounts"]
        if not any(o.get("name") == name for o in accts):
            return _err(404, f"no account {name!r}")
        h = store.save("accounts.yaml", {"accounts": [o for o in accts if o.get("name") != name]}, body.get("hash", ""),
                       f"deleted account {name}")
        return JSONResponse({"ok": True, "hash": h})

    @handle
    async def save_preset(request: Request) -> Response:
        body, refused = await guarded(request)
        if refused:
            return refused
        label = (body.get("label") or "").strip()
        firm = (body.get("firm") or "").strip()
        stage = (body.get("stage") or "").strip()
        if not label or not firm or stage not in ("evaluation", "funded", "after first payout"):
            return _err(422, "a name, the firm and the stage (evaluation, funded or after first payout) are needed")
        fields = preset_fields(dict(body.get("fields") or {}))
        _, issues = derive.validate({**fields, "name": "presetcheck"}, [], settings)
        errors = [i for i in issues if i.level == "error"]
        if errors:
            return _err(422, "the account's fields don't pass their checks yet", issues=[i.__dict__ for i in issues])
        slug = re.sub(r"[^a-z0-9]+", "_", f"own {label} {stage}".lower()).strip("_")
        loaded = store.saved_presets()
        own = loaded.data["presets"]
        if any(p.label == label and p.stage == stage for p in store.all_presets()):
            return _err(422, f"a preset named {label!r} ({stage}) already exists")
        own = own + [{"id": slug, "label": label, "firm": firm, "stage": stage, "fields": fields}]
        h = store.save("presets.yaml", {"presets": own}, body.get("presets_hash", loaded.hash), f"saved preset {label} ({stage})")
        return JSONResponse({"ok": True, "hash": h, "id": slug})

    @handle
    async def delete_preset(request: Request) -> Response:
        body, refused = await guarded(request)
        if refused:
            return refused
        loaded = store.saved_presets()
        own = loaded.data["presets"]
        pid = body.get("id")
        hit = next((p for p in own if p.get("id") == pid), None)
        if hit is None:
            return _err(404, "only your saved presets can be deleted")
        if (body.get("typed") or "").strip() != hit["label"]:
            return _err(422, "type the preset's name to delete it")
        h = store.save("presets.yaml", {"presets": [p for p in own if p.get("id") != pid]}, loaded.hash, f"deleted preset {hit['label']}")
        return JSONResponse({"ok": True, "hash": h})

    @handle
    async def get_settings(request: Request) -> Response:
        loaded = store.accounts()
        rows = []
        for raw in loaded.data["accounts"]:
            rep = derive.report(raw, [], settings)
            rows.append({"account": raw, "derived": rep["derived"]})
        ui = store.ui()
        return JSONResponse({
            "hash": loaded.hash, "accounts": rows,
            # the profiles' backtested stats come from the step-15 sweep (models/profiles.yaml); none yet
            "profile_stats": None,
            "globals": {
                "gate_pass_rate_min": (settings.get("gates") or {}).get("gate_pass_rate_min"),
                "blackout_before_min": settings.get("blackout_before_min"), "blackout_after_min": settings.get("blackout_after_min"),
                "windows": windows(settings, features),
                "profile_risk_multiplier": settings.get("profile_risk_multiplier"),
            },
            "ui": {"hash": ui.hash, **(ui.data or {})},
        }, headers={"cache-control": "no-store"})

    @handle
    async def save_settings(request: Request) -> Response:
        body, refused = await guarded(request)
        if refused:
            return refused
        name = body.get("name")
        changes = dict(body.get("changes") or {})
        bad = sorted(set(changes) - SETTINGS_FIELDS)
        if bad:
            return _err(422, f"not editable here: {', '.join(bad)} (use the Accounts page)")
        loaded = store.accounts()
        accts = loaded.data["accounts"]
        old = next((o for o in accts if o.get("name") == name), None)
        if old is None:
            return _err(404, f"no account {name!r}")
        new = {**old, **changes}
        acc, issues = derive.validate(new, accts, settings, original_name=name)
        errors = [i for i in issues if i.level == "error"]
        if acc is None or errors:
            return _err(422, "fix the marked fields first", issues=[i.__dict__ for i in issues])
        h = store.save("accounts.yaml", {"accounts": [acc.model_dump(mode="json") if o is old else o for o in accts]},
                       body.get("hash", ""), f"settings of {name}: {', '.join(sorted(changes))}", {"changes": changes})
        return JSONResponse({"ok": True, "hash": h})

    @handle
    async def save_ui(request: Request) -> Response:
        body, refused = await guarded(request)
        if refused:
            return refused
        effects = body.get("effects")
        if effects not in EFFECTS:
            return _err(422, f"effects must be one of {', '.join(EFFECTS)}")
        ui = store.ui()
        h = store.save("ui.yaml", {**(ui.data or {}), "effects": effects}, ui.hash, f"effects: {effects}")
        return JSONResponse({"ok": True, "hash": h})

    @handle
    async def home(request: Request) -> Response:
        now = datetime.now(ET)
        w = windows(settings, features)
        try:
            accts = [a for a in store.accounts().data["accounts"]]
        except PanelUnavailable:
            accts = []
        checked = [a for a in accts if not a.get("draft")]
        risks = sorted({float(a.get("risk_usd_per_trade") or 0) for a in (checked or accts)})
        return JSONResponse({
            "now_et": now.isoformat(timespec="seconds"),
            "windows": w,
            "accounts": len(accts), "drafts": len(accts) - len(checked),
            "risk_per_trade_usd": risks,
            # live numbers that need the engine (balances from step 22), the review loop (stage 4) or ledgers (step 15)
            "closest_drawdown_margin_usd": None, "review_cards_left": None, "month_pnl_usd": None,
        }, headers={"cache-control": "no-store"})

    return [
        Route("/api/accounts", get_accounts, methods=["GET"]),
        Route("/api/accounts/check", check_account, methods=["POST"]),
        Route("/api/accounts/save", save_account, methods=["POST"]),
        Route("/api/accounts/delete", delete_account, methods=["POST"]),
        Route("/api/presets/save", save_preset, methods=["POST"]),
        Route("/api/presets/delete", delete_preset, methods=["POST"]),
        Route("/api/settings", get_settings, methods=["GET"]),
        Route("/api/settings/account", save_settings, methods=["POST"]),
        Route("/api/ui", save_ui, methods=["POST"]),
        Route("/api/home", home, methods=["GET"]),
    ]
