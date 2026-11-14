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


# --- the engine speed fix (owner 2026-11-12, SPEC §13 Q-step13b (e)): both changes exact ------------------------------

def _eager_band_track(sf, e, dts, near, centre, tol):
    """The band track as first built: the whole window's edges at once (the world without the group's retested
    extreme built whole), then each removal time's first instant."""
    from nqbot.features.session_tape import NS

    tape = sf.nq
    end = dts + snap.TRACK_HORIZON_SEC * NS
    ts = tape.ts[tape.idx_at(dts):tape.idx_at(end)]
    ot = snap.own_time(sf, e)
    if ot is not None and len(ts) and int(ts[-1]) >= ot:
        k = int(np.searchsorted(ts, ot, side="left"))
        w, we = sf.world(e, ot)
        a, b = snap._edges_self(sf, e, ts[:k]), snap._edges_self(w, we, ts[k:])
        lo, hi = np.r_[a[0], b[0]], np.r_[a[1], b[1]]
    else:
        lo, hi = snap._edges_self(sf, e, ts)
    nr = lo if e.direction < 0 else hi
    ctr = (lo + hi) / 2
    out = {"t_move_near": None, "t_move_center": None, "t_vanish": None, "t_cross": None}
    with np.errstate(invalid="ignore"):
        gone = np.isnan(lo)
        for name, arr, ref in (("t_move_near", nr, near), ("t_move_center", ctr, centre)):
            k = np.flatnonzero(gone | (np.abs(arr - ref) > tol + 1e-9))
            out[name] = int(ts[k[0]]) if len(k) else None
        k = np.flatnonzero(gone)
        out["t_vanish"] = int(ts[k[0]]) if len(k) else None
    x = sf.cross(e, end)
    if e.kind == "level" and ot is not None and end >= ot and (x is None or x[0] >= ot):
        w, we = sf.world(e, ot)                   # owner 2026-11-13, Q-step13-review B2: the cross like the edges
        x = w.cross(we, end)
    if x is not None and x[0] > dts:
        if x[1] == "broke":
            out["t_cross"] = int(x[0])
        elif out["t_vanish"] is None or x[0] < out["t_vanish"]:
            out["t_vanish"] = int(x[0])
    return out


def _alt_builds(monkeypatch):
    from nqbot.features import session_features as sfm

    seen = []
    orig = sfm.SessionFeatures.build

    def build(self):
        if self.exclude_source is not None:
            seen.append(self.horizon_ns)
        return orig(self)
    monkeypatch.setattr(sfm.SessionFeatures, "build", build)
    return seen


def test_the_engine_speed_fix_changes_no_output(full, monkeypatch):  # noqa: F811
    """The lazy band track and the horizon-limited worlds give the engine stage's outputs exactly as the eager band
    track with whole-session worlds (the build before the fix)."""
    *_, sf = full
    seen = _alt_builds(monkeypatch)
    sf._alts.clear()
    sf._own_out.clear()
    with monkeypatch.context() as m:
        m.setattr(snap, "band_track", _eager_band_track)
        m.setattr(snap, "group_horizon", lambda sf_, e_: None)
        old = eb.session_outputs(sf, sf.touch_rows(), None, S)
    n_old = len(seen)
    assert n_old >= 1 and all(h is None for h in seen), "the synthetic day builds a world without a retested extreme"
    sf._alts.clear()
    sf._own_out.clear()
    del seen[:]
    new = eb.session_outputs(sf, sf.touch_rows(), None, S)
    for k in ("events", "decisions", "decision_rows", "level_events"):
        same(old[k], new[k], k)
    assert {k: v for k, v in old["info"].items() if not k.endswith("_sec")} == \
        {k: v for k, v in new["info"].items() if not k.endswith("_sec")}
    assert len(seen) <= n_old and all(h is not None for h in seen)


