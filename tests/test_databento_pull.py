"""Step-8 Databento plan, cost gate and resumable job, on a fake client and a temporary SSD (no network, no charges)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from nqbot.clients import databento_cost as cost_mod
from nqbot.clients.databento_client import MBP1, OHLCV_1S, TRADES, DatabentoClient
from nqbot.clients.databento_pull import (DOWNLOAD_RETRY_SEC, HOLIDAY, ROLL, WARMUP, WINDOW, DatabentoPullJob, PlanRefused,
                                          PullItem, failed_payg, make_plan, mbp1_span, plan_path, sessions_before,
                                          window_days)
from nqbot.clients.databento_pull import ET
from nqbot.clients.databento_store import CacheMismatch, DatabentoStore
from nqbot.config import load_features
from nqbot.jobs.runner import Ctx, StopChunk, run_job
from tests.databento_fakes import FakeHistorical

TODAY = date(2026, 11, 20)  # L1 included from 2025-11-21; Skylit reaches back to 2025-11-20


@pytest.fixture
def dec_settings(settings):
    settings["months"] = {1: "2025-12"}
    return settings


def plan_with(fake, paths, settings, approved=None, today=TODAY, lines=None, retry_failed_payg=False):
    store = DatabentoStore(paths)
    client = DatabentoClient(fake, on_call=store.log_call)
    echo = (lines.append if lines is not None else lambda s: None)
    return make_plan(client, store, settings, load_features(), today, approved_usd=approved, echo=echo,
                     retry_failed_payg=retry_failed_payg)


def job_for(fake, today=TODAY):
    return DatabentoPullJob(client_factory=lambda ctx, store: DatabentoClient(fake, on_call=store.log_call),
                            today=lambda: today)


def run(job, settings, paths, clock, log):
    return run_job(job, settings, paths, until=clock.now() + timedelta(hours=8), log=log, now=clock.now,
                   sleep=clock.sleep)


# --- pure planning ------------------------------------------------------------------------------------------

def test_sessions_before_skips_holidays_and_weekends():
    got = sessions_before(date(2025, 12, 1), 5)
    assert got == [date(2025, 11, 21), date(2025, 11, 24), date(2025, 11, 25), date(2025, 11, 26), date(2025, 11, 28)]


def test_window_days_drop_what_skylit_can_no_longer_serve(dec_settings):
    days, cut = window_days(dec_settings, date(2026, 12, 10))  # Skylit reaches back to 2025-12-10
    assert days[0] == date(2025, 12, 10) and days[-1] == date(2025, 12, 31)
    assert cut[0] == date(2025, 12, 1) and cut[-1] == date(2025, 12, 9)
    days, cut = window_days(dec_settings, date(2025, 12, 17))  # only complete days: up to yesterday
    assert days[-1] == date(2025, 12, 16) and not cut


def test_window_days_follow_the_skylit_timestamp_rule(dec_settings):
    """presd_fixes issue 7: the same timestamp rule as the Skylit backfill, and its wholly lost days excluded."""
    days, cut = window_days(dec_settings, date(2026, 12, 10), now=datetime(2026, 12, 10, 16, 0, tzinfo=ET))
    assert days[0] == date(2025, 12, 11) and cut[-1] == date(2025, 12, 10)  # its 15:45 window left at 15:15 ET
    days, cut = window_days(dec_settings, date(2026, 12, 10), skylit_lost={date(2025, 12, 15)})
    assert date(2025, 12, 15) in cut and date(2025, 12, 15) not in days


def test_mbp1_window_normal_dst_and_early_close():
    s, e = mbp1_span(date(2025, 12, 16))  # EST
    assert (s.astimezone(timezone.utc).strftime("%H:%M"), e.astimezone(timezone.utc).strftime("%H:%M")) == ("14:25", "21:05")
    s, e = mbp1_span(date(2026, 6, 16))  # EDT
    assert (s.astimezone(timezone.utc).strftime("%H:%M"), e.astimezone(timezone.utc).strftime("%H:%M")) == ("13:25", "20:05")
    s, e = mbp1_span(date(2025, 12, 24))  # early close 13:00 ET -> 13:05
    assert (s.strftime("%H:%M"), e.strftime("%H:%M")) == ("09:25", "13:05")


def test_cost_classification_at_the_12_month_boundary():
    assert cost_mod.months_back(date(2026, 3, 31), 1) == date(2026, 2, 28)
    assert cost_mod.l1_included_from(TODAY) == date(2025, 11, 21)
    at = lambda d: datetime(d.year, d.month, d.day, 18, tzinfo=timezone.utc)
    assert cost_mod.is_payg(TRADES, at(date(2025, 11, 20)), TODAY)
    assert not cost_mod.is_payg(TRADES, at(date(2025, 11, 21)), TODAY)
    assert cost_mod.is_payg(MBP1, at(date(2025, 11, 1)), TODAY)
    assert not cost_mod.is_payg(OHLCV_1S, at(date(2015, 1, 1)), TODAY)  # 1-second bars: 16+ years in the plan


def test_cost_check_approvals():
    lines = []
    est = cost_mod.Estimate("x").add("warm-up trades", 15, 12.5, True).add("window trades", 23, 40.0, False)
    assert not cost_mod.check(est, approved_usd=None, echo=lines.append)
    assert any("NOT approved" in ln and "--approve-usd 12.50" in ln for ln in lines)
    assert not cost_mod.check(est, approved_usd=12.0, echo=lines.append)
    assert cost_mod.check(est, approved_usd=12.5, echo=lines.append)
    included_only = cost_mod.Estimate("y").add("window trades", 23, 40.0, False)
    assert cost_mod.check(included_only, approved_usd=None, echo=lines.append)


def test_plan_items(paths, dec_settings):
    lines: list[str] = []
    fake = FakeHistorical()
    plan = plan_with(fake, paths, dec_settings, lines=lines)
    items = [PullItem.from_json(d) for d in plan["items"]]
    by = lambda **kw: [i for i in items if all(getattr(i, k) == v for k, v in kw.items())]

    # Warm-up: 21 sessions before 2025-12-01 (Thanksgiving skipped), NQ/ES trades + 1-second only.
    warm_days = sorted({i.day for i in by(kind=WARMUP)})
    assert len(warm_days) == 21 and warm_days[0] == date(2025, 10, 30) and warm_days[-1] == date(2025, 11, 28)
    assert {(i.root, i.schema) for i in by(kind=WARMUP)} == {("NQ", TRADES), ("NQ", OHLCV_1S), ("ES", TRADES), ("ES", OHLCV_1S)}

    # A normal window session: 6 requests; the MNQ contract follows NQ through the roll.
    assert len(by(day=date(2025, 12, 1))) == 6
    assert by(day=date(2025, 12, 12), root="MNQ")[0].symbol == "MNQZ5"
    assert by(day=date(2025, 12, 15), root="MNQ")[0].symbol == "MNQH6"
    assert by(day=date(2025, 12, 15), root="ES", schema=TRADES)[0].symbol == "ESZ5"  # ES rolls a day later

    # The holiday: NQ/ES trades + 1-second only, not a session (no mbp-1, no MNQ).
    xmas = by(day=date(2025, 12, 25))
    assert {i.kind for i in xmas} == {HOLIDAY} and {i.schema for i in xmas} == {TRADES, OHLCV_1S}
    assert {i.root for i in xmas} == {"NQ", "ES"}

    # Early close: mbp-1 to 13:05 ET.
    assert by(day=date(2025, 12, 24), schema=MBP1)[0].end.strftime("%H:%M") == "13:05"

    # Rolls: the new contract's 1-second bars on the session before each switch.
    assert [(i.day, i.symbol) for i in by(kind=ROLL)] == [(date(2025, 12, 12), "NQH6"), (date(2025, 12, 15), "ESH6")]
    assert [r["roll_day"] for r in plan["rolls"]] == ["2025-12-15", "2025-12-16"]

    # Estimate printed first, the warm-up trades before 2025-11-21 are pay-as-you-go and not approved.
    text = "\n".join(lines)
    assert "PAY-AS-YOU-GO" in text and "NOT approved" in text and "roll NQ: NQZ5 -> NQH6" in text
    assert plan_path(paths).exists()
    assert not any(c[0] == "get_range" for c in fake.calls)  # planning never downloads


def test_plan_refuses_without_a_ledger(paths, settings):
    from nqbot.months import LedgerMissing

    with pytest.raises(LedgerMissing):
        plan_with(FakeHistorical(), paths, settings)


# --- the job ------------------------------------------------------------------------------------------------

def test_job_pulls_included_items_then_payg_only_after_approval(paths, dec_settings, clock, log):
    fake = FakeHistorical()
    plan = plan_with(fake, paths, dec_settings)  # not approved
    items = [PullItem.from_json(d) for d in plan["items"]]
    payg = [i for i in items if i.payg(TODAY)]
    # The CME day of 2025-11-21 starts 18:00 ET on 11-20: on the paid side of the boundary (conservative).
    assert payg and all(i.kind == WARMUP and i.schema == TRADES and i.day <= date(2025, 11, 21) for i in payg)
    assert max(i.day for i in payg) == date(2025, 11, 21)

    result = run(job_for(fake), dec_settings, paths, clock, log)
    assert result.status == "complete"
    assert not any(c.endswith(":payg") for c in result.ran)
    assert result.ran == sorted(result.ran)  # oldest day first
    got = {(c[1], c[2], c[3]) for c in fake.downloads()}
    assert len(fake.downloads()) == len(items) - len(payg) == len(got)  # each request once
    assert not any(s == TRADES and (sym, st) in {(i.symbol, i.start.astimezone(timezone.utc).isoformat()) for i in payg}
                   for s, sym, st in got)

    store = DatabentoStore(paths)
    assert store.bars_path("NQ", date(2025, 12, 1), 1).exists() and store.bars_path("ES", date(2025, 12, 1), 5).exists()
    assert not store.bars_path("NQ", date(2025, 11, 3), 1).exists()  # its trades are pay-as-you-go, not bought yet
    rolls = {r["root"]: r for r in store.rolls()}
    assert rolls["NQ"]["spread"] == 250.0 and rolls["NQ"]["prev_session"] == "2025-12-12"
    assert rolls["ES"]["spread"] == 50.0
    ledger = store.ledger()
    assert sum(1 for r in ledger if r["kind"] == "download") == len(fake.downloads())
    assert all(r["ok"] for r in ledger)

    # The owner approves: re-plan with the amount, the next run buys only the pay-as-you-go items.
    before = len(fake.downloads())
    plan_with(fake, paths, dec_settings, approved=1000.0)
    result = run(job_for(fake), dec_settings, paths, clock, log)
    assert result.status == "complete"
    assert {c.split(":")[1] for c in result.ran if ":" in c} == {"payg"}
    new = fake.downloads()[before:]
    assert len(new) == len(payg) and all(c[1] == TRADES for c in new)
    assert store.bars_path("NQ", date(2025, 11, 3), 1).exists()


def test_cached_files_are_never_pulled_again(paths, dec_settings, clock, log):
    fake = FakeHistorical()
    plan_with(fake, paths, dec_settings)
    run(job_for(fake), dec_settings, paths, clock, log)
    n = len(fake.downloads())
    (paths.data_dir / "jobs" / "databento_backfill" / "state.json").unlink()  # forget progress: the cache still holds
    run(job_for(fake), dec_settings, paths, clock, log)
    assert len(fake.downloads()) == n


def test_a_file_pulled_with_other_parameters_is_a_mismatch(paths, dec_settings):
    fake = FakeHistorical()
    plan = plan_with(fake, paths, dec_settings)
    store = DatabentoStore(paths)
    client = DatabentoClient(fake, on_call=store.log_call)
    it = next(PullItem.from_json(d) for d in plan["items"] if d["schema"] == MBP1)
    client.download(it.schema, it.symbol, it.start, it.end, store.raw_path(it.schema, it.symbol, it.day))
    assert store.cached(it.schema, it.symbol, it.day, it.start, it.end, client.dataset)
    with pytest.raises(CacheMismatch):
        store.cached(it.schema, it.symbol, it.day, it.start, it.end + timedelta(minutes=5), client.dataset)


def _ctx(paths, settings, log, stop_after=None):
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return "reached 07:30" if stop_after is not None and calls["n"] > stop_after else None

    slept: list[float] = []
    ctx = Ctx(paths=paths, settings=settings, out_dir=paths.data_dir / "jobs" / "databento_backfill", low_memory=True,
              max_workers=1, log=log, deadline=datetime.now(timezone.utc), should_stop=should_stop, sleep=slept.append)
    ctx.slept = slept  # type: ignore[attr-defined]
    return ctx


def test_a_chunk_stops_cleanly_between_requests(paths, dec_settings, log):
    fake = FakeHistorical()
    plan_with(fake, paths, dec_settings)
    job = job_for(fake)
    with pytest.raises(StopChunk):
        job.run_chunk("2025-12-01:incl:x", _ctx(paths, dec_settings, log, stop_after=2))
    assert len(fake.downloads()) == 2
    store = DatabentoStore(paths)
    assert not store.bars_path("NQ", date(2025, 12, 1), 1).exists()
    job.run_chunk("2025-12-01:incl:x", _ctx(paths, dec_settings, log))  # the re-run finishes the day without re-pulling
    assert len(fake.downloads()) == 6
    assert store.bars_path("NQ", date(2025, 12, 1), 1).exists()


def test_a_dropped_download_leaves_no_file(paths, dec_settings, log):
    fake = FakeHistorical(fail_on=lambda schema, symbol, start: schema == MBP1)
    plan_with(fake, paths, dec_settings)
    ctx = _ctx(paths, dec_settings, log)
    with pytest.raises(StopChunk) as e:
        job_for(fake).run_chunk("2025-12-01:incl:x", ctx)
    assert e.value.status == "api_error"  # a clean stop: the chunk re-runs next time (presd_fixes issue 4)
    assert ctx.slept == list(DOWNLOAD_RETRY_SEC)  # an included download is retried with backoff, then gives up
    folder = DatabentoStore(paths).root / MBP1
    assert [p for p in folder.rglob("*") if p.is_file()] == []  # neither the file nor its temp file
    failed = [r for r in DatabentoStore(paths).ledger() if not r["ok"]]
    assert len(failed) == 1 + len(DOWNLOAD_RETRY_SEC) and all("ConnectionError" in r["error"] for r in failed)


def test_job_refuses_without_a_plan(paths, dec_settings, clock, log):
    with pytest.raises(SystemExit, match="databento plan"):
        run(job_for(FakeHistorical()), dec_settings, paths, clock, log)


def test_plan_json_is_readable(paths, dec_settings):
    plan_with(FakeHistorical(), paths, dec_settings, approved=5.0)
    plan = json.loads(plan_path(paths).read_text(encoding="utf-8"))
    assert plan["approved_usd"] == 5.0 and plan["front"]["NQ"]["2025-12-15"] == "NQH6"
    assert WINDOW in {d["kind"] for d in plan["items"]}


# --- the command ------------------------------------------------------------------------------------------------

def test_cli_plan(settings, drive, monkeypatch, capsys):
    from nqbot import cli, storage

    fake = FakeHistorical()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(DatabentoClient, "from_env", classmethod(lambda cls, **kw: cls(fake, **kw)))
    assert cli.main(["databento", "plan", "--today", TODAY.isoformat()]) == cli.EXIT_REFUSED  # no ledger yet
    assert "step 4b" in capsys.readouterr().err
    settings["months"] = {1: "2025-12"}
    assert cli.main(["databento", "plan", "--today", TODAY.isoformat(), "--approve-usd", "100"]) == 0
    out = capsys.readouterr().out
    assert "Databento cost estimate" in out and "approved: $100.00" in out
    assert plan_path(storage.resolve_paths(settings)).exists()
    assert not fake.downloads()


def test_cli_plan_refuses_before_ssd_day(settings, monkeypatch, capsys):
    from nqbot import cli, storage

    settings["paths"]["root"] = None
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(storage, "candidate_drives", lambda: [])
    assert cli.main(["databento", "plan"]) == cli.EXIT_REFUSED
    assert "step 4b" in capsys.readouterr().err


# --- review fixes -------------------------------------------------------------------------------------------

def test_payg_spending_is_capped_by_the_approved_requests(paths, dec_settings, clock, log):
    # Approved at TODAY; the job runs 10 days later, when the CME days up to 2025-12-01 have also left the included
    # window. Those were never approved: they wait, only the approved list is bought.
    fake = FakeHistorical()
    plan = plan_with(fake, paths, dec_settings, approved=1000.0)
    approved = set(plan["approved_payg"])
    assert approved
    later = TODAY + timedelta(days=10)
    run(job_for(fake, today=later), dec_settings, paths, clock, log)
    items = [PullItem.from_json(d) for d in plan["items"]]
    bought = {(c[1], c[2], c[3]) for c in fake.downloads()}
    key = lambda i: (i.schema, i.symbol, i.start.astimezone(timezone.utc).isoformat())
    aged = [i for i in items if i.payg(later) and i.key not in approved]
    assert aged and not any(key(i) in bought for i in aged)
    assert all(key(i) in bought for i in items if i.key in approved)
    assert all(key(i) in bought for i in items if not i.payg(later))


def test_an_item_that_ages_out_mid_run_is_not_bought(paths, dec_settings, log):
    fake = FakeHistorical()
    plan_with(fake, paths, dec_settings)
    now = {"today": TODAY}
    job = DatabentoPullJob(client_factory=lambda ctx, store: DatabentoClient(fake, on_call=store.log_call),
                           today=lambda: now["today"])
    now["today"] = TODAY + timedelta(days=11)  # past midnight: 2025-12-01's CME day is now pay-as-you-go
    job.run_chunk("2025-12-01:incl:x", _ctx(paths, dec_settings, log))
    assert [c for c in fake.downloads() if c[1] in (TRADES, MBP1)] == []
    assert len(fake.downloads()) == 2  # the 1-second bars stay included


def test_a_replan_that_adds_requests_to_a_finished_day_pulls_them(paths, dec_settings, clock, log):
    fake = FakeHistorical()
    plan_with(fake, paths, dec_settings)
    run(job_for(fake), dec_settings, paths, clock, log)
    n = len(fake.downloads())
    path = plan_path(paths)
    plan = json.loads(path.read_text(encoding="utf-8"))
    extra = next(d for d in plan["items"] if d["day"] == "2025-12-01" and d["schema"] == OHLCV_1S and d["root"] == "NQ")
    plan["items"].append({**extra, "symbol": "NQH6", "kind": ROLL})  # as a re-plan finding an earlier roll would
    path.write_text(json.dumps(plan), encoding="utf-8")
    result = run(job_for(fake), dec_settings, paths, clock, log)
    assert [c.split(":")[0] for c in result.ran] == ["2025-12-01"]
    assert len(fake.downloads()) == n + 1 and fake.downloads()[-1][2] == "NQH6"


def test_cost_calls_are_in_the_ledger_with_their_dollars(paths, dec_settings):
    plan_with(FakeHistorical(), paths, dec_settings)
    costs = [r for r in DatabentoStore(paths).ledger() if r["kind"] == "cost"]
    assert costs and all(r["usd"] is not None for r in costs) and any(r["usd"] > 0 for r in costs)


def test_included_data_is_shown_as_zero_dollars():
    est = cost_mod.Estimate("x").add("window trades", 23, 40.0, False).add("warm-up trades", 15, 12.5, True)
    text = est.text()
    assert "window trades: 23 day(s) -> $0.00 (included in Standard" in text
    assert "warm-up trades: 15 day(s) -> $12.50 PAY-AS-YOU-GO" in text
    assert "total extra charge: $12.50" in text


# --- second review fixes --------------------------------------------------------------------------------------

def test_an_included_request_that_get_cost_prices_needs_approval(paths, dec_settings, clock, log):
    # The date rule calls Dec 2025 included, but this account's get_cost prices L1 before 2025-12-10: that is a real
    # charge (get_cost applies the plan), so those requests are pay-as-you-go and wait for approval.
    fake = FakeHistorical(included_from=date(2025, 12, 10))
    plan = plan_with(fake, paths, dec_settings)
    assert "mbp-1|NQZ5|2025-12-01" in plan["payg_keys"] and plan["approved_payg"] == []
    run(job_for(fake), dec_settings, paths, clock, log)
    bought = {(c[1], c[2], c[3][:10]) for c in fake.downloads()}
    assert ("mbp-1", "NQZ5", "2025-12-01") not in bought and ("mbp-1", "NQZ5", "2025-12-10") in bought


def test_the_suggested_approval_always_passes():
    est = cost_mod.Estimate("x").add("warm-up trades", 3, 12.344, True)
    lines: list[str] = []
    assert not cost_mod.check(est, approved_usd=12.34, echo=lines.append)
    assert "--approve-usd 12.35" in lines[-1]
    assert cost_mod.check(est, approved_usd=12.35, echo=lines.append)


def test_a_replan_estimates_only_what_is_not_yet_pulled(paths, dec_settings, clock, log):
    fake = FakeHistorical()
    plan_with(fake, paths, dec_settings, approved=1000.0)
    run(job_for(fake), dec_settings, paths, clock, log)
    lines: list[str] = []
    plan = plan_with(fake, paths, dec_settings, lines=lines)
    assert plan["payg_keys"] == [] and "total extra charge: $0.00" in "\n".join(lines)


def test_expired_contracts_resolve_partially(paths, dec_settings):
    plan = plan_with(FakeHistorical(partial_ids=True), paths, dec_settings)
    assert plan["front"]["NQ"]["2025-12-12"] == "NQZ5" and plan["front"]["NQ"]["2025-12-15"] == "NQH6"


def test_prices_convert_exactly():
    import numpy as np

    from nqbot.features.bars import bars_from_records

    ticks = np.arange(15000 * 4, 30000 * 4) / 4  # every NQ tick 15,000..30,000
    rec = np.zeros(len(ticks), dtype=[("ts_event", "i8"), ("sequence", "u4"), ("price", "i8"), ("size", "u4"),
                                      ("side", "S1")])
    rec["ts_event"] = np.arange(len(ticks)) * 60_000_000_000
    rec["price"] = np.arange(15000 * 4, 30000 * 4, dtype=np.int64) * 250_000_000
    rec["size"], rec["side"] = 1, b"B"
    assert np.array_equal(np.asarray(bars_from_records(rec, 1).column("close")), ticks)


def test_a_roll_spread_that_cannot_be_measured_is_recorded(paths, dec_settings, clock, log):
    from tests.databento_fakes import default_records

    def no_new_contract_bars(schema, symbol, s, e):  # NQH6 has no 1-second bar before 17:00 on 2025-12-12
        return [] if (schema == OHLCV_1S and symbol == "NQH6") else default_records(schema, symbol, s, e)

    fake = FakeHistorical(records=no_new_contract_bars)
    plan_with(fake, paths, dec_settings)
    run(job_for(fake), dec_settings, paths, clock, log)
    store = DatabentoStore(paths)
    errors = store.roll_errors()
    assert [(e["root"], e["roll_day"]) for e in errors] == [("NQ", "2025-12-15")]
    assert "NQH6" in errors[0]["error"] and "NQ" not in {r["root"] for r in store.rolls()}


def test_a_transient_failure_is_retried_but_a_paid_download_is_not(paths, dec_settings, log):
    failures = {"n": 0}

    def once(schema, symbol, start):
        if schema == MBP1 and failures["n"] == 0:
            failures["n"] += 1
            return True
        return False

    fake = FakeHistorical(fail_on=once)
    plan_with(fake, paths, dec_settings)
    ctx = _ctx(paths, dec_settings, log)
    job_for(fake).run_chunk("2025-12-01:incl:x", ctx)
    assert ctx.slept == [DOWNLOAD_RETRY_SEC[0]]
    assert DatabentoStore(paths).raw_path(MBP1, "NQZ5", date(2025, 12, 1)).exists()

    paid = FakeHistorical(fail_on=lambda schema, symbol, start: schema == TRADES)
    plan_with(paid, paths, dec_settings, approved=1000.0)
    ctx = _ctx(paths, dec_settings, log)
    job_for(paid).run_chunk("2025-11-03:payg:x", ctx)  # recorded, not retried, not raised
    assert ctx.slept == [] and len(paid.downloads()) == 2  # NQ and ES warm-up trades: each tried once


def test_window_days_keep_days_skylit_already_stored(dec_settings):
    """Review finding 2: a re-plan days later keeps the days whose Skylit boards are stored."""
    days, _ = window_days(dec_settings, date(2026, 12, 20), skylit_has=lambda d: d == date(2025, 12, 12))
    assert date(2025, 12, 12) in days and date(2025, 12, 11) not in days


# --- pre-SSD session B (presd_fixes issues 4 and 5) ----------------------------------------------------------

def test_a_broken_stream_is_retried_but_a_4xx_is_not(paths, dec_settings, log):
    """databento 0.87 wraps a mid-stream failure as a plain BentoError: retried for included items."""
    from databento.common.error import BentoClientError, BentoError

    broken = {"n": 0}

    def once(schema, symbol, start):
        if schema == MBP1 and broken["n"] == 0:
            broken["n"] += 1
            return BentoError("Error streaming response: peer closed connection")
        return False

    fake = FakeHistorical(fail_on=once)
    plan_with(fake, paths, dec_settings)
    ctx = _ctx(paths, dec_settings, log)
    job_for(fake).run_chunk("2025-12-01:incl:x", ctx)
    assert ctx.slept == [DOWNLOAD_RETRY_SEC[0]] and DatabentoStore(paths).raw_path(MBP1, "NQZ5", date(2025, 12, 1)).exists()

    bad = FakeHistorical(fail_on=lambda schema, symbol, start: BentoClientError(http_status=422, message="bad request")
                         if schema == MBP1 else False)
    ctx = _ctx(paths, dec_settings, log)
    with pytest.raises(BentoClientError):
        job_for(bad).run_chunk("2025-12-02:incl:x", ctx)
    assert ctx.slept == []


def test_a_broken_paid_download_waits_for_the_owner(paths, dec_settings, clock, log):
    from databento.common.error import BentoError

    fake = FakeHistorical(fail_on=lambda schema, symbol, start: BentoError("Error streaming response: reset")
                          if schema == TRADES and symbol == "NQZ5" and start.startswith("2025-11-02") else False)
    plan_with(fake, paths, dec_settings, approved=1000.0)
    r = run(job_for(fake), dec_settings, paths, clock, log)
    assert r.status == "complete"
    store = DatabentoStore(paths)
    warm = date(2025, 11, 3)
    assert not store.raw_path(TRADES, "NQZ5", warm).exists() and store.raw_path(TRADES, "ESZ5", warm).exists()
    assert list(failed_payg(paths)) == [f"trades|NQZ5|{warm}"]
    tried = len(fake.downloads())
    run(job_for(fake), dec_settings, paths, clock, log)  # the next night: never retried automatically
    assert len(fake.downloads()) == tried
    lines: list[str] = []
    plan_with(fake, paths, dec_settings, approved=1000.0, lines=lines)  # a re-plan alone doesn't clear it
    assert any("--retry-failed-payg" in line for line in lines)
    run(job_for(fake), dec_settings, paths, clock, log)
    assert len(fake.downloads()) == tried
    fake.fail_on = None
    plan = plan_with(fake, paths, dec_settings, approved=1000.0, retry_failed_payg=True)
    assert plan["retry_failed"] == [f"trades|NQZ5|{warm}"]
    run(job_for(fake), dec_settings, paths, clock, log)
    assert store.raw_path(TRADES, "NQZ5", warm).exists() and len(fake.downloads()) == tried + 1


def test_a_retry_plan_reruns_a_paid_chunk_whose_items_all_broke(paths, dec_settings, clock, log):
    """Review finding 1: the same missing requests after a --retry-failed-payg plan are a new chunk, not 'done'."""
    from databento.common.error import BentoError

    warm = date(2025, 11, 3)
    fake = FakeHistorical(fail_on=lambda schema, symbol, start: BentoError("Error streaming response: reset")
                          if schema == TRADES and start.startswith("2025-11-02") else False)
    plan_with(fake, paths, dec_settings, approved=1000.0)
    run(job_for(fake), dec_settings, paths, clock, log)
    assert sorted(failed_payg(paths)) == [f"trades|ESZ5|{warm}", f"trades|NQZ5|{warm}"]
    fake.fail_on = None
    plan_with(fake, paths, dec_settings, approved=1000.0, retry_failed_payg=True)
    run(job_for(fake), dec_settings, paths, clock, log)
    store = DatabentoStore(paths)
    assert store.raw_path(TRADES, "NQZ5", warm).exists() and store.raw_path(TRADES, "ESZ5", warm).exists()


def test_plan_refused_when_get_cost_prices_the_probe(paths, dec_settings):
    fake = FakeHistorical(price_all=True)  # subscription not active yet
    with pytest.raises(PlanRefused, match="subscription is not active"):
        plan_with(fake, paths, dec_settings, approved=10**6)
    assert not plan_path(paths).exists()
    probes = [c for c in fake.calls if c[0] == "cost" and c[3].endswith("T15:00:00+00:00")]
    assert probes and probes[0][1] == OHLCV_1S


def test_plan_refused_when_most_included_requests_are_priced(paths, dec_settings, monkeypatch):
    from nqbot.clients import databento_pull as dbp

    fake = FakeHistorical(included_from=date(2025, 12, 31))  # the probe day is $0, nearly everything else priced
    monkeypatch.setattr(dbp, "probe_included", lambda *a, **k: None)
    with pytest.raises(PlanRefused, match="get_cost priced"):
        plan_with(fake, paths, dec_settings, approved=10**6)
    assert not plan_path(paths).exists()


def test_step7_sample_days_are_pulled_first(paths, dec_settings, clock, log):
    """presd_fixes 12: the sample days (and only they) jump the queue, so step 8b can run after night 1."""
    from nqbot.clients.skylit_pull import SkylitPullJob

    samples = [date(2025, 12, 18), date(2025, 12, 10)]
    SkylitPullJob.write_plan(paths, "skylit_sample_days", samples, approved=None)
    fake = FakeHistorical()
    plan_with(fake, paths, dec_settings)
    result = run(job_for(fake), dec_settings, paths, clock, log)
    assert result.status == "complete"
    days = [date.fromisoformat(c.split(":")[0]) for c in result.ran]
    assert days[:2] == sorted(samples) and days[2:] == sorted(days[2:])  # then oldest first, as before
    assert all(c[3][:10] in ("2025-12-09", "2025-12-10") for c in fake.downloads()[:2])  # CME day 12-10 first
