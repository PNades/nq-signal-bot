"""The per-session Skylit engine (step 11 part 2; feature_spec F1.8, F2.10, F2.14, F2.15, F2.18, F2.19, F2.20, F5.16;
owner 2026-10-31 SPEC §13 Q-step11d (6)-(7))."""

import numpy as np
from pytest import approx

from nqbot.config import load_features, load_settings
from nqbot.features import level_state as ls
from nqbot.features import session_tape as st
from nqbot.features.bands import StepBand
from nqbot.features.boards import MapBoards
from nqbot.features.chart_levels import LevelRecord
from nqbot.features.node_levels import Scale
from nqbot.features.skylit_session import Context, SkylitSession
from tests.test_map_state import DAY, NS, T0

F = load_features()
S = load_settings()
CEIL, FLOOR = ls.CEILING, ls.FLOOR


def mb_of(name, boards, r=1.0):
    """boards: [(t_sec, spot, {strike: value})]; r a number or a list per board."""
    t = np.array([T0 + int(round(x[0] * NS)) for x in boards], dtype=np.int64)
    ks, vs, offs = [], [], [0]
    for _, _, tab in boards:
        for k in sorted(tab):
            ks.append(float(k))
            vs.append(float(tab[k]))
        offs.append(len(ks))
    n = len(boards)
    rr = np.full(n, float(r)) if np.isscalar(r) else np.asarray(r, float)
    return MapBoards(name, DAY, t, t, t, np.array([x[1] for x in boards], float), rr.copy(), rr.copy(),
                     np.zeros(n, dtype=bool), np.array(offs, dtype=np.int64), np.array(ks), np.array(vs))


def tape(trades, symbol):
    return st.Tape.from_arrays(DAY, [T0 + int(round(s * NS)) for s, _ in trades], [p for _, p in trades], symbol=symbol)


def at(sec):
    return T0 + int(round(sec * NS))


def session(nq, es, *, qqq=None, ndxp=None, spy=None, spx=None, scale=Scale(0.5, 0.5, None), em=(100.0, 50.0),
            atr=(80.0, 20.0), es_levels=None, end=900, r=None):
    r = r or {}
    boards = {name: mb_of(name, b, r.get(name, 1.0)) for name, b in (("QQQ", qqq), ("NDXP", ndxp), ("SPY", spy),
                                                                      ("SPX", spx)) if b is not None}
    ctx = Context(F, S, em_nq=em[0], em_es=em[1], atr_nq=atr[0], atr_es=atr[1],
                  em_frac={"QQQ": 0.01, "SPY": 0.01}, scale=scale)
    return SkylitSession(boards, tape(nq, "NQH6"), tape(es, "ESH6"), ctx, end_ns=at(end), es_levels=es_levels)


KING_NQ = {300.5: 1.0}     # a far King so the test node is relevant but not the King's neighbour
QQQ_CEIL = [(0, 90, {**KING_NQ, 100.5: 0.5})]     # band [100, 101] on NQ (R = 1, node_band 0.5)
SPY_CEIL = [(0, 45, {150.5: 1.0, 50.5: 0.5})]     # band [50, 51] on ES
SPX_FAR = [(0, 45, {500.0: 1.0, 400.0: 0.5})]     # SPX nodes nowhere near ES


# --- F1.8 guard wiring --------------------------------------------------------------------------------------

def guarded():
    # QQQ's R steps 0.25% at board 20 (1-s boards): a bot's-R fire at 20 (F1.8); NDXP takes QQQ's blocks
    n = 300
    r = [1.0] * 20 + [1.0025] * (n - 20)
    qqq = [(s, 90, {**KING_NQ, 100.5: 0.5}) for s in range(n)]
    ndxp = [(s, 90, {**KING_NQ, 100.5: 0.5}) for s in range(n)]
    return session([(0, 89), (400, 89)], [(0, 40), (400, 40)], qqq=qqq, ndxp=ndxp, r={"QQQ": r, "NDXP": r}, end=600)


