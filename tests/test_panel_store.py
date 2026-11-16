"""The panel's files on the SSD (SPEC §9b, owner 2026-11-15): drafts seeded once, lock + hash check, history, change
log, no secrets, refusal without the SSD; and the API that writes through it (raising risk, presets, settings)."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from nqbot.config import load_features, load_settings
from nqbot.storage import read_jsonl
from nqbot.ui.app import PanelConfig, create_app
from nqbot.ui.panel_store import CHANGE_LOG, Conflict, PanelStore, PanelUnavailable, file_hash

BASE = "http://127.0.0.1:8733"
H = {"origin": BASE, "x-cf-token": "tok"}


def test_drafts_are_seeded_once(paths):
    store = PanelStore(lambda: paths)
    loaded = store.accounts()
    names = [(a["name"], a["preset"], a["draft"], a["traderspost_webhook_env"]) for a in loaded.data["accounts"]]
    assert names == [("mffur1", "mffu_rapid_eod_25k_evaluation", True, "TP_WEBHOOK_MFFUR1"),
                     ("mffub1", "mffu_builder_25k_evaluation", True, "TP_WEBHOOK_MFFUB1")]
    assert loaded.data["accounts"][0]["consistency_pct"] == 0.30 and loaded.data["accounts"][1]["firm_daily_loss_limit_usd"] == 600
    log = read_jsonl(paths.log_dir / CHANGE_LOG)
    assert len(log) == 1 and "seeded" in log[0]["what"]
    store.accounts()
    assert len(read_jsonl(paths.log_dir / CHANGE_LOG)) == 1           # never seeded twice


def test_writes_check_the_hash_keep_history_and_log(paths):
    store = PanelStore(lambda: paths)
    loaded = store.accounts()
    data = loaded.data
    data["accounts"][0]["draft"] = False
    h2 = store.save("accounts.yaml", data, loaded.hash, "checked mffur1")
    with pytest.raises(Conflict):
        store.save("accounts.yaml", data, loaded.hash, "stale page")      # the page loaded the older file
    assert store.accounts().hash == h2
    hist = list((paths.data_dir / "panel" / "history").glob("accounts.*.yaml"))
    assert len(hist) == 1
    assert [r["what"] for r in read_jsonl(paths.log_dir / CHANGE_LOG)][-1] == "checked mffur1"
    assert not list((paths.data_dir / "panel").glob(".panel.lock"))      # the lock is released


def test_secrets_never_reach_the_file(paths):
    store = PanelStore(lambda: paths)
    loaded = store.accounts()
    loaded.data["accounts"][0]["firm"] = "https://webhooks.traderspost.io/x/y"
    with pytest.raises(ValueError):
        store.save("accounts.yaml", loaded.data, loaded.hash, "leak")
    assert "traderspost.io" not in (paths.data_dir / "panel" / "accounts.yaml").read_text(encoding="utf-8")


def test_no_ssd_no_panel_file():
    store = PanelStore(lambda: None)
    with pytest.raises(PanelUnavailable):
        store.accounts()
    assert file_hash(None) == file_hash("")


def test_edits_refused_while_the_engine_is_down_mid_session(paths):
    store = PanelStore(lambda: paths, edits_allowed=lambda: "the engine is down mid-session")
    loaded = store.accounts()   # seeding is the panel's own first write, not an edit
    with pytest.raises(PanelUnavailable, match="mid-session"):
        store.save("accounts.yaml", loaded.data, loaded.hash, "edit")


# ── API ──────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def api(paths, tmp_path):
    cfg = PanelConfig(port=8733, paths_fn=lambda: paths, token="tok", web_dir=tmp_path / "web",
                      settings=load_settings(), features=load_features())
    return TestClient(create_app(cfg), base_url=BASE)


def test_accounts_api_lists_drafts_with_derived_values(api):
    r = api.get("/api/accounts").json()
    m = r["accounts"][0]
    assert m["account"]["name"] == "mffur1" and m["derived"]["daily_profit_limit_usd"] == pytest.approx(450)
    assert {p["id"] for p in r["presets"]} >= {"mffu_pro_50k_funded", "lucid_flex_25k_funded"}
    assert r["windows"] == {"rth_open": "09:30", "golden_zone_from": "09:45", "all_setups_from": "10:00",
                            "last_entry": "15:45", "flat_by": "15:59"}


def test_writes_need_the_token(api):
    assert api.post("/api/accounts/save", json={}, headers={"origin": BASE}).status_code == 403
    assert api.post("/api/ui", json={"effects": "calm"}, headers={"origin": BASE}).status_code == 403


def test_raising_risk_needs_the_typed_confirmation_and_waits_for_the_sweep(api):
    r = api.get("/api/accounts").json()
    acc = dict(r["accounts"][0]["account"], draft=False)
    h = api.post("/api/accounts/save", json={"account": acc, "original_name": "mffur1", "hash": r["hash"]}, headers=H).json()["hash"]
    up = dict(acc, max_risk_fraction_divisor=2, risk_usd_per_trade=500)
    res = api.post("/api/accounts/save", json={"account": up, "original_name": "mffur1", "hash": h}, headers=H)
    assert res.status_code == 428
    eff = res.json()["effect"]
    assert eff["confirm_text"] == "mffur1 1/2" and eff["new_risk_usd"] == 500 and eff["lives_new"] == 2
    res = api.post("/api/accounts/save", json={"account": up, "original_name": "mffur1", "hash": h, "confirm": "yes"}, headers=H)
    assert res.status_code == 428
    res = api.post("/api/accounts/save", json={"account": up, "original_name": "mffur1", "hash": h, "confirm": "mffur1 1/2"}, headers=H)
    assert res.status_code == 200
    saved = api.get("/api/accounts").json()["accounts"][0]["account"]
    # still trading at 1/3 until the sweep; the raise waits in pending_risk
    assert saved["max_risk_fraction_divisor"] == 3 and saved["risk_usd_per_trade"] == 333
    assert saved["pending_risk"]["max_risk_fraction_divisor"] == 2
    # lowering risk applies at once and cancels the pending raise
    h = api.get("/api/accounts").json()["hash"]
    down = dict(saved, max_risk_fraction_divisor=4, risk_usd_per_trade=250)
    assert api.post("/api/accounts/save", json={"account": down, "original_name": "mffur1", "hash": h}, headers=H).status_code == 200
    saved = api.get("/api/accounts").json()["accounts"][0]["account"]
    assert saved["max_risk_fraction_divisor"] == 4 and saved["pending_risk"] is None


def test_errors_block_saving_and_stale_pages_conflict(api):
    r = api.get("/api/accounts").json()
    bad = dict(r["accounts"][0]["account"], risk_usd_per_trade=900)
    res = api.post("/api/accounts/save", json={"account": bad, "original_name": "mffur1", "hash": r["hash"]}, headers=H)
    assert res.status_code == 422 and res.json()["issues"][0]["field"] == "risk_usd_per_trade"
    ok = dict(r["accounts"][0]["account"])
    assert api.post("/api/accounts/save", json={"account": ok, "original_name": "mffur1", "hash": r["hash"]}, headers=H).status_code == 200
    assert api.post("/api/accounts/save", json={"account": ok, "original_name": "mffur1", "hash": r["hash"]}, headers=H).status_code == 409


def test_save_as_preset_and_delete(api):
    r = api.get("/api/accounts").json()
    fields = dict(r["accounts"][0]["account"], start_balance_usd=50000, drawdown_usd=2500, risk_usd_per_trade=833)
    res = api.post("/api/presets/save", json={"label": "Lucid Flex 50k", "firm": "Lucid", "stage": "evaluation",
                                              "fields": fields}, headers=H)
    assert res.status_code == 200
    presets = {p["id"]: p for p in api.get("/api/accounts").json()["presets"]}
    own = presets[res.json()["id"]]
    assert own["builtin"] is False and own["fields"]["drawdown_usd"] == 2500 and "name" not in own["fields"]
    dup = api.post("/api/presets/save", json={"label": "Lucid Flex 50k", "firm": "Lucid", "stage": "evaluation", "fields": fields}, headers=H)
    assert dup.status_code == 422
    assert api.post("/api/presets/delete", json={"id": own["id"], "typed": "nope"}, headers=H).status_code == 422
    assert api.post("/api/presets/delete", json={"id": "mffu_pro_50k_funded", "typed": "MFFU Pro 50k"}, headers=H).status_code == 404
    assert api.post("/api/presets/delete", json={"id": own["id"], "typed": "Lucid Flex 50k"}, headers=H).status_code == 200


def test_settings_page_edits_only_its_fields(api):
    s = api.get("/api/settings").json()
    assert s["profile_stats"] is None and s["globals"]["blackout_before_min"] == 30
    ok = api.post("/api/settings/account", json={"name": "mffur1", "hash": s["hash"], "changes": {"risk_profile": "conservative"}}, headers=H)
    assert ok.status_code == 200
    s = api.get("/api/settings").json()
    no = api.post("/api/settings/account", json={"name": "mffur1", "hash": s["hash"], "changes": {"drawdown_usd": 5}}, headers=H)
    assert no.status_code == 422
    custom = api.post("/api/settings/account", json={"name": "mffur1", "hash": s["hash"], "changes": {"risk_profile": "custom", "custom_risk_usd": 500}}, headers=H)
    assert custom.status_code == 422 and custom.json()["issues"][0]["field"] == "custom_risk_usd"   # above $1,000 / 3


def test_delete_needs_the_typed_name(api):
    r = api.get("/api/accounts").json()
    assert api.post("/api/accounts/delete", json={"name": "mffub1", "typed": "x", "hash": r["hash"]}, headers=H).status_code == 422
    assert api.post("/api/accounts/delete", json={"name": "mffub1", "typed": "mffub1", "hash": r["hash"]}, headers=H).status_code == 200
    assert [a["account"]["name"] for a in api.get("/api/accounts").json()["accounts"]] == ["mffur1"]


def test_ui_settings_and_home(api):
    assert api.post("/api/ui", json={"effects": "loud"}, headers=H).status_code == 422
    assert api.post("/api/ui", json={"effects": "calm"}, headers=H).status_code == 200
    assert api.get("/api/settings").json()["ui"]["effects"] == "calm"
    home = api.get("/api/home").json()
    assert home["accounts"] == 2 and home["drafts"] == 2 and home["risk_per_trade_usd"] == [333.0]
    assert home["month_pnl_usd"] is None and home["closest_drawdown_margin_usd"] is None


# ── backup (owner 2026-11-15: data/panel/ to Drive NQ bot backup/panel/, its own lock) ──────────────────────────────
class FakeRclone:
    def __init__(self, fail_on: str | None = None):
        self.calls: list[list[str]] = []
        self.fail_on = fail_on

    def quick(self, args):
        self.calls.append(args)
        if args[0] == "listremotes":
            return 0, "gdrive:\n"
        return (1, "boom") if args[0] == self.fail_on else (0, "rclone v1.70.0\n")


def test_panel_backup_copies_then_checks_without_the_job_lock(paths, settings):
    from nqbot.ui.panel_backup import backup

    PanelStore(lambda: paths).accounts()   # creates data/panel/
    rc = FakeRclone()
    out: list[str] = []
    assert backup(paths, settings, rclone=rc, echo=out.append) == 0
    verbs = [c[0] for c in rc.calls]
    assert verbs == ["version", "listremotes", "copy", "check"]
    copy = rc.calls[2]
    assert "--checksum" in copy and copy[-2] == str(paths.data_dir / "panel") and copy[-1] == "gdrive:NQ bot backup/panel"
    assert ".panel.lock" in copy                      # lock files never copied
    assert not list((paths.data_dir / "panel").glob(".panel_backup.lock"))
    assert backup(paths, settings, rclone=FakeRclone(fail_on="check"), echo=out.append) == 1


def test_panel_backup_with_no_panel_folder_does_nothing(paths, settings):
    from nqbot.ui.panel_backup import backup

    rc = FakeRclone()
    assert backup(paths, settings, rclone=rc, echo=lambda s: None) == 0 and rc.calls == []


def test_status_lists_every_account_for_the_switcher(api):
    s = api.get("/api/status").json()
    a = {x["name"]: x for x in s["accounts"]}
    assert a["mffur1"]["kind"] == "Rapid" and a["mffub1"]["kind"] == "Builder"
    assert a["mffur1"]["target"] == {"kind": "profit_target", "usd": 1500} and a["mffur1"]["drawdown_usd"] == 1000
    # the live numbers come with the engine (step 22): null until then, never invented
    assert set(a["mffur1"]["live"].values()) == {None}
