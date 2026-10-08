"""Owner overrides on top of parsed gameplans (owner 2026-10-07): kept apart, survive a re-parse, never silently
dropped."""

from __future__ import annotations

from datetime import date

from nqbot.gameplan import overrides as ov
from nqbot.gameplan import parser as gp
from tests.anthropic_fakes import FakeAnthropic
from tests.test_gameplan_parser import CANON, D_CANON, put, quiet, store  # noqa: F401 (pytest fixture)

D = date(2026, 3, 11)


def test_override_value_relabel_and_confirm(settings, store):
    (f,) = put(store, CANON)
    gp.parse_file(FakeAnthropic(), settings, store, f, out=quiet)
    raw = store.load(D_CANON)
    ov.set_value(store.processed_dir, D, ["zones", {"id": "E2"}, "zone"], [24255, 24280], "owner picked the map")
    ov.set_value(store.processed_dir, D, ["zones", {"id": "E3"}, "trigger", "nq_price"], [24150, 24150], "v3 way")
    eff = store.load_effective(D_CANON)
    e2 = next(z for z in eff["zones"] if z["id"] == "E2")
    assert e2["zone"] == [24255, 24280] and len(eff["owner_overrides"]) == 2
    assert store.load(D_CANON) == raw                       # the parsed file itself is never changed
    assert eff["owner_confirmed"] is None and eff["status"] == "ok"

    ov.set_value(store.processed_dir, D, ["zones", {"id": "E2"}, "zone"], [24260, 24280], "changed my mind")
    assert next(z for z in store.load_effective(D_CANON)["zones"] if z["id"] == "E2")["zone"] == [24260, 24280]
    assert len(ov.load(store.processed_dir, D)["ops"]) == 2  # replaced, not stacked

    ov.confirm(store.processed_dir, D, "all notes accepted")
    assert store.load_effective(D_CANON)["owner_confirmed"]["note"] == "all notes accepted"


def test_relabelling_a_review_note_as_info_clears_the_status():
    doc = {"zones": [], "status": "flagged",
           "flags": [{"field": "scenario_sets[0]", "reason": "left empty", "origin": "model", "review": True}]}
    out = ov.apply(doc, {"ops": [{"path": ["flags", {"field": "scenario_sets[0]", "reason": "left empty"}, "review"],
                                  "value": False, "reason": "owner rule", "at": "t"}]})
    assert out["status"] == "ok" and out["flags"][0]["review"] is False and doc["flags"][0]["review"] is True


def test_override_that_no_longer_matches_becomes_a_review_flag():
    doc = {"zones": [{"id": "Z9", "zone": [1, 2]}], "flags": [], "status": "ok"}
    out = ov.apply(doc, {"ops": [{"path": ["zones", {"id": "E2"}, "zone"], "value": [5, 6], "reason": "r", "at": "t"}]})
    assert out["status"] == "flagged" and out["owner_overrides"] == []
    assert out["flags"][-1]["field"] == "owner_override" and "matches no single item" in out["flags"][-1]["reason"]


def test_unlabelled_entries_are_found_by_side_and_level_not_name():
    """Owner 2026-10-07: 08-17's Z6 came back as L5-ACC on the next parse; only E-labels are stable."""
    z6 = {"id": "Z6", "side": "sell", "zone": None, "trigger": {"nq_price": [30150, 30150]}, "stop": None}
    assert ov.entry_selector(z6) == {"side": "sell", "trigger.nq_price": [30150, 30150]}
    assert ov.entry_selector({"id": "E5b", "side": "buy", "zone": [1, 2], "trigger": {}}) == {"id": "E5b"}
    assert ov.entry_selector({"id": "S1", "side": "buy", "zone": [1, 2], "trigger": {"nq_price": None}}) == \
        {"side": "buy", "zone": [1, 2]}
    renamed = {"zones": [{**z6, "id": "L5-ACC"}], "flags": [], "status": "ok"}
    op = {"path": ["zones", ov.entry_selector(z6), "stop"], "value": 30175, "reason": "r", "at": "t"}
    assert ov.apply(renamed, {"ops": [op]})["zones"][0]["stop"] == 30175
    twice = {"zones": [{**z6, "id": "A"}, {**z6, "id": "B"}], "flags": [], "status": "ok"}
    assert ov.apply(twice, {"ops": [op]})["status"] == "flagged"   # ambiguous: never applied to both
    named = ov.entry_selector(twice["zones"][1], twice["zones"])    # not unique by side + level: the name is added
    assert named == {"side": "sell", "trigger.nq_price": [30150, 30150], "id": "B"}
    assert ov.apply(twice, {"ops": [{**op, "path": ["zones", named, "stop"]}]})["zones"][1]["stop"] == 30175


def test_overrides_survive_a_reparse(settings, store):
    (f,) = put(store, CANON)
    gp.parse_file(FakeAnthropic(), settings, store, f, out=quiet)
    ov.set_value(store.processed_dir, D, ["zones", {"id": "E1"}, "stop"], 24580, "owner")
    gp.parse_file(FakeAnthropic(), settings, store, f, reparse=True, out=quiet)
    assert next(z for z in store.load_effective(D_CANON)["zones"] if z["id"] == "E1")["stop"] == 24580
    assert next(z for z in store.load(D_CANON)["zones"] if z["id"] == "E1")["stop"] == 24575


def test_an_override_settles_the_review_flags_on_its_field():
    doc = {"zones": [{"id": "E1", "targets": []}], "regime_line": [29990, 29990], "status": "flagged",
           "scenario_sets": [{"frame": "ny_am", "scenarios": [{"id": "S2", "prob": None}]}],
           "flags": [{"field": "zones[E1].targets", "reason": "two versions", "origin": "validate", "review": True},
                     {"field": "hvl,regime_line", "reason": "same value", "origin": "validate", "review": True},
                     {"field": "scenario_sets[0].scenarios[S2].prob", "reason": "two", "origin": "model",
                      "review": True},
                     {"field": "zones[E1].stop", "reason": "other field", "origin": "model", "review": True}]}
    ops = [{"path": ["zones", {"id": "E1"}, "targets"], "value": [], "reason": "r", "at": "t"},
           {"path": ["regime_line"], "value": None, "reason": "r", "at": "t"},
           {"path": ["scenario_sets", {"frame": "ny_am"}, "scenarios", {"id": "S2"}, "prob"], "value": None,
            "reason": "r", "at": "t"}]
    out = ov.apply(doc, {"ops": ops})
    assert [f["review"] for f in out["flags"]] == [False, False, False, True]   # zones[E1].stop is untouched
    assert out["flags"][0]["reason"].endswith("[resolved by owner override]")
