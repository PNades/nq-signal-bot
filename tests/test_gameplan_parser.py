"""Gameplan parser, batch, report and CLI with a fake Claude client (step 9, SPEC §3.4, §5.6). No network."""

from __future__ import annotations

import json
import re
import shutil
from datetime import date

import anthropic
import httpx2
import pytest

from nqbot import cli, storage
from nqbot.gameplan import parser as gp
from nqbot.gameplan.schema import GAMEPLAN_SCHEMA, from_wire
from nqbot.storage import read_jsonl
from tests.anthropic_fakes import FIXTURES, FakeAnthropic, canned, message

CANON = "2026-03-11.md"
NFP = "NFP Gameplan.md"
D_CANON = date(2026, 3, 11)
D_NFP = date(2026, 4, 3)


@pytest.fixture
def store(tmp_path):
    return gp.GameplanStore(tmp_path / "raw" / "gameplans", tmp_path / "processed" / "gameplans",
                            tmp_path / "reports" / "gameplans")


def put(store, *names):
    store.raw_dir.mkdir(parents=True, exist_ok=True)
    for n in names:
        shutil.copy(FIXTURES / n, store.raw_dir / n)
    return [store.raw_dir / n for n in names]


def quiet(_):
    pass


def connection_error():
    return anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))


def test_parse_canonical_file(settings, store):
    (f,) = put(store, CANON)
    client = FakeAnthropic()
    r = gp.parse_file(client, settings, store, f, out=quiet)
    assert (r.status, r.date, r.date_source, r.flags) == ("ok", D_CANON, "filename", [])

    req = client.requests[0]
    assert req["model"] == settings["gameplan_model"] == "claude-opus-5-5"
    assert req["max_tokens"] == settings["gameplan_max_tokens"]
    assert req["output_config"] == {"effort": settings["gameplan_effort"],
                                    "format": {"type": "json_schema", "schema": GAMEPLAN_SCHEMA}}
    assert "fallbacks" not in req and "betas" not in req  # a fallback model would break the cost bound
    assert client.options == [{"max_retries": 0}]          # never auto-retried
    assert req["system"] == gp.SYSTEM_PROMPT
    assert "E1 | Short" in req["messages"][0]["content"][0]["text"]
    assert len(client.counts) == 1  # the estimate is a free token count

    doc = store.load(D_CANON)
    want = from_wire(canned(CANON))[0]
    keys = ("date", "hvl", "hvl0", "regime_line", "zones", "scenario_sets")
    assert {k: doc[k] for k in keys} == {k: want[k] for k in keys}
    assert doc["status"] == "ok" and doc["flags"] == []
    assert doc["parse"]["dropped"] == ["CPI blackout note 08:00 – 09:30"]
    src = doc["source"]
    assert src["file"] == CANON and src["sha256"] == gp.sha256(f) and src["request_id"] == "req_fake"
    assert src["canonical_copy"] == CANON and src["parser_version"] == gp.PARSER_VERSION
    assert src["cost_usd"] == pytest.approx((3000 * 4 + 2000 * 20) / 1e6) == r.cost_usd
    calls = read_jsonl(store.calls_path)
    assert len(calls) == 1 and calls[0]["cost_usd"] == src["cost_usd"]
    assert store.load_response(gp.sha256(f))["text"]
    assert sorted(p.name for p in store.raw_dir.iterdir()) == [CANON]  # already canonical: no copy


