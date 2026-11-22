"""After-13c knock-on check (owner 2026-11-22): `step13 buildcheck-plan` / job `step13_buildcheck` /
`buildcheck-report`: a 13c build row's start value against a challenger, both months 1-4 builds under the committed
settings, scored with 13c's method; nothing written to the configuration or 13c's records. Synthetic records and
builds only."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from nqbot.checks import step13_buildcheck as bc
from nqbot.checks import step13b as s13b
from nqbot.checks import step13c as s13c
from nqbot.storage import atomic_write_json
from nqbot.tuning import compare as cmp
from tests.test_feature_build import LEDGER, env  # noqa: F401  (fixture)
from tests.test_step13c import _synthetic

R04 = {"id": "r04", "name": "sweep_reclaim_bars", "kind": "build", "values": [1, 2, 3, 5], "start": 3, "rule": "best",
       "mode": "c", "subset": None, "keys": ["sweep_reclaim_bars"], "note": ""}


def _tune_plan(env, rows=(R04,)):
    atomic_write_json(s13c.plan_file(env), {"program": list(rows)})


def _doc(key, overrides, months=range(1, 13)):
    days = [f"{LEDGER[m]}-0{d}" for m in months for d in (6, 7)]
    return {"key": key, "months": list(months), "sessions": days, "overrides": overrides}


def _wire(env, monkeypatch, docs, complete=True):
    monkeypatch.setattr(s13b, "build_doc_for", lambda env_, key: docs[key])
    monkeypatch.setattr(s13b, "labels_plan_for", lambda env_, key, over=None, firings_ok=True: {"label_key": "L" + key})
    monkeypatch.setattr(s13c, "build_complete", lambda env_, key, days: complete)


def test_the_plan_takes_the_row_from_13c_and_the_values_from_the_builds(env, monkeypatch):
    _tune_plan(env)
    docs = {"K3": _doc("K3", {"sweep_reclaim_bars": 3}), "K2": _doc("K2", {})}
    _wire(env, monkeypatch, docs)
    plan = bc.buildcheck_plan(env, "r04", "K3", "K2")
    assert plan["row"]["mode"] == "c" and plan["row"]["id"] == "r04"
    assert plan["start"] == {"key": "K3", "value": {"sweep_reclaim_bars": 3}, "overrides": {"sweep_reclaim_bars": 3},
                             "label_key": "LK3"}
    # the challenger has no override: its value is the committed one
    assert plan["challenger"]["value"] == {"sweep_reclaim_bars": env.features["sweep_reclaim_bars"]}
    assert plan["order_types"] == {s: env.settings["entry_order_type"][s] for s in ("c", "d")}
    assert plan["sessions"] == _doc("K3", {}, range(1, 5))["sessions"]              # months 1-4 of a 1-12 build
    assert bc.load_plan(env)["challenger"]["key"] == "K2"
    assert bc.label_targets(plan) == [("K3", {}), ("K2", {})]                        # the committed labeler settings


@pytest.mark.parametrize("docs, args, match", [
    ({"K3": _doc("K3", {"sweep_reclaim_bars": 3}), "K2": _doc("K2", {})}, ("r04", "K3", "K3"), "same build"),
    ({"K3": _doc("K3", {"sweep_reclaim_bars": 3, "zone_proximity": 8}), "K2": _doc("K2", {})}, ("r04", "K3", "K2"),
     "only the row's"),
    ({"K3": _doc("K3", {"sweep_reclaim_bars": 2}), "K2": _doc("K2", {})}, ("r04", "K3", "K2"), "both builds have"),
    ({"K3": _doc("K3", {"sweep_reclaim_bars": 3}), "K2": _doc("K2", {})}, ("r12", "K3", "K2"), "no row r12"),
])
def test_the_plan_refuses_builds_that_are_not_a_clean_pair(env, monkeypatch, docs, args, match):
    _tune_plan(env)
    monkeypatch.setitem(env.features, "sweep_reclaim_bars", 2)
    _wire(env, monkeypatch, docs)
    with pytest.raises(bc.BuildCheckRefused, match=match):
        bc.buildcheck_plan(env, *args)


def test_the_plan_refuses_an_incomplete_build_and_a_non_build_row(env, monkeypatch):
    _tune_plan(env, (R04, {**R04, "id": "r12", "kind": "exec_ttl", "keys": []}))
    docs = {"K3": _doc("K3", {"sweep_reclaim_bars": 3}), "K2": _doc("K2", {})}
    _wire(env, monkeypatch, docs, complete=False)
    with pytest.raises(bc.BuildCheckRefused, match="not complete"):
        bc.buildcheck_plan(env, "r04", "K3", "K2")
    with pytest.raises(bc.BuildCheckRefused, match="not a build row"):
        bc.buildcheck_plan(env, "r12", "K3", "K2")


def test_a_changed_labeler_setting_refuses_the_plan_later(env, monkeypatch):
    _tune_plan(env)
    docs = {"K3": _doc("K3", {"sweep_reclaim_bars": 3}), "K2": _doc("K2", {})}
    _wire(env, monkeypatch, docs)
    bc.buildcheck_plan(env, "r04", "K3", "K2")
    monkeypatch.setattr(s13b, "labels_plan_for", lambda env_, key, over=None, firings_ok=True: {"label_key": "new"})
    with pytest.raises(bc.BuildCheckRefused, match="labeler"):
        bc.load_plan(env)


def test_the_rule_is_the_best_value_outright_with_the_start_counting_0():
    assert bc.verdict({"diff": 0.01, "lo": -5.0, "hi": 5.0}) == "challenger"     # no interval test (13c's best rule)
    assert bc.verdict({"diff": -0.01, "lo": -5.0, "hi": 5.0}) == "start"
    assert bc.verdict({"diff": 0.0, "lo": -5.0, "hi": 5.0}) == "start"           # not above 0: the start value stays


@pytest.mark.parametrize("shift, winner", [(0.6, "challenger"), (-0.6, "start")])
def test_the_score_is_13cs_build_row_comparison_on_the_rows_run(env, monkeypatch, tmp_path, shift, winner):
    _, b0, p, picks, days = _synthetic(tmp_path, monkeypatch)
    pk_ = {"LK3": picks(b0), "LK2": picks(b0, shift)}
    monkeypatch.setattr("nqbot.tuning.data.load_build", lambda env_, key, d, m: b0)
    monkeypatch.setattr("nqbot.tuning.model.phold", lambda build, settings, threads, cache: (p, {}))
    monkeypatch.setattr("nqbot.tuning.data.load_picks", lambda env_, key, lkey, d, cache: pk_[lkey])
    monkeypatch.setattr(s13b, "labelled", lambda env_, key, lkey, d: True)
    ot = {"c": "stop_limit", "d": "marketable_limit"}
    plan = {"row": R04, "start": {"key": "K3", "value": {"sweep_reclaim_bars": 3}, "label_key": "LK3"},
            "challenger": {"key": "K2", "value": {"sweep_reclaim_bars": 2}, "label_key": "LK2"},
            "order_types": ot, "sessions": days}
    atomic_write_json(s13c.winners_file(env), {"r04": {"value": 2}})
    md = bc.score(env, plan, threads=1)
    out = json.loads(md.with_suffix(".json").read_text(encoding="utf-8"))
    reps = int(env.settings["tune_13"]["bootstrap_replicates"])
    want = cmp.compare(b0, b0, p, p, pk_["LK3"], pk_["LK2"], ot, "c", None, reps, extra_modes=("combined", "d"))
    assert out["lead"]["diff"] == pytest.approx(want["rules"]["none"]["diff"])           # (c) only, rule none
    assert out["lead"]["lo"] == pytest.approx(want["rules"]["none"]["lo"])
    assert {"none", "fs", "combined:none", "d:none"} <= set(out["result"]["rules"])
    assert out["winner"] == winner and (out["lead"]["diff"] > 0) == (winner == "challenger")
    assert out["winner_value"] == ({"sweep_reclaim_bars": 2} if winner == "challenger" else {"sweep_reclaim_bars": 3})
    text = md.read_text(encoding="utf-8")
    assert "c (decides)" in text and "**Result:**" in text
    assert md.parent == s13c.rep_dir(env) and md.name == "buildcheck_r04_K3_K2.md"
    assert s13c.winners(env) == {"r04": {"value": 2}}                                    # 13c's records untouched


def test_a_month_outside_1_to_4_is_refused_by_the_score(env):
    plan = {"row": R04, "start": {"key": "K3", "label_key": "L"}, "challenger": {"key": "K2", "label_key": "L"},
            "order_types": {}, "sessions": [f"{LEDGER[5]}-06"]}
    with pytest.raises(bc.months_mod.NotDevelopmentMonth):
        bc.score(env, plan, threads=1)


def test_the_job_labels_both_builds_in_its_own_pool_then_scores(env, monkeypatch):
    plan = {"row": R04, "start": {"key": "K3"}, "challenger": {"key": "K2"}, "sessions": ["2025-10-06"]}
    monkeypatch.setattr(bc, "load_plan", lambda env_: plan)
    monkeypatch.setattr(s13b, "_job_env", lambda ctx: env)
    seen = {}
    monkeypatch.setattr(s13c, "label_jobs", lambda env_, targets, days: seen.update(targets=targets) or [("t", ["x"])])
    monkeypatch.setattr(s13c, "run_pool", lambda env_, ctx, tasks, workers, log_name: seen.update(
        tasks=tasks, log_name=log_name))
    monkeypatch.setattr(bc, "score", lambda env_, plan_: seen.update(scored=True) or env.paths.root)
    job = bc.BuildCheckJob()
    ctx = SimpleNamespace(log=SimpleNamespace(error=lambda *a: None))
    assert job.chunks(ctx) == ["labels", "score"]
    job.run_chunk("labels", ctx)
    job.run_chunk("score", ctx)
    assert seen == {"targets": [("K3", {}), ("K2", {})], "tasks": [("t", ["x"])], "log_name": "step13_buildcheck",
                    "scored": True}
    from nqbot.jobs.runner import StopChunk
    with pytest.raises(StopChunk, match="unknown chunk"):
        job.run_chunk("other", ctx)


def test_the_job_is_registered_and_outside_both_keys():
    from nqbot.checks import feature_build
    from nqbot.jobs.registry import JOBS

    assert JOBS["step13_buildcheck"] is bc.BuildCheckJob
    assert not any("checks/step13_buildcheck.py".startswith(p.rstrip("/")) for p in feature_build.CODE_PARTS)
