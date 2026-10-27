"""config/gate_record.json (owner 2026-10-26, SPEC §13 Q-perfpage): it parses, and its latest gate is the one the
CLAUDE.md Status line names, so the two never disagree."""

import json
import re
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STATUS = re.compile(r"^- Last gate passed: ✅ step (\S+) \((\d{4}-\d{2}-\d{2})\)", re.MULTILINE)


def test_gate_record_parses_and_matches_the_claude_md_status_line():
    gates = json.loads((REPO / "config" / "gate_record.json").read_text(encoding="utf-8"))["gates"]
    assert gates, "no gate recorded"
    for g in gates:
        assert set(g) >= {"gate", "passed", "commit"}
        date.fromisoformat(g["passed"])
        assert re.fullmatch(r"[0-9a-f]{40}", g["commit"]), g
    assert [g["passed"] for g in gates] == sorted(g["passed"] for g in gates)  # appended in order
    assert len({g["gate"] for g in gates}) == len(gates)  # one entry per gate
    m = STATUS.search((REPO / "CLAUDE.md").read_text(encoding="utf-8"))
    assert m, "CLAUDE.md has no 'Last gate passed' Status line"
    assert (gates[-1]["gate"], gates[-1]["passed"]) == (m.group(1), m.group(2))