def test_archived_name_dated_from_content_and_flagged(settings, store):
    (f,) = put(store, NFP)
    r = gp.parse_file(FakeAnthropic(), settings, store, f, out=quiet)
    assert (r.status, r.date, r.date_source) == ("flagged", D_NFP, "content")
    fields = [(x["field"], x["origin"]) for x in r.flags]
    assert ("date", "validate") in fields                      # confirm the file -> date (§13 step 9)
    assert ("zones[E4].stop", "validate") in fields and ("zones[E4].targets", "validate") in fields
    assert ("zones[E4].zone", "model") in fields
    copy = store.raw_dir / "2026-04-03.md"
    assert copy.read_bytes() == (FIXTURES / NFP).read_bytes()
    index = store.load_index()
    assert {f for e in index.values() for f in e["files"]} == {NFP, "2026-04-03.md"}
    doc = store.load(D_NFP)
    assert doc["parse"]["version_used"] == "RTH v2 (09:10)" and "RTH v1 (07:45)" in doc["parse"]["dropped"]
    assert doc["source"]["canonical_copy"] == "2026-04-03.md"
    # Parser v2 (owner 2026-10-07): both probability sets kept and labelled, a range target stays one target.
    assert [s["frame"] for s in doc["scenario_sets"]] == ["ny_am", "session"]
    e2 = next(z for z in doc["zones"] if z["id"] == "E2")
    assert e2["targets"][1] == {"low": 25140, "high": 25160, "as_written": "T2 25,140 – 25,160"}
    assert not [x for x in r.flags if x["field"].startswith(("zones[E2]", "scenario_sets"))]
    # Parser v3: the breakout is its own entry (zone empty, trigger level kept), VIX and size kept word for word,
    # the model's 'no invalidation' note is info.
    acc = next(z for z in doc["zones"] if z["id"] == "E1 accepted")
    assert acc["zone"] is None and acc["trigger"]["nq_price"] == [25285, 25285] and acc["stop"] == 25250
    assert acc["other_conditions"] == ["Only with the VIX under 16."]
    assert doc["other_conditions"] == ["Size: half until 10:00."]
    note = next(x for x in r.flags if x["field"] == "zones[E1 accepted].invalidation")
    assert note["origin"] == "model" and note["review"] is False


def test_date_prefix_in_the_file_name(settings, store, tmp_path):
    f = tmp_path / "2026-03-11 gameplan.md"
    shutil.copy(FIXTURES / CANON, f)
    r = gp.parse_file(FakeAnthropic(replies={f.name: message(canned(CANON))}), settings, store, f, out=quiet)
    assert (r.status, r.date, r.date_source) == ("ok", D_CANON, "filename")
    assert (store.raw_dir / CANON).exists()


def test_never_sent_twice_unless_reparse(settings, store):
    (f,) = put(store, CANON)
    client = FakeAnthropic()
    gp.parse_file(client, settings, store, f, out=quiet)
    r = gp.parse_file(client, settings, store, f, out=quiet)
    assert r.status == "cached" and len(client.requests) == 1
    r = gp.parse_file(client, settings, store, f, reparse=True, out=quiet)
    assert r.status == "ok" and len(client.requests) == 2
    assert len(read_jsonl(store.calls_path)) == 2


@pytest.mark.parametrize("reply, words", [
    (message(None, stop_reason="refusal"), "refused"),
    (message(canned(CANON), stop_reason="max_tokens"), "cut off"),
    (message("{not json"), "not valid JSON"),
    (connection_error(), "APIConnectionError"),
])
def test_failed_calls_write_nothing(settings, store, reply, words):
    (f,) = put(store, CANON)
    r = gp.parse_file(FakeAnthropic(replies={CANON: reply}), settings, store, f, out=quiet)
    assert r.status == "failed" and words in r.message
    assert store.load(D_CANON) is None and store.load_index() == {} and store.load_response(gp.sha256(f)) is None
    (call,) = read_jsonl(store.calls_path)
    assert words in call["error"]


def test_undated_file_keeps_its_answer_then_dated_for_free(settings, store, tmp_path):
    f = tmp_path / "Tuesday plan.md"
    f.write_text("E1 sell 100 - 110", encoding="utf-8")
    client = FakeAnthropic(queue=[message({**canned(CANON), "date": None})])
    r = gp.parse_file(client, settings, store, f, out=quiet)
    assert r.status == "failed" and "--date" in r.message and r.cost_usd > 0
    r = gp.parse_file(client, settings, store, f, given_date=date(2026, 3, 10), out=quiet)
    assert (r.status, r.date, r.date_source, r.cost_usd) == ("ok", date(2026, 3, 10), "given", 0.0)
    assert len(client.requests) == 1  # the saved answer was reused
    assert (store.raw_dir / "2026-03-10.md").exists()


