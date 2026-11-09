"""Step 13-0's check of the corrections code (owner 2026-11-09; SPEC §12 step 13, §13 Q-step13-0). Development
months only (§7 rule 2b); reads the stored `features_version` 36 build and writes only to a scratch folder
(`<tmp_dir>/step13_0_check/`) and `<reports_dir>/step13/`. Outside the build key (not in `feature_build.CODE_PARTS`).

Days: the five step-12 sample days and the two smoke days (`CHECK_DAYS`), each rebuilt from the v36 build's stored
start state (the state after the previous planned session; the warm-up state for 2025-10-01).

Runs (`RUNS`), each a set of run-time overrides plus diagnostic switches (`features.diagnostics`):
- `legacy`: neutral settings and every unparameterised fix off: must reproduce v36 exactly (events, labels and their
  resolution times, every v36 touch-row column, the day-type rows, the end state's v36 fields);
- `neutral`: neutral settings, the unparameterised fixes on (candidate times, level jumps, own re-arm, profile rows,
  merged-zone touch count): its differences from v36 are traced to the `legacy+<fix>` runs (each fix alone);
- one run per parameterised correction or variant on top of `neutral` (zone buffers, debounce, hold 30, top-n,
  strike side, retest lookback 3 / 5);
- `start` / `start_again`: the committed configuration, built twice (determinism).
Neutral = zone buffers 0, every hold distance 20, `node_relevance_top_n` off, `node_role_at_formation` approach,
`node_relevance_min_sec` 0 (no debounce), `retest_lookback_sessions` 10.

Job `step13_check` (`nqbot run step13_check --until HH:MM`): one (run, day) per chunk, each in its own process
(`nqbot step13 check-day D --run R`), then the 2025-11-18 jump plot (Q-step12b (e)) and the report
(`<reports_dir>/step13/corrections_check.md`). Caveat: every day starts from v36's carried state (its carried touch
history labels, for example, were made at 20 pts).
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, time, timezone
from pathlib import Path
from statistics import median
from typing import Any

from nqbot.checks.step8b import Env
from nqbot.features import diagnostics as dg
from nqbot.features.session_tape import ET, NS, et_ns
from nqbot.months import require_development
from nqbot.storage import atomic_write_json, atomic_write_text

JOB = "step13_check"
STORED_VERSION = 36
CHECK_DAYS = tuple(date.fromisoformat(d) for d in ("2025-10-01", "2026-03-06", "2026-03-11", "2026-03-17",
                                                     "2026-03-20", "2026-03-24", "2026-03-26"))
JUMP_DAY = date(2025, 11, 18)          # Q-step12b (a), (e): the weekly POC jump (13:19:00 in v36)
JUMP_FEW = 3                           # owner 2026-11-09: VWAP / node bands jumping more often in a session -> stop
NEUTRAL: dict[str, Any] = {"zone_join_buffer": 0, "zone_qualify_buffer": 0, "hold_reaction_pts": 20,
                           "history_hold_pts": 20, "retest_hold_pts": 20, "pair_end_hold_pts": 20,
                           "node_relevance_top_n": None, "node_role_at_formation": "approach",
                           "node_relevance_min_sec": 0, "retest_lookback_sessions": 10}
ALL_LEGACY = tuple(dg.LEGACY)
RUNS: dict[str, tuple[dict[str, Any], tuple[str, ...]]] = {
    "legacy": (NEUTRAL, ALL_LEGACY),
    "neutral": (NEUTRAL, ()),
    **{f"legacy+{k}": (NEUTRAL, tuple(x for x in ALL_LEGACY if x != k)) for k in ALL_LEGACY},
    "join5": ({**NEUTRAL, "zone_join_buffer": 5}, ()),
    "qual5": ({**NEUTRAL, "zone_qualify_buffer": 5}, ()),
    "debounce": ({**NEUTRAL, "node_relevance_min_sec": 60}, ()),
    "hold30": ({**NEUTRAL, "hold_reaction_pts": 30, "history_hold_pts": 30}, ()),
    "topn5": ({**NEUTRAL, "node_relevance_top_n": 5}, ()),
    "strike_side": ({**NEUTRAL, "node_role_at_formation": "strike_side"}, ()),
    "lookback3": ({**NEUTRAL, "retest_lookback_sessions": 3}, ()),
    "lookback5": ({**NEUTRAL, "retest_lookback_sessions": 5}, ()),
    "start": ({}, ()),
    "start_again": ({}, ()),
}
PLOT_CHUNK, REPORT_CHUNK = f"plot:{JUMP_DAY.isoformat()}", "report"


class Refused(Exception):
    pass


# --- the stored build (read-only) --------------------------------------------------------------------------------

def stored_plan(env: Env) -> dict[str, Any]:
    from nqbot.checks import feature_build as fb

    p = fb.plan_path(env)
    if not p.exists():
        raise Refused(f"no stored feature-build plan ({p})")
    doc = json.loads(p.read_text(encoding="utf-8"))
    if int(doc.get("features_version", -1)) != STORED_VERSION:
        raise Refused(f"the stored plan is features_version {doc.get('features_version')}, not {STORED_VERSION}: "
                      "the check compares with the v36 build")
    if not stored_dir(env, doc["key"]).exists():
        raise Refused(f"the stored v36 build {stored_dir(env, doc['key'])} is missing")
    return doc


def stored_dir(env: Env, key: str) -> Path:
    return env.paths.data_dir / "processed" / "features" / f"v{STORED_VERSION}_{key}"


def scratch(env: Env) -> Path:
    return env.paths.tmp_dir / "step13_0_check"


def report_dir(env: Env) -> Path:
    return env.paths.reports_dir / "step13"


def start_state_file(env: Env, doc: dict[str, Any], day: date) -> Path:
    days = [date.fromisoformat(x) for x in doc["sessions"]]
    if day not in days:
        raise Refused(f"{day} is not a session of the stored build")
    k = days.index(day)
    base = stored_dir(env, doc["key"]) / "state"
    return base / (f"state_warmup_{day.isoformat()}.json" if k == 0 else f"state_{days[k - 1].isoformat()}.json")


def end_state_file(env: Env, doc: dict[str, Any], day: date) -> Path:
    return stored_dir(env, doc["key"]) / "state" / f"state_{day.isoformat()}.json"


# --- one (run, day) ----------------------------------------------------------------------------------------------

def run_env(env: Env, run: str) -> tuple[Env, dict[str, Any]]:
    from nqbot.checks import feature_build as fb
    from nqbot.config import load_features

    if run not in RUNS:
        raise Refused(f"unknown run {run!r}; runs: {', '.join(RUNS)}")
    overrides, legacy = RUNS[run]
    feats = fb.apply_overrides(load_features(), overrides)
    switches = {dg.LEGACY[k][0]: dg.LEGACY[k][1] for k in legacy}
    return Env(settings=env.settings, features=feats, paths=env.paths, log=env.log, echo=env.echo, cal=env.cal), \
        switches


def build(env: Env, day: date, run: str):
    """The day built from the v36 start state under the run's settings. The caller holds the run's diagnostic
    switches (`switches_of`) for as long as it reads the session: its rows are computed when asked for."""
    from nqbot.checks import session_build as sb

    require_development(day, env.settings)
    doc = stored_plan(env)
    e2, _ = run_env(env, run)
    src = start_state_file(env, doc, day)
    state = sb.DayState.from_json(json.loads(src.read_text(encoding="utf-8")))
    pl = sb.plan(e2)
    planned = sb.planned_sessions(pl, e2.cal)
    built = sb.build_day(e2, day, state, pl, planned, sb.load_damage(e2), allow_diagnostic_switches=True)
    return built, e2, doc


def switches_of(run: str) -> dict[str, Any]:
    return {dg.LEGACY[k][0]: dg.LEGACY[k][1] for k in RUNS[run][1]}


def check_day(env: Env, day: date, run: str) -> dict[str, Any]:
    """`nqbot step13 check-day D --run R`: build, write the day's files to the scratch folder, diff with v36 (all
    under the run's diagnostic switches)."""
    if run not in RUNS:
        raise Refused(f"unknown run {run!r}; runs: {', '.join(RUNS)}")
    with dg.switched(switches_of(run)):
        return _check_day(env, day, run)


