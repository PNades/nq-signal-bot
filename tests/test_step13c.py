"""Step 13c, the tuning on months 1-4 (owner 2026-11-14, SPEC §13 Q-step13c; §7.1 Tuning detector parameters, §7 rule
3b, Q-step13plan (1), (3), (4), (8), Q13-vol): hand-built examples of the bootstrap pick, the sequential run, the
pick rules and their ties, the fold bar on the shared events, the comparison, the lead over the next-best point, the
cross-validation's embargo, the row program, the volatility-scaled stop floor and the driver's resume logic."""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import date, time

import numpy as np
import pytest

from nqbot.config import load_features, load_settings
from nqbot.features import session_tape as st
from nqbot.labeler import candidates as cand
from nqbot.labeler import params as lp
from nqbot.tuning import compare as cmp
from nqbot.tuning import pick as pk
from nqbot.tuning import policy as pol
from nqbot.tuning import program as prog
from nqbot.tuning import provisional as prov
from nqbot.tuning import stats
from nqbot.tuning import structure_inputs as si
from nqbot.tuning import volfloor as vf
from nqbot.tuning.data import Build

def start_13c(settings: dict, features: dict) -> tuple[dict, dict]:
    """13c's start configuration: the committed one before after-13c step 6 wrote the winners in (owner 2026-11-21,
    SPEC §13 Q-step13c (e): `sweep_max_depth` 15 -> 10, `sweep_reclaim_bars` 3 -> 2, `entry_ttl_sec` c 60 -> 120,
    `entry_order_type` d limit -> marketable_limit). The driver's program is defined on its start values (row 12 needs
    one start ttl for (c) and (d)), so the 13c tests keep them rather than the committed winners."""
    s, f = copy.deepcopy(settings), copy.deepcopy(features)
    s["entry_ttl_sec"] = {**s["entry_ttl_sec"], "c": 60, "d": 60}
    s["entry_order_type"] = {**s["entry_order_type"], "c": "stop_limit", "d": "limit"}
    f["sweep_max_depth"], f["sweep_reclaim_bars"] = 15, 3
    return s, f


S, F = start_13c(load_settings(), load_features())
DAY = date(2026, 1, 15)
NS = st.NS


def at(h, m, s=0.0):
    return st.et_ns(DAY, time(h, m)) + int(s * NS)


# --- the bootstrap pair (§6.3) ----------------------------------------------------------------------------------------

def pair(pid, stop="2", kind="zone_near", rr=1.5, ext=False, pts=10.0, dec="D1", ot="limit"):
    return {"pair_id": pid, "decision_id": dec, "order_type": ot, "stop_types": stop, "target_kind": kind,
            "net_rr": rr, "rr_ext": ext, "target_pts": pts}


def test_bootstrap_pair_is_stop_2_with_the_nearest_target_at_1_to_1():
    rows = [pair("a", stop="1", pts=5), pair("b", rr=0.9, pts=6), pair("c", kind="baseline", pts=7),
            pair("d", ext=True, pts=8), pair("e", pts=20), pair("f", stop="1,2", pts=12), pair("g", pts=12)]
    best = pol.pick_bootstrap(rows)
    assert best[("D1", "limit")]["pair_id"] == "f"        # merged "1,2" counts as candidate 2; 12 < 20; f < g by id
    assert pol.pick_bootstrap([pair("x", rr=0.99)]) == {}
    two = pol.pick_bootstrap([pair("y", pts=9), pair("z", pts=3, ot="stop_limit")])
    assert set(two) == {("D1", "limit"), ("D1", "stop_limit")}   # one per order type


# --- the sequential run (§7 rule 3b) -----------------------------------------------------------------------------------

def cand_(dec, t, style="d", group="g1", status="filled", exit_t=None, r=1.0, removal=None, tradeable=True, fn=1,
          event=None, day="2026-01-15"):
    return {"day": day, "decision_id": dec, "event_id": event or dec, "touch_group_id": group, "style": style,
            "firing_n": fn, "decision_ts": t, "tradeable": tradeable, "fill_status": status,
            "removal_t": removal if removal is not None else t + 60 * NS,
            "t_exit_none": exit_t if exit_t is not None else t + 600 * NS, "r_none": r,
            "t_exit_fs": exit_t if exit_t is not None else t + 600 * NS, "r_fs": r / 2}


