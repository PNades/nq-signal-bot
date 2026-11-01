"""The feature-build job (step 11 part 3): plan, chunk ids, the build key, the pass-2 checkpoint check, the sealed-month
report guard (§7 rule 2b), parquet output, and the part-3 state carried across a session (checkpoint round trip)."""

from __future__ import annotations

import copy
import json
from datetime import date, timedelta

import pyarrow.parquet as pq
import pytest

from nqbot import calendar as cal_mod
from nqbot.checks import feature_build as fb
from nqbot.checks import session_build as sb
from nqbot.checks.step8b import Env
from nqbot.config import load_features
from nqbot.features.carry import CarryState
from nqbot.storage import atomic_write_json

LEDGER = {k: (f"2025-{k + 9:02d}" if k <= 3 else f"2026-{k - 3:02d}") for k in range(1, 13)}


@pytest.fixture
def env(paths, settings, log):
    s = copy.deepcopy(settings)
    s["months"] = dict(LEDGER)
    return Env(settings=s, features=load_features(), paths=paths, log=log, cal=cal_mod.load(), echo=lambda *_: None)


def write_backfill_plan(env, days):
    atomic_write_json(env.paths.data_dir / "jobs" / "databento_backfill" / "plan.json",
                      {"front": {"NQ": {d.isoformat(): "NQZ5" for d in days}, "ES": {d.isoformat(): "ESZ5" for d in days}}})


def trading_days(a: date, b: date, cal) -> list[date]:
    out, d = [], a
    while d <= b:
        if cal_mod.is_trading_day(d, cal):
            out.append(d)
        d += timedelta(days=1)
    return out


def test_parse_months():
    assert fb.parse_months("1-12") == list(range(1, 13))
    assert fb.parse_months("1-4,7") == [1, 2, 3, 4, 7]
    with pytest.raises(fb.BuildRefused):
        fb.parse_months("0-3")


