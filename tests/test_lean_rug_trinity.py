"""Owner answers 2026-10-25: lean sign and magnitude velocity (Q1/Q2), whipsaw twice (Q17), rug partners and the
per-board strike step with mixed spacing (Q12/Q13/Q27), Trinity with provisional roles (Q18/Q19)."""

from nqbot.features import lean, rug, trinity


def test_barney_node_growing_is_building_in_the_lean():
    assert lean.velocity(-2e6, -1e6) == -1.0  # signed per-node velocity stays (Skylit's velocityPct)
    assert lean.magnitude_velocity(-2e6, -1e6) == 1.0
    assert lean.magnitude_velocity(1e6, 0) is None and lean.velocity(None, 1e6) is None


def test_lean_sign_above_minus_below_is_bias_up():
    # a relevant node above spot building (+0.5 at 1.0 of King), one below unchanged -> lean +0.5 = up
    nodes = [(760.0, 0.5, 1.0, True), (750.0, 0.0, 0.5, True), (770.0, 3.0, 0.1, False), (755.0, 9.0, 1.0, True)]
    raw = lean.lean_raw(nodes, 755.0)  # the strike at spot and the irrelevant node are skipped
    assert raw == 0.5 and lean.bias(raw, 0.05) == "up"
    below = lean.lean_raw([(750.0, 0.5, 1.0, True)], 755.0)
    assert below == -0.5 and lean.bias(below, 0.05) == "down"
    assert lean.bias(0.04, 0.05) == "pinned"


def test_lean_time_weighted_f29_example():
    assert abs(lean.lean([0.0, 360.0], [1.0, 0.4], 600.0, 600.0) - 0.76) < 1e-12


def test_whipsaw_per_trinity():
    # SPX up, SPY pinned; QQQ down, NDXP up -> the QQQ trinity conflicts, the NDXP one doesn't
    assert lean.trinity_bias("pinned", "up", "down") == (1, True)
    assert lean.whipsaw("pinned", "up", "down", True, True) is True
    assert lean.whipsaw("pinned", "up", "up", True, True) is False
    assert lean.whipsaw("pinned", "up", "down", True, False) is False


def test_strike_step_with_mixed_spacing_is_per_board():
    spot = 20_000.0
    a = [*range(19_800, 19_950, 10), *range(19_950, 20_050, 5), *range(20_050, 20_201, 10)]
    b = [*range(19_800, 19_900, 10), *range(19_900, 20_100, 5), *range(20_100, 20_201, 10)]
    assert rug.strike_step(a, spot, 1.0) == 10.0  # 30 gaps of 10, 20 of 5 within +-1%
    assert rug.strike_step(b, spot, 1.0) == 5.0   # 40 gaps of 5, 20 of 10
    assert rug.strike_step([19_990, 20_000, 20_005], spot, 1.0) == 5.0  # tie 1-1 -> the smaller (owner 2026-10-26)
    vals = {19_990.0: -1.0, 19_995.0: 3.0, 20_000.0: 9.0}
    # P = 20,000 with spot below: with step 10 the negative 19,990 is in reach; with step 5 it is not
    assert rug.rug_partners(vals, 20_000.0, 19_980.0, 10.0, 1, True) == [19_990.0]
    assert rug.rug_partners(vals, 20_000.0, 19_980.0, 5.0, 1, True) == []


def test_rug_partner_rules():
    vals = {755: 1.0, 756: -1.0, 757: -5.0, 759: -0.01, 760: 9.0}  # no 758 listed
    # 759 in reach (any size), 757 is the next list position and negative (contiguous), 756 too, 755 positive ends it
    assert rug.rug_partners(vals, 760, 750.0, 1.0, 1, True) == [759, 757, 756]
    assert rug.rug_partners({759: 0.0, 760: 9.0}, 760, 750.0, 1.0, 1, True) == []  # exactly 0 is not a partner
    assert rug.rug_partners(vals, 760, 750.0, 1.0, 1, False) == []  # P must be relevant
    assert rug.rug_partners({750: 9.0, 751: -1.0}, 750, 752.0, 1.0, 1, True) == [751]  # reverse rug


def test_trinity_counts_provisional_roles():
    # a SPY ceiling crossed upward at 100 holds the floor role provisionally; a long post-cross retest at 130 (rho floor)
    tr = trinity.RoleTrack(roles=[(0, "ceiling"), (100, "floor")], crosses=[(100, "ceiling")])
    assert trinity.counts_in_role(tr, "floor", tau=130, t=140)
    assert not trinity.counts_in_role(tr, "ceiling", tau=130, t=140)
    # crossed back against the floor role after the overlap -> no longer counts at t
    tr.crosses.append((150, "floor"))
    tr.roles.append((150, "ceiling"))
    assert trinity.counts_in_role(tr, "floor", tau=130, t=140)
    assert not trinity.counts_in_role(tr, "floor", tau=130, t=160)


def test_trinity_unroled_node_takes_the_role_from_its_entry_side():
    tr = trinity.RoleTrack(roles=[(0, None)])
    assert trinity.counts_in_role(tr, "floor", tau=10, t=20, entered_from="above")
    assert not trinity.counts_in_role(tr, "floor", tau=10, t=20, entered_from="below")
    assert not trinity.counts_in_role(tr, "floor", tau=10, t=20)


def test_es_at_level_counts_a_provisional_es_level_on_a_post_cross_retest():
    # owner 2026-10-26: an NQ post-cross retest (long, rho floor; the QQQ ceiling was crossed upward) with ES at an ES PDH
    # zone that was also crossed upward at 95 and holds the floor role provisionally -> es_at_level counts it
    es_pdh = trinity.RoleTrack(roles=[(0, "ceiling"), (95, "floor")], crosses=[(95, "ceiling")])
    assert trinity.counts_in_role(es_pdh, "floor", tau=130, t=135)
    es_pdh.crosses.append((140, "floor"))  # crossed back below before decision_ts -> no longer counts
    assert not trinity.counts_in_role(es_pdh, "floor", tau=130, t=145)
