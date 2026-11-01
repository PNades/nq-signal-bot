"""Multi-expiration context (feature_spec F2.23; owner 2026-10-31 Q-step11d (2): walls re-chosen at t)."""

from datetime import date, time, timedelta

from pytest import approx

from nqbot.config import load_features
from nqbot.features import multiexp as mx
from nqbot.features import session_tape as st

F = load_features()
DAY = date(2026, 3, 20)
PREV = date(2026, 3, 19)
NS = st.NS


def doc(roots):
    return {"body": {"data": {"symbols": roots}}}


def qqq_root(as_of="2026-03-20T13:59:00Z"):
    # strikes 98, 100, 102, 105, 110; columns: an expired one (the day before), 0DTE, +3 d, +20 d, +60 d
    exps = [DAY - timedelta(days=1), DAY, DAY + timedelta(days=3), DAY + timedelta(days=20), DAY + timedelta(days=60)]
    strikes = [98.0, 100.0, 102.0, 105.0, 110.0]
    m = [[0.0] * 5 for _ in strikes]
    m[1][0] = 99.0          # expired: dropped
    m[1][1] = 5.0           # 0DTE +5 at 100
    m[2][2] = -3.0          # 1-7 days -3 at 102
    m[0][2] = -4.0          # 1-7 days -4 at 98
    m[3][3] = -2.0          # 8-45 days -2 at 105
    m[4][4] = 50.0          # a 60-day column: ignored
    return {"symbol": "QQQ", "asOf": as_of, "expirations": [e.isoformat() for e in exps],
            "strikes": [{"strike": k, "value": 0.0} for k in strikes], "matrix": m}


def ex(snaps):
    return mx.MultiExp(snaps)


def t_et(h, m, s=0):
    return st.et_ns(DAY, time(h, m)) + s * NS


def feats(e, t, px=100.0, em=10.0, blocked=False):
    return e.features_at(t, {"nasdaq": px, "sp": None}, {"nasdaq": em, "sp": None}, lambda m, t: 1.0,
                         float(F["mx_wall_reach_em"]), blocked_of=lambda m, t: blocked)


def test_f223_signs_agreement_and_walls():
    s = mx.parse(doc([qqq_root()]), "1000", DAY, PREV, F)
    r = s.roots["QQQ"]
    assert r.n == approx(-1.0) and r.z == approx(1.0)
    out = feats(ex([s]), t_et(10, 5))
    assert out["mx_net_sign_nasdaq"] == -1 and out["mx_0dte_agree_nasdaq"] == 0.0
    assert out["mx_wall_above_dist_em_nasdaq"] == approx(0.2)        # 102 (w 3/9), 2 pts / EM 10
    assert out["mx_wall_below_dist_em_nasdaq"] == approx(0.2)        # 98 (w 4/9)
    assert r.w[2] == approx(3 / 9) and r.w[0] == approx(4 / 9)
    assert out["mx_0dte_share_nasdaq"] == approx(5 / 14)
    assert out["mx_snapshot"] == "1000"


def test_q_step11d_2_wall_rechosen_at_t():
    s = mx.parse(doc([qqq_root()]), "1000", DAY, PREV, F)
    out = feats(ex([s]), t_et(10, 5), px=103.0)
    assert out["mx_wall_above_dist_em_nasdaq"] == approx(0.2)        # 105 now: (105 - 103) / 10
    assert out["mx_wall_below_dist_em_nasdaq"] == approx(0.5)        # 98 (w 4/9 beats 102's 3/9), 5 / 10
    far = feats(ex([s]), t_et(10, 5), px=200.0)
    assert far["mx_wall_above_dist_em_nasdaq"] is None and far["mx_wall_below_dist_em_nasdaq"] is None
    blk = feats(ex([s]), t_et(10, 5), blocked=True)
    assert blk["mx_wall_above_dist_em_nasdaq"] is None and blk["mx_net_sign_nasdaq"] == -1


def test_f223_in_force_times_and_stale_root():
    a = mx.parse(doc([qqq_root("2026-03-19T20:00:00Z")]), "0925", DAY, PREV, F)
    b = mx.parse(doc([qqq_root()]), "1000", DAY, PREV, F)
    e = ex([a, b])
    delay = int(F["mx_snapshot_delay_sec"])
    assert feats(e, t_et(9, 59))["mx_snapshot"] == "0925"
    assert feats(e, t_et(10, 0, delay - 1))["mx_snapshot"] == "0925"
    assert feats(e, t_et(10, 0, delay))["mx_snapshot"] == "1000"
    assert feats(e, t_et(9, 25))["mx_snapshot"] is None
    stale = mx.parse(doc([qqq_root("2026-03-19T19:58:00Z")]), "0925", DAY, PREV, F)   # 15:58 ET the day before
    assert stale.roots["QQQ"] is None
