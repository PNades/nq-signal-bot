"""Owner 2026-11-13 review (labeler side, outside both keys): a failed identity check is never taken as a labelled
session, and a slippage day made with other settings is made again."""

from __future__ import annotations

import json
from datetime import date

import pytest

from nqbot.checks import step13b as s13
from nqbot.events.common import read_meta, write_table
from tests.test_feature_build import env  # noqa: F401  (fixture)

DAY = date(2025, 10, 8)


def _fake_labels(env, monkeypatch, identity_ok):
    doc = {"key": "bk"}
    plan = {"sessions": [DAY.isoformat()], "label_key": "lk", "overrides": None}
    monkeypatch.setattr(s13, "load_labels_plan", lambda env_: (plan, doc, object()))
    monkeypatch.setattr(s13.months_mod, "require_development", lambda d, s: None)
    monkeypatch.setattr(s13, "out_dir", lambda env_, d: env.paths.data_dir / "processed" / "features" / "v_bk")
    monkeypatch.setattr(s13, "label_one", lambda *a, **k: {
        "pairs": [{"a": 1}], "fills": [{"a": 1}], "paths": [{"a": 1}],
        "info": {"pairs": 1, "label_sec": 0.1, "identity_ok": identity_ok, "identity_rows": 1, "ext_rows": 0}})
    return doc, plan


def test_a_failed_identity_check_is_not_a_labelled_session(env, monkeypatch):
    doc, plan = _fake_labels(env, monkeypatch, identity_ok=False)
    lp_ = s13.label_paths(env, doc, "lk", DAY)
    with pytest.raises(s13.Refused, match="low-RR extension"):
        s13.labels_day(env, DAY)
    assert not lp_["summary"].exists()                                      # the job would take it as done
    failed = s13.identity_failed_file(lp_["summary"])
    assert json.loads(failed.read_text(encoding="utf-8"))["identity_ok"] is False
    report = s13.labels_report(env).read_text(encoding="utf-8")
    assert "all identical: False (FAILED: 2025-10-08)" in report
    # fixed: the session labels, the failure record goes
    _fake_labels(env, monkeypatch, identity_ok=True)
    s13.labels_day(env, DAY)
    assert lp_["summary"].exists() and not failed.exists()
    assert "all identical: True" in s13.labels_report(env).read_text(encoding="utf-8")


def test_slippage_days_carry_their_settings_digest(env, tmp_path):
    f = tmp_path / "d.parquet"
    write_table(f, [], {"day": DAY.isoformat(), "key": "bk", "slip_digest": s13.slip_digest(env)})
    assert s13.slip_day_current(env, f)
    write_table(f, [], {"day": DAY.isoformat(), "key": "bk"})                 # made before the digest: made again
    assert not s13.slip_day_current(env, f)
    write_table(f, [], {"day": DAY.isoformat(), "key": "bk", "slip_digest": "x" * 12})   # other settings
    assert not s13.slip_day_current(env, f) and read_meta(f)["slip_digest"] != s13.slip_digest(env)
    assert not s13.slip_day_current(env, tmp_path / "missing.parquet")


def test_the_slippage_digest_follows_the_settings(env):
    a = s13.slip_digest(env)
    env.settings["entry_order_type"] = {**env.settings["entry_order_type"], "d": "limit_center"}
    assert s13.slip_digest(env) != a
