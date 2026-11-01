"""No lookahead in the Skylit, cross-market and multi-expiration features (step 11 part 2; SPEC §7 rule 1; feature_spec
F0.1 global test, B5): every value at t is unchanged when the boards and trades after t are removed, or shifted later
by one second or one bar (the part-1 cut/shift test extended to boards)."""

import math
from dataclasses import replace

import numpy as np
import pytest

from nqbot.config import load_features, load_settings
from nqbot.features import cross_market as cm
from nqbot.features import multiexp as mx
from nqbot.features import session_tape as st
from nqbot.features.boards import MapBoards
from nqbot.features.node_levels import Scale
from nqbot.features.skylit_session import Context, SkylitSession
from tests.test_map_state import DAY, NS, T0
from tests.test_multiexp import PREV, doc, qqq_root

F = load_features()
S = load_settings()
END = 1500   # seconds after T0


def synthetic():
    rng = np.random.default_rng(7)
    out = {}
    for name, spot0, step, n_k, r0 in (("QQQ", 600.0, 1.0, 25, 41.0), ("NDXP", 24_600.0, 10.0, 25, 1.0),
                                       ("SPY", 700.0, 1.0, 25, 10.0), ("SPX", 7000.0, 5.0, 25, 1.0)):
        strikes = spot0 + step * (np.arange(n_k) - n_k // 2)
        vals = rng.uniform(0.05, 1.0, n_k) * rng.choice([-1, 1], n_k, p=[0.3, 0.7])
        t, ks, vs, offs, spot, r = [], [], [], [0], [], []
        sp = spot0
        every, off = (3, 0.4) if name == "NDXP" else (1, 0.0)   # NDXP off QQQ's grid: its (QQQ's) blocks start between
        for s in range(0, END, every):                          # its boards (review 2026-10-31)
            vals = vals * np.exp(rng.normal(0, 0.05, n_k))
            if rng.random() < 0.01:
                vals[rng.integers(n_k)] *= -1
            sp += rng.normal(0, step * 0.05)
            present = rng.random(n_k) > 0.02          # a strike now and then missing from a board (F2.1)
            t.append(T0 + int(round((s + off) * NS)))
            ks.extend(strikes[present].tolist())
            vs.extend(vals[present].tolist())
            offs.append(len(ks))
            spot.append(sp)
            jump = 1.0025 if name == "QQQ" and s >= 600 else 1.0    # a dislocation-guard fire at 600 s (F1.8)
            r.append(r0 * jump * (1 + rng.normal(0, 1e-5)))
        ta = np.array(t, dtype=np.int64)
        rr = np.array(r)
        out[name] = MapBoards(name, DAY, ta, ta, ta, np.array(spot), rr.copy(), rr.copy(), np.zeros(len(ta), bool),
                              np.array(offs, dtype=np.int64), np.array(ks), np.array(vs))
    nq_px = 600.0 * 41.0 + np.cumsum(rng.normal(0, 3.0, 2 * END + 400))
    es_px = 700.0 * 10.0 + np.cumsum(rng.normal(0, 0.8, 2 * END + 400))
    secs = np.arange(-200, END, 0.5)[: len(nq_px)]
    nq = st.Tape.from_arrays(DAY, (T0 + secs * NS).astype(np.int64), nq_px[: len(secs)],
                             rng.integers(1, 10, len(secs)), rng.choice([-1, 0, 1], len(secs)), symbol="NQH6")
    es = st.Tape.from_arrays(DAY, (T0 + secs * NS).astype(np.int64), es_px[: len(secs)],
                             rng.integers(1, 10, len(secs)), rng.choice([-1, 0, 1], len(secs)), symbol="ESH6")
    return out, nq, es


def cut_boards(mb: MapBoards, t: int, shift_ns: int | None) -> MapBoards:
    """Boards applied after t removed (shift None) or moved later by shift_ns."""
    j = mb.index_at(t) + 1
    if shift_ns is None:
        o = mb.offsets[: j + 1]
        return replace(mb, as_of=mb.as_of[:j], tau=mb.tau[:j], t_apply=mb.t_apply[:j], spot=mb.spot[:j],
                       r_board=mb.r_board[:j], r=mb.r[:j], one_minute=mb.one_minute[:j], offsets=o,
                       s_strike=mb.s_strike[: o[-1]], s_value=mb.s_value[: o[-1]])
    sh = np.where(np.arange(len(mb)) >= j, shift_ns, 0)
    return replace(mb, as_of=mb.as_of + sh, tau=mb.tau + sh, t_apply=mb.t_apply + sh)


def cut_tape(tape: st.Tape, t: int, shift_ns: int | None) -> st.Tape:
    if shift_ns is None:
        return tape.cut(t)
    j = tape.idx_at(t)
    ts = tape.ts.copy()
    ts[j:] += shift_ns
    return st.Tape(tape.day, tape.symbol, ts, tape.price, tape.size, tape.side)


def build(boards, nq, es):
    ctx = Context(F, S, em_nq=250.0, em_es=60.0, atr_nq=300.0, atr_es=70.0, em_frac={"QQQ": 0.01, "SPY": 0.01},
                  scale=Scale(0.28, 0.285, T0 + 30 * NS))
    return SkylitSession(boards, nq, es, ctx, end_ns=T0 + (END + 200) * NS)


def snapshot(sess: SkylitSession, nq, es, t: int) -> dict:
    out = {"features": sess.features_at(t, +1), "registry": sorted(sess.registry_at(t), key=lambda x: x["level_id"]),
           "es_registry": sorted(sess.es_registry_at(t), key=lambda x: x["level_id"]),
           "cross": cm.features_at(nq, es, t, F, +1)}
    for rho in ("ceiling", "floor"):
        out[f"trinity_{rho}"] = sess.trinity(t - 20 * NS, t, rho)
        nq_band = (float(nq.last_price_at(t)) - 8, float(nq.last_price_at(t)) + 8)

        def nq_inside(tau, b=nq_band):
            p = nq.last_price_at(tau)
            return p is not None and b[0] <= p <= b[1]
        out[f"es_at_level_{rho}"] = sess.es_at_level(t - 20 * NS, t, rho, nq_inside)
    out["sp_path"] = sess.sp_path(t, float(nq.last_price_at(t)) + 60, +1)
    out["pockets"] = {m: sess.pockets(m, t, +1) for m in sess.ms}
    root = qqq_root()
    root["strikes"] = [{"strike": x["strike"] * 6.0, "value": 0.0} for x in root["strikes"]]  # mapped near NQ
    snap = mx.parse(doc([root]), "1000", DAY, PREV, F)
    out["mx"] = mx.MultiExp([snap]).features_at(
        t, {"nasdaq": nq.last_price_at(t), "sp": es.last_price_at(t)}, {"nasdaq": 250.0, "sp": 60.0},
        lambda m, tt: (None if sess.board_at(m, tt) < 0 else float(sess.ms[m].mb.r[sess.board_at(m, tt)])), 3.0,
        blocked_of=sess.blocked)
    return out


def same(a, b, path="") -> None:
    if isinstance(a, dict):
        assert set(a) == set(b), path
        for k in a:
            same(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, (list, tuple)):
        assert len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b)):
            same(x, y, f"{path}[{i}]")
    elif isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


@pytest.fixture(scope="module")
def full():
    boards, nq, es = synthetic()
    return boards, nq, es, build(boards, nq, es)


@pytest.mark.parametrize("sec", [300.4, 610.2, 700.0, 1100.7])
def test_no_lookahead_cut_and_shift(full, sec):
    boards, nq, es, sess = full
    t = T0 + int(round(sec * NS))
    want = snapshot(sess, nq, es, t)
    assert want["registry"] and want["features"]["qqq_king_strike"] is not None
    if not sess.blocked("QQQ", t):
        assert want["mx"]["mx_wall_above_dist_em_nasdaq"] is not None or want["mx"]["mx_wall_below_dist_em_nasdaq"] is not None
    for shift in (None, NS, 60 * NS):
        b2 = {m: cut_boards(mb, t, shift) for m, mb in boards.items()}
        nq2, es2 = cut_tape(nq, t, shift), cut_tape(es, t, shift)
        got = snapshot(build(b2, nq2, es2), nq2, es2, t)
        same(want, got, f"shift={shift}")


def test_the_synthetic_day_has_a_block(full):
    _, _, _, sess = full
    assert sess.blocks["QQQ"] and sess.blocks["QQQ"][0][0] == T0 + 600 * NS
