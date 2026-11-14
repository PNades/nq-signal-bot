"""Owner 2026-11-13 (SPEC §13 Q-step13-review): the review's fixes that change a key. Labels key L1-L5, build key B1,
B3, B4, B5; each with a hand-built case."""

from __future__ import annotations

import ast
import copy
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from nqbot.checks import feature_build as fb
from nqbot.clients import mbp1
from nqbot.config import load_features, load_settings
from nqbot.events.build import damage_drop
from nqbot.events.triggers import rearm_time
from nqbot.features import damage
from nqbot.features.session_tape import ET, NS
from nqbot.labeler import candidates as cand
from nqbot.labeler import fills as fl
from nqbot.labeler import params as lp
from nqbot.labeler.outcomes import timeout_at

DAY = date(2025, 10, 8)


def at(h, m, s=0):
    return int(datetime(DAY.year, DAY.month, DAY.day, h, m, s, tzinfo=ET).timestamp()) * NS


# --- L1: a decision after flat-by times out at the session end -------------------------------------------------------

def test_a_fill_after_flat_by_times_out_at_the_session_end():
    meta = {"flat_ns": at(15, 59), "end_ns": at(17, 0), "blackouts": []}
    lat = int(0.3 * NS)
    assert timeout_at(at(10, 0), meta, lat) == (at(15, 59) + lat, "flat_by")
    t_x, why = timeout_at(at(16, 2), meta, lat)
    assert (t_x, why) == (at(17, 0) + lat, "session_end") and t_x > at(16, 2)       # never before its fill
    meta["blackouts"] = [[at(16, 30), at(17, 30)]]                                    # a news flatten before the end
    assert timeout_at(at(16, 2), meta, lat) == (at(16, 30) + lat, "news_flatten")


# --- L2: no book past the file's covered end -------------------------------------------------------------------------

def test_the_book_is_not_in_force_after_the_file_ends():
    b = mbp1.TopOfBook.from_arrays([at(9, 25), at(16, 4, 59)], [100.0, 101.0], [100.25, 101.25], [1, 2], [3, 4])
    assert b.at(at(16, 4, 59)) == (101.0, 101.25, 2.0, 4.0)
    assert b.at(at(16, 4, 59) + mbp1.BOOK_END_TOL_NS) is not None          # within the tolerance
    assert b.at(at(16, 5, 11)) is None                                       # the stale 16:04:59 book is not read
    assert b.at(at(9, 0)) is None
    assert mbp1.TopOfBook.from_arrays([], [], [], [], []).at(at(10, 0)) is None


def test_after_the_book_a_marketable_limit_is_no_book_and_a_stop_limit_fills_at_its_trade():
    book = mbp1.TopOfBook.from_arrays([at(16, 4, 59)], [25331.75], [25332.0], [5], [5])
    p = SimpleNamespace(costs=SimpleNamespace(max_entry_slip_ticks=4))
    ref, _ = fl.reference("marketable_limit", {"direction": 1, "decision_ts": at(16, 5, 30)}, book, p)
    assert ref is None                                                       # -> no_book
    px, nb = fl._book_fill(book, at(16, 5, 11), +1, 1.0, 25334.75, 25333.75)
    assert (px, nb) == (25333.75, True)                                      # its own trigger trade, not 25332.00


# --- L3: a stop-limit never fills better than the trade that triggered it ----------------------------------------------

def test_a_stop_limit_never_fills_better_than_its_trigger_trade():
    # sell stop at 24,789.25 triggered by a 24,789.25 print; the book at that timestamp (after the up-sweep) bids 24,789.75
    book = mbp1.TopOfBook.from_arrays([at(11, 43, 50)], [24789.75], [24790.0], [3], [3])
    px, nb = fl._book_fill(book, at(11, 43, 50), -1, 1.0, 24788.25, 24789.25)
    assert (px, nb) == (24789.25, False)
    # a worse book than the trade: the book (never better than either)
    px, _ = fl._book_fill(book, at(11, 43, 50), +1, 1.0, 24791.0, 24789.5)
    assert px == 24790.0
    # a marketable limit (no trigger trade) is unchanged
    px, _ = fl._book_fill(book, at(11, 43, 50), +1, 1.0, 24791.0, None)
    assert px == 24790.0
    # never worse than the limit
    px, _ = fl._book_fill(book, at(11, 43, 50), +1, 1.0, 24790.5, 24792.0)
    assert px == 24790.5


