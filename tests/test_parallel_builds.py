"""Two feature builds at once (owner 2026-11-12, SPEC §13 Q-step13b (f) (2)): shared data locks and the per-job run
lock, build slot 2 with its own plan, state and children, the machine-wide core and memory budget, `nqbot resume`
running both; switched off unless `parallel_builds: true` is in config/local.yaml, and never in a build key."""

from __future__ import annotations

import copy
import json
import os
import shutil
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from nqbot import config
from nqbot.checks import feature_build as fb
from nqbot.checks import feature_build_job as fbj
from nqbot.checks import session_build as sb
from nqbot.checks.step8b import Env
from nqbot.jobs import queue
from nqbot.jobs.lock import DATABENTO, SKYLIT, JobLock, LockHeld, holders, job_locks, lock_path, shared_path
from nqbot.jobs.runner import ET, RunResult, load_state, run_job, run_lock_path, state_path
from tests.test_feature_build import (FakeSF, _job_ctx, _no_engine_stage, _Proc, _SlowProc, env,  # noqa: F401
                                     trading_days, write_backfill_plan)
from tests.test_resume_queue import NOW, _state


# --- locks ---------------------------------------------------------------------------------------------------------

def test_shared_holds_coexist_and_never_overlap_an_exclusive_one(tmp_path):
    with job_locks(tmp_path, (SKYLIT, DATABENTO), "feature_build", shared=True):
        with job_locks(tmp_path, (SKYLIT, DATABENTO), "feature_build_2", shared=True):
            assert [m for _, _, m in holders(tmp_path, SKYLIT)] == ["shared", "shared"]
            with pytest.raises(LockHeld):   # a pull that writes the data waits for the builds
                with job_locks(tmp_path, (SKYLIT,), "skylit_backfill"):
                    pass
            assert not lock_path(tmp_path, SKYLIT).exists()   # the refused exclusive lock left nothing behind
    assert holders(tmp_path, SKYLIT) == [] and holders(tmp_path, DATABENTO) == []
    with job_locks(tmp_path, (DATABENTO,), "databento_backfill"):
        with pytest.raises(LockHeld):   # a build waits for the pull
            with job_locks(tmp_path, (SKYLIT, DATABENTO), "feature_build", shared=True):
                pass
        assert not list(tmp_path.glob("*.shared-*"))   # its Skylit hold, taken first, was given back


def test_stale_shared_files_are_reclaimed_another_machines_never(tmp_path):
    dead = shared_path(tmp_path, SKYLIT, "feature_build").with_name(".nqbot_skylit.lock.shared-x-1-feature_build")
    dead.write_text(json.dumps({"host": __import__("socket").gethostname(), "pid": 2**22 + 7, "job": "feature_build"}))
    with job_locks(tmp_path, (SKYLIT,), "skylit_backfill"):   # a dead build's hold on this machine: removed
        assert not dead.exists()
    other = tmp_path / ".nqbot_skylit.lock.shared-OTHERPC-5-feature_build"
    other.write_text(json.dumps({"host": "OTHERPC", "pid": 5, "job": "feature_build"}))
    with pytest.raises(LockHeld, match="OTHERPC"):
        with job_locks(tmp_path, (SKYLIT,), "skylit_backfill"):
            pass
    assert other.exists()


class _Job:
    def __init__(self, name, shared=True, inside=None):
        self.name, self.shared_locks, self.locks, self.inside = name, shared, (SKYLIT, DATABENTO), inside

    def chunks(self, ctx):
        return ["a"]

    def run_chunk(self, chunk_id, ctx):
        if self.inside:
            self.inside()


