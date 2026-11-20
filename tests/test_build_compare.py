"""`nqbot step13 compare-builds` (owner 2026-11-19, the pre-13d proof of SPEC §13 Q-review-stack (b)): two builds'
files compared session by session, on synthetic build folders only."""

from __future__ import annotations

import copy
import json

import pytest

from nqbot import calendar as cal_mod
from nqbot.checks import build_compare as bc
from nqbot.checks import feature_build as fb
from nqbot.checks.step8b import Env
from nqbot.config import load_features
from nqbot.months import NotDevelopmentMonth
from nqbot.storage import atomic_write_json

LEDGER = {k: (f"2025-{k + 9:02d}" if k <= 3 else f"2026-{k - 3:02d}") for k in range(1, 13)}
DAYS = ("2025-10-07", "2025-10-08")


@pytest.fixture
def env(paths, settings, log):
    s = copy.deepcopy(settings)
    s["months"] = dict(LEDGER)
    return Env(settings=s, features=load_features(), paths=paths, log=log, cal=cal_mod.load(), echo=lambda *_: None)


def make_build(env, key, version=39, days=DAYS, new_col=False, touch_change=None, sec=1.0, extra_dir=None):
    root = env.paths.data_dir / "processed" / "features" / f"v{version}_{key}"
    for i, iso in enumerate(days):
        rows = [{"event_id": f"e{i}-1", "stack_count": 2, "zone_width": 12.5, "role": "floor",
                 "founding_level_ids": ["a", "b"], "nan_col": float("nan")},
                {"event_id": f"e{i}-2", "stack_count": 1, "zone_width": 8.0, "role": "ceiling",
                 "founding_level_ids": [], "nan_col": None}]
        if new_col:
            rows[0]["stack_level_ids"], rows[1]["stack_level_ids"] = ["a", "b"], ["c"]
        if touch_change and touch_change[0] == iso:
            rows[1][touch_change[1]] = touch_change[2]
        fb.write_rows(root / "touches" / f"{iso}.parquet", rows, {"key": key})
        fb.write_rows(root / "events" / f"{iso}.parquet", [{"event_id": r["event_id"], "label": "held"} for r in rows],
                      {"key": key})
        if extra_dir:
            fb.write_rows(root / extra_dir / f"{iso}.parquet", [{"x": 1}], {"key": key})
        atomic_write_json(root / "state" / f"state_{iso}.json", {"day": iso, "nq": {"rth": [1, 2]}})
        atomic_write_json(root / "days" / f"{iso}.json", {"pass1": {"build_sec": sec, "events": 2},
                                                          "pass2": {"total_sec": sec * 3, "n": 2}})
    # the labeler's folders are not build files
    fb.write_rows(root / "pairs" / "lk" / f"{days[0]}.parquet", [{"p": key}], {})
    return root


def test_a_record_only_column_passes_and_is_reported(env):
    make_build(env, "aaa")
    make_build(env, "bbb", version=40, new_col=True, sec=99.0)       # timings and the version differ: not compared
    path, ok = bc.report(env, "aaa", "bbb", ["stack_level_ids"])
    text = path.read_text(encoding="utf-8")
    assert ok and "PASS" in text and "`stack_level_ids` in 2 files, 4 rows" in text
    assert "| touches | 2 | 0 |" in text and "| state | 2 | 0 |" in text and "| days | 2 | 0 |" in text


def test_a_changed_value_or_an_unlisted_column_fails(env):
    make_build(env, "aaa")
    make_build(env, "bbb", new_col=True, touch_change=(DAYS[1], "zone_width", 9.0))
    res = bc.compare(env, "aaa", "bbb", ["stack_level_ids"])
    assert not res["ok"] and res["per"]["touches"]["differ"] == 1
    assert any("column zone_width: 1 rows differ" in x for x in res["examples"])
    make_build(env, "ccc", new_col=True)
    res = bc.compare(env, "aaa", "ccc", [])                          # the new column not allowed
    assert not res["ok"] and any("new columns not allowed: stack_level_ids" in x for x in res["examples"])


def test_an_allowed_column_that_never_appears_fails(env):
    make_build(env, "aaa")
    make_build(env, "bbb")
    res = bc.compare(env, "aaa", "bbb", ["stack_level_ids"])
    assert not res["ok"] and res["unseen"] == ["stack_level_ids"] and not res["examples"]


def test_state_and_summaries_and_missing_folders_are_checked(env):
    make_build(env, "aaa")
    root = make_build(env, "bbb", new_col=True, extra_dir="decision_rows")
    atomic_write_json(root / "state" / f"state_{DAYS[0]}.json", {"day": DAYS[0], "nq": {"rth": [1, 3]}})
    atomic_write_json(root / "days" / f"{DAYS[1]}.json", {"pass1": {"build_sec": 5, "events": 3},
                                                          "pass2": {"total_sec": 1, "n": 2}})
    res = bc.compare(env, "aaa", "bbb", ["stack_level_ids"])
    assert res["per"]["state"]["differ"] == 1 and res["per"]["days"]["differ"] == 1
    assert res["per"]["decision_rows"]["differ"] == 2 and any("only in B" in x for x in res["examples"])


def test_refusals(env):
    make_build(env, "aaa", days=DAYS[:1])
    make_build(env, "bbb")
    with pytest.raises(bc.CompareRefused, match="lacks 1"):
        bc.compare(env, "aaa", "bbb")
    with pytest.raises(bc.CompareRefused, match="no folder"):
        bc.compare(env, "aaa", "zzz")
    make_build(env, "sealed", days=("2026-08-12",))                   # ledger month 11
    make_build(env, "sealed2", days=("2026-08-12",))
    with pytest.raises(NotDevelopmentMonth):
        bc.compare(env, "sealed", "sealed2")


def test_nan_and_nested_values_compare_by_value():
    assert bc.same_value(float("nan"), float("nan")) and not bc.same_value(1.0, 2.0)
    assert bc.same_value({"a": [1, float("nan")]}, {"a": [1, float("nan")]})
    assert bc.strip_timings({"x_sec": 1, "y": {"z_sec": 2, "w": 3}}) == {"y": {"w": 3}}
    assert json.dumps(bc.strip_timings([{"a_sec": 1}])) == "[{}]"
