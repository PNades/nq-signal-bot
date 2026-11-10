"""Jobs runnable through `nqbot run <job>`. Later steps register their pulls, rebuilds, folds and sweeps here."""

from __future__ import annotations

from typing import Callable

from nqbot.backup import SkylitBackupJob
from nqbot.checks.feature_build import FeatureBuildJob
from nqbot.checks.step8b_disloc import DislocationsJob
from nqbot.checks.step13 import CheckJob as Step13CheckJob
from nqbot.checks.step13a import ReactionsJob
from nqbot.checks.step8b_gate import GateJob
from nqbot.clients.databento_pull import DatabentoPullJob
from nqbot.clients.skylit_backfill import SkylitBackfillJob
from nqbot.clients.skylit_multiexp import MultiExpJob
from nqbot.clients.skylit_pull import SkylitPullJob
from nqbot.jobs.drill import DrillJob
from nqbot.jobs.dummy import DummyJob
from nqbot.jobs.runner import Job

JOBS: dict[str, Callable[[], Job]] = {
    "dummy": DummyJob,
    "dummy_drill": DrillJob,  # owner 2026-10-28: the resume drill (set up with nqbot drill setup)
    "skylit_sample_days": lambda: SkylitPullJob("skylit_sample_days"),  # step 7 (started by nqbot step7 sample-days)
    # step 7 after skylit_max_strikes was raised (nqbot step7 sample-days --raised; its own state, presd_fixes D4)
    "skylit_sample_days_raise": lambda: SkylitPullJob("skylit_sample_days_raise"),
    "skylit_backfill": SkylitBackfillJob,  # step 10 (plan first: nqbot skylit plan)
    "databento_backfill": DatabentoPullJob,  # step 8 (plan first: nqbot databento plan)
    "skylit_backup": SkylitBackupJob,  # §11 Storage: Skylit history to Google Drive (also: nqbot backup)
    "step8b_dislocations": DislocationsJob,  # step 8b: months 1-6 dislocation count and guard fires, a day per chunk
    "skylit_multiexp": MultiExpJob,  # owner 2026-10-23: multi-expiration snapshots, raw only (plan: skylit multiexp-plan)
    "step8b_gate": GateJob,  # step 8b: bias, estimator, alignment on the 22 development days, a day per chunk
    "feature_build": FeatureBuildJob,  # step 11 part 3: the two-pass feature build (plan: nqbot features plan)
    "step13_check": Step13CheckJob,  # step 13-0: the corrections check on the development days (owner 2026-11-09)
    "step13a_reactions": ReactionsJob,  # step 13a: reaction distances on months 1-4 (plan: nqbot step13 reactions-plan)
}


def get_job(name: str) -> Job:
    try:
        return JOBS[name]()
    except KeyError:
        raise SystemExit(f"unknown job {name!r}; known jobs: {', '.join(sorted(JOBS))}") from None