# --- L4: the RR bands follow pair_build_min_rr ------------------------------------------------------------------------

def test_rr_bands_follow_the_build_floor():
    assert [cand.rr_band(x, 0.8) for x in (0.3, 0.5, 0.79, 0.8, 2.0)] == ["<0.5", "0.5-0.8", "0.5-0.8", ">=0.8", ">=0.8"]
    assert [cand.rr_band(x, 1.0) for x in (0.4, 0.9, 1.0)] == ["<0.5", "0.5-1", ">=1"]
    assert cand.RR_BAND_LOW == 0.5


# --- L5: a time-to-live longer than the band track is refused ----------------------------------------------------------

def test_a_ttl_longer_than_the_band_track_is_refused():
    from nqbot.events.snapshot import TRACK_HORIZON_SEC

    s, f = load_settings(), load_features()
    lp.params(s, f, {"entry_ttl_sec.c": TRACK_HORIZON_SEC})
    with pytest.raises(ValueError, match="band track"):
        lp.params(s, f, {"entry_ttl_sec.c": TRACK_HORIZON_SEC + 1})


# --- B1: a later merge never cuts an audit --------------------------------------------------------------------------

def test_rearm_counts_the_absorbing_zones_arms_only_from_the_merge():
    e = SimpleNamespace(zone_id="z2", kind="level", t=100 * NS)
    sf = SimpleNamespace(book=SimpleNamespace(merged_into={"z2": (5000 * NS, "z1")}),
                         arms={"z1": [200 * NS, 6000 * NS], "z2": []})
    assert rearm_time(sf, e) == 6000 * NS            # z1's arm at 200 s is before the merge: another zone's
    sf.arms["z2"] = [3000 * NS]
    assert rearm_time(sf, e) == 3000 * NS            # its own zone's re-arm first
    # a chain: z2 -> z1 at 5,000 s -> z0 at 7,000 s
    sf = SimpleNamespace(book=SimpleNamespace(merged_into={"z2": (5000 * NS, "z1"), "z1": (7000 * NS, "z0")}),
                         arms={"z0": [6500 * NS, 7500 * NS], "z1": [], "z2": []})
    assert rearm_time(sf, e) == 7500 * NS
    golden = SimpleNamespace(zone_id="gz", kind="golden", t=100 * NS)
    assert rearm_time(SimpleNamespace(book=SimpleNamespace(merged_into={"gz": (150 * NS, "z1")}),
                                      arms={"z1": [200 * NS], "gz": [900 * NS]}), golden) == 900 * NS


# --- B2: an event's own outcome readings use the world its zone edges come from --------------------------------------

def _fake_session(self_cross, ot, alt, calls):
    fake = SimpleNamespace(exclude_source=None, cross=lambda e, t: self_cross, own_time=lambda e: ot)
    fake.world = lambda e, t, need=None, horizon=None: (calls.append((t, need, horizon)) or (alt, e))
    return fake


def test_the_own_cross_reads_the_world_without_the_groups_retested_extreme(monkeypatch):
    from nqbot.events import snapshot as snap
    from nqbot.features.session_features import SessionFeatures as SF

    monkeypatch.setattr(snap, "group_horizon", lambda sf, e: 10**12)
    lvl, calls = SimpleNamespace(kind="level"), []
    alt = SimpleNamespace(x=None, cross=lambda e, t: alt.x)
    assert SF.own_cross(_fake_session((50, "broke"), None, alt, calls), lvl, 100) == (50, "broke")   # no extreme
    assert SF.own_cross(_fake_session((50, "broke"), 60, alt, calls), lvl, 100) == (50, "broke")     # cross before it
    assert SF.own_cross(_fake_session(None, 160, alt, calls), lvl, 100) is None                      # window ends first
    assert not calls                                                                                 # no other world
    assert SF.own_cross(_fake_session((70, "broke"), 60, alt, calls), lvl, 100) is None              # its extreme's
    assert calls == [(60, 100, 10**12)]                                                              # from the extreme
    alt.x = (80, "ended")
    assert SF.own_cross(_fake_session(None, 60, alt, calls), lvl, 100) == (80, "ended")
    alt.x = (60, "broke")
    assert SF.own_cross(_fake_session((60, "broke"), 60, alt, calls), lvl, 100) == (60, "broke")     # at the extreme
    del calls[:]
    for kind in ("golden", "gameplan"):                                                             # never contain one
        assert SF.own_cross(_fake_session((70, "broke"), 60, alt, calls), SimpleNamespace(kind=kind), 100) == \
            (70, "broke")
    assert not calls


