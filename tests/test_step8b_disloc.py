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
        assert set(rep["chains"][m]) == {"board", "in_force"}  # both series (owner 2026-10-10)
        assert set(rep["chains"][m]["in_force"]) == {str(t) for t in step8b_disloc.THRESHOLDS}
    assert rep["per_day"][0]["futures_mean_1min_range"]["NQ"] > 0
    saved = step8b.reports_dir(env.paths) / "dislocations" / f"{DAY.isoformat()}.json"
    assert saved.exists()  # one file per day: a stopped run resumes
    assert (step8b.reports_dir(env.paths) / "dislocations.md").read_text(encoding="utf-8").startswith("# Mapping")
    again = step8b_disloc.check_dislocations(env, [DAY])
    assert again["chains"] == rep["chains"]
    missing = step8b_disloc.check_dislocations(env, [date(2026, 3, 4)])  # no boards stored: recorded as skipped
    assert missing["days"] == 1 and "2026-03-04" in missing["skipped"]  # the summary keeps every saved day


def test_a_one_board_glitch_counts_on_the_board_r_not_on_the_bots_r():
    """Owner 2026-10-10: the 60-s median the bot uses removes a one-board glitch, so it is no dislocation there."""
    t = np.arange(600, dtype=np.int64) * NS
    r = np.full(600, 41.0)
    r[300] = 41.0 * 1.003  # one bad board, 0.3%
    seg = mp.gap_segments(t, 10)
    board = step8b_disloc.events(t, step8b_disloc.trailing_range(t, r, seg), 0.002)
    r_in = mp.ratio_in_force(t, r, 60, 10, 3, 3)
    in_force = step8b_disloc.events(t, step8b_disloc.trailing_range(t, r_in, seg), 0.002)
    assert len(board) == 1 and in_force == []


def test_isolated_run_stops_cleanly_on_low_memory(env, monkeypatch):
    """Owner 2026-10-10: each day in its own process, and a clean stop before a day when memory is short."""
    import pytest

    _setup(env, noise=1e-6)
    calls = []
    monkeypatch.setattr(step8b_disloc, "free_mb", lambda: 1000)
    monkeypatch.setattr(step8b_disloc, "_run_day_in_child", lambda d: calls.append(d))
    with pytest.raises(step8b.CheckRefused, match="1000 MB free"):
        step8b_disloc.check_dislocations(env, [DAY, date(2026, 3, 6)], isolate=True)
    assert calls == []
    monkeypatch.setattr(step8b_disloc, "free_mb", lambda: 8000)
    monkeypatch.setattr(step8b_disloc, "_run_day_in_child",
                        lambda d, month_set="development": (calls.append(d), step8b_disloc.day_dislocations(
                            env, d, step8b.front_symbols(env), step8b_disloc.cal_mod.load())))
    rep = step8b_disloc.check_dislocations(env, [DAY, date(2026, 3, 6)], isolate=True)
    assert calls == [DAY, date(2026, 3, 6)] and rep["days"] == 1  # 03-06 has no boards here: skipped
    calls.clear()
    step8b_disloc.check_dislocations(env, [DAY, date(2026, 3, 6)], isolate=True)
    assert calls == []  # both saved: nothing runs again


def test_every_run_summarizes_all_saved_days(env):
    """Owner 2026-10-11: a one-day run (as each day's own process does) writes the summary of every saved day file;
    an old day file without the bot's-R series is listed as not counted yet, never summarized."""
    import json

    _setup(env, noise=1e-6)
    step8b_disloc.check_dislocations(env, [DAY])
    folder = step8b.reports_dir(env.paths) / "dislocations"
    x = json.loads((folder / f"{DAY.isoformat()}.json").read_text(encoding="utf-8"))
    other, old = date(2026, 3, 4), date(2026, 3, 3)
    (folder / f"{other.isoformat()}.json").write_text(json.dumps({**x, "date": other.isoformat()}), encoding="utf-8")
    v1 = {"date": old.isoformat(), "era": x["era"], "chains": {m: {"events": {}} for m in x["chains"]}}
    (folder / f"{old.isoformat()}.json").write_text(json.dumps(v1), encoding="utf-8")
    rep = step8b_disloc.check_dislocations(env, [DAY])
    assert rep["days"] == 2 and [p["date"] for p in rep["per_day"]] == [other.isoformat(), DAY.isoformat()]
    assert old.isoformat() in rep["pending"]
    md = (step8b.reports_dir(env.paths) / "dislocations.md").read_text(encoding="utf-8")
    assert "2 days" in md and old.isoformat() in md


