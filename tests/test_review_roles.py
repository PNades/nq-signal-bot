"""A level's role over the day from its stored level events (SPEC §9b item 3 Levels; review/roles.py), hand cases."""

from nqbot.review import roles


def steps(evs):
    return [(s.t, s.role, s.provisional, s.broken_back, s.flips) for s in roles.timeline(evs)]


def test_cross_then_flip_settles_the_new_role():
    evs = [(10, "role_ceiling"), (20, "armed"), (30, "cross"), (31, "prov_armed"), (40, "flip")]
    assert steps(evs) == [(10, "ceiling", False, False, 0), (30, "floor", True, False, 0), (40, "floor", False, False, 1)]


def test_cross_back_then_reclaim_returns_the_original_role():
    evs = [(10, "role_floor"), (30, "cross"), (35, "cross_back"), (36, "reclaim")]
    assert steps(evs) == [(10, "floor", False, False, 0), (30, "ceiling", True, False, 0),
                          (35, "ceiling", True, True, 0), (36, "floor", False, False, 0)]


def test_resume_undoes_the_cross_back_and_same_time_events_collapse():
    evs = [(10, "role_floor"), (30, "cross"), (35, "cross_back"), (50, "resume")]
    assert steps(evs)[-1] == (50, "ceiling", True, False, 0)
    # a cross back and its reclaim at the same instant (the confirming bar): one step, the reclaim's
    evs2 = [(10, "role_floor"), (30, "cross"), (60, "cross_back"), (60, "reclaim")]
    assert steps(evs2)[-1] == (60, "floor", False, False, 0)


def test_role_from_price_events_take_the_given_role():
    # the 13-0 return rule sets a confirmed role from price's side, which the caller reads from price
    evs = [(10, "role_ceiling"), (30, "cross"), (40, "return_role", "ceiling")]
    assert steps(evs)[-1] == (40, "ceiling", False, False, 0)
    assert steps([(5, "natural_role")]) == []          # no role given: nothing to draw


def test_sweeps_and_arming_leave_the_role_and_unknown_names_are_ignored(caplog):
    evs = [(10, "role_ceiling"), (20, "break_start"), (25, "swept"), (30, "armed"), (40, "something_new")]
    assert steps(evs) == [(10, "ceiling", False, False, 0)]
    assert "something_new" in caplog.text


def test_role_at_and_markers():
    evs = [(10, "role_floor"), (30, "cross"), (40, "flip"), (50, "cross"), (55, "cross_back"), (56, "reclaim")]
    st = roles.timeline(evs)
    assert roles.role_at(st, 5) is None
    assert roles.role_at(st, 45).role == "ceiling" and not roles.role_at(st, 45).provisional
    assert roles.role_at(st, 52).role == "floor" and roles.role_at(st, 52).provisional
    assert roles.markers(evs) == [(30, "cross"), (40, "flip"), (50, "cross"), (56, "reclaim")]
