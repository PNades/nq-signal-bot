"""The step-13b labeler on hand-built examples (SPEC §5.4b, §5.5b, §6.1-§6.6; feature_spec F7, F11.1-F11.3; owner
2026-11-10, SPEC §13 Q-step13b, Q-flexfarm (1)): fills by order type, exits (stop, target, flat-by, the news flatten,
`order_latency_sec`), breakeven rules, candidates, the low-RR extension's identity check, and a brute-force trade loop
the labeler must agree with."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import time

import numpy as np
import pytest

from nqbot.checks import feature_build as fb
from nqbot.checks import step13b as s13b
from nqbot.clients.mbp1 import TopOfBook
from nqbot.config import load_features, load_settings
from nqbot.features import session_tape as st
from nqbot.labeler import candidates as cand
from nqbot.labeler import fills as fl
from nqbot.labeler import labeler as lb
from nqbot.labeler import params as lp
from nqbot.labeler.costs import Costs, be_ticks
from nqbot.labeler.firsthit import PathIndex
from nqbot.labeler.outcomes import Labeler, market_exit, timeout_at
from tests.test_touches_labels import DAY

NS = st.NS
S, F = load_settings(), load_features()
LAT = int(S["order_latency_sec"] * NS)


def at(h, m, s=0.0):
    return st.et_ns(DAY, time(h, m)) + int(s * NS)


def tape_of(trades):
    return st.Tape.from_arrays(DAY, [t for t, _ in trades], [p for _, p in trades], size=[1] * len(trades))


def meta(**kw):
    return {"day": DAY.isoformat(), "flat_ns": at(15, 59), "entry_end_ns": at(15, 45), "open_ns": at(9, 30),
            "close_ns": at(16, 0), "end_ns": at(17, 0), "blackouts": [], "nq_symbol": "NQH6", **kw}


def dp(**kw):
    """A long decision point at a floor zone [100, 102] (near edge 102), decided at 10:00."""
    base = {"decision_id": "E-d1", "event_id": "E", "touch_group_id": "g1", "style": "d", "firing_n": 1,
            "decision_ts": at(10, 0), "direction": 1, "event_type": "level", "tradeable": True,
            "zone_lo": 100.0, "zone_hi": 102.0, "zone_lo_src": "chart", "zone_hi_src": "chart", "zone_center": 101.0,
            "near_edge": 102.0, "price_at_decision": 102.5, "swing_high": 110.0, "swing_low": 101.0,
            "fib_0": None, "fib_-0.272": None, "fib_-0.618": None, "fib_1": None,
            "z_id": ["za", "zb"], "z_kind": ["level", "level"], "z_lo": [80.0, 130.0], "z_hi": [90.0, 134.0],
            "z_lo_src": ["chart", "chart"], "z_hi_src": ["chart", "chart"],
            "t_move_near": None, "t_move_center": None, "t_vanish": None, "t_cross": None,
            "trigger_bar_high": None, "trigger_bar_low": None}
    base.update(kw)
    return base


# the hand-built examples below are written at 1 tick of exit slippage and a 1-tick breakeven offset, whatever the
# frozen config values are (owner 2026-11-11 froze both at 4; `test_the_frozen_slippage_values_flow_into_the_costs`)
P = lp.params(S, F)
P = replace(P, costs=replace(P.costs, exit_slip_ticks=1.0), be_offset_ticks=1.0)


def P_(**kw):
    return replace(P, **kw)


# --- first hits ------------------------------------------------------------------------------------------------------

def test_first_hits_equal_a_plain_loop():
    rng = np.random.default_rng(1)
    p = np.round(np.cumsum(rng.normal(0, 1, 5000)) / 0.25) * 0.25
    ix = PathIndex(p, block=64)
    for _ in range(400):
        i, j = sorted(rng.integers(0, len(p), 2))
        x = float(rng.choice(p))
        want_ge = next((k for k in range(i, j) if p[k] >= x), None)
        want_le = next((k for k in range(i, j) if p[k] <= x), None)
        assert ix.first_ge(i, x, j) == want_ge and ix.first_le(i, x, j) == want_le
        if j > i:
            for d in (1, -1):
                v, k = ix.extreme(i, j, d)
                seg = p[i:j]
                best = seg.max() if d > 0 else seg.min()
                assert v == best and k == i + int(np.flatnonzero(seg == best)[0])


# --- costs -----------------------------------------------------------------------------------------------------------

def test_costs_follow_section_6_1():
    c = Costs(1.90, 1.0, 4.0, 333.0)
    assert c.risk_per_micro(10.0, "limit") == pytest.approx(10 * 2 + 1.90 + 0.5)
    assert c.risk_per_micro(10.0, "stop_limit") == pytest.approx((10 + 1.0) * 2 + 1.90 + 0.5)
    assert c.net_rr(10.0, 20.0, "limit") == pytest.approx((40 - 1.90) / 22.4)
    assert c.size(10.0, "limit") == (14, 2, False)                  # floor(333 / 22.4) = 14 micros -> 2 NQ-equivalent
    assert c.size(400.0, "limit")[2] is True                        # not one micro fits: too wide
    assert c.stop_fill(100.0, 1) == 99.75 and c.stop_fill(100.0, -1) == 100.25
    # the gap rule: never better than the triggering trade
    assert c.stop_fill(100.0, 1, 98.0) == 98.0 and c.stop_fill(100.0, 1, 100.0) == 99.75
    assert c.stop_fill(100.0, -1, 101.5) == 101.5 and c.stop_fill(100.0, -1, 100.0) == 100.25
    assert be_ticks(1.90, 1) == 5                                    # ceil(1.90 / 0.50) + 1


# --- fills -----------------------------------------------------------------------------------------------------------

def test_a_passive_limit_needs_a_trade_through_after_arrival():
    d0 = dp()
    trades = [(at(10, 0, 0.5), 101.75),      # through the limit before arrival (10:00:01): no fill
              (at(10, 0, 2), 102.0),          # at the limit: no fill
              (at(10, 0, 3), 101.75)]         # through it: filled at 102
    tape = tape_of(trades)
    f = fl.simulate("limit", d0, 1.0, tape, PathIndex(tape.price), None, meta(), P)
    assert (f.status, f.px, f.idx, f.on_trade) == ("filled", 102.0, 2, True)


def test_the_time_to_live_and_its_latency():
    d0 = dp()
    ttl = int(P.ttl_sec["d"] * NS)
    for dt, want in ((ttl + LAT - 1, "filled"), (ttl + LAT, "unfilled")):
        tape = tape_of([(at(10, 0) + dt, 101.75)])
        f = fl.simulate("limit", d0, 1.0, tape, PathIndex(tape.price), None, meta(), P)
        assert f.status == want, dt
        assert f.removal_reason == "ttl"


def test_a_zone_move_entry_end_and_a_blackout_remove_the_order():
    tape = tape_of([(at(10, 0, 30), 101.75)])
    f = fl.simulate("limit", dp(t_move_near=at(10, 0, 10)), 1.0, tape, PathIndex(tape.price), None, meta(), P)
    assert (f.status, f.removal_reason, f.removal_t) == ("unfilled", "moved", at(10, 0, 10) + LAT)
    f = fl.simulate("limit", dp(decision_ts=at(15, 44, 50)), 1.0, tape_of([(at(15, 45, 0.5), 101.75)]),
                    PathIndex(np.array([101.75])), None, meta(), P)
    assert (f.status, f.removal_reason) == ("filled", "entry_end")     # 15:45:00.5 < 15:45 + latency
    m = meta(blackouts=[[at(10, 0, 20), at(11, 0)]])
    f = fl.simulate("limit", dp(), 1.0, tape, PathIndex(tape.price), None, m, P)
    assert (f.status, f.removal_reason) == ("unfilled", "blackout")


def test_stop_limit_fills_at_the_ask_then_the_limit():
    d0 = dp(price_at_decision=102.5)              # style (d): trigger 1 tick above 102.5 = 102.75, limit + 4 ticks
    book = TopOfBook.from_arrays([at(10, 0, 4)], [102.75], [103.0], [5], [1])
    tape = tape_of([(at(10, 0, 5), 102.75)])
    f = fl.simulate("stop_limit", d0, 3.0, tape, PathIndex(tape.price), book, meta(), P)
    assert (f.ref, f.limit) == (102.75, 103.75)
    assert f.status == "filled" and f.px == pytest.approx((1 * 103.0 + 2 * 103.75) / 3)   # 1 at the ask, 2 at the cap
    # the ask beyond the limit: rests at the limit, filled by a later trade through it
    book2 = TopOfBook.from_arrays([at(10, 0, 4)], [104.0], [104.25], [5], [5])
    tape2 = tape_of([(at(10, 0, 5), 102.75), (at(10, 0, 9), 103.5)])
    f = fl.simulate("stop_limit", d0, 3.0, tape2, PathIndex(tape2.price), book2, meta(), P)
    assert (f.status, f.px, f.idx) == ("filled", 103.75, 1)
    # no book: the triggering trade's price, flagged
    f = fl.simulate("stop_limit", d0, 3.0, tape, PathIndex(tape.price), None, meta(), P)
    assert (f.status, f.px, f.entry_no_book) == ("filled", 102.75, True)


def test_a_marketable_limit_beyond_the_displayed_size_and_without_a_book():
    d0 = dp()
    book = TopOfBook.from_arrays([at(9, 59), at(10, 0, 0.5)], [102.25, 102.5], [102.5, 102.75], [9, 9], [9, 2])
    tape = tape_of([(at(10, 0, 3), 103.0)])
    f = fl.simulate("marketable_limit", d0, 5.0, tape, PathIndex(tape.price), book, meta(), P)
    assert f.ref == 102.5 and f.limit == 103.5                       # the ask at decision_ts, + 4 ticks
    assert f.status == "filled" and f.t == at(10, 0) + LAT and not f.on_trade
    assert f.px == pytest.approx((2 * 102.75 + 3 * 103.5) / 5)       # the ask at arrival for its size, the rest at cap
    f = fl.simulate("marketable_limit", d0, 5.0, tape, PathIndex(tape.price), None, meta(), P)
    assert f.status == "no_book"


# --- exits -----------------------------------------------------------------------------------------------------------

def label_one(trades, d0, stop, target, ot="limit", m=None, size=1.0, book=None, p=P):
    tape = tape_of(trades)
    path = PathIndex(tape.price)
    m = m or meta()
    f = fl.simulate(ot, d0, size, tape, path, book, m, p)
    lab = Labeler(tape, path, st.bars(tape, 1), book, m, p)
    rpm = p.costs.risk_per_micro(abs(f.ref - stop), ot)
    out = lab.label(f, stop, target, 1, rpm, cand.first_structure(d0, f.ref, target, 1), size)
    return f, out


FILL = (at(10, 0, 3), 101.75)


def test_stop_first_and_target_needs_one_tick_through():
    f, out = label_one([FILL, (at(10, 5), 130.0), (at(10, 6), 130.25)], dp(), 95.0, 130.0)
    assert out["out_none"] == "target" and out["t_exit_none"] == at(10, 6)       # touched at 10:05, through at 10:06
    f, out = label_one([FILL, (at(10, 5), 130.0), (at(10, 6), 95.0)], dp(), 95.0, 130.0)
    assert out["out_none"] == "stop"
    assert out["r_none"] == pytest.approx(((94.75 - 102.0) * 2 - 1.90) / P.costs.risk_per_micro(7.0, "limit"))


def test_a_gap_through_the_stop_fills_at_the_trade():
    # long from 102, stop 95: price gaps from 99 to 92.5 (the first trade at or beyond the stop)
    f, out = label_one([FILL, (at(10, 1), 99.0), (at(10, 2), 92.5), (at(10, 3), 96.0)], dp(), 95.0, 130.0)
    rpm = P.costs.risk_per_micro(7.0, "limit")
    assert out["out_none"] == "stop" and out["t_exit_none"] == at(10, 2)
    assert out["r_none"] == pytest.approx(((92.5 - 102.0) * 2 - 1.90) / rpm)       # not 95 - 1 tick
    assert out["gap_ticks_none"] == pytest.approx((94.75 - 92.5) / 0.25)
    # a touch exactly at the stop: the plain rule (stop - exit_slippage_ticks), no gap cost
    f, out = label_one([FILL, (at(10, 2), 95.0)], dp(), 95.0, 130.0)
    assert out["r_none"] == pytest.approx(((94.75 - 102.0) * 2 - 1.90) / rpm) and out["gap_ticks_none"] == 0.0
    # a short whose stop at 110 is gapped to 112
    d_short = dp(direction=-1, zone_lo=100.0, zone_hi=102.0, near_edge=100.0, price_at_decision=99.5,
                 swing_high=101.0, z_lo=[80.0], z_hi=[90.0], z_id=["za"], z_kind=["level"], z_lo_src=["chart"],
                 z_hi_src=["chart"])
    tape = tape_of([(at(10, 0, 3), 100.25), (at(10, 2), 112.0)])
    path = PathIndex(tape.price)
    fs = fl.simulate("limit", d_short, 1.0, tape, path, None, meta(), P)
    lab = Labeler(tape, path, st.bars(tape, 1), None, meta(), P)
    o = lab.label(fs, 110.0, 85.0, -1, P.costs.risk_per_micro(10.0, "limit"), None, 1.0)
    assert o["out_none"] == "stop" and o["gap_ticks_none"] == pytest.approx((112.0 - 110.25) / 0.25)


def test_a_breakeven_stop_gapped_through_fills_at_the_trade():
    # +1R breakeven from 102 / stop 95: the signal at 109.25 (10:01), effective 10:01:01; BE stop 103.25; a gap to 100
    trades = [FILL, (at(10, 1), 109.25), (at(10, 1, 30), 106.0), (at(10, 2), 100.0)]
    f, out = label_one(trades, dp(), 95.0, 130.0)
    assert out["out_r1"] == "be" and out["t_exit_r1"] == at(10, 2)
    assert out["r_r1"] == pytest.approx(((100.0 - 102.0) * 2 - 1.90) / P.costs.risk_per_micro(7.0, "limit"))
    assert out["gap_ticks_r1"] == pytest.approx((103.0 - 100.0) / 0.25)


def test_a_market_exit_never_fills_better_than_the_first_trade():
    # the flat-by exit at 15:59:01: the book's bid says 104.75, but the first trade at or after it is 103.0
    book = TopOfBook.from_arrays([at(15, 58)], [104.75], [105.0], [10], [10])
    f, out = label_one([FILL, (at(15, 59, 1.5), 103.0), (at(15, 59, 3), 106.0)], dp(), 95.0, 130.0, book=book)
    assert out["out_none"] == "timeout" and out["gap_ticks_none"] == pytest.approx((104.75 - 103.0) / 0.25)
    assert out["r_none"] == pytest.approx(((103.0 - 102.0) * 2 - 1.90) / P.costs.risk_per_micro(7.0, "limit"))
    # the book worse than the trade: the book stands, no gap cost
    book = TopOfBook.from_arrays([at(15, 58)], [102.5], [102.75], [10], [10])
    f, out = label_one([FILL, (at(15, 59, 1.5), 103.0)], dp(), 95.0, 130.0, book=book)
    assert out["gap_ticks_none"] == 0.0
    assert out["r_none"] == pytest.approx(((102.5 - 102.0) * 2 - 1.90) / P.costs.risk_per_micro(7.0, "limit"))


def test_a_sweep_through_the_stop_fills_at_the_sweeps_last_print():
    # owner 2026-11-11 (Q-step13b (d)): long from 102, stop 95; one aggressor at 10:02 prints 95.0, 94.5, 93.75, 93.0
    # under one timestamp: the first print is at the stop, the sweep leaves 93.0
    t = at(10, 2)
    trades = [FILL, (at(10, 1), 99.0), (t, 95.0), (t, 94.5), (t, 93.75), (t, 93.0), (at(10, 2, 1), 96.0)]
    p4 = replace(P, costs=replace(P.costs, exit_slip_ticks=4.0))
    f, out = label_one(trades, dp(), 95.0, 130.0, p=p4)
    rpm = p4.costs.risk_per_micro(7.0, "limit")
    assert out["out_none"] == "stop" and out["t_exit_none"] == t
    assert out["r_none"] == pytest.approx(((93.0 - 102.0) * 2 - 1.90) / rpm)         # not 95 - 4 ticks = 94.0
    assert out["gap_ticks_none"] == pytest.approx((94.0 - 93.0) / 0.25)
    # a sweep that ends within the slippage allowance changes nothing: 95.0, 94.5 -> 95 - 4 ticks = 94.0 stands
    f, out = label_one([FILL, (t, 95.0), (t, 94.5), (at(10, 3), 93.0)], dp(), 95.0, 130.0, p=p4)
    assert out["r_none"] == pytest.approx(((94.0 - 102.0) * 2 - 1.90) / rpm) and out["gap_ticks_none"] == 0.0
    # a short: one aggressor buys 110.0 -> 111.5 through the stop at 110
    d_short = dp(direction=-1, zone_lo=100.0, zone_hi=102.0, near_edge=100.0, price_at_decision=99.5,
                 swing_high=101.0, z_lo=[80.0], z_hi=[90.0], z_id=["za"], z_kind=["level"], z_lo_src=["chart"],
                 z_hi_src=["chart"])
    tape = tape_of([(at(10, 0, 3), 100.25), (t, 110.0), (t, 110.75), (t, 111.5)])
    path = PathIndex(tape.price)
    fs = fl.simulate("limit", d_short, 1.0, tape, path, None, meta(), p4)
    o = Labeler(tape, path, st.bars(tape, 1), None, meta(), p4).label(
        fs, 110.0, 85.0, -1, p4.costs.risk_per_micro(10.0, "limit"), None, 1.0)
    assert o["out_none"] == "stop" and o["gap_ticks_none"] == pytest.approx((111.5 - 111.0) / 0.25)


def test_a_calm_stop_fills_at_the_stop_less_the_slippage():
    # one print at the stop and nothing else under its timestamp: stop - exit_slippage_ticks, no gap cost; a later
    # print (another timestamp) further away is not part of the sweep
    p4 = replace(P, costs=replace(P.costs, exit_slip_ticks=4.0))
    f, out = label_one([FILL, (at(10, 2), 95.0), (at(10, 2, 0.001), 90.0)], dp(), 95.0, 130.0, p=p4)
    assert out["out_none"] == "stop"
    assert out["r_none"] == pytest.approx(((94.0 - 102.0) * 2 - 1.90) / p4.costs.risk_per_micro(7.0, "limit"))
    assert out["gap_ticks_none"] == 0.0


def test_a_market_exit_never_fills_better_than_the_sweeps_last_print():
    # the flat-by exit at 15:59:01: the bid says 104.75 (size 10); the first prints at or after it are one sweep at
    # 15:59:01.5, 104.5 -> 103.25: the exit fills at 103.25
    book = TopOfBook.from_arrays([at(15, 58)], [104.75], [105.0], [10], [10])
    t = at(15, 59, 1.5)
    f, out = label_one([FILL, (t, 104.5), (t, 103.75), (t, 103.25), (at(15, 59, 3), 106.0)], dp(), 95.0, 130.0,
                       book=book)
    assert out["out_none"] == "timeout" and out["gap_ticks_none"] == pytest.approx((104.75 - 103.25) / 0.25)
    assert out["r_none"] == pytest.approx(((103.25 - 102.0) * 2 - 1.90) / P.costs.risk_per_micro(7.0, "limit"))
    assert market_exit(tape_of([FILL, (t, 104.5), (t, 103.25)]), book, at(15, 59) + LAT, 1, 1.0) == (103.25, False)
    # a short covered at market: the sweep goes up, the worse price is the higher one
    assert market_exit(tape_of([FILL, (t, 105.25), (t, 106.0)]), book, at(15, 59) + LAT, -1, 1.0) == (106.0, False)


def test_the_frozen_slippage_values_flow_into_the_costs():
    p = lp.params(S, F)
    assert p.costs.exit_slip_ticks == S["exit_slippage_ticks"] and p.be_offset_ticks == S["be_offset_ticks"]
    # §6.6: a breakeven exit at the frozen values nets at or above $0 per micro after costs
    net = (be_ticks(p.costs.commission_micro, p.be_offset_ticks) - p.costs.exit_slip_ticks) * 0.5 \
        - p.costs.commission_micro
    assert net >= 0


def test_the_labeler_counts_what_the_gap_rule_changes():
    tape = tape_of([FILL, (at(10, 1), 99.0), (at(10, 2), 92.5)])
    out = lb.label_session([dp()], tape, None, meta(), P_(order_types={"d": ["limit"], "c": []}))
    g = out["info"]["gap_rule"]["stop"]
    assert g["exits"] > 0 and g["changed"] > 0 and g["mean_extra_ticks"] > 0


def test_entry_and_stop_on_one_print_is_filled_and_stopped():
    f, out = label_one([(at(10, 0, 3), 94.0)], dp(), 95.0, 130.0)                 # a gap through limit and stop
    assert out["ambiguous_tick"] and out["out_none"] == "stop"


def test_the_flat_by_and_the_news_flatten_take_effect_after_the_latency():
    f, out = label_one([FILL, (at(15, 59, 0.5), 104.0), (at(15, 59, 2), 105.0)], dp(), 95.0, 130.0)
    assert (out["out_none"], out["t_exit_none"], out["timeout_reason"]) == ("timeout", at(15, 59) + LAT, "flat_by")
    assert out["r_none"] == pytest.approx(((105.0 - 102.0) * 2 - 1.90) / P.costs.risk_per_micro(7.0, "limit"))
    # a release at 11:00: the news flatten at 11:00 - 30 min - 30 s, effective `order_latency_sec` later
    rel = at(11, 0)
    a = rel - (S["blackout_before_min"] * 60 + S["news_flatten_lead_sec"]) * NS
    m = meta(blackouts=[[a, rel + 60 * 60 * NS]])
    f, out = label_one([FILL, (a + LAT // 2, 103.0), (a + LAT, 104.0)], dp(), 95.0, 130.0, m=m)
    assert (out["out_none"], out["t_exit_none"], out["timeout_reason"]) == ("timeout", a + LAT, "news_flatten")
    # an early-close day: the session's flat-by is 12:59 (calendar_flags), the label's timeout follows it
    m = meta(flat_ns=at(12, 59))
    assert timeout_at(at(10, 0), m, LAT) == (at(12, 59) + LAT, "flat_by")


def test_the_early_close_flat_by_and_entry_end_come_from_the_calendar():
    from datetime import date

    from nqbot import calendar as cal_mod
    from nqbot.features import calendar_flags as cf

    cal = cal_mod.load()
    ec = next(date.fromisoformat(str(x["date"])) for x in cal["early_closes"])
    assert cf.flat_by(ec, cal, S).strftime("%H:%M") == "12:59"
    assert cf.entry_end(DAY, cal, S).strftime("%H:%M") == "15:45"


def test_market_exit_uses_the_book_size_then_the_trades():
    tape = tape_of([(at(10, 0, 1), 99.5), (at(10, 0, 2), 99.25)])
    book = TopOfBook.from_arrays([at(10, 0)], [99.75], [100.0], [2], [2])
    from nqbot.labeler.outcomes import _market_raw

    raw, nb = _market_raw(tape, book, at(10, 0, 0.5), 1, 4.0)          # the book's size, then the trades
    assert not nb and raw == pytest.approx((2 * 99.75 + 99.5 + 99.25) / 4)
    px, nb = market_exit(tape, book, at(10, 0, 0.5), 1, 4.0)           # never better than the first trade (99.5)
    assert not nb and px == 99.5
    px, nb = market_exit(tape, None, at(10, 0, 0.5), 1, 1.0)
    assert nb and px == 99.5


def test_breakeven_rules():
    # the F11.3 example: long from 102 (limit), target 140, a zone [115, 117] between; a bar opening 117.25 and
    # closing 118 -> the breakeven signal at its close, effective order_latency_sec later; then a trade at the
    # breakeven stop (102 + 5 ticks) exits it
    d0 = dp(z_id=["zm", "zb"], z_kind=["level", "level"], z_lo=[115.0, 150.0], z_hi=[117.0, 152.0],
            z_lo_src=["chart"] * 2, z_hi_src=["chart"] * 2)
    trades = [FILL, (at(10, 3, 0), 117.25), (at(10, 3, 50), 118.0), (at(10, 4, 0.5), 110.0), (at(10, 4, 2), 103.0),
              (at(10, 5), 94.0)]
    f, out = label_one(trades, d0, 95.0, 140.0)
    assert out["out_none"] == "stop"
    assert out["out_fs"] == "be" and out["t_exit_fs"] == at(10, 4, 2)          # BE stop 103.25 active from 10:04:01
    assert out["r_fs"] == pytest.approx(((103.25 - 0.25 - 102.0) * 2 - 1.90) / P.costs.risk_per_micro(7.0, "limit"))
    # +1R: the first trade >= 102 + 7 = 109 (10:03:00, 117.25), effective 10:03:01
    assert out["out_r1"] == "be" and out["t_exit_r1"] == at(10, 4, 2)
    # the target the first structure itself: no breakeven
    f, out = label_one(trades, d0, 95.0, 116.0)
    assert out["out_fs"] == out["out_none"] == "target"


# --- candidates ------------------------------------------------------------------------------------------------------

def test_stop_candidates_floors_and_merge():
    p1 = P_(stop_buffer=1.0)
    s = cand.stops(dp(zone_lo_src="QQQ"), 102.0, 1, p1)
    assert dict((t, x) for x, t in s)["2"] == 100.0 - 4.25                         # the QQQ floor beats a 1-pt buffer
    s = cand.stops(dp(zone_lo_src="NDXP"), 102.0, 1, p1)
    assert dict((t, x) for x, t in s)["2"] == 93.5                                 # 100 - 6.50
    # swing 101 - 5 = 96, far edge 100 - 5 = 95 (4 ticks apart: two stops); 3 = the next zone below's far edge 80 - 5
    s = cand.stops(dp(), 102.0, 1, P)
    assert s == [(96.0, "1"), (95.0, "2"), (75.0, "3")]
    s = cand.stops(dp(swing_low=100.25), 102.0, 1, P)                              # 95.25 vs 95.0: within 2 ticks
    assert s == [(95.25, "1,2"), (75.0, "3")]
    s = cand.stops(dp(event_type="golden", fib_1=90.0), 102.0, 1, P)
    assert (85.0, "6") in s


def test_target_counting_baseline_and_the_low_rr_extension():
    # stop 95 (7 pts): zones ahead at [104, 106] (RR < 0.8 at both prices), [108, 120] (near 108 < 0.8, centre 114 >=),
    # [130, 134] (both >= 0.8)
    d0 = dp(z_id=["z1", "z2", "z3"], z_kind=["level"] * 3, z_lo=[104.0, 108.0, 130.0], z_hi=[106.0, 120.0, 134.0],
            z_lo_src=["chart"] * 3, z_hi_src=["chart"] * 3)
    t = cand.targets(d0, 102.0, 95.0, 1, "limit", P)
    base = [x for x in t if not x["rr_ext"]]
    assert [(x["target_zone"], x["target_kind"], x["target"]) for x in base] == [
        ("z2", "zone_center", 114.0), ("z3", "zone_near", 130.0), ("z3", "zone_center", 132.0),
        (None, "baseline", 119.5)]
    ext = [x for x in t if x["rr_ext"]]
    assert {x["target_zone"] for x in ext} == {"z1"} and all(0.25 <= x["net_rr"] < 0.8 for x in ext)
    assert cand.targets(d0, 102.0, 95.0, 1, "limit", replace(P, extension=False)) == base
    t1 = cand.targets(d0, 102.0, 95.0, 1, "limit", P_(max_targets=1))
    assert [x["target_zone"] for x in t1 if not x["rr_ext"] and x["target_zone"]] == ["z2"]
    # golden: the fib 0 / extensions with RR >= 0.8, outside the count
    g = cand.targets(dp(event_type="golden", **{"fib_0": 125.0, "fib_-0.272": 140.0, "fib_-0.618": 103.0}),
                     102.0, 95.0, 1, "limit", P_(max_targets=0))
    assert [x["target_kind"] for x in g if not x["rr_ext"]] == ["fib_0", "fib_-0.272", "baseline"]
    assert lb.rr_band(0.3) == "<0.5" and lb.rr_band(0.6) == "0.5-0.8" and lb.rr_band(0.8) == ">=0.8"


def test_the_extension_leaves_the_base_pairs_and_labels_identical():
    rng = np.random.default_rng(7)
    trades = [(at(10, 0, 2 + k), 100 + float(np.round(x / 0.25) * 0.25))
              for k, x in enumerate(np.cumsum(rng.normal(0, 1.5, 3000)))]
    tape = tape_of(trades)
    zs = sorted(rng.uniform(60, 160, 14))
    d0 = dp(z_id=[f"z{k}" for k in range(14)], z_kind=["level"] * 14, z_lo=zs, z_hi=[z + 2 for z in zs],
            z_lo_src=["chart"] * 14, z_hi_src=["chart"] * 14)
    decs = [d0, {**d0, "decision_id": "E-c1", "style": "c", "trigger_bar_high": 103.0, "trigger_bar_low": 101.0}]
    on = lb.label_session(decs, tape, None, meta(), P)
    off = lb.label_session(decs, tape, None, meta(), replace(P, extension=False))
    chk = lb.identity_check(on["pairs"], off["pairs"])
    assert chk["identity_ok"] and chk["ext_rows"] > 0
    assert sum(on["info"]["rr_bands"].values()) == len(on["pairs"])


# --- the brute-force cross-check -----------------------------------------------------------------------------------

def _brute(tape, d0, ot, stop, target, m, p):
    """F7 and §5.5b read literally, one trade at a time (no index, no cache)."""
    d = 1
    arr = d0["decision_ts"] + p.latency_ns
    rem, _ = fl.removal(d0, ot, m, p)
    ref, limit = fl.reference(ot, d0, None, p)
    fill_k = None
    if ot == "limit":
        for k in range(len(tape)):
            t = tape.ts[k]
            if arr <= t < rem and tape.price[k] < limit:
                fill_k, px = k, limit
                break
    else:   # stop_limit, no book: the triggering trade's price if within the limit, else rest at the limit
        trig_k = next((k for k in range(len(tape)) if arr <= tape.ts[k] < rem and tape.price[k] >= ref), None)
        if trig_k is not None:
            if tape.price[trig_k] <= limit:
                fill_k, px = trig_k, float(tape.price[trig_k])
            else:
                fill_k = next((k for k in range(trig_k + 1, len(tape)) if tape.ts[k] < rem and tape.price[k] < limit),
                              None)
                px = limit
    if fill_k is None:
        return None
    t_x = timeout_at(int(tape.ts[fill_k]), m, p.latency_ns)[0]
    for k in range(fill_k, len(tape)):
        if tape.ts[k] >= t_x:
            break
        if tape.price[k] <= stop:
            return ("stop", int(tape.ts[k]), px)
        if k > fill_k and tape.price[k] >= target + 0.25:
            return ("target", int(tape.ts[k]), px)
    return ("timeout", t_x, px)


@pytest.mark.parametrize("seed", range(6))
def test_the_labeler_agrees_with_a_trade_by_trade_loop(seed):
    rng = np.random.default_rng(seed)
    n = 4000
    ts = at(10, 0, 1.2) + np.cumsum(rng.integers(50, 3000, n)) * 1_000_000
    px = 102 + np.round(np.cumsum(rng.normal(0, 0.6, n)) / 0.25) * 0.25
    tape = st.Tape.from_arrays(DAY, ts, px, size=np.ones(n))
    m = meta(flat_ns=int(ts[n // 2]))
    d0 = dp(z_lo=[60.0, 104.0, 108.0, 115.0], z_hi=[70.0, 105.0, 111.0, 119.0], z_id=list("abcd"),
            z_kind=["level"] * 4, z_lo_src=["chart"] * 4, z_hi_src=["chart"] * 4)
    out = lb.label_session([d0], tape, None, m, P_(order_types={"d": ["limit", "stop_limit"], "c": []}))
    rows = [r for r in out["pairs"]]
    assert rows
    for r in rows:
        want = _brute(tape, d0, r["order_type"], r["stop"], r["target"], m, P)
        if want is None:
            assert not r["filled"]
            continue
        assert r["filled"], r["pair_id"]
        assert (r["out_none"], r["t_exit_none"]) == want[:2], r["pair_id"]


# --- the labeler key --------------------------------------------------------------------------------------------

def test_the_labeler_reads_only_its_keyed_settings():
    class Rec(dict):
        def __init__(self, d):
            super().__init__(d)
            self.read: set = set()

        def __getitem__(self, k):
            self.read.add(k)
            return super().__getitem__(k)

        def get(self, k, default=None):
            self.read.add(k)
            return super().get(k, default)

    s, f = Rec(S), Rec(F)
    lp.params(s, f)
    assert s.read <= set(lp.SETTING_KEYS) and f.read <= set(lp.FEATURE_KEYS)
    assert set(lp.SETTING_KEYS) <= set(fb.NON_BUILD_SETTING_KEYS) | {"order_latency_sec", "max_target_candidates",
                                                                     "pair_build_min_rr"}


def test_the_label_key_changes_with_each_covered_setting():
    k0 = lp.label_key("b", P)
    for k, v in (("stop_buffer", 8), ("entry_ttl_sec.c", 30), ("exit_slippage_ticks", 2), ("pair_ext_min_rr", 0.3)):
        assert lp.label_key("b", lp.params(S, F, {k: v})) != k0, k
    assert lp.label_key("other build", P) != k0
    with pytest.raises(KeyError):
        lp.params(S, F, {"zone_proximity": 3})
    assert "events" in fb.CODE_PARTS and "labeler" not in fb.CODE_PARTS


def test_slippage_market_fill_takes_the_book_then_the_trades():
    tape = tape_of([(at(10, 0, 1), 99.75), (at(10, 0, 2), 99.5), (at(10, 0, 3), 99.25)])
    tape = st.Tape(tape.day, tape.symbol, tape.ts, tape.price, np.array([1.0, 2.0, 5.0]), tape.side)
    book = TopOfBook.from_arrays([at(10, 0)], [99.75], [100.0], [2], [3])
    px = s13b.market_fill(tape, book, 0, -1, 5.0)                    # a long's stop sells 5
    assert px == pytest.approx((2 * 99.75 + 2 * 99.5 + 1 * 99.25) / 5)
    assert math.isfinite(px) and (100.0 - px) / 0.25 > 0       # a stop at 100: slippage against the trade


def test_the_slippage_proposal_follows_the_sweep_gap_rule():
    # owner 2026-11-11 (Q-step13b (d)): the labeler charges max(S, the gap to the sweep's last print); the proposal is
    # the smallest S covering the measured fill on >= 95% of the stops. 100 long stops at 100: 90 calm (1 tick, sweep at
    # the stop), 10 swept 8 ticks (measured 8, sweep's last print 98.0): S = 1 covers them all under the sweep rule,
    # while the first-print rule needs S = 8
    rows = [{"slip_es1": 1.0, "direction": 1, "stop": 100.0, "first_px": 100.0, "sweep_px": 100.0}] * 90 + \
           [{"slip_es1": 8.0, "direction": 1, "stop": 100.0, "first_px": 100.0, "sweep_px": 98.0}] * 10
    prop, cov = s13b.sweep_rule_proposal(rows, 1)
    assert prop == 1 and cov[1][0] == pytest.approx(1.0) and cov[1][2] == pytest.approx(0.9)
    # the sweep ends short of the fill (book thinner than the prints): measured 8, sweep's last print 2 ticks through
    rows2 = rows[:90] + [{**rows[-1], "sweep_px": 99.5}] * 10
    prop2, cov2 = s13b.sweep_rule_proposal(rows2, 1)
    assert prop2 == 8 and cov2[2][0] == pytest.approx(0.9)


def test_the_budget_wall_leaves_out_time_between_sessions():
    # one pass-2 chunk: four sessions in parallel 0-100 s, two more 100-160 s, then the PC asleep 13 h before the chunk
    # was recorded: the wall is 160 s, not the recorded chunk time
    iv = [(0, 100), (5, 60), (2, 90), (1, 99), (100, 160), (101, 130)]
    assert s13b.union_seconds(iv) == 160
    assert s13b.union_seconds([(0, 10), (50, 70)]) == 30 and s13b.union_seconds([]) == 0
