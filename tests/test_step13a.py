"""Step 13a reaction-distance measurement (feature_spec F5.14; `checks/step13a.py`): hand-built approaches."""

import json
from types import SimpleNamespace

import numpy as np
import pyarrow as pa
import pytest

from nqbot.checks import step13a as r

S = 10**9
CAP, PROX, REARM, REACT = 12.0, 5.0, 10.0, 10.0
REACT_NS = 5 * 60 * S


def _run(path, line=100.0, top=None, bot=None, sweep=None, cap=CAP):
    """path: [(seconds, price)] -> measure() on a constant line (or run edges top/bot)."""
    ts = np.array([int(t * S) for t, _ in path], np.int64)
    px = np.array([p for _, p in path], float)
    n = len(ts)
    top_a = np.full(n, line if top is None else top)
    bot_a = np.full(n, line if bot is None else bot)
    sw = None if sweep is None else {"depth": np.full(n, sweep[0]), "bars": sweep[1]}
    return r.measure(ts, px, top_a, bot_a, np.full(n, cap), np.full(n, PROX), np.full(n, REARM), np.full(n, REACT),
                     REACT_NS, sw)


def _from_above(t0, pen, react=True):
    """Armed at 130, down to 100 - pen, then back up 10 (or not within 5 minutes), then away to 130."""
    low = 100 - pen
    out = [(t0, 130.0), (t0 + 10, 115.0), (t0 + 20, low)]
    if react:
        out += [(t0 + 30, low + REACT), (t0 + 40, 130.0)]
    else:
        out += [(t0 + 30, low + 5), (t0 + 400, low + 5), (t0 + 410, 130.0)]
    return out


def test_f5_14_example_quantile_over_the_reacted_ones():
    path = []
    for k, pen in enumerate((0, 1, 2, 3)):
        path += _from_above(1000 * k, pen)
    path += _from_above(4000, 10, react=False)
    res = _run(path)
    assert len(res) == 5 and [x["reacted"] for x in res] == [True] * 4 + [False]
    dists = np.array([x["dist"] for x in res if x["reacted"]])
    assert dists.tolist() == [0.0, 1.0, 2.0, 3.0]
    assert r.width_of(dists, 0.8, CAP) == pytest.approx(round(np.quantile([0, 1, 2, 3], 0.8) / 0.25) * 0.25)


def test_no_approach_without_a_re_arm_and_sides():
    # starts near the line (never armed): nothing; then below it, armed at 73, approaches from below
    path = [(0, 101.0), (10, 95.0), (20, 103.0), (30, 73.0), (40, 90.0), (50, 102.0), (60, 92.0), (70, 70.0)]
    res = _run(path)
    assert len(res) == 1 and res[0]["side"] == -1 and res[0]["reacted"] and res[0]["dist"] == 2.0
    assert res[0]["i0"] == 4


def test_cap_clips_the_width():
    assert r.width_of(np.array([20.0, 30.0, 40.0]), 0.8, CAP) == CAP


def test_single_print_from_its_near_edge():
    # an unfilled run [100, 104]: from above the near edge is 104; price reaches 102 then reacts -> 2 past it
    path = [(0, 135.0), (10, 120.0), (20, 102.0), (30, 112.0), (40, 135.0)]
    res = _run(path, top=104.0, bot=100.0)
    assert res[0]["reacted"] and res[0]["dist"] == 2.0


def test_missing_line_splits_the_measurement():
    ts = np.array([0, 10, 20, 30, 40], np.int64) * S
    px = np.array([130.0, 130.0, 100.0, 110.0, 130.0])
    line = np.array([100.0, np.nan, 100.0, 100.0, 100.0])   # the arm at t=0 is in another stretch
    res = r.measure(ts, px, line, line, np.full(5, CAP), np.full(5, PROX), np.full(5, REARM), np.full(5, REACT),
                    REACT_NS)
    assert res == []


def _high(path_after, sweep=(15.0, 3)):
    # a high at 100 approached from below: armed at 70, then the path
    return _run([(0, 70.0), (10, 90.0)] + path_after, sweep=sweep)


def test_high_swept_counts_with_its_depth():
    # 5 past at 60 s (bar 1), that bar closes at 99 (back below the line): swept; reaction 10 down from 105
    res = _high([(60, 105.0), (119, 99.0), (130, 95.0), (200, 60.0)])
    assert res[0]["reacted"] and res[0]["excluded"] is None and res[0]["dist"] == 5.0


def test_high_break_by_the_window_is_excluded():
    # 5 past at 60 s, 12 past at 179 s, back to 2 past at 200 s (the 10-pt reaction, still past the line); the break
    # bar (minute 1) and bars 1-3 after it (to 299 s) all close above 100 -> a break at the window's end
    res = _high([(60, 105.0), (119, 104.0), (179, 112.0), (200, 102.0), (239, 104.0), (299, 103.0), (310, 94.0),
                 (400, 60.0)])
    assert not res[0]["reacted"] and res[0]["excluded"] == "sweep_break"


def test_high_too_deep_is_excluded():
    res = _high([(60, 116.0), (70, 104.0), (119, 99.0), (200, 60.0)])
    assert not res[0]["reacted"] and res[0]["excluded"] == "sweep_break"


def test_ndxp_choice_rule():
    assert r.ndxp_choice(8.0) == (10, False)
    assert r.ndxp_choice(12.5) == (15, False)
    assert r.ndxp_choice(20.0) == (20, False)
    assert r.ndxp_choice(23.0) == (20, True)
    assert r.ndxp_choice(None) == (None, False)


