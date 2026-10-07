"""Gameplan schema, validator and converter (step 9, SPEC §3.4, §5.6). Hand-built examples, no API."""

from __future__ import annotations

import copy
import struct
import zlib
from datetime import date

import pytest

from nqbot.gameplan import validate
from nqbot.gameplan.convert import CannotConvert, convert
from nqbot.gameplan.schema import GAMEPLAN_SCHEMA, from_wire
from tests.anthropic_fakes import FIXTURES, canned

UNSUPPORTED = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minLength", "maxLength",
               "minItems", "maxItems", "uniqueItems", "pattern", "patternProperties"}


def _walk(schema):
    yield schema
    for v in schema.values():
        if isinstance(v, dict):
            yield from _walk(v)
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, dict):
                    yield from _walk(x)


def conforms(value, schema) -> bool:
    """Enough of JSON Schema for GAMEPLAN_SCHEMA: type, anyOf, enum, properties/required, items."""
    if "anyOf" in schema:
        return any(conforms(value, s) for s in schema["anyOf"])
    if "enum" in schema and value not in schema["enum"]:
        return False
    t = schema.get("type")
    if t == "null":
        return value is None
    if t == "string":
        return isinstance(value, str)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "array":
        return isinstance(value, list) and all(conforms(x, schema["items"]) for x in value)
    if t == "object":
        return (isinstance(value, dict) and set(value) == set(schema["properties"])
                and all(conforms(value[k], s) for k, s in schema["properties"].items()))
    return False


def tgt(low, high=None, words=None):
    high = low if high is None else high
    return {"low": low, "high": high, "as_written": words or (f"{low}" if low == high else f"{low}-{high}")}


def inv(direction, price, timeframe="1m_close", minutes=None, instrument="NQ"):
    return {"instrument": instrument, "direction": direction, "price": price, "timeframe": timeframe,
            "acceptance_minutes": minutes, "as_written": f"{direction} {price}"}


def gate(direction, low, high=None):
    return {"direction": direction, "price": [low, low if high is None else high],
            "as_written": f"ES {direction} {low}"}


def spec_example() -> dict:
    """The §5.6 example, with the audit fields and nulls the schema requires."""
    return {
        "date": "2026-09-29", "date_evidence": "2026-09-29", "hvl": [30590, 30590], "hvl0": [30600, 30600],
        "regime_line": [30575, 30615],
        "zones": [{"id": "E1", "side": "sell", "zone": [30640, 30722],
                   "trigger": {"type": "failed_audit", "es_gates": [gate("below", 7766)], "note": None},
                   "stop": 30734,
                   "invalidation": [inv("above", 30722)],
                   "targets": [tgt(30615), tgt(30590)], "source_quote": "E1"}],
        "scenario_sets": [{"frame": "ny_am", "label_as_written": None, "scenarios": [
            {"id": "S-A", "prob": 0.5, "bias": "down", "desc": "lid refuses, rotation to regime line"},
            {"id": "S-B", "prob": 0.5, "bias": "up", "desc": "lid breaks"}]}],
        "version_used": None, "dropped": [], "uncertain": [],
    }


# --- schema ---------------------------------------------------------------------------------------------------

def test_schema_uses_only_structured_output_keywords():
    for node in _walk(GAMEPLAN_SCHEMA):
        assert not UNSUPPORTED & set(node), node
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert node["required"] == list(node["properties"])


def to_wire(doc: dict) -> dict:
    """The stored §5.6 shape -> the model's wire format (empty list/string for a missing value, [v] for one value)."""
    one = lambda v: [] if v is None else [v]  # noqa: E731
    rng = lambda v: [] if v is None else v  # noqa: E731
    return {
        "date": doc["date"], "date_evidence": doc["date_evidence"],
        "hvl": rng(doc["hvl"]), "hvl0": rng(doc["hvl0"]), "regime_line": rng(doc["regime_line"]),
        "zones": [{"id": z["id"], "side": z["side"], "zone": rng(z["zone"]),
                   "trigger": {"type": z["trigger"]["type"] or "", "note": z["trigger"]["note"] or "",
                               "es_gates": [{**g, "price": rng(g["price"])} for g in z["trigger"]["es_gates"]]},
                   "stop": one(z["stop"]),
                   "invalidation": [{**c, "price": one(c["price"]), "acceptance_minutes": one(c["acceptance_minutes"])}
                                    for c in z["invalidation"]],
                   "targets": [{"price": [] if t["low"] is None else [t["low"], t["high"]],
                                "as_written": t["as_written"]} for t in z["targets"]],
                   "source_quote": z["source_quote"]} for z in doc["zones"]],
        "scenario_sets": [{"frame": x["frame"], "label_as_written": x["label_as_written"] or "",
                           "scenarios": [{**c, "prob": one(c["prob"])} for c in x["scenarios"]]}
                          for x in doc["scenario_sets"]],
        "version_used": doc["version_used"] or "", "dropped": doc["dropped"], "uncertain": doc["uncertain"],
    }


