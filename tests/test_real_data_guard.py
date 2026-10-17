"""Owner 2026-10-17: tests run on a temporary root and can never touch the real data (a test once appended four lines
to the real 10-15 task log on the SSD). The guard lives in tests/conftest.py."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nqbot import console, fallback, storage
from nqbot.config import load_settings
from tests import conftest


def _rel_root() -> str:
    return str(load_settings()["paths"]["root"]).strip("/\\")


@pytest.mark.parametrize("letter", ["D", "Z"])
def test_opening_a_file_under_the_real_root_fails_the_test(letter):
    target = Path(f"{letter}:\\") / _rel_root() / "logs" / "stream_session_20261015.txt"
    with pytest.raises(PermissionError, match="real data folder"):
        open(target, "a")  # refused by the audit hook before Windows sees it
    with pytest.raises(PermissionError, match="real data folder"):
        (target.parent / "new_dir").mkdir()
    assert [h.split()[0] for h in conftest._guard["hits"]] == ["open", "os.mkdir"]
    conftest._guard["hits"].clear()  # this test provoked them on purpose: don't fail it at teardown


def test_a_swallowed_refusal_still_fails_the_test():
    """Code that catches OSError (the console fallback, the drop retry) can't hide a touch of the real data."""
    target = Path("Z:\\") / _rel_root() / "logs" / "x.txt"
    try:
        target.write_text("x")
    except OSError:
        pass
    assert conftest._guard["hits"] == [f"open {os.path.normcase(str(target))}"]
    conftest._guard["hits"].clear()


def test_the_real_internal_drive_folder_is_guarded_too():
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        pytest.skip("no LOCALAPPDATA")
    with pytest.raises(PermissionError):
        open(Path(local) / "nqbot" / "ssd_events.log", "a")
    conftest._guard["hits"].clear()


def test_every_test_runs_on_a_temporary_root(tmp_path):
    """The SSD carrying the marker is never found, the fallbacks and the event log are temporary, no task log."""
    assert all(not (d / storage.MARKER).exists() for d in storage.candidate_drives())
    with pytest.raises(storage.SSDNotFound):
        storage.resolve_paths({"paths": {"root": _rel_root()}})
    assert conftest._under_real_root(fallback.FALLBACK_DRIVE) is None
    assert conftest._under_real_root(console.fallback_log_dir()) is None
    assert conftest._under_real_root(storage.SSD_EVENT_LOG) is None
    assert console.TASK_LOG_ENV not in os.environ


def test_the_shared_fixtures_are_temporary(paths):
    assert conftest._under_real_root(paths.root) is None and paths.mounted()