def test_a_horizon_world_answers_as_the_whole_one_up_to_its_horizon(full):  # noqa: F811
    from nqbot.features.session_features import SessionFeatures

    *_, sf = full
    groups = {}
    for r in sf.retested_today:
        if r.source in sf.by_id:
            e = sf.by_id[r.source]
            groups.setdefault(sf.group_of(e), e)
    assert groups
    for grp, e in sorted(groups.items(), key=lambda x: sorted(x[0])):
        h = snap.group_horizon(sf, e)
        whole = SessionFeatures(sf.c, exclude_source=grp).build()
        part = SessionFeatures(sf.c, exclude_source=grp, horizon_ns=h).build()
        assert part.horizon_ns == h and part.end_ns < whole.end_ns
        ot = snap.own_time(sf, e)
        ts = sf.nq.ts[sf.nq.idx_before(ot):sf.nq.idx_at(h)]
        ts = sorted(set(ts[::max(1, len(ts) // 150)].tolist()) | {int(ts[-1])}) if len(ts) else []
        for x in sorted(grp):
            if x not in whole.by_id:
                continue
            ew, ep = whole.by_id[x], part.by_id[x]
            same([whole.zone_edges(ew, t) for t in ts], [part.zone_edges(ep, t) for t in ts], f"edges {x}")
            same([sorted(m.uid for m in whole.members_at(ew, t)) for t in ts],
                 [sorted(m.uid for m in part.members_at(ep, t)) for t in ts], f"members {x}")
            same([whole.row(ew, t) for t in ts[::10]], [part.row(ep, t) for t in ts[::10]], f"rows {x}")
        same([whole.book.zones_at(t) for t in ts], [part.book.zones_at(t) for t in ts], "zone book")
        same([[(z, whole.nq_levels[m].band.at(t)) for z, mem in sorted(whole.book.zones_at(t).items()) for m in mem]
              for t in ts],
             [[(z, part.nq_levels[m].band.at(t)) for z, mem in sorted(part.book.zones_at(t).items()) for m in mem]
              for t in ts], "zone bands")


def test_a_cached_horizon_world_is_never_read_past_its_horizon(full, monkeypatch):  # noqa: F811
    *_, sf = full
    r = next(r for r in sf.retested_today if r.source in sf.by_id)
    e = sf.by_id[r.source]
    seen = _alt_builds(monkeypatch)
    sf._alts.clear()
    ot = snap.own_time(sf, e)
    w1, _ = sf.world(e, ot, need=ot, horizon=ot + 10**9)
    assert w1.horizon_ns == ot + 10**9 and seen == [ot + 10**9]
    assert sf.world(e, ot + 10**9)[0] is w1                   # within its horizon: reused
    w2, _ = sf.world(e, ot + 2 * 10**9)                        # past it, no horizon asked: the whole session again
    assert w2 is not w1 and w2.horizon_ns is None and seen == [ot + 10**9, None]
    sf._alts.clear()


def test_level_files_come_in_a_fixed_order(full, staged):  # noqa: F811
    """Owner 2026-11-12 (Q-step13b (e)): level_events/ and levels/ rows by level uid (the registries' own order came
    from carried sets, so it changed between processes with the string hash seed)."""
    from nqbot.features import level_timeline as lt

    *_, sf = full
    uids = [r["uid"] for r in staged["level_events"]]
    assert uids and uids == sorted(uids)
    for lv in list(sf.nq_levels.values()) + list(sf.containers):
        mine = [(r["t"], r["name"]) for r in staged["level_events"] if r["uid"] == lv.uid]
        assert mine == [(int(t), n) for t, n in lv.state.events]
    rows, _ = lt.session_rows(sf)
    nq = [r["uid"] for r in rows if r["instrument"] == "NQ"]
    es = [r["uid"] for r in rows if r["instrument"] != "NQ"]
    assert nq == sorted(nq) and es == sorted(es) and len(nq) == len(sf.nq_levels)


# --- B2 (owner 2026-11-13, SPEC §13 Q-step13-review): the own outcome readings on the synthetic day --------------------

def test_own_labels_and_crosses_equal_the_whole_world_without_the_groups_extreme(full):  # noqa: F811
    from nqbot.features.session_features import SessionFeatures

    *_, sf = full
    own = [e for e in sf.events if e.kind == "level" and sf.own_time(e) is not None]
    assert own, "the synthetic day has an event whose touch group made a retested extreme"
    for e in sf.events:
        lab, pend = sf.own_outcome(e)
        if e not in own:
            assert lab is sf.labels[e.event_id] and pend is sf.pair_end[e.event_id]
    for e in own:
        grp, ot = sf.group_of(e), sf.own_time(e)
        whole = SessionFeatures(sf.c, exclude_source=grp).build()
        t_end = slab.window_end(e.t, float(sf.f["hold_window_min"]), sf.flat_ns, sf.end_ns)
        x = sf.cross(e, t_end)
        want = x if (x is not None and x[0] < ot) or t_end < ot else whole.cross(whole.by_id[e.event_id], t_end)
        assert sf.own_cross(e, t_end) == want
        lab, pend = sf.own_outcome(e)
        assert lab == slab.label(e, sf.nq, want, t_end, float(sf.f["hold_reaction_pts"]))
        assert pend == slab.label(e, sf.nq, want, t_end, float(sf.f["pair_end_hold_pts"]))
        for t in (ot, (ot + t_end) // 2, t_end):                    # the band track's cross at any window end
            xt = sf.cross(e, t)
            assert sf.own_cross(e, t) == (xt if xt is not None and xt[0] < ot else
                                          whole.cross(whole.by_id[e.event_id], t))
    rows = {r["event_id"]: r for r in sf.outcome_rows()}
    for e in sf.events:
        lab, pend = sf.own_outcome(e)
        assert (rows[e.event_id]["label"], rows[e.event_id]["t_resolved"]) == (lab.label, lab.t_resolved)
        assert (rows[e.event_id]["pair_end_label"], rows[e.event_id]["t_pair_end"]) == (pend.label, pend.t_resolved)
