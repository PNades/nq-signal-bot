"""Step 12 visual check helpers (SPEC §12 step 12): readings, compare, LVN lifetimes, node types, single prints, plots."""

from __future__ import annotations

from datetime import date, datetime, time
from types import SimpleNamespace

import numpy as np
import pytest

from nqbot.checks import step12 as s12
from nqbot.config import load_features, load_settings
from nqbot.features import golden_zone as gz
from nqbot.features import session_tape as st
from nqbot.features import tpo
from nqbot.features.bands import StepBand
from nqbot.months import NotDevelopmentMonth

S = load_settings()
F = load_features()
DAY = date(2026, 3, 6)


def ns(h: int, m: int, s: int = 0) -> int:
    return st.et_ns(DAY, time(h, m, s))


# --- readings ----------------------------------------------------------------------------------------------------

def test_reading_plan_sample_day_redraw_and_flip():
    flips = [ns(15, 33), ns(15, 50)]
    plan = s12.reading_plan(True, flips)
    assert [(r.at, r.scope) for r in plan] == [(time(9, 46), "fib"), (time(10, 1), "all"), (time(10, 31), "ib_fib"),
                                               (time(15, 33), "fib")]   # the first flip only


def test_reading_plan_plain_day_and_added_days():
    assert [r.at for r in s12.reading_plan(False, [])] == [time(9, 46), time(10, 1)]
    # an added flip day: IB and fib only, the flip minute, not the redraw
    plan = s12.reading_plan(True, [ns(11, 37)], added_for="flip")
    assert [(r.at, r.scope) for r in plan] == [(time(9, 46), "fib"), (time(10, 1), "ib_fib"), (time(11, 37), "fib")]
    plan = s12.reading_plan(True, [ns(11, 37)], added_for="redraw")
    assert [r.at for r in plan] == [time(9, 46), time(10, 1), time(10, 31)]


def _fake_cs(drawing, price=24700.0):
    snap = SimpleNamespace(t_ns=ns(10, 1), poc=24675.0, vah=24938.0, val=24613.5, lvns=((24980.0, 24981.25),))
    vw = SimpleNamespace(at=lambda t: (24800.0, 10.0))
    ib = [SimpleNamespace(side="high", price=24742.5, level_id="ib_high-x"),
          SimpleNamespace(side="low", price=24608.75, level_id="ib_low-x")]
    orr = SimpleNamespace(drawing_at=lambda t: drawing, ib_at=lambda t: ib if t >= ns(10, 0) else [])
    reg = [{"sub_type": "pdh", "level_id": "pdh-x", "band": (24996.0, 25004.0)},
           {"sub_type": "single_print", "level_id": "sp-2026-03-05T03-24500-24500", "band": (24500.0, 24510.0)},
           {"sub_type": "single_print", "level_id": "sp-2026-03-06T01-24690-24690", "band": (24690.0, 24695.0)},
           {"sub_type": "poc_daily", "level_id": "poc_daily-x", "band": (24671.0, 24679.0)}]
    return SimpleNamespace(opening=orr, vwap={"vwap18": vw, "vwap0930": vw}, daily=SimpleNamespace(at=lambda t: snap),
                           weekly=SimpleNamespace(at=lambda t: None), prev_prof={"poc": 1.0, "vah": 2.0, "val": 0.5},
                           f={"prev_profile_anchor": "globex_1800_1700", "tpo_tick_pts": 1}, registry_at=lambda t: reg,
                           tape=SimpleNamespace(last_price_at=lambda t: price))


