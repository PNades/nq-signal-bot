"""Owner 2026-10-29 (CLAUDE.md rule 14 (c), (f)): `nqbot test` records a full-suite pass on a clean commit, and a
long job runs only a commit that passed (or one with only Markdown changes since a passed ancestor). Fake git only."""

from __future__ import annotations

import pytest

from nqbot import cli, testpass, testrun

A, B, C = "a" * 40, "b" * 40, "c" * 40


class FakeGit:
    """HEAD, the dirty tracked files, ancestry and the files changed between two commits."""

    def __init__(self, head=B, dirty=(), ancestors=None, diffs=None, fail=False):
        self.head, self.dirty, self.fail = head, list(dirty), fail
        self.ancestors = ancestors or {}  # commit -> set of its ancestors
        self.diffs = diffs or {}  # (old, new) -> changed files

    def __call__(self, args):
        if self.fail:
            raise testpass.GitError("git not found")
        if args[0] == "status":
            return "".join(f" M {f}\n" for f in self.dirty)
        if args[0] == "rev-parse":
            return self.head + "\n"
        if args[0] == "merge-base":
            if args[2] in self.ancestors.get(args[3], set()):
                return ""
            raise testpass.GitError("not an ancestor")
        if args[0] == "diff":
            return "".join(f + "\n" for f in self.diffs[(args[2], args[3])])
        raise AssertionError(args)


def _record(paths, commit, git):
    return testpass.record(paths, testpass.RunState(commit, ""), 0, 60.0, git=git, host="PC1")


def test_a_clean_full_pass_is_recorded_and_lets_the_job_run(paths, full_suite_passed):
    git = FakeGit(head=B)
    assert "recorded for commit bbbbbbb" in _record(paths, B, git)
    v = full_suite_passed(paths, git=git)
    assert v.ok is True and "passed the full suite" in v.message


def test_an_unrecorded_commit_refuses(paths, full_suite_passed):
    v = full_suite_passed(paths, git=FakeGit(head=B))
    assert v.ok is False and "hasn't passed the full suite" in v.message


def test_only_markdown_since_a_passed_ancestor_counts(paths, full_suite_passed):
    _record(paths, A, FakeGit(head=A))
    docs = FakeGit(head=B, ancestors={B: {A}}, diffs={(A, B): ["CLAUDE.md", "docs/SPEC.md"]})
    assert full_suite_passed(paths, git=docs).ok is True
    code = FakeGit(head=B, ancestors={B: {A}}, diffs={(A, B): ["docs/SPEC.md", "src/nqbot/cli.py"]})
    assert full_suite_passed(paths, git=code).ok is False


def test_a_passed_commit_that_isnt_an_ancestor_doesnt_count(paths, full_suite_passed):
    _record(paths, A, FakeGit(head=A))
    assert full_suite_passed(paths, git=FakeGit(head=C, diffs={(A, C): ["README.md"]})).ok is False


def test_uncommitted_code_refuses_even_on_a_passed_commit(paths, full_suite_passed):
    _record(paths, B, FakeGit(head=B))
    v = full_suite_passed(paths, git=FakeGit(head=B, dirty=["src/nqbot/cli.py"]))
    assert v.ok is False and "src/nqbot/cli.py" in v.message
    assert full_suite_passed(paths, git=FakeGit(head=B, dirty=["docs/SPEC.md"])).ok is True


def test_git_unreadable_warns_and_goes_on(paths, full_suite_passed):
    v = full_suite_passed(paths, git=FakeGit(fail=True))
    assert v.ok is None and "can't read git" in v.message


def test_a_dirty_or_moved_run_is_not_recorded(paths):
    assert "NOT recorded" in testpass.record(paths, testpass.before_run(FakeGit(dirty=["src/x.py"])), 0, 1.0)
    moved = testpass.record(paths, testpass.RunState(A, ""), 0, 1.0, git=FakeGit(head=B))
    assert "NOT recorded" in moved and "changed during the run" in moved
    assert "not recorded" in testpass.record(paths, testpass.RunState(B, ""), 1, 1.0, git=FakeGit(head=B))
    assert not testpass.pass_path(paths).exists()


def test_require_refuses_or_warns(paths, monkeypatch):
    out = []
    monkeypatch.setattr(testpass, "check", lambda p: testpass.Verdict(False, "not passed"))
    assert testpass.require(paths, out.append, "run dummy") is False and out[-1].startswith("refused: run dummy")
    monkeypatch.setattr(testpass, "check", lambda p: testpass.Verdict(None, "git missing"))
    assert testpass.require(paths, out.append, "run dummy") is True and out[-1].startswith("WARNING: run dummy")


@pytest.mark.parametrize("argv", [["run", "dummy"], ["resume"], ["queue", "add", "dummy"], ["backup"]])
def test_every_long_job_entry_refuses_without_a_pass(drive, paths, monkeypatch, capsys, argv):
    monkeypatch.setattr(testpass, "check", lambda p: testpass.Verdict(False, "commit bbbbbbb hasn't passed"))
    assert cli.main(argv) == cli.EXIT_REFUSED
    assert "hasn't passed" in capsys.readouterr().err
    from nqbot.jobs import queue

    assert queue.read_queue(paths) == []


def test_pytest_cmd_parallel_by_default_serial_on_request():
    cmd, full = testrun.pytest_cmd([], cpus=8)
    assert full and cmd[3:7] == ["-n", "6", "--dist", "load"] and cmd[-2:] == ["--basetemp", testrun.BASETEMP]
    cmd, full = testrun.pytest_cmd(["--serial"], cpus=8)
    assert full and "-n" not in cmd and "--basetemp" in cmd
    cmd, full = testrun.pytest_cmd(["tests/test_config.py", "--serial"], cpus=8)
    assert not full and cmd[-1] == "tests/test_config.py"
    assert testrun.workers(2) == 1 and testrun.workers(1) == 1
