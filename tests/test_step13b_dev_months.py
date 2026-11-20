"""Owner 2026-11-20 (pre-13d): `step13 counts` and `labels-plan` read a build's development months only (months 1-6,
§7 rule 2b), never months 7-12, even on the 13d full-window build; on a months 1-4 build (13c) they stay months 1-4.
Synthetic builds only: every month's files are written, so a leak would show."""

from __future__ import annotations

from datetime import date

import pytest

from nqbot import months as months_mod
from nqbot.checks import step13b as s13
from nqbot.events.common import write_table
from nqbot.storage import atomic_write_json
from tests.test_feature_build import LEDGER, env  # noqa: F401  (fixture)

# two sessions in each ledger month (month 1 = 2025-10 ... month 12 = 2026-09)
DAYS = {m: [date.fromisoformat(f"{LEDGER[m]}-0{d}") for d in (6, 7)] for m in range(1, 13)}


def _event(iso: str, n: int) -> dict:
    return {"event_id": f"ev-{iso}-{n}", "event_type": "level", "direction": "long", "label": "held",
            "pair_end_label": "held", "ts": 0, "t_resolved": 0, "n_firings_c": 9, "damage_drop": False,
            "tradeable": True, "primary_member_type": "pdh", "post_cross_retest": False, "primary_flip_count": 0}


def _fake_build(env, monkeypatch, months, *, skip=()):
    """A built plan over `months`: every session has its day summary (with a zone difference, so the session's date
    would be listed), events with more than 5 style-(c) firings (so their ids would be listed) and decisions."""
    sessions = [d for m in months for d in DAYS[m]]
    doc = {"key": "k13d", "months": list(months), "sessions": [d.isoformat() for d in sessions]}
    monkeypatch.setattr(s13, "build_doc", lambda env_: doc)
    base = s13.out_dir(env, doc)
    for d in sessions:
        if d in skip:
            continue
        iso = d.isoformat()
        atomic_write_json(s13.fb.summary_file(env, doc["key"], d),
                          {"pass2": {"zone_diff_any": True, "firings_c_max": 9,
                                     "level_jumps": [{"root": "NQ", "sub_type": f"jump-{iso}"}]}})
        write_table(base / "events" / f"{iso}.parquet", [_event(iso, 1), _event(iso, 2)], {"day": iso})
        write_table(base / "decisions" / f"{iso}.parquet",
                    [{"event_id": f"ev-{iso}-1", "style": "c", "decision_ts": 0, "trigger_kind": "audit",
                      "zone_lo": 1.0, "zone_hi": 2.0}], {"day": iso})
        write_table(base / "level_events" / f"{iso}.parquet", [{"group": "x", "name": "cross"}], {"day": iso})
    return doc


def _watch_reads(monkeypatch) -> list[date]:
    """Every session whose files `step13b` opens (day summaries and parquet reads)."""
    seen: list[date] = []
    real_summary, real_read = s13.day_summary, s13._read

    def summary(env_, doc, day):
        seen.append(day)
        return real_summary(env_, doc, day)

    def read(path):
        seen.append(date.fromisoformat(path.stem))
        return real_read(path)
    monkeypatch.setattr(s13, "day_summary", summary)
    monkeypatch.setattr(s13, "_read", read)
    return seen


def _late(env, days) -> list[date]:
    return [d for d in days if months_mod.month_index(d, env.settings) not in months_mod.DEVELOPMENT]


def test_counts_on_a_full_window_build_reads_months_1_to_6_only(env, monkeypatch):
    _fake_build(env, monkeypatch, range(1, 13))
    seen = _watch_reads(monkeypatch)
    text = s13.counts(env).read_text(encoding="utf-8")
    assert not _late(env, seen)                                          # no month 7-12 file was even opened
    assert {months_mod.month_index(d, env.settings) for d in seen} == set(range(1, 7))
    assert text.startswith("# Step 13b counts on months 1-6 (build k13d, 12 sessions)")
    for m in range(7, 13):
        assert LEDGER[m] not in text                                      # no month 7-12 date, event or jump
    for m in range(1, 7):                                                 # the leak check can see dates at all
        assert any(d.isoformat() in text for d in DAYS[m])


def test_counts_on_a_months_1_to_4_build_stays_months_1_to_4(env, monkeypatch):
    _fake_build(env, monkeypatch, range(1, 5))
    text = s13.counts(env).read_text(encoding="utf-8")
    assert text.startswith("# Step 13b counts on months 1-4 (build k13d, 8 sessions)")
    assert "months 1-4 here" in text


def test_labels_plan_on_a_full_window_build_is_months_1_to_6(env, monkeypatch):
    # months 7-12 not even built: they never hold the plan up, and never enter it
    late = [d for m in range(7, 13) for d in DAYS[m]]
    _fake_build(env, monkeypatch, range(1, 13), skip=late)
    seen = _watch_reads(monkeypatch)
    plan = s13.labels_plan(env, firings_ok=True)
    assert plan["sessions"] == [d.isoformat() for m in range(1, 7) for d in DAYS[m]]
    assert not _late(env, [date.fromisoformat(d) for d in plan["sessions"]]) and not _late(env, seen)
    assert s13.labels_chunk_ids(env)[:-1] == [f"l:{d}:{plan['label_key']}" for d in plan["sessions"]]


def test_labels_plan_refuses_while_month_5_or_6_is_not_built(env, monkeypatch):
    _fake_build(env, monkeypatch, range(1, 13), skip=[DAYS[6][1]])
    with pytest.raises(s13.Refused, match=f"months 1-6 of build k13d are not complete .*first {DAYS[6][1]}"):
        s13.labels_plan(env, firings_ok=True)


def test_labels_plan_on_a_months_1_to_4_build_is_unchanged(env, monkeypatch):
    _fake_build(env, monkeypatch, range(1, 5))
    plan = s13.labels_plan(env, firings_ok=True)
    assert plan["sessions"] == [d.isoformat() for m in range(1, 5) for d in DAYS[m]]
    with pytest.raises(s13.Refused, match="style-\\(c\\) firings"):
        s13.labels_plan(env)                                              # the firings guard is unchanged


def test_a_month_7_to_12_session_is_refused_by_the_labeler(env, monkeypatch):
    # belt and braces: even a hand-edited plan listing a month-7 session can't label it
    d = DAYS[7][0]
    monkeypatch.setattr(s13, "load_labels_plan", lambda env_: ({"sessions": [d.isoformat()], "label_key": "lk"},
                                                               {"key": "k13d"}, object()))
    with pytest.raises(months_mod.NotDevelopmentMonth):
        s13.labels_day(env, d)


def test_13c_keeps_its_months_1_to_4_tuning_set(env, monkeypatch):
    doc = _fake_build(env, monkeypatch, range(1, 13))
    assert s13.sessions_14(env, doc) == [d for m in range(1, 5) for d in DAYS[m]]
    assert s13.built_days(env, doc) == s13.sessions_14(env, doc)
