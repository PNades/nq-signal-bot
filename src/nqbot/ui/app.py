"""The panel's Starlette app (SPEC §9b): the SPA and its static files, the JSON API, the status stream, the
no-JavaScript /emergency page and the one action path for KILL / FLAT ALL / PAUSE / RESUME.

Every route sits behind `security.GuardMiddleware` (Host, Origin, security headers); every state-changing route
checks the per-launch token itself (`require_token`).
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import secrets
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import Route

from nqbot.storage import Paths
from nqbot.ui import actions, emergency, spa
from nqbot.ui.security import TOKEN_FIELD, TOKEN_HEADER, GuardMiddleware, new_token, token_ok
from nqbot.ui.status import build_status

# design-system/pages/shell.md: the status stream's frame period; the SPA calls the server lost after 8 s of silence
EVENTS_PERIOD_SEC = 2.0
PAGE_PATHS = ("/", "/home", "/live", "/gameplan", "/accounts", "/settings", "/review", "/performance")
MAX_FORM_BYTES = 4096
RESULTS_KEPT = 32


@dataclass
class PanelConfig:
    port: int
    paths_fn: Callable[[], Paths | None]
    synthetic: bool = False
    token: str = field(default_factory=new_token)
    style_nonce: str = field(default_factory=lambda: new_token()[:24])
    web_dir: Path = spa.WEB_DIR
    accounts_fn: Callable[[Paths | None], list[dict[str, Any]]] | None = None


def create_app(cfg: PanelConfig) -> Starlette:
    results: OrderedDict[str, dict[str, Any]] = OrderedDict()
    spa_cache: dict[str, Any] = {"mtime": None, "html": "", "hashes": []}

    def status() -> dict[str, Any]:
        return build_status(cfg.paths_fn, synthetic=cfg.synthetic, accounts_fn=cfg.accounts_fn)

    def spa_page() -> tuple[spa.SpaState, str]:
        state = spa.spa_state(cfg.web_dir)
        if not state.ok:
            return state, ""
        index = state.dist / "index.html"
        mtime = index.stat().st_mtime_ns
        if spa_cache["mtime"] != mtime:
            text = index.read_text(encoding="utf-8")
            spa_cache.update(mtime=mtime, html=text, hashes=spa.watchdog_hashes(text))
        return state, spa.page_html(spa_cache["html"], cfg.token, cfg.style_nonce)

    def require_token(given: str | None) -> Response | None:
        if not token_ok(given, cfg.token):
            return PlainTextResponse("Refused: the request lacks this launch's token (reload the panel's page).", status_code=403)
        return None

    async def page(request: Request) -> Response:
        state, text = spa_page()
        if not state.ok:
            # the SPA can't be served: say why, with the kill/flat/pause controls inline (SPEC §9b)
            return HTMLResponse(emergency.render(status(), cfg.token, reason=state.reason, app_available=False),
                                headers={"cache-control": "no-store"})
        return HTMLResponse(text, headers={"cache-control": "no-store"})

    async def static(request: Request) -> Response:
        rel = request.url.path.lstrip("/")
        dist = (cfg.web_dir / "dist").resolve()
        target = (dist / rel).resolve()
        if dist not in target.parents or not target.is_file() or target.name.startswith("."):
            return PlainTextResponse("Not found", status_code=404)
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".woff2":
            ctype = "font/woff2"
        cache = "public, max-age=31536000, immutable" if rel.startswith("assets/") else "no-cache"
        return Response(target.read_bytes(), media_type=ctype, headers={"cache-control": cache})

    async def api_status(request: Request) -> Response:
        return JSONResponse(status(), headers={"cache-control": "no-store"})

    async def api_events(request: Request) -> Response:
        async def stream():
            while True:
                if await request.is_disconnected():
                    return
                yield f"event: status\ndata: {json.dumps(status())}\n\n"
                await asyncio.sleep(EVENTS_PERIOD_SEC)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"cache-control": "no-store", "x-accel-buffering": "no"})

    async def api_action(request: Request) -> Response:
        refused = require_token(request.headers.get(TOKEN_HEADER))
        if refused:
            return refused
        action = request.path_params["action"]
        if action not in actions.ACTIONS:
            return JSONResponse({"error": f"unknown action {action!r}"}, status_code=404)
        return JSONResponse(actions.perform(action, "panel", cfg.paths_fn).to_dict())

    async def emergency_page(request: Request) -> Response:
        result = results.get(request.query_params.get("r", ""))
        state = spa.spa_state(cfg.web_dir)
        return HTMLResponse(emergency.render(status(), cfg.token, result=result, app_available=state.ok),
                            headers={"cache-control": "no-store"})

    async def emergency_css(request: Request) -> Response:
        return Response(emergency.css(), media_type="text/css", headers={"cache-control": "no-cache"})

    async def emergency_action(request: Request) -> Response:
        body = await request.body()
        if len(body) > MAX_FORM_BYTES:
            return PlainTextResponse("Refused: form too large.", status_code=413)
        form = parse_qs(body.decode("utf-8", errors="replace"))
        refused = require_token((form.get(TOKEN_FIELD) or [None])[0])
        if refused:
            return refused
        action = (form.get("action") or [""])[0]
        if action not in actions.ACTIONS:
            return PlainTextResponse(f"Refused: unknown action {action!r}.", status_code=400)
        result = actions.perform(action, "emergency page", cfg.paths_fn).to_dict()
        rid = secrets.token_urlsafe(8)
        results[rid] = result
        while len(results) > RESULTS_KEPT:
            results.popitem(last=False)
        return RedirectResponse(f"/emergency?r={rid}", status_code=303)

    async def api_missing(request: Request) -> Response:
        return JSONResponse({"error": f"no API route {request.url.path}"}, status_code=404)

    routes = [
        *[Route(p, page, methods=["GET", "HEAD"]) for p in PAGE_PATHS],
        Route("/api/status", api_status, methods=["GET"]),
        Route("/api/events", api_events, methods=["GET"]),
        Route("/api/actions/{action}", api_action, methods=["POST"]),
        Route("/emergency", emergency_page, methods=["GET", "HEAD"]),
        Route("/emergency.css", emergency_css, methods=["GET"]),
        Route("/emergency/action", emergency_action, methods=["POST"]),
        Route("/api/{rest:path}", api_missing, methods=["GET", "POST"]),
        Route("/{rest:path}", static, methods=["GET", "HEAD"]),
    ]
    app = Starlette(routes=routes)
    app.state.cfg = cfg
    return GuardMiddleware(app, port=cfg.port, script_hashes=lambda: spa_cache["hashes"], style_nonce=cfg.style_nonce)  # type: ignore[return-value]