def test_the_report_counts_the_guards_fires_and_possible_false_fires(env):
    """Owner 2026-10-14 (§13 Q-dislguard): every day records the guard's fires; the summary counts blocks, the possible
    false fires (only the per-board trigger fired) and their weekly rate against about one a week."""
    _setup(env, noise=1e-6)
    rep = step8b_disloc.check_dislocations(env, [DAY])
    assert set(rep["guard"]["chains"]) == {"QQQ", "SPY", "SPX"}
    assert all(c["blocks"] == 0 for c in rep["guard"]["chains"].values())  # a quiet hand-built day: no fire
    day = lambda d, chain_blocks: {"date": d, "era": "x", "futures_mean_1min_range": {}, "chains": {
        m: {"board": {"events": {}, "median_range_bps": 0}, "in_force": {"events": {}, "median_range_bps": 0},
            "guard": {"fires": [{"trigger": t} for b in chain_blocks.get(m, []) for t in b["triggers"]],
                      "blocks": chain_blocks.get(m, [])}} for m in step8b_disloc.DISLOC_MAPS}}
    blk = lambda trig, false=False: {"start_et": "10:00:00", "end_et": "10:02:00", "s": 120.0, "triggers": trig,
                                     "peak_pct": 0.25, "per_board_only": trig == ["per_board"], "false_fire": false,
                                     "max_level_gap": 1.0, "min_after_open": 30, "in_news_blackout": False}
    used = [day(f"2026-01-0{i}", {}) for i in range(1, 5)]
    used.append(day("2026-01-05", {"QQQ": [blk(["per_board"], True), blk(["bot_r", "per_board"])],
                                   "SPY": [blk(["per_board"]), blk(["bot_r"], True)]}))
    g = step8b_disloc._guard_summary(used)  # 5 days = one week
    q = g["chains"]["QQQ"]
    assert q["blocks"] == 2 and q["per_board_only"] == 1 and q["false_fires"] == 1
    assert q["fires"] == {"per_board": 2, "bot_r": 1}
    assert g["chains"]["SPY"]["per_board_only"] == 1 and g["chains"]["SPY"]["false_fires"] == 1  # independent columns
    assert g["false_fires_per_week_all"] == 2.0 and g["over_limit"]
    used[-1] = day("2026-01-05", {"QQQ": [blk(["per_board"], True), blk(["per_board"])]})
    assert not step8b_disloc._guard_summary(used)["over_limit"]  # exactly one false fire a week: not above


def test_false_fire_tolerance_and_hindsight_ratio(env):
    """QQQ uses its stop_buffer_floor; SPY/SPX have none and use atlas_match_tol ES (owner 2026-10-14)."""
    assert step8b_disloc.false_fire_tolerance(env, "QQQ") == (4.25, "stop_buffer_floor[QQQ]")
    assert step8b_disloc.false_fire_tolerance(env, "SPY") == (0.80, "atlas_match_tol[ES]")
    assert step8b_disloc.false_fire_tolerance(env, "SPX") == (0.80, "atlas_match_tol[ES]")
    t = np.arange(300, dtype=np.int64) * NS
    r = np.full(300, 41.0)
    r[150:] = 42.0
    h = step8b_disloc.hindsight_ratio(t, r, np.array([100, 140, 160, 200]))
    assert h.tolist() == [41.0, 41.0, 42.0, 42.0]  # centred: board 140 sees 90..200 (61 old, 51 new)