def test_redating_moves_the_gameplan_and_is_remembered(settings, store):
    (f,) = put(store, NFP)
    client = FakeAnthropic()
    gp.parse_file(client, settings, store, f, out=quiet)
    r = gp.parse_file(client, settings, store, f, given_date=date(2026, 4, 2), out=quiet)
    assert (r.status, r.date, r.date_source) == ("flagged", date(2026, 4, 2), "given")
    assert any("content says 2026-04-03" in x["reason"] for x in r.flags)
    assert len(client.requests) == 1                                   # no new call
    assert store.parsed_days() == [date(2026, 4, 2)]                   # old JSON removed
    assert not (store.raw_dir / "2026-04-03.md").exists() and (store.raw_dir / "2026-04-02.md").exists()
    assert {e["date"] for e in store.load_index().values()} == {"2026-04-02"}
    # A later --reparse (or batch) keeps the owner's correction.
    r = gp.parse_file(client, settings, store, f, reparse=True, out=quiet)
    assert (r.date, r.date_source) == (date(2026, 4, 2), "given") and len(client.requests) == 2


def test_bom_crlf_text_reparse_is_not_a_conflict(settings, store):
    store.raw_dir.mkdir(parents=True)
    f = store.raw_dir / NFP
    f.write_bytes(b"\xef\xbb\xbf" + (FIXTURES / NFP).read_bytes().replace(b"\n", b"\r\n"))
    client = FakeAnthropic()
    assert gp.parse_file(client, settings, store, f, out=quiet).status == "flagged"
    r = gp.parse_file(client, settings, store, f, reparse=True, out=quiet)
    assert r.status == "flagged" and len(client.requests) == 2


def test_date_conflicts(settings, store, tmp_path):
    (f,) = put(store, CANON)
    client = FakeAnthropic()
    gp.parse_file(client, settings, store, f, out=quiet)

    other = tmp_path / "elsewhere" / CANON  # a different file for the same day, dated name
    other.parent.mkdir()
    other.write_text("E9 buy 1-2", encoding="utf-8")
    with pytest.raises(gp.GameplanRefused, match="--replace"):
        gp.parse_file(client, settings, store, other, out=quiet)
    assert len(client.requests) == 1  # refused before any call

    late = tmp_path / "late upload.md"  # date only known after the call
    late.write_text("E9 buy 1-2", encoding="utf-8")
    late_client = FakeAnthropic(queue=[message(canned(CANON))])
    r = gp.parse_file(late_client, settings, store, late, out=quiet)
    assert r.status == "conflict" and CANON in r.message
    assert store.load(D_CANON)["source"]["file"] == CANON
    r = gp.parse_file(late_client, settings, store, late, out=quiet)  # retried: the answer is reused
    assert r.status == "conflict" and len(late_client.requests) == 1


def test_replace_moves_the_old_copy_aside(settings, store, tmp_path):
    (f,) = put(store, CANON)
    gp.parse_file(FakeAnthropic(), settings, store, f, out=quiet)
    fixed = tmp_path / "fixed.md"
    fixed.write_text("corrected plan", encoding="utf-8")
    r = gp.parse_file(FakeAnthropic(queue=[message(canned(CANON))]), settings, store, fixed,
                      given_date=D_CANON, replace=True, out=quiet)
    assert r.status == "ok"
    assert store.load(D_CANON)["source"]["file"] == "fixed.md"
    assert (store.raw_dir / CANON).read_text(encoding="utf-8") == "corrected plan"
    (old,) = (store.raw_dir / "replaced").iterdir()
    assert old.read_bytes() == (FIXTURES / CANON).read_bytes()
    index = store.load_index()
    assert [e["files"] for e in index.values()] == [["2026-03-11.md", "fixed.md"]]