def test_spec_example_and_fixtures_fit_the_schema():
    assert conforms(to_wire(spec_example()), GAMEPLAN_SCHEMA)
    for name in ("2026-03-11.md", "NFP Gameplan.md"):
        assert conforms(canned(name), GAMEPLAN_SCHEMA), name
    bad = to_wire(spec_example())
    bad["zones"][0]["side"] = "short"
    assert not conforms(bad, GAMEPLAN_SCHEMA)


def test_schema_has_one_union_only():
    """Structured outputs refused the v2 schema with ~15 nullable fields ('compiled grammar is too large', 2026-10-07;
    the API allows at most 16 union-type parameters): missing values are [] or '' on the wire, `date` is the only
    union."""
    unions = [n for n in _walk(GAMEPLAN_SCHEMA) if "anyOf" in n or isinstance(n.get("type"), list)]
    assert len(unions) == 1 and unions[0]["anyOf"][0]["format"] == "date"


def test_from_wire_round_trips_and_never_trims():
    doc = spec_example()
    back, flags = from_wire(to_wire(doc))
    assert flags == [] and back == doc
    w = to_wire(doc)
    w["zones"][0]["zone"] = [30640]                      # one price -> [p, p]
    w["zones"][0]["stop"] = [30734, 30740]               # two stops: left empty, flagged
    w["hvl"] = [1, 2, 3]
    back, flags = from_wire(w)
    assert back["zones"][0]["zone"] == [30640, 30640] and back["zones"][0]["stop"] is None and back["hvl"] is None
    assert [f["field"] for f in flags] == ["zones[E1].stop", "hvl"]


# --- validator ------------------------------------------------------------------------------------------------

def _fields(flags):
    return [f["field"] for f in flags]


def test_clean_gameplans_have_no_flags():
    assert validate.check(spec_example()) == []
    assert validate.check(from_wire(canned("2026-03-11.md"))[0]) == []


def _zone(**changes):
    g = spec_example()
    g["zones"][0].update(changes)
    return g


@pytest.mark.parametrize("changes, field, words", [
    ({"zone": [30722, 30640]}, "zones[E1].zone", "out of order"),
    ({"stop": 30700}, "zones[E1].stop", "not above the zone high"),
    ({"stop": None}, "zones[E1].stop", "no stop"),
    ({"targets": [tgt(30650)]}, "zones[E1].targets", "not below the zone low"),
    ({"targets": [tgt(30600, 30650)]}, "zones[E1].targets", "not below the zone low"),
    ({"targets": [tgt(30590), tgt(30615)]}, "zones[E1].targets", "not in order"),
    ({"targets": [tgt(30615), tgt(30600, 30580)]}, "zones[E1].targets", "edges out of order"),
    ({"targets": [tgt(None, words="home")]}, "zones[E1].targets", "'home' has no price"),
    ({"targets": []}, "zones[E1].targets", "no targets"),
    ({"invalidation": [inv("below", 30600)]}, "zones[E1].invalidation", "wrong side"),
    ({"invalidation": [inv("above", 30700, "settled_bar")]}, "zones[E1].invalidation", "inside the zone"),
    ({"invalidation": [inv("above", 30740, "acceptance")]}, "zones[E1].invalidation", "acceptance with no minutes"),
    ({"trigger": {"type": None, "es_gates": [gate("below", 7766), gate("above", 7770)], "note": None}},
     "zones[E1].trigger", "can't hold"),
    ({"trigger": {"type": None, "es_gates": [gate("below", 7770, 7760)], "note": None}},
     "zones[E1].trigger.es_gates", "out of order"),
])
def test_sell_zone_flags(changes, field, words):
    flags = validate.check(_zone(**changes))
    hit = [f for f in flags if f["field"] == field]
    assert hit and words in hit[0]["reason"] and hit[0]["origin"] == "validate", flags


def test_buy_zone_flags():
    g = _zone(side="buy", stop=30700, targets=[tgt(30700), tgt(30800)], invalidation=[inv("above", 30800)])
    reasons = " | ".join(f["reason"] for f in validate.check(g))
    assert "not below the zone low" in reasons
    assert "T1 '30700' is not above the zone high" in reasons
    assert "wrong side" in reasons
    ok = _zone(side="buy", stop=30630, targets=[tgt(30750), tgt(30800, 30830)],
               invalidation=[inv("below", 30640), inv("below", 30500, "acceptance", 15),
                             inv("above", 7800, "not_stated", instrument="ES")],
               trigger={"type": None, "es_gates": [gate("unclear", 7776), gate("above", 7760)], "note": None})
    assert validate.check(ok) == []