def test_one_trade_at_a_time_and_each_touch_group_once():
    t0 = at(10, 0)
    cs = [cand_("A", t0, group="g1", exit_t=t0 + 300 * NS, r=2.0),
          cand_("B", t0 + 100 * NS, group="g2", r=5.0),             # while A is open: skipped, never queued
          cand_("C", t0 + 400 * NS, group="g1", r=3.0),             # A's group already traded
          cand_("D", t0 + 400 * NS, group="g3", r=-1.0)]
    out = pol.simulate(cs)
    assert [t["decision_id"] for t in out] == ["A", "D"] and sum(t["r"] for t in out) == 1.0


def test_an_unfilled_entry_blocks_until_removed_then_frees_its_group():
    t0 = at(10, 0)
    cs = [cand_("A", t0, group="g1", status="unfilled", removal=t0 + 30 * NS),
          cand_("A2", t0 + 10 * NS, group="g1", style="c"),            # pending: skipped
          cand_("A3", t0 + 30 * NS, group="g1", style="c", r=4.0)]     # removed at 30 s: the group may trade again
    out = pol.simulate(cs)
    assert [(t["decision_id"], t["filled"]) for t in out] == [("A", False), ("A3", True)]
    assert sum(t["r"] for t in out if t["filled"]) == 4.0


def test_same_moment_style_c_before_d_and_untradeable_rows_skipped():
    t0 = at(10, 0)
    out = pol.simulate([cand_("d1", t0, style="d", group="g1"), cand_("c1", t0, style="c", group="g2", r=7.0)])
    assert [t["decision_id"] for t in out] == ["c1"]
    assert pol.simulate([cand_("x", t0, tradeable=False)]) == []


def test_no_book_entries_are_counted_and_never_block():
    t0 = at(10, 0)
    out = pol.simulate([cand_("nb", t0, status="no_book"), cand_("ok", t0 + NS, group="g2", r=2.0)])
    assert [(t["decision_id"], t["no_book"], t["filled"]) for t in out] == [("nb", True, False), ("ok", False, True)]


def test_each_day_starts_free_and_the_fs_rule_reads_its_own_columns():
    t0 = at(15, 0)
    cs = [cand_("a", t0, exit_t=t0 + 10_000 * NS, r=1.0), cand_("b", t0, group="g2", r=1.0, day="2026-01-16")]
    assert len(pol.simulate(cs)) == 2
    assert pol.simulate([cand_("a", t0, r=2.0)], "fs")[0]["r"] == 1.0
    assert pol.per_day(pol.simulate(cs), ["2026-01-15", "2026-01-16", "2026-01-17"]) == [1.0, 1.0, 0.0]


# --- the pick rules (Q-step13plan (8), keep-current) -------------------------------------------------------------------

def res(v, d, lo=-1.0, hi=1.0):
    return {"value": v, "diff": d, "lo": lo, "hi": hi}


def test_best_value_rule_and_every_tie():
    dist = pk.numeric_distance
    assert pk.best_value(5, [res(2, 0.5), res(8, 1.5), res(12, 1.0)], dist) == 8
    assert pk.best_value(5, [res(2, -0.1), res(8, 0.0)], dist) == 5           # none above 0: the start stays
    assert pk.best_value(5, [res(2, 1.0), res(12, 1.0)], dist) == 2           # tie: the closest to the start
    assert pk.best_value(5, [res(2, 1.0, lo=0.1), res(8, 1.0, lo=0.3)], dist) == 8   # equally far: larger lower bound
    assert pk.best_value(5, [res(2, 1.0, lo=0.3), res(8, 1.0, lo=0.3)], dist) == 5   # still equal: the start stays


def test_keep_current_needs_the_interval_to_exclude_zero():
    assert pk.keep_current("on", [res("off", 2.0, lo=-0.1, hi=4.0)]) == "on"
    assert pk.keep_current("on", [res("off", 2.0, lo=0.1, hi=4.0)]) == "off"
    assert pk.keep_current(10, [res(3, 1.0, lo=0.2), res(5, 3.0, lo=0.1)]) == 3    # the largest lower bound
    assert pk.keep_current_lower(60, [res(30, -0.01, lo=-0.02, hi=0.001)]) == 60
    assert pk.keep_current_lower(60, [res(30, -0.01, lo=-0.02, hi=-0.001), res(120, -0.02, lo=-0.03, hi=-0.005)]) == 120


