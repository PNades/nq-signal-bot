"""Step-8b mapping maths (feature_spec F1.2, F1.3, F1.6, F2.3; §12 step 8b), hand-built examples."""

import numpy as np
import pytest

from nqbot.features import mapping as m

NS = m.NS


def test_outlier_rejection_and_reaccept_f12():
    """F1.2 test case: 40.80 x5, then 40.95 rejected (36.8 bps); two more at 40.95 -> the second is accepted."""
    r = [40.80] * 5 + [40.95, 40.95, 40.95, 40.951]
    acc, rejected, shifted = m.accept_ratios(np.array(r), outlier_bps=3, reaccept_n=3)
    assert acc.tolist() == [True] * 5 + [False, False, True, True]  # history reset to 40.95: 40.951 is close
    assert rejected == 2 and shifted
    assert abs(40.95 - 40.80) / 40.80 * 1e4 == pytest.approx(36.76, abs=0.01)


def test_reaccept_needs_agreeing_rejections_and_nan_is_skipped():
    acc, rejected, shifted = m.accept_ratios(np.array([40.80, np.nan, 40.95, 41.10, 40.95, 40.801]), 3, 3)
    assert acc.tolist() == [True, False, False, False, False, True]  # the 3 rejections disagree: no re-accept
    assert rejected == 3 and not shifted


def test_median_uses_the_last_five_accepted():
    r = [40.00] * 5 + [40.011] * 5 + [40.022]  # 40.022 is 2.75 bps from the last five (40.011), 4.1 from all ten
    acc, _, _ = m.accept_ratios(np.array(r), 3, 3)
    assert acc.all()


def test_open_skip_f13():
    open_ns = 1_000 * NS
    as_of = np.array([open_ns - NS, open_ns + 30 * NS, open_ns + 120 * NS])
    assert m.open_skip_mask(as_of, "NDXP", open_ns, 120).tolist() == [False, False, True]
    assert m.open_skip_mask(as_of, "QQQ", open_ns, 120).tolist() == [False, True, True]


def test_pct_of_king_f23():
    """values {740: +2.0e6, 745: -3.0e6, 750: +1.5e6} -> King 745 (negative), pct(740) = 0.667."""
    board = np.array([0, 0, 0, 1])
    vals = np.array([2.0e6, -3.0e6, 1.5e6, 5.0])
    pct = m.pct_of_king(board, vals, 2)
    assert pct[0] == pytest.approx(2 / 3) and pct[1] == 1.0 and pct[3] == 1.0


def test_step_errors_f16():
    """Two accepted boards: R 40.80 -> 40.81; relevant strike 600 -> error 600 x 0.01 = 6 pts. The rejected board
    in between is applied with the R in force (error 0, F1.2/F1.6); the next accepted one compares with the R in
    force (the last accepted). A board not applied (no futures price) is left out."""
    r = np.array([40.80, 40.81, 45.0, 40.82, np.nan])
    acc = np.array([True, True, False, True, False])
    applied = np.array([True, True, True, True, False])
    board = np.array([0, 1, 1, 2, 3, 3, 4])
    strikes = np.array([600.0, 600.0, 610.0, 600.0, 600.0, 500.0, 600.0])
    relevant = np.array([True, True, False, True, True, True, True])
    err, rows = m.step_errors(r, acc, applied, board, strikes, relevant)
    assert rows.tolist() == [1, 2, 3, 3]
    assert err == pytest.approx([6.0, 0.0, 6.0, 5.0])
    assert m.prev_accepted(acc).tolist() == [-1, 0, 1, 1, 3]


def test_spot_lag_finds_a_known_delay():
    rng = np.random.default_rng(1)
    start, n = 0, 3_000
    fut = 20_000 + np.cumsum(rng.normal(0, 1, n))
    t = np.arange(n, dtype=np.int64) * NS
    for lag in (0, 1, 7, -3):
        spot = np.roll(fut / 40.0, lag)  # spot(t) = fut(t - lag) / 40
        out = m.spot_lag(t, spot, t, fut, start, (n - 1) * NS, max_lag_s=20)
        assert out["peak_lag_s"] == lag and out["corr"] > 0.9


def test_trailing_rv_uses_only_finished_minutes():
    minutes = np.arange(0, 40) * 60 * NS
    px = np.exp(np.arange(40) * 0.001) * 100  # +0.1% a minute
    start = 15 * 60 * NS
    rv = m.trailing_rv(minutes, px, np.array([start, start + 30 * NS, 20 * 60 * NS]), start, 30 * 60 * NS, minutes=15)
    assert rv[0] == pytest.approx(np.sqrt(15) * 0.001)
    assert rv[1] == rv[0]  # half a minute later: no new minute finished
    early = m.trailing_rv(minutes, px, np.array([5 * 60 * NS]), 5 * 60 * NS, 30 * 60 * NS, minutes=15)
    assert np.isnan(early[0])  # fewer than 15 minutes of trades behind it