def test_single_file_cost_cap(settings, store):
    (f,) = put(store, CANON)
    client = FakeAnthropic(count_tokens=200_000)  # e.g. a very long PDF
    with pytest.raises(gp.GameplanRefused, match="--approve-usd"):
        gp.parse_file(client, settings, store, f, cap_usd=settings["gameplan_max_usd_per_file"], out=quiet)
    assert client.requests == []
    assert gp.parse_file(client, settings, store, f, cap_usd=2.0, out=quiet).status == "ok"


def test_batch_needs_approval_then_parses_and_reports(settings, store):
    put(store, CANON, NFP)
    (store.raw_dir / "old.doc").write_bytes(b"\xd0\xcf")
    client = FakeAnthropic()
    lines = []
    with pytest.raises(gp.GameplanRefused, match="--approve-usd"):
        gp.batch(client, settings, store, approved_usd=None, out=lines.append)
    worst = gp.cents_up(2 * (3000 * 4 + settings["gameplan_max_tokens"] * 20) / 1e6)  # printed rounded up
    assert lines[0].startswith(f"reading {store.raw_dir.resolve()}: 3 file(s)")
    assert f"worst case ${worst:.2f}" in lines[1]
    with pytest.raises(gp.GameplanRefused):
        gp.batch(client, settings, store, approved_usd=worst - 0.01, out=quiet)
    assert client.requests == []

    results, report = gp.batch(client, settings, store, approved_usd=worst, out=quiet)
    assert {r.file: r.status for r in results} == {"old.doc": "unconvertible", CANON: "ok", NFP: "flagged"}
    text = report.read_text(encoding="utf-8")
    assert "| 2026-03 | 1 | ledger not written yet (step 4b) |" in text
    assert "| 2026-04 | 1 | ledger not written yet (step 4b) |" in text
    assert "| total | 2 | |" in text
    assert f"| {NFP} | 2026-04-03.md | 2026-04-03 | content |" in text
    assert "old.doc: unconvertible" in text and "`zones[E4].zone` (model)" in text
    assert "info: 1 no stop in the gameplan, 1 no targets in the gameplan" in text  # info flags only counted
    assert "`zones[E4].stop`" not in text

    # Resumable: the canonical copy written by the first run and both originals are skipped.
    results, _ = gp.batch(client, settings, store, approved_usd=None, out=quiet)
    assert [r.status for r in results] == ["unconvertible"] and len(client.requests) == 2

    # --reparse sends each distinct gameplan once (not its canonical copy again).
    results, _ = gp.batch(client, settings, store, approved_usd=worst, reparse=True, out=quiet)
    assert len(client.requests) == 4


def test_batch_reparse_skips_our_docx_copies(settings, store):
    import docx

    store.raw_dir.mkdir(parents=True)
    d = docx.Document()
    d.add_paragraph("NQ gameplan 2026-03-11: E1 sell 24,520 - 24,560")
    d.save(str(store.raw_dir / "plan.docx"))
    client = FakeAnthropic(replies={"plan.docx": message(canned(CANON))})
    gp.batch(client, settings, store, approved_usd=1, out=quiet)
    assert (store.raw_dir / CANON).exists()
    gp.batch(client, settings, store, approved_usd=1, reparse=True, out=quiet)
    assert [_name(r) for r in client.requests] == ["plan.docx", "plan.docx"]


def _name(req):
    return req["messages"][0]["content"][-1]["text"].split("\n")[0].removeprefix("File name: ")


def test_batch_keeps_going_after_api_errors(settings, store):
    put(store, CANON, NFP)
    client = FakeAnthropic(replies={CANON: connection_error()})
    results, report = gp.batch(client, settings, store, approved_usd=1, out=quiet)
    assert {r.file: r.status for r in results} == {CANON: "failed", NFP: "flagged"}
    assert "APIConnectionError" in report.read_text(encoding="utf-8")
    assert [c["file"] for c in read_jsonl(store.calls_path) if "APIConnectionError" in c.get("error", "")] == [CANON]

    counting_fails = FakeAnthropic(count_error=connection_error())
    results, _ = gp.batch(counting_fails, settings, store, approved_usd=1, out=quiet)
    assert [(r.file, r.status) for r in results] == [(CANON, "failed")] and counting_fails.requests == []