def test_row_12_ties_fewer_changes_first_then_the_nearest_ttl_in_seconds():
    # owner 2026-11-14 (Q-step13c (7)): only the ttl < only the order type < both, then |ttl - start|
    start = ("limit", 60)
    d = pk.exec_distance
    assert d(("limit", 30), start) < d(("limit", 120), start) < d(("stop_limit", 60), start) < d(("stop_limit", 30), start)
    pts = [res(("stop_limit", 60), 1.0), res(("limit", 120), 1.0), res(("limit", 30), 1.0)]
    assert pk.best_value(start, pts, d) == ("limit", 30)
    # two order-type-only points equally far with the same difference: the larger lower bound, then the start stays
    assert pk.best_value(start, [res(("stop_limit", 60), 1.0, lo=0.2), res(("marketable_limit", 60), 1.0, lo=0.1)],
                         d) == ("stop_limit", 60)
    assert pk.best_value(start, [res(("stop_limit", 60), 1.0, lo=0.2), res(("marketable_limit", 60), 1.0, lo=0.2)],
                         d) == start


# --- the bar on the shared events, the comparison and the lead ---------------------------------------------------------

def mk_build(key, events, days=("2026-01-15",), month=4):
    evs = [{"event_id": eid, "day": dd, "touch_group_id": f"g{eid}", "month": month, "tradeable": True,
            "damaged": False, "held": True, "labelled": True,
            "subset": {"golden": False, "va_edge": False, "prev_profile": False, "node": eid.startswith("n"),
                       "retest": False}} for eid, dd in events]
    rows = {}
    for i, (eid, dd) in enumerate(events):
        rows.setdefault(dd, []).append({"event_id": eid, "touch_idx": i, "direction": 1, "founding_level_ids": [eid[-1]],
                                        "in_golden_zone": False, "touch_zone_lo": 0.0, "touch_zone_hi": 1.0})
    return Build(key, None, list(days), {d: month for d in days}, evs, rows, np.zeros((len(evs), 0)), [], [])


def test_each_builds_bar_is_its_own_median_over_the_shared_events():
    a = mk_build("A", [("a1", "2026-01-15"), ("a2", "2026-01-15"), ("a3", "2026-01-15")])
    pa = {"a1": 0.2, "a2": 0.6, "a3": 0.9}
    # only a1 and a2 are shared: the bar is their median, applied to all of A's events
    bar = cmp.bars(a, pa, ["a1", "a2"])
    assert bar == {4: pytest.approx(0.4)}
    assert cmp.allowed(a, pa, bar) == {"a2", "a3"}


def picks_for(build, t0, r):
    return [{**cand_(e["event_id"], t0 + i * 1000 * NS, group=e["touch_group_id"], r=r.get(e["event_id"], 1.0),
                     event=e["event_id"]), "order_type": "limit"} for i, e in enumerate(build.events)]


def test_comparing_a_build_with_itself_gives_zero():
    a = mk_build("A", [("x1", "2026-01-15"), ("x2", "2026-01-15"), ("x3", "2026-01-15")])
    p = {"x1": 0.3, "x2": 0.6, "x3": 0.9}
    pk_ = picks_for(a, at(10, 0), {"x2": 2.0, "x3": -1.0})
    out = cmp.compare(a, a, p, p, pk_, pk_, {"c": "stop_limit", "d": "limit"}, "combined", "node", 200)
    r = out["rules"]["none"]
    assert out["events"]["shared"] == 3 and r["diff"] == 0 and r["lo"] == 0 and r["hi"] == 0
    # the bar is the median 0.6; only x3 is above it (strictly, "above the fold median")
    assert r["start"]["trades"] == 1 and r["start"]["r_per_month"] == pytest.approx(-1.0)


def test_the_excess_score_values_each_trade_at_its_margin_over_the_start_mean():
    # start: 4 trades, -4 R (mean -1); value: 2 trades, -1 R: total +3 but excess = 3 - (-1) x (2 - 4) = +1
    rs, ns = np.array([-2.0, -2.0]), np.array([2.0, 2.0])
    rc, nc = np.array([-1.0, 0.0]), np.array([1.0, 1.0])
    sc = cmp.score(rc, nc, rs, ns, 1.0, 500)
    assert sc["total"][0] == pytest.approx(3.0) and sc["diff"] == pytest.approx(1.0)
    assert sc["trades_change"] == -2 and sc["trades_change_pct"] == pytest.approx(-0.5)
    assert sc["per_trade_start"] == pytest.approx(-1.0) and sc["per_trade_value"] == pytest.approx(-0.5)
    # dropping trades at exactly the start's mean scores 0 (a losing policy is not rewarded for trading less)
    rc2, nc2 = np.array([-1.0, -1.0]), np.array([1.0, 1.0])
    assert cmp.score(rc2, nc2, rs, ns, 1.0, 500)["diff"] == pytest.approx(0.0)


