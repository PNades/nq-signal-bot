"""Review rounds and answers (review/store.py) and the Review API (ui/api_review.py): development months only (§7 rule
2b), the decision-time cut (§8), answers stored once in `answers_<round>.parquet`; synthetic data only."""

from datetime import date

import pyarrow.parquet as pq
import pytest
from starlette.testclient import TestClient

from nqbot.config import load_features, load_settings
from nqbot.months import NotDevelopmentMonth
from nqbot.review import store as rs
from nqbot.review import synthetic as syn
from nqbot.ui.app import PanelConfig, create_app

BASE = "http://127.0.0.1:8733"
H = {"origin": BASE, "x-cf-token": "tok"}
GOOD = {"verdict": "take", "reason": "Held the ON high twice; the King sits above."}


@pytest.fixture(scope="module")
def synth():
    return syn.synthetic_round()


@pytest.fixture
def round_on_disk(paths, synth):
    meta, cards = synth
    settings = load_settings()
    rs.write_round(paths, "r1", {k: v for k, v in meta.items() if k not in ("id", "cards", "synthetic")}, cards, settings)
    return paths, settings


def test_a_round_refuses_a_test_month(paths, synth):
    _, cards = synth
    bad = [{**cards[0], "day": "2026-05-13"}]                     # ledger month 8 (walk-forward)
    with pytest.raises(NotDevelopmentMonth):
        rs.write_round(paths, "bad", {}, bad, load_settings())
    assert not (paths.data_dir / "reviews" / "rounds" / "bad").exists()


def test_every_card_read_checks_its_month_again(round_on_disk):
    paths, settings = round_on_disk
    f = paths.data_dir / "reviews" / "rounds" / "r1" / "cards" / "c01.json"
    f.write_text(f.read_text(encoding="utf-8").replace('"day":"2026-01-14"', '"day":"2026-08-12"'), encoding="utf-8")
    with pytest.raises(NotDevelopmentMonth):
        rs.load_card(paths, "r1", "c01", settings)


def test_answers_are_stored_once_with_the_outcome(round_on_disk):
    paths, settings = round_on_disk
    row = rs.answer(paths, "r1", "c02", {**GOOD, "confidence": 4, "entry_choice": "plain", "stop": "21348.5"}, settings)
    assert row["verdict"] == "take" and row["result"] == "stop" and row["correct"] is False
    with pytest.raises(rs.ReviewRefused, match="answered already"):
        rs.answer(paths, "r1", "c02", GOOD, settings)
    t = pq.read_table(paths.data_dir / "reviews" / "answers_r1.parquet").to_pylist()
    assert len(t) == 1 and t[0]["stop"] == 21348.5 and t[0]["confidence"] == 4 and t[0]["entry_choice"] == "plain"
    s = rs.summary(paths, "r1")
    assert s["answered"] == 1 and s["n"] == 5
    c = {x["id"]: x for x in s["cards"]}
    assert c["c02"]["stratum"] == "lost" and "stratum" not in c["c01"]   # the stratum only once answered


@pytest.mark.parametrize("body,msg", [({"verdict": "maybe", "reason": "x"}, "verdict"),
                                      ({"verdict": "take", "reason": "  "}, "reason"),
                                      ({**GOOD, "confidence": 6}, "confidence"),
                                      ({**GOOD, "entry_choice": "market"}, "entry choice"),
                                      ({**GOOD, "target": "abc"}, "target")])
def test_answers_are_validated(body, msg):
    with pytest.raises(rs.ReviewRefused, match=msg):
        rs.validate_answer(body)


def test_correct_against_the_outcome():
    assert rs.correct("take", 1.2) and not rs.correct("take", -1.0)
    assert rs.correct("skip", -1.0) and rs.correct("skip", 0.0) and not rs.correct("skip", 0.4)
    assert rs.correct("take", None) is None


def client(paths, synthetic=False):
    cfg = PanelConfig(port=8733, paths_fn=lambda: paths, token="tok", settings=load_settings(), features=load_features(),
                      synthetic=synthetic)
    return TestClient(create_app(cfg), base_url=BASE)


def test_api_sends_only_the_pre_half_until_the_answer(round_on_disk):
    paths, _ = round_on_disk
    c = client(paths)
    idx = c.get("/api/review").json()
    assert idx["round"]["id"] == "r1" and idx["round"]["answered"] == 0
    got = c.get("/api/review/card", params={"round": "r1", "card": "c03"}).json()
    assert "post" not in got and "outcome" not in got and got["answer"] is None
    assert max(b[0] for b in got["pre"]["bars"]["NQ"]) <= got["t"]["decision"]
    assert c.post("/api/review/answer", json={"round": "r1", "card": "c03", **GOOD}, headers={"origin": BASE}).status_code == 403
    r = c.post("/api/review/answer", json={"round": "r1", "card": "c03", **GOOD}, headers=H)
    assert r.status_code == 200 and r.json()["card"]["outcome"]["result"] == "timeout"
    again = c.get("/api/review/card", params={"round": "r1", "card": "c03"}).json()
    assert again["answer"]["verdict"] == "take" and "post" in again
    assert c.post("/api/review/answer", json={"round": "r1", "card": "c03", **GOOD}, headers=H).status_code == 409


