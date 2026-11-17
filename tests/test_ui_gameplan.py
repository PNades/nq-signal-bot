"""The Gameplan page's API (SPEC §3.4, §9b item 4; build stage 5, `ui/api_gameplan.py`): the day's rules, the
upload with its free cost estimate, the paid parse only with the owner's approval (rule 2) and within the per-file cap,
replace only when asked, the owner's edits as overrides, and the confirmation that counts for the day's trading only
before 09:30 ET. The Claude API is the parser tests' fake (`anthropic_fakes.py`): no network, no cost."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from tests.anthropic_fakes import FIXTURES, FakeAnthropic, canned, message
from nqbot.config import load_features, load_settings
from nqbot.gameplan import overrides as ov
from nqbot.ui import api_gameplan as ag

ET = ZoneInfo("America/New_York")
DAY = date(2026, 3, 11)
FILE = FIXTURES / "2026-03-11.md"
TOKEN = {"x-cf-token": "tok"}


def _token(t):
    return None if t == "tok" else JSONResponse({"error": "no token"}, status_code=403)


def make(paths, *, now=datetime(2026, 3, 11, 8, 40, tzinfo=ET), count_tokens=3000, synthetic=False):
    fake = FakeAnthropic(count_tokens=count_tokens)
    clock = {"now": now}
    app = Starlette(routes=ag.routes(lambda: paths, load_settings(), load_features(), _token, synthetic=synthetic,
                                     now_fn=lambda: clock["now"], client_fn=None if synthetic else (lambda: fake)))
    return TestClient(app), fake, clock


def upload(c, name="2026-03-11.md", day=DAY, data: bytes | None = None):
    return c.post("/api/gameplan/upload", content=data if data is not None else FILE.read_bytes(),
                  headers={**TOKEN, "x-file-name": name, "x-gameplan-date": day.isoformat()})


def test_the_day_rules_windows_blackouts_and_an_early_close(paths):
    c, _, _ = make(paths)
    r = c.get("/api/gameplan", params={"date": "2025-08-12"}).json()      # CPI 08:30
    assert r["status"] == {"state": "none"} and r["gameplan"] is None
    w = r["rules"]["windows"]
    assert w["rth_open"] == "09:30" and w["flat_by"] == "15:59" and w["last_entry"] == "15:45"
    cpi = next(b for b in r["rules"]["blackouts"] if b["name"] == "CPI")
    assert (cpi["at"], cpi["from"], cpi["to"]) == ("08:30", "07:59:30", "09:30")       # 30 min + 30 s before, 60 after
    e = c.get("/api/gameplan", params={"date": "2025-11-28"}).json()["rules"]
    assert e["early_close"] == "13:00" and e["windows"]["flat_by"] == "12:59" and e["windows"]["last_entry"] == "12:45"
    assert c.get("/api/gameplan", params={"date": "2025-12-25"}).json()["rules"]["trading_day"] is False
    # today by default (New York)
    assert c.get("/api/gameplan").json()["date"] == DAY.isoformat()


def test_upload_estimates_for_free_and_parses_only_with_approval(paths):
    c, fake, _ = make(paths)
    r = upload(c)
    assert r.status_code == 200, r.text
    u = r.json()
    assert u["input_tokens"] == 3000 and u["worst_usd"] > 0 and not u["over_cap"] and not u["replaces"]
    assert fake.counts and not fake.requests                                # counted, nothing parsed yet
    staged = list((Path(paths.tmp_dir) / "gameplan_uploads").glob("*/*"))
    assert [p.name for p in staged] == ["2026-03-11.md"]
    # no approval, or less than the printed worst case: refused, no call
    assert c.post("/api/gameplan/parse", json={"upload": u["upload"]}, headers=TOKEN).status_code == 400
    assert c.post("/api/gameplan/parse", json={"upload": u["upload"], "approve_usd": u["worst_usd"] - 0.01},
                  headers=TOKEN).status_code == 400
    assert not fake.requests
    p = c.post("/api/gameplan/parse", json={"upload": u["upload"], "approve_usd": u["worst_usd"]}, headers=TOKEN)
    assert p.status_code == 200, p.text
    j = p.json()
    assert len(fake.requests) == 1 and j["parsed"]["status"] in ("ok", "flagged")
    assert j["date"] == DAY.isoformat() and j["gameplan"]["zones"] and j["status"]["state"] == "parsed"
    assert not list((Path(paths.tmp_dir) / "gameplan_uploads").glob("*/*"))  # the staged file is gone
    assert DAY.isoformat() in j["days"]
    # the same file again: already parsed, no estimate, no call
    again = upload(c).json()
    assert again["already"] == DAY.isoformat() and len(fake.requests) == 1


def test_over_the_per_file_cap_goes_to_the_cli(paths):
    c, fake, _ = make(paths, count_tokens=400_000)
    u = upload(c).json()
    assert u["over_cap"] is True
    r = c.post("/api/gameplan/parse", json={"upload": u["upload"], "approve_usd": u["worst_usd"]}, headers=TOKEN)
    assert r.status_code == 400 and "--approve-usd" in r.json()["error"] and not fake.requests


def test_a_stored_day_is_replaced_only_when_asked(paths):
    c, fake, _ = make(paths)
    u = upload(c).json()
    c.post("/api/gameplan/parse", json={"upload": u["upload"], "approve_usd": u["worst_usd"]}, headers=TOKEN)
    other = FILE.read_bytes() + b"\n\nEdited after the first upload.\n"
    u2 = upload(c, name="2026-03-11 v2.md", data=other).json()
    assert u2["replaces"] is True
    fake.queue.append(message(canned("2026-03-11.md")))           # the v2 file gets the same canned answer
    r = c.post("/api/gameplan/parse", json={"upload": u2["upload"], "approve_usd": u2["worst_usd"]}, headers=TOKEN)
    assert r.status_code == 409 and "Replace" in r.json()["error"] and len(fake.requests) == 1
    r = c.post("/api/gameplan/parse", json={"upload": u2["upload"], "approve_usd": u2["worst_usd"], "replace": True},
               headers=TOKEN)
    assert r.status_code == 200, r.text
    assert r.json()["gameplan"]["source"]["file"] == "2026-03-11 v2.md"


def _parsed(paths, **kw):
    c, fake, clock = make(paths, **kw)
    u = upload(c).json()
    j = c.post("/api/gameplan/parse", json={"upload": u["upload"], "approve_usd": u["worst_usd"]}, headers=TOKEN).json()
    return c, clock, j


def test_edits_are_overrides_and_the_confirmation_counts_only_before_the_open(paths, monkeypatch):
    c, clock, j = _parsed(paths)
    wall = {"t": datetime(2026, 3, 11, 8, 40, tzinfo=ET)}                # the overrides' own clock (edits, confirm)
    monkeypatch.setattr(ov, "_now", lambda: wall["t"].isoformat())
    z = j["gameplan"]["zones"][0]
    r = c.post("/api/gameplan/edit", json={"date": DAY.isoformat(), "entry": z["id"], "field": "stop", "value": "21,512.25"},
               headers=TOKEN)
    assert r.status_code == 200, r.text
    assert next(x for x in r.json()["gameplan"]["zones"] if x["id"] == z["id"])["stop"] == 21512.25
    ops = ov.load(Path(paths.data_dir) / "processed" / "gameplans", DAY)["ops"]
    assert ops[-1]["path"][-1] == "stop" and ops[-1]["value"] == 21512.25
    r = c.post("/api/gameplan/edit", json={"date": DAY.isoformat(), "field": "hvl", "value": "21,400–21,420"}, headers=TOKEN)
    assert r.json()["gameplan"]["hvl"] == [21400.0, 21420.0]
    assert c.post("/api/gameplan/edit", json={"date": DAY.isoformat(), "field": "side", "entry": z["id"], "value": "long"},
                  headers=TOKEN).status_code == 422
    # confirmed at 08:40 ET: counts for the day's trading
    s = c.post("/api/gameplan/confirm", json={"date": DAY.isoformat()}, headers=TOKEN).json()["status"]
    assert s["state"] == "confirmed" and s["counts_for_trading"] is True
    # an edit after it: confirm again
    wall["t"] = datetime(2026, 3, 11, 8, 50, tzinfo=ET)
    c.post("/api/gameplan/edit", json={"date": DAY.isoformat(), "entry": z["id"], "field": "stop", "value": 21513},
           headers=TOKEN)
    s = c.get("/api/gameplan", params={"date": DAY.isoformat()}).json()["status"]
    assert s["state"] == "changed" and s["counts_for_trading"] is False


def test_a_confirmation_after_the_open_is_for_training_only(paths, monkeypatch):
    c, clock, j = _parsed(paths)
    monkeypatch.setattr(ov, "_now", lambda: datetime(2026, 3, 11, 9, 41, tzinfo=ET).isoformat())
    s = c.post("/api/gameplan/confirm", json={"date": DAY.isoformat()}, headers=TOKEN).json()["status"]
    assert s["state"] == "confirmed" and s["confirmed_at"] == "09:41" and s["counts_for_trading"] is False


def test_every_write_needs_the_token_and_bad_files_are_refused(paths):
    c, fake, _ = make(paths)
    assert c.post("/api/gameplan/upload", content=b"x", headers={"x-file-name": "a.md"}).status_code == 403
    for route in ("parse", "edit", "confirm", "discard"):
        assert c.post(f"/api/gameplan/{route}", json={}).status_code == 403
    assert upload(c, name="notes.exe").status_code == 415
    assert upload(c, data=b"").status_code == 400
    # a name with a path in it is kept inside the staging folder
    r = upload(c, name="..\\..\\2026-03-11.md")
    assert r.status_code == 200
    root = (Path(paths.tmp_dir) / "gameplan_uploads").resolve()
    assert all(root in p.resolve().parents for p in root.glob("**/*") if p.is_file())


def test_edit_values():
    assert ag.parse_range("29,450–29,500") == [29450.0, 29500.0]
    assert ag.parse_range("29500") == [29500.0, 29500.0] and ag.parse_range("") is None
    assert ag.edit_value("targets", "29,555 → 29,600-29,620") == [
        {"as_written": "29,555", "low": 29555.0, "high": 29555.0},
        {"as_written": "29,600-29,620", "low": 29600.0, "high": 29620.0}]
    with pytest.raises(ValueError):
        ag.edit_value("side", "long")


def test_the_synthetic_panel_never_touches_the_owners_gameplans(paths):
    c, _, _ = make(paths, synthetic=True)
    u = upload(c).json()
    j = c.post("/api/gameplan/parse", json={"upload": u["upload"], "approve_usd": u["worst_usd"]}, headers=TOKEN).json()
    assert j["synthetic"] is True and j["gameplan"]["zones"]
    assert not (Path(paths.data_dir) / "processed" / "gameplans").exists()
    assert not (Path(paths.tmp_dir) / "gameplan_uploads").exists()
    assert json.loads(ag.SyntheticClient.CANNED.read_text(encoding="utf-8"))


# ── owner 2026-11-16, stage-5 answers ─────────────────────────────────────────────────────────────────────────────

DAY2 = date(2026, 3, 12)          # the fixture's content says 2026-03-11: a review flag on the date


def _parsed_on(paths, day, now, **kw):
    c, fake, clock = make(paths, now=now, **kw)
    u = upload(c, day=day).json()
    j = c.post("/api/gameplan/parse", json={"upload": u["upload"], "approve_usd": u["worst_usd"]}, headers=TOKEN).json()
    return c, fake, clock, j


def _review(j):
    return [f for f in j["gameplan"]["flags"] if f.get("review", True)]


def test_before_the_open_confirm_needs_every_review_flag_fixed_or_ok(paths, monkeypatch):
    monkeypatch.setattr(ov, "_now", lambda: datetime(2026, 3, 12, 8, 40, tzinfo=ET).isoformat())
    c, _, _, j = _parsed_on(paths, DAY2, datetime(2026, 3, 12, 8, 40, tzinfo=ET))
    open_ = _review(j)
    assert open_ and any(f["field"] == "date" for f in open_)
    r = c.post("/api/gameplan/confirm", json={"date": DAY2.isoformat()}, headers=TOKEN)
    assert r.status_code == 409 and "OK as parsed" in r.json()["error"]
    # "OK as parsed": only an open review flag, matched by its field and exact text
    assert c.post("/api/gameplan/accept", json={"date": DAY2.isoformat(), "field": "date", "flag": "not the text"},
                  headers=TOKEN).status_code == 404
    for f in open_:
        r = c.post("/api/gameplan/accept", json={"date": DAY2.isoformat(), "field": f["field"], "flag": f["reason"]}, headers=TOKEN)
        assert r.status_code == 200, r.text
    j = r.json()
    assert not _review(j) and j["status"]["review_flags"] == 0
    assert any("[OK as parsed: owner]" in f["reason"] for f in j["gameplan"]["flags"])
    s = c.post("/api/gameplan/confirm", json={"date": DAY2.isoformat()}, headers=TOKEN).json()["status"]
    assert s["state"] == "confirmed" and s["counts_for_trading"] is True


def test_after_the_open_confirm_is_allowed_with_flags_open(paths, monkeypatch):
    monkeypatch.setattr(ov, "_now", lambda: datetime(2026, 3, 12, 9, 45, tzinfo=ET).isoformat())
    c, _, _, j = _parsed_on(paths, DAY2, datetime(2026, 3, 12, 9, 45, tzinfo=ET))
    assert _review(j)
    r = c.post("/api/gameplan/confirm", json={"date": DAY2.isoformat()}, headers=TOKEN)
    assert r.status_code == 200, r.text
    s = r.json()["status"]
    assert s["state"] == "confirmed" and s["counts_for_trading"] is False and s["review_flags"] > 0


def test_an_accepted_flag_reworded_by_a_reparse_is_open_again():
    doc = {"flags": [{"field": "date", "origin": "validate", "reason": "the new wording", "review": True}], "zones": []}
    out = ov.apply(doc, {"ops": [], "accepted": [{"field": "date", "flag": "the old wording", "at": "x"}]})
    assert out["flags"][0]["review"] is True and out["status"] == "flagged"


def test_a_page_edit_settles_exactly_the_flags_on_its_field():
    doc = {"zones": [{"id": "E1", "side": "buy", "trigger": {"nq_price": [100, 100], "es_gates": []}}],
           "flags": [{"field": "zones[E1].trigger.es_gates", "origin": "model", "reason": "a", "review": True},
                     {"field": "zones[E1].trigger.nq_price", "origin": "model", "reason": "b", "review": True}]}
    path = ["zones", {"id": "E1"}, "trigger", "es_gates"]
    page = ov.apply(doc, {"ops": [{"path": path, "value": [], "reason": "r", "at": "x", "resolves": ["zones[E1].trigger.es_gates"]}]})
    assert [f["review"] for f in page["flags"]] == [False, True]
    legacy = ov.apply(doc, {"ops": [{"path": path, "value": [], "reason": "r", "at": "x"}]})   # an archive op: the old rule
    assert [f["review"] for f in legacy["flags"]] == [False, False]
    assert ag.review_flags_for(doc, "es_gates", entry="E1") == ["zones[E1].trigger.es_gates"]
    assert ag.review_flags_for(doc, "trigger", entry="E1") == ["zones[E1].trigger.nq_price"]


def test_es_gates_invalidations_and_scenarios_are_editable(paths, monkeypatch):
    wall = {"t": datetime(2026, 3, 11, 8, 40, tzinfo=ET)}
    monkeypatch.setattr(ov, "_now", lambda: wall["t"].isoformat())
    c, _, _, j = _parsed_on(paths, DAY, datetime(2026, 3, 11, 8, 40, tzinfo=ET))
    z = j["gameplan"]["zones"][0]
    d = DAY.isoformat()
    r = c.post("/api/gameplan/edit", json={"date": d, "entry": z["id"], "field": "es_gates", "value": "ES > 6,812; ES < 6,790"}, headers=TOKEN)
    assert r.status_code == 200, r.text
    zz = next(x for x in r.json()["gameplan"]["zones"] if x["id"] == z["id"])
    assert [(g["direction"], g["price"]) for g in zz["trigger"]["es_gates"]] == [("above", [6812.0, 6812.0]), ("below", [6790.0, 6790.0])]
    r = c.post("/api/gameplan/edit", json={"date": d, "entry": z["id"], "field": "invalidation", "value": "1m close above 24,560"}, headers=TOKEN)
    inv = next(x for x in r.json()["gameplan"]["zones"] if x["id"] == z["id"])["invalidation"]
    assert inv == [{"as_written": "1m close above 24,560", "direction": "above", "price": 24560.0, "timeframe": "1m_close",
                    "instrument": "NQ", "acceptance_minutes": None}]
    sets = r.json()["gameplan"]["scenario_sets"]
    sid = sets[0]["scenarios"][0]["id"]
    for field, value, want in (("scenario.prob", "40%", 0.4), ("scenario.bias", "down", "down"), ("scenario.desc", "fails at E1", "fails at E1")):
        r = c.post("/api/gameplan/edit", json={"date": d, "field": field, "set": 0, "scenario": sid, "value": value}, headers=TOKEN)
        assert r.status_code == 200, r.text
        assert next(x for x in r.json()["gameplan"]["scenario_sets"][0]["scenarios"] if x["id"] == sid)[field.split(".")[1]] == want
    assert c.post("/api/gameplan/edit", json={"date": d, "field": "scenario.prob", "set": 0, "scenario": sid, "value": "140"},
                  headers=TOKEN).status_code == 422
    # kept apart from the parsed file
    parsed = json.loads((Path(paths.data_dir) / "processed" / "gameplans" / f"{d}.json").read_text(encoding="utf-8"))
    assert parsed["scenario_sets"][0]["scenarios"][0]["desc"] != "fails at E1"
    # an edit after the confirmation asks for a new one (after the open, so open flags don't block the confirmation)
    clock_after = datetime(2026, 3, 11, 9, 40, tzinfo=ET)
    wall["t"] = clock_after
    c2, _, clk = make(paths, now=clock_after)
    assert c2.post("/api/gameplan/confirm", json={"date": d}, headers=TOKEN).status_code == 200
    wall["t"] = datetime(2026, 3, 11, 9, 50, tzinfo=ET)
    c2.post("/api/gameplan/edit", json={"date": d, "field": "scenario.prob", "set": 0, "scenario": sid, "value": "45"}, headers=TOKEN)
    assert c2.get("/api/gameplan", params={"date": d}).json()["status"]["state"] == "changed"


def test_the_actual_cost_is_shown_and_a_failed_parse_is_never_retried(paths):
    c, fake, _ = make(paths)
    fake.queue.append(message({}, stop_reason="max_tokens", output_tokens=16000))   # the first call fails
    u = upload(c).json()
    r = c.post("/api/gameplan/parse", json={"upload": u["upload"], "approve_usd": u["worst_usd"]}, headers=TOKEN)
    assert r.status_code == 422 and "Nothing is retried automatically" in r.json()["error"] and r.json()["cost_usd"] > 0
    assert len(fake.requests) == 1                                         # one call, no retry
    assert list((Path(paths.tmp_dir) / "gameplan_uploads").glob("*/*"))     # the file waits for another click
    r = c.post("/api/gameplan/parse", json={"upload": u["upload"], "approve_usd": u["worst_usd"]}, headers=TOKEN)
    assert r.status_code == 200 and len(fake.requests) == 2
    p = r.json()["parsed"]
    assert p["cost_usd"] > 0 and p["estimate_usd"] == u["worst_usd"] and p["cost_usd"] <= p["estimate_usd"]


def test_ledger_months_7_to_12_stay_off_the_page_until_gate_16(paths, monkeypatch):
    from nqbot.months import days_of
    from nqbot.performance import guard

    c, _, _ = make(paths)
    s = load_settings()
    sealed, dev = date(2026, 8, 12), date(2026, 3, 11)                    # ledger months 11 and 6
    assert ag.hidden(sealed, s, frozenset()) and not ag.hidden(dev, s, frozenset())
    for m in range(7, 13):                                                  # walk-forward and sealed alike
        assert ag.hidden(days_of(m, s)[3], s, frozenset()) and not ag.hidden(days_of(m, s)[3], s, frozenset({"16"}))
    for m in range(1, 7):
        assert not ag.hidden(days_of(m, s)[3], s, frozenset())
    assert not ag.hidden(date(2026, 11, 16), s, frozenset())               # after the window: a live day
    pdir = Path(paths.data_dir) / "processed" / "gameplans"
    pdir.mkdir(parents=True, exist_ok=True)
    for d in (sealed, dev):
        (pdir / f"{d.isoformat()}.json").write_text(json.dumps({"date": d.isoformat(), "flags": [], "zones": [], "status": "ok"}), encoding="utf-8")
    j = c.get("/api/gameplan").json()
    days, states = j["days"], j["day_states"]
    assert dev.isoformat() in days and sealed.isoformat() not in days       # the Day picker's list
    assert dev.isoformat() in states and sealed.isoformat() not in states and j["today"] in states
    assert c.get("/api/gameplan", params={"date": sealed.isoformat()}).status_code == 403
    assert upload(c, day=sealed).status_code == 403
    for route, body in (("edit", {"field": "hvl", "value": "1"}), ("confirm", {}), ("accept", {"field": "x", "flag": "y"})):
        assert c.post(f"/api/gameplan/{route}", json={"date": sealed.isoformat(), **body}, headers=TOKEN).status_code == 403
    monkeypatch.setattr(guard, "gates_passed", lambda *a, **k: frozenset({"16"}))
    assert sealed.isoformat() in c.get("/api/gameplan").json()["days"]
    assert c.get("/api/gameplan", params={"date": sealed.isoformat()}).status_code == 200


def test_the_plan_wide_conditions_are_editable(paths, monkeypatch):
    # owner 2026-11-17: edited in place, emptied to remove, added; one override of the whole list (kept apart from the
    # parsed file); an edit after the confirmation asks for a new one
    wall = {"t": datetime(2026, 3, 11, 9, 40, tzinfo=ET)}
    monkeypatch.setattr(ov, "_now", lambda: wall["t"].isoformat())
    c, _, _, j = _parsed_on(paths, DAY, datetime(2026, 3, 11, 9, 40, tzinfo=ET))
    d = DAY.isoformat()
    before = list(j["gameplan"]["other_conditions"] or [])
    r = c.post("/api/gameplan/edit", json={"date": d, "field": "other_conditions", "index": None, "value": "VIX under 16.5"}, headers=TOKEN)
    assert r.status_code == 200, r.text
    assert r.json()["gameplan"]["other_conditions"] == [*before, "VIX under 16.5"]
    n = len(before)
    r = c.post("/api/gameplan/edit", json={"date": d, "field": "other_conditions", "index": n, "value": "VIX under 17"}, headers=TOKEN)
    assert r.json()["gameplan"]["other_conditions"][-1] == "VIX under 17"
    assert c.post("/api/gameplan/edit", json={"date": d, "field": "other_conditions", "index": None, "value": "  "},
                  headers=TOKEN).status_code == 422
    assert c.post("/api/gameplan/edit", json={"date": d, "field": "other_conditions", "index": 99, "value": "x"},
                  headers=TOKEN).status_code == 404
    parsed = json.loads((Path(paths.data_dir) / "processed" / "gameplans" / f"{d}.json").read_text(encoding="utf-8"))
    assert (parsed.get("other_conditions") or []) == before                  # the parsed file is untouched
    ops = ov.load(Path(paths.data_dir) / "processed" / "gameplans", DAY)["ops"]
    assert [o["path"] for o in ops] == [["other_conditions"]]                 # one override of the whole list
    assert c.post("/api/gameplan/confirm", json={"date": d}, headers=TOKEN).status_code == 200
    wall["t"] = datetime(2026, 3, 11, 9, 50, tzinfo=ET)
    r = c.post("/api/gameplan/edit", json={"date": d, "field": "other_conditions", "index": n, "value": ""}, headers=TOKEN)
    assert r.json()["gameplan"]["other_conditions"] == before                 # emptied: removed
    assert r.json()["status"]["state"] == "changed"
