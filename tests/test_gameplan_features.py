"""F8 gameplan features on the feature_spec example (step 11 part 3)."""

from __future__ import annotations

from datetime import date, time

import numpy as np

from nqbot.features import gameplan_features as gpf
from nqbot.features.session_tape import MIN_NS, NS, Tape, bars, et_ns

DAY = date(2026, 9, 29)
SETTINGS = {"gameplan_ny_am_end": "12:00", "months": {k: f"2025-{k + 9:02d}" if k <= 3 else f"2026-{k - 3:02d}"
                                                      for k in range(1, 13)}}


def gp_doc(gates=None, inval=None):
    return {"date": "2026-09-29", "status": "ok", "hvl": [30590, 30590], "hvl0": [30600, 30600], "regime_line": None,
            "zones": [{"id": "E1", "side": "sell", "zone": [30640, 30722],
                       "trigger": {"type": "failed_audit", "nq_price": None,
                                   "es_gates": gates if gates is not None else
                                   [{"direction": "below", "price": [7766, 7766], "as_written": "ES < 7,766"}]},
                       "stop": 30734, "invalidation": inval or [], "targets": []},
                      {"id": "E1 accepted", "side": "buy", "zone": None,
                       "trigger": {"type": "acceptance", "nq_price": [30722, 30722], "es_gates": []},
                       "invalidation": [], "targets": []}],
            "scenario_sets": [{"frame": "ny_am", "scenarios": [{"id": "S-A", "prob": 0.5, "bias": "down"},
                                                               {"id": "S-B", "prob": 0.5, "bias": "up"}]},
                              {"frame": "session", "scenarios": [{"id": "S-A", "prob": 0.4, "bias": "down"},
                                                                 {"id": "S-B", "prob": 0.6, "bias": "neutral"}]}]}


def tapes(nq_points, es_price=7760.0):
    ts, px = zip(*nq_points)
    nq = Tape.from_arrays(DAY, np.array(ts, dtype=np.int64), np.array(px, float))
    es = Tape.from_arrays(DAY, np.array(ts, dtype=np.int64), np.full(len(ts), es_price), symbol="ES")
    return nq, es


def at(h, m, s=0):
    return et_ns(DAY, time(h, m)) + s * NS


def make(doc, nq, es):
    return gpf.Gameplan(doc, DAY, nq, es, SETTINGS, bars(nq, 1))


def test_spec_example_zone_side_gate_scenario_hvl():
    nq, es = tapes([(at(9, 30), 30650.0), (at(10, 15), 30650.0), (at(13, 0), 30620.0)])
    g = make(gp_doc(), nq, es)
    assert [z.gid for z in g.zones] == ["gp_E1", "gp_E1_accepted"] and g.zones[1].lo == g.zones[1].hi == 30722
    x = g.features(at(10, 15), 30650.0, -1, (30640, 30722))
    assert x["gp_in_zone"] and x["gp_zone_side_agrees"] and x["gp_es_gate_ok"] is True
    assert x["gp_scenario_agrees"] and x["gp_scenario_prob_agree"] == 0.5
    y = g.features(at(13, 0), 30620.0, -1, None)
    assert y["gp_scenario_prob_agree"] == 0.4 and y["gp_in_zone"] is False
    assert y["gp_hvl"] == 30.0 and y["gp_hvl0"] == 20.0 and y["gp_above_hvl"] is True


def test_es_gate_wording():
    nq, es = tapes([(at(9, 30), 30650.0)], es_price=7780.0)
    ok = make(gp_doc(gates=[{"direction": "unclear", "price": [7776, 7776], "as_written": "ES holding 7,776"}]), nq, es)
    assert ok.features(at(9, 31), 30650.0, -1, None)["gp_es_gate_ok"] is True
    unclear = make(gp_doc(gates=[{"direction": "unclear", "price": [7740, 7740],
                                  "as_written": "ES stalling under 7,740"}]), nq, es)
    assert unclear.features(at(9, 31), 30650.0, -1, None)["gp_es_gate_ok"] is None


def test_acceptance_invalidation_needs_the_long_bar():
    inval = [{"instrument": "NQ", "direction": "above", "price": 30722, "timeframe": "acceptance",
              "acceptance_minutes": 15}]
    # a 15-minute bar (10:00-10:15) closing at 30725 -> invalidated
    nq, es = tapes([(at(10, 0, 5), 30700.0), (at(10, 14, 50), 30725.0), (at(10, 16), 30700.0)])
    g = make(gp_doc(inval=inval), nq, es)
    assert g.features(at(10, 16), 30700.0, -1, None, own="gp_E1")["gp_invalidated"] is True
    # only a 1-minute close above, the 15-minute bar closes below -> not
    nq, es = tapes([(at(10, 0, 5), 30700.0), (at(10, 3, 50), 30725.0), (at(10, 14, 50), 30700.0), (at(10, 16), 30700.0)])
    g = make(gp_doc(inval=inval), nq, es)
    assert g.features(at(10, 16), 30700.0, -1, None, own="gp_E1")["gp_invalidated"] is False


def test_no_gameplan_and_held_back_months():
    nq, es = tapes([(at(9, 30), 30650.0)])
    assert all(v is None for v in make(None, nq, es).features(at(10, 0), 30650.0, -1, None).values())
    doc = gp_doc()
    assert gpf.usable(doc, date(2026, 9, 29), SETTINGS) is doc          # ledger month 12 (sealed): used
    assert gpf.usable(doc, date(2026, 3, 20), SETTINGS) is None         # month 6: held back until step 20
    assert gpf.usable({**doc, "status": "flagged"}, date(2026, 9, 29), SETTINGS) is None
    assert gpf.usable({**doc, "status": "flagged", "owner_confirmed": {"at": "x"}}, date(2026, 9, 29), SETTINGS) is not None
