"""Area-event cross-market features (owner 2026-10-26, SPEC §13 Q-areasp; feature_spec F5.8, F5.16, F2.19).
The rule is tested here on hand-built stretches; the area-event builder that feeds it comes at step 19."""

from nqbot.features import area_cross as ac
from nqbot.features.trinity import CEILING, FLOOR, RoleTrack

M = 60.0  # times are seconds after 09:30:00
RHO = ac.area_role("val")  # a VAL-area event is long: floor


def floor_node(inside, crossed_down_at=None, pct=0.9, king=False, gatekeeper=False):
    roles = [(0.0, FLOOR)] + ([(crossed_down_at, CEILING)] if crossed_down_at is not None else [])
    crosses = [(crossed_down_at, FLOOR)] if crossed_down_at is not None else []
    return ac.Candidate(inside=inside, track=RoleTrack(roles=roles, crosses=crosses),
                        stats=lambda tau: {"pct_of_king": pct, "king": king, "gatekeeper": gatekeeper})


def test_roles():
    assert (ac.area_role("vah"), ac.area_role("val")) == (CEILING, FLOOR)


def test_es_in_a_spy_king_floor_band_while_nq_is_in_the_val_area():
    w0, t = 40 * M, 45 * M  # NQ entered the VAL area at 10:10, decision at 10:15
    king = floor_node([(42 * M, 43 * M)], pct=1.0, king=True)
    out = ac.area_sp({"SPY": [king]}, RHO, w0, t)
    assert out["area_sp_aligned_spy"] is True and out["area_sp_count"] >= 1
    assert (out["area_sp_pct_of_king_spy"], out["area_sp_king_spy"]) == (1.0, True)
    assert ac.es_at_level([king], RHO, w0, t) is True  # the SPY node is an ES level too


def test_that_spy_node_crossed_down_before_decision_ts_no_longer_counts():
    node = floor_node([(42 * M, 43 * M)], crossed_down_at=44 * M)
    assert ac.area_sp({"SPY": [node]}, RHO, 40 * M, 45 * M)["area_sp_aligned_spy"] is False


def test_es_at_the_spy_node_only_before_the_visit_started():
    node = floor_node([(35 * M, 38 * M)])
    out = ac.area_sp({"SPY": [node]}, RHO, 40 * M, 45 * M)
    assert out["area_sp_aligned_spy"] is False and out["area_sp_count"] == 0


def test_es_at_its_on_low_with_no_sp_node():
    on_low = ac.Candidate(inside=[(41 * M, None)], track=RoleTrack(roles=[(0.0, FLOOR)]))
    assert ac.es_at_level([on_low], RHO, 40 * M, 45 * M) is True
    assert ac.area_sp({"SPY": [], "SPX": []}, RHO, 40 * M, 45 * M)["area_sp_count"] == 0


def test_spy_and_spx_both_aligned():
    out = ac.area_sp({"SPY": [floor_node([(41 * M, 42 * M)])], "SPX": [floor_node([(43 * M, 44 * M)])]},
                     RHO, 40 * M, 45 * M)
    assert out["area_sp_aligned_spy"] and out["area_sp_aligned_spx"] and out["area_sp_count"] == 2


def test_visit_start_uses_the_earlier_weekly_entry():
    # weekly VAL from 10:00, daily VAL from 10:05, first firing 10:08: the visit starts at 10:00
    w0 = ac.visit_start({"weekly_val": [(30 * M, None)], "daily_val": [(35 * M, None)]}, 38 * M)
    assert w0 == 30 * M
    node = floor_node([(32 * M, 33 * M)])  # ES at a SPY floor node at 10:02
    assert ac.area_sp({"SPY": [node]}, RHO, w0, 40 * M)["area_sp_aligned_spy"] is True


def test_visit_start_after_a_gap_and_blocked_map():
    assert ac.visit_start({"daily_val": [(10 * M, 12 * M), (20 * M, None)]}, 25 * M) == 20 * M
    out = ac.area_sp({"SPY": [floor_node([(41 * M, 42 * M)])]}, RHO, 40 * M, 45 * M, blocked={"SPX"})
    assert out["area_sp_aligned_spx"] is None and out["area_sp_count"] == 1