def test_random_thinning_is_neutral_under_the_excess_score_and_not_under_the_total():
    # 84 days of a losing policy (8 trades a day, -0.18 R each on average, as the baseline); half the trades removed
    # at random, 40 draws: the total net R calls the thinned run better far more often than the excess does
    tot_above, ex_above, ex_mean = 0, 0, []
    for seed in range(40):
        rng = np.random.default_rng(seed)
        r_tr = rng.normal(-0.18, 1.0, size=(84, 8))
        keep = rng.random((84, 8)) < 0.5
        rs, ns = r_tr.sum(1), np.full(84, 8.0)
        rc, nc = (r_tr * keep).sum(1), keep.sum(1).astype(float)
        sc = cmp.score(rc, nc, rs, ns, 4.0, 300)
        tot_above += sc["total"][0] > 0
        ex_above += sc["diff"] > 0
        ex_mean.append(sc["diff"])
    assert tot_above >= 34                          # the total "prefers" trading less (about 9 times in 10)
    assert 10 <= ex_above <= 30                     # the excess is a coin toss
    assert abs(np.mean(ex_mean)) < 1.0


def test_the_lead_over_the_next_best_point_is_paired_by_day():
    z = np.zeros(3)
    w = {"series": [np.array([3.0, 1, 2]), np.array([1.0, 1, 1]), z, np.array([1.0, 1, 1])]}
    r = {"series": [np.array([2.0, 1, 1]), np.array([1.0, 1, 1]), np.array([1.0, 0, 0]), np.array([1.0, 1, 1])]}
    # same trade counts as the start: excess = total difference; (6 - 0) - (4 - 1) = 3
    lead = cmp.lead(w, r, 1.0, 500)
    assert lead[0] == pytest.approx(3.0) and lead[1] <= lead[0] <= lead[2]
    assert cmp.lead(w, None, 1.0, 500)[0] == pytest.approx(6.0)        # the next-best is the start value
    assert cmp.lead(None, r, 1.0, 500)[0] == pytest.approx(-3.0)       # the start wins: its lead over r


def test_day_block_interval_resamples_whole_days():
    x = np.array([1.0, 1.0, 1.0, 1.0])
    assert stats.per_month_ci(x, 4.0, 100) == (1.0, 1.0, 1.0)
    p, lo, hi = stats.per_month_ci(np.array([4.0, -4.0, 0.0, 0.0]), 2.0, 2000)
    assert p == 0 and lo < 0 < hi


# --- the provisional model's cross-validation ---------------------------------------------------------------------------

def test_the_embargo_leaves_out_the_sessions_next_to_the_predicted_month():
    pos = np.arange(10)
    assert prov.train_mask(pos, 4, 6).tolist() == [True, True, True, False, False, False, False, False, True, True]


def test_out_of_fold_predictions_never_see_their_own_months_labels():
    rng = np.random.default_rng(0)
    n_days, per = 40, 30
    pos = np.repeat(np.arange(n_days), per)
    fold = pos // 10 + 1
    X = rng.normal(size=(len(pos), 3))
    y = (X[:, 0] + rng.normal(scale=0.5, size=len(pos)) > 0).astype(float)
    w = np.ones(len(pos))
    s = {**S, "lgbm_grid": {**S["lgbm_grid"], "fixed": {**S["lgbm_grid"]["fixed"], "num_boost_round_max": 30,
                                                          "early_stopping_rounds": 5}},
         "tune_13": {**S["tune_13"], "provisional_model": {"num_leaves": 7, "min_data_in_leaf": 20}}}
    tr = np.ones(len(pos), dtype=bool)
    p1, info = prov.cv_predict(X, y, w, pos, fold, tr, [], s, 2)
    y2 = y.copy()
    y2[fold == 2] = 1 - y2[fold == 2]          # month 2's own labels flipped
    p2, _ = prov.cv_predict(X, y2, w, pos, fold, tr, [], s, 2)
    assert np.array_equal(p1[fold == 2], p2[fold == 2])     # month 2 is predicted without its labels
    assert not np.isnan(p1).any() and len(info) == 4