def test_the_own_outcome_relabels_on_the_own_cross_and_keeps_the_built_labels(monkeypatch):
    from nqbot.features import structure_label as slab
    from nqbot.features.session_features import SessionFeatures as SF

    monkeypatch.setattr(slab, "label", lambda e, tape, x, t_end, d: ("label", x, t_end, d))
    built = {"e1": "built30", "e2": "built30"}, {"e1": "built20", "e2": "built20"}
    fake = SimpleNamespace(labels=dict(built[0]), pair_end=dict(built[1]), _own_out={}, exclude_source=None,
                           f={"hold_window_min": 30, "hold_reaction_pts": 30, "pair_end_hold_pts": 20},
                           flat_ns=at(15, 59), end_ns=at(17, 0), nq=None,
                           own_time=lambda e: at(10, 4) if e.event_id == "e1" else None,
                           own_cross=lambda e, t_end: (at(10, 6), "broke"))
    e1 = SimpleNamespace(event_id="e1", kind="level", t=at(10, 0))
    e2 = SimpleNamespace(event_id="e2", kind="level", t=at(10, 0))
    lab, pend = SF.own_outcome(fake, e1)
    assert lab == ("label", (at(10, 6), "broke"), at(10, 30), 30.0) and pend[3] == 20.0
    assert SF.own_outcome(fake, e2) == ("built30", "built20")                       # no own extreme: as built
    assert (fake.labels, fake.pair_end) == built                                    # the built dicts never change
    fake.own_time = lambda e: at(10, 31)                                            # made after the window: as built
    fake._own_out.clear()
    assert SF.own_outcome(fake, e1) == ("built30", "built20")


# --- B3: excess empty minutes damage the whole RTH part ----------------------------------------------------------------

def _row(excess, gaps=()):
    return {"cme_day": DAY.isoformat(), "part": "RTH", "span": "10-08 09:30–10-08 16:00 ET", "series": "NQ trades",
            "empty_minutes": 10 + excess, "baseline_empty_max": 10, "baseline_gap_max_sec": 5.0, "gaps": list(gaps)}


def test_excess_only_rth_damage_drops_the_rth_events():
    s = load_settings()
    over = s["databento_damage_excess_minutes"] + 2
    dd = damage.classify([_row(over)], s)[DAY]
    assert dd.rth_gap_minutes == [] and dd.rth_unlocated == [
        (datetime(2025, 10, 8, 9, 30, tzinfo=ET), datetime(2025, 10, 8, 16, 0, tzinfo=ET))]
    sf = SimpleNamespace(f={"hold_window_min": 30}, flat_ns=at(15, 59), end_ns=at(17, 0))
    assert damage_drop(sf, SimpleNamespace(t=at(11, 0)), dd, 15)            # inside RTH: dropped
    assert damage_drop(sf, SimpleNamespace(t=at(9, 10)), dd, 15)            # its window reaches into RTH + pad
    assert not damage_drop(sf, SimpleNamespace(t=at(4, 0)), dd, 15)         # overnight, far from RTH
    clean = damage.classify([_row(0)], s).get(DAY)                         # no damage: nothing to drop
    assert clean is None or (clean.rth_unlocated == [] and clean.rth_minutes == 0)


def test_gap_only_damage_keeps_the_gap_padding():
    s = load_settings()
    gap = {"from": "11:00:00", "sec": max(600.0, float(s["databento_damage_gap_sec"]) + 60)}
    dd = damage.classify([_row(0, [gap])], s)[DAY]
    assert dd.rth_unlocated == [] and len(dd.rth_gap_minutes) == 1