def _check_day(env: Env, day: date, run: str) -> dict[str, Any]:
    from nqbot.checks import feature_build as fb

    built, e2, doc = build(env, day, run)
    base = scratch(env) / run
    fb.write_day_files(base, built.sf, day, f"step13_0:{run}", e2.features)
    stored_end = json.loads(end_state_file(env, doc, day).read_text(encoding="utf-8"))
    out = {"day": day.isoformat(), "run": run, "info": built.info,
           "state_digest": built.state_out.digest(),
           "end_state_v36_fields_equal": _v36_fields_equal(
               stored_end, json.loads(json.dumps(built.state_out.to_json(), default=str))),
           "node_levels": sum(1 for x in built.sf.nq_levels.values() if x.rtype == "gex_node" and x.spans),
           "relevance_changes": _relevance_changes(built.sf),
           "diff": diff_day(stored_dir(env, doc["key"]), base, day)}
    atomic_write_json(base / "days" / f"{day.isoformat()}.json", out)
    env.echo(f"{run} {day}: {out['diff']['summary']}")
    return out


def _relevance_changes(sf) -> dict[str, int]:
    import numpy as np

    sky = sf.c.sky
    if sky is None:
        return {}
    out = {}
    for m, ms in sky.ms.items():
        b, k, r = ms.mb.s_board, ms.mb.s_strike, ms.relevant
        if not len(b):
            out[m] = 0
            continue
        o = np.lexsort((b, k))
        ko, ro = k[o], r[o]
        out[m] = int(((ro[1:] != ro[:-1]) & (ko[1:] == ko[:-1])).sum() + ro[np.r_[True, ko[1:] != ko[:-1]]].sum())
    return out


