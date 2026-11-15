"""The panel server's safety rules (SPEC §9b): Host and Origin checks, the per-launch token, the CSP on every
response, the no-JavaScript /emergency page (works with no SPA build and no SSD), the stale-build fallback, the
loopback-only bind, and the one action path."""

from __future__ import annotations

import json
import socket

import pytest
from starlette.testclient import TestClient

from nqbot.storage import read_jsonl
from nqbot.ui import actions, spa
from nqbot.ui.app import PanelConfig, create_app
from nqbot.ui.security import script_hash
from nqbot.ui.serve import BindRefused, bind, check_bound

PORT = 8733
BASE = f"http://127.0.0.1:{PORT}"
ORIGIN = {"origin": BASE}
WATCHDOG = "setTimeout(function(){},6000);"
INDEX = (f'<!doctype html><html><head><meta name="cf-token" content="{spa.TOKEN_PLACEHOLDER}">'
         f'<meta name="cf-nonce" content="{spa.NONCE_PLACEHOLDER}"></head>'
         f'<body><div id="root"></div><script id="cf-watchdog">{WATCHDOG}</script>'
         '<script type="module" src="/assets/app.js"></script></body></html>')


def make_web(tmp_path, *, built=True, stale=False, failed=False):
    web = tmp_path / "web"
    (web / "src").mkdir(parents=True)
    (web / "src" / "main.tsx").write_text("export {};\n", encoding="utf-8")
    (web / "package.json").write_text("{}", encoding="utf-8")
    if built:
        dist = web / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text(INDEX, encoding="utf-8")
        (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
        info = {"ok": not failed, "digest": spa.source_digest(web), "error": "npm audit found 1 high advisory"}
        (dist / spa.BUILD_INFO).write_text(json.dumps(info), encoding="utf-8")
        if stale:
            (web / "src" / "main.tsx").write_text("export const changed = 1;\n", encoding="utf-8")
    return web


def client(tmp_path, *, paths=None, token="tok-123", **web_kw):
    cfg = PanelConfig(port=PORT, paths_fn=lambda: paths, token=token, style_nonce="n0nce", web_dir=make_web(tmp_path, **web_kw))
    return TestClient(create_app(cfg), base_url=BASE)


def test_host_header_must_be_this_panel(tmp_path):
    c = client(tmp_path)
    assert c.get("/api/status").status_code == 200
    assert c.get("/api/status", headers={"host": f"localhost:{PORT}"}).status_code == 200
    for bad in ("evil.example:8733", f"127.0.0.1:{PORT + 1}", "127.0.0.1", f"192.168.1.5:{PORT}"):
        r = c.get("/api/status", headers={"host": bad})
        assert r.status_code == 421, bad
        assert "default-src 'self'" in r.headers["content-security-policy"]


def test_post_needs_origin_and_token(tmp_path):
    c = client(tmp_path)
    assert c.post("/api/actions/flat").status_code == 403  # no Origin
    assert c.post("/api/actions/flat", headers={"origin": "http://evil.example"}).status_code == 403
    assert c.post("/api/actions/flat", headers=ORIGIN).status_code == 403  # no token
    assert c.post("/api/actions/flat", headers={**ORIGIN, "x-cf-token": "wrong"}).status_code == 403
    r = c.post("/api/actions/flat", headers={**ORIGIN, "x-cf-token": "tok-123"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["engine"] == "none" and "step 22" in body["message"]


def test_every_action_goes_through_the_one_path(tmp_path, paths):
    c = client(tmp_path, paths=paths)
    for a in actions.ACTIONS:
        r = c.post(f"/api/actions/{a}", headers={**ORIGIN, "x-cf-token": "tok-123"})
        assert r.status_code == 200 and r.json()["action"] == a
    assert c.post("/api/actions/reverse", headers={**ORIGIN, "x-cf-token": "tok-123"}).status_code == 404
    rows = read_jsonl(paths.log_dir / actions.ACTION_LOG)
    assert [r["action"] for r in rows] == list(actions.ACTIONS)
    assert {r["source"] for r in rows} == {"panel"}


def test_csp_on_every_response(tmp_path):
    c = client(tmp_path)
    for path in ("/", "/accounts", "/api/status", "/emergency", "/emergency.css", "/assets/app.js", "/nope.txt", "/api/nope"):
        r = c.get(path)
        assert r.headers["content-security-policy"].startswith("default-src 'self'"), path
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
    r = c.post("/api/actions/flat")
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_spa_page_carries_the_token_and_the_watchdog_hash(tmp_path):
    c = client(tmp_path)
    r = c.get("/")
    assert r.status_code == 200
    assert 'content="tok-123"' in r.text and spa.TOKEN_PLACEHOLDER not in r.text
    assert script_hash(WATCHDOG) in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"
    assert c.get("/performance").text == r.text  # every page path serves the app


def test_inline_styles_only_with_this_launchs_nonce(tmp_path):
    # Radix's scroll lock and Motion's popLayout insert a <style> element: allowed by the nonce, never 'unsafe-inline'
    c = client(tmp_path)
    r = c.get("/")
    assert 'name="cf-nonce" content="n0nce"' in r.text and spa.NONCE_PLACEHOLDER not in r.text
    policy = r.headers["content-security-policy"]
    assert "style-src 'self' 'nonce-n0nce'" in policy and "unsafe-inline" not in policy and "unsafe-eval" not in policy
    assert r.headers["referrer-policy"] == "same-origin"  # no-referrer makes Chrome send Origin: null on form posts


def test_emergency_page_works_without_the_app_and_without_the_ssd(tmp_path):
    c = client(tmp_path, built=False, paths=None)
    r = c.get("/emergency")
    assert r.status_code == 200
    t = r.text
    assert "<script" not in t.lower()
    assert '<meta http-equiv="refresh"' in t
    for word in ("KILL", "FLATTEN ALL", "PAUSE", "RESUME"):
        assert f">{word}</button>" in t
    assert t.count('name="token" value="tok-123"') == 4
    assert "SSD not found" in t and "the SSD isn't found" in t
    assert 'style="' not in t  # the strict CSP blocks inline styles: everything is in /emergency.css
    # what each button does, as SPEC §9c defines !kill / !flat / !pause (KILL does not stop the engine)
    assert "blocks new entries until the next session" in t and "engine keeps running" in t
    assert "pauses new entries until Resume" in t and "removes pending unfilled entries" in t


def test_emergency_form_posts_to_the_same_action_path(tmp_path, paths):
    c = client(tmp_path, paths=paths)
    form = {"content-type": "application/x-www-form-urlencoded", **ORIGIN}
    assert c.post("/emergency/action", content="action=flat", headers=form).status_code == 403  # no token
    assert c.post("/emergency/action", content="action=flat&token=tok-123", headers={"content-type": form["content-type"]}).status_code == 403  # no Origin
    r = c.post("/emergency/action", content="action=flat&token=tok-123", headers=form, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/emergency?r=")
    page = c.get(r.headers["location"]).text
    assert "FLATTEN ALL: nothing was sent." in page and "step 22" in page
    assert read_jsonl(paths.log_dir / actions.ACTION_LOG)[-1]["source"] == "emergency page"
    assert c.post("/emergency/action", content="action=reverse&token=tok-123", headers=form).status_code == 400


@pytest.mark.parametrize("kw, reason", [
    ({"built": False}, "built on this machine"),
    ({"stale": True}, "stale"),
    ({"failed": True}, "last app build failed"),
])
def test_a_missing_stale_or_failed_build_serves_the_controls_with_the_reason(tmp_path, kw, reason):
    c = client(tmp_path, **kw)
    r = c.get("/")
    assert r.status_code == 200
    assert reason in r.text
    assert ">FLATTEN ALL</button>" in r.text and ">KILL</button>" in r.text
    assert "<script" not in r.text.lower()


def test_static_files_stay_inside_dist(tmp_path):
    c = client(tmp_path)
    assert c.get("/assets/app.js").status_code == 200
    assert c.get(f"/{spa.BUILD_INFO}").status_code == 404
    assert c.get("/..%2Fpackage.json").status_code == 404
    assert c.get("/assets/../../package.json").status_code == 404


def test_status_says_what_exists(tmp_path, paths):
    s = client(tmp_path, paths=None).get("/api/status").json()
    assert s["ssd"]["found"] is False and s["alerts"][0]["id"] == "ssd-missing"
    assert s["engine"]["state"] == "offline" and s["calm_forced"] is False
    s = client(tmp_path / "b", paths=paths).get("/api/status").json()
    assert s["ssd"]["found"] is True and s["alerts"] == []


def test_bind_is_loopback_only():
    class Fake:
        def __init__(self, addr):
            self.addr = addr

        def getsockname(self):
            return self.addr

    check_bound(Fake(("127.0.0.1", PORT)), PORT)
    for addr in (("0.0.0.0", PORT), ("192.168.1.5", PORT), ("127.0.0.1", PORT + 1)):
        with pytest.raises(BindRefused):
            check_bound(Fake(addr), PORT)
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    free = probe.getsockname()[1]
    probe.close()
    s = bind(free)
    try:
        assert s.getsockname() == ("127.0.0.1", free)
    finally:
        s.close()
