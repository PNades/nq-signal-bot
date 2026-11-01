"""F5.3 zone merge, F5.5 identity, the zone book and its VWAP parity (step 11 part 3)."""

from __future__ import annotations

import random
from datetime import date

import numpy as np

from nqbot.features import zones as zn
from nqbot.features.bands import StepBand, TradeBand
from nqbot.features.session_tape import NS, Tape

DAY = date(2026, 3, 20)
T0 = 1_773_000_000 * NS


def g(lid, lo, hi, pct=None):
    return zn.Geom(lid, lo, hi, pct)


def run(levels, prev=(), tol=10.0, maxw=60.0, ids=None):
    return zn.merge(list(levels), list(prev), tol, maxw, ids or zn.IdSource())


def members(zones):
    return sorted(tuple(z.members) for z in zones)


def test_chain_beyond_max_width_splits():
    # spec F5.3: A-B-C chain, A-C would exceed max_zone_width -> {A, B} and {C}
    a, b, c = g("A", 100, 120), g("B", 120, 140), g("C", 140, 170)
    zones, _ = run([a, b, c])
    assert members(zones) == [("A", "B"), ("C",)]


def test_order_independent():
    lv = [g("A", 100, 120), g("B", 118, 140), g("C", 139, 165), g("D", 300, 310), g("E", 305, 312, 0.9),
          g("F", 150, 151), g("G", 90, 95)]
    base = members(run(lv)[0])
    rnd = random.Random(7)
    for _ in range(20):
        sh = lv[:]
        rnd.shuffle(sh)
        assert members(run(sh)[0]) == base


def test_merge_of_two_zones_keeps_older_id():
    ids = zn.IdSource()
    z1, _ = run([g("A", 100, 110)], ids=ids)
    z2, _ = run([g("A", 100, 110), g("B", 200, 210)], prev=z1, ids=ids)
    assert {z.zone_id for z in z2} == {"z1", "z2"}
    # B moves next to A: the zones qualify and merge; the older id survives
    z3, absorbed = run([g("A", 100, 110), g("B", 108, 118)], prev=z2, ids=ids)
    assert [z.zone_id for z in z3] == ["z1"] and absorbed == {"z2": "z1"}
    assert z3[0].members == ["A", "B"]


def test_centres_within_tol_qualify_and_touching_bands_overlap():
    zones, _ = run([g("A", 100, 102), g("B", 110, 112)], tol=10)
    assert members(zones) == [("A", "B")]
    zones, _ = run([g("A", 100, 102), g("B", 102, 104)], tol=0)
    assert members(zones) == [("A", "B")]
    zones, _ = run([g("A", 100, 102), g("B", 102.25, 104)], tol=0)
    assert members(zones) == [("A",), ("B",)]


def test_release_farthest_from_founding_anchors():
    ids = zn.IdSource()
    z1, _ = run([g("A", 100, 110), g("B", 115, 125)], tol=20, ids=ids)
    # C joins (width 100..150 = 50); then C moves so the zone would be 70 wide: C (farthest from the founders A, B)
    # is released and founds its own zone
    z2, _ = run([g("A", 100, 110), g("B", 115, 125), g("C", 130, 150)], prev=z1, tol=20, ids=ids)
    assert members(z2) == [("A", "B", "C")]
    z3, _ = run([g("A", 100, 110), g("B", 115, 125), g("C", 160, 170)], prev=z2, tol=20, ids=ids)
    assert members(z3) == [("A", "B"), ("C",)]
    assert next(z for z in z3 if "A" in z.members).zone_id == "z1"


def test_join_nearest_tie_larger_pct_of_king():
    ids = zn.IdSource()
    z1, _ = run([g("A", 100, 102, 0.3)], ids=ids)
    z1, _ = run([g("A", 100, 102, 0.3), g("B", 120, 122, 0.9)], prev=z1, ids=ids)
    # X is 10 from both centres (tol 10): equal distance -> the zone with the larger % of King (B)
    z2, _ = run([g("A", 100, 102, 0.3), g("B", 120, 122, 0.9), g("X", 110, 112)], prev=z1, tol=10, maxw=15, ids=ids)
    assert ("B", "X") in members(z2) or ("X", "B") in members(z2)


def _tape(prices, step=NS):
    ts = T0 + np.arange(len(prices), dtype=np.int64) * step
    return Tape.from_arrays(DAY, ts, np.asarray(prices, float))


def test_zone_book_vwap_parity_with_every_trade():
    rng = np.random.default_rng(3)
    p = 20_000 + np.cumsum(rng.normal(0, 2.0, 3000))
    tape = _tape(p)
    # a moving line (VWAP-like: a slow average of price) and static levels around its path
    c = np.convolve(p, np.ones(200) / 200, mode="full")[:len(p)]
    c[:200] = np.nan
    mv = TradeBand(tape, c, 4.0, int(tape.ts[0]))
    levels = [zn.BookLevel("V", mv, [(int(tape.ts[0]), int(tape.ts[-1]) + 1)])]
    for k, x in enumerate(np.linspace(p.min(), p.max(), 9)):
        b = StepBand.const(tape, int(tape.ts[0]), x - 4, x + 4)
        levels.append(zn.BookLevel(f"L{k}", b, [(int(tape.ts[0]), int(tape.ts[-1]) + 1)], change_times=b.times))
    mk = lambda: zn.ZoneBook([zn.BookLevel(x.level_id, x.band, x.spans, change_times=x.change_times) for x in levels],
                             tape, lambda t: (10.0, 60.0), end_ns=int(tape.ts[-1]) + 1)
    fast = mk().run()
    ref = mk().run(every_trade=True)
    for t in tape.ts[::7]:
        assert fast.zones_at(int(t)) == ref.zones_at(int(t))
    assert fast.recomputes < ref.candidates


def test_book_follows_absorbed_zone():
    tape = _tape([100.0] * 10)
    a = StepBand.const(tape, int(tape.ts[0]), 100, 110)
    b = StepBand(tape, np.array([tape.ts[0], tape.ts[5]], dtype=np.int64), np.array([200.0, 108.0]),
                 np.array([210.0, 118.0]))
    end = int(tape.ts[-1]) + 1
    book = zn.ZoneBook([zn.BookLevel("A", a, [(int(tape.ts[0]), end)], change_times=a.times),
                        zn.BookLevel("B", b, [(int(tape.ts[0]), end)], change_times=b.times)],
                       tape, lambda t: (10.0, 60.0), end_ns=end).run()
    t5 = int(tape.ts[5])
    assert book.zones_at(t5) == {"z1": ("A", "B")}
    assert book.follow("z2", t5) == "z1" and book.merged_into["z2"][0] == t5
