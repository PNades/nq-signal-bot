"""F3.10 day-type inputs and rows (step 11 part 3)."""

from __future__ import annotations

from datetime import date, time
from types import SimpleNamespace

import numpy as np

from nqbot.features import daytype_rows as dr
from nqbot.features.session_tape import MIN_NS, NS, Tape, bars, et_ns
from nqbot.features.vwap import Vwap

DAY = date(2026, 3, 20)
F = {"ib_lock_minute": 30, "ib_avg_sessions": 2, "daytype_cross_bar_minutes": 5, "rotation_min_pts": 0.25,
     "daytype_snapshot_min": 15, "trend_max_vwap_crosses": 2, "trend_close_pct": 0.2,
     "er_bar_minutes": 5, "er_windows_min": [30, 60, 120], "vr_q": 6, "vr_sessions": 10}


def test_zigzag_three_rotations():
    # ATR 100, rotation_min_pts 0.25 -> 25 pts: 0 -> 100 -> 0 -> 100 -> 0: the first move sets the direction,
    # then 3 rotations
    path = [0, 50, 100, 60, 0, 50, 100, 70, 0]
    p = np.repeat(np.array(path, float), 1)
    ts = np.arange(len(p), dtype=np.int64) * NS
    assert len(dr.zigzag_times(ts, p, 25.0)) == 3
    assert dr.zigzag_times(ts, p, 0) == []


def _session(prices, start=time(9, 30), step_s=60):
    o = et_ns(DAY, start)
    ts = o + np.arange(len(prices), dtype=np.int64) * step_s * NS
    tape = Tape.from_arrays(DAY, ts, np.asarray(prices, float))
    cs = SimpleNamespace(tape=tape, open_ns=et_ns(DAY, time(9, 30)), close_ns=et_ns(DAY, time(16, 0)),
                         bars_1m=bars(tape, 1), vwap={"vwap0930": Vwap.build(tape, et_ns(DAY, time(9, 30)))},
                         daily=SimpleNamespace(at=lambda t: None), atr=100.0, em=SimpleNamespace(points=200.0))
    return cs


def test_new_highs_lows_skew_and_shares():
    # 1-minute bars: 7 new highs then 2 new lows (the first bar counts for neither)
    prices = [100, 101, 102, 103, 104, 105, 106, 107, 99, 98]
    cs = _session(prices)
    d = dr.DayType(cs, F, [10.0, 10.0])
    t = cs.open_ns + 10 * MIN_NS
    x = d.at(t)
    assert (x["new_highs"], x["new_lows"], x["new_extreme_skew"]) == (7, 2, 5)
    assert x["range_vs_em"] == (107 - 98) / 200.0
    assert 0.5 <= x["vwap_side_share"] <= 1.0
    assert x["ib_width_ratio"] is None   # before the 10:00 lock


def test_time_above_vwap_fraction():
    # price above its own running VWAP for 8 of 10 one-minute stretches after a drop from 110 to 100 then a rise
    prices = [100] + [110] * 9 + [100]
    cs = _session(prices)
    d = dr.DayType(cs, F, [])
    x = d.at(cs.open_ns + 10 * MIN_NS)
    assert abs(x["time_above_vwap_frac"] - 0.9) < 1e-9 and abs(x["vwap_side_share"] - 0.9) < 1e-9


def test_ib_width_ratio_and_snapshots():
    prices = [100 + (k % 7) for k in range(60)]
    cs = _session(prices)
    d = dr.DayType(cs, F, [3.0, 9.0])
    x = d.at(cs.open_ns + 31 * MIN_NS)
    assert x["ib_width_ratio"] == 6.0 / 6.0
    snaps = d.snapshot_times()
    assert snaps[0] == cs.open_ns + 30 * MIN_NS and snaps[1] - snaps[0] == 15 * MIN_NS and snaps[-1] < cs.close_ns


def test_eod_label_trend_day():
    # a steady up day: new highs every minute, no VWAP cross, close at the high -> trend
    prices = list(np.linspace(100, 200, 390))
    cs = _session(prices)
    e = dr.DayType(cs, F, []).eod()
    assert e["day_type"] == "trend" and e["trend_side"] == "up" and e["vwap_crosses"] == 0


def test_efficiency_ratio_so_far_and_windows():
    # 5-minute closes from an open of 100: 105, 110, 105, 110, 115, 120 (one trade per 5 minutes, at the bar's start)
    prices = [100, 105, 110, 105, 110, 115, 120]
    cs = _session(prices, step_s=300)
    d = dr.DayType(cs, F, [])
    # at 10:05 the first six bars have closed (09:30 ... 09:55 starts): closes 100 (the open's own bar), 105, ... 115
    x = d.er_at(cs.open_ns + 30 * MIN_NS)
    c = [100, 100, 105, 110, 105, 110, 115]   # the open, then the closed bars' closes
    moves = sum(abs(b - a) for a, b in zip(c, c[1:]))
    assert abs(x["er_so_far"] - abs(c[-1] - c[0]) / moves) < 1e-12
    w = d.er_at(cs.open_ns + 35 * MIN_NS)
    # er_30 over the last 30 minutes of closed bars: from the close at 09:35 (100, the bar that ended there) to 120
    seg = [100, 105, 110, 105, 110, 115, 120]
    assert abs(w["er_30"] - abs(seg[-1] - seg[0]) / sum(abs(b - a) for a, b in zip(seg, seg[1:]))) < 1e-12
    assert w["er_60"] is None and d.er_at(cs.open_ns + 4 * MIN_NS)["er_so_far"] is None   # no bar closed yet


def test_variance_ratio_random_walk_and_trend():
    rng = np.random.default_rng(1)
    rw = [rng.normal(0, 1e-3, 78) for _ in range(10)]
    assert abs(dr.variance_ratio(rw, 6, 10) - 1.0) < 0.2
    trend = [np.convolve(rng.normal(0, 1e-3, 83), np.ones(6) / 6, "valid") for _ in range(10)]   # smoothed noise
    assert dr.variance_ratio(trend, 6, 10) > 2.0      # positively autocorrelated returns
    assert dr.variance_ratio(rw[:9], 6, 10) is None     # fewer than the prior 10 full sessions


def test_vr_prior_reads_only_full_completed_sessions():
    rng = np.random.default_rng(2)
    cs = _session([100, 101, 102])
    cs.carry = SimpleNamespace(rth5=[{"day": f"d{k}", "full": k != 3, "r": rng.normal(0, 1e-3, 78).tolist()}
                                     for k in range(11)])
    d = dr.DayType(cs, F, [])
    assert d.vr_prior is not None and d.at(cs.open_ns)["vr_prior"] == d.vr_prior
    cs.carry = SimpleNamespace(rth5=[{"day": "x", "full": True, "r": [0.001] * 78}] * 9)
    assert dr.DayType(cs, F, []).vr_prior is None
