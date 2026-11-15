"""The no-JavaScript emergency page (SPEC §9b kill/flat fallback, layer 1).

Rendered by the Python server from `templates/`, never from the SPA's `dist/`, so it works when the SPA build is
missing, stale, failed its audit or crashes in the browser: plain HTML form buttons KILL, FLATTEN ALL and
PAUSE / RESUME that post to the same action path as the SPA's controls, plus a read-only table of the engine status
and each account's state, refreshed by `<meta http-equiv="refresh">`. It renders without the SSD too. When the SPA
can't be served, `/` shows the same page with the reason at the top.
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Any
from zoneinfo import ZoneInfo

TEMPLATES = Path(__file__).resolve().parent / "templates"
# SPEC §9b: the emergency page refreshes itself; a few seconds keeps the state table current without hammering
REFRESH_SEC = 5
ET = ZoneInfo("America/New_York")
RESULT_WORDS = {"flat": "FLATTEN ALL", "kill": "KILL", "pause": "PAUSE", "resume": "RESUME"}


def css() -> str:
    return (TEMPLATES / "emergency.css").read_text(encoding="utf-8")


def _notice(kind: str, title: str, text: str) -> str:
    return f'<div class="notice {kind}" role="{"alert" if kind == "crit" else "status"}"><strong>{html.escape(title)}</strong>{html.escape(text)}</div>'


def _accounts_table(accounts: list[dict[str, Any]], ssd_found: bool) -> str:
    if not ssd_found:
        return "<p>Accounts can't be read: the SSD isn't found. KILL and FLATTEN ALL still act on every account.</p>"
    if not accounts:
        return "<p>No accounts saved yet.</p>"
    rows = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(a.get(k, '')))}</td>" for k in ("name", "firm", "phase")) +
        f"<td>{'draft (not trading)' if a.get('draft') else ('demo' if a.get('demo') else 'live')}</td>"
        f"<td>{html.escape(str(a.get('state', 'no engine: not trading')))}</td></tr>"
        for a in accounts
    )
    return ("<table><thead><tr><th scope='col'>Account</th><th scope='col'>Firm</th><th scope='col'>Phase</th>"
            f"<th scope='col'>Kind</th><th scope='col'>State</th></tr></thead><tbody>{rows}</tbody></table>")


def render(status: dict[str, Any], token: str, *, reason: str | None = None, result: dict[str, Any] | None = None,
           app_available: bool = True) -> str:
    """The emergency page. `reason` (the SPA can't be served) shows at the top; `result` is the last action's
    outcome (after the form's post-redirect-get)."""
    reason_html = _notice("crit", "The panel's app can't be shown.", reason) if reason else ""
    result_html = ""
    if result:
        word = RESULT_WORDS.get(str(result.get("action")), str(result.get("action")))
        title = f"{word} sent." if result.get("ok") else f"{word}: nothing was sent."
        result_html = _notice("info" if result.get("ok") else "warn", title, str(result.get("message", "")))
    for a in status.get("alerts", []):
        result_html += _notice(a.get("kind", "warn"), a.get("title", ""), a.get("text", ""))
    engine = status.get("engine", {})
    tpl = Template((TEMPLATES / "emergency.html").read_text(encoding="utf-8"))
    return tpl.substitute(
        refresh_sec=REFRESH_SEC,
        now_et=datetime.now(ET).strftime("%H:%M:%S"),
        token=html.escape(token, quote=True),
        reason=reason_html,
        result=result_html,
        engine_state=html.escape(str(engine.get("state", "unknown")).upper()),
        engine_note=html.escape(str(engine.get("note", ""))),
        ssd=html.escape(status.get("ssd", {}).get("message", "")),
        host=html.escape(str(status.get("host", ""))),
        accounts=_accounts_table(status.get("accounts", []), bool(status.get("ssd", {}).get("found"))),
        app_link='<a href="/">Back to the panel</a>' if app_available else "The panel's app isn't available; this page is the panel for now.",
    )