def test_a_block_is_a_false_fire_only_when_the_bots_levels_stayed_within_tolerance():
    """Hand-built: per-board R jumps 25 bps for good (a real shift): the bot's 60-s median lags the hindsight R, so
    the levels differ by more than 4 NQ at strike 600 -> not false. A two-way stale episode around a flat true R
    leaves the bot's R on the hindsight R -> false fire."""
    from nqbot.config import load_features
    from nqbot.features import disloc_guard as dg

    params = dg.GuardParams.from_features(load_features())
    t = np.arange(400, dtype=np.int64) * NS
    top = np.full(400, 600.0)
    r = np.full(400, 41.0)
    r[150:] = 41.0 * 1.0025
    r_in = mp.ratio_in_force(t, r, 60, 10, 3, 3)
    g = step8b_disloc._guard_day("QQQ", params, t, r, r_in, 0, [], top, 4.0)
    assert len(g["blocks"]) == 1 and g["blocks"][0]["max_level_gap"] > 4.0 and not g["blocks"][0]["false_fire"]
    r = np.full(400, 41.0)
    r[150:154] = 41.0 * 1.0011
    r[190:194] = 41.0 * 0.9989
    r_in = mp.ratio_in_force(t, r, 60, 10, 3, 3)
    g = step8b_disloc._guard_day("QQQ", params, t, r, r_in, 0, [], top, 4.0)
    assert len(g["blocks"]) == 1 and g["blocks"][0]["per_board_only"]
    assert g["blocks"][0]["max_level_gap"] <= 4.0 and g["blocks"][0]["false_fire"]


def test_walk_forward_months_only_take_walk_forward_dates(env):
    import pytest

    _setup(env, noise=1e-6)
    with pytest.raises(step8b_disloc.months_mod.NotDevelopmentMonth):
        step8b_disloc.check_dislocations(env, [DAY], month_set="walk-forward")  # DAY is a development day
    with pytest.raises(step8b.CheckRefused, match="top-up and demo"):
        step8b_disloc.check_dislocations(env, [DAY], month_set="sealed")


def test_the_job_runs_one_day_per_chunk_and_stops_on_low_memory(env, monkeypatch):
    """`nqbot run step8b_dislocations` (owner 2026-10-14): a day per chunk in its own process, a clean stop when
    memory is short (the day re-runs next time), saved days skipped."""
    import logging
    from types import SimpleNamespace

    import pytest

    from nqbot.jobs.lock import DATABENTO
    from nqbot.jobs.registry import get_job
    from nqbot.jobs.runner import StopChunk

    _setup(env, noise=1e-6)
    job = get_job("step8b_dislocations")
    assert job.locks == (DATABENTO,)
    ctx = SimpleNamespace(settings=env.settings, paths=env.paths, log=logging.getLogger("t"))
    monkeypatch.setattr(step8b_disloc, "development_days", lambda e, c, m="development": [DAY, date(2026, 3, 6)])
    v = step8b_disloc.DAY_VERSION  # owner 2026-10-19: the version is in the name, a new version re-runs every day
    assert job.chunks(ctx) == [f"{DAY.isoformat()}:v{v}", f"2026-03-06:v{v}"]
    ran = []
    monkeypatch.setattr(step8b_disloc, "_run_day_in_child", lambda d, m="development": ran.append((d, m)))
    monkeypatch.setattr(step8b_disloc, "free_mb", lambda: 1000)
    with pytest.raises(StopChunk, match="1000 MB free"):
        job.run_chunk(DAY.isoformat(), ctx)
    monkeypatch.setattr(step8b_disloc, "free_mb", lambda: 8000)
    job.run_chunk(DAY.isoformat(), ctx)
    assert ran == [(DAY, "development")]
    step8b_disloc.check_dislocations(env, [DAY])  # now saved
    job.run_chunk(DAY.isoformat(), ctx)
    assert ran == [(DAY, "development")]  # a saved day is not run again
