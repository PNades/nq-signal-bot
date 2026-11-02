"""Regression tests for the independent review of step 11 part 3 (2026-11-01, SPEC §13 Q-step11g): each finding fixed
with a hand-built case."""

from __future__ import annotations

from datetime import date, time
from types import SimpleNamespace

import numpy as np

from nqbot.features import freshness
from nqbot.features import level_state as ls
from nqbot.features import registry as rg
from nqbot.features import session_levels as sl
from nqbot.features import structure_label as slab
from nqbot.features import touches as tch
from nqbot.features import zones as zn
from nqbot.features.bands import StepBand, TradeBand
from nqbot.features.gameplan_features import zones_of
from nqbot.features.node_levels import _track
from nqbot.features.session_features import SessionFeatures
from nqbot.features.session_tape import MIN_NS, NS, Tape, bars, et_ns
from nqbot.features.zones import ZoneBook

DAY = date(2026, 3, 20)
P = ls.Params(rearm_dist=10, flip_margin=0, flip_bar_minutes=5, sweep_max_depth=15, sweep_reclaim_bars=3)


def at(h, m, s=0.0):
    return et_ns(DAY, time(h, m)) + int(s * NS)


def tape_of(points):
    ts, px = zip(*points)
    return Tape.from_arrays(DAY, np.array(ts, dtype=np.int64), np.array(px, float))


def reg_level(tape, uid, kind, band, role=None):
    st = ls.LevelState(uid, kind, tape, band, P, bars(tape, 1), bars(tape, 5), initial_role=role,
                       end_ns=int(tape.ts[-1]) + MIN_NS).run()
    return rg.RegLevel(uid, uid, uid, "x", "session_hl", "NQ", kind, False, band, st, _track(st),
                       spans=rg.active_spans(st, band, int(tape.ts[-1]) + MIN_NS))


# --- touches: an unresolved far-side break never holds the near side back (F5.6) -----------------------------------

def test_near_side_rearms_while_the_far_side_waits_for_a_break():
    # PDH ceiling [100, 100.5], prox 2, rearm 10: 85 -> 99 (touch) -> 105 -> 113 (an unresolved sweep: the far side
    # can't re-arm) -> 85 (the near side re-arms) -> 99 (a new touch). The skip used to jump over the 85.
    pts = [(at(10, 0, 0), 85), (at(10, 0, 10), 99), (at(10, 0, 20), 105), (at(10, 0, 30), 113),
           (at(10, 0, 40), 85), (at(10, 0, 50), 99)]
    tape = tape_of(pts)
    pdh = reg_level(tape, "PDH", ls.SWEEP, StepBand.const(tape, int(tape.ts[0]), 100, 100.5), role=ls.CEILING)
    book = ZoneBook([pdh.book_level()], tape, lambda t: (10.0, 60.0), end_ns=int(tape.ts[-1]) + MIN_NS).run()
    zt = tch.ZoneTouches(tape, book, {"PDH": pdh}, 2.0, 10.0).run()
    assert [x.idx for x in zt.touches] == [1, 5]
    assert all(x.side == -1 for x in zt.touches)


# --- registry: a VWAP line is a member from its first value (F5.3) -----------------------------------------------

def test_moving_line_active_from_its_first_value():
    # the VWAP is anchored at 09:30:00.000 but no trade happens exactly then: the line exists from the first trade
    tape = tape_of([(at(9, 30, 0.4 + k), 100.0 + 0.25 * k) for k in range(30)])
    c = np.full(len(tape), np.nan)
    c[3:] = 101.0
    band = TradeBand(tape, c, 1.0, at(9, 30))
    lv = reg_level(tape, "vwap0930", ls.CHART, band)
    assert lv.spans and lv.spans[0][0] == int(tape.ts[3])


# --- zones: a relation flip at the first trade after a span starts (F5.3) -------------------------------------------

