"""NQ / ES cross-market reads (feature_spec F5.16; owner 2026-10-16 B5; 2026-10-31 Q-step11d (3))."""

from pytest import approx

from nqbot.features import cross_market as cm
from nqbot.features import session_tape as st
from tests.test_map_state import DAY, NS, T0


def tape(trades, symbol="NQH6"):
    """trades: (sec, price[, size, side])"""
    ts = [T0 + int(round(x[0] * NS)) for x in trades]
    px = [x[1] for x in trades]
    size = [x[2] if len(x) > 2 else 1.0 for x in trades]
    side = [x[3] if len(x) > 3 else 0 for x in trades]
    return st.Tape.from_arrays(DAY, ts, px, size, side, symbol=symbol)


def test_smt_nq_new_high_es_not():
    # 15-minute windows: prior (−1800, −900], recent (−900, 0]
    nq = tape([(-1500, 100.0), (-1000, 105.0), (-600, 103.0), (-60, 106.0)])
    es = tape([(-1500, 50.0), (-1000, 55.0), (-600, 52.0), (-60, 53.0)], "ESH6")
    out = cm.smt_div(nq, es, T0, 15)
    assert out["smt_div_high"] == "nq_only"
    assert cm.smt_div(es, nq, T0, 15)["smt_div_high"] == "es_only"
    assert cm.smt_div(nq, nq, T0, 15)["smt_div_high"] == "none"
    empty = tape([(-60, 1.0)])
    assert cm.smt_div(empty, es, T0, 15)["smt_div_high"] is None


def test_rel_delta_example_and_side_n_left_out():
    nq = tape([(-30, 1, 600, 1), (-20, 1, 400, -1), (-10, 1, 5000, 0)])     # an N print counts in neither
    es = tape([(-30, 1, 300, 1), (-20, 1, 500, -1)], "ESH6")
    out = cm.rel_delta(nq, es, T0, 60)
    assert out["rel_delta"] == approx(0.2 - (-0.25)) and out["delta_sign_agree"] is False
    only_n = tape([(-30, 1, 100, 0)])
    assert cm.rel_delta(only_n, es, T0, 60)["rel_delta"] is None


def test_rs_nq_es_signed_to_direction_and_nulls():
    nq = tape([(-900, 20_000.0), (-300, 20_000.0), (0, 20_100.0)])
    es = tape([(-900, 5000.0), (-300, 5000.0), (0, 5010.0)], "ESH6")
    up = cm.rs_nq_es(nq, es, T0, +1)
    assert up["rs_nq_es_5"] == approx(0.5 - 0.2) and up["rs_nq_es_15"] == approx(0.3)
    assert cm.rs_nq_es(nq, es, T0, -1)["rs_nq_es_5"] == approx(-0.3)
    late = tape([(-200, 20_000.0), (0, 20_100.0)])
    out = cm.rs_nq_es(late, es, T0, +1)
    assert out["rs_nq_es_5"] is None and out["rs_nq_es_15"] is None
