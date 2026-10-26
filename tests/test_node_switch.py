"""Confirmed King by lead time (owner 2026-10-25, Q6/Q10/Q28/Q29; feature_spec F2.16, F2.5): hand-built boards at W = 60."""

from nqbot.features import node_switch as ns

W, SHARE, GAP = 60.0, 0.25, 10.0


def tracker(w=W):
    return ns.ConfirmedNode(window_sec=w, incumbent_max_share=SHARE, gap_reset_sec=GAP)


def run(lead_at, times, w=W, one_minute=False, present=None):
    """Feed one board per time; `lead_at(t)` = the board's leader. Returns (confirmed per board, switches)."""
    tr, conf, sw = tracker(w), {}, []
    for t in times:
        lead = lead_at(t)
        elig = present(t) if present else {760, 759, 755, 750, lead}
        s = tr.on_board(t, lead, elig, one_minute)
        if s:
            sw.append(s)
        conf[t] = tr.confirmed
    return conf, sw


def test_alternating_every_30s_never_switches_and_gatekeepers_stay():
    conf, sw = run(lambda t: 760 if (t // 30) % 2 == 0 else 759, range(0, 601))
    assert sw == [] and set(conf.values()) == {760}
    values = {755: 1.0, 759: 2.0, 760: 2.0, 745: 0.1}
    gk = {frozenset(k for k, v in ns.node_types(values, {755, 759, 760}, 750.0, conf[t]).items() if v == "gatekeeper")
          for t in range(0, 601)}
    assert gk == {frozenset({755, 759})}


def test_59_to_1_split_switches_once_incumbent_share_is_low_and_it_is_not_leading():
    conf, sw = run(lambda t: 760 if t % 60 == 0 else 759, range(0, 200))
    assert [(s.t, s.old, s.new) for s in sw] == [(61, 760, 759)]  # at 60 760 leads the board itself
    assert conf[60] == 760 and conf[61] == 759


def test_steady_70_30_split_never_switches():
    conf, sw = run(lambda t: 760 if t % 60 < 18 else 759, range(0, 1201))  # incumbent 30% of every minute
    assert sw == [] and conf[1200] == 760


def test_fading_king_two_strikes_swapping():
    # session from 09:59:00 (t = -60), 750 leads until 10:00:00, then 759 / 760 swap every 30 s (759 first)
    lead = lambda t: 750 if t < 0 else (759 if (t // 30) % 2 == 0 else 760)
    conf, sw = run(lead, range(-60, 600))
    # 750's share of the trailing minute falls under 0.25 at 10:00:46: 759 led 30 s, 760 16 s -> 759
    assert [(s.t, s.new) for s in sw] == [(46, 759)] and conf[599] == 759
    # the owner's case with the window starting at 10:00:00 (a 15-s board gap before it restarts the window, 750 kept):
    # at 10:01:00 759 and 760 tie at 30 s each -> the current board's leader 759; it stays 759 while they swap
    times = [t for t in range(-60, 600) if not -15 < t < 0]
    conf, sw = run(lead, times)
    assert [(s.t, s.new) for s in sw] == [(60, 759)] and conf[59] == 750 and conf[599] == 759


def test_no_second_switch_within_a_window_of_a_switch():
    lead = lambda t: 760 if t < 0 else (755 if t < 50 else 750)
    conf, sw = run(lead, range(-60, 200))
    assert [(s.t, s.new) for s in sw] == [(46, 755), (106, 750)]
    assert all(conf[t] == 755 for t in range(46, 106))


def test_clean_new_leader_switches_and_gatekeepers_change_at_that_board():
    lead = lambda t: 760 if t < 0 else 755
    conf, sw = run(lead, range(-60, 120))
    assert [(s.t, s.new) for s in sw] == [(46, 755)]  # 760's share 14/60 < 0.25 at the first board after 10:00:45
    values = {755: 3.0, 758: 1.0, 760: 2.0}
    types = {t: ns.node_types(values, {755, 758, 760}, 750.0, conf[t]) for t in (45, 46)}
    assert types[45] == {755: "gatekeeper", 758: "gatekeeper", 760: "king"}
    assert types[46] == {755: "king", 758: "significant", 760: "significant"}


def test_session_start_confirms_the_first_leader_and_waits_a_window():
    conf, sw = run(lambda t: 760 if t == 0 else 755, range(0, 100))
    assert conf[0] == 760 and conf[59] == 760
    assert [(s.t, s.new) for s in sw] == [(60, 755)]


def test_missing_strike_counts_as_not_leading_and_is_no_target():
    present = lambda t: {760, 755} if t < 0 else {755, 750}  # 760 absent from every board from t = 0
    conf, sw = run(lambda t: 760 if t < 0 else 755, range(-60, 100), present=present)
    assert [(s.t, s.new) for s in sw] == [(46, 755)]


def test_board_gap_restarts_the_window_on_a_1_second_day():
    times = list(range(-100, -14)) + list(range(0, 100))  # 15-s gap -15 .. 0
    conf, sw = run(lambda t: 760 if t < 0 else 755, times)
    assert [(s.t, s.new) for s in sw] == [(60, 755)]  # not 46: the gap restarted the window at 0


def test_one_minute_day_needs_the_full_last_minute_and_the_current_board():
    seq = {0: 760, 60: 755, 120: 760, 180: 755, 240: 755}
    conf, sw = run(seq.get, sorted(seq), one_minute=True)
    assert [(s.t, s.new) for s in sw] == [(240, 755)]
    conf30, sw30 = run(seq.get, sorted(seq), w=30, one_minute=True)  # under 60 behaves as 60
    assert [(s.t, s.new) for s in sw30] == [(240, 755)]
    seq2 = {0: 760, 60: 760, 120: 755, 180: 750}  # 755 led the last minute but 750 leads the current board
    _, sw2 = run(seq2.get, sorted(seq2), one_minute=True)
    assert sw2 == []


def test_king_and_roll_windows_are_read_separately():
    f = {"king_switch_min_sec": 120, "roll_switch_min_sec": 30, "node_switch_incumbent_max_share": 0.25,
         "mapping": {"ratio_gap_reset_sec": 10}}
    king, ceil = ns.from_config(f, "king"), ns.from_config(f, "ceiling")
    assert (king.window_sec, ceil.window_sec) == (120, 30)
    hits = {}
    for name, tr in (("king", king), ("ceiling", ceil)):
        for t in range(-200, 200):
            s = tr.on_board(t, 760 if t < 0 else 755, {760, 755})
            if s:
                hits[name] = s.t
    assert hits == {"king": 91, "ceiling": 23}  # the incumbent's share falls under 0.25 of each window


def test_stairstep_counts_and_signed_shift():
    strikes = [740, 745, 750, 755, 760]
    m = ns.KingMigration()
    assert m.stairstep_count == 0
    for old, new, want in ((740, 750, 1), (750, 760, 2), (760, 745, 1)):
        m.on_switch(ns.Switch(0, old, new), strikes)
        assert m.stairstep_count == want
    assert m.changes_today == 3 and m.last_shift_strikes == -3
