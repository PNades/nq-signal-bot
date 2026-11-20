"""`nqbot status` time left and finish date (owner 2026-11-19, SPEC §13 Q-status-eta): two builds at once, rows ending
with one build alone, the daily stop; synthetic job states only."""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest

from nqbot.jobs import estimate as est

ET = est.ET


def ts(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=ET)


def iso(t):
    return t.isoformat()


ROW = {"kind": "build", "values": [1, 2, 3, 4], "start": 2}        # three challengers: a pair, then one alone
PLAN = {"program": [{**ROW, "id": "r01"}, {**ROW, "id": "r02"}, {**ROW, "id": "r03"}],
        "chunks": [f"{r}:{w}" for r in ("r01", "r02", "r03") for w in ("b1", "b2", "labels", "score")]
        + ["end:summary"]}


def chunk(fin, hours):
    return {"sec": hours * 3600, "finished": iso(fin)}


def tune_state():
    """Run A (from 11-18 17:00): r01's pair 8 h, its single build 5 h, labels 0.6 h for 3 sets, score 0.1 h, then
    r02:b1 in flight 06:42-07:27 when the clock stops it; run B (07:35) finishes r02:b1 after 7.5 h more (8.25 h in
    all: 15 min redone) and is on r02:b2 now."""
    a0 = ts(2026, 11, 18, 17)
    fin = {"r01:b1": a0 + timedelta(hours=8), "r01:b2": a0 + timedelta(hours=13)}
    fin["r01:labels"] = fin["r01:b2"] + timedelta(hours=0.6)
    fin["r01:score"] = fin["r01:labels"] + timedelta(hours=0.1)
    b0 = ts(2026, 11, 19, 7, 35)
    fin["r02:b1"] = b0 + timedelta(hours=7.5)
    hours = {"r01:b1": 8, "r01:b2": 5, "r01:labels": 0.6, "r01:score": 0.1, "r02:b1": 7.5}
    done = {c: chunk(fin[c], hours[c]) for c in hours}
    runs = [{"start": iso(a0), "until": iso(ts(2026, 11, 19, 7, 30)), "end": iso(ts(2026, 11, 19, 7, 27)),
             "status": "until", "chunks": ["r01:b1", "r01:b2", "r01:labels", "r01:score"]},
            {"start": iso(b0), "until": iso(ts(2026, 11, 20, 7, 30)), "end": iso(fin["r02:b1"]), "chunks": ["r02:b1"]}]
    return {"done": done, "runs": runs, "chunks": PLAN["chunks"], "total": len(PLAN["chunks"])}


def test_the_constants_match_the_driver():
    from nqbot.checks import step13c
    from nqbot.tuning import program

    assert est.TUNE_BATCH == program.BATCH and est.BUILD_START_MARGIN_MIN == step13c.MIN_MINUTES_LEFT
    assert est.TUNE_JOB == step13c.JOB


def test_chunk_kinds_pairs_then_one_alone_and_label_sets():
    plan = {"program": [{**ROW, "id": "r06"}, {"id": "r07", "kind": "build", "values": [False, True], "start": False},
                        {"id": "r12", "kind": "exec_ttl", "values": [10, 30, 60, 120, 300], "start": 60},
                        {"id": "r13", "kind": "exec_stop", "values": [1, 2.5, 5, 8, 12], "start": 5},
                        {"id": "r14a", "kind": "logloss", "values": [30, 60, 120, 300], "start": 60}],
            "chunks": ["r00:relabel", "end:summary"]}
    k = est.tune_kinds(plan, {"r09": {"value": None}})
    assert k["r06:b1"] == ("b", 2) and k["r06:b2"] == ("b", 1) and k["r06:labels"] == ("labels", 3)
    assert k["r07:b1"] == ("b", 1) and "r07:b2" not in k
    assert k["r12:labels"] == ("labels", 4) and k["r13:labels"] == ("labels", 9)
    assert k["r14a:b1"] == ("b", 2) and k["r14a:b2"] == ("b", 1) and k["r14a:labels"] == ("labels", 0)
    assert k["r00:relabel"] == ("pre", 1) and k["end:summary"] == ("end", 1)
    # node_relevance_top_n = 5 won row 9: row 14 is event-changing, its builds get labels
    assert est.tune_kinds(plan, {"r09": {"value": 5}})["r14a:labels"] == ("labels", 3)


