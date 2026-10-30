"""Dataset condition: recording, upgrade flags, session mapping and completeness (owner 2026-10-09, §3.2, §13)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np

from nqbot.clients import databento_condition as cond
from nqbot.clients.databento_client import DatabentoClient
from nqbot.clients.databento_store import DatabentoStore
from nqbot.storage import Paths

from tests.databento_fakes import FakeHistorical
from tests.test_databento_pull import dec_settings  # noqa: F401  (fixture)

ET = ZoneInfo("America/New_York")
CAL = {"holidays": [], "early_closes": []}


def _env(tmp_path):
    paths = Paths.under(tmp_path, "nqbot/")
    fake = FakeHistorical()
    return fake, DatabentoClient(fake), DatabentoStore(paths)


def test_record_flags_non_available_and_keeps_history(tmp_path):
    fake, client, store = _env(tmp_path)
    fake.conditions = {date(2026, 3, 16): "degraded"}
    msgs: list[str] = []
    res = cond.record_conditions(client, store, date(2026, 3, 13), date(2026, 3, 17), today=date(2026, 10, 9),
                                 source="t", echo=msgs.append)
    assert [r["date"] for r in res["flagged"]] == ["2026-03-16"]
    assert len(res["rows"]) == 5 and res["upgrades"] == []
    known = cond.load_conditions(store)
    assert known["2026-03-16"]["condition"] == "degraded" and len(known["2026-03-16"]["history"]) == 1
    assert any("degraded" in m for m in msgs)
    assert "1 of 5 dates not available" in "\n".join(cond.report_lines(res))


def test_upgrade_is_flagged_never_pulled(tmp_path):
    fake, client, store = _env(tmp_path)
    fake.conditions = {date(2026, 3, 16): "degraded"}
    cond.record_conditions(client, store, date(2026, 3, 16), date(2026, 3, 16), today=date(2026, 10, 9), source="a",
                           echo=lambda m: None, stored=lambda d: True)
    fake.conditions = {}
    msgs: list[str] = []
    res = cond.record_conditions(client, store, date(2026, 3, 16), date(2026, 3, 16), today=date(2026, 10, 9),
                                 source="b", echo=msgs.append, stored=lambda d: True)
    assert res["flagged"] == []
    assert [u["date"] for u in res["upgrades"]] == ["2026-03-16"] and res["upgrades"][0]["included"]
    assert cond.upgrades(store)[0]["status"] == "ask_owner"
    assert any("NOT re-pulled" in m for m in msgs)
    assert fake.downloads() == []
    hist = cond.load_conditions(store)["2026-03-16"]["history"]
    assert [h["condition"] for h in hist] == ["degraded", "available"]


def test_parts_touched():
    assert cond.parts_touched(date(2026, 1, 31), CAL) == []  # Saturday: Fri 19:00 -> Sat 19:00 ET, no CME day
    sunday = cond.parts_touched(date(2026, 3, 15), CAL)  # EDT: Sat 20:00 -> Sun 20:00 ET
    assert [(p.cme_day, p.kind, p.start.hour, p.end.hour) for p in sunday] == [(date(2026, 3, 16), "ETH", 18, 20)]
    monday = cond.parts_touched(date(2026, 3, 16), CAL)
    assert [(p.cme_day, p.kind) for p in monday] == [(date(2026, 3, 16), "ETH"), (date(2026, 3, 16), "RTH"),
                                                     (date(2026, 3, 16), "ETH"), (date(2026, 3, 17), "ETH")]
    assert monday[0].start == datetime(2026, 3, 15, 20, 0, tzinfo=ET)  # clipped to the UTC date
    early = {"holidays": [], "early_closes": [{"date": date(2025, 11, 28), "early_close_et": "13:00"}]}
    parts = cond.parts_touched(date(2025, 11, 28), early)
    assert [p.kind for p in parts if p.cme_day == date(2025, 11, 28)] == ["ETH", "RTH"]  # nothing after the close


def test_completeness_hand_built():
    a = datetime(2026, 3, 16, 9, 30, tzinfo=ET)
    b = datetime(2026, 3, 16, 9, 40, tzinfo=ET)
    a_ns = int(a.timestamp()) * 10**9
    # one record in minutes 0, 1, 2 and 8: minutes 3..7 and 9 empty; gaps over 60 s: 80, 340 and 110 (to the end)
    ts = np.array([a_ns + int(s * 1e9) for s in (10, 70, 150, 490)], dtype=np.int64)
    out = cond.completeness(ts, a, b)
    assert out["minutes"] == 10 and out["empty_minutes"] == 6 and out["records"] == 4
    assert out["longest_gap_sec"] == 340.0
    assert [(g["from"], g["sec"]) for g in out["gaps"]] == [("09:31:10", 80.0), ("09:32:30", 340.0),
                                                         ("09:38:10", 110.0)]


def test_mbp1_reader_skips_standalone_f_last_records_with_action_n(tmp_path):
    # Databento's 2026-08-08 normalization: standalone F_LAST records with action N carry no quote change, so a gap
    # with only such records stays a gap (owner 2026-10-29)
    import databento_dbn as d

    from tests.databento_fakes import FIX, dbn_bytes

    a_ns = int(datetime(2026, 8, 10, 9, 30, tzinfo=ET).timestamp()) * 10**9
    lvl = d.BidAskPair(bid_px=20000 * FIX, ask_px=20000 * FIX + FIX // 4, bid_sz=5, ask_sz=7)

    def rec(sec, action, flags):
        ts = a_ns + sec * 10**9
        return d.MBP1Msg(1, 1001, ts, 20000 * FIX, 0, action, d.Side.NONE, flags, ts + 1, levels=lvl)

    records = [rec(0, d.Action.ADD, d.F_LAST), rec(100, d.Action.NONE, d.F_LAST), rec(200, d.Action.CANCEL, d.F_LAST)]
    path = tmp_path / "mbp1.dbn.zst"
    path.write_bytes(dbn_bytes("mbp-1", "NQZ5", a_ns, a_ns + 300 * 10**9, records))
    assert cond.timestamps(path, "mbp-1").tolist() == [a_ns, a_ns + 200 * 10**9]
    out = cond.completeness(cond.timestamps(path, "mbp-1"), datetime(2026, 8, 10, 9, 30, tzinfo=ET),
                            datetime(2026, 8, 10, 9, 35, tzinfo=ET))
    assert out["longest_gap_sec"] == 200.0


def test_backfill_records_each_days_condition_and_flags_it(paths, dec_settings, clock, log, caplog):
    from tests.test_databento_pull import job_for, plan_with, run

    fake = FakeHistorical()
    fake.conditions = {date(2025, 12, 1): "degraded"}
    plan_with(fake, paths, dec_settings)
    with caplog.at_level("WARNING"):
        assert run(job_for(fake), dec_settings, paths, clock, log).status == "complete"
    known = cond.load_conditions(DatabentoStore(paths))
    assert known["2025-12-01"]["condition"] == "degraded" and known["2025-12-02"]["condition"] == "available"
    assert any("2025-12-01 is degraded" in r.getMessage() for r in caplog.records)


def test_damage_lines_label_warm_up_rows():
    rows = [{"utc_date": "2025-09-17", "cme_day": "2025-09-17", "month": None, "part": "RTH", "span": "s",
             "series": "NQ mbp-1", "note": "no stored file"},
            {"utc_date": "2025-09-17", "cme_day": "2025-09-17", "month": None, "part": "RTH", "span": "s",
             "series": "NQ trades", "minutes": 390, "empty_minutes": 0, "records": 9, "longest_gap_sec": 5.0,
             "gaps": [], "baseline_n": 20, "baseline_empty_median": 0, "baseline_empty_max": 0,
             "baseline_gap_max_sec": 7.0},
            {"utc_date": "2026-01-31", "part": None, "note": "touches no CME session (weekend)"}]
    text = "\n".join(cond.damage_lines(rows))
    assert text.count("| warm-up |") == 2 and "None" not in text
