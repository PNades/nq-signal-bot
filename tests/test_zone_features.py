"""F5.13 zone makeup, F5.12 day memory, F5.16 `es_at_level` on merged ES zones (step 11 part 3)."""

from __future__ import annotations

from datetime import date, time
from types import SimpleNamespace

import numpy as np

from nqbot.features import level_state as ls
from nqbot.features import registry as rg
from nqbot.features import structure_label as slab
from nqbot.features import touches as tch
from nqbot.features.bands import StepBand
from nqbot.features.node_levels import _track
from nqbot.features.session_features import SessionFeatures
from nqbot.features.session_tape import MIN_NS, NS, Tape, bars, et_ns
from nqbot.features.zones import ZoneBook

DAY = date(2026, 3, 20)
P = ls.Params(rearm_dist=10, flip_margin=0, flip_bar_minutes=5, sweep_max_depth=15, sweep_reclaim_bars=3)


def at(h, m, s=0):
    return et_ns(DAY, time(h, m)) + s * NS


def tape_of(points, symbol="NQ"):
    ts, px = zip(*points)
    return Tape.from_arrays(DAY, np.array(ts, dtype=np.int64), np.array(px, float), symbol=symbol)


def level(tape, uid, sub_type, rtype, lo, hi, role=ls.CEILING, kind=ls.CHART):
    band = StepBand.const(tape, int(tape.ts[0]), lo, hi)
    st = ls.LevelState(uid, kind, tape, band, P, bars(tape, 1), bars(tape, 5), initial_role=role,
                       end_ns=int(tape.ts[-1]) + MIN_NS).run()
    return rg.RegLevel(uid, uid, sub_type, "x", rtype, tape.symbol, kind, False, band, st, _track(st),
                       spans=rg.active_spans(st, band, int(tape.ts[-1]) + MIN_NS))


def test_makeup_king_and_weekly_poc():
    # F5.13: a zone with a QQQ King and the weekly POC -> stack_count 2, king_present, the one-hot counts
    tape = tape_of([(at(10, 0), 100.0), (at(10, 1), 100.0)])
    king = level(tape, "qqq_node-x-600", "qqq_node", rg.GEX_NODE, 95, 105, kind=ls.NODE)
    poc = level(tape, "poc_weekly-w", "poc_weekly", "volume_profile", 98, 102)
    info = {"qqq_node-x-600": {"pct_of_king": 1.0, "node_type": "king", "node_sign": 1, "rug": False,
                               "reverse_rug": False, "in_pika_cloud_member": False, "velocity": 0.2,
                               "minutes_since_relevant": 30.0}}
    fake = SimpleNamespace(_node_info=lambda t: info, _sp_stacked=lambda m, t: 0,
                           c=SimpleNamespace(sky=object(),
                                             cs_nq=SimpleNamespace(atr=100.0, em=SimpleNamespace(points=200.0))))
    out = SessionFeatures._makeup(fake, [king, poc], at(10, 0, 30))
    assert out["stack_count"] == 2 and out["king_present"] is True and out["gatekeeper_present"] is False
    assert out["n_qqq_node"] == 1 and out["n_poc_weekly"] == 1 and out["n_poc_daily"] == 0
    assert out["largest_pct_of_king"] == 1.0 and out["n_node_king"] == 1 and out["n_post_cross"] == 0


def test_day_memory_counts_resolved_labels():
    L = lambda lab, t: slab.Label("e", lab, t, 0.0, 0.0, lab)
    fake = SimpleNamespace(c=SimpleNamespace(yesterday_day_type="trend"))
    out = SessionFeatures._day_memory(fake, at(11, 0), [L("held", 1), L("held", 2), L("broke", 3), L("timeout", 4)])
    assert (out["held_today"], out["broke_today"], out["timeout_today"]) == (2, 1, 1)
    assert out["held_rate_today"] == 0.5 and out["yesterday_day_type"] == "trend"


def es_case(es_points, nq_points):
    """An NQ ceiling event at [200, 202] touched at 10:00:00; ES PDH [100, 101] and ES ON high [101.5, 102.5] (both
    ceilings) merge into one ES zone (tol 10 x 0.5 = 5); es_zone_proximity 0.5."""
    es = tape_of(es_points, "ES")
    nq = tape_of(nq_points)
    pdh = level(es, "es_pdh-d", "pdh", "session_hl", 100, 101)
    onh = level(es, "es_on_high-d", "on_high", "session_hl", 101.5, 102.5)
    end = int(es.ts[-1]) + MIN_NS
    book = ZoneBook([pdh.book_level(), onh.book_level()], es, lambda t: (5.0, 30.0), end_ns=end).run()
    assert len(book.zones_at(at(10, 0))) == 1 and len(next(iter(book.zones_at(at(10, 0)).values()))) == 2
    taus = lambda t0, t: np.unique(np.concatenate([[t0], es.ts[(es.ts > t0) & (es.ts <= t)],
                                                   nq.ts[(nq.ts > t0) & (nq.ts <= t)]]))
    sky = SimpleNamespace(check_times=taus, blocked_in=lambda m, a, b: False,
                          ctx=SimpleNamespace(es_prox=lambda tau: 0.5))
    fake = SimpleNamespace(es_book=book, es_levels={"es_pdh-d": pdh, "es_on_high-d": onh}, nq=nq, es=es,
                           f={"zone_proximity": 5.0}, c=SimpleNamespace(sky=sky), zone_edges=lambda e, tau: (200.0, 202.0))
    # the NQ side: NQ inside the event's zone [200, 202] +- zone_proximity at each tau
    fake._nq_inside_many = lambda e_, ts: np.array([(p := nq.last_price_at(int(x))) is not None and 195.0 <= p <= 207.0
                                                    for x in ts], dtype=bool)
    e = tch.Touch("z1", at(10, 0), 0, 199.0, -1, ("x",), 200.0, 202.0)
    return fake, e


def test_es_at_level_merged_zone_later_overlap():
    # ES enters the merged zone 10 s after the NQ touch with NQ still in its zone: false at the touch, true later
    es_points = [(at(9, 50), 80.0), (at(10, 0), 95.0), (at(10, 0, 10), 100.25), (at(10, 0, 30), 100.5)]
    nq_points = [(at(9, 50), 180.0), (at(10, 0), 199.0), (at(10, 0, 20), 200.5)]
    fake, e = es_case(es_points, nq_points)
    a = SessionFeatures._es_at_level(fake, e, e.t)
    assert a["es_at_level"] is False and a["es_stack_count"] == 0
    b = SessionFeatures._es_at_level(fake, e, at(10, 0, 30))
    assert b["es_at_level"] is True and b["es_stack_count"] == 2 and b["es_member_types"] == "on_high,pdh"


def test_es_at_level_after_nq_left_does_not_count():
    # ES reaches the ES zone only after NQ has left its zone -> false
    es_points = [(at(9, 50), 80.0), (at(10, 0), 95.0), (at(10, 0, 20), 100.25)]
    nq_points = [(at(9, 50), 180.0), (at(10, 0), 199.0), (at(10, 0, 10), 190.0)]
    fake, e = es_case(es_points, nq_points)
    assert SessionFeatures._es_at_level(fake, e, at(10, 0, 30))["es_at_level"] is False
