"""Repo guards (owner 2026-10-07, after a 50 MB CLAUDE.md was committed): no tracked file over 1 MB, and CLAUDE.md
may not more than double or halve in one change."""

from __future__ import annotations

import shutil

import pytest

from nqbot import repo_guard as g

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def test_no_tracked_file_over_one_megabyte():
    big = [p for p in (g.too_big(path, (g.REPO / path).stat().st_size) for path in g.tracked_files()
                       if (g.REPO / path).is_file()) if p]
    assert big == []


def test_claude_md_is_within_its_size_band():
    assert g.check_worktree() == []


@pytest.mark.parametrize("old, new, refused", [
    (47_663, 48_584, False),
    (47_663, 95_326, False),        # exactly double is allowed
    (47_663, 95_327, True),
    (47_663, 50_165_567, True),     # the 2026-10-07 incident
    (47_663, 23_831, True),         # under half
    (None, 5_000, False),           # a new file has nothing to compare with
])
def test_size_change_rule(old, new, refused):
    assert (g.size_change_problem("CLAUDE.md", old, new) is not None) is refused


def test_too_big_and_exit_code(monkeypatch, capsys):
    assert g.too_big("x.bin", g.MAX_FILE_BYTES) is None and g.too_big("x.bin", g.MAX_FILE_BYTES + 1)
    monkeypatch.setattr(g, "check_worktree", lambda: ["CLAUDE.md: too big"])
    assert g.main(["repo_guard", "--worktree"]) == 2 and "refused" in capsys.readouterr().err
    monkeypatch.setattr(g, "check_worktree", lambda: [])
    assert g.main(["repo_guard"]) == 0