def test_f18_guard_blocks_qqq_and_ndxp_r_features_null_non_r_stay():
    s = guarded()
    assert s.blocks["QQQ"] and s.blocks["QQQ"][0][0] == at(20)
    assert s.map_blocks("NDXP") == s.blocks["QQQ"]
    f = s.features_at(at(30))
    assert f["qqq_magnet_net"] is None and f["ndxp_magnet_net"] is None and f["qqq_king_distance"] is None
    assert f["qqq_king_strike"] == 300.5 and f["qqq_regime_net"] is not None   # no R: kept (F1.8)
    end = s.blocks["QQQ"][0][1]
    assert s.features_at(end + NS)["qqq_magnet_net"] is not None
    reg = s.registry_at(at(30))
    assert reg and all(x["blocked"] for x in reg) and reg[0]["crossed"] is None


def test_registry_relevant_nasdaq_nodes_and_es_registry():
    s = session([(0, 89), (60, 89)], [(0, 40), (60, 40)], qqq=QQQ_CEIL, spy=SPY_CEIL)
    reg = {x["strike"]: x for x in s.registry_at(at(10))}
    assert set(reg) == {100.5, 300.5}
    assert reg[100.5]["band"] == (100.0, 101.0) and reg[100.5]["role"] == CEIL and reg[100.5]["sub_type"] == "qqq_node"
    es = {x["strike"]: x for x in s.es_registry_at(at(10))}
    assert set(es) == {50.5, 150.5} and es[50.5]["instrument"] == "ES"


# --- F2.10 magnets ------------------------------------------------------------------------------------------

def test_f210_magnets():
    # EM 100; nodes 0.5 EM above (pct 1.0) and 1.0 EM below (pct 0.5) of NQ 1,000
    qqq = [(0, 1000, {1050.0: 1.0, 900.0: 0.5})]
    s = session([(0, 1000), (60, 1000)], [(0, 40)], qqq=qqq)
    m = s.magnets("QQQ", at(10), direction=-1)
    assert m["magnet_pull_above"] == approx(2.0) and m["magnet_pull_below"] == approx(0.5)
    assert m["magnet_net"] == approx(1.5) and m["magnet_pull_with"] == approx(0.5) and m["magnet_pull_against"] == approx(2.0)


# --- F2.18 whipsaw ------------------------------------------------------------------------------------------

def test_f218_whipsaw_twice():
    floor_ceiling = [(0, 100, {120.0: 1.0, 80.0: 0.6})]   # a relevant ceiling and floor on the Nasdaq maps
    s = session([(0, 100), (60, 100)], [(0, 40)], qqq=floor_ceiling, ndxp=floor_ceiling, spy=SPY_CEIL, spx=SPX_FAR)
    for m, v in (("SPX", 0.5), ("QQQ", -0.5), ("SPY", 0.0), ("NDXP", 0.5)):
        s.ms[m].board["lean_raw"] = np.array([v])
    f = s.features_at(at(30))
    assert f["spx_bias"] == "up" and f["qqq_bias"] == "down" and f["spy_bias"] == "pinned"
    assert f["trinity_bias_conflict_qqq"] and f["trinity_bias_agree_qqq"] == 1
    assert f["whipsaw_qqq"] is True and f["whipsaw_ndxp"] is False


# --- F2.19 Trinity ------------------------------------------------------------------------------------------
# NQ from 89 (ceiling role on [100, 101]); ES from 40 (ceiling role on [50, 51]); zone_proximity 5 NQ, 2.5 ES.

def test_f219_count_two_not_aligned():
    s = session([(0, 89), (10, 97)], [(0, 40), (10, 49)], qqq=QQQ_CEIL, spy=SPY_CEIL, spx=SPX_FAR)
    tr = s.trinity(at(10), at(10), CEIL)
    assert tr["trinity_count"] == 2 and tr["trinity_aligned"] is False
    assert tr["trinity_qqq"] and tr["trinity_spy"] and tr["trinity_spx"] is False


