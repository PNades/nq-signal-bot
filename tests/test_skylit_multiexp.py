"""Multi-expiration snapshots (owner 2026-10-23): plan costs and refusals, tier-by-tier pull, cache, budget, coverage."""

from __future__ import annotations

import contextlib
import json
from dataclasses import asdict
from datetime import date, timedelta

import pytest

from nqbot.clients import skylit_multiexp as mx
from nqbot.clients.skylit import CallRecord, SkylitError
from nqbot.config import load_features
from nqbot.jobs.runner import load_state, run_job, state_path

MONTHS = {i + 1: f"{2025 + (9 + i) // 12}-{(9 + i) % 12 + 1:02d}" for i in range(12)}  # 2025-10 .. 2026-09


def _body(at: str, same_day: bool = True) -> dict:
    return {"data": {"symbols": [{"symbol": r, "asOf": at, "spot": 1.0, "expirations": ["2026-01-02"],
                                  "strikes": [{"strike": 1, "value": 2.0}], "matrix": [[2.0]]} for r in mx.ROOTS]},
            "meta": {"resolution": "1s"}}


class FakeCredits:
    def __init__(self, remaining):
        self.remaining = remaining


class FakeClient:
    def __init__(self, store, balance=12_000, fail_on=()):
        self.store, self.credits, self.fail_on = store, FakeCredits(balance), set(fail_on)
        self.calls: list[str] = []
        self.context: dict = {}

    @contextlib.contextmanager
    def tagged(self, **kw):
        saved, self.context = self.context, {**self.context, **kw}
        try:
            yield
        finally:
            self.context = saved

    def get(self, path, params):
        self.calls.append(params["at"])
        if params["at"] in self.fail_on:
            rec = CallRecord(path, params, 404, 1, 10, None, 0, "t", "404 no_data", dict(self.context))
            self.store.log_call(rec)
            raise SkylitError(404, "no_data", "no snapshot")
        self.credits.remaining -= mx.CREDITS_PER_CALL
        rec = CallRecord(path, params, 200, 1, 10, self.credits.remaining, mx.CREDITS_PER_CALL, "t", None,
                         dict(self.context))
        self.store.log_call(rec)
        return _body(params["at"]), rec


@pytest.fixture
def setup(settings, paths):
    settings["months"] = MONTHS
    return settings, paths, load_features()


def _plan(settings, paths, features, tiers, end, approve=10_000, balance=12_000):
    out: list[str] = []
    plan = mx.make_plan(paths, settings, features, tiers, end, approve, balance, echo=out.append, cal=None)
    return plan, "\n".join(out)


def test_sessions_are_the_warmup_then_every_session_to_the_end(setup):
    settings, _, features = setup
    days = mx.sessions(settings, features, date(2025, 10, 10))
    assert days[:21] == [d for d in days if d < date(2025, 10, 1)] and len(days[:21]) == 21
    assert days[0] == date(2025, 9, 2) and days[20] == date(2025, 9, 30)
    assert days[21:] == [date(2025, 10, d) for d in (1, 2, 3, 6, 7, 8, 9, 10)]


def test_plan_prints_each_tier_and_refuses_what_is_not_approved(setup):
    settings, paths, features = setup
    end = date(2026, 10, 23)
    n = len(mx.sessions(settings, features, end))
    with pytest.raises(mx.MultiExpRefused, match="needs the owner's OK"):
        _plan(settings, paths, features, ["0925", "1000"], end, approve=None)
    plan, text = _plan(settings, paths, features, ["1000", "0925"], end, approve=2 * n * 5)
    assert plan["tiers"] == ["0925", "1000"]  # the owner's priority order whatever was typed
    assert load_state(state_path(paths, mx.JOB_NAME))["chunks"] == mx.chunk_ids(plan)  # owner 2026-10-30
    assert len(mx.chunk_ids(plan)) == 2 * n and "(0/" in text
    assert f"tier 0925 ET: {n} calls" in text and f"cumulative {2 * n * 5:,}" in text
    with pytest.raises(mx.MultiExpRefused, match="reserve"):
        _plan(settings, paths, features, ["0925"], end, approve=n * 5, balance=n * 5 + 500)
    with pytest.raises(mx.MultiExpRefused, match="unknown tiers"):
        _plan(settings, paths, features, ["0930"], end, approve=None)


