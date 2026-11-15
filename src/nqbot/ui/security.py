"""Request checks and security headers for the panel (SPEC §9b).

- Every request must carry a Host header of `127.0.0.1:<ui_port>` or `localhost:<ui_port>` (against DNS rebinding).
- Every state-changing request (anything but GET / HEAD) must carry a matching Origin and the per-launch token the
  server put into its own pages (header `X-CF-Token`, or a `token` form field on the no-JavaScript pages), so another
  website open in the same browser can't send kill, flat or edit requests.
- Every response carries `Content-Security-Policy` with `default-src 'self'`: no remote code, fonts, images or
  scripts. The SPA's one inline script (the load watchdog) is allowed by its sha256 hash. Two libraries insert a
  `<style>` element at run time (Radix's scroll lock under a dialog, Motion's popLayout); they carry this launch's
  style nonce (`style-src 'self' 'nonce-…'`), so no `unsafe-inline` is ever needed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from starlette.datastructures import Headers

TOKEN_HEADER = "x-cf-token"
TOKEN_FIELD = "token"
SAFE_METHODS = frozenset({"GET", "HEAD"})

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def new_token() -> str:
    """A fresh per-launch token (32 bytes, URL-safe)."""
    return secrets.token_urlsafe(32)


def allowed_hosts(port: int) -> frozenset[str]:
    return frozenset({f"127.0.0.1:{port}", f"localhost:{port}"})


def allowed_origins(port: int) -> frozenset[str]:
    return frozenset({f"http://{h}" for h in allowed_hosts(port)})


def token_ok(given: str | None, token: str) -> bool:
    return bool(given) and hmac.compare_digest(str(given), token)


def script_hash(text: str) -> str:
    """The CSP source for an inline script with exactly this text."""
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii") + "'"


def csp(script_hashes: list[str] | tuple[str, ...] = (), style_nonce: str | None = None) -> str:
    scripts = " ".join(["'self'", *script_hashes])
    styles = "'self'" + (f" 'nonce-{style_nonce}'" if style_nonce else "")
    return (
        "default-src 'self'; "
        f"script-src {scripts}; "
        f"style-src {styles}; img-src 'self'; font-src 'self'; connect-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    )


def security_headers(script_hashes: list[str] | tuple[str, ...] = (), style_nonce: str | None = None) -> list[tuple[bytes, bytes]]:
    return [
        (b"content-security-policy", csp(script_hashes, style_nonce).encode()),
        (b"x-content-type-options", b"nosniff"),
        # same-origin, not no-referrer: with no-referrer Chrome sends `Origin: null` on a form post, which the Origin
        # check (rightly) refuses, and the /emergency forms would never work
        (b"referrer-policy", b"same-origin"),
        (b"x-frame-options", b"DENY"),
        (b"cross-origin-opener-policy", b"same-origin"),
        (b"cross-origin-resource-policy", b"same-origin"),
    ]


async def _plain(send: Send, status: int, text: str, extra: list[tuple[bytes, bytes]]) -> None:
    body = text.encode("utf-8")
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode()), *extra]})
    await send({"type": "http.response.body", "body": body})


class GuardMiddleware:
    """Host check on every request, Origin check on every non-GET request, security headers on every response.

    The token itself is checked by each state-changing endpoint (`require_token` in app.py), because a form post
    carries it in its body. `script_hashes` is read on every response, so the SPA's watchdog hash can follow a rebuild.
    """

    def __init__(self, app: ASGIApp, *, port: int, script_hashes: Callable[[], list[str]], style_nonce: str | None = None):
        self.app = app
        self.style_nonce = style_nonce
        self.hosts = allowed_hosts(port)
        self.origins = allowed_origins(port)
        self.script_hashes = script_hashes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        extra = security_headers(self.script_hashes(), self.style_nonce)
        headers = Headers(scope=scope)
        host = headers.get("host", "")
        if host not in self.hosts:
            await _plain(send, 421, f"Refused: Host {host!r} is not this panel (open http://127.0.0.1:<ui_port>/).", extra)
            return
        if scope.get("method", "GET") not in SAFE_METHODS:
            origin = headers.get("origin")
            if origin not in self.origins:
                await _plain(send, 403, "Refused: this request did not come from the panel's own page (Origin).", extra)
                return

        async def send_with_headers(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                # computed at response start: the page handler has just loaded the SPA (and its watchdog's hash)
                names = {k.lower() for k, _ in message.get("headers", [])}
                late = security_headers(self.script_hashes(), self.style_nonce)
                message["headers"] = [*message.get("headers", []), *[(k, v) for k, v in late if k not in names]]
            await send(message)

        await self.app(scope, receive, send_with_headers)
