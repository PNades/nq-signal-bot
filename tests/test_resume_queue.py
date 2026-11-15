"""Owner 2026-10-28 (SPEC §11 Overnight jobs): the job queue, `nqbot resume` and the resume drill job."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from nqbot.jobs import drill, queue
from nqbot.jobs.dummy import DummyJob
from nqbot.jobs.lock import LockHeld, lock_path, SKYLIT
from nqbot.jobs.runner import RunResult, load_state, record_plan, run_job, state_path
from nqbot.jobs.status import job_lines
from nqbot.storage import atomic_write_json

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


def _state(paths, job, runs, done=0, total=5):
    atomic_write_json(state_path(paths, job), {"done": {f"c{i}": {"sec": 1} for i in range(done)}, "total": total,
                                               "runs": runs})


def _resume(paths, settings, run, verify=lambda since: [], boot=1000.0, echo=None):
    lines = []
    code = queue.resume(paths, settings, run=run, verify=verify, echo=echo or lines.append, now=lambda: NOW,
                        booted=lambda: boot)
    return code, lines


def test_queue_file_round_trip_ignores_comments_and_duplicates(paths):
    queue.write_queue(paths, ["dummy", "step8b_gate"])
    assert queue.read_queue(paths) == ["dummy", "step8b_gate"]
    queue.queue_path(paths).write_text("# c\ndummy  # first\n\ndummy\nstep8b_gate\n", encoding="utf-8")
    assert queue.read_queue(paths) == ["dummy", "step8b_gate"]


@pytest.mark.parametrize("end,action", [(None, "run"), ("until", "run"), ("ssd_missing", "run"), ("died", "run"),
                                        ("budget_stall", "run"),   # owner 2026-11-15: a stuck two-builds mutex
                                        ("stopped", "wait"), ("credits", "wait"), ("failures", "wait"),
                                        ("low_space", "wait"), ("api_error", "wait")])
def test_decide_resumes_only_clean_interruptions(end, action):
    runs = [] if end is None else [{"start": "x"} if end == "died" else {"start": "x", "status": end}]
    d = queue.decide("dummy", {"done": {}, "total": 5, "runs": runs}, {"attempts": {}, "held": {}}, 3)
    assert d.action == action


def test_old_plan_done_ids_never_make_a_job_look_complete():
    """Owner 2026-10-30: done keeps the old plan's ids after a re-plan; only the current plan's chunks count."""
    old = {f"old-{i}": {"sec": 1} for i in range(10)}
    state = {"done": {**old, "new-0": {"sec": 1}}, "total": 3, "chunks": ["new-0", "new-1", "new-2"],
             "runs": [{"start": "x", "status": "complete"}]}
    assert not queue.is_complete(state)
    assert queue.decide("dummy", state, {"attempts": {}, "held": {}}, 3).action == "run"
    state["done"].update({"new-1": {"sec": 1}, "new-2": {"sec": 1}})
    assert queue.is_complete(state)


def test_complete_job_is_skipped_and_the_next_one_runs(paths, settings):
    queue.write_queue(paths, ["dummy", "step8b_gate"])
    _state(paths, "dummy", [{"status": "complete"}], done=5, total=5)
    ran = []
    code, lines = _resume(paths, settings, lambda j: (ran.append(j), RunResult("until", 2, 23))[1])
    assert code == 0 and ran == ["step8b_gate"]
    assert any("dummy: complete" in line for line in lines)


def test_a_completed_job_moves_on_to_the_next(paths, settings):
    queue.write_queue(paths, ["dummy", "step8b_gate"])
    ran = []
    code, _ = _resume(paths, settings, lambda j: (ran.append(j), RunResult("complete", 5, 5))[1])
    assert code == 0 and ran == ["dummy", "step8b_gate"]


def test_an_owner_stop_is_never_resumed(paths, settings):
    queue.write_queue(paths, ["dummy"])
    _state(paths, "dummy", [{"status": "stopped"}], done=2)
    code, lines = _resume(paths, settings, lambda j: pytest.fail("must not run"))
    assert code == 2 and "waiting for the owner" in lines[-1]


def test_lock_held_is_not_counted_as_an_attempt(paths, settings):
    queue.write_queue(paths, ["dummy"])

    def held(job):
        raise LockHeld(lock_path(paths.root, SKYLIT), {"job": job, "pid": 1, "host": "pc"})

    code, _ = _resume(paths, settings, held)
    assert code == 3 and "dummy" not in queue.load_resume_state(paths)["attempts"]


def test_repeated_deaths_without_progress_hold_the_job_until_released(paths, settings):
    queue.write_queue(paths, ["dummy"])
    _state(paths, "dummy", [{"start": "x"}], done=1)  # the process died mid-run

    def dies(job):  # the run dies again (power cut): no outcome recorded, the state unchanged
        raise SystemExit  # escapes resume like a killed process would

    for _ in range(3):
        with pytest.raises(SystemExit):
            _resume(paths, settings, dies)
    code, lines = _resume(paths, settings, lambda j: pytest.fail("held: must not run"))
    assert code == 2 and "HELD" in lines[-1]
    assert queue.release(paths, "dummy") == ["dummy"]
    code, _ = _resume(paths, settings, lambda j: RunResult("until", 2, 5))
    assert code == 0


def test_progress_resets_the_crash_count(paths, settings):
    queue.write_queue(paths, ["dummy"])
    _state(paths, "dummy", [{"start": "x"}], done=1)
    for done in (2, 3, 4):
        with pytest.raises(SystemExit):
            _resume(paths, settings, lambda j: (_ for _ in ()).throw(SystemExit))
        _state(paths, "dummy", [{"start": "x"}], done=done)  # a chunk finished before the next death
    assert queue.decide("dummy", load_state(state_path(paths, "dummy")), queue.load_resume_state(paths),
                        3).action == "run"


def test_a_refused_start_waits_for_the_owner(paths, settings):
    queue.write_queue(paths, ["dummy"])
    code, _ = _resume(paths, settings, lambda j: RunResult("refused", 0, 0, message="MissingSecret: no key"))
    assert code == 2
    code, lines = _resume(paths, settings, lambda j: pytest.fail("must not run"))
    assert code == 2 and "refused on its last start" in lines[-1]


def test_first_resume_after_a_restart_verifies_and_holds_on_damage(paths, settings):
    queue.write_queue(paths, ["dummy"])
    seen = []
    code, _ = _resume(paths, settings, lambda j: pytest.fail("must not run"),
                      verify=lambda since: (seen.append(since), [r"D:\nqbot\logs\a.txt", r"D:\nqbot\x.parquet"])[1])
    assert code == 1 and seen == [NOW - timedelta(days=settings["ops"]["resume_verify_days"])]
    # owner 2026-10-31: the hold says when the check ran (ET) and which files it flagged, not the boot time
    assert queue.load_resume_state(paths)["hold_all"] == (
        r"nqbot verify at 2026-10-28 08:00 ET (first resume since the computer started) flagged 2 file(s): "
        r"D:\nqbot\logs\a.txt, D:\nqbot\x.parquet (see <reports_dir>/verify/verify.md)")
    code, lines = _resume(paths, settings, lambda j: pytest.fail("must not run"))
    assert code == 2 and "everything held" in lines[-1]
    queue.release(paths)
    code, _ = _resume(paths, settings, lambda j: RunResult("until", 1, 5), verify=lambda s: [])
    assert code == 0


def test_a_long_hold_names_the_first_files_and_counts_the_rest(paths, settings):
    files = [f"f{i}.txt" for i in range(queue.HOLD_SHOW_FILES + 3)]
    _resume(paths, settings, lambda j: pytest.fail("must not run"), verify=lambda since: files)
    held = queue.load_resume_state(paths)["hold_all"]
    assert f"flagged {len(files)} file(s): f0.txt, " in held and "f4.txt and 3 more (see" in held and "f5" not in held


def test_verify_runs_once_per_boot(paths, settings):
    queue.write_queue(paths, ["dummy"])
    calls = []
    for boot in (1000.0, 1001.0, 5000.0):  # 1001: the same boot read a second later
        _resume(paths, settings, lambda j: RunResult("until", 1, 5), verify=lambda s: (calls.append(s), [])[1],
                boot=boot)
    assert len(calls) == 2


def test_a_complete_job_replanned_with_new_chunks_is_shown_and_resumed(paths, settings, log, clock):
    """Owner 2026-10-30: a re-plan saves the new plan's ids at once: status shows n/m, the queue runs the rest."""
    queue.write_queue(paths, ["dummy"])

    def run(job):
        return run_job(DummyJob(3, 1), settings, paths, until=clock.now() + timedelta(hours=1), log=log,
                       now=clock.now, sleep=clock.sleep)

    assert _resume(paths, settings, run)[0] == 0
    assert job_lines(paths)[0].startswith("dummy: 3/3 chunks done (complete)")
    assert record_plan(paths, "dummy", [f"chunk-{i:03d}" for i in range(5)]) == (3, 5)  # two new chunks
    state = load_state(state_path(paths, "dummy"))
    assert not queue.is_complete(state) and job_lines(paths)[0].startswith("dummy: 3/5 chunks done")
    d = queue.decide("dummy", state, queue.load_resume_state(paths), 3)
    assert (d.action, d.reason) == ("run", "re-planned since its last complete run")
    ran = []

    def run5(job):
        r = run_job(DummyJob(5, 1), settings, paths, until=clock.now() + timedelta(hours=1), log=log,
                    now=clock.now, sleep=clock.sleep)
        ran.extend(r.ran)
        return r

    assert _resume(paths, settings, run5)[0] == 0
    assert ran == ["chunk-003", "chunk-004"]  # only the new ones, nothing re-run
    assert job_lines(paths)[0].startswith("dummy: 5/5 chunks done (complete)")