def test_zone_book_flip_at_the_first_trade_after_a_span_start():
    ts = at(10, 0) + np.arange(20, dtype=np.int64) * NS
    tape = Tape.from_arrays(DAY, ts, np.full(20, 100.0))
    c = np.where(np.arange(20) > 9, 101.0, 50.0)    # the moving line jumps onto L at trade 10
    mv = TradeBand(tape, c, 1.0, int(ts[0]))
    a = int(ts[9]) + NS // 2                         # L's span starts between trades 9 and 10
    L = StepBand.const(tape, a, 100.0, 104.0)
    end = int(ts[-1]) + NS

    def mk():
        return ZoneBook([zn.BookLevel("V", mv, [(int(ts[0]), end)]),
                         zn.BookLevel("L", L, [(a, end)], change_times=L.times)], tape, lambda t: (1.0, 60.0),
                        end_ns=end)
    fast, ref = mk().run(), mk().run(every_trade=True)
    t10 = int(ts[10])
    assert [sorted(m) for m in ref.zones_at(t10).values()] == [["L", "V"]]
    assert fast.zones_at(t10) == ref.zones_at(t10)


# --- freshness: a zone id is unique within one session only (F5.5, F5.11) -------------------------------------------

def test_zone_tests_so_far_counts_today_only():
    old = freshness.TouchRecord(1, ("pdh-x",), "z12", "floor", 2, "held")      # yesterday's z12
    today = freshness.TouchRecord(100, ("poc-y",), "z12", "floor", 200, "held")
    out = freshness.zone_freshness([old, today], ["pdh-x"], "floor", "z12", 300, zone_touches=[today])
    assert out["zone_tests_so_far"] == 1 and out["tests_so_far_max"] == 1
    assert isinstance(out["tests_so_far_mean"], float)


# --- F3.8: retests accepted earliest first (causal, as live) ---------------------------------------------------------

class _RetestFake(SessionFeatures):
    def __init__(self):   # noqa: D107 (bypasses the engine: only the retest loop is under test)
        self.f, self.passes, self.retested_today, self.day = {"dp_in_model": False}, 0, [], DAY
        self.retest_source, self._mine_uids, self.made = {}, set(), []
        levels: dict = {}

        def const(lid, sub_type, price, start, multi_day):
            self.made.append(lid)
            levels.setdefault(lid, []).append(SimpleNamespace(state=SimpleNamespace(band=SimpleNamespace(start_ns=start))))
        self.c = SimpleNamespace(cs_nq=SimpleNamespace(_const=const, start_ns=0, levels=levels))

    def _build_once(self):
        pass

    def _new_retests(self):
        have = {r.level_id for r in self.retested_today}
        if "R1" in have:
            return []          # with R1 in force, the touch that made R2 is gone (R1 moved the zone onto price)
        return [sl.Retested("R2", 1.0, "pdh", date(2026, 3, 19), 20), sl.Retested("R1", 2.0, "pdl", date(2026, 3, 19), 10)]


def test_retests_are_accepted_earliest_first():
    sf = _RetestFake().build()
    assert [r.level_id for r in sf.retested_today] == ["R1"] and sf.made == ["R1"] and sf.passes == 2


def test_retested_level_id_survives_a_roll():
    cid = "pdh-2026-03-18-20000"
    before = sl.retests_from_touches([sl.Candidate(20_000.0, "pdh", date(2026, 3, 18), cid)],
                                     [{"price": 20_000.25, "t_resolved": 5, "label": "held"}], DAY,
                                     [date(2026, 3, 18), DAY], 4, 10)
    after = sl.retests_from_touches([sl.Candidate(20_250.0, "pdh", date(2026, 3, 18), cid)],     # shifted by a roll
                                    [{"price": 20_250.0, "t_resolved": 5, "label": "held"}], DAY,
                                    [date(2026, 3, 18), DAY], 4, 10)
    assert before[0].level_id == after[0].level_id == f"retested_extreme-{cid}"


# --- F10: earlier golden-zone touches only, also at a later decision_ts ---------------------------------------------

