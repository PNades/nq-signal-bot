"""The step-13b engine stage on the synthetic session of the global no-lookahead test (SPEC §7 rule 1; owner
2026-11-10, SPEC §13 Q-step13b (1)): decision snapshots hold only data known at `decision_ts` (the band track, read only
to remove a pending entry, is the one exception), they equal the session's own queries, and the stage reads only
configuration the build key covers."""

from __future__ import annotations

import numpy as np
import pytest

from nqbot.checks import feature_build as fb
from nqbot.events import build as eb
from nqbot.events import snapshot as snap
from nqbot.events.common import decision_id
from nqbot.features import structure_label as slab
from tests.test_feature_lookahead import F, S, TIMES, base_data, build, full, same, variant  # noqa: F401

pytestmark = pytest.mark.slow
TRACK = ("t_move_near", "t_move_center", "t_vanish", "t_cross")


@pytest.fixture(scope="module")
def staged(full):  # noqa: F811
    *_, sf = full
    return eb.session_outputs(sf, sf.touch_rows(), None, S)


def known(rows, t):
    return [{k: v for k, v in r.items() if k not in TRACK} for r in rows if r["decision_ts"] <= t]


@pytest.mark.parametrize("t", TIMES[1:3])
@pytest.mark.parametrize("how", ["cut", 60 * 10**9, "changed"])
def test_decision_snapshots_hold_only_data_known_at_decision_ts(full, staged, t, how):  # noqa: F811
    want = known(staged["decisions"], t)
    assert want
    v = variant(full, t, how)
    got = known(eb.session_outputs(v, v.touch_rows(), None, S)["decisions"], t)
    same(want, got, f"how={how}")


def test_snapshots_equal_the_session_queries(full, staged):  # noqa: F811
    *_, sf = full
    n = 0
    for d in staged["decisions"][:80]:
        e = sf.by_id[d["event_id"]]
        assert d["decision_id"] == decision_id(e.event_id, d["style"], d["firing_n"])
        assert slab.pair_allowed(d["decision_ts"], sf.pair_end[e.event_id].t_resolved)
        lo, hi = sf.zone_edges(e, d["decision_ts"])
        assert (d["zone_lo"], d["zone_hi"]) == pytest.approx((lo, hi))
        t0 = e.t_armed if e.t_armed is not None else e.t
        p = sf.nq.price[sf.nq.idx_before(t0):sf.nq.idx_at(d["decision_ts"])]
        assert (d["swing_high"], d["swing_low"]) == (float(p.max()), float(p.min()))
        # the band track's first move, by asking zone_edges at every trade of the horizon
        near0 = lo if e.direction < 0 else hi
        ts = sf.nq.ts[sf.nq.idx_at(d["decision_ts"]):sf.nq.idx_at(d["decision_ts"] + snap.TRACK_HORIZON_SEC * 10**9)]
        first = None
        for t in ts[:400].tolist():
            ed = sf.zone_edges(e, t)
            nr = None if ed is None else (ed[0] if e.direction < 0 else ed[1])
            if nr is None or abs(nr - near0) > float(F["entry_reprice_tol"]) + 1e-9:
                first = t
                break
        if first is not None:
            assert d["t_move_near"] == first
            n += 1
    assert staged["decisions"]


def test_one_style_d_decision_per_event_and_events_rows_match(full, staged):  # noqa: F811
    *_, sf = full
    ev = staged["events"]
    assert [x["event_id"] for x in ev] == [e.event_id for e in sf.events]
    d_ids = {d["event_id"] for d in staged["decisions"] if d["style"] == "d"}
    for e in sf.events:
        allowed = slab.pair_allowed(e.t, sf.pair_end[e.event_id].t_resolved)
        assert (e.event_id in d_ids) == (allowed and sf.zone_edges(e, e.t) is not None)
    c = [d for d in staged["decisions"] if d["style"] == "c"]
    assert c                                      # the synthetic day has style-(c) firings to check
    rows = {r["decision_id"] for r in staged["decision_rows"]}
    assert {d["decision_id"] for d in c} == rows
    assert sum(x["n_firings_c"] for x in ev) == len(c)


def test_the_engine_stage_reads_only_configuration_the_build_key_covers(full):  # noqa: F811
    class Rec(dict):
        def __init__(self, d):
            super().__init__(d)
            self.read: set = set()

        def __getitem__(self, k):
            self.read.add(k)
            return super().__getitem__(k)

        def get(self, k, default=None):
            self.read.add(k)
            return super().get(k, default)

    *_, sf = full
    s = Rec(S)
    f = Rec(sf.f)
    sf.f = f
    try:
        eb.session_outputs(sf, sf.touch_rows(), None, s)
    finally:
        sf.f = dict(f)
    assert not (s.read & fb.NON_BUILD_SETTING_KEYS) and not (f.read & fb.NON_BUILD_FEATURE_KEYS)
