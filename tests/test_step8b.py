"""Step-8b gate (presd_fixes 12): hand-built boards and DBN trades on a temporary SSD; no network."""

import json
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pyarrow as pa
import pytest

from nqbot import cli
from nqbot.checks import step8b
from nqbot.clients.databento_client import TRADES
from nqbot.clients.databento_pull import plan_path
from nqbot.clients.skylit_pull import SkylitPullJob
from nqbot.clients.skylit_store import BOARD_SCHEMA, STRIKE_SCHEMA, write_parquet
from nqbot.config import load_features
from tests.databento_fakes import INSTRUMENT, dbn_bytes, trade

ET = ZoneInfo("America/New_York")
NS = 1_000_000_000
DAY = date(2026, 3, 5)  # development month 6
MONTHS = {i + 1: f"{2025 + (9 + i) // 12}-{(9 + i) % 12 + 1:02d}" for i in range(12)}  # 1 = 2025-10 ... 12 = 2026-09
R0 = {"QQQ": 40.0, "NDXP": 1.0, "SPY": 10.0, "SPX": 1.0}
ROOT = {"QQQ": "NQ", "NDXP": "NQ", "SPY": "ES", "SPX": "ES"}
STEP = {"QQQ": 1.0, "NDXP": 10.0, "SPY": 1.0, "SPX": 5.0}
FRONT = {"NQ": "NQH6", "ES": "ESH6"}


def _ns(t: time) -> int:
    return int(datetime.combine(DAY, t, tzinfo=ET).timestamp()) * NS


def _futures(seed=3, vol=(0.5, 0.25)):
    """One trade a second 09:00-10:30 ET: NQ around 24,000 and ES around 6,000 (0.25 ticks), `vol` pts a second."""
    rng = np.random.default_rng(seed)
    ts = np.arange(_ns(time(9, 0)), _ns(time(10, 30)), NS, dtype=np.int64)
    nq = 24_000 + np.round(np.cumsum(rng.normal(0, vol[0], len(ts))) * 4) / 4
    es = 6_000 + np.round(np.cumsum(rng.normal(0, vol[1], len(ts))) * 4) / 4
    return ts, {"NQ": nq, "ES": es}


def _write_futures(env, ts, px):
    for root, sym in FRONT.items():
        path = env.db.raw_path(TRADES, sym, DAY)
        path.parent.mkdir(parents=True, exist_ok=True)
        recs = [trade(INSTRUMENT[sym], int(t), float(p), seq=i) for i, (t, p) in enumerate(zip(ts, px[root]))]
        path.write_bytes(dbn_bytes(TRADES, sym, int(ts[0]), int(ts[-1]) + NS, recs))
    plan_path(env.paths).parent.mkdir(parents=True, exist_ok=True)
    plan_path(env.paths).write_text(json.dumps({"front": {r: {DAY.isoformat(): s} for r, s in FRONT.items()}}))