def test_golden_history_counts_earlier_touches_only():
    mk = lambda t, eid: tch.Touch("g1", t, 0, 100.0, 1, (), 99.0, 101.0, kind="golden", part="shallow", event_id=eid)
    prev, e, nxt = mk(50, "a"), mk(100, "b"), mk(150, "c")
    labels = {"a": slab.Label("a", "held", 60, 0, 5.0, "held"), "b": slab.Label("b", "broke", 120, 0, 0, "broke"),
              "c": slab.Label("c", "held", 160, 0, 0, "held")}
    fake = SimpleNamespace(events=[prev, e, nxt], labels=labels, zone_edges=lambda *_: None,
                           nq=SimpleNamespace(last_price_at=lambda t: 1.0), es=SimpleNamespace(last_price_at=lambda t: 1.0),
                           c=SimpleNamespace(sky=None))
    out = SessionFeatures._golden(fake, e, 200, [])     # a pair's decision_ts after its own label and a later touch
    assert out["gz_touch_number"] == 2 and out["gz_prev_held"] == 1 and out["gz_prev_broke"] == 0
    assert out["gz_prev_pending"] == 0 and out["gz_last_run_pts"] == 5.0


# --- F5.13: no Skylit = node makeup unknown, not "no King" ------------------------------------------------------------

def test_makeup_without_skylit_is_null():
    tape = tape_of([(at(10, 0), 100.0), (at(10, 1), 100.0)])
    poc = reg_level(tape, "poc_weekly-w", ls.CHART, StepBand.const(tape, int(tape.ts[0]), 98, 102), role=ls.CEILING)
    fake = SimpleNamespace(_node_info=lambda t: {}, _sp_stacked=lambda m, t: 0,
                           c=SimpleNamespace(sky=None, cs_nq=SimpleNamespace(atr=100.0, em=SimpleNamespace(points=200.0))))
    out = SessionFeatures._makeup(fake, [poc], at(10, 0, 30))
    assert out["king_present"] is None and out["gatekeeper_present"] is None and out["stack_count"] == 1


# --- F8: two gameplan entries with one label stay two containers ----------------------------------------------------

def test_gameplan_duplicate_labels_stay_separate():
    zs = zones_of({"zones": [{"id": "E1", "zone": [100, 102]}, {"id": "E1", "zone": [90, 92]}]})
    assert [z.gid for z in zs] == ["gp_E1", "gp_E1_2"]


# --- Q-step11g (1) answered (owner 2026-11-01): one retested extreme per price -----------------------------------

def test_candidates_one_per_price_newest_kept():
    d1, d2, d3 = date(2026, 3, 16), date(2026, 3, 17), date(2026, 3, 18)
    cands = [sl.Candidate(20_000.0, "prior_week_high", d1), sl.Candidate(20_000.0, "prior_week_high", d2),
             sl.Candidate(20_000.75, "prior_week_high", d3),            # 3 ticks away: the same extreme
             sl.Candidate(19_900.0, "pdh", d2), sl.Candidate(19_900.0, "prior_rth_high", d2),   # one price, one session
             sl.Candidate(19_801.25, "on_low", d1), sl.Candidate(19_800.0, "on_low", d2)]       # 5 ticks: two extremes
    kept = sl.dedupe_candidates(cands, 4)
    assert [(c.price, c.session) for c in kept if c.price >= 19_999] == [(20_000.75, d3)]
    assert len([c for c in kept if c.price == 19_900.0]) == 1
    assert sorted(c.price for c in kept if c.price < 19_850) == [19_800.0, 19_801.25]
    assert sl.dedupe_candidates(kept, 4) == kept     # idempotent


class _NewRetests(SessionFeatures):
    def __init__(self, levels, cands, touches):   # noqa: D107 (only _new_retests is under test)
        self.f = {"retest_tol_ticks": 4, "retest_lookback_sessions": 10}
        self.day, self.retested_today, self.nq_levels, self.exclude_source = DAY, [], levels, None
        prev = date(2026, 3, 19)
        self.c = SimpleNamespace(retested=[], retest_candidates=cands, sessions=[prev, DAY])
        self.events = [tch.Touch("z1", t, k, p, 1, (), p - 1, p + 1, event_id=f"e{k}") for k, (t, p) in
                       enumerate(touches)]
        self.labels = {e.event_id: slab.Label(e.event_id, "held", e.t + 10, 0, 0, "held") for e in self.events}