def test_batch_never_pays_twice_for_an_undated_file(settings, store):
    store.raw_dir.mkdir(parents=True)
    (store.raw_dir / "Tuesday plan.md").write_text("E1 sell 100 - 110", encoding="utf-8")
    client = FakeAnthropic(queue=[message({**canned(CANON), "date": None})])
    results, _ = gp.batch(client, settings, store, approved_usd=1, out=quiet)
    assert results[0].status == "failed"
    results, _ = gp.batch(client, settings, store, approved_usd=None, out=quiet)  # no approval needed: no call
    assert results[0].status == "failed" and len(client.requests) == 1


def test_batch_stops_when_ssd_unplugged(settings, store):
    put(store, CANON)
    with pytest.raises(gp.GameplanRefused, match="no longer mounted"):
        gp.batch(FakeAnthropic(), settings, store, approved_usd=100, still_mounted=lambda: False, out=quiet)


def test_report_marks_held_back_ledger_months(settings, store):
    put(store, CANON, NFP)
    # Months 1-12 = 2025-07 .. 2026-06: 2026-03 is month 9 (held back, §5.6), 2026-04 is month 10 (sealed).
    yms = [f"2025-{m:02d}" for m in range(7, 13)] + [f"2026-{m:02d}" for m in range(1, 7)]
    settings["months"] = {i + 1: ym for i, ym in enumerate(yms)}
    _, report = gp.batch(FakeAnthropic(), settings, store, approved_usd=100, out=quiet)
    text = report.read_text(encoding="utf-8")
    assert "| 2026-03 | 1 | ledger month 9: held back until step 20 (§5.6) |" in text
    assert "| 2026-04 | 1 | ledger month 10 |" in text


# --- CLI ------------------------------------------------------------------------------------------------------

@pytest.fixture
def cli_env(settings, drive, monkeypatch):
    client = FakeAnthropic()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(gp, "make_client", lambda s: client)
    return client, storage.resolve_paths(settings)


def test_cli_gameplan(cli_env, capsys):
    client, paths = cli_env
    assert cli.main(["gameplan", str(FIXTURES / NFP)]) == 0
    out = capsys.readouterr().out
    assert "worst case" in out and "flagged 2026-04-03" in out and "review date" in out and "info zones[E4].stop" in out
    doc = json.loads((paths.data_dir / "processed" / "gameplans" / "2026-04-03.json").read_text(encoding="utf-8"))
    assert doc["status"] == "flagged"
    assert (paths.raw_dir / "gameplans" / "2026-04-03.md").exists()
    assert cli.main(["gameplan", str(FIXTURES / "missing.md")]) == cli.EXIT_REFUSED


def test_cli_gameplan_cap(cli_env, capsys):
    client, _ = cli_env
    client.count_tokens = 200_000
    assert cli.main(["gameplan", str(FIXTURES / CANON)]) == cli.EXIT_REFUSED
    assert "--approve-usd" in capsys.readouterr().err and client.requests == []
    assert cli.main(["gameplan", str(FIXTURES / CANON), "--approve-usd", "2"]) == 0


def test_cli_gameplan_batch(cli_env, capsys):
    client, paths = cli_env
    raw = paths.raw_dir / "gameplans"
    raw.mkdir(parents=True)
    shutil.copy(FIXTURES / CANON, raw / CANON)
    assert cli.main(["gameplan-batch"]) == cli.EXIT_REFUSED
    assert "--approve-usd" in capsys.readouterr().err and client.requests == []
    assert cli.main(["gameplan-batch", "--approve-usd", "1"]) == 0
    out = capsys.readouterr().out
    assert "| 2026-03 | 1 |" in out and "spent $0.05" in out
    assert (paths.reports_dir / "gameplans" / "step9_report.md").exists()