def test_api_without_rounds_or_ssd_says_why(paths):
    assert "round-new" in client(paths).get("/api/review").json()["empty"]
    assert "SSD" in client(None).get("/api/review").json()["empty"]
    assert client(None).get("/api/review/card", params={"round": "r1", "card": "c01"}).status_code == 503


def test_api_synthetic_answers_stay_in_memory(paths):
    c = client(paths, synthetic=True)
    idx = c.get("/api/review").json()
    assert idx["synthetic"] and idx["round"]["synthetic"] and idx["round"]["n"] == 5
    r = c.post("/api/review/answer", json={"round": "synthetic", "card": "c01", **GOOD}, headers=H)
    assert r.status_code == 200 and r.json()["answer"]["correct"] is True
    assert c.get("/api/review").json()["round"]["answered"] == 1
    assert not (paths.data_dir / "reviews").exists()            # nothing written to the owner's answers


def test_bad_ids_are_refused(round_on_disk):
    paths, settings = round_on_disk
    with pytest.raises(rs.ReviewRefused):
        rs.load_card(paths, "r1", "../x", settings)
    with pytest.raises(rs.ReviewRefused):
        rs.round_dir(paths, "../../etc")
    assert client(paths).get("/api/review/card", params={"round": "r1", "card": "c99"}).status_code == 404


def test_development_days_reads_the_build_plan(tmp_path):
    (tmp_path / "build_plan.json").write_text('{"sessions": ["2025-10-01", "2026-01-14", "2026-05-13"]}', encoding="utf-8")
    assert rs.development_days(tmp_path, load_settings()) == [date(2025, 10, 1), date(2026, 1, 14)]


# ── preview rounds (owner 2026-11-16): kept apart, marked, and their touches never in a real round ─────────────────

def test_a_preview_round_and_its_answers_are_kept_apart_and_marked(paths, synth):
    meta, cards = synth
    settings = load_settings()
    rs.write_round(paths, "pv1", {}, cards, settings, preview=True)
    rs.write_round(paths, "r2", {}, cards, settings)
    assert (paths.data_dir / "reviews" / "preview" / "rounds" / "pv1" / "round.json").is_file()
    assert not (paths.data_dir / "reviews" / "rounds" / "pv1").exists()
    with pytest.raises(rs.ReviewRefused, match="exists already"):
        rs.write_round(paths, "pv1", {}, cards, settings)          # one id across real and preview rounds
    row = rs.answer(paths, "pv1", "c01", GOOD, settings)
    assert row["preview"] is True
    t = pq.read_table(paths.data_dir / "reviews" / "preview" / "answers_pv1.parquet").to_pylist()
    assert t[0]["preview"] is True and t[0]["why"]
    assert [p.name for p in (paths.data_dir / "reviews").glob("*.parquet")] == []   # data/reviews/*.parquet: real only
    assert rs.answer(paths, "r2", "c01", GOOD, settings)["preview"] is False
    assert [p.name for p in (paths.data_dir / "reviews").glob("*.parquet")] == ["answers_r2.parquet"]
    assert [(m["id"], m["preview"]) for m in rs.list_rounds(paths)] == [("r2", False), ("pv1", True)]
    assert rs.summary(paths, "pv1")["preview"] is True


def _cand(i, day, touch_s, direction):
    from nqbot.review import sampler as smp

    return smp.Candidate(day=day, decision_id=f"d{i}", event_id=f"e{i}", touch_group_id=f"g{i}", style="d",
                         decision_ts=touch_s * 10**9, minute_et=600, stack_count=2, level_group="vwap", lost=False,
                         never_take=False, extra={"pair_id": f"p{i}", "touch_ts": touch_s * 10**9, "direction": direction})