# --- the RTH flag and the report (owner 2026-11-10, SPEC §13 Q-step13a) ------------------------------------------

def test_rth_flag_over_20_percent_or_another_width():
    assert r.rth_flag(4.0, 4.5, 4.0, 4.5, CAP) is not None           # another width
    assert r.rth_flag(4.0, 4.2, 4.0, 4.0, CAP) is None                # +5%, same width
    assert r.rth_flag(4.0, 4.9, 4.0, 4.0, CAP) is not None            # +22.5% (widths made equal here on purpose)
    assert r.rth_flag(15.0, 25.0, CAP, CAP, CAP) is None              # both beyond the cap: both freeze to it
    assert r.rth_flag(0.0, 0.5, 0.0, 0.5, CAP) is not None            # from 0
    assert r.rth_flag(3.0, None, 3.0, None, CAP) == "no RTH reactions"
    assert r.rth_flag(None, None, None, None, CAP) is None


def _table(rows):
    cols = {"day": pa.string(), "uid": pa.string(), "level_id": pa.string(), "sub_type": pa.string(),
            "group": pa.string(), "instrument": pa.string(), "side": pa.int8(), "t0": pa.int64(), "rth": pa.bool_(),
            "reacted": pa.bool_(), "excluded": pa.string(), "dist_pts": pa.float64(), "cap_pts": pa.float64(),
            "underlying_per_pt": pa.float64(), "em_pts": pa.float64()}
    full = [{"day": "2025-10-07", "uid": "u", "level_id": "u", "sub_type": x["group"], "instrument": "NQ", "side": 1,
             "t0": 0, "excluded": None, "cap_pts": 12.0, "underlying_per_pt": None, "em_pts": 250.0, **x} for x in rows]
    return pa.Table.from_pylist(full, schema=pa.schema(list(cols.items())))


def test_chart_row_flags_an_rth_difference():
    # all hours 0..9 (q0.8 = 7.2 -> 7.25); RTH only the small ones 0..4 (q0.8 = 3.2 -> 3.25): flagged
    rows = [{"group": "poc", "reacted": True, "dist_pts": float(d), "rth": d < 5} for d in range(10)]
    rows.append({"group": "poc", "reacted": False, "dist_pts": None, "rth": True})
    s = r.chart_row(_table(rows), "poc", 0.8, CAP)
    assert s["proposed"] == 7.25 and s["proposed_rth"] == 3.25 and s["rth_flag"].startswith("RTH width")
    assert s["approaches"] == 11 and s["reacted"] == 10
    same = [{"group": "poc", "reacted": True, "dist_pts": float(d), "rth": True} for d in range(10)]
    assert r.chart_row(_table(same), "poc", 0.8, CAP)["rth_flag"] is None


def test_ndxp_row_in_ndxp_dollars_rth_beside():
    # 1 NQ pt = 1 NDXP $ here; all hours q0.8 of 0..19 = 15.2 -> ±20; RTH only 0..9 -> 7.2 -> ±10: flagged
    rows = [{"group": "ndxp_node", "reacted": True, "dist_pts": float(d), "rth": d < 10, "underlying_per_pt": 1.0}
            for d in range(20)]
    nd = r.ndxp_row(r.summarize(_table(rows), "ndxp_node", 0.8), 0.8)
    assert nd["pick"] == 20 and nd["pick_rth"] == 10 and nd["rth_flag"] is not None


def test_report_writes_widths_flags_and_charts(tmp_path, monkeypatch):
    from nqbot.config import load_features

    f = load_features()
    rows = []
    for k in f["chart_level_half_width"]:
        rows += [{"group": k, "reacted": True, "dist_pts": float(d % 6), "rth": True} for d in range(30)]
    rows += [{"group": "em_level", "reacted": True, "dist_pts": 11.0, "rth": False} for _ in range(30)]
    for g in ("qqq_node", "ndxp_node", "spy_node", "spx_node"):
        rows += [{"group": g, "reacted": True, "dist_pts": float(d), "rth": True, "underlying_per_pt": 0.5,
                  "instrument": "ES" if g in ("spy_node", "spx_node") else "NQ"} for d in range(25)]
    t = _table(rows)
    env = SimpleNamespace(features=f, settings={}, paths=SimpleNamespace(reports_dir=tmp_path), echo=lambda *_: None)
    monkeypatch.setattr(r, "build_doc", lambda e: {"key": "k1", "months": [1, 2, 3, 4], "sessions": ["2025-10-07"]})
    monkeypatch.setattr(r.months_mod, "month_index", lambda d, s: 1)
    monkeypatch.setattr(r, "load_days", lambda *a: t)
    path = r.report(env)
    md = path.read_text(encoding="utf-8")
    prop = json.loads((tmp_path / "step13" / "proposed_widths.json").read_text(encoding="utf-8"))
    assert (tmp_path / "step13" / "reactions_widths.png").exists() and (tmp_path / "step13" / "reactions_nodes.png").exists()
    assert prop["chart_level_half_width"]["poc"] == 4.0          # q0.8 of 0..5 repeated = 4.0
    assert "em_level" in prop["rth_flags"] and "poc" not in prop["rth_flags"]
    assert prop["ndxp_node_band"] == 10                          # q0.8 of 0..24 = 19.2 pts x 0.5 = 9.6 NDXP $
    assert "RTH: " in md and "Q-step13a" in md