def test_a_chunk_split_by_a_stop_is_not_measured():
    st = tune_state()
    clean, resumed = est.clean_chunks(st)
    assert set(resumed) == {"r02:b1"} and resumed["r02:b1"] == pytest.approx(0.75 * 3600, abs=1)
    assert {"r01:b1", "r01:b2", "r01:labels", "r01:score"} <= clean
    # a run that finished nothing adds its whole time to the chunk the next run finishes
    st["runs"].insert(1, {"start": iso(ts(2026, 11, 19, 7, 31)), "until": iso(ts(2026, 11, 19, 7, 30)),
                          "end": iso(ts(2026, 11, 19, 7, 33)), "status": "until", "chunks": []})
    assert est.clean_chunks(st)[1]["r02:b1"] == pytest.approx(0.75 * 3600 + 120, abs=1)


def test_the_schedule_is_measured_from_the_runs():
    sch = est.schedule_of(tune_state(), queued=True)
    assert sch.until == time(7, 30) and sch.restart_min == pytest.approx(5) and sch.measured
    # no clock stop yet: 5 min if queued for nqbot resume, else the nightly window's complement
    one = {"runs": [{"start": iso(ts(2026, 11, 18, 20)), "until": iso(ts(2026, 11, 19, 7, 30)), "status": None}]}
    assert est.schedule_of(one, queued=True).restart_min == est.RESTART_DEFAULT_MIN
    assert est.schedule_of(one, queued=False).restart_min == pytest.approx(24 * 60 - 11.5 * 60)
    # a run that stopped early (a fault, not the clock) is no restart measurement
    early = {"runs": [{"start": iso(ts(2026, 11, 14, 20)), "until": iso(ts(2026, 11, 15, 7, 25)),
                       "end": iso(ts(2026, 11, 15, 1, 42)), "status": "until"},
                      {"start": iso(ts(2026, 11, 15, 2, 5)), "until": iso(ts(2026, 11, 15, 7, 30)), "status": None}]}
    assert not est.schedule_of(early, queued=True).measured


def test_the_timeline_waits_splits_and_keeps_wall_time_across_daylight_saving():
    sch = est.Schedule(time(7, 30), 5.0, cost_min=15.0)
    now = ts(2026, 11, 19, 5)
    # a 3-h chunk that may not be split waits for the 07:35 restart
    assert est.finish(now, [est.Seg(3.0)], sch) == (ts(2026, 11, 19, 10, 35), 1)
    # a split one runs to 07:30, then 0.5 h + 15 min redone after 07:35
    assert est.finish(now, [est.Seg(3.0, split=True)], sch) == (ts(2026, 11, 19, 8, 20), 1)
    # a build doesn't start in the last 10 min
    assert est.finish(ts(2026, 11, 19, 7, 25), [est.Seg(1.0, split=True, margin_min=10)], sch) == \
        (ts(2026, 11, 19, 8, 35), 1)
    # in flight: no start rule
    assert est.finish(ts(2026, 11, 19, 7, 25), [est.Seg(0.05, started=True)], sch) == (ts(2026, 11, 19, 7, 28), 0)
    # 2026-11-01: the stop stays at 07:30 wall time on the switch day
    assert est.next_stop(ts(2026, 10, 31, 8), time(7, 30)) == datetime(2026, 11, 1, 7, 30, tzinfo=ET)
    assert est.finish(now, [est.Seg(2.0)], None) == (ts(2026, 11, 19, 7), 0)


def test_the_tune_line_prices_each_chunk_by_its_kind():
    line = est.tune_line(tune_state(), PLAN, {}, ts(2026, 11, 19, 17, 5), queued=True)
    # left: r02:b2 (3 of its 5 h), labels 0.6, score 0.1; r03: pair 8, single 5 (split by 07:30), labels 0.6,
    # score 0.1; the summary 0.1
    assert "1 chunk(s) of two builds at once (~8.0 h each)" in line
    assert "2 single build(s) alone (~5.0 h each)" in line
    assert "labels for 6 set(s) (~12 min each)" in line and "3 score/summary chunk(s)" in line
    assert "~17.5 h of work, 1 stop(s) (daily stop 07:30, restart +5 min, ~15 min redone per stop)" in line
    assert line.endswith("finishes ~Fri 2026-11-20 10:55 ET")