def test_level_rows_every_level_at_1001():
    d = gz.Drawing(24742.5, 24608.75, gz.LONG, gz.LOCKED, 1, 2)
    rows = s12.level_rows(_fake_cs(d), DAY, time(10, 1), "all")
    by = {r["level"]: r for r in rows}
    assert by["IB high"]["bot_value"] == 24742.5 and by["IB high"]["tolerance_ticks"] == 0
    assert by["fib orientation"]["bot_value"].startswith("long") and by["fib orientation"]["kind"] == "text"
    assert by["fib 0.5"]["bot_value"] == pytest.approx(24675.625) and by["fib 0.5"]["tolerance_ticks"] == 1
    assert by["fib 0.79"]["bot_value"] == pytest.approx(24742.5 - 0.79 * (24742.5 - 24608.75))
    assert by["VWAP 18:00 +2σ"]["bot_value"] == 24820.0 and by["VWAP 18:00 +2σ"]["tolerance_ticks"] == 2
    assert by["VWAP 09:30"]["tolerance_ticks"] == 1
    assert by["daily VAH"]["tolerance_ticks"] == 4 and by["daily LVN"]["tolerance_ticks"] == 4
    assert by["PDH (prior CME day high)"]["bot_value"] == 25000.0
    assert by["previous session POC"]["bot_value"] == 1.0
    assert by["daily POC"]["tolerance_ticks"] == 8 and by["previous session POC"]["tolerance_ticks"] == 8   # ±2 pts
    assert by["previous session VAH"]["tolerance_ticks"] == 4
    assert "poc_daily-x" not in {r["level_id"] for r in rows}   # the daily POC comes from the profile, once
    sp = [r for r in rows if r["group"] == "single_print"]
    assert [r["bot_value"] for r in sp] == [24690.0, 24695.0, 24500.0, 24510.0]   # nearest price first
    assert sp[0]["note"].startswith("formed 2026-03-06; 5 pts")
    assert {r["tolerance_ticks"] for r in sp} == {4.0}   # one 1-pt TPO bin (owner 2026-11-02)


def test_level_rows_fib_only_and_short_orientation():
    d = gz.Drawing(24732.5, 24608.75, gz.SHORT, gz.RUNNING, 1, 2)
    rows = s12.level_rows(_fake_cs(d), DAY, time(9, 46), "fib")
    assert {r["group"] for r in rows} == {"fib"}
    by = {r["level"]: r for r in rows}
    assert by["fib orientation"]["bot_value"].startswith("short")
    assert by["fib 0.5"]["bot_value"] == pytest.approx(24608.75 + 0.5 * (24732.5 - 24608.75))
    rows = s12.level_rows(_fake_cs(None), DAY, time(9, 46), "fib")
    assert rows[0]["level"] == "no fib drawn"


# --- compare -----------------------------------------------------------------------------------------------------

def _r(level, bot, dc, tol, group="vwap", kind="price"):
    return {"day": "2026-03-06", "reading_et": "10:01", "group": group, "level": level, "level_id": "", "kind": kind,
            "bot_value": bot, "tolerance_ticks": str(tol), "deepcharts_value": dc}


def test_compare_tolerances_text_blanks_and_lvns():
    rows = [_r("VWAP", "24832.54", "24832.75", 1),          # 0.84 ticks: within 1
            _r("VWAP +1σ", "25001.6", "25002.25", 2),       # 2.6 ticks: miss
            _r("IB high", "24742.5", "24742.5", 0),
            _r("IB low", "24608.75", "24608.5", 0),         # 1 tick off an exact level
            _r("fib orientation", "long (low→high)", "Long", "text", kind="text"),
            _r("daily POC", "24675", "", 2),
            _r("daily LVN", "24981.25", "24982", 4, group="lvn_daily"),
            _r("daily LVN", "24900", "none", 4, group="lvn_daily"),
            _r("daily LVN", "", "24983.25", 4, group="lvn_daily"),   # a Deepcharts LVN 8 ticks from the nearest bot LVN
            _r("daily LVN", "", "24901", 4, group="lvn_daily")]      # 4 ticks from 24900: matched
    res = s12.compare_rows(rows)
    st_ = [r["status"] for r in res]
    assert st_[0] == "ok" and res[0]["diff_ticks"] == pytest.approx(0.84)
    assert st_[1] == "miss" and st_[2] == "ok" and st_[3] == "miss"
    assert st_[4] == "ok" and st_[5] == "not read" and st_[6] == "ok"
    assert st_[7].startswith("miss: not in Deepcharts")
    assert st_[8] == "miss: no bot LVN near" and res[8]["diff_ticks"] == pytest.approx(8.0)   # 24981.25 is 8 ticks off
    assert st_[9] == "ok"


def test_compare_poc_2_points_va_4_ticks_other_levels_unchanged():
    """Owner 2026-11-07: daily / weekly / previous-session POC within ±2 pts (8 ticks), VAH / VAL within ±4 ticks,
    also in a table written with the earlier 2 ticks; other levels keep the table's tolerance."""
    res = s12.compare_rows([_r("daily POC", "24675", "24677", 2, group="profile_daily"),            # +8 ticks: ok
                            _r("weekly POC", "25000", "24998", 2, group="profile_weekly"),          # -8 ticks: ok
                            _r("previous session POC", "25050", "25052.25", 2, group="prev_profile"),   # 9: miss
                            _r("daily VAH", "24938", "24939", 2, group="profile_daily"),            # +4 ticks: ok
                            _r("weekly VAL", "24698.5", "24697.25", 2, group="profile_weekly"),     # -5 ticks: miss
                            _r("VWAP +1σ", "25001.5", "25002.25", 2)])                              # 3 ticks: miss
    assert [r["status"] for r in res] == ["ok", "ok", "miss", "ok", "miss", "miss"]
    assert [r["tolerance_ticks"] for r in res] == [8, 8, 8, 4, 4, "2"]


