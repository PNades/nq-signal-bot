"""Volume baselines (owner 2026-10-17, §13 Q-featB B1; feature_spec F9.10): hand-built examples."""

from datetime import date, timedelta

import numpy as np
import pytest

from nqbot.config import load_features
from nqbot.features import volume_norms as vn


def _normals():
    """A typical minute of 1,000; the first 30 minutes 3,000; the last 10 minutes 2,000."""
    n = np.full(vn.RTH_MINUTES, 1000.0)
    n[:30] = 3000.0
    n[380:] = 2000.0
    return n


def test_minute_normals_are_medians_per_minute():
    sessions = [np.full(vn.RTH_MINUTES, v) for v in (100.0, 300.0, 200.0)]
    sessions[1][0] = 10_000.0  # one spike in one session moves no median
    n = vn.minute_normals(sessions)
    assert n[0] == 200.0 and n[1] == 200.0


def test_baseline_sessions_skip_early_closes_holidays_and_damaged_sessions_and_need_twenty():
    days = [date(2025, 9, 1) + timedelta(days=i) for i in range(40)]
    early = {days[5]}
    damaged = {days[7]}
    got = vn.baseline_sessions(days[30], days, full=lambda d: d not in early, damaged=damaged, n=20)
    assert len(got) == 20 and got[-1] == days[29] and days[5] not in got and days[7] not in got
    assert got[0] == days[10]  # the 20 most recent eligible days: 10 .. 29
    assert vn.baseline_sessions(days[15], days, full=lambda d: True, damaged=set(), n=20) is None


def test_window_normal_is_pro_rated_and_null_outside_rth():
    n = _normals()
    # (1785, 1845]: 15 s of minute 29 (3,000) + 45 s of minute 30 (1,000) → 750 + 750
    assert vn.window_normal(n, 1845, 60) == pytest.approx(1500.0)
    assert vn.window_normal(n, 30, 5) == pytest.approx(250.0)  # 5 s of the first minute
    assert vn.window_normal(n, 3, 5) is None  # starts before 09:30
    assert vn.window_normal(n, vn.RTH_SECONDS + 1, 5) is None


def test_vol_tod_and_the_time_of_day_part():
    n = _normals()
    assert vn.vol_tod(6000.0, n, 120, 60) == pytest.approx(2.0)  # 6,000 in a minute whose normal is 3,000
    assert vn.tod_norm(n, 120, 60) == pytest.approx(3.0)  # the open: 3× a typical minute
    assert vn.tod_norm(n, 10_000, 5) == pytest.approx(1.0)  # mid-day: a typical stretch
    assert vn.tod_norm(n, vn.RTH_SECONDS, 60) == pytest.approx(2.0)  # the last minute


def test_rvol_today_clips_each_minute_and_pro_rates_the_current_one():
    n = _normals()
    v = np.full(vn.RTH_MINUTES, 3000.0)
    v[1] = 60_000.0  # a news spike: clipped at 5 × 3,000
    r = vn.rvol_today(v, partial=1500.0, s=150, normals=n, clip_mult=5)  # 2 full minutes + half of the third
    assert r == pytest.approx((3000 + 15_000 + 1500) / (3000 + 3000 + 1500))
    assert vn.rvol_today(v, 0.0, 0, n, 5) is None


def test_the_scale_caps_todays_part_only():
    """Owner 2026-10-17: scale = tod_norm × clip(rvol_today, 0.5, 2.0); at the open a quiet day still scales up 3×."""
    n = _normals()
    f = load_features()
    floor, cap = f["of_scale_floor"], f["of_scale_cap"]
    assert vn.of_scale(n, 120, 60, 1.0, floor, cap) == pytest.approx(3.0)  # not capped at 2
    assert vn.of_scale(n, 120, 60, 4.0, floor, cap) == pytest.approx(6.0)  # today's part capped at 2
    assert vn.of_scale(n, 10_000, 30, 0.2, floor, cap) == pytest.approx(0.5)  # today's part floored at 0.5
    assert vn.of_scale(n, 120, 60, None, floor, cap) is None


def test_big_trade_thresholds_by_slot_are_clipped_to_the_session_value():
    rng = np.random.default_rng(0)
    secs = rng.uniform(0, vn.RTH_SECONDS, 20_000)
    size = np.where(secs < 1800, 40.0, 10.0)  # the first slot trades bigger orders
    size[::50] = 15.0
    th = vn.big_trade_slot_thresholds([(secs, size)], 30, 0.5, 2.0)
    whole = np.percentile(size, 99)
    assert len(th) == 13 and th[0] == pytest.approx(min(40.0, 2.0 * whole))
    assert th[5] == pytest.approx(max(np.percentile(size[(secs >= 9000) & (secs < 10800)], 99), 0.5 * whole))


def test_b1_keys_are_the_owners():
    f = load_features()
    assert (f["vol_baseline_sessions"], f["rvol_clip_mult"], f["of_scale_floor"], f["of_scale_cap"]) == (20, 5, 0.5, 2.0)
    assert f["of_context_windows_sec"] == [5, 60]
    assert (f["big_trade_slot_min"], f["big_trade_slot_floor"], f["big_trade_slot_cap"]) == (30, 0.5, 2.0)
