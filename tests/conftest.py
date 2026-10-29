"""Shared fixtures: a temporary folder stands in for the external SSD (marker file at its root)."""

from __future__ import annotations

import copy
import logging
import shutil
from datetime import datetime, timedelta

import pytest

from nqbot import storage
from nqbot.config import CONFIG_DIR, load_settings
from nqbot.jobs.runner import ET


@pytest.fixture
def drive(tmp_path, monkeypatch):
    d = tmp_path / "ssd"
    d.mkdir()
    (d / storage.MARKER).write_text("")
    monkeypatch.setattr(storage, "candidate_drives", lambda: [d])
    return d


@pytest.fixture
def settings(tmp_path_factory):
    # Committed settings.yaml only: this machine's uncommitted config/local.yaml must not change test results.
    cfg = tmp_path_factory.mktemp("config")
    shutil.copy(CONFIG_DIR / "settings.yaml", cfg / "settings.yaml")
    s = copy.deepcopy(load_settings(cfg))
    s["paths"]["root"] = "nqbot"
    s["min_free_gb"] = 0
    s["skylit_max_strikes"] = 200  # the fakes are built for 200; the owner's step-7 value ("all") must not change tests
    s["months"] = None  # as before step 4b: a test that needs a ledger sets one (the real one must not change tests)
    return s


@pytest.fixture
def paths(drive, settings):
    return storage.resolve_paths(settings)


@pytest.fixture
def log():
    return logging.getLogger("nqbot.test")


class FakeClock:
    """Injected clock: chunks 'take time' by calling sleep, which advances it."""

    def __init__(self, start: datetime):
        self.t = start

    def now(self) -> datetime:
        return self.t

    def sleep(self, sec: float) -> None:
        self.t += timedelta(seconds=sec)


@pytest.fixture
def clock():
    return FakeClock(datetime(2026, 10, 3, 22, 0, tzinfo=ET))  # a Saturday evening


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests never reach a real API (no pulls, no credits): only httpx.MockTransport may answer."""
    import httpx

    def refuse(*args, **kwargs):
        raise RuntimeError("a test tried to use the real network")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)
    # The anthropic SDK (1.x) sends through httpx2, not httpx: block it too (tests use tests/anthropic_fakes.py).
    import httpx2

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", refuse)


# --- Tests never touch the real data (owner 2026-10-17: a test once appended to the real 10-15 task log) ---------
# Every test runs on a temporary root: the real SSD (the drive holding the marker) is never found, the internal-drive
# fallback, the console fallback logs and the SSD event log point into a temporary folder, and the task-log variable
# of a scheduled run is cleared. On top, an audit hook fails any test that opens, creates, removes, renames or
# changes a path under the real paths.root (on any drive letter, so an unplugged SSD is covered too) or the real
# %LOCALAPPDATA%\nqbot, even if the code under test swallowed the refusal.
_GUARD_EVENTS = frozenset({"open", "os.mkdir", "os.remove", "os.rmdir", "os.rename", "os.chmod", "os.truncate",
                           "os.utime", "os.link", "os.symlink", "shutil.copyfile", "shutil.copytree", "shutil.move",
                           "shutil.rmtree"})
_guard = {"on": False, "roots": (), "hits": []}


def _real_roots() -> tuple[str, ...]:
    import os
    import string

    rel = str((load_settings().get("paths") or {}).get("root") or "nqbot").strip("/\\")
    roots = [f"{letter}:\\{rel}" for letter in string.ascii_uppercase]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(os.path.join(local, "nqbot"))
    return tuple(os.path.normcase(os.path.abspath(r)) for r in roots)


def _under_real_root(path) -> str | None:
    import os

    if isinstance(path, int) or path is None:
        return None
    try:
        p = os.fsdecode(path) if isinstance(path, (bytes, os.PathLike)) else str(path)
        p = os.path.normcase(os.path.abspath(p))
    except (TypeError, ValueError):
        return None
    for root in _guard["roots"]:
        if p == root or p.startswith(root + os.sep):
            return p
    return None


def _audit(event: str, args: tuple) -> None:
    if not _guard["on"] or event not in _GUARD_EVENTS:
        return
    for a in args[:2]:  # the path(s): open(path, ...), rename(src, dst), copyfile(src, dst), ...
        hit = _under_real_root(a)
        if hit:
            _guard["hits"].append(f"{event} {hit}")
            raise PermissionError(f"a test touched the real data folder: {event} {hit} (tests/conftest.py guard)")


def pytest_configure(config):
    import sys

    _guard["roots"] = _real_roots()
    sys.addaudithook(_audit)  # can't be removed; it does nothing outside a test (`on` is False)


@pytest.fixture(autouse=True)
def temporary_root(tmp_path_factory, monkeypatch):
    from nqbot import console, fallback

    real_drives = storage.candidate_drives
    # the drives are still listed (tests of the scan itself), but never the one carrying the SSD marker
    monkeypatch.setattr(storage, "candidate_drives", lambda: [d for d in real_drives() if not (d / storage.MARKER).exists()])
    from nqbot import setup_ssd

    monkeypatch.setattr(setup_ssd, "candidate_drives", storage.candidate_drives)  # imported by name there
    local = tmp_path_factory.mktemp("internal_drive")
    monkeypatch.setattr(fallback, "FALLBACK_DRIVE", local / "nqbot" / "fallback")
    monkeypatch.setattr(console, "fallback_log_dir", lambda: local / "nqbot" / "fallback" / "nqbot" / "logs")
    monkeypatch.delenv(console.TASK_LOG_ENV, raising=False)
    _guard["hits"] = []
    _guard["on"] = True
    yield
    _guard["on"] = False
    hits, _guard["hits"] = _guard["hits"], []
    if hits:
        pytest.fail("test touched the real data folder:\n  " + "\n  ".join(hits), pytrace=False)


@pytest.fixture(autouse=True)
def ssd_event_log(tmp_path_factory, monkeypatch):
    """Tests never write the real SSD drop log on the internal drive (owner 2026-10-11)."""
    log = tmp_path_factory.mktemp("ssd_events") / "ssd_events.log"
    monkeypatch.setattr(storage, "SSD_EVENT_LOG", log)
    return log


@pytest.fixture(autouse=True)
def full_suite_passed(monkeypatch):
    """CLI job tests never read the real repo's git state (owner 2026-10-29, rule 14 (f)): the full-suite check
    answers 'passed'. tests/test_testpass.py tests the real check with a fake git."""
    from nqbot import testpass

    real = testpass.check
    monkeypatch.setattr(testpass, "check", lambda paths, git=None: testpass.Verdict(True, "stubbed in tests"))
    return real


@pytest.fixture(autouse=True)
def no_power_calls(monkeypatch):
    """Tests never call powercfg or SetThreadExecutionState: the calls are recorded instead (tests/test_power.py)."""
    from nqbot import power

    calls: list[int] = []
    monkeypatch.setattr(power, "_set_state", calls.append)
    monkeypatch.setattr(power, "powercfg_query", lambda subgroup, setting: "Current AC Power Setting Index: 0x00000000\n"
                        "Current DC Power Setting Index: 0x00000000\n")
    return calls