def test_cli_needs_the_api_key(settings, drive, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("nqbot.secrets.REPO_ROOT", tmp_path)  # no .env
    assert cli.main(["gameplan", str(FIXTURES / CANON)]) == cli.EXIT_REFUSED
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_cli_gameplan_ignores_the_job_lock(cli_env, capsys):
    from nqbot.jobs.lock import ALL_LOCKS, job_locks

    _, paths = cli_env
    paths.root.mkdir(parents=True)
    with job_locks(paths.root, ALL_LOCKS, "databento_backfill"):  # overnight pulls still running
        assert cli.main(["gameplan", str(FIXTURES / CANON)]) == 0


def test_cli_gameplan_lock_and_unlock(cli_env, capsys):
    _, paths = cli_env
    store = gp.GameplanStore.from_paths(paths)
    store.processed_dir.mkdir(parents=True)
    other = {"host": "home-pc", "pid": 1, "job": "gameplan-batch", "started_utc": "2026-10-05T12:00:00+00:00"}
    store.lock_path.write_text(json.dumps(other), encoding="utf-8")  # left by another machine
    assert cli.main(["gameplan", str(FIXTURES / CANON)]) == cli.EXIT_LOCKED
    assert "nqbot unlock --gameplan" in capsys.readouterr().err
    assert cli.main(["unlock", "--gameplan"]) == cli.EXIT_LOCKED and store.lock_path.exists()
    assert "home-pc" in capsys.readouterr().out
    assert cli.main(["unlock", "--gameplan", "--yes"]) == 0 and not store.lock_path.exists()
    assert cli.main(["gameplan", str(FIXTURES / CANON)]) == 0


# --- regression tests from the second review --------------------------------------------------------------------

def test_already_parsed_is_never_paid_again_after_a_version_bump(settings, store, monkeypatch):
    (f,) = put(store, NFP)
    client = FakeAnthropic()
    gp.parse_file(client, settings, store, f, out=quiet)
    monkeypatch.setattr(gp, "PARSER_VERSION", gp.PARSER_VERSION + 1)  # the saved answer is now another version
    r = gp.parse_file(client, settings, store, f, out=quiet)
    assert (r.status, r.cost_usd) == ("cached", 0.0) and len(client.requests) == 1
    with pytest.raises(gp.GameplanRefused, match="--reparse"):  # re-dating would need a new paid call
        gp.parse_file(client, settings, store, f, given_date=date(2026, 4, 2), out=quiet)
    assert len(client.requests) == 1


def test_stale_version_is_rebuilt_from_a_saved_new_answer_for_free(settings, store, monkeypatch):
    """2026-10-07: a v2 --reparse left an undated answer saved while the v1 gameplan stayed under the date; the
    owner's --date (same date) must rebuild from the saved v2 answer, and a later batch too, never a new call."""
    (f,) = put(store, NFP)
    client = FakeAnthropic()
    gp.parse_file(client, settings, store, f, out=quiet)
    monkeypatch.setattr(gp, "PARSER_VERSION", gp.PARSER_VERSION + 1)
    saved = json.loads((store.processed_dir / "responses" / f"{gp.sha256(f)}.v{gp.PARSER_VERSION - 1}.json")
                       .read_text(encoding="utf-8"))
    (store.processed_dir / "responses" / f"{gp.sha256(f)}.v{gp.PARSER_VERSION}.json").write_text(
        json.dumps(saved), encoding="utf-8")
    r = gp.parse_file(client, settings, store, f, given_date=D_NFP, out=quiet)
    assert (r.status, r.cost_usd, len(client.requests)) == ("flagged", 0.0, 1)
    assert store.load(D_NFP)["source"]["parser_version"] == gp.PARSER_VERSION
    monkeypatch.setattr(gp, "PARSER_VERSION", gp.PARSER_VERSION + 1)  # an older doc again, no saved answer: skipped
    results, _ = gp.batch(client, settings, store, approved_usd=None, out=quiet)
    assert results == [] and len(client.requests) == 1


def test_owner_confirms_a_content_date_with_date_for_free(settings, store):
    """--date with the date already read from the content records the owner's confirmation (date flag cleared)."""
    (f,) = put(store, NFP)
    client = FakeAnthropic()
    r = gp.parse_file(client, settings, store, f, out=quiet)
    assert r.date_source == "content" and any(x["field"] == "date" for x in r.flags)
    r = gp.parse_file(client, settings, store, f, given_date=D_NFP, out=quiet)
    assert (r.status, r.date_source, r.cost_usd, len(client.requests)) == ("flagged", "given", 0.0, 1)
    assert not any(x["field"] == "date" for x in r.flags)
    r = gp.parse_file(client, settings, store, f, given_date=D_NFP, out=quiet)
    assert r.status == "cached" and len(client.requests) == 1


def test_replace_moves_the_old_original_aside_and_batch_stays_clean(settings, store, tmp_path):
    (f,) = put(store, NFP)
    gp.parse_file(FakeAnthropic(), settings, store, f, out=quiet)
    fixed = tmp_path / "fixed.md"
    fixed.write_text("corrected plan", encoding="utf-8")
    r = gp.parse_file(FakeAnthropic(queue=[message(canned(NFP))]), settings, store, fixed,
                      given_date=D_NFP, replace=True, out=quiet)
    assert r.status == "flagged" and store.load(D_NFP)["source"]["file"] == "fixed.md"
    assert sorted(p.name for p in store.raw_dir.iterdir() if p.is_file()) == ["2026-04-03.md"]
    assert len(list((store.raw_dir / "replaced").iterdir())) == 2  # NFP Gameplan.md and its old copy
    results, report = gp.batch(FakeAnthropic(), settings, store, approved_usd=None, out=quiet)
    assert results == [] and "- none" in report.read_text(encoding="utf-8").split("## Not parsed")[1]


def test_replace_across_file_kinds(settings, store, tmp_path):
    store.raw_dir.mkdir(parents=True)
    pdf = store.raw_dir / "2026-03-11.pdf"
    pdf.write_bytes(b"%PDF-1.4 old plan")
    client = FakeAnthropic(replies={pdf.name: message(canned(CANON))})
    gp.parse_file(client, settings, store, pdf, out=quiet)
    md = tmp_path / "2026-03-11.md"
    md.write_text("new plan", encoding="utf-8")
    r = gp.parse_file(FakeAnthropic(replies={md.name: message(canned(CANON))}), settings, store, md,
                      replace=True, out=quiet)
    assert r.status == "ok" and not pdf.exists()
    assert [p.suffix for p in (store.raw_dir / "replaced").iterdir()] == [".pdf"]
    results, _ = gp.batch(FakeAnthropic(), settings, store, approved_usd=None, out=quiet)
    assert results == []


def test_a_saved_copy_is_never_parsed_on_its_own(settings, store):
    import docx

    store.raw_dir.mkdir(parents=True)
    d = docx.Document()
    d.add_paragraph("NQ gameplan: E1 sell 24,520 - 24,560")
    d.save(str(store.raw_dir / "plan.docx"))
    client = FakeAnthropic(replies={"plan.docx": message(canned(CANON))})
    gp.parse_file(client, settings, store, store.raw_dir / "plan.docx", out=quiet)
    for replace in (False, True):
        with pytest.raises(gp.GameplanRefused, match="copy saved from plan.docx"):
            gp.parse_file(client, settings, store, store.raw_dir / CANON, replace=replace, out=quiet)
    assert len(client.requests) == 1


def test_sdk_value_error_fails_one_file_only(settings, store):
    put(store, CANON, NFP)
    client = FakeAnthropic(replies={CANON: ValueError("Streaming is strongly recommended")})
    results, _ = gp.batch(client, settings, store, approved_usd=1, out=quiet)
    assert {r.file: r.status for r in results} == {CANON: "failed", NFP: "flagged"}
    assert "not sent" in [c for c in read_jsonl(store.calls_path) if c["file"] == CANON][0]["error"]


def test_real_client_gets_a_verifying_tls_context(monkeypatch):
    """Regression: with pip-system-certs (a databento dependency on Windows) the SDK's default TLS setup recursed."""
    import ssl

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    client = gp.make_client({})
    ctx = client._client._transport._pool._ssl_context
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


def test_batch_reads_one_folder_down_but_never_replaced(settings, store):
    """presd_fixes D9: a Drive zip extracts into a subfolder; `replaced/` (old --replace files) is never parsed."""
    sub = store.raw_dir / "Jeron gameplans"
    sub.mkdir(parents=True)
    shutil.copy(FIXTURES / NFP, sub / NFP)
    (store.raw_dir / "replaced").mkdir()
    shutil.copy(FIXTURES / CANON, store.raw_dir / "replaced" / CANON)
    (sub / "deeper").mkdir()
    shutil.copy(FIXTURES / CANON, sub / "deeper" / CANON)
    assert gp.gameplan_files(store) == [sub / NFP]
    lines = []
    results, _ = gp.batch(FakeAnthropic(), settings, store, approved_usd=1.0, out=lines.append)
    assert lines[0] == f"reading {store.raw_dir.resolve()}: 1 file(s) (1 in subfolders)"
    assert [(r.file, r.status) for r in results] == [(NFP, "flagged")]


def test_batch_says_where_it_looked_when_empty(settings, store):
    store.raw_dir.mkdir(parents=True)
    lines = []
    gp.batch(FakeAnthropic(), settings, store, approved_usd=None, out=lines.append)
    assert str(store.raw_dir.resolve()) in lines[0] and "no files found" in lines[1]


def test_the_printed_worst_case_is_rounded_up_so_approving_it_works(settings, store, monkeypatch):
    """2026-10-07: a $10.8235 worst case printed as $10.82 refused --approve-usd 10.82. Now it prints the cent above
    and that figure is accepted."""
    assert gp.cents_up(10.8235) == 10.83 and gp.cents_up(10.82) == 10.82 and gp.cents_up(0.33) == 0.33
    put(store, CANON)
    lines = []
    with pytest.raises(gp.GameplanRefused, match=r"--approve-usd (\d+\.\d\d)") as e:
        gp.batch(FakeAnthropic(), settings, store, approved_usd=None, out=lines.append)
    printed = float(re.search(r"--approve-usd (\d+\.\d\d)", str(e.value)).group(1))
    results, _ = gp.batch(FakeAnthropic(), settings, store, approved_usd=printed, out=lines.append)
    assert [r.status for r in results] == ["ok"]


def test_a_gameplan_with_info_flags_only_is_ok(settings, store, tmp_path):
    """Owner 2026-10-07: a morning gameplan with no stop or no targets is `ok` (info flags only), never held up."""
    answer = canned(CANON)
    answer["zones"][2]["stop"] = []
    answer["zones"][2]["targets"] = []
    (f,) = put(store, CANON)
    r = gp.parse_file(FakeAnthropic(replies={f.name: message(answer)}), settings, store, f, out=quiet)
    assert r.status == "ok" and {x["reason"] for x in r.flags} == {"no stop in the gameplan", "no targets in the gameplan"}
    assert store.load(D_CANON)["status"] == "ok"


def test_the_prompt_contains_the_glossary_and_its_version_is_pinned():
    """Owner 2026-10-07: every gameplan (archived and live) is parsed with docs/gameplan_glossary.md in the prompt.
    Editing the glossary changes the prompt, so its checksum is pinned next to PARSER_VERSION: bump both together."""
    import hashlib

    text = gp.GLOSSARY_PATH.read_text(encoding="utf-8")
    assert text.startswith("# Gameplan glossary (for the parser)") and text in gp.SYSTEM_PROMPT
    assert "two separate targets, never a range" in gp.SYSTEM_PROMPT
    assert hashlib.sha256(gp.GLOSSARY_PATH.read_bytes()).hexdigest() == gp.GLOSSARY_SHA256, \
        "docs/gameplan_glossary.md changed: bump PARSER_VERSION and update GLOSSARY_SHA256"
    assert "gameplan_terminology" not in gp.SYSTEM_PROMPT.split("Source:")[0]  # the cheat sheet is not sent