def test_range_targets_are_ordered_by_their_middle():
    """Group 4 (owner 2026-10-07): a range is one target; 08-25's floor 28,998-29,016 after 29,050 is in order."""
    g = _zone(zone=[29100, 29110], stop=29130, targets=[tgt(29050), tgt(28998, 29016)])
    assert validate.check(g) == []
    g = _zone(zone=[29300, 29330], stop=29350, targets=[tgt(29273, 29305), tgt(29128)])
    assert [f["field"] for f in validate.check(g)] == ["zones[E1].targets"]  # 29,305 is above the zone low


def test_flags_never_change_values():
    g = _zone(zone=[30722, 30640], stop=30700)
    before = copy.deepcopy(g)
    validate.check(g)
    assert g == before


def _set(frame, *probs, ids=None):
    ids = ids or [chr(65 + i) for i in range(len(probs))]
    return {"frame": frame, "label_as_written": None,
            "scenarios": [{"id": i, "prob": p, "bias": "up", "desc": ""} for i, p in zip(ids, probs)]}


def test_scenario_regime_and_id_flags():
    g = spec_example()
    g["regime_line"] = [30615, 30575]
    g["hvl0"] = [30610, 30600]
    g["zones"].append(copy.deepcopy(g["zones"][0]))
    g["scenario_sets"] = [_set("ny_am", 0.7, 0.5, 55, ids=["A", "A", "B"])]
    flags = validate.check(g)
    reasons = {f["field"]: [x["reason"] for x in flags if x["field"] == f["field"]] for f in flags}
    assert "out of order" in reasons["regime_line"][0] and "out of order" in reasons["hvl0"][0]
    assert any("'E1' appears more than once" in r for r in reasons["zones"])
    assert any("'A' appears more than once" in r for r in reasons["scenario_sets[ny_am]"])
    assert any("add up to 56.2, not 1" in r for r in reasons["scenario_sets[ny_am]"])
    assert "not a fraction" in reasons["scenario_sets[ny_am][B].prob"][0]


def test_each_scenario_set_adds_up_to_one_on_its_own():
    """Owner 2026-10-07: the NY AM and session sets are kept apart; each must sum to 1 (four v1 days summed to 2)."""
    both = {**spec_example(), "scenario_sets": [_set("ny_am", 0.5, 0.3, 0.2), _set("session", 0.4, 0.3, 0.3)]}
    assert validate.check(both) == []
    rounded = {**spec_example(), "scenario_sets": [_set("unspecified", 0.33, 0.33, 0.33)]}
    assert validate.check(rounded) == []
    fl = validate.check({**spec_example(), "scenario_sets": [_set("ny_am", 0.6, 0.5), _set("session", 0.4, 0.3)]})
    assert [(f["field"], f["reason"]) for f in fl] == [
        ("scenario_sets[ny_am]", "probabilities add up to 1.1, not 1"),
        ("scenario_sets[session]", "probabilities add up to 0.7, not 1")]
    fl = validate.check({**spec_example(), "scenario_sets": [_set("session", None, None), _set("session", 0.5, None)]})
    assert [f["reason"] for f in fl] == ["more than one session set", "no probabilities in this set",
                                         "some scenarios have no probability"]


def test_model_uncertainty_becomes_flags():
    g = spec_example()
    g["uncertain"] = [{"field": "hvl", "reason": "two HVLs given"}]
    assert validate.check(g) == [{"field": "hvl", "reason": "two HVLs given", "origin": "model", "review": True}]


@pytest.mark.parametrize("changes, words", [
    ({"stop": None}, "no stop"),
    ({"targets": []}, "no targets"),
    ({"targets": [tgt(None, words="home")]}, "has no price"),
    ({"zone": None}, "no NQ price range"),
    ({"stop": 30700}, "not above the zone high"),                       # stop inside the zone, kept as written
    ({"invalidation": [inv("above", 30700)]}, "inside the zone"),       # invalidation inside, kept as written
    ({"invalidation": [inv("above", 30740, "acceptance")]}, "acceptance with no minutes"),
])
def test_missing_or_owner_rule_flags_never_need_review(changes, words):
    """Owner 2026-10-07: a flag that only says something is missing, or follows a rule he set, never needs his
    confirmation, so a morning gameplan with no stop is still `ok` before 09:30."""
    flags = validate.check(_zone(**changes))
    hit = [f for f in flags if words in f["reason"]]
    assert hit and all(f["review"] is False for f in flags) and not validate.needs_review(flags), flags