def _v36_fields_equal(stored: dict, built: dict) -> dict[str, Any]:
    """Every field the stored (v36) state has, compared with the rebuilt state (new fields are ignored)."""
    bad = []

    def walk(a, b, path):
        if isinstance(a, dict):
            if not isinstance(b, dict):
                bad.append(path)
                return
            for k in a:
                if k == "source":
                    continue
                if k not in b:
                    bad.append(f"{path}.{k} (missing)")
                else:
                    walk(a[k], b[k], f"{path}.{k}")
        elif isinstance(a, list):
            if not isinstance(b, list) or len(a) != len(b):
                bad.append(path)
                return
            for i, (x, y) in enumerate(zip(a, b)):
                walk(x, y, f"{path}[{i}]")
        elif not _eq(a, b):
            bad.append(path)

    walk(stored, built, "state")
    return {"equal": not bad, "differ": bad[:20], "n_differ": len(bad)}


def _eq(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return (math.isnan(a) and math.isnan(b)) or a == b or abs(a - b) <= 1e-9 * max(1.0, abs(a))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return float(a) == float(b)
    return a == b


# --- the diff ------------------------------------------------------------------------------------------------------

def _read(base: Path, kind: str, day: date) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq

    p = base / kind / f"{day.isoformat()}.parquet"
    return pq.read_table(p).to_pylist() if p.exists() else []


def _types(r: dict[str, Any]) -> str:
    t = sorted(k[2:] for k, v in r.items() if k.startswith("n_") and not k.startswith("n_node_")
               and k != "n_post_cross" and (v or 0) > 0)
    if r.get("in_golden_zone"):
        t.append(f"golden_{r.get('golden_zone_part')}")
    return "+".join(t)


def signature(r: dict[str, Any]) -> str:
    """An event's identity across builds for this report: its touch time, direction and member types."""
    return f"{int(r['ts'])}|{int(r['direction'])}|{_types(r)}"


def diff_day(stored: Path, built: Path, day: date) -> dict[str, Any]:
    st_rows, bt_rows = _read(stored, "touches", day), _read(built, "touches", day)
    st_out = {r["event_id"]: r for r in _read(stored, "outcomes", day) if r.get("how") != "eod_day_type"}
    bt_out = {r["event_id"]: r for r in _read(built, "outcomes", day) if r.get("how") != "eod_day_type"}
    s_by, b_by = {r["event_id"]: r for r in st_rows}, {r["event_id"]: r for r in bt_rows}
    same_id = sorted(set(s_by) & set(b_by))
    s_only = [s_by[k] for k in sorted(set(s_by) - set(b_by))]
    b_only = [b_by[k] for k in sorted(set(b_by) - set(s_by))]
    b_sig: dict[str, list] = {}
    for r in b_only:
        b_sig.setdefault(signature(r), []).append(r)
    renamed, removed = [], []
    for r in s_only:
        cands = b_sig.get(signature(r), [])
        if len(cands) == 1:
            renamed.append((r, cands.pop()))
        else:
            removed.append(r)
    added = [r for v in b_sig.values() for r in v]
    pairs = [(s_by[k], b_by[k]) for k in same_id] + renamed
    lab_chg = [s["event_id"] for s, b in pairs if st_out[s["event_id"]]["label"] != bt_out[b["event_id"]]["label"]]
    res_chg = [s["event_id"] for s, b in pairs
               if int(st_out[s["event_id"]]["t_resolved"]) != int(bt_out[b["event_id"]]["t_resolved"])]
    cols: dict[str, int] = {}
    v36_cols = set().union(*(r.keys() for r in st_rows)) if st_rows else set()
    for s, b in ((s_by[k], b_by[k]) for k in same_id):
        for c in v36_cols:
            if c == "event_id":
                continue
            if not _same_val(s.get(c), b.get(c)):
                cols[c] = cols.get(c, 0) + 1
    dts, dtb = _read(stored, "daytype", day), _read(built, "daytype", day)
    dt_cols = set().union(*(r.keys() for r in dts)) if dts else set()
    dt_diff = len(dts) != len(dtb) or any(not _same_val(a.get(c), b.get(c)) for a, b in zip(dts, dtb) for c in dt_cols)
    diff_sigs = sorted({signature(r) for r in removed} | {signature(r) for r in added} |
                       {signature(s) for s, b in pairs if s["event_id"] in set(lab_chg) | set(res_chg)})
    summary = (f"events {len(st_rows)} -> {len(bt_rows)}: same id {len(same_id)}, renamed {len(renamed)}, "
               f"removed {len(removed)}, added {len(added)}; labels changed {len(lab_chg)}, resolution times "
               f"changed {len(res_chg)}; row columns with a change {len(cols)}; day-type rows "
               f"{'differ' if dt_diff else 'equal'}")
    return {"stored": len(st_rows), "built": len(bt_rows), "same_id": len(same_id), "renamed": len(renamed),
            "removed": len(removed), "added": len(added), "label_changed": len(lab_chg),
            "resolution_changed": len(res_chg), "columns_changed": dict(sorted(cols.items(), key=lambda x: -x[1])),
            "daytype_differs": dt_diff, "diff_signatures": diff_sigs, "summary": summary,
            "added_by_type": _by_type(added), "removed_by_type": _by_type(removed),
            "removed_examples": [_ex(r, st_out) for r in removed[:5]],
            "added_examples": [_ex(r, bt_out) for r in added[:5]]}


def _by_type(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Events by member type (an event with several types counts once for each)."""
    out: dict[str, int] = {}
    for r in rows:
        for t in (_types(r) or "none").split("+"):
            out[t] = out.get(t, 0) + 1
    return dict(sorted(out.items(), key=lambda x: -x[1]))


def _ex(r: dict[str, Any], out: dict[str, dict]) -> dict[str, Any]:
    o = out.get(r["event_id"], {})
    return {"event_id": r["event_id"], "et": _et(int(r["ts"])), "direction": r["direction"], "types": _types(r),
            "zone": [r.get("zone_lo"), r.get("zone_hi")], "label": o.get("label")}


def _same_val(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return (math.isnan(a) and math.isnan(b)) or a == b
    return a == b


def _et(t_ns: int) -> str:
    return datetime.fromtimestamp(t_ns / NS, timezone.utc).astimezone(ET).strftime("%H:%M:%S")


# --- the jump plot (Q-step12b (e)) -------------------------------------------------------------------------------------

def jump_plot(env: Env) -> Path:
    """2025-11-18 with every correction on (the `neutral` run): the weekly POC's old level ends at its first jump in the
    window, each later piece is a new level, and the zone the old one belonged to carries on with its other members (it
    never follows the POC onto price). Jump times, prices and the zone's edges are read from the build; the legend sits
    below the chart so it hides no band (owner 2026-11-04, Q-step12b (e))."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    built, _, _ = build(env, JUMP_DAY, "neutral")   # "neutral" has no switch off
    sf = built.sf
    a, z = et_ns(JUMP_DAY, time(12, 30)), et_ns(JUMP_DAY, time(14, 30))
    hhmm = lambda ns: datetime.fromtimestamp(ns / NS, ET).strftime("%H:%M:%S")
    mins = lambda ns: (np.asarray(ns, dtype=np.int64) - a) / NS / 60.0
    tape = sf.nq
    sl = tape.window(a, z)
    fig, ax = plt.subplots(figsize=(13, 8.5))
    ax.plot(mins(tape.ts[sl]), tape.price[sl], lw=0.6, color="#555", label="NQ trades")
    ts = np.arange(a, z, 5 * NS, dtype=np.int64)
    weekly = sorted((x for x in sf.nq_levels.values() if x.sub_type == "poc_weekly"), key=lambda x: x.band.start_ns)
    shown = []
    for x in weekly:
        b = [x.band.at(int(u)) for u in ts]
        lo = np.array([np.nan if v is None else v[0] for v in b])
        hi = np.array([np.nan if v is None else v[1] for v in b])
        if np.isfinite(lo).any():
            shown.append((x, lo, hi))
    colors = ["#c0392b", "#2471a3", "#7d3c98", "#1e8449", "#b9770e", "#17a589", "#884ea0"]
    jumps = [int(x.band.start_ns) for x, _, _ in shown if "@" in x.level_id and a <= int(x.band.start_ns) < z]
    first_jump = min(jumps) if jumps else None
    for k, (x, lo, hi) in enumerate(shown):
        ok = np.isfinite(lo)
        px = f"{np.nanmin(lo):,.2f}-{np.nanmax(hi):,.2f}"
        if "@" not in x.level_id or int(x.band.start_ns) < a:
            what = (f"weekly POC band {px}, level {x.level_id} (in force from before 12:30"
                    + (f"; ends at the {hhmm(first_jump)} jump without a cross" if first_jump else "") + ")")
        else:
            what = f"weekly POC band {px}, new level from the {hhmm(int(x.band.start_ns))} jump (fresh state, approach role)"
        ax.fill_between(mins(ts), lo, hi, where=ok, color=colors[k % len(colors)], alpha=0.45, lw=0, label=what)
    if first_jump is not None:
        old = shown[0][0]
        bands = {x.uid: x.band for x in sf.nq_levels.values()}
        zids = sorted(zid for zid, m in sf.book.zones_at(first_jump - NS).items() if old.uid in m)
        for zid in zids[:1]:
            e = [sf.book.edges(zid, int(u), bands) if sf.book.follow(zid, int(u)) == zid else None for u in ts]
            lo = np.array([np.nan if v is None else v[0] for v in e])
            hi = np.array([np.nan if v is None else v[1] for v in e])
            before = [v for u, v in zip(ts, e) if v is not None and u < first_jump]
            after = [v for u, v in zip(ts, e) if v is not None and u >= first_jump]
            fmt = lambda v: f"{v[0]:,.2f}-{v[1]:,.2f}"
            lab = (f"zone {zid} edges (holds the old weekly POC before the jump"
                   + (f": {fmt(before[-1])}" if before else "")
                   + ("; after it, its other members only" + (f": {fmt(after[0])}" if after else "") if after else
                      "; it ends at the jump") + ")")
            ax.plot(mins(ts), lo, color="#d68910", lw=1.4, ls="--", label=lab)
            ax.plot(mins(ts), hi, color="#d68910", lw=1.4, ls="--")
            env.echo(f"zone {zid}: before the jump {fmt(before[-1]) if before else '-'}, "
                     f"after {fmt(after[0]) if after else '-'}")
        for k, u in enumerate(sorted(jumps)):
            ax.axvline(mins(u), color="k", lw=0.8, ls=":",
                       label=(f"weekly POC jumps: {', '.join(hhmm(v) for v in sorted(jumps))}" if k == 0 else None))
    env.echo(f"weekly POC pieces in 12:30-14:30: {len(shown)}; jumps at "
             f"{', '.join(hhmm(v) for v in sorted(jumps)) or 'none'}")
    ax.set_xlabel("minutes after 12:30 ET")
    ax.set_ylabel("NQ")
    ax.set_title("2025-11-18: each weekly POC jump starts a new level (step 13, SPEC §13 Q-step12b (a), (e))")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.09), fontsize=8, ncol=1, frameon=False)
    out = report_dir(env) / "plots" / f"{JUMP_DAY.isoformat()}_weekly_poc_jump.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)
    env.echo(f"jump plot: {out}")
    return out