def test_compare_lvn_owner_row_far_from_any_bot_lvn():
    res = s12.compare_rows([_r("daily LVN", "24981.25", "24981.25", 4, group="lvn_daily"),
                            _r("daily LVN", "", "24990", 4, group="lvn_daily")])
    assert res[1]["status"] == "miss: no bot LVN near"


# --- POC near-ties (owner 2026-11-07) -------------------------------------------------------------------------------

def test_profile_volume_matches_the_developing_profile(monkeypatch):
    from nqbot.features import profile as pf
    # the step-12 tools read the features_version 36 build, whose POC / VAH / VAL were single ticks (the step-13 rows,
    # SPEC §13 Q-step12 (4), are 1-pt): the profile is built on single ticks here, as that build's was
    monkeypatch.setattr(pf, "PROFILE_ROW_TICKS", 1)
    start = st.cme_start_ns(DAY)
    rng = np.random.default_rng(7)
    ts = np.sort(rng.integers(start, ns(10, 3), 400))
    ts = np.append(ts, ns(10, 1))   # a trade exactly at the instant counts (ts_event <= t)
    ts.sort()
    price = 24600 + 0.25 * rng.integers(0, 40, len(ts))
    size = rng.integers(1, 5, len(ts)).astype(float)
    tape = st.Tape.from_arrays(DAY, ts, price, size)
    carried = {98400: 7.0, 98410: 3.0}
    t = ns(10, 1, 30)   # between instants: the 10:01:00 instant holds
    dp = pf.DevelopingProfile.build("weekly", tape, start - 3600 * 10**9, start, ns(10, 1), 60, None, carried)
    got = s12.profile_volume(tape, start - 3600 * 10**9, t, 60, carried)
    assert got == pytest.approx(dp.end_volume)
    assert s12.reproduces(got, "poc", dp.at(t).poc) and s12.reproduces(got, "vah", dp.at(t).vah)
    assert s12.reproduces(got, "val", dp.at(t).val)


def _ticks(d: dict[float, float]) -> dict[int, float]:
    return {int(round(p / 0.25)): v for p, v in d.items()}


def test_rows_whole_point_alignment_and_offsets():
    assert s12.row_start(98757, 4) == 98756 and s12.row_start(98756, 4) == 98756   # 24689.25 -> row 24689
    assert s12.row_start(98757, 4, offset=2) == 98754                              # rows from x.5: 24688.5
    assert s12.row_start(98763, 8) == 98760                                        # 2-pt rows at even points
    vol = _ticks({24675.0: 100.0, 24688.25: 50.0, 24688.75: 45.0})
    assert s12.group_rows(vol, 4) == {98700: 100.0, 98752: 95.0}
    assert s12.row_tie_pct(vol, 24688.5, 4) == pytest.approx(95.0)       # the row 24688-24688.75
    assert s12.row_tie_pct(vol, 24688.5, 4, offset=3) == pytest.approx(50.0)   # 24688.75 falls in the next row
    assert s12.row_tie_pct(vol, 24700.0, 4) == 0.0


def test_row_profile_on_rows_and_single_prices():
    # one big price at 24675.25, but the 24688 row holds more in all: the 1-pt POC is the 24688 row
    vol = _ticks({24675.25: 60.0, 24688.0: 40.0, 24688.25: 40.0, 24680.0: 10.0, 24695.0: 5.0})
    one = s12.row_profile(vol, 4)
    assert one["poc"] == (24688.0, 24688.75)
    assert s12.row_profile(vol, 1)["poc"] == (24675.25, 24675.25)   # the bot's own POC at single prices
    assert s12.reproduces(vol, "poc", 24675.25) and not s12.reproduces(vol, "poc", 24688.0)
    # value area row by row from the 24688 row (80 of 155, 70% = 108.5): empty rows tie and go both ways, the 24695
    # row (5) comes in before the 24680 row (10) is reached, then down to the 24675 row
    assert one["val"] == (24675.0, 24675.75) and one["vah"] == (24695.0, 24695.75)
    assert s12.row_profile({}, 4) is None