def test_without_a_single_build_measured_it_counts_as_a_pair():
    st = tune_state()
    for c in ("r01:b2",):
        st["done"].pop(c)
    st["runs"][0]["chunks"].remove("r01:b2")
    line = est.tune_line(st, PLAN, {}, ts(2026, 11, 19, 17, 5), queued=True)
    assert "~8.0 h each (not measured yet: as a pair)" in line


def build_states(other_running: bool):
    k, j = "KKK", "JJJ"
    days = [f"2026-01-0{i}" for i in range(1, 6)]
    chunks = [f"p0:warmup:{k}:{days[0]}"] + [f"p1:{d}:{k}:{days[0]}" for d in days] + \
        [f"p2:{days[0]}~{days[-1]}:{k}:{days[0]}"]
    done = {chunks[0]: chunk(ts(2026, 11, 19, 8, 30), 0.1),
            chunks[1]: chunk(ts(2026, 11, 19, 9), 2 / 60),              # alone
            chunks[2]: chunk(ts(2026, 11, 19, 11), 5 / 60),             # the other slot ran then
            chunks[3]: chunk(ts(2026, 11, 19, 13), 2 / 60),             # alone
            # an older build J: two sessions alone, then its pass 2 alone, 20 min = 10 min a session
            "p1:2025-12-01:JJJ:2025-12-01": chunk(ts(2026, 11, 18, 19), 2 / 60),
            "p1:2025-12-02:JJJ:2025-12-01": chunk(ts(2026, 11, 18, 19, 30), 2 / 60),
            f"p2:2025-12-01~2025-12-02:{j}:2025-12-01": chunk(ts(2026, 11, 18, 20), 20 / 60)}
    state = {"done": done, "chunks": chunks, "runs": [
        {"start": iso(ts(2026, 11, 19, 8)), "until": iso(ts(2026, 11, 20, 7, 30)), "end": iso(ts(2026, 11, 19, 13)),
         "chunks": chunks[:4]}]}
    runs = [{"start": iso(ts(2026, 11, 19, 10)), "until": iso(ts(2026, 11, 20, 7, 30)),
             "end": iso(ts(2026, 11, 19, 12)), "status": "complete"}]
    if other_running:
        runs.append({"start": iso(ts(2026, 11, 19, 12, 50)), "until": iso(ts(2026, 11, 20, 7, 30))})
    return state, {"done": {}, "runs": runs}


def test_a_build_is_priced_by_pass_as_it_runs_now():
    state, other = build_states(other_running=False)
    line = est.build_line(state, other, {}, ts(2026, 11, 19, 13, 1), queued=True)
    assert "pass 1: 2 session(s) × ~2 min (alone)" in line and "pass 2: 5 session(s) × ~10 min wall (alone)" in line
    state, other = build_states(other_running=True)
    line = est.build_line(state, other, {"pass": 1}, ts(2026, 11, 19, 13, 1), queued=True)
    assert "pass 1: 2 session(s) × ~5 min (two at once)" in line and "(mixed)" in line
    # in pass 2, the pool's own count of sessions left
    state["done"].update({c: chunk(ts(2026, 11, 19, 13, 30), 2 / 60) for c in state["chunks"][4:6]})
    line = est.build_line(state, other, {"pass": 2, "pass_done": 3, "pass_total": 5, "session_sec": [60.0]},
                          ts(2026, 11, 19, 14), queued=True)
    assert "pass 1" not in line and "pass 2: 2 session(s)" in line and "~1 min redone per stop" in line


def test_status_shows_the_estimate_and_survives_a_broken_plan(paths):
    from nqbot.jobs.runner import state_path
    from nqbot.jobs.status import job_lines
    from nqbot.storage import atomic_write_json

    atomic_write_json(state_path(paths, "step13_tune"), tune_state())
    atomic_write_json(paths.data_dir / "jobs" / "step13_tune" / "tune.json", PLAN)
    (paths.data_dir / "jobs" / "queue.txt").write_text("step13_tune\n", encoding="utf-8")
    line = job_lines(paths, now=ts(2026, 11, 19, 17, 5))[0]
    assert line.startswith("step13_tune: 5/13 chunks done; left: ") and "finishes ~Fri 2026-11-20 10:55 ET" in line
    assert "p90 chunk" not in line
    atomic_write_json(paths.data_dir / "jobs" / "step13_tune" / "tune.json", {"program": [{"id": "r01"}]})
    assert "estimate unavailable (KeyError" in job_lines(paths, now=ts(2026, 11, 19, 17, 5))[0]
