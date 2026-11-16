"""One review card from a hand-built synthetic day (review/cards.py, review/synthetic.py; SPEC §8: the chart stops at
the decision time and the outcome stays hidden until the owner answers; §13 Q-dryrun13 (12): synthetic only)."""

import random

import numpy as np
import pytest

from nqbot.features import session_tape as st
from nqbot.review import cards as cd
from nqbot.review import store as rs
from nqbot.review import synthetic as syn


@pytest.fixture(scope="module")
def day():
    return syn.synthetic_day()


@pytest.fixture(scope="module")
def card(day):
    dd, specs = day
    s = specs[1]                                    # c02: 11:25:10, style (d), stopped out
    return cd.card_payload(dd, s["id"], s["decision_id"], s["pair_id"], s["stratum"])


def test_nothing_in_pre_is_after_the_decision_time(day, card):
    dd, _ = day
    T = card["t"]["decision_ns"]
    Ts = T // cd.NS
    pre = card["pre"]
    for k in ("NQ", "ES"):
        assert pre["bars"][k] and pre["bars"][k][-1][0] <= Ts
        for lv in pre["levels"][k]:
            assert all(seg[0] <= Ts for seg in lv["segs"])
            assert all(r[0] <= Ts for r in lv["roles"]) and all(m[0] <= Ts for m in lv["marks"])
    assert all(e["t"] <= Ts for e in pre["events"])
    assert all(pt[0] <= Ts for z in pre["zones"] for pt in z["pts"])
    assert all(s["t"] <= Ts for snaps in pre["maps"].values() for s in snaps)
    assert all(k[0] <= Ts for ks in pre["kings"].values() for k in ks)


def test_the_bar_in_progress_is_built_from_trades_up_to_t_only(day, card):
    dd, _ = day
    T = card["t"]["decision_ns"]
    tape = dd.tapes["NQ"]
    last = card["pre"]["bars"]["NQ"][-1]
    m = T - T % cd.MIN_NS
    i0, i1 = tape.idx_before(m), tape.idx_at(T)
    assert last[0] == m // cd.NS
    assert last[4] == pytest.approx(tape.price[i1 - 1])                  # its close: the last trade at or before T
    assert last[2] == pytest.approx(tape.price[i0:i1].max()) and last[3] == pytest.approx(tape.price[i0:i1].min())
    # after the answer the same minute comes back whole (it replaces the partial bar)
    first_post = card["post"]["bars"]["NQ"][0]
    assert first_post[0] == last[0] and first_post[2] >= last[2] and first_post[3] <= last[3]


def test_the_card_outcome_and_later_labels_stay_hidden_until_the_answer(card):
    own = [e for e in card["pre"]["events"] if e["own"]]
    assert len(own) == 1 and own[0]["label"] is None
    earlier = [e for e in card["pre"]["events"] if not e["own"]]
    # c01 (10:12) resolved 25 min later, before c02 (11:25): its label is known; nothing resolved after T is labelled
    assert earlier and earlier[0]["label"] == "held"
    pub = rs.public_card(card, None)
    assert "post" not in pub and "outcome" not in pub and "stratum" not in pub and "why" not in pub and pub["answer"] is None
    full = rs.public_card(card, {"verdict": "skip"})
    assert full["outcome"]["result"] == "stop" and full["outcome"]["r"] == -1.0
    assert [e for e in full["post"]["events"] if e["own"]][0]["label"] == "broke"


def test_levels_roles_and_markers(card):
    lv = {x["uid"]: x for x in card["pre"]["levels"]["NQ"]}
    poc = lv["poc_daily"]
    # crossed 11:20, flipped 11:25 (before T 11:25:10): provisional, then settled in the new role
    assert [r[1:3] for r in poc["roles"]] == [["floor", False], ["ceiling", True], ["ceiling", False]]
    assert [m[1] for m in poc["marks"]] == ["cross", "flip"]
    vw = lv["vwap18"]
    assert len(vw["segs"]) > 100                    # the VWAP moves every minute
    assert lv["qqq_node-612"]["node"] and lv["qqq_node-612"]["chain"] == "QQQ"
    assert "qqq_node-605" in lv                     # relevant from 11:00, before T


def test_maps_kings_and_the_replay_span(day, card):
    dd, _ = day
    t = card["t"]
    assert t["replay_start"] == t["decision"] - t["decision"] % 60 - cd.REPLAY_LOOKBACK_MIN * 60
    q = card["pre"]["maps"]["QQQ"]
    assert q[0]["t"] == t["replay_start"] and q[-1]["t"] == t["minute"]
    assert q[-1]["king"] == 612.0 and len(q[-1]["s"]) <= cd.MAP_STRIKES_KEPT
    # the King switch to 615 at 12:40 is after T: not in pre, in post
    assert [k[1] for k in card["pre"]["kings"]["QQQ"]] == [612.0]
    later = syn.synthetic_day()[0]
    s3 = syn.synthetic_day()[1][3]                  # c04: 14:05
    c4 = cd.card_payload(later, s3["id"], s3["decision_id"], s3["pair_id"])
    assert [k[1] for k in c4["pre"]["kings"]["QQQ"]] == [612.0, 615.0]