def test_plan_chunks_and_key(env):
    days = trading_days(date(2025, 10, 1), date(2026, 1, 31), env.cal)
    write_backfill_plan(env, days)
    doc = fb.make_plan(env, [1, 2], batch=4)
    want = [d.isoformat() for d in days if date(2025, 10, 1) <= d <= date(2025, 11, 30)]
    assert doc["sessions"] == want
    ids = fb.chunk_ids(doc)
    p1 = [c for c in ids if c.startswith("p1:")]
    p2 = [c for c in ids if c.startswith("p2:")]
    assert len(p1) == len(want) and len(p2) == -(-len(want) // 4)
    assert ids == [f"p0:warmup:{doc['key']}"] + p1 + p2
    assert all(c.endswith(doc["key"]) for c in ids)
    assert fb.load_plan(env)["key"] == doc["key"]
    # any configuration change renames every chunk: the plan refuses until re-planned
    env.features = {**env.features, "zone_proximity": env.features["zone_proximity"] + 1}
    with pytest.raises(fb.BuildRefused, match="re-plan"):
        fb.load_plan(env)
    assert fb.build_key(env) != doc["key"]


def test_report_lists_development_dates_only(env):
    days = trading_days(date(2026, 3, 2), date(2026, 5, 29), env.cal)   # months 6 (dev), 7, 8 (walk-forward)
    write_backfill_plan(env, days)
    doc = fb.make_plan(env, [6, 7, 8], batch=4)
    key = doc["key"]
    for d in days:
        atomic_write_json(fb.summary_file(env, key, d), {"pass2": {"events": 10, "rows": 10, "tradeable": 3,
                                                                   "labels": {"held": 6, "broke": 3, "timeout": 1},
                                                                   "day_type": "range", "build_sec": 1, "total_sec": 2,
                                                                   "skylit": "ok"}})
    text = fb.write_report(env, doc).read_text(encoding="utf-8")
    for d in days:
        if d.month == 3:
            assert d.isoformat() in text
        else:
            assert d.isoformat() not in text, f"{d} (a walk-forward month) must appear as a count only"
    assert "| 7 |" in text and "| 8 |" in text


def test_write_rows_float32_and_nulls(tmp_path):
    p = tmp_path / "x.parquet"
    fb.write_rows(p, [{"event_id": "a", "ts": 1_000_000_000_000_000_001, "x": 1.5, "flag": True, "s": None},
                      {"event_id": "b", "ts": 2, "x": None, "flag": False, "s": None}], {"day": "d"})
    t = pq.read_table(p)
    assert str(t.schema.field("x").type) == "float" and str(t.schema.field("ts").type) == "int64"
    assert t.column("ts").to_pylist()[0] == 1_000_000_000_000_000_001
    assert json.loads(t.schema.metadata[b"nqbot"])["day"] == "d"


def test_state_round_trip_and_digest():
    c = CarryState("NQ", retested=[{"level_id": "r", "price": 1.0, "type": "pdh", "session": "2026-03-05",
                                    "created_ns": 5}], touch_history=[{"t_touch": 1}], yesterday_day_type="trend")
    s = sb.DayState("2026-03-06", c.to_json(), CarryState("ES").to_json(), {"2026-03-06": [1.0, 2.0]})
    back = sb.DayState.from_json(json.loads(json.dumps(s.to_json())))
    assert back.digest() == s.digest()
    # a session mutating its carry never changes the state it started from
    c2 = CarryState.from_json(s.nq)
    c2.retested.append({"x": 1})
    assert len(s.nq["retested"]) == 1


def test_roll_shifts_retested_extremes():
    c = CarryState("NQ", retested=[{"level_id": "r", "price": 20_000.0, "type": "pdh", "session": "2026-03-05",
                                    "created_ns": 5}], retest_candidates=[{"price": 19_000.0, "type": "pdl",
                                                                          "session": "2026-03-05"}])
    c.shift(250.0)
    assert c.retested[0]["price"] == 20_250.0 and c.retest_candidates[0]["price"] == 19_250.0


class FakeSF:
    """Stands in for a built session (the real engine is covered by the lookahead and smoke tests)."""

    def __init__(self, n):
        from types import SimpleNamespace

        self.events = list(range(n))
        self.passes = 1
        self.book = SimpleNamespace(created={"z1": 0}, recomputes=3)
        self.es_book = SimpleNamespace(created={})
        self.labels = {}

    def touch_rows(self):
        return [{"event_id": f"e{k}", "ts": k, "tradeable": k % 2 == 0, "x": float(k)} for k in self.events]

    def daytype_rows(self):
        return [{"day": "d", "t": 1, "vwap_crosses": 0}]

    def outcome_rows(self):
        return [{"event_id": f"e{k}", "label": "held"} for k in self.events]

    def eod(self):
        return {"day_type": "range"}


def test_two_passes_with_a_fake_build(env, monkeypatch):
    days = trading_days(date(2025, 10, 1), date(2025, 10, 10), env.cal)
    write_backfill_plan(env, days)
    doc = fb.make_plan(env, [1], batch=2)
    plan_days = [date.fromisoformat(x) for x in doc["sessions"]]
    seen = []

    def fake_build(env_, day, state, pl, planned, damage, digest_salt=[""]):
        seen.append((day, state.day))
        out = sb.DayState(day.isoformat(), {"n": (state.nq.get("n", 0) + 1)}, {}, {})
        return sb.Built(FakeSF(3), out, {"skylit": "ok"})
    monkeypatch.setattr(sb, "build_day", fake_build)
    monkeypatch.setattr(sb, "warmup_state", lambda *a, **k: sb.DayState("warm", {"n": 0}, {}, {}))
    monkeypatch.setattr(sb, "load_damage", lambda env_: {})
    # the first session needs the warm-up state (chunk p0) first
    with pytest.raises(fb.BuildRefused, match="warm-up"):
        fb.build_day_cmd(env, plan_days[0], 1)
    assert fb.build_warmup_cmd(env) == 0 and fb.warmup_file(env, doc["key"]).exists()
    # pass 2 of a session refuses before pass 1 wrote its checkpoint; pass 1 runs in order
    with pytest.raises(fb.BuildRefused, match="pass 1"):
        fb.build_day_cmd(env, plan_days[0], 2)
    with pytest.raises(fb.BuildRefused, match="previous session"):
        fb.build_day_cmd(env, plan_days[1], 1)
    for d in plan_days[:2]:
        assert fb.build_day_cmd(env, d, 1) == 0
    assert seen[0][1] == "warm" and seen[1][1] == plan_days[0].isoformat()
    for d in plan_days[:2]:
        assert fb.build_day_cmd(env, d, 2) == 0
        assert (fb.out_dir(env, doc["key"]) / "touches" / f"{d.isoformat()}.parquet").exists()
    t = pq.read_table(fb.out_dir(env, doc["key"]) / "touches" / f"{plan_days[0].isoformat()}.parquet")
    assert t.num_rows == 3
    s = json.loads(fb.summary_file(env, doc["key"], plan_days[0]).read_text(encoding="utf-8"))
    assert s["pass1"]["events"] == 3 and s["pass2"]["rows"] == 3 and s["pass2"]["day_type"] == "range"
    # a pass 2 whose end state differs from pass 1's checkpoint is refused
    monkeypatch.setattr(sb, "build_day", lambda *a, **k: sb.Built(FakeSF(1), sb.DayState("x", {"n": 99}, {}, {}),
                                                                   {}))
    with pytest.raises(fb.BuildRefused, match="differs"):
        fb.build_day_cmd(env, plan_days[1], 2)


def test_full_warmup_builds_after_the_first_fold(env, monkeypatch):
    days = trading_days(date(2025, 9, 2), date(2025, 9, 12), env.cal)
    calls = []

    def fold(env_, pl, d, state, damage):
        calls.append(("fold", d))
        return sb.DayState(d.isoformat(), {"rth": [1]}, {"rth": [1]}, {})

    def build(env_, d, state, pl, planned, damage):
        calls.append(("build", d))
        return sb.Built(None, sb.DayState(d.isoformat(), state.nq, state.es, {}), {})
    monkeypatch.setattr(sb, "fold_only", fold)
    monkeypatch.setattr(sb, "build_day", build)
    nxt = date(2025, 9, 15)
    st = sb.warmup_state(env, {}, days + [nxt], nxt, {}, full=True)
    assert calls[0] == ("fold", days[0]) and all(k == "build" for k, _ in calls[1:]) and len(calls) == len(days)
    assert st.day == days[-1].isoformat()
    calls.clear()
    sb.warmup_state(env, {}, days + [nxt], nxt, {}, full=False)
    assert all(k == "fold" for k, _ in calls)


def test_read_rows_fills_columns_a_day_lacks(tmp_path):
    a, b = tmp_path / "a.parquet", tmp_path / "b.parquet"
    fb.write_rows(a, [{"event_id": "x", "qqq_lean": 0.5}], {})
    fb.write_rows(b, [{"event_id": "y", "skylit_missing": True}], {})
    t = fb.read_rows([a, b])
    assert t.num_rows == 2 and t.column("qqq_lean").to_pylist()[1] is None
    assert t.column("skylit_missing").to_pylist() == [None, True]