def test_no_retested_extreme_at_a_high_or_low_already_there():
    tape = tape_of([(at(10, 0), 100.0), (at(12, 0), 100.0)])
    pdh = reg_level(tape, "pdh-x", ls.SWEEP, StepBand.const(tape, int(tape.ts[0]), 20_000.0, 20_000.0))
    pdh.key = "pdh_pdl"
    prev = date(2026, 3, 19)
    cands = [sl.Candidate(20_000.5, "on_high", prev, "a"), sl.Candidate(19_950.0, "on_low", prev, "b")]
    sf = _NewRetests({"pdh-x": pdh}, cands, [(at(10, 30), 20_000.5), (at(11, 0), 19_950.25)])
    got = sf._new_retests()
    assert [r.price for r in got] == [19_950.0]          # 20,000.5 is within 4 ticks of today's PDH: no new level
    assert got[0].source == "e1"


def test_retested_extreme_source_round_trip():
    from nqbot.features.session_features import _retested_json, retested_from_json
    r = sl.Retested("retested_extreme-x", 1.0, "pdh", date(2026, 3, 19), 5, "20260320T150000Z-z3")
    assert retested_from_json(_retested_json(r)) == r



# --- second review (2026-11-01, SPEC §13 Q-step11h) ---------------------------------------------------------------

def test_candidate_merge_is_the_same_whatever_order_sessions_arrive():
    # A, B, C each 3 ticks apart (A and C 6 ticks: two extremes): merged at once -> {A, C}; the checkpoints keep the
    # candidates as they come and merge only when used, so session-by-session arrival gives the same
    d1, d2, d3 = date(2026, 3, 16), date(2026, 3, 17), date(2026, 3, 18)
    A, B, C = (sl.Candidate(20_000.0, "on_high", d1), sl.Candidate(20_000.75, "on_high", d2),
               sl.Candidate(20_001.5, "on_high", d3))
    assert [c.price for c in sl.dedupe_candidates([A, B, C], 4)] == [20_000.0, 20_001.5]
    tape = tape_of([(at(10, 0), 100.0), (at(10, 1), 100.0)])
    fake = SimpleNamespace(f={"retest_lookback_sessions": 10, "retest_tol_ticks": 4}, end_ns=int(tape.ts[-1]) + 1,
                           nq_levels={}, retested_today=[], records=[], day=d3, eod=lambda: None,
                           _candidates=lambda: [C],
                           c=SimpleNamespace(retested=[], history=[], sessions=[d1, d2, d3], retest_candidates=[A, B]))
    kept = SessionFeatures.end_state(fake)["retest_candidates"]
    assert sorted(x["price"] for x in kept) == [20_000.0, 20_000.75, 20_001.5]     # raw: merged at use only


def test_zone_book_flip_after_a_parameter_switch():
    # the confluence tolerance rises from 4 to 8 at trade 5; a moving line 10 from a level until trade 12, then 7: the
    # merge at trade 12 exists only under the new tolerance, so the flip must be judged with the parameters in force
    # then (judged with the span's first ones, no candidate instant appeared there)
    ts = at(10, 0) + np.arange(20, dtype=np.int64) * NS
    tape = Tape.from_arrays(DAY, ts, np.full(20, 100.0))
    c = np.where(np.arange(20) >= 12, 99.0, 96.0)
    mv = TradeBand(tape, c, 0.5, int(ts[0]))
    L = StepBand.const(tape, int(ts[0]), 105.5, 106.5)
    end = int(ts[-1]) + NS
    switch = int(ts[5])

    def mk():
        return ZoneBook([zn.BookLevel("V", mv, [(int(ts[0]), end)]),
                         zn.BookLevel("L", L, [(int(ts[0]), end)], change_times=L.times)], tape,
                        lambda t: (8.0 if t >= switch else 4.0, 60.0), param_times=(switch,), end_ns=end)
    fast, ref = mk().run(), mk().run(every_trade=True)
    for t in ts.tolist():
        assert fast.zones_at(t) == ref.zones_at(t)
    assert len(fast.zones_at(int(ts[12]))) == 1 and len(fast.zones_at(int(ts[11]))) == 2