def test_zone_bands_count_their_members(card):
    own = [z for z in card["pre"]["zones"] if z["own"]]
    assert len(own) == 1
    # the POC zone 21354-21378 holds the daily POC band at T (and, crossed back over, the VWAP sometimes)
    assert own[0]["pts"][-1][3] >= 1


def test_board_leader_ties():
    k = np.array([600.0, 605.0, 610.0])
    assert cd.board_leader(k, np.array([1.0, -3.0, 3.0]), 608.0) == 610.0     # tie: nearer spot
    assert cd.board_leader(k, np.array([1.0, -3.0, 3.0]), 607.0) == 605.0
    assert cd.board_leader(k, np.array([1.0, -3.0, 3.0]), 607.5) == 605.0     # equally near: the lower
    assert cd.board_leader(np.zeros(0), np.zeros(0), 1.0) is None
    # the same answer as the bot's own (node_switch.leader) on random boards with ties
    from nqbot.features import node_switch as ns
    rng = np.random.default_rng(4)
    for _ in range(300):
        ks = np.sort(rng.choice(np.arange(590.0, 630.0), 12, replace=False))
        vs = rng.integers(-4, 5, 12).astype(float)
        spot = float(rng.uniform(595, 625))
        assert cd.board_leader(ks, vs, spot) == ns.leader(dict(zip(ks.tolist(), vs.tolist())), spot)


def test_minute_grid_reads_each_minute_at_its_close_and_t1_itself():
    m = 1_000 * cd.MIN_NS
    g = cd.minute_grid(m, m + 2 * cd.MIN_NS + 5 * cd.NS)
    assert g == [(m, m + cd.MIN_NS), (m + cd.MIN_NS, m + 2 * cd.MIN_NS), (m + 2 * cd.MIN_NS, m + 2 * cd.MIN_NS + 5 * cd.NS)]
    assert cd.minute_grid(m, m + cd.MIN_NS) == [(m, m + cd.MIN_NS)]


def test_price_window_uses_only_the_bars_given():
    assert cd.price_window([]) is None
    lo, hi = cd.price_window([[0, 100, 110, 90, 105, 1]])
    assert lo == pytest.approx(90 - 1.1) and hi == pytest.approx(110 + 1.1)


def test_synthetic_round_is_repeatable():
    a, b = syn.synthetic_round(), syn.synthetic_round()
    assert [c["outcome"] for c in a[1]] == [c["outcome"] for c in b[1]]
    assert a[0]["synthetic"] and len(a[1]) == 5
    rng = random.Random(1)
    assert syn.tape(syn.DAY, 0, 10 * cd.NS, 100.0, rng).price.shape == (5,)


def test_a_decision_at_a_bar_close_shows_the_closed_bar_as_its_minute():
    dd, specs = syn.synthetic_day()
    d = next(x for x in dd.decisions if x["decision_id"] == specs[0]["decision_id"])
    T = d["decision_ts"] - d["decision_ts"] % cd.MIN_NS            # exactly 10:12:00.000 (a style-(c) bar close)
    dd.decisions = [{**x, "decision_ts": T} if x is d else x for x in dd.decisions]
    nq = dd.tapes["NQ"]
    keep = nq.ts != T                                               # no trade stamped exactly at T
    dd.tapes["NQ"] = st.Tape(nq.day, nq.symbol, nq.ts[keep], nq.price[keep], nq.size[keep], nq.side[keep])
    c = cd.card_payload(dd, "c01", specs[0]["decision_id"], specs[0]["pair_id"])
    assert c["t"]["minute"] == T // cd.NS - 60                       # the 10:11 bar, closed at T
    assert c["pre"]["bars"]["NQ"][-1][0] == c["t"]["minute"]
    assert c["pre"]["maps"]["QQQ"][-1]["t"] == c["t"]["minute"]
    assert any(seg[0] == c["t"]["minute"] or seg[0] < c["t"]["minute"] < seg[1] for lv in c["pre"]["levels"]["NQ"] for seg in lv["segs"][-1:])
    assert c["post"]["bars"]["NQ"][0][0] == c["t"]["minute"]
    assert cd.last_known_minute(T) == T - cd.MIN_NS and cd.last_known_minute(T + 1) == T
    assert cd.last_known_minute(T, nq) == T                         # a trade at T itself opens the 10:12 bar