def test_the_structure_inputs_leave_out_ids_prices_day_type_gameplan_and_outcomes():
    schema = [("event_id", "string"), ("ts", "numeric"), ("touch_price", "numeric"), ("qqq_king_strike", "numeric"),
              ("or_fib_prices_0", "numeric"), ("dt_vwap_crosses", "numeric"), ("gp_in_zone", "null"),
              ("founding_level_ids", "list"), ("zone_width", "numeric"), ("role", "string"),
              ("king_present", "bool"), ("label", "string"), ("with_day_direction", "numeric"), ("tradeable", "bool")]
    num, cat = si.input_columns(schema)
    assert num == ["zone_width", "king_present", "with_day_direction"] and cat == ["role"]
    # the recorded stack members (pre-13d, Q-review-stack (b)) are an id column, never an input, whatever its type
    assert si.excluded("stack_level_ids") and si.input_columns([("stack_level_ids", "string")]) == ([], [])


# --- the row program (Q-step13plan (1)) --------------------------------------------------------------------------------

def test_the_rows_run_in_the_owners_order_with_their_rules():
    rows = prog.rows(F, S)
    assert [r.id for r in rows] == ["r01", "r02", "r03", "r04", "r05a", "r05b", "r05c", "r06", "r07", "r08", "r09",
                                    "r10", "r11", "r12", "r13", "r14a", "r14b"]
    assert [r.name for r in rows[:4]] == ["zone_proximity", "confluence_tol", "sweep_max_depth", "sweep_reclaim_bars"]
    assert [r.extra.get("group") for r in rows[4:7]] == ["gex_nodes", "chart_lines", "highs_lows"]
    assert [r.name for r in rows[7:13]] == ["golden_zone_variants", "va_edge_sweep", "prev_profile_anchor",
                                            "node_relevance_top_n", "node_role_at_formation", "retest_lookback_sessions"]
    assert {r.id: r.rule for r in rows}["r01"] == "best" and {r.id: r.rule for r in rows}["r06"] == "keep"
    assert rows[3].mode == "c" and rows[3].info_modes == ("d", "combined")
    assert all(r.start in r.values for r in rows)          # F16.4: every grid holds its start value
    gz = rows[7]
    assert gz.start == (True, True) and len(gz.challengers) == 3
    assert gz.build_overrides((False, True)) == {"golden_zone_flip": False, "golden_zone_redraw_1030": True}
    assert rows[4].build_overrides(2) == {"flip_margin.qqq_node": 2, "flip_margin.ndxp_node": 2}
    assert rows[0].build_overrides(5) == {} and rows[0].batches() == [[2, 8], [12]]
    ids = prog.chunk_ids(rows)
    assert ids[:4] == prog.PRE and ids[-1] == "end:summary"
    assert ids.index("r01:score") < ids.index("r02:b1") < ids.index("r12:labels") < ids.index("r14a:b1")


# --- the volatility-scaled stop floor (Q13-vol) ------------------------------------------------------------------------

def test_trailing_range_reads_only_bars_completed_by_t():
    ts = [at(10, m, 1) for m in range(20)] + [at(10, m, 50) for m in range(20)]
    px = [100.0] * 20 + [100.0 + m for m in range(20)]
    order = np.argsort(ts)
    tape = st.Tape.from_arrays(DAY, np.array(ts)[order], np.array(px)[order])
    bars = st.bars(tape, 1)
    # at 10:16:00 the bars of 10:01 ... 10:15 are complete (15 slots); ranges 1 ... 15
    assert cand.trailing_range(bars, at(10, 16), 15) == pytest.approx(np.mean(np.arange(1, 16)))
    assert cand.trailing_range(bars, at(10, 16, 30), 15) == pytest.approx(np.mean(np.arange(1, 16)))  # 10:16 open
    assert cand.trailing_range(bars, at(10, 0, 30), 15) is None


