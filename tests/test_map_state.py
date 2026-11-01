"""Per-board map structure (step 11 part 2; feature_spec F2.1-F2.5, F2.8, F2.9, F2.12-F2.17), each on its spec example."""

from datetime import date, time

import numpy as np
import pytest
from pytest import approx

from nqbot.config import load_features
from nqbot.features import node_switch as nsw
from nqbot.features import session_tape as st
from nqbot.features.boards import MapBoards
from nqbot.features.map_state import MapState

F = load_features()
DAY = date(2026, 3, 20)
NS = st.NS
T0 = st.et_ns(DAY, time(10, 0))


def mk(boards, name="QQQ", one_minute=False, r=1.0):
    """boards: [(t_sec, spot, {strike: value})] -> MapBoards with tau = t_apply = T0 + t (R fixed)."""
    t = np.array([T0 + int(round(x[0] * NS)) for x in boards], dtype=np.int64)
    ks, vs, offs = [], [], [0]
    for _, _, tab in boards:
        for k in sorted(tab):
            ks.append(float(k))
            vs.append(float(tab[k]))
        offs.append(len(ks))
    n = len(boards)
    return MapBoards(name, DAY, t, t, t, np.array([x[1] for x in boards], float), np.full(n, r), np.full(n, r),
                     np.full(n, one_minute), np.array(offs, dtype=np.int64), np.array(ks), np.array(vs))


def state(boards, features=None, **kw):
    return MapState.build(mk(boards, **kw), features or F)


def row(ms, i, strike):
    rows = ms.rows(i)
    j = np.flatnonzero(ms.mb.s_strike[rows] == strike)
    assert len(j) == 1, strike
    return rows.start + int(j[0])


# --- F2.3 King and % of King -------------------------------------------------------------------------------------

def test_f23_king_negative_allowed_and_pct():
    ms = state([(0, 742, {740: 2.0e6, 745: -3.0e6, 750: 1.5e6})])
    assert ms.board["king"][0] == 745
    assert ms.pct[row(ms, 0, 740)] == approx(2 / 3, abs=1e-3)
    assert ms.board["king_dominance"][0] == approx(3.0 / 2.0)


def test_f23_king_tie_nearer_spot_then_lower():
    ms = state([(0, 748, {745: 2.0, 750: 2.0}), (1, 747.5, {745: 2.0, 750: 2.0})])
    assert ms.board["king"].tolist() == [750, 745]
    assert ms.board["king_dominance"][0] == approx(1.0)


# --- F2.4 relevance through the map ------------------------------------------------------------------------------

def test_f24_relevance_hysteresis():
    seq = [0.24, 0.26, 0.22, 0.19]
    ms = state([(i, 700, {700: 1.0, 760: p}) for i, p in enumerate(seq)])
    assert [bool(ms.relevant[row(ms, i, 760)]) for i in range(4)] == [False, True, True, False]
    fresh = state([(0, 700, {700: 1.0, 760: 0.22})])
    assert not fresh.relevant[row(fresh, 0, 760)]


# --- F2.5 node types -------------------------------------------------------------------------------------------

def test_f25_gatekeepers_between_spot_and_confirmed_king():
    tab = {745: 0.5, 750: 0.5, 752: 0.15, 755: -0.5, 760: 1.0}
    ms = state([(0, 748, tab)])
    t = {n["strike"]: n["node_type"] for n in ms.nodes_at(0)}
    assert t == {745: "significant", 750: "gatekeeper", 752: "normal", 755: "gatekeeper", 760: "king"}
    assert ms.board["gatekeepers_to_king"][0] == 2
    ms2 = state([(0, 757, tab)])
    t2 = {n["strike"]: n["node_type"] for n in ms2.nodes_at(0)}
    assert t2[750] == "significant" and t2[755] == "significant" and t2[760] == "king"


def test_f25_types_match_node_switch_rule_on_random_boards():
    rng = np.random.default_rng(3)
    boards = []
    for i in range(40):
        ks = np.arange(740, 761, 1.0)
        vs = rng.normal(0, 1, len(ks)) * rng.choice([1, 5], len(ks))
        boards.append((i * 2, 750 + rng.normal(0, 3), dict(zip(ks, vs))))
    ms = state(boards)
    for i in range(len(boards)):
        nodes = ms.nodes_at(i)
        values = {n["strike"]: n["value"] for n in nodes}
        relevant = {n["strike"] for n in nodes if n["relevant"]}
        ck = ms.board["confirmed_king"][i]
        want = nsw.node_types(values, relevant, float(ms.mb.spot[i]), float(ck))
        assert {n["strike"]: n["node_type"] for n in nodes} == want