def test_mark_poc_ties_counts_statuses_and_weekly_2pt():
    rows = [_r("daily POC", "24675", "24688.5", 2, group="profile_daily"),           # row 24688: 95% -> near-tie
            _r("weekly POC", "25000", "25030", 2, group="profile_weekly"),          # row 25030: 50% -> other
            _r("previous session POC", "25050", "25050", 2, group="prev_profile"),  # within tolerance: untouched
            _r("daily VAH", "24938", "24950", 2, group="profile_daily")]            # not a POC row
    rows[0]["day"] = "3/6/2026"   # Excel's re-save of the day
    vols = {"daily": _ticks({24675.0: 100.0, 24688.5: 95.0}),
            "weekly": _ticks({25000.0: 10.0, 25030.5: 5.0, 25031.0: 4.0}), "prev": _ticks({25050.0: 1.0})}
    asked = []
    res = s12.compare_rows(rows)
    counts = s12.mark_poc_ties(res, lambda d: asked.append(d) or vols)
    assert counts == {"near-tie": 1, "other": 1, "unchecked": 0, "alignment-sensitive": 0}
    assert res[0]["status"] == "miss: POC near-tie" and res[0]["dc_row_pct_1pt"] == 95.0
    assert res[0]["dc_row_pct_2pt"] == ""   # 2-pt rows for the weekly only
    # weekly: 1-pt row 25030 holds 5 (50%); the 2-pt row 25030-25031.75 holds 9 (90%), reported but not the call
    assert res[1]["status"] == "miss: POC" and res[1]["dc_row_pct_1pt"] == 50.0 and res[1]["dc_row_pct_2pt"] == 90.0
    assert res[2]["status"] == "ok" and res[3]["status"] == "miss"
    assert asked == [DAY]   # one volume lookup per day, both day spellings the same day
    assert s12.row_day("2026-03-06") == s12.row_day("3/6/2026") == DAY


def test_mark_poc_ties_alignment_sensitive_exactly_90_and_unreproduced():
    # rows from whole points: 24688 row = 45 + 45 = 90 (90%, a near-tie); rows from x.25 split them: not a near-tie
    res = s12.compare_rows([_r("daily POC", "24675", "24688.5", 2, group="profile_daily"),
                            _r("weekly POC", "25000", "25010", 2, group="profile_weekly")])
    counts = s12.mark_poc_ties(res, lambda d: {"daily": _ticks({24675.0: 100.0, 24688.0: 45.0, 24688.5: 45.0}),
                                               "weekly": _ticks({25010.0: 9.0})})
    assert counts == {"near-tie": 1, "other": 0, "unchecked": 1, "alignment-sensitive": 1}
    assert res[0]["status"].startswith("miss: POC near-tie (not a near-tie with rows offset +1")
    assert "+1 tick 45.0" in res[0]["dc_row_pct_1pt_other_offsets"]
    assert res[1]["status"].startswith("miss: POC (bot volume not reproduced")


def test_row_evidence_only_filled_days_and_dc_inside_the_row():
    rows = [_r("daily POC", "24675", "24688.5", 2, group="profile_daily"),
            _r("daily VAL", "24675", "", 2, group="profile_daily")]
    other = _r("daily POC", "24675", "", 2, group="profile_daily")
    other["day"] = "2026-03-11"   # nothing filled in: no evidence rows for that day
    vols = {"daily": _ticks({24675.0: 100.0, 24688.0: 60.0, 24688.5: 55.0})}
    ev = s12.row_evidence(s12.compare_rows(rows + [other]), lambda d: vols)
    assert [e["level"] for e in ev] == ["daily POC", "daily VAL"] and {e["day"] for e in ev} == {"2026-03-06"}
    assert ev[0]["reproduced"] == "yes" and ev[0]["bot_1pt"] == "24688–24688.75" and ev[0]["bot_2pt"] == ""
    assert ev[0]["dc_outside_1pt_ticks"] == "0" and ev[0]["dc_vs_now_ticks"] == "54"
    assert ev[1]["deepcharts"] == "not read"
    assert ev[0]["pass_now"] is False and ev[0]["pass_1pt"] is True and ev[0]["in_1pt_row"] is True


