"""`step8b dislocations` (owner 2026-10-09): hand-built ratios and one hand-built day on a temporary SSD."""

from datetime import date

import numpy as np

from nqbot.checks import step8b, step8b_disloc
from nqbot.features import mapping as mp
from tests.test_step8b import DAY, NS, _setup, env  # noqa: F401 (env is a fixture)


def test_trailing_range_and_events():
    t = np.arange(600, dtype=np.int64) * NS  # 10 minutes of 1-second boards
    r = np.full(600, 41.0)
    r[300:] = 41.0 * 1.0015  # a 0.15% step at 300 s
    rng = step8b_disloc.trailing_range(t, r, mp.gap_segments(t, 10))
    assert rng[299] == 0 and rng[300] > 0.0014 and rng[418] > 0.0014 and rng[419] == 0  # (t - 120 s, t]: ~2 min
    ev = step8b_disloc.events(t, rng, 0.001)
    assert len(ev) == 1 and ev[0][0] == 300 * NS and ev[0][1] == 418 * NS
    assert step8b_disloc.events(t, rng, 0.002) == []
    seg = mp.gap_segments(np.concatenate([t[:300], t[300:] + 20 * NS]), 10)  # a 20-s gap at the step resets the window
    assert step8b_disloc.trailing_range(np.concatenate([t[:300], t[300:] + 20 * NS]), r, seg).max() == 0


def test_dislocation_count_on_a_hand_built_day(env):
    _setup(env, noise=1e-6)
    rep = step8b_disloc.check_dislocations(env, [DAY])
    assert rep["days"] == 1 and set(rep["chains"]) == {"QQQ", "SPY", "SPX"}
    for m in rep["chains"]:
        assert set(rep["chains"][m]) == {str(t) for t in step8b_disloc.THRESHOLDS}
    saved = step8b.reports_dir(env.paths) / "dislocations" / f"{DAY.isoformat()}.json"
    assert saved.exists()  # one file per day: a stopped run resumes
    assert (step8b.reports_dir(env.paths) / "dislocations.md").read_text(encoding="utf-8").startswith("# Mapping")
    again = step8b_disloc.check_dislocations(env, [DAY])
    assert again["chains"] == rep["chains"]
    missing = step8b_disloc.check_dislocations(env, [date(2026, 3, 4)])  # no boards stored: recorded as skipped
    assert missing["days"] == 0 and "2026-03-04" in missing["skipped"]