def test_f219_es_reaches_spy_later_while_nq_still_inside():
    s = session([(0, 89), (10, 97), (30, 98)], [(0, 40), (10, 44), (18, 49)], qqq=QQQ_CEIL, spy=SPY_CEIL, spx=SPX_FAR)
    assert s.trinity(at(10), at(10), CEIL)["trinity_spy"] is False      # structure model, at the touch
    assert s.trinity(at(10), at(25), CEIL)["trinity_spy"] is True       # pair scorer, later decision_ts


def test_f219_es_reaches_spy_only_after_nq_left():
    s = session([(0, 89), (10, 97), (15, 90)], [(0, 40), (10, 44), (18, 49)], qqq=QQQ_CEIL, spy=SPY_CEIL, spx=SPX_FAR)
    assert s.trinity(at(10), at(25), CEIL)["trinity_spy"] is False


def test_f219_crossed_spy_ceiling_counts_for_long_not_short():
    # SPY ceiling crossed at 5 (ES 51.5): provisional floor. A long event at a QQQ floor (NQ from 115 onto [100, 101]).
    s = session([(0, 115), (10, 103)], [(0, 40), (5, 51.5), (8, 57), (10, 52)], qqq=QQQ_CEIL, spy=SPY_CEIL,
                spx=SPX_FAR)
    assert s.trinity(at(10), at(10), FLOOR)["trinity_spy"] is True
    s2 = session([(0, 89), (10, 97)], [(0, 40), (5, 51.5), (8, 57), (10, 52)], qqq=QQQ_CEIL, spy=SPY_CEIL, spx=SPX_FAR)
    assert s2.trinity(at(10), at(10), CEIL)["trinity_spy"] is False


def test_f219_q19_unroled_spy_node_by_entry_side():
    # ES stays within rearm_dist of the SPY band (no role); entering from above counts as a floor
    long_q = [(0, 115), (10, 103)]
    above = session(long_q, [(0, 54), (10, 52)], qqq=QQQ_CEIL, spy=SPY_CEIL, spx=SPX_FAR)
    assert above.nodes["SPY"].node_at(50.5, at(10))["role"] is None
    assert above.trinity(at(10), at(10), FLOOR)["trinity_spy"] is True
    below = session(long_q, [(0, 47), (10, 49)], qqq=QQQ_CEIL, spy=SPY_CEIL, spx=SPX_FAR)
    assert below.trinity(at(10), at(10), FLOOR)["trinity_spy"] is False


def test_q_step11d_6_q19_on_the_nasdaq_side():
    # the QQQ node has no role yet (NQ never rearm_dist away); NQ entered its band from below -> counts as a ceiling
    s = session([(0, 93), (10, 99)], [(0, 40), (10, 49)], qqq=QQQ_CEIL, spy=SPY_CEIL, spx=SPX_FAR)
    assert s.nodes["QQQ"].node_at(100.5, at(10))["role"] is None
    tr = s.trinity(at(10), at(10), CEIL)
    assert tr["trinity_qqq"] is True and tr["trinity_spy"] is True
    assert s.trinity(at(10), at(10), FLOOR)["trinity_qqq"] is False


def test_f219_spy_crossed_against_rho_before_decision():
    s = session([(0, 89), (10, 97), (30, 98)], [(0, 40), (10, 49), (20, 51.5)], qqq=QQQ_CEIL, spy=SPY_CEIL,
                spx=SPX_FAR)
    assert s.trinity(at(10), at(10), CEIL)["trinity_spy"] is True
    assert s.trinity(at(10), at(25), CEIL)["trinity_spy"] is False


