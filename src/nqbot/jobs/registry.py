"""Jobs runnable through `nqbot run <job>`. Later steps register their pulls, rebuilds, folds and sweeps here."""

from __future__ import annotations

from typing import Callable

from nqbot.jobs.dummy import DummyJob
from nqbot.jobs.runner import Job

JOBS: dict[str, Callable[[], Job]] = {
    "dummy": DummyJob,
}


def get_job(name: str) -> Job:
    try:
        return JOBS[name]()
    except KeyError:
        raise SystemExit(f"unknown job {name!r}; known jobs: {', '.join(sorted(JOBS))}") from None