def test_the_vol_floor_replaces_the_fixed_floor_of_its_source_only():
    p = lp.params(S, F, {"stop_floor_vol": {"window_min": 15, "QQQ": {"min": 2.79, "k": 0.195}}})
    assert p.stop_floor_vol == {"window_min": 15, "QQQ": {"min": 2.79, "k": 0.195}}
    assert cand.source_floor(p, "QQQ", 40.0) == pytest.approx(7.8)
    assert cand.source_floor(p, "QQQ", 5.0) == pytest.approx(2.79)
    assert cand.source_floor(p, "QQQ", None) == pytest.approx(2.79)
    assert cand.source_floor(p, "NDXP", 40.0) == S["stop_buffer_floor"]["NDXP"]
    off = lp.params(S, F)
    assert off.stop_floor_vol is None and cand.source_floor(off, "QQQ", 40.0) == S["stop_buffer_floor"]["QQQ"]
    assert lp.label_key("b", p) != lp.label_key("b", off)
    with pytest.raises(ValueError):
        lp.params(S, F, {"stop_floor_vol": {"window_min": 15}})
    with pytest.raises(ValueError):
        lp.params(S, F, {"stop_floor_vol": {"window_min": 15, "QQQ": {"min": 1.0}}})


def test_stop_2_uses_the_vol_floor_from_the_decision_point():
    p = replace(lp.params(S, F, {"stop_floor_vol": {"window_min": 15, "QQQ": {"min": 1.0, "k": 0.5}}}), stop_buffer=1.0)
    d = {"direction": 1, "zone_lo": 100.0, "zone_hi": 102.0, "zone_lo_src": "QQQ", "zone_hi_src": "QQQ",
         "swing_low": None, "z_id": [], "z_kind": [], "z_lo": [], "z_hi": [], "z_lo_src": [], "z_hi_src": [],
         "event_type": "level", cand.VOL_KEY: 20.0}
    (stop, typ), = cand.stops(d, 103.0, 1, p)
    assert typ == "2" and stop == pytest.approx(90.0)       # 100 - max(1, 0.5 x 20)


def test_the_floor_fit_follows_the_studys_rule():
    rng = np.random.default_rng(3)
    vol = rng.uniform(5, 30, size=4000)
    err = rng.normal(scale=0.1 * vol)
    res = vf.fit(err, vol)
    a = np.abs(err)
    assert res["coverage"] >= 0.95
    floor = np.maximum(res["min"], (res["k"] - 0.005) * vol)
    assert (a <= floor).mean() < 0.95                 # the smallest k on the grid
    assert vf.override(res) == {"window_min": 15, "QQQ": {"min": res["min"], "k": res["k"]}}


# --- the driver ---------------------------------------------------------------------------------------------------------

def test_the_driver_resolves_each_row_at_the_earlier_winners(tmp_path, monkeypatch):
    from nqbot.checks import step13c as s13c

    w = {"r01": {"build_overrides": {"zone_proximity": 8}}, "r02": {"build_overrides": {}},
         "r03": {"build_overrides": {"sweep_max_depth": 25}}}
    monkeypatch.setattr(s13c, "winners", lambda env: w)

    class E:
        features, settings = F, S

    assert s13c.build_over(E, "r04") == {"zone_proximity": 8, "sweep_max_depth": 25}
    with pytest.raises(s13c.TuneRefused):
        s13c.build_over(E, "r05a")                    # r04 has no winner yet
    assert s13c.order_types(E, "r12") == {"c": S["entry_order_type"]["c"], "d": S["entry_order_type"]["d"]}
    w["r12"] = {"order_types": {"c": "limit", "d": "limit_center"}, "label_overrides": {"entry_ttl_sec.d": 30}}
    for k in ("r04", "r05a", "r05b", "r05c", "r06", "r07", "r08", "r09", "r10", "r11"):
        w[k] = {"build_overrides": {}}
    assert s13c.order_types(E, "r13") == {"c": "limit", "d": "limit_center"}
    assert s13c.label_over(E, "r13") == {"entry_ttl_sec.d": 30}


def test_a_builds_chunk_skips_finished_builds_and_plans_the_rest_in_slots_1_and_2(monkeypatch):
    from nqbot.checks import step13c as s13c

    row = prog.rows(F, S)[0]
    planned, ran = [], []
    monkeypatch.setattr(s13c, "build_over", lambda env, rid: {})
    monkeypatch.setattr(s13c, "key_for", lambda env, over: f"k{over.get('zone_proximity')}")
    monkeypatch.setattr(s13c, "build_complete", lambda env, key, days: key == "k2" or key in done)
    monkeypatch.setattr(s13c.fbj, "plan_slot", lambda e, months, batch, over, slot: planned.append((over, slot)))
    done: set = set()

    def fake_run(env, ctx, jobs):
        from nqbot.jobs.runner import RunResult

        ran.append(jobs)
        done.update({"k8", "k12"})
        return {j: RunResult("complete", 1, 1) for j in jobs}

    monkeypatch.setattr(s13c, "run_jobs", fake_run)

    class Env_:
        features, settings = F, S
        paths = log = cal = None

        def echo(self, *_):
            pass

    s13c.TuneJob._builds(None, Env_(), None, row, 1, [])     # batch 1 = [2, 8]: 2 is built already
    assert planned == [({"zone_proximity": 8}, 1)] and ran == [["feature_build"]]
    planned.clear()
    s13c.TuneJob._builds(None, Env_(), None, row, 2, [])     # batch 2 = [12]: built by now, nothing runs
    assert planned == [] and ran == [["feature_build"]]