def test_row_size_summary_counts_single_ticks_vs_1pt_rows():
    def e(level, now, one, inside, filled=True, rep="yes"):
        return {"level": level, "filled": filled, "reproduced": rep, "bot_1pt": "x", "pass_now": now,
                "pass_1pt": one, "in_1pt_row": inside}
    ev = [e("daily POC", False, True, True), e("weekly POC", True, True, False), e("daily VAH", True, False, False),
          e("daily VAL", False, True, False), e("weekly VAL", True, True, True, filled=False),   # not read: out
          e("previous session VAL", True, True, True, rep="NO")]                                # not reproduced: out
    line = s12.row_size_summary(ev)
    assert "of 4 filled" in line and "1-pt rows: 2 vs 3 (1 inside the row);" in line
    assert "POC 1 vs 2 (1 inside the row) of 2" in line and "VAH 1 vs 0 (0 inside the row) of 1" in line
    assert "VAL 0 vs 1 (0 inside the row) of 1" in line and "1 left out" in line


def test_roll_day_misses_marked_and_left_out_of_the_summary():
    """Owner 2026-11-08: the 03-17 weekly / previous-session POC, VAH, VAL misses and the weekly LVN misses are a
    roll-day definition difference; other levels and other days stay misses."""
    rows = [_r("weekly POC", "24850", "24890", 8, group="profile_weekly"),
            _r("previous session VAL", "24845", "24856.25", 4, group="prev_profile"),
            _r("daily VAL", "24728.25", "24730.5", 4, group="profile_daily"),                    # 9 ticks: a miss
            _r("weekly LVN", "24897.25", "24913.25", 4, group="lvn_weekly"),
            _r("weekly POC", "24850", "24850", 8, group="profile_weekly")]                       # ok stays ok
    for r in rows:
        r["day"] = "3/17/2026"
    other = _r("weekly POC", "25000", "25030", 8, group="profile_weekly")                         # 03-06: not a roll day
    res = s12.compare_rows(rows + [other])
    assert s12.mark_roll_day(res) == 3
    assert [r["status"] for r in res] == [s12.ROLL_DAY_STATUS, s12.ROLL_DAY_STATUS, "miss", s12.ROLL_DAY_STATUS, "ok",
                                          "miss"]
    ev = [{"level": "weekly POC", "filled": True, "reproduced": "yes", "bot_1pt": "x", "pass_now": False,
           "pass_1pt": False, "in_1pt_row": False, "roll_day": True},
          {"level": "daily POC", "filled": True, "reproduced": "yes", "bot_1pt": "x", "pass_now": True,
           "pass_1pt": True, "in_1pt_row": True}]
    line = s12.row_size_summary(ev)
    assert "of 1 filled" in line and "1 roll-day-definition values left out" in line


def test_csv_round_trip(tmp_path):
    rows = [s12._row(DAY, time(10, 1), "vwap", "VWAP 18:00", "vwap18-x", 24832.5442, "vwap")]
    p = tmp_path / "levels.csv"
    s12.write_csv(p, rows)
    back = s12.read_csv(p)
    assert back[0]["bot_value"] == "24832.5442" and back[0]["deepcharts_value"] == "" and back[0]["level"] == "VWAP 18:00"


# --- LVN lifetimes ---------------------------------------------------------------------------------------------

def _lvn(sub, times, prices, end):
    tape = st.Tape.from_arrays(DAY, [ns(9, 0)], [1.0])
    band = StepBand(tape, np.array(times, np.int64), np.array(prices) - 4, np.array(prices) + 4, end)
    return SimpleNamespace(sub_type=sub, state=SimpleNamespace(band=band))


def test_lvn_spans_and_histogram():
    levels = {
        "lvn_daily-2026-03-06-24980": [_lvn("lvn_daily", [ns(9, 40)], [24981.25], ns(9, 41)),
                                       _lvn("lvn_daily", [ns(10, 0), ns(10, 1), ns(10, 2)], [24980.0, 24981.0, 24982.0],
                                            ns(10, 3))],
        "lvn_weekly-2026-03-01-24700": [_lvn("lvn_weekly", [ns(2, 0) + k * 60 * 10**9 for k in range(200)],
                                             [24700.0] * 200, None)],
        "poc_daily-2026-03-06": [_lvn("poc_daily", [ns(9, 30)], [1.0], None)],
    }
    spans = s12.lvn_spans(levels, ns(9, 30), ns(16, 0), ns(17, 0))
    assert len(spans) == 3
    d1, d2, w = spans
    assert d1["lifetime_min"] == 1 and d1["instants"] == 1 and d1["identity"] == "24980"
    assert d2["lifetime_min"] == 3 and d2["price_min"] == 24980.0 and d2["price_max"] == 24982.0
    assert d2["last_ns"] == ns(10, 2) and d2["stop_ns"] == ns(10, 3)
    assert w["stop_ns"] is None and w["lifetime_min"] == (ns(17, 0) - ns(2, 0)) / 60e9 and w["rth"]
    h = s12.lifetime_histogram(spans)
    assert h["daily"]["1–2 min"] == 1 and h["daily"]["3–10 min"] == 1 and h["weekly"]["> 120 min"] == 1


