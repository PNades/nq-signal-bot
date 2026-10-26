"""Area-event cross-market features (owner 2026-10-26, SPEC §13 Q-areasp; feature_spec F5.8, F5.16, F2.19).
The rule is tested here on hand-built data; the area-event builder that feeds it comes at step 19."""

from nqbot.features import area_cross as ac
from nqbot.features import es_level as el
from nqbot.features.trinity import CEILING, FLOOR, RoleTrack

M = 60.0  # times are seconds after 09:30:00
RHO = ac.area_role("val")  # a VAL-area event is long: floor
TRADES = [10.0 * i for i in range(0, 6 * 60 + 1)]  # an ES/NQ trade or board apply every 10 s, 09:30-10:30


def spans(*ivs):
    """ES inside the band during the given (start, end or None) stretches."""
    return lambda tau: any(s <= tau and (e is None or tau <= e) for s, e in ivs)


def floor_node(inside, crossed_down_at=None, pct=0.9, king=False, gatekeeper=False, relevant_from=None):
    roles = [(0.0, FLOOR)] + ([(crossed_down_at, CEILING)] if crossed_down_at is not None else [])
    crosses = [(crossed_down_at, FLOOR)] if crossed_down_at is not None else []
    return ac.Candidate(inside_at=spans(*inside), track=RoleTrack(roles=roles, crosses=crosses),
                        relevant_at=(lambda tau: tau >= relevant_from) if relevant_from is not None else ac._always,
                        stats=lambda tau: {"pct_of_king": pct, "king": king, "gatekeeper": gatekeeper})


def taus(w0, t):
    return ac.check_times(w0, t, TRADES)


def test_roles():
    assert (ac.area_role("vah"), ac.area_role("val")) == (CEILING, FLOOR)


def test_es_in_a_spy_king_floor_band_while_nq_is_in_the_val_area():
    w0, t = 40 * M, 45 * M  # NQ entered the VAL area at 10:10, decision at 10:15
    king = floor_node([(42 * M, 43 * M)], pct=1.0, king=True)
    out = ac.area_sp({"SPY": [king]}, RHO, taus(w0, t), t)
    assert out["area_sp_aligned_spy"] is True and out["area_sp_count"] >= 1
    assert (out["area_sp_pct_of_king_spy"], out["area_sp_king_spy"]) == (1.0, True)
    spy_zone = el.single(king.inside_at, king.track, chain="SPY", member_type="spy_node")  # the SPY node is an ES level too
    assert el.es_at_level([spy_zone], RHO, taus(w0, t), t)["es_at_level"] is True


def test_that_spy_node_crossed_down_before_decision_ts_no_longer_counts():
    node = floor_node([(42 * M, 43 * M)], crossed_down_at=44 * M)
    assert ac.area_sp({"SPY": [node]}, RHO, taus(40 * M, 45 * M), 45 * M)["area_sp_aligned_spy"] is False


def test_es_at_the_spy_node_only_before_the_visit_started():
    node = floor_node([(35 * M, 38 * M)])
    out = ac.area_sp({"SPY": [node]}, RHO, taus(40 * M, 45 * M), 45 * M)
    assert out["area_sp_aligned_spy"] is False and out["area_sp_count"] == 0


def test_es_already_inside_before_the_visit_and_still_inside_at_its_start_counts():
    node = floor_node([(38 * M, 40 * M + 5)])  # inside from 10:08 to 10:10:05; the visit starts 10:10:00
    w0 = 40 * M + 3  # between two check times: the visit start itself is checked
    assert ac.area_sp({"SPY": [node]}, RHO, ac.check_times(w0, 45 * M, TRADES), 45 * M)["area_sp_aligned_spy"] is True


def test_node_becoming_relevant_while_es_is_inside_counts_from_that_moment():
    node = floor_node([(40 * M, None)], relevant_from=42 * M, pct=0.25)
    assert ac.first_overlap(node, RHO, taus(40 * M, 45 * M), 45 * M) == 42 * M
    assert ac.first_overlap(node, RHO, taus(40 * M, 41 * M), 41 * M) is None  # not yet relevant by 10:11


def test_es_at_its_on_low_with_no_sp_node():
    on_low = el.single(spans((41 * M, None)), RoleTrack(roles=[(0.0, FLOOR)]), member_type="es_on_low")
    assert el.es_at_level([on_low], RHO, taus(40 * M, 45 * M), 45 * M)["es_at_level"] is True
    assert ac.area_sp({"SPY": [], "SPX": []}, RHO, taus(40 * M, 45 * M), 45 * M)["area_sp_count"] == 0


def test_spy_and_spx_both_aligned():
    out = ac.area_sp({"SPY": [floor_node([(41 * M, 42 * M)])], "SPX": [floor_node([(43 * M, 44 * M)])]},
                     RHO, taus(40 * M, 45 * M), 45 * M)
    assert out["area_sp_aligned_spy"] and out["area_sp_aligned_spx"] and out["area_sp_count"] == 2


def test_stats_from_the_first_overlapping_node_that_still_counts():
    # A overlaps 10:01 and is crossed down by 10:04; B overlaps 10:03 and holds; decision 10:05 -> B's columns
    a = floor_node([(31 * M, 32 * M)], crossed_down_at=34 * M, pct=1.0, king=True)
    b = floor_node([(33 * M, 34 * M)], pct=0.4, gatekeeper=True)
    out = ac.area_sp({"SPY": [a, b]}, RHO, taus(30 * M, 35 * M), 35 * M)
    assert out["area_sp_aligned_spy"] is True
    assert (out["area_sp_pct_of_king_spy"], out["area_sp_king_spy"], out["area_sp_gatekeeper_spy"]) == (0.4, False, True)


def test_visit_start_uses_the_earlier_weekly_entry():
    # weekly VAL from 10:00, daily VAL from 10:05, first firing 10:08: the visit starts at 10:00
    w0 = ac.visit_start({"weekly_val": [(30 * M, None)], "daily_val": [(35 * M, None)]}, 38 * M)
    assert w0 == 30 * M
    node = floor_node([(32 * M, 33 * M)])  # ES at a SPY floor node at 10:02
    assert ac.area_sp({"SPY": [node]}, RHO, taus(w0, 40 * M), 40 * M)["area_sp_aligned_spy"] is True


def test_visit_start_after_a_gap():
    assert ac.visit_start({"daily_val": [(10 * M, 12 * M), (20 * M, None)]}, 25 * M) == 20 * M


def test_a_blocked_chain_nulls_its_map_and_the_count_like_trinity_count():
    out = ac.area_sp({"SPY": [floor_node([(41 * M, 42 * M)])]}, RHO, taus(40 * M, 45 * M), 45 * M, blocked={"SPX"})
    assert out["area_sp_aligned_spy"] is True and out["area_sp_aligned_spx"] is None
    assert out["area_sp_count"] is None