def test_a_child_stopped_by_the_clock_leaves_the_chunk_to_rerun(monkeypatch):
    from nqbot.checks import step13c as s13c
    from nqbot.jobs.runner import RunResult, StopChunk

    row = prog.rows(F, S)[0]
    monkeypatch.setattr(s13c, "build_over", lambda env, rid: {})
    monkeypatch.setattr(s13c, "key_for", lambda env, over: f"k{over.get('zone_proximity')}")
    monkeypatch.setattr(s13c, "build_complete", lambda env, key, days: False)
    monkeypatch.setattr(s13c.fbj, "plan_slot", lambda *a: None)
    monkeypatch.setattr(s13c, "run_jobs", lambda env, ctx, jobs: {"feature_build": RunResult("complete", 1, 1),
                                                                  "feature_build_2": RunResult("until", 0, 1)})

    class Env_:
        features, settings = F, S
        paths = log = cal = None

        def echo(self, *_):
            pass

    with pytest.raises(StopChunk) as e:
        s13c.TuneJob._builds(None, Env_(), None, row, 1, [])
    assert e.value.status is None                      # the runner turns it into "until": resume runs it again



def test_a_child_stopped_on_the_build_budget_passes_that_status_on(monkeypatch):
    """Owner 2026-11-15: a budget mutex that stayed locked was recorded as `until`; the driver now passes it on."""
    from nqbot.checks import step13c as s13c
    from nqbot.jobs.runner import RunResult, StopChunk

    row = prog.rows(F, S)[0]
    monkeypatch.setattr(s13c, "build_over", lambda env, rid: {})
    monkeypatch.setattr(s13c, "key_for", lambda env, over: f"k{over.get('zone_proximity')}")
    monkeypatch.setattr(s13c, "build_complete", lambda env, key, days: False)
    monkeypatch.setattr(s13c.fbj, "plan_slot", lambda *a: None)
    monkeypatch.setattr(s13c, "run_jobs", lambda env, ctx, jobs: {"feature_build": RunResult("budget_stall", 85, 86),
                                                                  "feature_build_2": RunResult("until", 85, 86)})

    class Env_:
        features, settings = F, S
        paths = log = cal = None

        def echo(self, *_):
            pass

    with pytest.raises(StopChunk) as e:
        s13c.TuneJob._builds(None, Env_(), None, row, 1, [])
    assert e.value.status == "budget_stall"

# --- the scoring functions end to end on synthetic builds (reports and winners in a temporary folder) ------------------