# --- node types (F2.5 example) -----------------------------------------------------------------------------------

def test_classify_board_f25_example():
    values = {760.0: 100.0, 745.0: 40.0, 750.0: 50.0, 755.0: -30.0, 752.0: 15.0}
    got = s12.classify_board(values, 748.0, 0.25)
    assert got == {760.0: "king", 745.0: "significant", 750.0: "gatekeeper", 755.0: "gatekeeper", 752.0: "normal"}
    got = s12.classify_board(values, 757.0, 0.25)   # nothing between price and the King
    assert got[750.0] == "significant" and got[755.0] == "significant"


def test_count_board_tables_and_sign_check():
    rows = [{"strike": 760, "value": 100, "spot": 748, "node_type": "king"},
            {"strike": 750, "value": 50, "spot": 748, "node_type": "gatekeeper"},
            {"strike": 745, "value": 40, "spot": 748, "node_type": "gatekeeper"},
            {"strike": 740, "value": -5, "spot": 748, "node_type": "pika"},       # a sign violation
            {"strike": 730, "value": -4, "spot": 748, "node_type": "barney"},
            {"strike": 720, "value": 2, "spot": 748, "node_type": "normal"}]
    nc = s12.NodeCounts()
    s12.count_board(nc, "QQQ", rows, 0.25, "f")
    assert nc.king_ok["QQQ"] == [1]
    assert nc.king_2x2["QQQ"] == {(True, True): 1, (False, False): 5}
    assert nc.rel_2x2["QQQ"] == {(True, True): 2, (False, False): 3}
    assert nc.full["QQQ"][("gatekeeper", "gatekeeper")] == 1 and nc.full["QQQ"][("gatekeeper", "significant")] == 1
    assert len(nc.sign_bad) == 1 and "740" in nc.sign_bad[0]


def test_as_of_date_forms():
    assert s12._as_of_date("2026-03-06T14:30:00Z") == DAY
    assert s12._as_of_date(datetime(2026, 3, 6, 19, 0, tzinfo=st.ET)) == DAY


# --- scan, events, development months ------------------------------------------------------------------------

def test_first_with_earliest_day_in_calendar_order():
    have = {date(2025, 10, 9): {"flip"}, date(2025, 10, 3): {"redraw"}, date(2025, 10, 7): {"flip", "redraw"}}
    days = sorted(have, reverse=True)
    assert s12.first_with(days, lambda d: have[d], "flip") == date(2025, 10, 7)
    assert s12.first_with(days, lambda d: have[d], "redraw") == date(2025, 10, 3)
    assert s12.first_with(days, lambda d: set(), "flip") is None


def test_check_events_match():
    stored = [{"event_id": "a", "label": "held"}, {"event_id": "b", "label": "broke"}]
    s12.check_events_match(["b", "a"], stored, {"a": "held", "b": "broke"})
    with pytest.raises(s12.Refused):
        s12.check_events_match(["a"], stored, {"a": "held"})
    with pytest.raises(s12.Refused):
        s12.check_events_match(["a", "b"], stored, {"a": "held", "b": "timeout"})


def test_event_hover_names_id_zone_types_and_label():
    e = {"event_id": "20260306T150000Z-z3", "zone_id": "z3", "zone_lo": 1.0, "zone_hi": 2.0, "types": ["pdh", "ib_high"],
         "role": "ceiling", "direction": -1, "price": 1.5, "ts": ns(10, 0), "label": "broke", "how": "broke",
         "t_resolved": ns(10, 5)}
    h = s12.event_hover(e)
    assert "20260306T150000Z-z3" in h and "pdh, ib_high" in h and "<b>broke</b>" in h and "10:05:00" in h
    assert "short" in h


def test_development_months_only():
    env = SimpleNamespace(settings=S)
    with pytest.raises(NotDevelopmentMonth):
        s12.lvn_cmd(env, date(2026, 5, 4))     # ledger month 8 (walk-forward)


