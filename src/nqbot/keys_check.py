"""`nqbot keys-check` (owner 2026-10-28): after a key rotation, one cheap authenticated call per service, so the old
keys are revoked only once the new ones work on this machine. Every call is free: Skylit `/v1/account` (no credits),
Databento `metadata.list_datasets`, Anthropic `models.list`, Discord `GET` of the webhook and of the bot's own user
(and its channel). Nothing is pulled, posted or charged. No key, token or response body is ever printed (CLAUDE.md
rule 9), only "ok" with a harmless detail, or the HTTP status / error type. The TradersPost webhooks are only listed,
never called: any request to one is a trade signal."""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from typing import Any, Callable

from nqbot.secrets import REPO_ROOT, MissingSecret, Secret, get_secret, read_env_file

DISCORD_API = "https://discord.com/api/v10"
TIMEOUT_SEC = 15.0
REQUIRED = ("SKYLIT_API_KEY", "DATABENTO_API_KEY", "ANTHROPIC_API_KEY")  # a missing one fails the check


@dataclass
class Result:
    name: str
    ok: bool | None  # None = not set / not checked
    detail: str


def _http(transport: Any = None):
    import httpx

    return httpx.Client(timeout=TIMEOUT_SEC, transport=transport, verify=ssl.create_default_context())


def check_skylit(key: Secret, settings: dict[str, Any], transport: Any = None) -> str:
    from nqbot.clients.skylit import SkylitClient

    with SkylitClient(key, symbols_map=settings["skylit_symbols"], transport=transport) as c:
        data = c.account()
    return f"ok (balance {data.get('creditsBalance', '?')} credits)"


def check_databento(key: Secret) -> str:
    import databento

    datasets = databento.Historical(key.reveal()).metadata.list_datasets()
    return f"ok ({len(datasets)} datasets visible)"


def check_anthropic(key: Secret, settings: dict[str, Any]) -> str:
    from nqbot.gameplan.parser import make_client

    page = make_client(settings).models.list(limit=1)
    return f"ok ({len(page.data)} model listed)"


def _get(url: str, headers: dict[str, str] | None = None, transport: Any = None) -> int:
    with _http(transport) as c:
        return c.get(url, headers=headers or {}).status_code


def check_discord_webhook(url: Secret, transport: Any = None) -> str:
    code = _get(url.reveal(), transport=transport)  # GET returns the webhook's details; it posts nothing
    if code != 200:
        raise RuntimeError(f"HTTP {code}")
    return "ok (webhook exists)"


def check_discord_bot(token: Secret, channel_id: str | None, transport: Any = None) -> str:
    auth = {"Authorization": f"Bot {token.reveal()}"}
    code = _get(f"{DISCORD_API}/users/@me", auth, transport)
    if code != 200:
        raise RuntimeError(f"HTTP {code} on the bot's own user")
    if not channel_id:
        return "ok (token valid; DISCORD_CHANNEL_ID not set)"
    code = _get(f"{DISCORD_API}/channels/{channel_id}", auth, transport)
    if code != 200:
        raise RuntimeError(f"token valid, but HTTP {code} reading DISCORD_CHANNEL_ID")
    return "ok (token valid, channel readable)"


def _secret(name: str, env: Callable[[str], Secret]) -> Secret | None:
    try:
        return env(name)
    except MissingSecret:
        return None


def run_checks(settings: dict[str, Any], *, env: Callable[[str], Secret] = get_secret,
               env_names: Callable[[], list[str]] = lambda: list(read_env_file(REPO_ROOT / ".env")),
               checks: dict[str, Callable[..., str]] | None = None) -> list[Result]:
    c = {"skylit": lambda k: check_skylit(k, settings), "databento": check_databento,
         "anthropic": lambda k: check_anthropic(k, settings), "discord_webhook": check_discord_webhook,
         "discord_bot": check_discord_bot, "rclone": lambda: _rclone(settings), **(checks or {})}
    plan = [("SKYLIT_API_KEY", "skylit"), ("DATABENTO_API_KEY", "databento"), ("ANTHROPIC_API_KEY", "anthropic"),
            ("DISCORD_WEBHOOK_URL", "discord_webhook")]
    out: list[Result] = []
    for name, kind in plan:
        key = _secret(name, env)
        if key is None:
            out.append(Result(name, False if name in REQUIRED else None, "not set in .env"))
            continue
        try:
            out.append(Result(name, True, c[kind](key)))
        except Exception as e:  # the message may quote the request, never the key: only its type and status
            out.append(Result(name, False, f"FAILED: {type(e).__name__}{_status(e)}"))
    token = _secret("DISCORD_BOT_TOKEN", env)
    if token is None:
        out.append(Result("DISCORD_BOT_TOKEN", None, "not set in .env"))
    else:
        channel = _secret("DISCORD_CHANNEL_ID", env)
        try:
            out.append(Result("DISCORD_BOT_TOKEN", True, c["discord_bot"](token, channel.reveal() if channel else None)))
        except Exception as e:
            out.append(Result("DISCORD_BOT_TOKEN", False, f"FAILED: {type(e).__name__}{_status(e)}"))
    found, line = c["rclone"]()  # owner 2026-10-30: the rclone status-push and backup use (not a key: never fails)
    out.append(Result("rclone", True if found else None, line))
    tp = sorted(n for n in env_names() if n.startswith("TP_WEBHOOK_"))
    out.append(Result("TP_WEBHOOK_*", None, f"{len(tp)} set, not called (a request would be a trade signal)"
                      + (f": {', '.join(tp)}" if tp else "")))
    return out


def _rclone(settings: dict[str, Any]) -> tuple[bool, str]:
    from nqbot.rclone_path import rclone_line

    return rclone_line(settings)


def _status(e: Exception) -> str:
    """Only a status code or a short code from the error, never its text (which could echo a URL with a token)."""
    for attr in ("status", "status_code", "http_status"):
        v = getattr(e, attr, None)
        if isinstance(v, int):
            return f" (HTTP {v})"
    resp = getattr(e, "response", None)
    code = getattr(resp, "status_code", None)
    if isinstance(code, int):
        return f" (HTTP {code})"
    if isinstance(e, RuntimeError) and str(e).startswith(("HTTP", "token valid")):
        return f" ({e})"
    return ""