def test_every_touch_a_preview_card_shows_is_left_out_of_real_rounds(paths, synth, monkeypatch):
    meta, cards = synth
    rs.write_round(paths, "pv1", {}, cards, load_settings(), preview=True)
    seen = rs.preview_touches(paths)
    # every touch of every preview card, before and after its answer
    shown = {(c["day"], e["t"], e["dir"]) for c in cards for h in ("pre", "post") for e in c[h]["events"]}
    assert set(seen) == shown and len(shown) == 5
    day, t, d = seen[0]
    cands = [_cand(1, day, t, d), _cand(2, day, t + 40, d),          # the same touch; the same touch in another build
             _cand(3, day, t + 3600, d), _cand(4, day, t, -d), _cand(5, "2026-01-15", t, d)]
    keep, n = rs.exclude_seen(cands, seen)
    assert n == 2 and [c.decision_id for c in keep] == ["d3", "d4", "d5"]

    # through draw_round itself: a real round never holds a shown touch; a preview round doesn't filter
    from types import SimpleNamespace

    from nqbot.checks import feature_build as fb
    from nqbot.review import cards as cd

    build = paths.data_dir / "b"
    (build / "pairs" / "L").mkdir(parents=True)
    (build / "build_plan.json").write_text('{"sessions": ["2026-01-14"]}', encoding="utf-8")
    monkeypatch.setattr(fb, "out_dir", lambda env, key: build)
    monkeypatch.setattr(rs, "candidates", lambda *a: list(cands))
    monkeypatch.setattr(cd, "load_day", lambda *a: None)
    monkeypatch.setattr(cd, "card_payload", lambda dd, cid, did, pid, stratum=None, why=None:
                        {"id": cid, "day": "2026-01-14", "decision_id": did, "pair_id": pid, "stratum": stratum, "why": why})
    env = SimpleNamespace(paths=paths, settings=load_settings())
    rs.draw_round(env, "K", "L", 10, 1, "real1", echo=lambda *_: None)
    real = rs.load_round(paths, "real1")
    assert sorted(c["decision_id"] for c in real["cards"]) == ["d3", "d4", "d5"] and real["excluded_preview_touches"] == 2
    assert all(c["why"] for c in real["cards"])
    rs.draw_round(env, "K", "L", 10, 1, "pv2", echo=lambda *_: None, preview=True)
    pv = rs.load_round(paths, "pv2")                                 # 3 a session (smp.MAX_PER_DAY) + the other day
    assert pv["excluded_preview_touches"] == 0 and len(pv["cards"]) == 4
    assert {c["decision_id"] for c in pv["cards"]} & {"d1", "d2"}     # a preview round may hold a shown touch


def test_deleting_preview_answers_keeps_their_touches_excluded(paths, synth):
    # owner 2026-11-16: the preview answers were test inputs and were deleted; the record of which touches a preview
    # card showed comes from the preview round's cards, never from its answers
    meta, cards = synth
    settings = load_settings()
    rs.write_round(paths, "pv1", {}, cards, settings, preview=True)
    before = rs.preview_touches(paths)
    rs.answer(paths, "pv1", "c01", GOOD, settings)
    assert rs.preview_touches(paths) == before
    rs.answers_path(paths, "pv1").unlink()
    assert rs.preview_touches(paths) == before and len(before) == 5
    assert rs.read_answers(paths, "pv1") == {} and rs.summary(paths, "pv1")["answered"] == 0


def test_a_real_round_is_spread_by_month_after_the_preview_exclusion(paths, monkeypatch):
    # owner 2026-11-16: the real round's cards follow each month's share of the sessions (months 1 and 5 here, 4 and 2
    # sessions), drawn from what the preview exclusion left: the exclusion empties two of month 1's sessions, and
    # without the quotas month 5's many candidates would take most of the cards. The exclusion itself is unchanged.
    from types import SimpleNamespace

    from nqbot.checks import feature_build as fb
    from nqbot.review import cards as cd

    import json

    m1 = ["2025-10-01", "2025-10-02", "2025-10-03", "2025-10-06"]
    m5 = ["2026-02-02", "2026-02-03"]
    cands, seen, i = [], [], 0
    for day in m1 + m5:
        for k in range(10 if day in m5 else 5):
            i += 1
            t = 1_760_000_000 + i * 600
            cands.append(_cand(i, day, t, 1))
            if day in m1[:2]:
                seen.append((day, t, 1))                       # shown on a preview card
    build = paths.data_dir / "b"
    (build / "pairs" / "L").mkdir(parents=True)
    (build / "build_plan.json").write_text(json.dumps({"sessions": m1 + m5}), encoding="utf-8")
    monkeypatch.setattr(fb, "out_dir", lambda env, key: build)
    monkeypatch.setattr(rs, "candidates", lambda *a: list(cands))
    monkeypatch.setattr(rs, "preview_touches", lambda p: list(seen))
    monkeypatch.setattr(cd, "load_day", lambda *a: None)
    monkeypatch.setattr(cd, "card_payload", lambda dd, cid, did, pid, stratum=None, why=None:
                        {"id": cid, "day": next(c.day for c in cands if c.decision_id == did), "decision_id": did,
                         "pair_id": pid, "stratum": stratum, "why": why})
    env = SimpleNamespace(paths=paths, settings=load_settings())
    rs.draw_round(env, "K", "L", 6, 1, "real-m", echo=lambda *_: None)
    real = rs.load_round(paths, "real-m")
    months = [1 if c["day"] in m1 else 5 for c in real["cards"]]
    assert months.count(1) == 4 and months.count(5) == 2        # 6 x 4/6 and 6 x 2/6
    assert not {c["day"] for c in real["cards"]} & set(m1[:2])  # nothing from the excluded touches
    assert real["excluded_preview_touches"] == 10
    assert real["by_month"]["1"] == {"sessions": 4, "quota": 4, "candidates": 10, "cards": 4}
    # without the quotas (the plain sampler on the same pool) month 5 takes more than its share
    from nqbot.review import sampler as smp

    keep, _ = rs.exclude_seen(cands, seen)
    plain = smp.sample(keep, 6, 1)
    assert sum(p.cand.day in m5 for p in plain) > 2
