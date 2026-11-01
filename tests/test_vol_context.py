"""Daily vol context (feature_spec F2.21; owner 2026-10-20 Q-vix; 2026-10-22 Tempest draft rule)."""

import math
from datetime import date, timedelta

import numpy as np
from pytest import approx

from nqbot.features import vol_context as vc
from nqbot.features.expected_move import TempestRows

DAY = date(2026, 3, 20)


def back(day, n):
    d = day
    for _ in range(n):
        d -= timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
    return d


def final(**kw):
    return {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "front_min": 1440.0, **kw}


def test_f221_row_of_t_minus_lag_never_t():
    rows = TempestRows({("QQQ", back(DAY, 1)): final(svx30=20.0), ("QQQ", DAY): final(svx30=99.0),
                        ("SPY", back(DAY, 1)): final(svx30=15.0)})
    out = vc.features(rows, DAY, 1, back, [], 20)
    assert out["qqq_svx30"] == 20.0 and out["spy_svx30"] == 15.0 and out["ndxp_svx30"] is None
    assert out["tempest_draft"] is False


def test_f221_vix_ref_chg_and_roll_null():
    d1, d2 = back(DAY, 1), back(DAY, 2)
    rows = TempestRows({("VIX", d2): {"close": 20.0, "front_min": 10_081.0},
                        ("VIX", d1): {"close": 22.0, "front_min": 8_641.0}})
    assert vc.features(rows, DAY, 1, back, [], 20)["vix_ref_chg"] == approx(math.log(1.1))
    rolled = TempestRows({("VIX", d2): {"close": 20.0, "front_min": 1_441.0},
                          ("VIX", d1): {"close": 22.0, "front_min": 40_321.0}})
    assert vc.features(rolled, DAY, 1, back, [], 20)["vix_ref_chg"] is None


def test_f221_rv_nq_and_ratio():
    closes = [20_000.0 * math.exp(0.01 * (i % 2)) for i in range(22)]
    r = np.diff(np.log(closes[-21:]))
    want = float(np.std(r, ddof=1) * math.sqrt(252))
    rows = TempestRows({("QQQ", back(DAY, 1)): final(svx30=25.0), ("SPY", back(DAY, 1)): final()})
    out = vc.features(rows, DAY, 1, back, closes, 20)
    assert out["rv_nq"] == approx(want) and out["rv_iv_ratio"] == approx(want / 0.25)
    assert vc.features(rows, DAY, 1, back, closes[:10], 20)["rv_nq"] is None


def test_draft_rule():
    assert vc.row_is_final(final())
    assert not vc.row_is_final(final(open=None))
    assert not vc.row_is_final(final(front_min=1440.5))
    rows = TempestRows({("QQQ", back(DAY, 1)): final(front_min=1440.5), ("SPY", back(DAY, 1)): final()})
    assert vc.features(rows, DAY, 1, back, [], 20)["tempest_draft"] is True