def _write_boards(env, ts, px, noise=1e-5, lag_s=0, seed=5):
    """1-second boards 09:30-10:30 for the 4 maps: spot = F(t - lag) / R0 * (1 + noise); five strikes around
    spot, the middle one the King, the outer two below 25% of King."""
    rng = np.random.default_rng(seed)
    as_of = np.arange(_ns(time(9, 30)), _ns(time(10, 30)), NS, dtype=np.int64)
    b_rows, s_rows = [], []
    for m in R0:
        f = px[ROOT[m]][np.searchsorted(ts, as_of - lag_s * NS, side="right") - 1]
        spot = f / R0[m] * (1 + rng.normal(0, noise, len(as_of)))
        for t, sp in zip(as_of, spot):
            t_ms = int(t // 1_000_000)
            b_rows.append({"as_of": t_ms, "symbol": m, "api_symbol": m, "spot": float(sp), "previous_close": float(sp),
                           "resolution": "1s", "axis_id": 0, "expirations": "2026-03-05", "n_strikes": 5,
                           "window_from": t_ms, "window_to": t_ms})
            mid = round(sp / STEP[m]) * STEP[m]
            for k, v in zip((-2, -1, 0, 1, 2), (1e5, 6e5, -1e6, 4e5, 2e5)):
                s_rows.append({"as_of": t_ms, "symbol": m, "strike": mid + k * STEP[m], "value": v})
    boards = pa.Table.from_pylist(b_rows, schema=BOARD_SCHEMA)
    strikes = pa.Table.from_pylist(s_rows, schema=STRIKE_SCHEMA)
    b_path, s_path = env.sky.range_paths(DAY, "0930")
    write_parquet(s_path, strikes, {})
    write_parquet(b_path, boards, {})


@pytest.fixture
def env(paths, settings, log):
    settings["months"] = dict(MONTHS)
    lines: list[str] = []
    e = step8b.Env(settings=settings, features=load_features(), paths=paths, log=log, echo=lines.append,
                   now=lambda: datetime(2026, 10, 7, 9, 0, tzinfo=ET))
    e.lines = lines
    SkylitPullJob.write_plan(paths, "skylit_sample_days", [DAY], approved=None)
    return e


def _setup(env, vol=(0.5, 0.25), **kw):
    ts, px = _futures(vol=vol)
    _write_futures(env, ts, px)
    _write_boards(env, ts, px, **kw)


def test_refuses_without_the_sample_days_data(env):
    with pytest.raises(step8b.CheckRefused, match="no Databento plan"):
        step8b.check_precision(env)
    ts, px = _futures()
    _write_futures(env, ts, px)
    with pytest.raises(step8b.CheckRefused, match="no Skylit boards"):
        step8b.check_precision(env)
    env.db.raw_path(TRADES, "ESH6", DAY).unlink()
    _write_boards(env, ts, px)
    with pytest.raises(step8b.CheckRefused, match="ES trades for 2026-03-05 not pulled"):
        step8b.check_precision(env)


def test_refuses_outside_the_development_months(env):
    from nqbot.months import NotDevelopmentMonth

    with pytest.raises(NotDevelopmentMonth):
        step8b.check_precision(env, [date(2026, 4, 15)])  # month 7


def test_precision_passes_with_a_tight_ratio(env):
    _setup(env, noise=1e-6)
    rep = step8b.check_precision(env)
    qqq = rep["per_chain"]["QQQ"]
    assert rep["all_within_p95_max"] and qqq["within_p95_max"] and qqq["unit"] == "NQ pts"
    assert qqq["overall"]["n"] > 3000 * 3  # three relevant nodes a board (the outer two are < 25% of King)
    assert qqq["rejected"] == 0 and qqq["ratio_shift_days"] == []
    assert rep["per_chain"]["NDXP"]["applied"] == 3600 - 120  # the opening 120 s skipped (F1.3)
    assert set(qqq["by_time"]) == {"09:30", "10:00"} and set(qqq["by_vol"]) >= {"low", "mid", "high"}
    assert rep["diagnostic"]  # owner Q8b-4: F1.6 is a diagnostic, not a gate condition
    assert rep["stop_buffer_floor"] == env.settings["stop_buffer_floor"]  # per level source, §5.1


def test_precision_fails_with_a_noisy_spot(env):
    _setup(env, noise=1e-4)
    rep = step8b.check_precision(env)
    assert not rep["all_within_p95_max"] and not rep["per_chain"]["QQQ"]["within_p95_max"]
    assert rep["per_chain"]["QQQ"]["rejected"] > 0  # 1 bp noise: some updates beyond 3 bps of the median


@pytest.mark.parametrize("lag, ok", [(0, True), (1, True), (5, False)])
def test_spot_lag_gate(env, lag, ok):
    _setup(env, noise=1e-6, lag_s=lag)
    rep = step8b.check_spot_lag(env)
    qqq = rep["per_day"][DAY.isoformat()]["QQQ"]
    assert qqq["peak_lag_s"] == lag and qqq["gate"] and rep["pass"] is ok


def test_atlas_side_by_side(env):
    _setup(env, noise=1e-6, vol=(0.2, 0.1))  # one trade a second: a calm tape keeps the 1-s read within tolerance
    env.settings["map_latency_sec"] = 0.59  # the owner's step-7 value (2026-10-09)
    late = step8b.check_atlas(env, DAY, [time(9, 45)])["maps"]["QQQ"]
    # the 09:44:59 board applies at 09:45:00.59: at 09:45:00 the 09:44:58 board is still in force
    assert late[0]["board"] == "09:44:58"
    env.settings["map_latency_sec"] = 0
    rep = step8b.check_atlas(env, DAY, [time(9, 45), time(10, 0)])
    rows = rep["maps"]["QQQ"]
    # the 09:45:00 board applies at floor(asOf) + 1 s (+ map_latency_sec): at 09:45 the 09:44:59 board is in force
    assert [r["minute"] for r in rows] == ["09:45", "10:00"] and rows[0]["board"] == "09:44:59"
    nodes = rows[0]["nodes"]
    assert len(nodes) == 3 and max(n["pct_of_king"] for n in nodes) == 1.0  # only nodes >= 25% of King
    king = next(n for n in nodes if n["pct_of_king"] == 1.0)
    assert king["band"][0] < king["level"] < king["band"][1]
    assert rep["crossings"] == rep["gated_crossings"] > 0 and rep["owner_check"] == "pending"
    assert rep["share_within_tol"]["SPY"] >= 0.9 and rep["tolerance"] == {"NQ": 4.05, "ES": 0.80}
    assert rep["within_tol_counts"]["SPY"][1] > 0
    md = (step8b.reports_dir(env.paths) / f"atlas_{DAY.isoformat()}.md").read_text(encoding="utf-8")
    assert "| Strike | % of King | Mapped NQ |" in md and "## SPY -> ES" in md


def test_atlas_lists_a_held_node_and_drops_its_crossings_after_it_drops(env):
    """F2.4 (owner 2026-10-11): QQQ spot 600 -> 599 at 09:50 puts strike 601 from 40% to 20% of King: it stays
    relevant, marked held, and its crossing at 09:55 (599 -> 603) counts; at 603 it is 10% of King and drops, so the
    crossing back down at 10:00 (603 -> 600.5) does not."""
    ts = np.arange(_ns(time(9, 0)), _ns(time(10, 30)), NS, dtype=np.int64)
    nq = np.select([ts < _ns(time(9, 50)), ts < _ns(time(9, 55)), ts < _ns(time(10, 0))],
                   [24_000.0, 23_960.0, 24_120.0], 24_020.0)
    px = {"NQ": nq, "ES": np.full(len(ts), 6_001.0)}
    _write_futures(env, ts, px)
    _write_boards(env, ts, px, noise=0.0)
    rep = step8b.check_atlas(env, DAY, [time(9, 45), time(9, 52), time(9, 57)])
    by_min = {r["minute"]: {n["strike"]: n for n in r["nodes"]} for r in rep["maps"]["QQQ"]}
    assert by_min["09:45"][601.0]["held"] is False and by_min["09:45"][601.0]["pct_of_king"] == 0.4
    n601 = by_min["09:52"][601.0]
    assert n601["held"] is True and n601["pct_of_king"] == 0.2
    assert [c["at"][:5] for c in n601["crossings"]] == ["09:55"]  # not the 10:00 one: dropped by then
    assert 601.0 not in by_min["09:57"]  # present at 10% of King: not relevant
    md = (step8b.reports_dir(env.paths) / f"atlas_{DAY.isoformat()}.md").read_text(encoding="utf-8")
    assert "| 601 | 20.0% held |" in md


def test_atlas_counts_an_exact_touch_once(env):
    """QQQ spot 599.5 -> exactly 600.0 for 5 s -> 600.5: strike 600 is crossed once, not twice (the board that
    leaves the strike would map it with its own R, k * F / k = F, a 0.00 diff that proves nothing)."""
    ts = np.arange(_ns(time(9, 0)), _ns(time(10, 30)), NS, dtype=np.int64)
    nq = np.where(ts < _ns(time(9, 50)), 23_980.0, np.where(ts < _ns(time(9, 50, 5)), 24_000.0, 24_020.0))
    px = {"NQ": nq, "ES": np.full(len(ts), 6_001.0)}
    _write_futures(env, ts, px)
    _write_boards(env, ts, px, noise=0.0)
    rep = step8b.check_atlas(env, DAY, [time(9, 45)])
    king = next(n for n in rep["maps"]["QQQ"][0]["nodes"] if n["strike"] == 600.0)
    assert [c["at"] for c in king["crossings"]] == ["09:50:00"]


def test_gate_report_covers_every_sample_day(env):
    other = date(2026, 3, 6)
    SkylitPullJob.write_plan(env.paths, "skylit_sample_days", [DAY, other], approved=None)
    _setup(env, noise=1e-6, vol=(0.2, 0.1))
    step8b.check_atlas(env, DAY, [time(9, 45)])
    text = step8b.check_report(env).read_text(encoding="utf-8")
    assert f"  - {DAY.isoformat()} (09:45): " in text and f"(table: atlas_{DAY.isoformat()}.md)" in text
    assert f"  - {other.isoformat()}: **pending**" in text and "not passed yet" in text


def test_gate_report_and_cli(env, settings, drive, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    assert cli.main(["step8b", "report"]) == 0
    text = (step8b.reports_dir(env.paths) / "mapping_gate.md").read_text(encoding="utf-8")
    assert "Precision: **pending**" in text and "not passed yet" in text
    assert cli.main(["step8b", "precision"]) == cli.EXIT_REFUSED  # no data yet: refused, not a traceback
    assert "refused" in capsys.readouterr().err
    _setup(env, noise=1e-6, vol=(0.2, 0.1))
    assert cli.main(["step8b", "precision"]) == 0
    assert cli.main(["step8b", "spot-lag", "--dates", DAY.isoformat()]) == 0
    assert cli.main(["step8b", "atlas", "--date", DAY.isoformat(), "--minutes", "09:45,10:00"]) == 0
    assert cli.main(["step8b", "report"]) == 0
    text = (step8b.reports_dir(env.paths) / "mapping_gate.md").read_text(encoding="utf-8")
    assert "pooled over every sample day" in text and "REGRESSION ALERT" not in text and "not passed yet" in text and "owner's check in Atlas replay" in text
    floors = next(line for line in text.splitlines() if line.startswith("- Stop floors per level source"))
    assert all(x in floors for x in ("QQQ 4.25", "NDXP 6.5", "chart 0"))
    assert "diagnostic, not a gate condition" in text and "(a) bias" in text and "**pending**" in text  # Q8b-4


def test_boards_after_an_early_close_are_left_out(env):
    env.cal = {"holidays": [], "early_closes": [{"date": DAY, "early_close_et": "10:00"}]}
    _setup(env, noise=1e-6)
    rep = step8b.check_precision(env)
    assert rep["per_chain"]["QQQ"]["applied"] == 1800  # 09:30-10:00 only (the probe window after it is left out)
    assert set(rep["per_chain"]["QQQ"]["by_time"]) == {"09:30"}


def test_refuses_while_step10_moves_a_raised_sample_day(env):
    """Fresh review #7: a sample day whose windows step 10 is replacing (half moved) is never read."""
    _setup(env, noise=1e-6)
    raised = env.sky.base("strike_window_raise", pulled=f"max{env.settings['skylit_max_strikes']}")
    for src in env.sky.range_paths(DAY, "0930"):
        dst = raised / src.relative_to(env.sky.root)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
    assert step8b.skylit_base(env, DAY) == raised  # both complete: the raised copy (current parameters)
    b2, s2 = (raised / p.relative_to(env.sky.root) for p in env.sky.range_paths(DAY, "0945"))
    b2.write_bytes(env.sky.range_paths(DAY, "0930")[0].read_bytes())
    s2.write_bytes(env.sky.range_paths(DAY, "0930")[1].read_bytes())
    with pytest.raises(step8b.CheckRefused, match="replacing this sample day"):
        step8b.skylit_base(env, DAY)


def test_atlas_regression_alert(env):
    """Q8b-5 (owner 2026-10-08): the share within the frozen tolerance is information only, with an alert below 90%."""
    _setup(env, noise=1e-6, vol=(0.2, 0.1))
    env.features["mapping"]["atlas_match_tol"] = {"NQ": 0.0, "ES": 0.0}  # nothing within: the regression alert
    rep = step8b.check_atlas(env, DAY, [time(9, 45)])
    assert rep["share_within_tol"]["SPY"] < 0.9
    text = step8b.check_report(env).read_text(encoding="utf-8")
    assert "**REGRESSION ALERT** (SPY)" in text and "owner's check in Atlas replay: pending" in text


def test_atlas_regression_is_judged_pooled():
    """One day under 90% doesn't alert when the share pooled over the sample days is above it (owner 2026-10-08)."""
    days = [{"within_tol_counts": {"QQQ": [130, 145], "SPY": [111, 114]}},  # QQQ 89.7% that day
            {"within_tol_counts": {"QQQ": [97, 97], "SPY": [0, 0]}}]
    pooled = step8b.atlas_pooled_share(days)
    assert pooled == {"QQQ": (227, 242), "SPY": (111, 114)} and 227 / 242 >= 0.9