def test_f219_two_spy_nodes_one_crossed_one_not():
    spy = [(0, 45, {150.5: 1.0, 50.5: 0.5, 51.5: 0.5})]           # bands [50, 51] and [51, 52]
    s = session([(0, 89), (10, 97), (30, 98)], [(0, 40), (10, 50.5), (20, 51.25)], qqq=QQQ_CEIL, spy=spy,
                spx=SPX_FAR)
    assert s.trinity(at(10), at(25), CEIL)["trinity_spy"] is True


def test_f219_spy_and_spx_at_different_moments_aligned():
    # SPY's node at [50, 51] counts until 12 s (then under 20% of King, frozen with its role); SPX's node there
    # becomes relevant at 18 s: they overlap ES at different moments, never both at once -> count 3 (option (a))
    spy = [(t, 45, {150.5: 1.0, 50.5: 0.5 if t < 12 else 0.1}) for t in range(40)]
    spx = [(t, 45, {500.0: 1.0, 50.5: 0.1 if t < 18 else 0.5}) for t in range(40)]
    ndxp = [(0, 90, {500.0: 1.0, 400.0: 0.5})]                     # NDXP far from NQ: known, no node here
    s = session([(0, 89), (10, 97), (40, 98)], [(0, 40), (10, 49)], qqq=QQQ_CEIL, ndxp=ndxp, spy=spy, spx=spx)
    assert s.trinity(at(10), at(15), CEIL)["trinity_spx"] is False
    tr = s.trinity(at(10), at(35), CEIL)
    assert tr["trinity_count"] == 3 and tr["trinity_aligned"] is True and tr["maps_with_node_here"] == 3


def test_f219_aligned_stats_at_first_overlap():
    spy = [(0, 45, {150.5: 1.0, 50.5: 0.40}), (20, 45, {150.5: 1.0, 50.5: 0.30})]
    s = session([(0, 89), (10, 97), (30, 98)], [(0, 40), (10, 49), (25, 49.5)], qqq=QQQ_CEIL, spy=spy, spx=SPX_FAR)
    tr = s.trinity(at(10), at(28), CEIL)
    assert tr["trinity_spy"] and tr["aligned_min_pct"] == approx(0.40)


def test_q_step11d_7_blocks_in_the_window():
    # SPY's R steps 0.25% at board 20 (1-s boards): blocked from 20 s to 60 s after the last flagged board
    n = 300
    r = [1.0] * 20 + [1.0025] * (n - 20)
    spy = [(t, 45, {150.5: 1.0, 50.5: 0.5}) for t in range(n)]
    qqq = [(t, 90, {**KING_NQ, 100.5: 0.5}) for t in range(n)]
    nq = [(0, 89), (10, 97), (600, 98)]
    # ES inside the SPY band only inside the block: the overlap doesn't count; partly blocked -> null
    s = session(nq, [(0, 40), (30, 49.9), (35, 44)], qqq=qqq, spy=spy, spx=SPX_FAR, r={"SPY": r}, end=900)
    a, z = s.blocks["SPY"][0]
    assert a == at(20) and z > at(140)
    late = z + NS
    tr = s.trinity(at(10), late, CEIL)
    assert tr["trinity_spy"] is None and tr["trinity_count"] is None
    assert s.trinity(at(25), at(25), CEIL)["trinity_spy"] is None           # blocked at t itself
    # the whole window unblocked and no overlap -> false
    assert s.trinity(at(1), at(15), CEIL)["trinity_spy"] is False
    # an overlap at an unblocked moment counts even with a block later in the window
    s2 = session(nq, [(0, 40), (12, 49.9), (35, 44)], qqq=qqq, spy=spy, spx=SPX_FAR, r={"SPY": r}, end=900)
    assert s2.trinity(at(10), late, CEIL)["trinity_spy"] is True


# --- F5.16 es_at_level ----------------------------------------------------------------------------------------

