"""The §3.2 damage rule on completeness rows (owner 2026-10-09, SPEC §13 Q8-degraded)."""

from datetime import date

from nqbot.config import load_settings
from nqbot.features import damage

S = {"databento_damage_excess_minutes": 5, "databento_damage_gap_sec": 120, "databento_damage_session_minutes": 30}


def row(part, series, empty, base_max, gaps=(), normal_gap=60.0, day="2025-11-28", span="11-27 19:00–11-28 09:30 ET"):
    return {"cme_day": day, "part": part, "span": span, "series": series, "empty_minutes": empty,
            "baseline_empty_max": base_max, "baseline_gap_max_sec": normal_gap, "gaps": list(gaps)}


def test_the_2025_11_28_overnight_outage_empties_on_levels_only():
    rows = [row("ETH", "NQ trades", 647, 0, [{"from": "21:44:14", "sec": 38745.2}, {"from": "20:46:59", "sec": 72.2}]),
            row("ETH", "ES trades", 645, 0, [{"from": "21:44:14", "sec": 38745.2}]),
            row("RTH", "NQ trades", 0, 0, span="11-28 09:30–11-28 13:00 ET")]
    d = damage.classify(rows, S)[date(2025, 11, 28)]
    assert d.eth_empty and not d.rth_dropped and d.rth_minutes == 0
    # one shared 646-minute gap (21:44:14 + 38,745 s, crossing midnight) + the larger excess 647: counted once
    assert d.eth_minutes == 646 + 647


def test_rth_small_damage_keeps_the_session_large_drops_it():
    small = [row("RTH", "NQ trades", 3, 0, [{"from": "10:00:00", "sec": 150}], span="03-16 09:30–03-16 16:00 ET",
                 day="2026-03-16")]
    d = damage.classify(small, S)[date(2026, 3, 16)]
    assert not d.rth_dropped and d.rth_minutes == 3 + 3 and len(d.rth_gap_minutes) == 1   # 10:00:00-10:02:30: 3 min
    big = [row("RTH", "NQ trades", 40, 0, span="03-16 09:30–03-16 16:00 ET", day="2026-03-16")]
    assert damage.classify(big, S)[date(2026, 3, 16)].rth_dropped


def test_within_normal_is_not_damage():
    rows = [row("RTH", "ES trades", 4, 0, [{"from": "10:00:00", "sec": 100}], normal_gap=60,
                span="04-10 09:30–04-10 16:00 ET", day="2026-04-10")]
    d = damage.classify(rows, S)[date(2026, 4, 10)]   # 4 <= 0 + 5 excess and the gap is under 120 s
    assert d.rth_minutes == 0 and not d.rth_dropped and not d.eth_empty


def test_settings_keys_exist():
    s = load_settings()
    assert all(k in s for k in S)
