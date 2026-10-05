"""Gameplan schema, validator and converter (step 9, SPEC §3.4, §5.6). Hand-built examples, no API."""

from __future__ import annotations

import copy
import struct
import zlib
from datetime import date

import pytest

from nqbot.gameplan import validate
from nqbot.gameplan.convert import CannotConvert, convert
from nqbot.gameplan.schema import GAMEPLAN_SCHEMA
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


def spec_example() -> dict:
    """The §5.6 example, with the audit fields and nulls the schema requires."""
    return {
        "date": "2026-09-29", "date_evidence": "2026-09-29", "hvl": 30590, "regime_line": [30575, 30615],
        "zones": [{"id": "E1", "side": "sell", "zone": [30640, 30722],
                   "trigger": {"type": "failed_audit", "es_below": 7766, "es_above": None, "note": None},
                   "stop": 30734,
                   "invalidation": {"settled_1m_above": 30722, "settled_1m_below": None, "note": None},
                   "targets": [30615, 30590], "source_quote": "E1"}],
        "scenarios": [{"id": "S-A", "prob": 0.5, "bias": "down", "desc": "lid refuses, rotation to regime line"}],
        "version_used": None, "dropped": [], "uncertain": [],
    }


# --- schema ---------------------------------------------------------------------------------------------------

def test_schema_uses_only_structured_output_keywords():
    for node in _walk(GAMEPLAN_SCHEMA):
        assert not UNSUPPORTED & set(node), node
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert node["required"] == list(node["properties"])


def test_spec_example_and_fixtures_fit_the_schema():
    assert conforms(spec_example(), GAMEPLAN_SCHEMA)
    for name in ("2026-03-11.md", "NFP Gameplan.md"):
        assert conforms(canned(name), GAMEPLAN_SCHEMA), name
    bad = spec_example()
    bad["zones"][0]["side"] = "short"
    assert not conforms(bad, GAMEPLAN_SCHEMA)


# --- validator ------------------------------------------------------------------------------------------------

def _fields(flags):
    return [f["field"] for f in flags]


def test_clean_gameplans_have_no_flags():
    assert validate.check(spec_example()) == []
    assert validate.check(canned("2026-03-11.md")) == []


def _zone(**changes):
    g = spec_example()
    g["zones"][0].update(changes)
    return g


@pytest.mark.parametrize("changes, field, words", [
    ({"zone": [30722, 30640]}, "zones[E1].zone", "out of order"),
    ({"stop": 30700}, "zones[E1].stop", "not above the zone high"),
    ({"stop": None}, "zones[E1].stop", "no stop"),
    ({"targets": [30650]}, "zones[E1].targets", "not below the zone low"),
    ({"targets": [30590, 30615]}, "zones[E1].targets", "not in order"),
    ({"targets": []}, "zones[E1].targets", "no targets"),
    ({"invalidation": {"settled_1m_above": None, "settled_1m_below": 30600, "note": None}},
     "zones[E1].invalidation", "wrong side"),
    ({"invalidation": {"settled_1m_above": 30700, "settled_1m_below": None, "note": None}},
     "zones[E1].invalidation", "inside the zone"),
    ({"trigger": {"type": None, "es_below": 7766, "es_above": 7770, "note": None}}, "zones[E1].trigger", "can't hold"),
])
def test_sell_zone_flags(changes, field, words):
    flags = validate.check(_zone(**changes))
    hit = [f for f in flags if f["field"] == field]
    assert hit and words in hit[0]["reason"] and hit[0]["origin"] == "validate", flags


def test_buy_zone_flags():
    g = _zone(side="buy", stop=30700, targets=[30700, 30800],
              invalidation={"settled_1m_above": 30800, "settled_1m_below": None, "note": None})
    reasons = " | ".join(f["reason"] for f in validate.check(g))
    assert "not below the zone low" in reasons
    assert "T1 30700 is not above the zone high" in reasons
    assert "wrong side" in reasons
    ok = _zone(side="buy", stop=30630, targets=[30750, 30800],
               invalidation={"settled_1m_above": None, "settled_1m_below": 30640, "note": None})
    assert validate.check(ok) == []


def test_flags_never_change_values():
    g = _zone(zone=[30722, 30640], stop=30700)
    before = copy.deepcopy(g)
    validate.check(g)
    assert g == before


def test_scenario_regime_and_id_flags():
    g = spec_example()
    g["regime_line"] = [30615, 30575]
    g["zones"].append(copy.deepcopy(g["zones"][0]))
    g["scenarios"] = [{"id": "A", "prob": 0.7, "bias": "up", "desc": ""},
                      {"id": "A", "prob": 0.5, "bias": "down", "desc": ""},
                      {"id": "B", "prob": 55, "bias": "down", "desc": ""}]
    flags = validate.check(g)
    reasons = {f["field"]: [x["reason"] for x in flags if x["field"] == f["field"]] for f in flags}
    assert "out of order" in reasons["regime_line"][0]
    assert any("'E1' appears more than once" in r for r in reasons["zones"])
    assert any("'A' appears more than once" in r for r in reasons["scenarios"])
    assert any("add up to 56.2" in r for r in reasons["scenarios"])
    assert "not a fraction" in reasons["scenarios[B].prob"][0]
    fl = validate.check({**spec_example(), "scenarios": [{"id": "A", "prob": 0.6, "bias": "up", "desc": ""},
                                                          {"id": "B", "prob": 0.5, "bias": "down", "desc": ""}]})
    assert any("add up to 1.1" in f["reason"] for f in fl)
    rounded = [{"id": i, "prob": 1 / 3, "bias": "up", "desc": ""} for i in "ABC"]
    assert validate.check({**spec_example(), "scenarios": rounded}) == []


def test_model_uncertainty_becomes_flags():
    g = spec_example()
    g["uncertain"] = [{"field": "hvl", "reason": "two HVLs given"}]
    assert validate.check(g) == [{"field": "hvl", "reason": "two HVLs given", "origin": "model"}]


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
    assert conforms(g, GAMEPLAN_SCHEMA)

