"""F3.5 single prints: formation, unfilled sub-runs, identity, carry with roll shift, stacked count (owner
2026-10-24) and the band edge (owner 2026-10-31, SPEC §13 Q-step11a)."""

from datetime import date, time

from nqbot.features import session_tape as st
from nqbot.features import tpo

DAY = date(2026, 3, 6)
NS = st.NS


def t_et(h, m, s=0):
    return st.et_ns(DAY, time(h, m, s))


def up_move_tape(extra=()):
    """Price sits at 100 in the 09:30 period, runs 100 -> 110 in 1-pt steps in the 10:00 period, then sits at 110."""
    rows = [(t_et(9, 35), 100.5), (t_et(9, 50), 100.5)]
    rows += [(t_et(10, 1, 0) + k * NS, 100.5 + k) for k in range(1, 10)]   # bins 101 .. 109, printed once each
    rows += [(t_et(10, 15), 110.5), (t_et(10, 40), 110.5)] + list(extra)
    rows.sort()
    return st.Tape.from_arrays(DAY, [r[0] for r in rows], [r[1] for r in rows])


def run(tape, carried=(), max_width=60.0):
    return tpo.SinglePrints(1.0, 8, max_width).run_session(tape, DAY, list(carried), end_ns=t_et(12, 0))


def live(sp, t):
    return {k: v for k, v in sp.levels.items() if v.created_ns <= t and (v.end_ns is None or v.end_ns > t)}


def test_up_move_forms_one_run_at_the_period_close():
    sp = run(up_move_tape())
    assert live(sp, t_et(10, 29, 59)) == {}
    lv = live(sp, t_et(10, 30))
    assert len(lv) == 1
    (only,) = lv.values()
    # bins 101..110 (the 10:15 trade at 110.5 prints bin 110 in the same period): band [101, 111), the top bin's
    # upper bound (owner 2026-10-31); the 10:40 trade at 110.5 then fills bin 110 -> [101, 110)
    assert (only.lo[0], only.hi[0]) == (101.0, 111.0) and only.created_ns == t_et(10, 30)
    assert only.times[1] == t_et(10, 40) and (only.lo[1], only.hi[1]) == (101.0, 110.0)


def test_a_trade_in_the_middle_splits_into_two_levels_with_their_own_ids():
    sp = run(up_move_tape([(t_et(11, 0), 105.5)]))
    before = live(sp, t_et(10, 59))
    after = live(sp, t_et(11, 0))
    assert len(before) == 1 and len(after) == 2
    (old_id,) = before
    ids = sorted(after, key=lambda k: after[k].lo[-1])
    assert ids[0] == old_id                                      # the lower part keeps the id
    lo_part, hi_part = after[ids[0]], after[ids[1]]
    assert (lo_part.lo[-1], lo_part.hi[-1]) == (101.0, 105.0) and (hi_part.lo[-1], hi_part.hi[-1]) == (106.0, 110.0)


def test_filling_from_the_bottom_keeps_the_id_and_full_fill_ends_it():
    sp = run(up_move_tape([(t_et(11, 0), 101.5), (t_et(11, 5), 102.5)]))
    (lid,) = live(sp, t_et(11, 6))
    assert sp.levels[lid].lo[-1] == 103.0 and len(sp.levels) == 1     # shrank, same id ("survives however short")
    fill_all = [(t_et(11, 10) + k * NS, 100.5 + k) for k in range(1, 10)]
    sp = run(up_move_tape(fill_all))
    assert live(sp, t_et(11, 11)) == {} and sp.carry() == []


def test_bins_printed_in_two_periods_are_not_single():
    sp = run(up_move_tape([(t_et(10, 35), 105.5)]))   # printed again in the 10:30 period, before its close
    # the run formed at 10:30 (bins 101..109); 10:35 fills bin 105 -> two levels; nothing new forms at 11:00
    assert len(live(sp, t_et(11, 0))) == 2


def test_max_zone_width_split_from_the_bottom():
    sp = run(up_move_tape(), max_width=4.0)
    lv = sorted(live(sp, t_et(10, 30)).values(), key=lambda v: v.lo[-1])
    assert [(v.lo[-1], v.hi[-1]) for v in lv] == [(101.0, 105.0), (105.0, 109.0), (109.0, 110.0)]


def test_carry_roll_shift_and_age():
    sp = run(up_move_tape())
    carried = sp.carry()
    assert len(carried) == 1
    r = tpo.Run.from_json(carried[0].to_json())
    r.shift(250.25)                                              # a roll spread not a multiple of the bin width
    nxt = date(2026, 3, 9)
    tape = st.Tape.from_arrays(nxt, [st.et_ns(nxt, time(10, 0))], [351.5])   # 101.25 + 250.25 = 351.5: in bin 0
    sp2 = tpo.SinglePrints(1.0, 8, 60.0).run_session(tape, nxt, [r], end_ns=st.et_ns(nxt, time(11, 0)))
    (lv,) = [v for v in sp2.levels.values() if v.end_ns is None]
    assert lv.lo[-1] == 101.0 + 250.25 + 1 and lv.hi[-1] == 110.0 + 250.25
    assert tpo.age_sessions(DAY, nxt, [DAY, nxt]) == 1


def test_stacked_single_prints_two_sessions():
    sp = run(up_move_tape())
    # an unfilled run from two sessions ago at [112, 120), above everything today trades
    old = tpo.Run("2026-03-04T05-112", date(2026, 3, 4), 0, list(range(112, 120)), [float(b) for b in range(112, 120)],
                  1.0, [False] * 8)
    sp2 = tpo.SinglePrints(1.0, 8, 60.0).run_session(up_move_tape(), DAY, [old], end_ns=t_et(12, 0))
    assert sp2.stacked(108.0, 113.0, t_et(10, 31)) == 2           # today's run and one from two sessions ago
    assert sp2.stacked(108.0, 113.0, t_et(10, 29)) == 1           # today's isn't formed yet
    assert sp2.stacked(114.0, 115.0, t_et(10, 31)) == 1
    assert sp.stacked(200.0, 210.0, t_et(10, 31)) == 0
