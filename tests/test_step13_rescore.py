"""Pre-13d item (8) (owner 2026-11-20): `step13 rescore-plan` / job `step13_rescore` / `rescore-report`: 13c's
execution winners against their runners-up on the corrected build, months 1-4, nothing written to the configuration.
Synthetic records and builds only."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from nqbot.checks import step13_rescore as rs
from nqbot.checks import step13b as s13b
from nqbot.checks import step13c as s13c
from nqbot.storage import atomic_write_json
from nqbot.tuning import compare as cmp
from nqbot.tuning.data import Build
from tests.test_feature_build import LEDGER, env  # noqa: F401  (fixture)

VOL = {"window_min": 15, "qqq_node": {"min": 4.0, "k": 0.5}}


def _records(env, *, r12=None, r13=None, vv=None):
    """13c's winners, row 13's report and the vol-floor fit, as 13c writes them."""
    s = env.settings
    start_buf = s["stop_buffer"]
    w12 = r12 or {"value": {"c": ["stop_limit", 120], "d": ["marketable_limit", s["entry_ttl_sec"]["d"]]},
                  "lead": {"c": {"next_best": ["stop_limit", 300], "lead": 0.05, "lo": -5.1, "hi": 5.21},
                           "d": {"next_best": ["marketable_limit", 10], "lead": 0.0, "lo": -1.96, "hi": 2.05}}}
    w13 = r13 or {"value": {"floor": "vol", "stop_buffer": start_buf + 2},
                  "lead": {"next_best": start_buf + 4, "lead": 1.0, "lo": -2.0, "hi": 3.0}}
    atomic_write_json(s13c.winners_file(env), {"r11": {"value": 10}, "r12": w12, "r13": w13})
    rep = {"floors": {"vol": {"winner": w13["value"]["stop_buffer"], "lead": w13["lead"]},
                      "fixed": {"winner": start_buf, "lead": {"next_best": start_buf + 2}}},
           "vol_vs_fixed": vv or {"diff": 2.5, "lo": 0.4, "hi": 4.0, "fixed_buffer": start_buf,
                                  "vol_buffer": w13["value"]["stop_buffer"]}}
    atomic_write_json(s13c.rep_dir(env) / "r13_stop_buffer_stop_floor.json", rep)
    atomic_write_json(s13c.vol_fit_file(env), {"override": VOL})
    return w12, w13


def test_points_come_from_13cs_records_as_overrides_of_the_committed_settings(env):
    _records(env)
    s = env.settings
    sets, comps = rs.comparisons(env)
    assert set(sets) == {"winners", "r12_runners_up", "r13_buffer_runner_up", "r13_floor_runner_up"}
    assert rs.overrides_of(env, sets["winners"]) == {"entry_ttl_sec.c": 120, "stop_buffer": s["stop_buffer"] + 2,
                                                    "stop_floor_vol": VOL}          # ttl (d) = the committed one
    assert rs.overrides_of(env, sets["r12_runners_up"]) == {"entry_ttl_sec.c": 300, "entry_ttl_sec.d": 10,
                                                           "stop_buffer": s["stop_buffer"] + 2, "stop_floor_vol": VOL}
    assert sets["r13_buffer_runner_up"]["stop_buffer"] == s["stop_buffer"] + 4
    # the floor decision: the other floor (fixed: no override) at its own best buffer (the committed one here)
    assert rs.overrides_of(env, sets["r13_floor_runner_up"]) == {"entry_ttl_sec.c": 120}
    by = {c["id"]: c for c in comps}
    assert by["r12_c"]["styles"] == ["c"] and by["r12_c"]["runner"]["order_types"] == {"c": "stop_limit"}
    assert by["r12_d"]["winner"]["order_types"] == {"d": "marketable_limit"}
    assert by["r13_floor"]["runner"]["label"] == ["fixed", s["stop_buffer"]]
    assert by["r13_buffer"]["winner"]["order_types"] == {"c": "stop_limit", "d": "marketable_limit"}


def test_a_committed_vol_floor_is_read_from_the_settings(env):
    # step 6 committed row 13's vol floor and buffer (owner 2026-11-20: the floor is a setting): the winners are the
    # committed configuration's own floor, the fixed runner-up forces it off
    _records(env)
    env.settings["stop_floor_vol"] = VOL
    env.settings["stop_buffer"] = env.settings["stop_buffer"] + 2
    sets, _ = rs.comparisons(env)
    assert rs.committed(env)["stop_floor_vol"] == VOL
    assert rs.overrides_of(env, sets["winners"]) == {"entry_ttl_sec.c": 120}
    assert rs.overrides_of(env, sets["r13_floor_runner_up"]) == {"entry_ttl_sec.c": 120,
                                                                "stop_buffer": env.settings["stop_buffer"] - 2,
                                                                "stop_floor_vol": None}


def test_equal_points_are_one_label_set(env):
    # both r12 runners-up differ from the winners only by order type: their set is the winners'
    s = env.settings
    ttl = s["entry_ttl_sec"]
    _records(env, r12={"value": {"c": ["stop_limit", ttl["c"]], "d": ["limit", ttl["d"]]},
                       "lead": {"c": {"next_best": ["limit", ttl["c"]]}, "d": {"next_best": ["stop_limit", ttl["d"]]}}})
    sets, comps = rs.comparisons(env)
    assert "r12_runners_up" not in sets
    assert all(c["runner"]["set"] == "winners" for c in comps if c["row"] == "r12")


def test_missing_13c_winners_are_refused(env):
    atomic_write_json(s13c.winners_file(env), {"r12": {}})
    with pytest.raises(rs.RescoreRefused, match="r13"):
        rs.comparisons(env)


def _doc(months):
    days = [f"{LEDGER[m]}-0{d}" for m in months for d in (6, 7)]
    return {"key": "k13d", "months": list(months), "sessions": days}


def test_the_plan_checks_the_build_and_reads_months_1_to_4_only(env, monkeypatch):
    _records(env)
    monkeypatch.setattr(s13b, "build_doc_for", lambda env_, key: _doc(range(1, 13)))
    monkeypatch.setattr(s13b, "labels_plan_for", lambda env_, key, over=None, firings_ok=True:
                        {"label_key": "L" + str(abs(hash(repr(sorted((over or {}).items())))) % 10**6)})
    monkeypatch.setattr(s13c, "build_complete", lambda env_, key, days: False)
    with pytest.raises(rs.RescoreRefused, match="not complete"):
        rs.rescore_plan(env, "k13d")
    monkeypatch.setattr(s13c, "build_complete", lambda env_, key, days: True)
    plan = rs.rescore_plan(env, "k13d")
    assert plan["sessions"] == [d for d in _doc(range(1, 5))["sessions"]]          # months 1-4 of a 1-12 build
    assert len(plan["sets"]) == 4 and len({x["label_key"] for x in plan["sets"].values()}) == 4
    assert rs.chunk_ids(plan) == ["labels:k13d", "score:k13d"]
    assert rs.load_plan(env)["build_key"] == "k13d"
    assert rs.label_targets(plan)[0] == ("k13d", plan["sets"]["winners"]["overrides"])


def _build(days):
    evs, rows = [], {}
    for i, d in enumerate(days):
        for j in range(3):
            eid = f"e{i}{j}"
            evs.append({"event_id": eid, "day": d, "touch_group_id": f"g{eid}", "month": 1 + i, "tradeable": True,
                        "damaged": False, "held": True, "labelled": True,
                        "subset": {k: False for k in ("golden", "va_edge", "prev_profile", "node", "retest")}})
    return Build("k13d", None, list(days), {d: 1 + i for i, d in enumerate(days)}, evs, rows,
                 np.zeros((len(evs), 0)), [], [])


def _picks(build, r_by_style):
    out = []
    for k, e in enumerate(build.events):
        for s, r in r_by_style.items():
            for ot in ("limit", "stop_limit", "marketable_limit"):
                out.append({"day": e["day"], "decision_ts": 10**12 * (k + 1) + (0 if s == "c" else 1), "style": s,
                            "firing_n": 1, "decision_id": f"{e['event_id']}-{s}", "event_id": e["event_id"],
                            "touch_group_id": e["touch_group_id"] + s, "tradeable": True, "fill_status": "filled",
                            "removal_t": 0, "t_exit_none": 10**12 * (k + 1) + 2, "r_none": r,
                            "t_exit_fs": 10**12 * (k + 1) + 2, "r_fs": r / 2, "order_type": ot})
    return out


def test_the_score_is_the_runner_ups_excess_over_the_winner(env, monkeypatch):
    days = ["2025-10-06", "2025-11-06"]
    b = _build(days)
    p = {e["event_id"]: {"0": 0.1, "1": 0.6, "2": 0.9}[e["event_id"][-1]] for e in b.events}
    picks = {"LW": _picks(b, {"c": 1.0, "d": 0.5}), "LR": _picks(b, {"c": -1.0, "d": 0.5})}
    monkeypatch.setattr("nqbot.tuning.data.load_build", lambda env_, key, d, m: b)
    monkeypatch.setattr("nqbot.tuning.model.phold", lambda build, settings, threads, cache: (p, {}))
    monkeypatch.setattr("nqbot.tuning.data.load_picks", lambda env_, key, lkey, d, cache: picks[lkey])
    monkeypatch.setattr(s13b, "labelled", lambda env_, key, lkey, d: True)
    plan = {"build_key": "k13d", "sessions": days,
            "sets": {"winners": {"label_key": "LW", "overrides": {}}, "r12_runners_up": {"label_key": "LR",
                                                                                         "overrides": {"x": 1}}},
            "comparisons": [{"id": "r12_c", "row": "r12", "styles": ["c"],
                             "winner": {"set": "winners", "order_types": {"c": "stop_limit"}, "label": ["stop_limit", 120]},
                             "runner": {"set": "r12_runners_up", "order_types": {"c": "stop_limit"},
                                        "label": ["stop_limit", 300]},
                             "lead_13c": {"lead": 0.05, "lo": -5.1, "hi": 5.21}},
                            {"id": "r12_d", "row": "r12", "styles": ["d"],
                             "winner": {"set": "winners", "order_types": {"d": "limit"}, "label": ["limit", 60]},
                             "runner": {"set": "r12_runners_up", "order_types": {"d": "limit"}, "label": ["limit", 10]},
                             "lead_13c": {"lead": 0.0, "lo": -2.0, "hi": 2.0}}]}
    winners_before = s13c.winners(env)
    md = rs.score(env, plan, threads=1)
    text = md.read_text(encoding="utf-8")
    out = __import__("json").loads(md.with_suffix(".json").read_text(encoding="utf-8"))
    res = {r["id"]: r for r in out["results"]}
    # the bar: each month's median P(held) over all events (0.6); one event a month above it: 2 trades a style
    bar = cmp.bars(b, p, [e["event_id"] for e in b.events])
    rw, nw = cmp.fixed_events_series(b, picks["LW"], p, bar, {"c": "stop_limit"}, ("c",), "none")["sum"]
    rr, nr = cmp.fixed_events_series(b, picks["LR"], p, bar, {"c": "stop_limit"}, ("c",), "none")["sum"]
    want = cmp.score(rr, nr, rw, nw, 2.0, int(env.settings["tune_13"]["bootstrap_replicates"]))
    assert res["r12_c"]["none"]["diff"] == pytest.approx(want["diff"]) and res["r12_c"]["none"]["winner_trades"] == 2
    assert res["r12_c"]["none"]["diff"] < 0 and res["r12_c"]["verdict"] == "winner better (interval below 0)"
    assert res["r12_d"]["none"]["diff"] == pytest.approx(0.0)
    assert res["r12_d"]["verdict"] == "no clear difference (interval contains 0)"
    assert "| r12_c |" in text and "+0.05 [-5.10, +5.21]" in text
    assert s13c.winners(env) == winners_before                                   # 13c's records untouched
    assert md.parent == s13c.rep_dir(env) and md.name == "rescore_exec_k13d.md"


def test_a_month_outside_1_to_4_is_refused_by_the_score(env):
    plan = {"build_key": "k13d", "sessions": [f"{LEDGER[5]}-06"], "sets": {}, "comparisons": []}
    with pytest.raises(rs.months_mod.NotDevelopmentMonth):
        rs.score(env, plan, threads=1)


def test_the_job_labels_in_its_own_pool_then_scores(env, monkeypatch):
    plan = {"build_key": "k13d", "sessions": ["2025-10-06"], "sets": {"winners": {"overrides": {}, "label_key": "L"}}}
    monkeypatch.setattr(rs, "load_plan", lambda env_: plan)
    monkeypatch.setattr(s13b, "_job_env", lambda ctx: env)
    seen = {}
    monkeypatch.setattr(s13c, "label_jobs", lambda env_, targets, days: [("t", ["x"])] if targets else [])
    monkeypatch.setattr(s13c, "run_pool", lambda env_, ctx, tasks, workers, log_name: seen.update(
        tasks=tasks, log_name=log_name))
    monkeypatch.setattr(rs, "score", lambda env_, plan_: seen.update(scored=plan_["build_key"]) or env.paths.root)
    job = rs.RescoreJob()
    ctx = SimpleNamespace(log=SimpleNamespace(error=lambda *a: None))
    assert job.chunks(ctx) == ["labels:k13d", "score:k13d"]
    job.run_chunk("labels:k13d", ctx)
    job.run_chunk("score:k13d", ctx)
    assert seen == {"tasks": [("t", ["x"])], "log_name": "step13_rescore", "scored": "k13d"}
    from nqbot.jobs.runner import StopChunk
    with pytest.raises(StopChunk, match="not for the plan's build"):
        job.run_chunk("score:other", ctx)


def test_the_job_is_registered_and_outside_both_keys():
    from nqbot.checks import feature_build
    from nqbot.jobs.registry import JOBS

    assert JOBS["step13_rescore"] is rs.RescoreJob
    assert not any("checks/step13_rescore.py".startswith(p.rstrip("/")) for p in feature_build.CODE_PARTS)