def test_resume_runs_the_real_runner_end_to_end(paths, settings, log, clock):
    queue.write_queue(paths, ["dummy"])

    def run(job):
        return run_job(DummyJob(3, 1), settings, paths, until=clock.now() + timedelta(hours=1), log=log,
                       now=clock.now, sleep=clock.sleep)

    code, _ = _resume(paths, settings, run)
    assert code == 0 and queue.is_complete(load_state(state_path(paths, "dummy")))
    code, lines = _resume(paths, settings, lambda j: pytest.fail("complete: must not run"))
    assert code == 0 and "every queued job is complete" in lines[-1]


def test_drill_runs_its_configured_chunks_and_checks_clean(paths, settings, log, clock):
    assert drill.setup(paths, 4, 2.5) is None
    r = run_job(drill.DrillJob(), settings, paths, until=clock.now() + timedelta(hours=1), log=log, now=clock.now,
                sleep=clock.sleep)
    assert (r.status, r.total) == ("complete", 4)
    c = drill.check(paths)
    assert c["clean"] and c["finished"] == 4 and c["processes"] == 1 and not c["finished_twice"]
    moved = drill.setup(paths, 2, 1)  # a fresh drill moves the old one aside, never deletes it
    assert moved is not None and (moved / drill.LOG).exists()
    assert not drill.check(paths)["clean"]