def test_runner_shares_the_data_locks_and_keeps_one_run_per_job(paths, settings, log):
    until = datetime.now(ET) + timedelta(hours=6)
    seen = {}

    def inside():
        seen["other"] = run_job(_Job("feature_build_2"), settings, paths, until=until, log=log).status
        assert run_lock_path(paths, "feature_build").exists()
        with pytest.raises(LockHeld):   # the same job twice: its run lock
            run_job(_Job("feature_build"), settings, paths, until=until, log=log)
        with pytest.raises(LockHeld):   # a job holding the data locks alone
            run_job(_Job("skylit_backfill", shared=False), settings, paths, until=until, log=log)
        seen["inside"] = True

    assert run_job(_Job("feature_build", inside=inside), settings, paths, until=until, log=log).status == "complete"
    assert seen == {"other": "complete", "inside": True}
    assert not run_lock_path(paths, "feature_build").exists() and holders(paths.root, SKYLIT) == []


# --- the switch ----------------------------------------------------------------------------------------------------

def test_parallel_builds_is_off_by_default_and_never_in_the_settings(tmp_path, no_local_parallel_builds):
    real = no_local_parallel_builds
    shutil.copy(config.CONFIG_DIR / "settings.yaml", tmp_path / "settings.yaml")
    base = config.load_settings(tmp_path)
    assert real(tmp_path) is False
    (tmp_path / "local.yaml").write_text("parallel_builds: true\n", encoding="utf-8")
    assert real(tmp_path) is True
    assert config.load_settings(tmp_path) == base   # so no build key (settings + features + code) can change
    (tmp_path / "local.yaml").write_text("parallel_builds: 1\n", encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load_settings(tmp_path)
    assert fbj.FeatureBuildJob().parallel is False and fbj.FeatureBuildJob().shared_locks is False


# --- the machine-wide budget ---------------------------------------------------------------------------------------

def test_may_start_keeps_every_build_within_the_cores_and_its_fair_share():
    one = {"want": 1, "children": []}           # the other build's pass 1, between two sessions
    assert fbj.may_start(6, [one], 8) and not fbj.may_start(7, [one], 8)   # its one place stays free
    runs = {"want": 1, "children": [11]}
    assert fbj.may_start(6, [runs], 8) and not fbj.may_start(7, [runs], 8)
    pool = {"want": 20, "children": []}         # the other's pass-2 pool, just starting: 4 of 8 are its share
    assert fbj.may_start(3, [pool], 8) and not fbj.may_start(4, [pool], 8)
    full = {"want": 20, "children": list(range(8))}
    assert not fbj.may_start(0, [full], 8)      # it took all 8 first: this one waits ...
    assert not fbj.may_start(7, [{"want": 1, "children": []}], 8)   # ... and that one refills only to leave our share
    assert fbj.may_start(7, [{"want": 0, "children": []}], 8)       # nothing wanted elsewhere: every place is ours
    assert fbj.may_start(0, [one], 1)            # fewer places than builds: first come, first served (no deadlock)


def test_headroom_counts_what_the_other_builds_sessions_will_still_grow_by(monkeypatch):
    grown = {1: 1.0, 2: 4.5, 7: 2.0}
    monkeypatch.setattr(fbj, "_grown_gb", lambda p: grown[getattr(p, "pid", p)])
    assert fbj.headroom_gb(30 * 1024, [1, 2], 4.5, [(SimpleNamespace(pid=7), 5.0)]) == pytest.approx(30 - 4 - 3.5 - 3.0)


# --- slots ---------------------------------------------------------------------------------------------------------

def _fresh(env, base):
    return Env(settings=env.settings, features=copy.deepcopy(base), paths=env.paths, log=env.log, cal=env.cal,
               echo=lambda *_: None)


def _fake_sessions(env, monkeypatch, base):
    from nqbot.checks import step8b_disloc

    import nqbot.config as cfg
    monkeypatch.setattr(cfg, "load_features", lambda: copy.deepcopy(base))
    monkeypatch.setattr(sb, "build_day", lambda env_, day, state, pl, planned, damage: sb.Built(
        FakeSF(2), sb.DayState(day.isoformat(), {"n": 1}, {}, {}), {"skylit": "ok"}))
    monkeypatch.setattr(sb, "warmup_state", lambda *a, **k: sb.DayState("2025-09-30", {}, {}, {}))
    monkeypatch.setattr(sb, "load_damage", lambda env_: {})
    monkeypatch.setattr(step8b_disloc, "free_mb", lambda: 64 * 1024)
    monkeypatch.setattr(fbj, "_grown_gb", lambda p: 0.0)


class _SlotProc(_Proc):
    """A child stand-in that serves the slot its argv names, as `nqbot features ... --slot N` does."""

    def __init__(self, env, argv, log, seen):
        seen.append(list(argv))
        slot = int(argv[argv.index("--slot") + 1]) if "--slot" in argv else 1
        with fbj.using_slot(slot):
            super().__init__(env, argv, log)


def test_slot_2_has_its_own_plan_state_children_outputs_and_report(env, monkeypatch):
    base = copy.deepcopy(env.features)
    write_backfill_plan(env, trading_days(date(2025, 9, 29), date(2025, 10, 10), env.cal))
    _fake_sessions(env, monkeypatch, base)
    d1 = fbj.plan_slot(_fresh(env, base), [1], batch=2)
    with pytest.raises(fb.BuildRefused, match="never build the same key"):
        fbj.plan_slot(_fresh(env, base), [1], batch=2, slot=2)
    over = fb.parse_overrides(["node_band.QQQ=0.5"])
    d2 = fbj.plan_slot(_fresh(env, base), [1], batch=2, overrides=over, slot=2)
    assert fb.JOB == "feature_build" and d1["key"] != d2["key"]
    assert json.loads(fbj.plan_file(env.paths, 1).read_text(encoding="utf-8"))["key"] == d1["key"]
    assert json.loads(fbj.plan_file(env.paths, 2).read_text(encoding="utf-8"))["key"] == d2["key"]
    assert load_state(state_path(env.paths, "feature_build"))["chunks"] == fbj.chunk_ids(d1)
    assert load_state(state_path(env.paths, "feature_build_2"))["chunks"] == fbj.chunk_ids(d2)
    assert fbj.load_plan(_fresh(env, base), 2)["key"] == d2["key"]

    seen: list[list[str]] = []
    monkeypatch.setattr(fbj, "_child", lambda argv, log: _SlotProc(_fresh(env, base), argv, log, seen))
    job = fbj.FeatureBuildJob(slot=2)
    assert job.name == "feature_build_2"
    ctx = SimpleNamespace(**vars(_job_ctx(env, workers=4)), sleep=lambda s: None)
    ids = job.chunks(ctx)
    assert ids == fbj.chunk_ids(d2)
    for c in ids:
        job.run_chunk(c, ctx)
    assert seen and all(a[-2:] == ["--slot", "2"] for a in seen)
    days = [date.fromisoformat(x) for x in d2["sessions"]]
    assert all(fb.pass2_done(env, d2["key"], d, days) for d in days)
    assert not any(fb.summary_file(env, d1["key"], d).exists() for d in days)   # slot 1's folder untouched
    job.at_end(ctx)
    assert fbj.report_file(env, 2).name == "feature_build_2.md" and fbj.report_file(env, 2).exists()
    assert not fbj.report_file(env, 1).exists()
    # slot 1's children keep their argv exactly (the 3a proof)
    assert fbj.FeatureBuildJob()._argv(["features", "build-day", "2025-10-01", "--pass", "2"]) == \
        ["features", "build-day", "2025-10-01", "--pass", "2"]


def test_a_running_slot_cant_be_replanned_nor_its_key_run_twice(env, monkeypatch):
    base = copy.deepcopy(env.features)
    write_backfill_plan(env, trading_days(date(2025, 9, 29), date(2025, 10, 10), env.cal))
    _fake_sessions(env, monkeypatch, base)
    d1 = fbj.plan_slot(_fresh(env, base), [1], batch=2)
    run_lock_path(env.paths, "feature_build").parent.mkdir(parents=True, exist_ok=True)
    with JobLock(run_lock_path(env.paths, "feature_build"), "feature_build"):   # slot 1 runs (this process)
        with pytest.raises(fb.BuildRefused, match="is running"):
            fbj.plan_slot(_fresh(env, base), [1], batch=3)
        # a hand-made slot-2 plan with slot 1's key: refused at its start
        fbj.plan_file(env.paths, 2).parent.mkdir(parents=True, exist_ok=True)
        fbj.plan_file(env.paths, 2).write_text(json.dumps(d1), encoding="utf-8")
        ctx = SimpleNamespace(**vars(_job_ctx(env)), sleep=lambda s: None)
        with pytest.raises(LockHeld):
            fbj.FeatureBuildJob(slot=2).chunks(ctx)
    assert fbj.FeatureBuildJob(slot=2).chunks(ctx) == fbj.chunk_ids(d1)   # once slot 1 has stopped


def _other_build(env, children, want):
    """Another build running on this machine: its run lock held (by this process) and its sessions published."""
    p = run_lock_path(env.paths, "feature_build_2")
    p.parent.mkdir(parents=True, exist_ok=True)
    lk = JobLock(p, "feature_build_2")
    lk.acquire()
    fbj.publish(env.paths, "feature_build_2", [SimpleNamespace(pid=os.getpid())] * children, 4.5, want)
    return lk


@pytest.mark.parametrize("children,want,most", [(2, 2, 2), (0, 1, 3), (0, 0, 4)])
def test_pool_shares_the_machine_with_the_other_build(env, monkeypatch, children, want, most):
    from tests.test_feature_build import _pool_setup

    doc, plan_days, job, ctx, ids, secs = _pool_setup(env, monkeypatch, batch=8)
    job.parallel = True
    monkeypatch.setattr(fbj, "machine_cap", lambda settings: 4)
    run_lock_path(env.paths, "feature_build").parent.mkdir(parents=True, exist_ok=True)
    mine = JobLock(run_lock_path(env.paths, "feature_build"), "feature_build")   # as the runner holds it
    mine.acquire()
    other = _other_build(env, children, want)
    try:
        assert [o["job"] for o in fbj.others(env.paths, "feature_build")] == ["feature_build_2"]
        seen = {"running": set(), "started": [], "ended": [], "max": 0}
        monkeypatch.setattr(fbj, "_child", lambda argv, log: _SlowProc(env, argv, log, 3, seen))
        job.run_chunk(ids[-1], ctx)
        assert seen["max"] == most and all(fb.pass2_done(env, doc["key"], d, plan_days) for d in plan_days)
        x = json.loads((fbj.budget_dir(env.paths) / "feature_build.json").read_text(encoding="utf-8"))
        assert x["children"] == [] and x["want"] == 0   # done: it holds nothing and wants nothing
    finally:
        other.release()
        mine.release()
    assert fbj.others(env.paths, "feature_build") == []   # its run lock gone: its published file no longer counts


# --- resume --------------------------------------------------------------------------------------------------------

def _resume_many(paths, settings, run, run_many, running=()):
    lines = []
    code = queue.resume(paths, settings, run=run, verify=lambda since: [], echo=lines.append, now=lambda: NOW,
                        booted=lambda: 1000.0, together=lambda j: fbj.slot_of(j) is not None, run_many=run_many,
                        running_now=lambda j: j in running)
    return code, lines


def test_resume_runs_the_first_two_queued_builds_at_once_then_the_rest(paths, settings):
    queue.write_queue(paths, ["feature_build", "feature_build_2", "dummy"])
    for j in ("feature_build", "feature_build_2"):
        _state(paths, j, [{"status": "until"}])
    calls = []

    def run_many(names):
        calls.append(("together", list(names)))
        for n in names:
            _state(paths, n, [{"status": "complete"}], done=5)
        return {n: RunResult("complete", 5, 5) for n in names}

    def run(name):
        calls.append(("alone", name))
        return RunResult("complete", 1, 1)

    code, lines = _resume_many(paths, settings, run, run_many)
    assert code == 0 and calls == [("together", ["feature_build", "feature_build_2"]), ("alone", "dummy")]
    assert any("together with feature_build_2" in x for x in lines)
    rs = queue.load_resume_state(paths)
    assert rs["attempts"]["feature_build"]["outcome"] == "complete" == rs["attempts"]["feature_build_2"]["outcome"]


def test_resume_leaves_a_running_build_alone_and_waits_for_both(paths, settings):
    queue.write_queue(paths, ["feature_build", "feature_build_2", "dummy"])
    _state(paths, "feature_build", [{"start": "x"}])            # running by hand: no end yet
    _state(paths, "feature_build_2", [{"status": "until"}])
    calls = []

    def run_many(names):
        calls.append(list(names))
        return {n: RunResult("until", 2, 5, message="reached 07:30") for n in names}

    code, lines = _resume_many(paths, settings, lambda n: calls.append(n), run_many, running={"feature_build"})
    assert code == 0 and calls == [["feature_build_2"]]            # never `dummy`: the queue waits for the builds
    assert any("feature_build: already running" in x for x in lines)
    # both running already: nothing to start, as a held job lock
    code, _ = _resume_many(paths, settings, lambda n: calls.append(n), run_many,
                           running={"feature_build", "feature_build_2"})
    assert code == 3 and calls == [["feature_build_2"]]


def test_resume_records_a_refusal_and_a_lock_held_in_a_group(paths, settings):
    queue.write_queue(paths, ["feature_build", "feature_build_2"])
    _state(paths, "feature_build", [{"status": "until"}])
    _state(paths, "feature_build_2", [{"status": "until"}])

    def run_many(names):
        return {"feature_build": RunResult("refused", 0, 0, message="exit 2: refused: no plan"),
                "feature_build_2": LockHeld(run_lock_path(paths, "feature_build_2"), None)}

    code, lines = _resume_many(paths, settings, lambda n: None, run_many)
    assert code == 2
    rs = queue.load_resume_state(paths)
    assert rs["attempts"]["feature_build"]["outcome"] == "refused" and "feature_build_2" not in rs["attempts"]


def test_run_many_for_resume_reads_each_childs_summary(log):
    from nqbot import cli

    out = {"feature_build": ("feature_build: until (3/10 chunks done) - reached 07:30\n", 0),
           "feature_build_2": ("job lock ... is held\n", cli.EXIT_LOCKED), "x": ("refused: no plan\n", cli.EXIT_REFUSED)}

    class FakeP:
        def __init__(self, argv, stdout, stderr, env):
            name = argv[argv.index("run") + 1]
            assert argv[argv.index("run") + 2:] == ["--until", "07:30"]
            stdout.write(out[name][0])
            self.returncode, self.pid = out[name][1], 1

        def poll(self):
            return self.returncode

    res = cli._run_many_for_resume(list(out), "07:30", log, popen=FakeP, poll_sec=0)
    assert (res["feature_build"].status, res["feature_build"].done, res["feature_build"].total,
            res["feature_build"].message) == ("until", 3, 10, "reached 07:30")
    assert isinstance(res["feature_build_2"], LockHeld)
    assert res["x"].status == "refused" and "no plan" in res["x"].message


def test_status_and_the_build_pin_show_shared_holders(paths):
    from nqbot import build_pin
    from nqbot.jobs.status import lock_lines

    paths.root.mkdir(parents=True, exist_ok=True)   # as the runner makes it
    with job_locks(paths.root, (SKYLIT, DATABENTO), "feature_build_2", shared=True):
        lines = lock_lines(paths)
        assert any(x.startswith("skylit job lock: shared by job 'feature_build_2'") for x in lines)
        assert any("databento shared (feature_build_2" in x for x in build_pin._live_locks(paths))
    assert not any("shared" in x for x in lock_lines(paths)) and build_pin._live_locks(paths) == []
