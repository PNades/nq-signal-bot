"""F0.8 ATR / expected-move units and F3.11 expected-move lines (owner 2026-10-14 Q-sigma, 2026-10-22 Q-svxwknd)."""

from datetime import date

from pytest import approx

from nqbot.config import load_features
from nqbot.features import expected_move as em


def test_atr_simple_mean_of_true_ranges():
    s = [{"high": 110, "low": 100, "close": 105}, {"high": 112, "low": 104, "close": 111},
         {"high": 120, "low": 113, "close": 114}, {"high": 116, "low": 100, "close": 101}]
    # TRs: max(8, 7, 1) = 8; max(7, 9, 2) = 9; max(16, 2, 14) = 16 -> mean 11
    assert em.true_ranges(s) == [8, 9, 16]
    assert em.atr(s, 3) == approx(11.0)
    assert em.atr(s, 4) is None                     # needs atr_period + 1 sessions


def test_em_unit_and_lines_feature_spec_example():
    e = em.ExpectedMove(20_000.0, em.em_fraction({"em_1d_pct": 0.80}, 1), (-2, -1, 1, 2))
    assert e.points == approx(160.0)
    assert e.lines() == {-2: approx(19_680), -1: approx(19_840), 1: approx(20_160), 2: approx(20_320)}
    assert e.running_sigma(20_080) == approx(0.5)
    above, below = e.nearest(20_000)
    assert above == (1, approx(20_160)) and below == (-1, approx(19_840))


def test_friday_row_used_as_stored_and_lag_two_formula():
    fri = em.ExpectedMove(20_000.0, em.em_fraction({"em_1d_pct": 1.400}, 1), (1,))
    assert fri.points == approx(280.0)                      # Monday's EM, the stored value as it is
    m = em.em_fraction({"svx1d": 15.4381}, 2, calendar_days=3)
    assert m * 100 == approx(1.400, abs=5e-4)               # 15.4381 / 100 x sqrt(3 / 365)


def test_missing_row_gives_no_lines_and_null_units():
    e = em.ExpectedMove(20_000.0, em.em_fraction(None, 1), (1, -1))
    assert e.points is None and e.lines() == {} and e.running_sigma(1.0) is None
    assert em.dist3(30.0, 120.0, None) == (30.0, 0.25, None)
    assert em.dist3(None, 1, 1) == (None, None, None)


def test_sub_types_and_config():
    assert [em.sub_type(k) for k in (-2, -1, 1, 2)] == ["em_dn2", "em_dn1", "em_up1", "em_up2"]
    assert load_features()["em_level_sigmas"] == [-2, -1, 1, 2]


def test_session_em_reads_row_t_minus_lag():
    rows = em.TempestRows({("QQQ", date(2026, 3, 5)): {"em_1d_pct": 0.9}, ("SPY", date(2026, 3, 5)): {"em_1d_pct": 0.7}})
    back = lambda d, n: date(2026, 3, 5) if (d, n) == (date(2026, 3, 6), 1) else None
    e = em.session_em("NQ", date(2026, 3, 6), 20_000.0, rows, 1, back, (1,))
    assert e.points == approx(180.0)
    assert em.session_em("ES", date(2026, 3, 6), 7_000.0, rows, 1, back, (1,)).points == approx(49.0)
