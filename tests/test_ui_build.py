"""`nqbot ui build` (SPEC §9b): npm ci only, npm audit fails the build on high or critical, the sources' digest is
recorded beside the build and is what `nqbot ui` checks (npm is faked: tests never run it)."""

from __future__ import annotations

import json
import subprocess

from nqbot.ui import build as ui_build
from nqbot.ui import spa


def audit(**counts):
    vulns = {"info": 0, "low": 0, "moderate": 0, "high": 0, "critical": 0, **counts}
    adv = {f"pkg{i}": {"severity": sev, "range": "<1.0.0", "via": [{"title": f"{sev} thing"}]}
           for i, (sev, n) in enumerate(counts.items()) if n}
    return json.dumps({"metadata": {"vulnerabilities": vulns}, "vulnerabilities": adv})


def web(tmp_path):
    w = tmp_path / "web"
    (w / "src").mkdir(parents=True)
    (w / "src" / "main.tsx").write_text("export {};\n", encoding="utf-8")
    (w / "package-lock.json").write_text("{}", encoding="utf-8")
    return w


class FakeNpm:
    def __init__(self, w, audit_json, fail=None):
        self.w, self.audit_json, self.fail, self.calls = w, audit_json, fail, []

    def __call__(self, cmd, cwd):
        step = cmd[1] if cmd[1] != "run" else "build"
        self.calls.append(step)
        if step == "build":
            (self.w / "dist").mkdir(exist_ok=True)
            (self.w / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
        out = self.audit_json if step == "audit" else ""
        return subprocess.CompletedProcess(cmd, 1 if step == self.fail else 0, stdout=out, stderr=f"{step} broke")


def info(w):
    return json.loads((w / "dist" / spa.BUILD_INFO).read_text(encoding="utf-8"))


def test_clean_build_records_the_digest(tmp_path):
    w = web(tmp_path)
    npm = FakeNpm(w, audit(low=2, moderate=1))
    assert ui_build.build(w, runner=npm, npm="npm", out=lambda s: None) == 0
    assert npm.calls == ["ci", "audit", "build"]  # never `npm install`
    i = info(w)
    assert i["ok"] is True and i["digest"] == spa.source_digest(w) and i["audit"]["low"] == 2
    assert spa.spa_state(w).ok


def test_high_or_critical_advisory_stops_the_build(tmp_path):
    for sev in ("high", "critical"):
        w = web(tmp_path / sev)
        (w / "dist").mkdir()
        npm = FakeNpm(w, audit(**{sev: 1, "low": 3}))
        lines: list[str] = []
        assert ui_build.build(w, runner=npm, npm="npm", out=lines.append) == 1
        assert "build" not in npm.calls
        assert info(w)["ok"] is False
        assert any(line.strip().startswith(f"{sev}: pkg") for line in lines)  # every advisory listed
        assert not spa.spa_state(w).ok


def test_failures_and_missing_tools_refuse(tmp_path, monkeypatch):
    w = web(tmp_path)
    assert ui_build.build(w, runner=FakeNpm(w, audit(), fail="ci"), npm="npm", out=lambda s: None) == 1
    assert ui_build.build(w, runner=FakeNpm(w, "{\"error\": {\"summary\": \"offline\"}}"), npm="npm", out=lambda s: None) == 1
    monkeypatch.setattr(ui_build.shutil, "which", lambda name: None)
    assert ui_build.build(w, runner=FakeNpm(w, audit()), npm=None, out=lambda s: None) == 2  # npm not on PATH
    (w / "package-lock.json").unlink()
    assert ui_build.build(w, runner=FakeNpm(w, audit()), npm="npm", out=lambda s: None) == 2


def test_digest_follows_the_sources_not_the_line_endings(tmp_path):
    w = web(tmp_path)
    d0 = spa.source_digest(w)
    (w / "src" / "main.tsx").write_bytes(b"export {};\r\n")
    assert spa.source_digest(w) == d0
    (w / "src" / "extra.ts").write_text("x", encoding="utf-8")
    assert spa.source_digest(w) != d0
    (w / "node_modules").mkdir()
    d1 = spa.source_digest(w)
    (w / "node_modules" / "junk.js").write_text("x", encoding="utf-8")
    assert spa.source_digest(w) == d1  # only the listed sources count


def test_audit_summary_reads_npm_json():
    counts, lines = ui_build.audit_summary(audit(moderate=1))
    assert counts["moderate"] == 1 and counts["high"] == 0
    assert lines == ["moderate: pkg0 <1.0.0 moderate thing"]