def es_pdh(es_tape, price=60.0, hw=0.5):
    band = StepBand.const(es_tape, T0 - 600 * NS, price - hw, price + hw)
    p = ls.Params(5.0, 0.0, 5, 7.5, 3)
    lv = ls.LevelState("es_pdh-x", ls.SWEEP, es_tape, band, p, st.bars(es_tape, 1), st.bars(es_tape, 5),
                       end_ns=at(900), natural_role=CEIL).run()
    return LevelRecord("es_pdh-x", "pdh", "pdh_pdl", "ES", True, lv)


def test_f516_es_at_level_later_decision():
    nq = [(0, 89), (10, 97), (40, 98)]
    es = [(0, 50), (20, 59)]
    sess = session(nq, es, qqq=QQQ_CEIL)
    sess.es_levels = []
    s = session(nq, es, qqq=QQQ_CEIL, es_levels=[es_pdh(sess.tapes["ES"])])

    def nq_inside(tau):
        p = s.tapes["NQ"].last_price_at(tau)
        return p is not None and 95 <= p <= 106

    assert s.es_at_level(at(10), at(10), CEIL, nq_inside)["es_at_level"] is False
    got = s.es_at_level(at(10), at(30), CEIL, nq_inside)
    assert got["es_at_level"] is True and got["es_member_types"] == ("pdh",)


def test_f516_es_at_level_spy_node_zone_and_block_rule():
    n = 300
    r = [1.0] * 20 + [1.0025] * (n - 20)
    spy = [(t, 45, {150.5: 1.0, 50.5: 0.5}) for t in range(n)]
    nq = [(0, 89), (10, 97), (600, 98)]
    s = session(nq, [(0, 40), (12, 49.9)], qqq=QQQ_CEIL, spy=spy, r={"SPY": r}, end=900)

    def nq_inside(tau):
        p = s.tapes["NQ"].last_price_at(tau)
        return p is not None and 95 <= p <= 106

    assert s.es_at_level(at(10), at(15), CEIL, nq_inside)["es_at_level"] is True        # before the block
    assert s.es_at_level(at(10), at(30), CEIL, nq_inside)["es_at_level"] is None        # SPY blocked in the window


# --- F2.20 S&P path -------------------------------------------------------------------------------------------

def test_f220_sp_path_count():
    spy = [(0, 7000, {7100.5: 1.0, 7010.5: 0.4, 7030.5: 0.5})]
    s = session([(0, 21_000)], [(0, 7000)], spy=spy)
    out = s.sp_path(at(5), 21_060.0, +1)
    assert out["sp_path_count"] == 1 and out["sp_path_max_pct"] == approx(0.4)
    assert out["sp_path_first_dist_atr"] == approx(10.0 / 20.0)


# --- F2.14 pika on an S&P map -------------------------------------------------------------------------------

def test_f214_sp_in_pika_cloud_uses_es_zone_proximity():
    spy = [(0, 7000, {6900.0: -1.0, 7000.5: 0.3, 7001.5: 0.3})]   # cloud band [7,000, 7,002]
    sc = Scale(0.25, 0.25, None)                                   # es_zone_proximity = 5 × 0.25 = 1.25
    assert session([(0, 21_000)], [(0, 7003.0)], spy=spy, scale=sc).pika("SPY", at(5))["in_pika_cloud"] is True
    assert session([(0, 21_000)], [(0, 7003.5)], spy=spy, scale=sc).pika("SPY", at(5))["in_pika_cloud"] is False


# --- F2.15 pockets ahead / behind ---------------------------------------------------------------------------

def test_f215_pocket_ahead_within_gap_to_next_node():
    tab = {700.0: 1.0, 760.0: 0.5, 752.0: 0.02, 753.0: 0.03, 754.0: 0.02, 750.0: 0.5, 745.0: 0.3}
    s = session([(0, 750.5)], [(0, 40)], qqq=[(0, 750.5, tab)])
    p = s.pockets("QQQ", at(5), +1)
    assert p["pocket_ahead_size"] == 3 and p["pocket_ahead_pts"] == approx(754.5 - 751.5)
    assert p["pocket_behind_size"] == 0