# --- the report ------------------------------------------------------------------------------------------------------

def _load(env: Env, run: str, day: date) -> dict[str, Any] | None:
    p = scratch(env) / run / "days" / f"{day.isoformat()}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def report(env: Env) -> Path:
    days = list(CHECK_DAYS)
    res = {r: {d: _load(env, r, d) for d in days} for r in RUNS}
    L: list[str] = ["# Step 13-0: the corrections check (development days)", "",
                    f"Built {datetime.now(ET):%Y-%m-%d %H:%M} ET from the stored v{STORED_VERSION} build's start states "
                    f"(scratch outputs in `{scratch(env)}`). Days: {', '.join(d.isoformat() for d in days)}.",
                    "Caveat: every day starts from v36's carried state (e.g. carried touch-history labels at 20 pts), "
                    "so differences are those of the day itself.", "",
                    "Neutral settings: " + ", ".join(f"`{k}` {v}" for k, v in NEUTRAL.items()) + ".", ""]
    missing = [(r, d) for r in RUNS for d in days if res[r][d] is None]
    if missing:
        L += [f"**Not complete:** {len(missing)} (run, day) chunks missing, e.g. {missing[:3]}.", ""]
    # the legacy check
    L += ["## 1. Legacy = v36 (neutral settings, every unparameterised fix off)", "",
          "| day | events | same id | removed | added | labels changed | resolution changed | row columns changed | "
          "day-type rows | end state (v36 fields) |", "|---|---|---|---|---|---|---|---|---|---|"]
    legacy_ok = True
    for d in days:
        x = res["legacy"][d]
        if x is None:
            L.append(f"| {d} | — | | | | | | | | |")
            legacy_ok = False
            continue
        df = x["diff"]
        ok = (df["removed"] == df["added"] == df["renamed"] == df["label_changed"] == df["resolution_changed"] == 0
              and not df["columns_changed"] and not df["daytype_differs"] and x["end_state_v36_fields_equal"]["equal"])
        legacy_ok &= ok
        L.append(f"| {d} | {df['stored']} -> {df['built']} | {df['same_id']} | {df['removed']} | {df['added']} | "
                 f"{df['label_changed']} | {df['resolution_changed']} | {len(df['columns_changed'])} "
                 f"{_top(df['columns_changed'])} | {'differ' if df['daytype_differs'] else 'equal'} | "
                 f"{'equal' if x['end_state_v36_fields_equal']['equal'] else 'differ: ' + ', '.join(x['end_state_v36_fields_equal']['differ'][:3])} |")
    L += ["", f"**Legacy reproduces v36 exactly: {'YES' if legacy_ok else 'NO'}.**", ""]
    # the per-run table
    L += ["## 2. Each run against v36", "",
          "| run | events stored -> built | same id | renamed | removed | added | labels changed | resolution changed | "
          "row columns changed (days) | own re-arms | formation touches | NQ node levels | relevance changes |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in RUNS:
        xs = [x for x in res[r].values() if x is not None]
        if not xs:
            continue
        s = lambda k: sum(x["diff"][k] for x in xs)
        colsets = {}
        for x in xs:
            for c, n in x["diff"]["columns_changed"].items():
                colsets[c] = colsets.get(c, 0) + n
        rel = sum(sum(x["relevance_changes"].values()) for x in xs)
        L.append(f"| {r} | {s('stored')} -> {s('built')} | {s('same_id')} | {s('renamed')} | {s('removed')} | "
                 f"{s('added')} | {s('label_changed')} | {s('resolution_changed')} | {len(colsets)} {_top(colsets)} | "
                 f"{sum(x['info'].get('own_arms', 0) for x in xs)} | "
                 f"{sum(x['info'].get('formation_touches', 0) for x in xs)} | {sum(x['node_levels'] for x in xs)} | "
                 f"{rel} |")
    L += ["", "The parameterised runs (join5 ... lookback5) are each one correction on top of `neutral`: compare them "
          "with the `neutral` row; the legacy+<fix> runs are one fix on top of `legacy`.", "",
          "Added / removed events by member type (all days; an event with several member types counts for each):", "",
          "| run | added by type | removed by type |", "|---|---|---|"]
    for r in RUNS:
        xs = [x for x in res[r].values() if x is not None]
        add, rem = {}, {}
        for x in xs:
            for k, v in x["diff"].get("added_by_type", {}).items():
                add[k] = add.get(k, 0) + v
            for k, v in x["diff"].get("removed_by_type", {}).items():
                rem[k] = rem.get(k, 0) + v
        L.append(f"| {r} | {_top(add, 8)} | {_top(rem, 8)} |")
    L.append("")
    # attribution of neutral's differences
    L += ["## 3. Neutral vs v36, traced to the unparameterised fixes", ""]
    tot, traced, inter = 0, {k: 0 for k in ALL_LEGACY}, 0
    for d in days:
        x = res["neutral"][d]
        if x is None:
            continue
        for sig in x["diff"]["diff_signatures"]:
            tot += 1
            who = [k for k in ALL_LEGACY if res[f"legacy+{k}"][d] is not None
                   and sig in set(res[f"legacy+{k}"][d]["diff"]["diff_signatures"])]
            for k in who:
                traced[k] += 1
            if not who:
                inter += 1
    L += [f"Events of `neutral` that differ from v36 (added, removed, or with a changed label / resolution): {tot}.",
          "", "| fix | also differs with that fix alone |", "|---|---|"]
    L += [f"| {k} ({dg.LEGACY[k][0].split(':')[1]}) | {n} |" for k, n in traced.items()]
    L += ["", f"Not traced to any single fix (interactions of two or more): {inter}.", ""]
    # determinism
    L += ["## 4. Determinism (the committed configuration, the same day built twice)", "",
          "| day | state digest | events | labels | row columns |", "|---|---|---|---|---|"]
    det_ok = True
    for d in days:
        a, b = res["start"][d], res["start_again"][d]
        if a is None or b is None:
            L.append(f"| {d} | — | | | |")
            det_ok = False
            continue
        same = a["state_digest"] == b["state_digest"]
        rows_same = _same_files(scratch(env) / "start", scratch(env) / "start_again", d)
        det_ok &= same and rows_same
        L.append(f"| {d} | {'same' if same else 'DIFFER'} | {a['diff']['built']} / {b['diff']['built']} | "
                 f"{'same' if rows_same else 'DIFFER'} | {'same' if rows_same else 'DIFFER'} |")
    L += ["", f"**Deterministic: {'YES' if det_ok else 'NO'}.**", ""]
    # level jumps
    L += _jump_section(res, days)
    out = report_dir(env) / "corrections_check.md"
    atomic_write_text(out, "\n".join(L) + "\n")
    env.echo(f"report: {out}")
    return out


def _same_files(a: Path, b: Path, day: date) -> bool:
    for kind in ("touches", "outcomes", "daytype"):
        x, y = _read(a, kind, day), _read(b, kind, day)
        if len(x) != len(y) or any(set(p) != set(q) or any(not _same_val(p[k], q[k]) for k in p) for p, q in zip(x, y)):
            return False
    return True


def _top(cols: dict[str, int], n: int = 4) -> str:
    if not cols:
        return ""
    top = sorted(cols.items(), key=lambda x: -x[1])[:n]
    return "(" + ", ".join(f"{c} {k}" for c, k in top) + ")"


def _jump_section(res: dict, days: list[date]) -> list[str]:
    """Owner 2026-11-09: jumps by level type per session (median and max), RTH vs overnight; VWAP lines/bands or node
    bands jumping more than JUMP_FEW times in a session on any check day -> stop and report with examples."""
    L = ["## 5. Level jumps (the `start` run: every correction at its committed value)", ""]
    per: dict[tuple[str, str], list[int]] = {}
    examples: dict[str, list[str]] = {}
    flagged = []
    for d in days:
        x = res["start"][d]
        if x is None:
            continue
        o, c = et_ns(d, time(9, 30)), et_ns(d, time(16, 0))
        counts: dict[tuple[str, str], int] = {}
        for j in x["info"].get("level_jumps", []):
            key = f"{j['root']} {j['sub_type']}"
            part = "RTH" if o <= j["t"] < c else "overnight"
            counts[(key, part)] = counts.get((key, part), 0) + 1
            examples.setdefault(key, []).append(f"{d} {_et(j['t'])}{' (session start)' if j.get('at_session_start') else ''}")
        for m, js in (x["info"].get("node_jumps") or {}).items():
            key = f"{m} node band"
            for _, t in js:
                part = "RTH" if o <= t < c else "overnight"
                counts[(key, part)] = counts.get((key, part), 0) + 1
                examples.setdefault(key, []).append(f"{d} {_et(t)}")
        for (key, part), n in counts.items():
            per.setdefault((key, part), [])
        for kp in per:
            per[kp].append(counts.get(kp, 0))
        for key in {k for k, _ in counts}:
            n = counts.get((key, "RTH"), 0) + counts.get((key, "overnight"), 0)
            if n > JUMP_FEW and ("vwap" in key or "node band" in key):
                flagged.append(f"{d} {key}: {n}")
    n_days = sum(1 for d in days if res["start"][d] is not None)
    L += ["| level type | part | median per session | max per session |", "|---|---|---|---|"]
    for (key, part), v in sorted(per.items()):
        v = v + [0] * (n_days - len(v))
        L.append(f"| {key} | {part} | {median(v):g} | {max(v)} |")
    if not per:
        L.append("| (none) | | 0 | 0 |")
    L += ["", "Examples (first 5 per type): " + "; ".join(f"{k}: {', '.join(v[:5])}" for k, v in sorted(examples.items()))
          if examples else "No level jumped.", ""]
    if flagged:
        L += [f"**STOP (owner 2026-11-09): VWAP lines/bands or node bands jump more than {JUMP_FEW} times in a session "
              f"on a check day: {'; '.join(flagged)}. Smooth drift must not become new levels: report to the owner "
              "with these examples before the wrap-up.**", ""]
    else:
        L += [f"No VWAP line/band or node band jumps more than {JUMP_FEW} times in a session on any check day.", ""]
    return L


# --- the job ---------------------------------------------------------------------------------------------------------

def chunk_ids() -> list[str]:
    return [f"{r}:{d.isoformat()}" for r in RUNS for d in CHECK_DAYS] + [PLOT_CHUNK, REPORT_CHUNK]


class CheckJob:
    """`nqbot run step13_check --until HH:MM`: one (run, day) per chunk in its own process, the jump plot, the report.
    Reads stored data only, writes only the scratch folder and the report; holds both job locks so no pull writes the
    data it reads (and the build pin can't move while it runs)."""

    name = JOB

    def __init__(self) -> None:
        from nqbot.jobs.lock import DATABENTO, SKYLIT

        self.locks = (SKYLIT, DATABENTO)

    def _env(self, ctx: Any) -> Env:
        from nqbot import calendar as cal_mod
        from nqbot.config import load_features

        return Env(settings=ctx.settings, features=load_features(), paths=ctx.paths, log=ctx.log, echo=ctx.log.info,
                   cal=cal_mod.load())

    def chunks(self, ctx: Any) -> list[str]:
        return chunk_ids()

    def run_chunk(self, chunk_id: str, ctx: Any) -> dict[str, Any] | None:
        from nqbot.checks.step8b_disloc import MIN_FREE_MB, free_mb, run_cli_in_child
        from nqbot.jobs.runner import StopChunk

        if chunk_id == REPORT_CHUNK:
            report(self._env(ctx))
            return None
        mb = free_mb()
        if mb is not None and mb < MIN_FREE_MB:
            raise StopChunk(f"{mb} MB free (< {MIN_FREE_MB}) before {chunk_id}")
        if chunk_id == PLOT_CHUNK:
            peak = run_cli_in_child(["step13", "jump-plot"])
        else:
            run, d = chunk_id.rsplit(":", 1)
            peak = run_cli_in_child(["step13", "check-day", d, "--run", run])
        ctx.log.info("%s: %s process peak %s GB, %s MB free before it", self.name, chunk_id, peak, mb)
        return {"peak_gb": peak}
