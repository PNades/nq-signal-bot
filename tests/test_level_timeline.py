"""Level timelines (step 13a, owner 2026-11-09, SPEC §13 Q-dryrun13 (3); `features/level_timeline.py`)."""

from datetime import date
from types import SimpleNamespace

import numpy as np

from nqbot.features import level_timeline as lt
from nqbot.features.bands import StepBand, TradeBand
from nqbot.features.session_tape import Tape

S = 10**9


def _tape(ts_s, prices):
    return Tape.from_arrays(date(2026, 3, 20), (np.asarray(ts_s, float) * S).astype(np.int64), prices)


def test_step_band_is_stored_exactly():
    tape = _tape([0, 1, 2], [100.0, 101.0, 102.0])
    b = StepBand(tape, np.array([1 * S, 5 * S], np.int64), np.array([96.0, np.nan]), np.array([104.0, np.nan]), 9 * S)
    t, lo, hi, end = lt.band_steps(b)
    assert t.tolist() == [S, 5 * S] and lo[0] == 96.0 and np.isnan(lo[1]) and hi[0] == 104.0 and end == 9 * S


def test_trade_band_sampled_at_each_seconds_last_trade():
    # trades at 0.2, 0.7 (second 0), 1.1, 1.5 (second 1), 3.0 (second 3, the line unchanged): 2 samples, the last
    # trade of each second, and none where the line doesn't change
    tape = _tape([0.2, 0.7, 1.1, 1.5, 3.0], [1, 2, 3, 4, 5])
    center = np.array([np.nan, 100.0, 100.5, 100.25, 100.25])
    b = TradeBand(tape, center, 4.0, int(0.1 * S))
    t, lo, hi, end = lt.band_steps(b)
    assert t.tolist() == [int(0.7 * S), int(1.5 * S)]
    assert lo.tolist() == [96.0, 96.25] and hi.tolist() == [104.0, 104.25] and end is None


def test_trade_band_piece_ends_and_nan_kept():
    tape = _tape([0.5, 1.5, 2.5, 3.5], [1, 2, 3, 4])
    b = TradeBand(tape, np.array([100.0, 101.0, np.nan, np.nan]), 2.0, 0, int(3 * S))   # a piece cut at 3 s
    t, lo, hi, end = lt.band_steps(b)
    assert t.tolist() == [int(0.5 * S), int(1.5 * S), int(2.5 * S)] and np.isnan(lo[2]) and end == 3 * S


def test_level_rows_carry_identity_spans_and_freezes():
    tape = _tape([0, 1], [100.0, 100.0])
    band = StepBand.const(tape, 0, 98.0, 102.0)
    x = SimpleNamespace(uid="qqq_node-2026-03-20-600", level_id="qqq_node-2026-03-20-600", sub_type="qqq_node",
                        key="qqq_node", rtype="gex_node", instrument="NQ", kind="node", chain="QQQ", strike=600.0,
                        band=band, spans=[(0, 5 * S)], state=SimpleNamespace(freezes=[(2 * S, 3 * S)]))
    (r,) = lt.level_rows([x])
    assert r["band_kind"] == "step" and r["strike"] == 600.0 and r["chain"] == "QQQ"
    assert r["spans"] == [[0, 5 * S]] and r["freezes"] == [[2 * S, 3 * S]] and r["lo"] == [98.0]
    assert lt.level_rows([x], lambda y: y.rtype != "gex_node") == []
