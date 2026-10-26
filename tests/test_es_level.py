"""F5.16 `es_at_level` with the dislocation-block rule (owner 2026-10-26, SPEC §13 Q-dislguard-2 (a); F1.8), for level
events (NQ inside the event's zone at the same moment, window from the touch) and area events (window from the visit start)."""

from nqbot.features import area_cross as ac
from nqbot.features import es_level as el
from nqbot.features.trinity import FLOOR, RoleTrack

M = 60.0
FLOOR_ROLE = RoleTrack(roles=[(0.0, FLOOR)])
TIMES = [10.0 * i for i in range(0, 6 * 60 + 1)]


def spans(*ivs):
    return lambda tau: any(s <= tau and (e is None or tau <= e) for s, e in ivs)


def both(nq, es):
    """Level events: NQ inside the event's zone and ES inside the ES zone at the same moment."""
    return lambda tau: nq(tau) and es(tau)


NQ_IN_ZONE = spans((40 * M, 46 * M))  # level event: NQ in its zone from the touch at 10:10


def level_case(es_on_low=None, es_spy=None):
    zones = []
    if es_on_low:
        zones.append(el.single(both(NQ_IN_ZONE, spans(es_on_low)), FLOOR_ROLE, member_type="es_on_low"))
    if es_spy:
        zones.append(el.single(both(NQ_IN_ZONE, spans(es_spy)), FLOOR_ROLE, chain="SPY", member_type="spy_node"))
    return zones, ac.check_times(40 * M, 45 * M, TIMES), 45 * M


def area_case(es_on_low=None, es_spy=None):
    zones = []
    if es_on_low:
        zones.append(el.single(spans(es_on_low), FLOOR_ROLE, member_type="es_on_low"))
    if es_spy:
        zones.append(el.single(spans(es_spy), FLOOR_ROLE, chain="SPY", member_type="spy_node"))
    return zones, ac.check_times(40 * M, 45 * M, TIMES), 45 * M


def run(case, blocked=frozenset(), **kw):
    zones, taus, t = case(**kw)
    return el.es_at_level(zones, FLOOR, taus, t, blocked)


def test_spy_blocked_es_at_its_on_low_is_true():
    for case in (level_case, area_case):
        out = run(case, {"SPY"}, es_on_low=(42 * M, 43 * M), es_spy=(41 * M, 42 * M))
        assert out == {"es_at_level": True, "es_stack_count": 1, "es_member_types": ("es_on_low",)}


def test_spy_blocked_es_at_no_unaffected_level_is_null():
    for case in (level_case, area_case):
        assert run(case, {"SPY"}, es_spy=(41 * M, 42 * M)) == {
            "es_at_level": None, "es_stack_count": None, "es_member_types": None}
        assert run(case, {"SPY"})["es_at_level"] is None  # ES at no level at all: still can't be ruled out


def test_no_block_es_at_no_level_is_false():
    for case in (level_case, area_case):
        assert run(case) == {"es_at_level": False, "es_stack_count": 0, "es_member_types": ()}


def test_no_block_a_spy_node_zone_counts():
    for case in (level_case, area_case):
        assert run(case, es_spy=(41 * M, 42 * M))["es_member_types"] == ("spy_node",)


def test_a_zone_with_a_member_from_the_blocked_chain_does_not_count():
    mixed = el.EsZone(inside_at=spans((41 * M, 42 * M)), members=[FLOOR_ROLE, FLOOR_ROLE], chains=frozenset({"SPY"}))
    taus = ac.check_times(40 * M, 45 * M, TIMES)
    assert el.es_at_level([mixed], FLOOR, taus, 45 * M, {"SPY"})["es_at_level"] is None
    assert el.es_at_level([mixed], FLOOR, taus, 45 * M, {"SPX"})["es_at_level"] is True  # only SPX blocked: it counts
    assert el.es_at_level([mixed], FLOOR, taus, 45 * M)["es_stack_count"] == 2


def test_level_event_needs_nq_in_its_zone_at_the_same_moment():
    late = el.single(both(NQ_IN_ZONE, spans((47 * M, 48 * M))), FLOOR_ROLE)  # ES arrives after NQ left
    assert el.es_at_level([late], FLOOR, ac.check_times(40 * M, 50 * M, TIMES), 50 * M)["es_at_level"] is False