def test_drill_resumes_after_an_interruption(paths, settings, log, clock):
    drill.setup(paths, 6, 600)
    r = run_job(drill.DrillJob(), settings, paths, until=clock.now() + timedelta(minutes=25), log=log,
                now=clock.now, sleep=clock.sleep)
    assert r.status == "until" and 0 < r.done < 6
    r = run_job(drill.DrillJob(), settings, paths, until=clock.now() + timedelta(hours=2), log=log, now=clock.now,
                sleep=clock.sleep)
    assert r.status == "complete" and drill.check(paths)["clean"]


def test_queue_lines_show_each_decision(paths, settings):
    assert queue.queue_lines(paths, settings) == ["queue: empty"]
    queue.write_queue(paths, ["dummy"])
    _state(paths, "dummy", [{"status": "stopped"}])
    assert "dummy: wait" in queue.queue_lines(paths, settings)[1]


def test_status_shows_a_hold_even_with_the_queue_empty(paths, settings):
    """Owner 2026-10-31: the after-restart verify hold (and a held job no longer queued) shows with an empty queue."""
    rs = queue.load_resume_state(paths)
    rs["hold_all"] = "nqbot verify found damaged files after the restart at 2026-10-22 04:17 UTC"
    rs["held"] = {"dummy_drill": "3 resume attempts in a row died"}
    queue.save_resume_state(paths, rs)
    assert queue.queue_lines(paths, settings) == [
        "queue: empty",
        "  HOLD ALL: nqbot verify found damaged files after the restart at 2026-10-22 04:17 UTC "
        "(lift: uv run nqbot queue release)",
        "  HELD dummy_drill (not queued): 3 resume attempts in a row died "
        "(lift: uv run nqbot queue release dummy_drill)"]
    queue.write_queue(paths, ["dummy"])
    lines = queue.queue_lines(paths, settings)
    assert lines[0] == "queue (nqbot resume):" and lines[1].startswith("  HOLD ALL:")
    assert lines[2].startswith("  HELD dummy_drill (not queued)") and lines[3].startswith("  dummy: run")
    queue.release(paths)
    assert not any("HOLD" in x or "HELD" in x for x in queue.queue_lines(paths, settings))