# --- single prints ---------------------------------------------------------------------------------------------

def _sp_tape():
    """Period 0 (18:00-18:30) sweeps 100 -> 105 (6 one-point bins), period 2 sweeps 200 -> 203 (4 bins); period 1
    trades only at 100 (fills bin 100 of the first run) and 112; later trades come back to 110 then 108."""
    t0 = st.cme_start_ns(DAY)
    m = 60 * 10**9
    ts, px = [], []
    for k, p in enumerate((100.0, 101, 102, 103, 104, 105)):
        ts.append(t0 + m + k * 10**9)
        px.append(p)
    ts += [t0 + 31 * m, t0 + 32 * m]
    px += [100.0, 112.0]
    for k, p in enumerate((200.0, 201, 202, 203)):
        ts.append(t0 + 61 * m + k * 10**9)
        px.append(p)
    ts += [t0 + 95 * m, t0 + 120 * m, t0 + 121 * m, t0 + 150 * m]
    px += [112.0, 110.0, 108.0, 150.0]
    return st.Tape.from_arrays(DAY, ts, px)


def test_single_print_variants_new_runs_sizes_and_carry():
    tape = _sp_tape()
    out = {}
    for n in (4, 6, 8):
        sp = tpo.SinglePrints(1.0, n, 60.0).run_session(tape, DAY, [], end_ns=st.cme_end_ns(DAY))
        out[n] = sp
    assert sorted(s12.new_runs(out[4], DAY)) == [4.0, 6.0]
    assert s12.new_runs(out[6], DAY) == [6.0]
    assert s12.new_runs(out[8], DAY) == []
    # the 100 bin was filled by the period-1 trade, the rest carries
    carried = out[6].carry()
    assert len(carried) == 1 and carried[0].filled == [True, False, False, False, False, False]


def test_single_print_standalone_touches():
    tape = _sp_tape()
    f = dict(F)
    sp6 = tpo.SinglePrints(1.0, 6, 60.0).run_session(tape, DAY, [], end_ns=st.cme_end_ns(DAY))
    sp8 = tpo.SinglePrints(1.0, 8, 60.0).run_session(tape, DAY, [], end_ns=st.cme_end_ns(DAY))
    n6, rth6 = s12.sp_touches(tape, sp6, f, None)
    assert n6 >= 1 and rth6 == 0           # price came back from 112 to 110 / 108 (within zone_proximity)
    assert s12.sp_touches(tape, sp8, f, None) == (0, 0)


# --- plots -----------------------------------------------------------------------------------------------------

def test_plots_write_png_and_html(tmp_path):
    t = np.arange(ns(9, 30), ns(10, 30), 60 * 10**9, dtype=np.int64)
    c = 100 + np.sin(np.arange(len(t)) / 5)
    bars = {"t": t, "open": c - 0.5, "high": c + 1, "low": c - 1, "close": c + 0.5}
    y = np.full(len(t), 100.0)
    off = np.full(len(t), np.nan)
    dp = s12.DayPlot(DAY, "NQH6", bars,
                     lines=[s12.Line("vwap18-x", "vwap18", t, y, off), s12.Line("qqq_node-x-600", "qqq_node", t, y + 1, off),
                            s12.Line("retested-x", "retested", t, off, y - 1)],
                     bands=[s12.Band("sp", "single_print", t, y - 2, y - 1.5),
                            s12.Band("golden", "golden_shallow", t, y - 0.5, y), s12.Band("z1", "zone", t, y - 3, y + 3)],
                     marks=[(ns(9, 45), "09:45 fib start")],
                     events=[{"event_id": "e1", "zone_id": "z1", "zone_lo": 99.0, "zone_hi": 101.0, "types": ["vwap18"],
                              "role": "floor", "direction": 1, "price": 100.0, "ts": ns(9, 50), "label": "held",
                              "how": "held", "t_resolved": ns(9, 55)}],
                     readings=[ns(9, 46)])
    for nodes in (False, True):
        p = tmp_path / f"p{nodes}.png"
        s12.render_png(dp, p, ns(9, 30), ns(10, 30), "test", nodes=nodes)
        assert p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    h = tmp_path / "p.html"
    s12.render_html(dp, h, "test")
    text = h.read_text(encoding="utf-8")
    assert "e1" in text and "event held (1)" in text