def _synthetic(tmp_path, monkeypatch, n_events=60, seed=0):
    from nqbot.checks import step13c as s13c

    rng = np.random.default_rng(seed)
    days = [f"2026-01-{d:02d}" for d in range(5, 25)]
    ev = [(f"e{i}", days[i % len(days)]) for i in range(n_events)]
    b0 = mk_build("K0", ev, days=days)
    for i, e in enumerate(b0.events):
        e["month"] = 1 + (i % 4)
    p = {e["event_id"]: float(rng.random()) for e in b0.events}

    def picks(build, r_shift=0.0, ots=None):
        out = []
        for i, e in enumerate(build.events):
            for s in ("c", "d"):
                for ot in (ots or {"c": ["limit", "marketable_limit", "stop_limit"],
                                   "d": ["limit", "marketable_limit", "stop_limit", "limit_center"]})[s]:
                    t0 = at(10, 0) + (i // len(days)) * 1800 * NS + (0 if s == "d" else 60 * NS)
                    out.append({**cand_(f"{e['event_id']}-{s}1", t0, style=s, group=e["touch_group_id"],
                                        r=float(rng.normal(-0.1 + r_shift, 1.0)), event=e["event_id"],
                                        day=e["day"]), "order_type": ot})
        return out

    monkeypatch.setattr(s13c, "rep_dir", lambda env: tmp_path / "rep")
    monkeypatch.setattr(s13c, "winners_file", lambda env: tmp_path / "winners.json")
    monkeypatch.setattr(s13c, "label_over", lambda env, rid: {})
    monkeypatch.setattr(s13c, "build_over", lambda env, rid: {})
    monkeypatch.setattr(s13c, "lkey_of", lambda env, key, over: "L" + "_".join(f"{k}={v}" for k, v in sorted(over.items())
                                                                              if k != "stop_floor_vol")
                        + ("_vol" if "stop_floor_vol" in over else ""))
    return s13c, b0, p, picks, days


class _Ld:
    def __init__(self, builds, ps, picks):
        self.builds, self.ps, self._picks, self.n_months, self.threads = builds, ps, picks, 4.0, 1

    def build(self, k):
        return self.builds[k]

    def p(self, k):
        return self.ps[k]

    def picks(self, k, lk):
        return self._picks[(k, lk)]


class _E:
    features, settings = F, {**S, "tune_13": {**S["tune_13"], "bootstrap_replicates": 200}}
    paths = log = cal = None

    def echo(self, *_):
        pass


def test_a_build_row_scores_reports_and_records_its_winner(tmp_path, monkeypatch):
    import json as _json

    s13c, b0, p, picks, _ = _synthetic(tmp_path, monkeypatch)
    row = prog.rows(F, S)[0]                                   # zone_proximity: challengers 2, 8, 12
    keys = {None: "K0", 2: "K2", 8: "K8", 12: "K12"}
    monkeypatch.setattr(s13c, "key_for", lambda env, over: keys[over.get("zone_proximity")])
    builds = {k: b0 for k in keys.values()}
    pk_ = {("K0", "L"): picks(b0), ("K2", "L"): picks(b0, 0.3), ("K8", "L"): picks(b0, -0.3), ("K12", "L"): picks(b0)}
    out = s13c.score_build_row(_E(), {}, row, _Ld(builds, {k: p for k in keys.values()}, pk_))
    w = _json.loads((tmp_path / "winners.json").read_text(encoding="utf-8"))["r01"]
    assert out["winner"] == w["value"] and out["score"].startswith("excess")
    r = out["results"]["2"]["rules"]
    assert {"none", "fs", "c:none", "d:none"} <= set(r) and "total" in r["none"] and "per_trade" in r["none"]
    md = next((tmp_path / "rep").glob("r01_*.md")).read_text(encoding="utf-8")
    assert "excess R/mo" in md and "(c)-only excess" in md and "Lead over the next-best" in md


def test_rows_12_and_13_score_on_the_frozen_events(tmp_path, monkeypatch):
    import json as _json

    s13c, b0, p, picks, _ = _synthetic(tmp_path, monkeypatch, seed=1)
    rows = prog.rows(F, S)
    monkeypatch.setattr(s13c, "key_for", lambda env, over: "K0")
    vol = tmp_path / "volfloor.json"
    vol.write_text(_json.dumps({"override": {"window_min": 15, "QQQ": {"min": 2.8, "k": 0.2}}}), encoding="utf-8")
    monkeypatch.setattr(s13c, "vol_fit_file", lambda env: vol)
    r12, r13 = rows[13], rows[14]
    pk_ = {("K0", s13c.lkey_of(None, "K0", s13c.ttl_over(r12, t))): picks(b0, 0.05 * i) for i, t in enumerate(r12.values)}
    out12 = s13c.score_exec_ttl(_E(), {}, r12, _Ld({"K0": b0}, {"K0": p}, pk_))
    assert set(out12["styles"]) == {"c", "d"} and len(out12["styles"]["c"]["points"]) == 3 * 5 - 1
    monkeypatch.setattr(s13c, "order_types", lambda env, rid: {"c": "stop_limit", "d": "limit"})
    pk13 = {}
    for f in ("fixed", "vol"):
        for i, buf in enumerate(r13.values):
            pk13[("K0", s13c.lkey_of(None, "K0", s13c.stop_over(_E(), r13, {}, f, buf)))] = picks(b0, 0.02 * i)
    out13 = s13c.score_exec_stop(_E(), {}, r13, _Ld({"K0": b0}, {"K0": p}, pk13))
    assert out13["winner"]["floor"] in ("fixed", "vol") and set(out13["floors"]) == {"fixed", "vol"}
    md = next((tmp_path / "rep").glob("r13_*.md")).read_text(encoding="utf-8")
    assert "Vol-scaled against fixed" in md and "(c) excess" in md
