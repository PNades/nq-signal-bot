"""Jobs runnable through `nqbot run <job>`. Later steps register their pulls, rebuilds, folds and sweeps here."""

from __future__ import annotations

from typing import Callable

from nqbot.backup import SkylitBackupJob
from nqbot.clients.databento_pull import DatabentoPullJob
from nqbot.clients.skylit_backfill import SkylitBackfillJob
from nqbot.clients.skylit_pull import SkylitPullJob
from nqbot.jobs.dummy import DummyJob
from nqbot.jobs.runner import Job

JOBS: dict[str, Callable[[], Job]] = {
    "dummy": DummyJob,
    "skylit_sample_days": lambda: SkylitPullJob("skylit_sample_days"),  # step 7 (started by nqbot step7 sample-days)
    # step 7 after skylit_max_strikes was raised (nqbot step7 sample-days --raised; its own state, presd_fixes D4)
    "skylit_sample_days_raise": lambda: SkylitPullJob("skylit_sample_days_raise"),
    "skylit_backfill": SkylitBackfillJob,  # step 10 (plan first: nqbot skylit plan)
    "databento_backfill": DatabentoPullJob,  # step 8 (plan first: nqbot databento plan)
    "skylit_backup": SkylitBackupJob,  # §11 Storage: Skylit history to Google Drive (also: nqbot backup)
}


def get_job(name: str) -> Job:
    try:
        return JOBS[name]()
    except KeyError:
        raise SystemExit(f"unknown job {name!r}; known jobs: {', '.join(sorted(JOBS))}") from None