def test_f25_gatekeeper_vs_beyond():
    # spot 748, Gatekeeper 750 (0.5) and 755 (-0.8) toward the King 760 (1.0): 750 vs max(|755|, |760|) = 0.5
    ms = state([(0, 748, {750: 0.5, 755: -0.8, 760: 1.0})])
    nodes = {n["strike"]: n for n in ms.nodes_at(0)}
    assert nodes[750]["gatekeeper_vs_beyond"] == approx(0.5) and nodes[755]["gatekeeper_vs_beyond"] == approx(0.8)
    assert nodes[760]["gatekeeper_vs_beyond"] is None


def test_f25_confirmed_king_tug_of_war_keeps_types():
    # 760 and 759 alternate as the board's largest every 30 s for 10 minutes: the confirmed King stays 760
    boards = []
    for s in range(0, 600):
        big = 760 if (s // 30) % 2 == 0 else 759
        boards.append((s, 750, {750: 0.6, 755: 0.6, 759: 2.0 if big == 759 else 1.9, 760: 2.0 if big == 760 else 1.9}))
    ms = state(boards)
    assert set(ms.board["confirmed_king"].tolist()) == {760}
    assert ms.board["king_changes_today"][-1] == 0
    # a clean new King 755 from 600 s: confirmed at the first board after 760's share falls under 25% (46 s)
    clean = [(s, 750, {750: 0.6, 755: 3.0 if s >= 100 else 0.6, 760: 2.0}) for s in range(0, 200)]
    ms = state(clean)
    first = int(np.flatnonzero(ms.board["confirmed_king"] == 755)[0])
    assert (ms.mb.t_apply[first] - T0) / NS == 146
    assert ms.board["king_changes_today"][first] == 1 and ms.board["king_stairstep_count"][first] == 1
    assert ms.board["last_king_shift_strikes"][first] == -1         # one listed strike down (755 -> 760 has 1 between)
    f = ms.features_at(T0 + 150 * NS)
    assert f["king_strike"] == 755 and f["minutes_since_king_change"] == approx(4 / 60)


# --- F2.8 velocity -------------------------------------------------------------------------------------------

def test_f28_velocity_uses_latest_board_at_least_lookback_back():
    boards = [(0, 750, {760: 1.0}), (30, 750, {760: 2.0}), (60, 750, {760: 3.0}), (90, 750, {760: 4.0})]
    ms = state(boards)
    assert np.isnan(ms.vel[row(ms, 0, 760)]) and np.isnan(ms.vel[row(ms, 1, 760)])
    assert ms.vel[row(ms, 2, 760)] == approx((3 - 1) / 1)          # at 60: the board at 0
    assert ms.vel[row(ms, 3, 760)] == approx((4 - 2) / 2)          # at 90: the board at 30


def test_f28_barney_velocity_and_magnitude_velocity():
    ms = state([(0, 750, {760: -1e6}), (60, 750, {760: -2e6})])
    r = row(ms, 1, 760)
    assert ms.vel[r] == approx(-1.0) and ms.mvel[r] == approx(1.0)


def test_f21_missing_strike_null_velocity_and_kept_relevance():
    ms = state([(0, 750, {750: 1.0, 760: 0.5}), (60, 750, {750: 1.0}), (120, 750, {750: 1.0, 760: 0.22})])
    r = row(ms, 2, 760)
    assert np.isnan(ms.vel[r])                                     # the board 60 s back does not carry 760
    assert ms.relevant[r]                                          # still relevant (0.22 >= 0.20, state kept)
    assert ms.board["king"][1] == 750                              # King over the strikes present only


# --- F2.9 lean -----------------------------------------------------------------------------------------------

def test_f29_lean_sign_and_time_weighting():
    # building above spot -> bias up; the same below -> down
    up = state([(0, 750, {760: 1.0}), (60, 750, {760: 1.5})])
    assert up.board["lean_raw"][1] == approx(0.5)
    dn = state([(0, 770, {760: 1.0}), (60, 770, {760: 1.5})])
    assert dn.board["lean_raw"][1] == approx(-0.5)
    # lean_raw 1.0 from t-10 min to t-4 min and 0.4 from t-4 min to t, lean_window 10 -> 0.76
    ms = state([(0, 750, {760: 1.0})], {**F, "lean_window": 10})
    ms.board["lean_raw"] = np.array([0.0])
    ms.mb = mk([(-600, 750, {760: 1.0}), (-240, 750, {760: 1.0})])
    ms.board["lean_raw"] = np.array([1.0, 0.4])
    assert ms.lean_at(T0) == approx(0.76)


# --- F2.12 regime ----------------------------------------------------------------------------------------------

def test_f212_regime_net_share_and_band():
    # spot 750, band 1% = 7.5: 745 (+2), 750 (-1), 755 (+1) in; 760 (-4, the King) out
    ms = state([(0, 750, {745: 2.0, 750: -1.0, 755: 1.0, 760: -4.0})])
    assert ms.board["regime_net"][0] == approx((2 - 1 + 1) / 4)
    assert ms.board["regime_pos_share"][0] == approx(3 / 4)
    assert ms.features_at(T0)["regime_long"] == 1


# --- F2.13 rug -----------------------------------------------------------------------------------------------

def test_f213_rug_and_reverse_rug():
    base = {700: 1.0, 757: 0.3, 758: -0.1, 759: -0.02, 760: 0.5}
    nodes = {n["strike"]: n for n in state([(0, 755, base)]).nodes_at(0)}
    assert nodes[760]["rug"] and nodes[760]["rug_neg_count"] == 2
    assert nodes[760]["rug_first_partner_pct"] == approx(0.02)
    assert not {n["strike"]: n for n in state([(0, 761, base)]).nodes_at(0)}[760]["rug"]
    pos = {**base, 759: 0.02}
    assert not {n["strike"]: n for n in state([(0, 755, pos)]).nodes_at(0)}[760]["rug"]
    weak = {**base, 760: 0.20}
    assert not {n["strike"]: n for n in state([(0, 755, weak)]).nodes_at(0)}[760]["rug"]
    rev = {700: 1.0, 750: 0.5, 751: -0.1}
    assert {n["strike"]: n for n in state([(0, 752, rev)]).nodes_at(0)}[750]["reverse_rug"]


# --- F2.14 pika clouds -----------------------------------------------------------------------------------------

def test_f214_pika_cloud_membership_with_hysteresis():
    cl = state([(0, 700, {700: -1.0, 749: 0.30, 750: 0.27})])
    assert [(c.lo, c.hi, c.n) for c in cl.clouds[0]] == [(749, 750, 2)]
    assert state([(0, 700, {700: -1.0, 749: 0.30, 750: 0.18})]).clouds[0] == []
    seq = state([(0, 700, {700: -1.0, 749: 0.30, 750: 0.26}), (1, 700, {700: -1.0, 749: 0.30, 750: 0.22}),
                 (2, 700, {700: -1.0, 749: 0.30, 750: 0.19})])
    assert [len(c) for c in seq.clouds] == [1, 1, 0]
    assert state([(0, 700, {700: -1.0, 749: 0.30, 750: -0.30})]).clouds[0] == []
    assert state([(0, 700, {700: -1.0, 749: 0.30, 750: 0.22})]).clouds[0] == []   # 0.22 never entered


# --- F2.15 air pockets -----------------------------------------------------------------------------------------

def test_f215_air_pocket_run_and_negative_share():
    tab = {750: 1.0, 751: 0.5, 752: -0.04, 753: 0.02, 754: -0.08, 755: -0.04, 756: 0.02, 757: 0.5}
    ms = state([(0, 754, tab)])
    pockets = [p for p in ms.pockets[0] if p.n > 1]
    assert len(pockets) == 1
    p = pockets[0]
    assert (p.lo, p.hi, p.n) == (752, 756, 5)
    assert p.neg_share == approx(0.16 / 0.20)
    tab2 = {**tab, 754: 0.12}
    assert all(p.n < 5 for p in state([(0, 754, tab2)]).pockets[0])


# --- F2.17 rainbow road ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("king,flag", [(1.1, True), (2.5, False)])
def test_f217_rainbow(king, flag):
    tab = {740: 1.0, 742: 0.9, 744: 0.8, 746: -0.9, 748: -0.8, 750: -0.7}
    tab[740] = king * 0.9          # dominance = king / 0.9 against 742
    f = state([(0, 745, tab)]).features_at(T0)
    assert f["n_pos_relevant"] == 3 and f["n_neg_relevant"] == 3 and f["sign_mix"] == 1.0
    assert f["rainbow"] is flag
