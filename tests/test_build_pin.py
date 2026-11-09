"""The build worktree pin (owner 2026-11-09, SPEC §13 Q-dryrun13 (1)) and the step-13-0 check harness's pure parts."""

from __future__ import annotations

import json
from datetime import date

import pytest

from nqbot import build_pin as bp
from nqbot import testpass
from nqbot.checks import step13 as s13
from nqbot.features import diagnostics as dg
from nqbot.storage import append_jsonl

SHA = "a" * 40


def fake_git(sha=SHA, dirty=""):
    def git(args):
        if args[:1] == ["status"]:
            return dirty
        if args[:2] == ["rev-parse", "HEAD"]:
            return sha + "\n"
        raise testpass.GitError("not faked")
    return git


def passed(paths, sha=SHA):
    append_jsonl(testpass.pass_path(paths), {"commit": sha, "finished": "2026-11-09T03:00:00", "host": "h"})


def test_no_pin_means_no_check(paths, tmp_path):
    assert bp.check(paths, repo=tmp_path, git=fake_git(), host="pc").ok


@pytest.fixture
def real_check(monkeypatch, full_suite_passed):
    monkeypatch.setattr(testpass, "check", full_suite_passed)   # the real full-suite check (conftest stubs it)


def test_pin_set_and_checked(paths, tmp_path, real_check):
    wt = tmp_path / "build"
    wt.mkdir()
    passed(paths)
    doc = bp.set_pin(paths, echo=lambda *_: None, repo=wt, git=fake_git(), host="pc")
    assert json.loads(bp.pin_path(paths).read_text())["commit"] == SHA and doc["host"] == "pc"
    assert bp.check(paths, repo=wt, git=fake_git(), host="pc").ok
    other = tmp_path / "code"
    other.mkdir()
    v = bp.check(paths, repo=other, git=fake_git(), host="pc")
    assert not v.ok and "build worktree" in v.message                     # the main folder: refused
    assert not bp.check(paths, repo=wt, git=fake_git("b" * 40), host="pc").ok   # moved HEAD: refused
    assert bp.check(paths, repo=other, git=fake_git(), host="laptop").ok        # another machine's pin: not checked
    bp.clear_pin(paths, echo=lambda *_: None)
    assert bp.read_pin(paths) is None


def test_pin_needs_a_recorded_full_suite_pass(paths, tmp_path, real_check):
    with pytest.raises(bp.PinRefused, match="can't be pinned"):   # a commit no pass covers (the drive is shared)
        bp.set_pin(paths, echo=lambda *_: None, repo=tmp_path, git=fake_git("c" * 40), host="pc")


def test_pin_never_moves_while_a_job_runs(paths, tmp_path, monkeypatch, real_check):
    passed(paths)
    monkeypatch.setattr(bp, "_live_locks", lambda p: ["skylit (feature_build, pid 1)"])
    with pytest.raises(bp.PinRefused, match="between jobs"):
        bp.set_pin(paths, echo=lambda *_: None, repo=tmp_path, git=fake_git(), host="pc")
    with pytest.raises(bp.PinRefused, match="between jobs"):
        bp.clear_pin(paths, echo=lambda *_: None)


# --- the step-13-0 check harness --------------------------------------------------------------------------------------

def test_check_runs_cover_every_fix_and_correction():
    assert set(s13.RUNS["legacy"][1]) == set(dg.LEGACY) and s13.RUNS["neutral"][1] == ()
    for k in dg.LEGACY:
        assert set(s13.RUNS[f"legacy+{k}"][1]) == set(dg.LEGACY) - {k}
    n = s13.NEUTRAL
    assert n["zone_join_buffer"] == n["zone_qualify_buffer"] == 0 and n["node_relevance_min_sec"] == 0
    assert {n[k] for k in ("hold_reaction_pts", "history_hold_pts", "retest_hold_pts", "pair_end_hold_pts")} == {20}
    assert n["node_relevance_top_n"] is None and n["node_role_at_formation"] == "approach"
    assert s13.RUNS["start"][0] == {} and s13.RUNS["start_again"][0] == {}
    ids = s13.chunk_ids()
    assert len(ids) == len(s13.RUNS) * len(s13.CHECK_DAYS) + 2 and ids[-1] == s13.REPORT_CHUNK
    assert all(d.month in (10, 11, 12, 1, 2, 3) for d in s13.CHECK_DAYS)          # development months only


def test_diff_day_counts_renamed_added_removed_and_labels(tmp_path):
    from nqbot.checks import feature_build as fb

    def rows(spec):
        return [{"event_id": e, "ts": ts, "direction": d, "n_poc_daily": 1, "zone_lo": 1.0, "zone_hi": 2.0,
                 "x": x} for e, ts, d, x in spec]

    a, b = tmp_path / "a", tmp_path / "b"
    day = date(2026, 3, 6)
    fb.write_rows(a / "touches" / "2026-03-06.parquet", rows([("e1", 10, 1, 1.0), ("e2", 20, -1, 2.0),
                                                                ("e3", 30, 1, 3.0)]), {})
    fb.write_rows(b / "touches" / "2026-03-06.parquet", rows([("e1", 10, 1, 1.5), ("e2b", 20, -1, 2.0),
                                                                ("e4", 40, 1, 4.0)]), {})
    out = lambda spec: [{"event_id": e, "label": lab, "t_resolved": t, "how": "x"} for e, lab, t in spec]
    fb.write_rows(a / "outcomes" / "2026-03-06.parquet", out([("e1", "held", 11), ("e2", "held", 21),
                                                                 ("e3", "broke", 31)]), {})
    fb.write_rows(b / "outcomes" / "2026-03-06.parquet", out([("e1", "held", 11), ("e2b", "broke", 22),
                                                                 ("e4", "held", 41)]), {})
    d = s13.diff_day(a, b, day)
    assert (d["same_id"], d["renamed"], d["removed"], d["added"]) == (1, 1, 1, 1)
    assert d["label_changed"] == 1 and d["resolution_changed"] == 1 and d["columns_changed"] == {"x": 1}
    assert d["added_by_type"] == {"poc_daily": 1} and len(d["diff_signatures"]) == 3


def test_redo_marks_only_the_named_runs_the_plot_and_the_report(paths):
    from nqbot.jobs.runner import load_state, record_plan, state_path
    from nqbot.storage import atomic_write_json

    record_plan(paths, s13.JOB, s13.chunk_ids())
    sp = state_path(paths, s13.JOB)
    st = load_state(sp)
    st["done"] = {c: {} for c in s13.chunk_ids()}
    atomic_write_json(sp, st)
    gone = s13.redo(paths, ["neutral", "start"])
    left = load_state(sp)["done"]
    assert sorted(gone) == sorted([c for c in s13.chunk_ids() if c.split(":")[0] in ("neutral", "start")]
                                  + [s13.PLOT_CHUNK, s13.REPORT_CHUNK])
    assert all(c not in left for c in gone) and "start_again:2026-03-06" in left and "legacy:2026-03-06" in left
    with pytest.raises(s13.Refused, match="unknown run"):
        s13.redo(paths, ["nope"])
