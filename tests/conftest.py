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