def test_plan_refuses_over_the_hard_cap(setup, monkeypatch):
    settings, paths, features = setup
    monkeypatch.setattr(mx, "HARD_CAP_CREDITS", 100)
    with pytest.raises(mx.MultiExpRefused, match="hard cap"):
        _plan(settings, paths, features, ["0925"], date(2025, 10, 31), approve=10_000)


def test_test_calls_join_the_store_and_are_not_paid_again(setup):
    settings, paths, features = setup
    test_dir = mx.base(paths) / "test"
    test_dir.mkdir(parents=True)
    (test_dir / f"2025-10-01_0925_{'-'.join(mx.ROOTS)}.json").write_text(json.dumps(_body("2025-09-30T20:00:00Z")))
    (test_dir / "2025-10-01_0925_QQQ.json").write_text("{}")  # other symbols: not the same call
    plan, text = _plan(settings, paths, features, ["0925"], date(2025, 10, 2))
    assert "test calls reused: 0925:2025-10-01" in text
    assert mx.read_snapshot(mx.snapshot_path(paths, "0925", date(2025, 10, 1)))["body"]["meta"]["resolution"] == "1s"
    assert "tier 0925 ET: 22 calls" in text  # 21 warm-up + 10-02


def _run(settings, paths, log, clock, fake):
    return run_job(mx.MultiExpJob(lambda s, store, lg: fake.setdefault("c", FakeClient(store, **fake["kw"]))),
                   settings, paths, until=clock.now() + timedelta(hours=8), log=log, now=clock.now,
                   sleep=clock.sleep, is_trading_day=lambda d: True)


def test_job_pulls_tier_by_tier_caches_and_reports(setup, log, clock):
    settings, paths, features = setup
    end = date(2025, 10, 2)
    plan, _ = _plan(settings, paths, features, ["0925", "1000"], end)
    fail_at = mx.iso_utc(mx.at_of("1000", date(2025, 9, 3)))
    fake = {"kw": {"fail_on": {fail_at}}}
    res = _run(settings, paths, log, clock, fake)
    assert res.status == "complete"
    calls = fake["c"].calls
    assert len(calls) == 2 * 23 and calls[0].endswith("13:25:00.000Z") and calls[23].endswith("14:00:00.000Z")
    doc = mx.read_snapshot(mx.snapshot_path(paths, "1000", end))
    assert doc["params"]["layout"] == "matrix" and doc["params"]["maxExpirations"] == "all"
    assert doc["call"]["context"]["plan_id"] == plan["plan_id"]
    report = (paths.reports_dir / "skylit_multiexp" / "coverage.md").read_text(encoding="utf-8")
    assert "stored 23 of 23" in report and "stored 22 of 23; missing 1: 2025-09-03" in report
    assert "failed calls: 1000:2025-09-03" in report and "credits charged 225" in report
    fake2 = {"kw": {}}  # a second run pays nothing for what is stored and nothing again for a recorded failure
    assert _run(settings, paths, log, clock, fake2).status == "complete" and "c" not in fake2


def test_job_stops_at_the_approved_credits(setup, log, clock):
    settings, paths, features = setup
    _plan(settings, paths, features, ["0925"], date(2025, 10, 2))
    p = mx.job_dir(paths) / "plan.json"
    plan = json.loads(p.read_text())
    plan["approved_credits"] = 10
    p.write_text(json.dumps(plan))
    fake = {"kw": {}}
    res = _run(settings, paths, log, clock, fake)
    assert res.status == "credits" and len(fake["c"].calls) == 2


def test_job_stops_at_the_reserve(setup, log, clock):
    settings, paths, features = setup
    _plan(settings, paths, features, ["0925"], date(2025, 10, 2))
    reserve = settings["skylit_alongside_reserve_credits"]
    fake = {"kw": {"balance": reserve + 12}}
    res = _run(settings, paths, log, clock, fake)
    assert res.status == "credits" and len(fake["c"].calls) == 2


def test_snapshot_keeps_the_call_record(paths):
    rec = CallRecord("/v1/historical", {}, 200, 1, 10, 100, 5, "t")
    path = mx.snapshot_path(paths, "0925", date(2026, 3, 24))
    mx.write_snapshot(path, _body("x"), asdict(rec), mx.at_of("0925", date(2026, 3, 24)))
    doc = mx.read_snapshot(path)
    assert doc["call"]["charged"] == 5 and doc["requested_at"].startswith("2026-03-24T09:25:00-04:00")