# --- B4: the labeler's settings are outside the build key ----------------------------------------------------------------

def test_labeler_only_settings_leave_the_build_key_alone(paths, settings, log):
    from nqbot import calendar as cal_mod
    from nqbot.checks.step8b import Env

    feats = load_features()
    base = Env(settings=settings, features=feats, paths=paths, log=log, cal=cal_mod.load(), echo=lambda *_: None)
    k0 = fb.build_key(base)
    for key, val in (("order_latency_sec", 0.9), ("max_target_candidates", 3), ("pair_build_min_rr", 1.2)):
        s = copy.deepcopy(settings)
        s[key] = val
        assert fb.build_key(Env(settings=s, features=feats, paths=paths, log=log, cal=base.cal,
                                echo=lambda *_: None)) == k0, key
    f2 = copy.deepcopy(feats)
    f2["stop_merge_ticks"] = float(f2["stop_merge_ticks"]) + 2
    assert fb.build_key(Env(settings=settings, features=f2, paths=paths, log=log, cal=base.cal,
                            echo=lambda *_: None)) == k0
    s = copy.deepcopy(settings)
    s["order_latency_sec"] = 0.9                                            # the labels key does follow them
    assert lp.label_key("b", lp.params(s, feats)) != lp.label_key("b", lp.params(settings, feats))


# --- B5: every direct import of the build-key code is covered or exempt with a reason ------------------------------------

def test_every_direct_import_of_build_key_code_is_covered_or_exempt():
    src = Path(fb.__file__).resolve().parents[1]
    covered = {Path(f).resolve() for f in fb.code_files()}

    def mod_file(name):
        parts = name.split(".")
        if parts[0] != "nqbot" or len(parts) < 2:
            return None
        rel = Path(*parts[1:])
        for c in (src / rel.with_suffix(".py"), src / rel / "__init__.py"):
            if c.exists():
                return c.resolve()
        return None

    missing = {}
    for f in sorted(covered):
        for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            names = ([a.name for a in n.names] if isinstance(n, ast.Import) else
                     [n.module] + [f"{n.module}.{a.name}" for a in n.names]
                     if isinstance(n, ast.ImportFrom) and n.module else [])
            for nm in names:
                m = mod_file(nm)
                if m is None or m in covered:
                    continue
                rel = m.relative_to(src).as_posix()
                if rel not in fb.KEY_EXEMPT:
                    missing.setdefault(rel, f.relative_to(src).as_posix())
    assert not missing, f"imported by build-key code, neither in CODE_PARTS nor KEY_EXEMPT: {missing}"
    for part in ("checks/step8b.py", "clients/ndx_sources.py", "clients/skylit_multiexp.py", "warmup.py",
                 "clients/databento_contracts.py", "gameplan/overrides.py", "config.py"):
        assert part in fb.CODE_PARTS
    assert all(reason for reason in fb.KEY_EXEMPT.values())


# --- the labeler's input selection is inside the labels key (owner 2026-11-13) ------------------------------------------

def test_the_labelers_input_selection_is_in_the_labels_key(monkeypatch):
    from nqbot.checks import step13b as s13
    from nqbot.labeler import session as lsess

    assert Path(lsess.__file__).resolve().parent == Path(lp.__file__).resolve().parent     # labeler/*.py: code_digest
    assert "session.py" in {f.name for f in Path(lp.__file__).resolve().parent.glob("*.py")}
    seen = []
    monkeypatch.setattr(lsess, "label_built", lambda db, plan, base, day, p, identity=True:
                        seen.append((db, plan, day, p, identity)) or {"ok": 1})
    monkeypatch.setattr(s13.sb, "plan", lambda env: "plan")
    env = SimpleNamespace(db="db")
    assert s13.label_one(env, Path("."), DAY, "p") == {"ok": 1} and seen == [("db", "plan", DAY, "p", True)]

    def wrong(*_a, **_k):
        raise lsess.WrongInputs("the stored NQ trades (NQH6) are not the build's (NQZ5)")
    monkeypatch.setattr(lsess, "label_built", wrong)
    with pytest.raises(s13.Refused, match="not the build's"):
        s13.label_one(env, Path("."), DAY, "p")