def test_missing_probabilities_are_info_other_scenario_flags_need_review():
    empty = validate.check({**spec_example(), "scenario_sets": [_set("ny_am", 0.36, None, None)]})
    assert [f["reason"] for f in empty] == ["some scenarios have no probability"] and not validate.needs_review(empty)
    bad = validate.check({**spec_example(), "scenario_sets": [_set("ny_am", 0.6, 0.5)]})
    assert validate.needs_review(bad)


@pytest.mark.parametrize("changes", [
    {"targets": [tgt(30590), tgt(30615)]},                              # out of order
    {"targets": [tgt(30650)]},                                          # on the wrong side of the zone
    {"invalidation": [inv("below", 30600)]},                            # wrong side
    {"zone": [30722, 30640]},                                           # edges out of order
    {"trigger": {"type": None, "es_gates": [gate("below", 7766), gate("above", 7770)], "note": None}},
])
def test_other_flags_need_review(changes):
    assert validate.needs_review(validate.check(_zone(**changes)))


def test_resolve_date():
    d = date(2026, 4, 3)
    assert validate.resolve_date("2026-04-03", None, None)[:2] == (d, "content")
    assert "confirm" in validate.resolve_date("2026-04-03", None, None)[2][0]["reason"]
    assert validate.resolve_date("2026-04-03", d, None) == (d, "filename", [])
    day, source, flags = validate.resolve_date("2026-04-02", d, None)
    assert (day, source) == (d, "filename") and "content says 2026-04-02" in flags[0]["reason"]
    assert validate.resolve_date(None, None, d) == (d, "given", [])
    assert validate.resolve_date(None, None, None)[0] is None
    assert validate.resolve_date("April 3", None, None)[0] is None


# --- converter ------------------------------------------------------------------------------------------------

def tiny_png() -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = zlib.compress(b"\x00\xff\x00\x00")
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


def test_markdown_as_is():
    conv = convert(FIXTURES / "NFP Gameplan.md")
    assert conv.kind == "text" and conv.blocks == [{"type": "text", "text": conv.markdown}]
    assert "RTH v2" in conv.markdown


def test_pdf_is_sent_as_a_document(tmp_path):
    f = tmp_path / "2026-03-12.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    conv = convert(f)
    assert conv.kind == "pdf" and conv.markdown is None
    assert conv.blocks[0]["type"] == "document" and conv.blocks[0]["source"]["media_type"] == "application/pdf"
    bad = tmp_path / "x.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(CannotConvert, match="not a PDF"):
        convert(bad)


def test_docx_converted_to_markdown(tmp_path):
    import docx

    png = tmp_path / "c.png"
    png.write_bytes(tiny_png())
    d = docx.Document()
    d.add_heading("NQ Gameplan 2026-03-12", level=1)
    d.add_paragraph("HVL 24,380.")
    d.add_paragraph("watch the ON low", style="List Bullet")
    t = d.add_table(rows=2, cols=3)
    for i, v in enumerate(("Zone", "Side", "Range")):
        t.rows[0].cells[i].text = v
    for i, v in enumerate(("E1", "Short", "24,520 | 24,560")):
        t.rows[1].cells[i].text = v
    d.add_picture(str(png))
    f = tmp_path / "plan.docx"
    d.save(str(f))
    conv = convert(f)
    md = conv.markdown
    assert conv.kind == "docx"
    assert "# NQ Gameplan 2026-03-12" in md and "- watch the ON low" in md
    assert "| Zone | Side | Range |" in md and "| E1 | Short | 24,520 \\| 24,560 |" in md
    assert conv.blocks == [{"type": "text", "text": md}]  # the picture is ignored (gameplans carry no images)


@pytest.mark.parametrize("name, data, words", [
    ("old.doc", b"\xd0\xcf\x11\xe0", "not supported"),
    ("broken.docx", b"PK not a zip", "can't open"),
    ("empty.md", b"  \n", "empty"),
])
def test_unconvertible(tmp_path, name, data, words):
    f = tmp_path / name
    f.write_bytes(data)
    with pytest.raises(CannotConvert, match=words):
        convert(f)


def test_zone_without_nq_price_is_flagged():
    flags = validate.check(_zone(zone=None))
    assert [(f["field"], f["reason"]) for f in flags] == [("zones[E1].zone", "no NQ price range for this zone")]
    g = spec_example()
    g["zones"][0]["zone"] = None
    assert conforms(to_wire(g), GAMEPLAN_SCHEMA) and to_wire(g)["zones"][0]["zone"] == []