def test_group_of():
    assert s12.group_of("vwap18") == "vwap18" and s12.group_of("vwap18_up2") == "vwap18_band"
    assert s12.group_of("pdh") == "session_hl" and s12.group_of("ib_low") == "ib" and s12.group_of("em_up1") == "em"
    assert s12.group_of("ndxp_node") == "ndxp_node" and s12.group_of("single_print") == "single_print"


# --- events breakdown --------------------------------------------------------------------------------------------

def test_held_path_class_long_and_short():
    lo, hi = 100.0, 120.0
    # a floor (long, approached from above, near edge 120)
    assert s12.held_path_class(1, lo, hi, [123, 122, 121]) == "never entered"
    assert s12.held_path_class(1, lo, hi, [123, 118, 101, 119.5]) == "stayed inside"   # a move inside the zone
    assert s12.held_path_class(1, lo, hi, [123, 115, 121, 110, 125]) == "left on the near side"
    assert s12.held_path_class(1, lo, hi, [119, 99.5, 105, 121]) == "went past the far edge"
    # a ceiling (short, approached from below, near edge 100)
    assert s12.held_path_class(-1, lo, hi, [97, 98]) == "never entered"
    assert s12.held_path_class(-1, lo, hi, [97, 101, 119, 100.25]) == "stayed inside"
    assert s12.held_path_class(-1, lo, hi, [101, 120.5, 90]) == "went past the far edge"
    assert s12.held_path_class(1, lo, hi, []) == "never entered"


def test_depth_into_zone():
    assert s12.depth_into_zone(1, 100, 120, 110) == (10.0, 0.5)
    assert s12.depth_into_zone(1, 100, 120, 125) == (-5.0, -0.25)     # never entered
    assert s12.depth_into_zone(-1, 100, 120, 126) == (26.0, 1.3)      # past the far edge
    assert s12.depth_into_zone(1, 100, 100, 99)[1] is None


def test_profile_lvn_spans_and_ages():
    t = [ns(10, k) for k in range(5)]
    snaps = [SimpleNamespace(lvns=((24980.0, 24981.0),)), SimpleNamespace(lvns=((24980.0, 24982.0), (24900.0, 24901.0))),
             SimpleNamespace(lvns=((24900.0, 24901.0),)), SimpleNamespace(lvns=((24980.0, 24979.0),)),
             SimpleNamespace(lvns=((24980.0, 24979.0),))]
    spans = s12.profile_lvn_spans(SimpleNamespace(times=np.array(t, np.int64), snaps=snaps))
    got = sorted((s["identity"], s["times"][0], s["stop"]) for s in spans)
    assert got == [(24900.0, t[1], t[3]), (24980.0, t[0], t[2]), (24980.0, t[3], None)]
    # at 10:01:30 the 24980 LVN (2 instants old, at 24982) overlaps a zone 24985-24990 with hw 4; 24900 doesn't
    assert s12.lvn_ages_at(spans, ns(10, 1, 30), 24985.0, 24990.0, 4.0) == [1.5]
    assert s12.lvn_ages_at(spans, ns(10, 2, 30), 24985.0, 24990.0, 4.0) == []     # the 24980 span stopped at 10:02
    assert s12.lvn_ages_at(spans, ns(10, 4), 24970.0, 24980.0, 4.0) == [1.0]     # the new span from 10:03


def test_zone_groups_and_event_record():
    r = {"zone_lo": 100.0, "zone_hi": 110.0, "direction": 1, "ts": ns(10, 1, 30), "n_lvn_daily": 1, "n_pdh": 1,
         "n_vwap18_up1": 0, "in_golden_zone": False}
    assert s12.zone_groups(r) == {"LVN", "highs/lows"}
    assert s12.zone_groups({"n_vwap0930_dn2": 2, "in_golden_zone": True}) == {"VWAP band", "golden zone"}
    spans = {"daily": [{"identity": 105.0, "times": [ns(10, 0), ns(10, 1)], "prices": [104.0, 105.0], "stop": None}],
             "weekly": []}
    e = s12.event_record(r, {"label": "held", "extreme": 104.0}, True, [111, 108, 104, 109.5], spans, 4.0)
    assert e["path"] == "stayed inside" and e["depth"] == 6.0 and e["lvn_ages"] == [1.5] and not e["lvn_only"]
    e = s12.event_record({**r, "n_pdh": 0}, {"label": "broke", "extreme": 95.0}, False, None, spans, 4.0)
    assert e["path"] is None and e["lvn_only"] and e["depth_share"] == 1.5
