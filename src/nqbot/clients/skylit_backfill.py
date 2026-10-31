"""The step-10 Skylit backfill (SPEC §3.1 Backfill, Dark pools, §12 step 10): plan, credit budget, the resumable
job `skylit_backfill` and its coverage report.

`nqbot skylit plan [--approve-credits N]` freezes what to pull and the owner's approval (after the step-7 report
and the pack purchase, decision 2 in docs/presd_fixes.md); `nqbot run skylit_backfill --until 07:30` pulls it,
oldest day first, one date per chunk:
- the Skylit RTH windows of every weekday of the ledger window up to yesterday (holiday probe, early closes to
  early_close_et plus a probe window: skylit_pull.session_windows). A window is pulled only while it is still
  inside Skylit's 365-day reach, checked just before its call; one that isn't is recorded as lost;
- that date's QQQ/SPY dark-pool prints (the window plus dp_lookback_days before month 1): 31-day range calls when
  the step-7 darkpool check showed they return the same prints as one-date calls (owner 2026-10-05), else one
  date per call; dates near the 365-day edge always one date per call;
  a refused (out-of-reach) or empty trading date is recorded, never cached as an empty day;
- last, one Tempest /v1/vol/history call (QQQ, SPY, plus NDXP/SPXW/VIX if served) from warmup_sessions + 2
  sessions before month 1.
Cached step-7 pulls are reused (sample days, dark-pool dates); sample days pulled before skylit_max_strikes was
raised are replaced by their `strike_window_raise` copies, or pulled again (presd_fixes issue 10).

Credits (CLAUDE.md rule 2): every call is checked against the approved amount and the known balance first; if
either can't cover it, the chunk stops cleanly (status `credits`) and the job resumes from it after a top-up or a
new plan. Spending is counted at max(charged, list price) per call, so the approval is never exceeded. The balance
check keeps skylit_alongside_reserve_credits for the step-7 checks that run alongside without the lock (decision 2).

Failing windows (decision 4, presd_fixes D1): a window (or dark-pool date) that keeps failing is recorded in
failed.json, its answer kept in skylit/failed/, and the job carries on; it is never pulled again in the main pass.
skylit_max_failures_in_row failures in a row stop the job cleanly (status `failures`). Every run
tries each one again at its start and at the end of the night, oldest first (owner 2026-10-06), so nothing leaves the
365-day reach while it waits: a refunded failure gets one more call, a paid one (its answer couldn't be converted or
stored) is only converted again from its kept answer, for free. The same holds for dark-pool dates (every page
answered is kept) and the Tempest call. A paid window, dark-pool date or Tempest range whose kept copy is itself cut
off, out of order or unreadable gets one paid re-pull while in reach, within skylit_dropped_retry_credits_per_night
(owner 2026-10-06). A failure whose window (or dark-pool date) has left the reach is recorded as lost. A window whose connection dropped (maybe charged, D7) gets up to
skylit_dropped_extra_tries more tries while in reach, in rounds at the end of the night (oldest first, waits between
rounds, each try sent once), within skylit_dropped_retry_credits_per_night (owner 2026-10-06). What is still missing,
and every such try with its credits, is listed in coverage.md.

Coverage report (counts only, §7 rule 2b): <reports_dir>/step10/coverage.{md,json}, rewritten after every chunk.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pyarrow.parquet as pq

from nqbot import months as months_mod
from nqbot.checks import step7
from nqbot.clients import skylit_credits as credits
from nqbot.clients.skylit import (DP_PAGE_LIMIT, DP_RANGE_MAX_DAYS, DarkPoolFailed, DarkPoolPull, OutOfCredits,
                                  SkylitError, dark_pool_prints, vol_history_cost)
from nqbot.clients.skylit_pull import (ET, NEVER_SKIPPED, REACH_ANSWER_TOLERANCE, SKYLIT_HISTORY, SessionResult,
                                       Window, full_day_windows, make_client, pull_session, pull_window,
                                       range_params_from_settings, sample_days, session_windows, store_window,
                                       window_reachable)
from nqbot.clients.skylit_store import CacheMismatch, SkylitStore, _norm, read_meta, vol_history_to_table, write_parquet
from nqbot.jobs.lock import SKYLIT
from nqbot.jobs.runner import Ctx, StopChunk, record_plan
from nqbot.storage import Paths, append_jsonl, atomic_write_bytes, atomic_write_json, atomic_write_text, replace_retry

JOB_NAME = "skylit_backfill"
TEMPEST_CHUNK = "tempest"  # last: a Tempest problem never holds up the time-critical days
# First (owner 2026-10-07): every dark-pool date, oldest first, before the range days, in case Skylit's dark-pool
# history (from dp_history_start) is a rolling window that loses a day each day; ~26 pages, a few minutes.
DP_CHUNK = "dp_"
TEMPEST_ALWAYS = ("QQQ", "SPY")  # §12 step 10
TEMPEST_IF_SERVED = ("NDXP", "SPXW", step7.VIX)  # §12 step 10: if step 7 found them served
RAISE = "strike_window_raise"
# §3.1 Backfill: a chunk whose call gave up after the client's own retries (5xx, network) is tried again after these
# waits (1, 5, 15 minutes), in the same run: an overnight blip must not end the night while the oldest windows keep
# leaving the reach.
CHUNK_RETRY_SEC = (60, 300, 900)
TRANSIENT_CODES = frozenset({"gave_up"})
WAIT_STEP_SEC = 5  # §11 Overnight jobs: a retry wait checks ctx.should_stop() this often (nqbot stop, --until)
# Dark-pool errors raised after pages were paid for (the prints are lost with the error): never called again.
DP_PAID_CODES = frozenset({"page_order", "no_cursor", "page_cap"})
# A 31-day answer kept in darkpool/range/ that couldn't be split: split again from it for free.
RANGE_KEPT_CODES = frozenset({"range_date", "range_split_failed"})
# §3.1 Backfill (owner 2026-10-06): waits before each round of extra tries for dropped windows (1, 5, 15 minutes)
DROPPED_ROUND_WAIT_SEC = (60, 300, 900)
DROPPED = "dropped"
# §3.1 Backfill: pages reserved before a dark-pool date starts: twice the most pages any date has needed, so a stop mid-date
# (which would throw paid pages away) needs a date with more than twice the largest seen.
DP_PAGE_RESERVE_FACTOR = 2
# §3.1 Dark pools: a range call starts only on a date at least this far inside the reach (an out-of-reach answer to a
# range call couldn't be told apart per date; the dates at the edge go one per call, which can).
DP_RANGE_REACH_MARGIN = timedelta(days=1)
NOMINAL = {"/v1/historical/range": credits.RANGE_CALL, "/v1/dark-pool/trades": credits.DP_TRADES_PAGE,
           "/v1/historical": credits.HISTORICAL_CALL}


class BackfillRefused(Exception):
    pass


class TempestFailed(SkylitError):
    """A paid Tempest answer that couldn't be used, kept in skylit/failed/ (presd_fixes D1). `complete`: the kept copy
    is the whole answer (converted again for free); False: it is unreadable (one paid re-pull, owner 2026-10-06)."""

    def __init__(self, code: str, message: str, raw_path: Path | None, complete: bool):
        super().__init__(200, code, message, charged=True)
        self.raw_path, self.complete = raw_path, complete


# --- plan ------------------------------------------------------------------------------------------------

@dataclass
class BackfillPlan:
    first: date
    last: date  # the window's last date pulled: min(window end, yesterday)
    days: list[date]  # Skylit weekdays still (at least partly) inside the reach
    lost_days: list[date]  # weekdays already wholly beyond the 365-day reach when planned
    dp_dates: list[date]
    tempest_from: date
    tempest_symbols: list[str]
    max_strikes: int | str
    created: str = ""
    approved_credits: int | None = None
    estimate_total: int = 0
    dp_range: bool = False  # dark pools by 31-day calls (the step-7 range check matched when planned)

    @property
    def plan_id(self) -> str:
        return self.created

    def to_json(self) -> dict[str, Any]:
        iso = lambda ds: [d.isoformat() for d in ds]  # noqa: E731
        return {"created": self.created, "approved_credits": self.approved_credits, "max_strikes": self.max_strikes,
                "window": [self.first.isoformat(), self.last.isoformat()], "days": iso(self.days),
                "lost_days": iso(self.lost_days), "dp_dates": iso(self.dp_dates),
                "tempest": {"from": self.tempest_from.isoformat(), "to": self.last.isoformat(),
                            "symbols": self.tempest_symbols},
                "estimate_total": self.estimate_total, "dp_range": self.dp_range}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> BackfillPlan:
        ds = lambda xs: [date.fromisoformat(x) for x in xs]  # noqa: E731
        return cls(first=date.fromisoformat(d["window"][0]), last=date.fromisoformat(d["window"][1]),
                   days=ds(d["days"]), lost_days=ds(d["lost_days"]), dp_dates=ds(d["dp_dates"]),
                   tempest_from=date.fromisoformat(d["tempest"]["from"]), tempest_symbols=list(d["tempest"]["symbols"]),
                   max_strikes=d["max_strikes"], created=d["created"], approved_credits=d["approved_credits"],
                   estimate_total=d.get("estimate_total", 0), dp_range=d.get("dp_range", False))


def tempest_symbols(through: dict[str, Any] | None) -> list[str]:
    """QQQ and SPY always; NDXP, SPXW and VIX when /v1/vol/symbols lists them."""
    served = set(through or {})
    return list(TEMPEST_ALWAYS) + [s for s in TEMPEST_IF_SERVED if s in served]


def margin_min(settings: dict[str, Any]) -> float:
    return float(settings["skylit_reach_margin_min"])


def plan_backfill(env: step7.Env, now: datetime, symbols: list[str]) -> BackfillPlan:
    """What step 10 pulls if it starts at `now` (one code path for the plan and the step-7 report)."""
    first, end = months_mod.span(env.settings)
    last = min(end, now.date() - timedelta(days=1))  # complete days only: stops at yesterday
    if last < first:
        raise BackfillRefused(f"the ledger window {first}..{end} has no complete day yet")
    margin = margin_min(env.settings)
    weekdays = step7._weekdays(first, last)
    # a day already (partly) stored stays in the plan whatever its reach now: a re-plan never calls it lost
    reachable = [d for d in weekdays if _day_reachable(d, now, margin, env.cal) or env.store.stored_windows(d)]
    lost = [d for d in weekdays if d not in set(reachable)]
    # dark pools only from Skylit's history start: earlier dates answer an empty, charged 200 (step 7, 2026-10-06)
    dp_dates = step7._weekdays(max(step7._f4_lower(env, first), step7.dp_history_start(env)), last)
    return BackfillPlan(first=first, last=last, days=reachable, lost_days=lost, dp_dates=dp_dates,
                        tempest_from=step7._tempest_start(env), tempest_symbols=symbols,
                        max_strikes=env.settings["skylit_max_strikes"], dp_range=step7.dp_range_ok(env.paths))


def _day_reachable(day: date, now: datetime, margin: float, cal: dict[str, Any] | None) -> bool:
    windows = session_windows(day, cal)
    return bool(windows) and window_reachable(windows[-1], now, margin)


def _windows_by_hhmm(day: date, cal: dict[str, Any] | None) -> dict[str, Window]:
    out = {w.hhmm: w for w in full_day_windows(day)}
    out.update({w.hhmm: w for w in session_windows(day, cal)})
    return out


def _stored_params(store: SkylitStore, day: date, hhmm: str, base: Path | None = None) -> dict[str, Any] | None:
    b, s = store.range_paths(day, hhmm, base)
    if not (b.exists() and s.exists()):
        return None  # absent, or a partial write (pull_window quarantines and re-pulls it)
    return read_meta(b).get("params")


def param_state(env: step7.Env, day: date) -> dict[str, tuple[str, dict[str, Any]]]:
    """Each stored normal-tree window of the day: (match | strikes_only | other, the stored parameters)."""
    by_hhmm = _windows_by_hhmm(day, env.cal)
    out = {}
    for hhmm in env.store.stored_windows(day):
        have = _stored_params(env.store, day, hhmm)
        w = by_hhmm.get(hhmm)
        if have is None:
            continue
        if w is None:
            out[hhmm] = ("other", have)
            continue
        want = _norm(range_params_from_settings(env.settings, w))
        diff = {k for k in set(want) | set(have) if want.get(k) != have.get(k)}
        out[hhmm] = ("match" if not diff else "strikes_only" if diff == {"maxStrikes"} else "other", have)
    return out


def refuse_hard_mismatches(env: step7.Env, days: list[date], dp_dates: list[date] = ()) -> None:
    """Any cached window pulled with other parameters than maxStrikes, or a cached dark-pool date pulled with other
    parameters, stops the job before it starts (never a CacheMismatch mid-backfill)."""
    samples = sample_days(env.paths)
    bad = [f"{d} {hhmm}: {have}" for d in days if env.store.stored_windows(d)
           for hhmm, (state, have) in param_state(env, d).items()
           if state == "other" or (state == "strikes_only" and d not in samples)]
    for d in dp_dates:
        for path in (env.store.dp_trades_path(d), env.store.dp_trades_path(d, step7._lookback_base(env))):
            try:
                step7.cached_or_mismatch(path, step7._dp_params(env, d))
            except CacheMismatch as e:
                bad.append(f"dark pools {d}: {e}")
    if bad:
        raise BackfillRefused("cached data was pulled with other parameters (only a step-7 sample day may differ, "
                              "and only in maxStrikes; a backfill day pulled with an older skylit_max_strikes would "
                              "mix strike sets, §3.1): " + "; ".join(bad[:5]) + (" ..." if len(bad) > 5 else "")
                              + ". Ask the owner.")


def max_pages_stored(store: SkylitStore) -> int:
    """The most pages any stored dark-pool date needed (at least 1)."""
    folder = store.root / "darkpool" / "trades"
    pages = [int(read_meta(p).get("pages") or 1) for p in folder.glob("*.parquet")] if folder.exists() else []
    return max([1, *pages])


def _raise_copy_ok(env: step7.Env, day: date, hhmm: str, w: Window, base: Path) -> bool:
    try:
        return env.store.range_cached(day, hhmm, range_params_from_settings(env.settings, w), base)
    except CacheMismatch:
        return False


def settled(paths: Paths) -> tuple[set[date], set[date]]:
    """(Skylit days, dark-pool dates) the backfill already finished: their no-data windows, lost windows and
    refused or empty dark-pool dates are never asked again, so they cost nothing more."""
    folder = job_dir(paths) / "days"
    sky, dp = set(), set()
    for p in sorted(folder.glob("*.json")) if folder.exists() else []:
        rec = json.loads(p.read_text(encoding="utf-8"))
        d = date.fromisoformat(rec["date"])
        if "skylit" in rec:
            sky.add(d)
        if "dp" in rec:
            dp.add(d)
    return sky, dp


def windows_todo(env: step7.Env, plan: BackfillPlan, now: datetime) -> int:
    """Range calls still needed: reachable windows not cached with the current parameters (a raised sample day's
    matching strike_window_raise copy counts as cached), on days the backfill hasn't finished."""
    margin = margin_min(env.settings)
    raise_base = env.store.base(RAISE, pulled=f"max{env.settings['skylit_max_strikes']}")
    done, _ = settled(env.paths)
    failed = load_failed(env.paths)
    n = sum((1 if retry_calls(r) or needs_repull(r) else dropped_tries_left(r, env.settings))
            for r in failed.values()
            if r["kind"] == "window" and r["state"] == "failed"
            and r["hhmm"] in _windows_by_hhmm(date.fromisoformat(r["date"]), env.cal)
            and window_reachable(_windows_by_hhmm(date.fromisoformat(r["date"]), env.cal)[r["hhmm"]], now, margin))
    for d in plan.days:
        if d in done:
            continue
        states = param_state(env, d) if env.store.stored_windows(d) else {}
        for w in session_windows(d, env.cal):
            if not window_reachable(w, now, margin) or window_key(d, w.hhmm) in failed:
                continue
            state = states.get(w.hhmm, ("absent", {}))[0]
            if state == "match" or (state == "strikes_only" and _raise_copy_ok(env, d, w.hhmm, w, raise_base)):
                continue
            n += 1
    return n


def tempest_files(store: SkylitStore, start: date) -> list[Path]:
    """The backfill's own Tempest pulls starting at `start` or continuing it (each covers the tail the last missed)."""
    return [p for p in store.vol_files() if read_meta(p).get("backfill_from") == start.isoformat()]


def tempest_todo(store: SkylitStore, plan: BackfillPlan) -> tuple[date, date] | None:
    """The Tempest range still to pull: from the day after the last stored backfill row range to plan.last."""
    ends = [date.fromisoformat(read_meta(p)["params"]["to"]) for p in tempest_files(store, plan.tempest_from)]
    start = max(ends) + timedelta(days=1) if ends else plan.tempest_from
    return (start, plan.last) if start <= plan.last else None


def dp_reach_edge(now: datetime) -> date:
    """The last ET date a refusal may mean "out of reach" (§3.1: 365 days back, with the answer tolerance)."""
    return (now.astimezone(timezone.utc) - SKYLIT_HISTORY + REACH_ANSWER_TOLERANCE).astimezone(ET).date()


def dp_range_covered(paths: Paths) -> dict[date, str]:
    """Dates a range call already settled (incl. empty trading days, which aren't cached): never asked again."""
    folder = job_dir(paths) / "dp_range"
    out: dict[date, str] = {}
    for f in sorted(folder.glob("*.json")) if folder.exists() else []:
        out.update({date.fromisoformat(d): st for d, st in json.loads(f.read_text(encoding="utf-8"))["dates"].items()})
    return out


def kept_range_covering(env: step7.Env, d: date) -> tuple[date, date] | None:
    """A range answer already kept in darkpool/range/ whose dates include d (paid for; reusing it is free)."""
    folder = env.store.root / "darkpool" / "range"
    for f in sorted(folder.glob("*.parquet")) if folder.exists() else []:
        start, end = (date.fromisoformat(x) for x in f.stem.split("_"))
        if start <= d <= end:
            return start, end
    return None


def dp_todo(env: step7.Env, plan: BackfillPlan) -> list[date]:
    """Dark-pool dates still to ask for: not cached, not settled by a chunk, a range call or the step-7 probe."""
    _, dp_done = settled(env.paths)
    covered = dp_range_covered(env.paths)
    failed = load_failed(env.paths)
    return [d for d in plan.dp_dates if not env.store.dp_trades_path(d).exists() and d not in dp_done
            and d not in covered and dp_key(d) not in failed and not step7.has_lookback_copy(env, d)
            and not (env.store.root / "darkpool" / "probe" / f"{d.isoformat()}.json").exists()]


def dp_retries_todo(paths: Paths) -> int:
    """Failed dark-pool dates the retry passes will call once more (one date per call): refunded ones, and paid ones
    whose kept copy is cut off or unreadable (their one re-pull)."""
    return sum(1 for r in load_failed(paths).values() if r["kind"] == "dp" and r["state"] == "failed"
               and (retry_calls(r) or needs_repull(r)))


def dp_range_groups(todo: list[date], edge: date) -> list[list[date]]:
    """The calls the job makes, oldest first: one date per call up to the reach edge (+ margin), then runs of
    consecutive to-do weekdays spanning at most 31 calendar days (§3.1). A run ends at any weekday not to do (cached,
    settled or probed), so a range never pays for a date already paid for (rule 2)."""
    todo_set = set(todo)
    groups: list[list[date]] = []
    inside = edge + DP_RANGE_REACH_MARGIN
    for d in sorted(todo):
        g = groups[-1] if groups else None
        if g and d > inside and g[0] > inside and d <= g[0] + timedelta(days=DP_RANGE_MAX_DAYS - 1) \
                and all(x in todo_set for x in step7._weekdays(g[-1], d)):
            g.append(d)
        else:
            groups.append([d])
    return groups


def dp_group_pages(env: step7.Env, group: list[date], per_date: float, ppd: float | None, edge: date) -> float:
    """Pages one call of the group needs (the plan's estimate and the job's reserve use the same number)."""
    if (len(group) == 1 and group[0] <= edge + DP_RANGE_REACH_MARGIN) or ppd is None:
        return per_date * len(group)
    return max(1, math.ceil(ppd * sum(env.trading_day(d) for d in group) / DP_PAGE_LIMIT))


def dp_pages_estimate(env: step7.Env, plan: BackfillPlan, todo: list[date], now: datetime) -> int:
    per_date = step7._pages_per_date(env)
    ppd = step7.dp_prints_per_date(env.paths)
    if not plan.dp_range or ppd is None:
        return int(math.ceil(len(todo) * per_date))
    edge = dp_reach_edge(now)
    return int(math.ceil(sum(dp_group_pages(env, g, per_date, ppd, edge) for g in dp_range_groups(todo, edge))))


def estimate(env: step7.Env, plan: BackfillPlan, now: datetime) -> credits.Estimate:
    todo_dp = dp_todo(env, plan)
    est = (credits.range_estimate(f"step-10 backfill {plan.first}..{plan.last}", windows_todo(env, plan, now))
           .add("/v1/dark-pool/trades pages" + (" (31-day calls)" if plan.dp_range else " (one date per call)"),
                dp_pages_estimate(env, plan, todo_dp, now)
                + int(math.ceil(dp_retries_todo(env.paths) * step7._pages_per_date(env))), credits.DP_TRADES_PAGE))
    todo = tempest_todo(env.store, plan)
    if todo:
        est.add_total(f"/v1/vol/history {todo[0]}..{todo[1]} ({', '.join(plan.tempest_symbols)})", 1,
                      vol_history_cost(len(plan.tempest_symbols), *todo))
    return est


def job_dir(paths: Paths) -> Path:
    return paths.data_dir / "jobs" / JOB_NAME


def plan_path(paths: Paths) -> Path:
    return job_dir(paths) / "plan.json"


def failed_path(paths: Paths) -> Path:
    return job_dir(paths) / "failed.json"


def window_key(day: date, hhmm: str) -> str:
    return f"{day.isoformat()} {hhmm}"


def dp_key(d: date) -> str:
    return f"dp {d.isoformat()}"


def tempest_key(start: date, end: date) -> str:
    return f"tempest {start.isoformat()}..{end.isoformat()}"


def load_failed(paths: Paths) -> dict[str, dict[str, Any]]:
    """The failure record (presd_fixes D1): key -> {kind: window | dp | tempest, date, hhmm, status, code, message,
    charged, raw, at, retries, last_retry, state: failed | recovered | no_coverage | lost; dark pools and Tempest
    also `complete` (the kept copy is the whole answer) and `repull` (its one paid re-pull)}."""
    path = failed_path(paths)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def still_failed(failed: dict[str, dict[str, Any]], key: str) -> bool:
    return failed.get(key, {}).get("state") == "failed"


def retry_calls(rec: dict[str, Any]) -> bool:
    """Does a retry pass make a paid call for it? Not for an answer already paid for: that one is only converted (or
    split) again from its kept copy, for free (rule 2). The estimate counts one pass. A dropped window: see
    dropped_tries_left."""
    return not rec["charged"]


# Kept copies read once per (path, kind), reused while the file's size and modified time are unchanged (coverage
# is rewritten after every chunk; a 31-day failure shares one file across its dates). Never changes what is recorded.
_KEPT_CACHE: dict[tuple[str, str], tuple[tuple[int, int], Any]] = {}


def _cached(path: Path, what: str, read: Callable[[], Any]) -> Any:
    try:
        st = path.stat()
    except OSError:
        return None
    stamp = (st.st_size, st.st_mtime_ns)
    hit = _KEPT_CACHE.get((str(path), what))
    if hit is not None and hit[0] == stamp:
        return hit[1]
    value = read()
    _KEPT_CACHE[(str(path), what)] = (stamp, value)
    return value


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None


def kept_copy(rec: dict[str, Any]) -> Any | None:
    """The kept answer of a paid failure, or None if there is none or the copy itself can't be read."""
    if not rec.get("raw"):
        return None
    path = Path(rec["raw"])
    if rec["kind"] == "dp":
        return _cached(path, "dp", lambda: step7.read_kept_dp(path))
    return _cached(path, "json", lambda: _read_json(path))


def kept_range_prefix(rec: dict[str, Any]) -> tuple[list[dict[str, Any]], frozenset[date]] | None:
    """A cut-off 31-day dark-pool answer: (its prints, the dates its pages fully cover). Pages come in time order, so
    every date before the last print's date is whole; only the rest is pulled again, one date per call (owner
    2026-10-06: never pay again for prints already held). None if it isn't one, or its pages can't be trusted."""
    if rec["kind"] != "dp" or rec.get("complete", True) or not rec.get("raw"):
        return None
    kept = kept_copy(rec)
    if kept is None or "date_start" not in (kept.get("span") or {}) or not kept["pages"]:
        return None

    def read() -> tuple[list[dict[str, Any]], frozenset[date]] | None:
        span = kept["span"]
        try:
            prints = dark_pool_prints(kept["pages"], "..".join(span.values()), span)
        except DarkPoolFailed:  # out of order or an unreadable print: nothing in it is trusted
            return None
        if not prints:
            return None
        start, last = date.fromisoformat(span["date_start"]), step7.dp_trade_date(prints[-1])
        return prints, frozenset(start + timedelta(days=i) for i in range((last - start).days))

    return _cached(Path(rec["raw"]), "prefix", read)


def copy_usable(rec: dict[str, Any]) -> bool:
    """A paid failure whose kept answer can be converted again for free (at any age)."""
    if not (rec["charged"] and rec.get("raw")):
        return False
    if rec["kind"] == "window":  # its body is only read by the retry itself (large); an unreadable one is marked then
        return bool(rec.get("complete", True))
    return bool(rec.get("complete", True)) and kept_copy(rec) is not None


def needs_repull(rec: dict[str, Any]) -> bool:
    """Owner 2026-10-06: a paid window, dark-pool date or Tempest range whose kept copy is cut off, out of order or
    unreadable (not a conversion bug: that one is only converted again, for free) gets one paid re-pull while still in
    reach, within skylit_dropped_retry_credits_per_night; coverage.md lists it."""
    return (rec["state"] == "failed" and rec["charged"] and bool(rec.get("raw")) and not rec.get("repull")
            and not copy_usable(rec))


def complete_of(exc: Exception) -> dict[str, Any]:
    """The `complete` field for a failure record: is the kept copy the whole answer? (Omitted when it is.)"""
    if hasattr(exc, "complete"):
        return {"complete": bool(exc.complete)}
    if getattr(exc, "code", None) == "undecodable":  # a paid body that couldn't be read: the copy is unusable
        return {"complete": False}
    return {}


def dropped_tries_left(rec: dict[str, Any], settings: dict[str, Any]) -> int:
    """Extra tries a dropped window still has (owner 2026-10-06); 0 for anything else."""
    if rec.get("code") != DROPPED or rec["kind"] != "window" or rec["state"] != "failed":
        return 0
    return max(0, int(settings["skylit_dropped_extra_tries"]) - len(rec.get("dropped_tries", [])))


def effective_state(env: step7.Env, rec: dict[str, Any], now: datetime) -> str:
    """The record's state, with a failure that can no longer be called (its window or date has left the 365-day
    reach) shown as lost even before the next retry pass marks it."""
    if rec["state"] != "failed" or copy_usable(rec):  # a kept answer converts at any age
        return rec["state"]
    if rec["kind"] == "tempest":  # /v1/vol/history isn't bound by the 365-day reach
        return rec["state"]
    d = date.fromisoformat(rec["date"])
    if rec["kind"] == "dp" and rec["charged"] and not rec.get("raw") and kept_range_covering(env, d) is not None:
        return rec["state"]  # its 31-day answer is kept in darkpool/range/: split again for free at any age
    prefix = kept_range_prefix(rec)
    if prefix is not None and d in prefix[1]:
        return rec["state"]  # a cut-off 31-day answer that already holds this whole date
    if rec["kind"] == "window":
        w = _windows_by_hhmm(d, env.cal).get(rec["hhmm"])
        gone = w is None or not window_reachable(w, now, margin_min(env.settings))
    else:
        gone = d <= dp_reach_edge(now)
    return "lost" if gone else "failed"


def lost_days_path(paths: Paths) -> Path:
    return paths.data_dir / "jobs" / JOB_NAME / "lost_days.json"


def fully_lost_days(paths: Paths) -> set[date]:
    """Days the backfill could not get any window of (read by the Databento plan: it doesn't buy them)."""
    path = lost_days_path(paths)
    if not path.exists():
        return set()
    return {date.fromisoformat(d) for d in json.loads(path.read_text(encoding="utf-8"))["fully_lost"]}


def chunk_ids(plan: BackfillPlan) -> list[str]:
    """The job's chunk ids for `plan`: dark-pool dates, then range days, then Tempest (job and re-plan alike)."""
    return ([f"{DP_CHUNK}{d.isoformat()}" for d in sorted(plan.dp_dates)]
            + [d.isoformat() for d in sorted(plan.days)] + [f"{TEMPEST_CHUNK}_{plan.last.isoformat()}"])


def make_plan(env: step7.Env, approved: int | None) -> BackfillPlan:
    """`nqbot skylit plan`: print the estimate, balance and shortfall; refuse over 5,000 credits without an
    approval at least as large (rule 2); write the plan."""
    now = env.now()
    from nqbot.setup_ssd import check_month1, ledger_fixed

    ok, reasons = step7.strike_window_check(env)  # decision 3: no override flag
    if not ok:
        raise BackfillRefused("the strike-window check hasn't passed (every backfill day would lose its edge strikes "
                              "for good): " + "; ".join(reasons))
    if not ledger_fixed(env.paths):  # step 10 starts: is the ledger's month-1 merge still right (§12 step 4b)?
        check_month1(env.settings, now, env.cal)
    through, _ = step7._history_through(env.client.vol_symbols())  # free
    plan = plan_backfill(env, now, tempest_symbols(through))
    refuse_hard_mismatches(env, plan.days, plan.dp_dates)
    est = estimate(env, plan, now)
    env.echo(est.text())
    balance = env.client.credits.remaining
    env.echo(f"  window {plan.first}..{plan.last}: {len(plan.days)} weekdays to pull, {len(plan.dp_dates)} dark-pool "
             f"dates, Tempest from {plan.tempest_from} ({', '.join(plan.tempest_symbols)})")
    if plan.lost_days:
        env.echo(f"  already beyond the 365-day reach (not pulled): {len(plan.lost_days)} weekdays "
                 f"{plan.lost_days[0]}..{plan.lost_days[-1]}")
    if balance is not None:
        usable = balance - reserve_credits(env.settings)
        env.echo(f"  balance: {balance:,} credits, {reserve_credits(env.settings):,} kept for the step-7 checks "
                 "running alongside" + (f" - SHORT by {est.total - usable:,}: the job stops cleanly when it runs "
                                        "out (buy the pack the step-7 report names)" if est.total > usable else ""))
    if est.total > credits.ASK_ABOVE_CREDITS and (approved is None or approved < est.total):
        raise credits.CreditRefused(f"estimate {est.total:,} is over {credits.ASK_ABOVE_CREDITS:,} credits: ask the "
                                    f"owner, then re-run with --approve-credits {est.total}")
    plan.created = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    plan.approved_credits = approved
    plan.estimate_total = est.total
    earlier = spent_on_plan(env.store, None)
    if earlier:
        env.echo(f"  already spent by earlier backfill plans: {earlier:,} credits (this approval is for the "
                 "estimate above only; every re-plan needs the owner's OK again)")
    append_jsonl(job_dir(env.paths) / "approvals.jsonl",
                 {"plan": plan.plan_id, "approved_credits": approved, "estimate": est.total,
                  "spent_before": earlier, "at": now.isoformat()})
    atomic_write_json(plan_path(env.paths), plan.to_json())
    done, total = record_plan(env.paths, JOB_NAME, chunk_ids(plan))
    env.echo(f"plan written: {plan_path(env.paths)} ({done}/{total} chunks already done); start it with: "
             f"uv run nqbot run {JOB_NAME} --until 07:30")
    return plan


# --- credit budget -----------------------------------------------------------------------------------------

def nominal_cost(row: dict[str, Any]) -> int:
    """The list price of one successful ledger call (vol/history from its own parameters)."""
    ep = row.get("endpoint", "")
    if ep == "/v1/vol/history":
        p = row.get("params") or {}
        return vol_history_cost(len(str(p.get("symbols", "")).split(",")), date.fromisoformat(p["from"]),
                                date.fromisoformat(p["to"]))
    if ep.startswith("/v1/dark-pool/top-prints"):
        return credits.DP_TOP_PRINTS
    return NOMINAL.get(ep, 0)


def spent_on_plan(store: SkylitStore, plan_id: str | None) -> int:
    """Credits spent by one plan (None: by every backfill plan)."""
    return sum(max(r.get("charged") or 0, nominal_cost(r)) for r in store.ledger()
               if r.get("job") == JOB_NAME and (plan_id is None or r.get("plan") == plan_id)
               and (r.get("status") or 999) < 400)


def reserve_credits(settings: dict[str, Any]) -> int:
    """Credits the backfill leaves for the step-7 checks running alongside it without the lock (decision 2)."""
    return int(settings["skylit_alongside_reserve_credits"])


@dataclass
class Budget:
    approved: int  # the owner's approval (or the 5,000-credit no-ask limit when none was needed)
    spent: int
    balance: Callable[[], int | None]
    taken: list[int] = field(default_factory=list)
    reserve: int = 0  # skylit_alongside_reserve_credits: never spent by the backfill

    def require(self, cost: int, what: str) -> None:
        if self.spent + cost > self.approved:
            raise StopChunk(f"the approved {self.approved:,} credits are used up ({self.spent:,} counted, refused calls "
                            f"included until the next run re-reads the ledger; {what} needs "
                            f"{cost}): ask the owner, then re-plan with nqbot skylit plan --approve-credits",
                            status="credits")
        bal = self.balance()
        if bal is not None and bal - self.reserve < cost:
            raise StopChunk(f"Skylit balance {bal:,} (less {self.reserve:,} kept for the step-7 checks running "
                            f"alongside) can't cover {what} ({cost} credits): top up (or wait for the 1st), then "
                            f"re-run nqbot run {JOB_NAME}", status="credits")

    def take(self, cost: int, what: str) -> None:
        """Before a call: refuse it if it can't be covered, else count it as spent (refunded failures stay
        counted until the next run re-reads the ledger: on the safe side)."""
        self.require(cost, what)
        self.spent += cost
        self.taken.append(cost)


# --- the job ----------------------------------------------------------------------------------------------

class SkylitBackfillJob:
    name = JOB_NAME
    locks = (SKYLIT,)  # its own lock only: the other backfill may run alongside (§11 One machine)

    def __init__(self, client_factory: Callable[..., Any] = make_client, now: Callable[[], datetime] | None = None,
                 cal: dict[str, Any] | None = None, features: dict[str, Any] | None = None):
        self.client_factory = client_factory
        self.now = now
        self.cal = cal
        self.features = features
        self._env: step7.Env | None = None
        self._plan: BackfillPlan | None = None
        self._budget: Budget | None = None
        self._max_pages = 1
        self._failed: dict[str, dict[str, Any]] = {}
        self._in_row = 0  # windows/dates failed in a row in this run's main pass (reset by any successful call)
        self._timeouts = 0  # retry pass: timeouts in a row
        # credits of this night's paid retries beyond the first (dropped windows' extra tries and the one re-pull of
        # a window, dark-pool date or Tempest range whose kept copy is unusable): skylit_dropped_retry_credits_per_night
        self._night_retry_spent = 0

    def env(self, ctx: Ctx) -> step7.Env:
        if self._env is None:
            from nqbot.config import load_features

            kw: dict[str, Any] = {"now": self.now} if self.now is not None else {}
            self._env = step7.Env(settings=ctx.settings, features=self.features or load_features(), paths=ctx.paths,
                                  log=ctx.log, client_factory=lambda store: self.client_factory(ctx.settings, store,
                                                                                                ctx.log),
                                  echo=ctx.log.info, cal=self.cal, **kw)
        return self._env

    def plan(self, ctx: Ctx) -> BackfillPlan:
        if self._plan is None:
            path = plan_path(ctx.paths)
            if not path.exists():
                raise BackfillRefused(f"no {path}: write it first with uv run nqbot skylit plan (after the step-7 "
                                      "report and the owner's OK)")
            self._plan = BackfillPlan.from_json(json.loads(path.read_text(encoding="utf-8")))
        return self._plan

    def chunks(self, ctx: Ctx) -> list[str]:
        plan, env = self.plan(ctx), self.env(ctx)
        if ctx.settings["skylit_max_strikes"] != plan.max_strikes:
            raise BackfillRefused(f"skylit_max_strikes is {ctx.settings['skylit_max_strikes']} but the plan was made "
                                  f"with {plan.max_strikes}: re-run nqbot skylit plan")
        refuse_hard_mismatches(env, plan.days, plan.dp_dates)
        now = env.now()
        est = estimate(env, plan, now)
        approved = plan.approved_credits if plan.approved_credits is not None else credits.ASK_ABOVE_CREDITS
        spent = spent_on_plan(env.store, plan.plan_id)
        env.echo(est.text())
        env.echo(f"  approved {approved:,}, spent on this plan so far {spent:,}")
        if est.total > approved - spent:
            raise BackfillRefused(f"what's left ({est.total:,} credits) is more than what's left of the approval "
                                  f"({approved - spent:,}): ask the owner, then re-plan with --approve-credits")
        balance = env.client.credits.remaining
        reserve = reserve_credits(ctx.settings)
        env.echo(f"  Skylit balance before starting: {f'{balance:,}' if balance is not None else 'unknown'} credits "
                 f"({reserve:,} kept for the step-7 checks running alongside); if it runs out the job stops cleanly "
                 "(status credits): top up, then run the same command again")
        if balance is not None and est.total > balance - reserve:
            env.log.warning("balance %s (less the %s reserve) < remaining estimate %s: the job will stop cleanly "
                            "when the balance runs out; top up, then re-run", f"{balance:,}", f"{reserve:,}",
                            f"{est.total:,}")
        self._budget = Budget(approved, spent, lambda: env.client.credits.remaining, reserve=reserve)
        self._max_pages = max_pages_stored(env.store)
        self._failed = load_failed(ctx.paths)
        self._in_row = 0
        self._night_retry_spent = 0
        return chunk_ids(plan)

    # retry passes (owner 2026-10-06): at the start of every run and the end of every night, oldest first
    def at_start(self, ctx: Ctx) -> None:
        self._retry_pass(ctx, "start")

    def at_end(self, ctx: Ctx) -> None:
        self._retry_pass(ctx, "end")

    # --- failures (decision 4, presd_fixes D1) ---
    def _wait(self, ctx: Ctx, sec: float) -> None:
        """Sleep `sec`, checking ctx.should_stop() every WAIT_STEP_SEC (a stop request never waits 15 minutes)."""
        left = sec
        while left > 0:
            step = min(WAIT_STEP_SEC, left)
            ctx.sleep(step)
            left -= step
            reason = ctx.should_stop()
            if reason:
                raise StopChunk(reason)

    def _succeeded(self) -> None:
        self._in_row = 0

    def _record(self, ctx: Ctx, key: str, rec: dict[str, Any]) -> None:
        self._failed[key] = rec
        atomic_write_json(failed_path(ctx.paths), self._failed)

    def _record_failure(self, ctx: Ctx, key: str, kind: str, d: date, hhmm: str | None, exc: Exception,
                        extra: dict[str, Any] | None = None) -> None:
        charged = bool(getattr(exc, "charged", False)) or getattr(exc, "code", None) in DP_PAID_CODES \
            or getattr(exc, "code", None) == "dropped"  # D7: possibly charged, already sent twice: not called a third time
        raw = getattr(exc, "raw_path", None)
        if getattr(exc, "code", None) in RANGE_KEPT_CODES:  # the 31-day answer is kept: re-split free
            charged, raw = True, None
        group = [x for x in getattr(exc, "group", []) if x != d]  # a failed 31-day call: its other dates too
        rec = {"kind": kind, "date": d.isoformat(), "hhmm": hhmm, "status": getattr(exc, "status", None),
               "code": getattr(exc, "code", type(exc).__name__), "message": str(exc)[:500], "charged": charged,
               "raw": str(raw) if raw else None, "at": datetime.now(timezone.utc).isoformat(), "retries": 0,
               "state": "failed", **complete_of(exc),
               **({"group": [d.isoformat(), *(x.isoformat() for x in group)]} if group else {}), **(extra or {})}
        for x in group:  # never asked again in the main pass: the kept answer covers them (rule 2)
            self._failed[dp_key(x)] = {**rec, "date": x.isoformat(), "shares": key}
        self._record(ctx, key, rec)

    def _on_error(self, ctx: Ctx, key: str, kind: str, d: date, hhmm: str | None, exc: Exception, attempt: int,
                  extra: dict[str, Any] | None = None) -> bool:
        """True: try again now (a transient error, after a wait); False: recorded as failed, carry on. Raises
        StopChunk(status="failures") after skylit_max_failures_in_row in a row. Only Skylit's answers are skipped
        (a refusal, an answer that couldn't be converted or stored: WindowFailed); anything else is a bug and raises."""
        if not isinstance(exc, SkylitError):
            raise exc
        if isinstance(exc, SkylitError) and exc.code in TRANSIENT_CODES and attempt < len(CHUNK_RETRY_SEC):
            ctx.log.warning("%s: %s; trying it again in %ss", key, exc, CHUNK_RETRY_SEC[attempt])
            self._wait(ctx, CHUNK_RETRY_SEC[attempt])
            return True
        self._record_failure(ctx, key, kind, d, hhmm, exc, extra)
        self._in_row += 1
        k = int(ctx.settings["skylit_max_failures_in_row"])
        ctx.log.error("%s failed (%s); recorded in %s, never pulled again in this pass; carrying on (%d/%d in a row)",
                      key, exc, failed_path(ctx.paths), self._in_row, k)
        if self._in_row >= k:
            raise StopChunk(f"{k} windows or dates failed in a row (last {key}: {exc}): stopped cleanly. Check the "
                            f"Skylit status page and {failed_path(ctx.paths)}, then run nqbot run {JOB_NAME} again "
                            "(recorded failures are skipped; ask Claude Code if it repeats)", status="failures")
        return False

    def run_chunk(self, chunk_id: str, ctx: Ctx) -> None:
        for attempt in range(len(CHUNK_RETRY_SEC) + 1):
            try:
                return self._run_chunk(chunk_id, ctx)
            except SkylitError as e:
                if isinstance(e, OutOfCredits) or e.code not in TRANSIENT_CODES:
                    raise
                if attempt == len(CHUNK_RETRY_SEC):
                    raise StopChunk(f"Skylit kept failing ({e}): stopped cleanly, re-run nqbot run {JOB_NAME}",
                                    status="api_error") from None
                ctx.log.warning("%s: %s; retrying the chunk in %ss (cached windows are reused)", chunk_id, e,
                                CHUNK_RETRY_SEC[attempt])
                self._wait(ctx, CHUNK_RETRY_SEC[attempt])

    def _run_chunk(self, chunk_id: str, ctx: Ctx) -> None:
        plan, env = self.plan(ctx), self.env(ctx)
        assert self._budget is not None, "chunks() runs first"
        try:
            with env.client.tagged(job=JOB_NAME, plan=plan.plan_id):
                if chunk_id.startswith(TEMPEST_CHUNK):
                    atomic_write_json(ctx.out_dir / "tempest.json", self._tempest(env, plan, ctx))
                else:
                    dp_only = chunk_id.startswith(DP_CHUNK)
                    day = chunk_id.removeprefix(DP_CHUNK)
                    d = date.fromisoformat(day)
                    path = ctx.out_dir / "days" / f"{day}.json"  # one record per date: the dp chunk's part is kept
                    rec: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"date": day}
                    if not dp_only and d in set(plan.days):
                        rec["skylit"] = self._skylit_day(env, plan, d, ctx)
                    if d in set(plan.dp_dates) and (dp_only or "dp" not in rec):
                        rec["dp"] = self._dp_guarded(env, d, plan, ctx)
                    atomic_write_json(path, rec)
        except OutOfCredits as e:
            raise StopChunk(f"Skylit answered 402 out of credits ({e.message}): top up, then re-run nqbot run "
                            f"{JOB_NAME}", status="credits") from None
        write_coverage(env, plan, ctx.out_dir, self._budget)

    # one Skylit session
    def _skylit_day(self, env: step7.Env, plan: BackfillPlan, d: date, ctx: Ctx) -> dict[str, Any]:
        reuse = replace_raised_sample_day(env, d)
        margin = margin_min(env.settings)
        budget = self._budget

        def gate(w: Window) -> bool:
            if not window_reachable(w, env.now(), margin):
                return False
            budget.take(credits.RANGE_CALL, f"window {w.day} {w.hhmm}")
            return True

        def on_error(w: Window, exc: Exception, attempt: int) -> bool:
            return self._on_error(ctx, window_key(d, w.hhmm), "window", d, w.hhmm, exc, attempt)

        res = pull_session(env.client, env.store, d, should_stop=ctx.should_stop, log=ctx.log, cal=env.cal,
                           repull=RAISE if reuse.get("pull_again") else None, gate=gate, now=env.now,
                           skip=lambda w: window_key(d, w.hhmm) in self._failed, on_error=on_error,
                           on_success=lambda w: self._succeeded())
        return {"windows": len(session_windows(d, env.cal)), "pulled": res.pulled, "cached": res.cached,
                "empty": res.empty, "lost": res.lost, "failed": res.failed, "reuse": reuse,
                "resolutions": stored_resolutions(env.store, d)}

    def _dp_guarded(self, env: step7.Env, d: date, plan: BackfillPlan, ctx: Ctx,
                    single: bool = False) -> dict[str, Any]:
        """_dp_date with the failure rule (D1): a date that keeps failing is recorded and the job carries on. A date
        already recorded is never asked again in the main pass (review: a paid failure would be paid twice, and in
        range mode its group would no longer start at it)."""
        if not single and dp_key(d) in self._failed:
            return {"status": "failed", "prints": 0, "recorded": True}
        attempt = 0
        while True:
            calls = len(self._budget.taken)
            try:
                out = self._dp_date(env, d, plan, ctx, single=single)
            except NEVER_SKIPPED as e:
                pages = getattr(e, "dp_pages", None)
                if pages is not None and pages.raw_path is not None:  # paid pages kept: recorded, never paid twice
                    self._record_failure(ctx, dp_key(d), "dp", d, None, pages)
                raise
            except Exception as e:  # noqa: BLE001 - _on_error decides; an unplugged SSD always stops
                if not env.paths.mounted():
                    raise
                if self._on_error(ctx, dp_key(d), "dp", d, None, e, attempt):
                    attempt += 1
                    continue
                return {"status": "failed", "prints": 0, "error": str(e)[:300]}
            if len(self._budget.taken) > calls:  # a call was made and answered (a cache hit proves nothing)
                self._succeeded()
            return out

    # one dark-pool date (or, in range mode, the 31-day call that starts at it)
    def _dp_date(self, env: step7.Env, d: date, plan: BackfillPlan, ctx: Ctx, single: bool = False) -> dict[str, Any]:
        path = env.store.dp_trades_path(d)
        if not path.exists() and step7.adopt_lookback_copy(env, d):  # a late-print check's copy: free (D3)
            return {"status": "from_late_print_check", "prints": pq.ParquetFile(path).metadata.num_rows}
        if path.exists():
            step7.pull_dp_date(env, d)  # parameter check only (cached)
            return {"status": "cached", "prints": pq.ParquetFile(path).metadata.num_rows}
        probe = env.store.root / "darkpool" / "probe" / f"{d.isoformat()}.json"
        if probe.exists():  # step 7 already paid for this answer: not asked again (rule 2)
            return {"status": json.loads(probe.read_text(encoding="utf-8"))["status"], "prints": 0, "from_probe": True}
        covered = dp_range_covered(env.paths)
        if d in covered:  # an earlier range call answered for it (an empty trading day isn't cached)
            return {"status": covered[d], "prints": 0, "via_range": True}
        kept = kept_range_covering(env, d)
        if kept is not None:  # an answer already paid for (a crash before its record was written): split it again
            return self._settle_range(env, d, kept[0], kept[1],
                                      sorted({d, *[x for x in dp_todo(env, plan) if kept[0] <= x <= kept[1]]}), ctx)
        budget = self._budget
        edge = dp_reach_edge(env.now())
        if plan.dp_range and not single and d > edge + DP_RANGE_REACH_MARGIN:
            return self._dp_range(env, d, plan, ctx, edge)
        budget.require(credits.DP_TRADES_PAGE * DP_PAGE_RESERVE_FACTOR * self._max_pages,
                       f"dark pools {d}")  # don't start a date it can't end
        try:
            got = step7.pull_dp_date(env, d, keep_empty_trading_day=False,
                                     before_page=lambda: budget.take(credits.DP_TRADES_PAGE, f"dark-pool page {d}"))
        except OutOfCredits:
            raise
        except SkylitError as e:
            if not step7.dp_out_of_reach(e) or d > edge:
                raise  # a refusal for a date well inside the reach is a real error, not "out of reach"
            env.log.warning("dark pools %s: answered %s %s: out of reach (not cached)", d, e.status, e.code)
            return {"status": "out_of_reach", "prints": 0}
        if got is None:
            return {"status": "no_coverage", "prints": 0}
        meta = read_meta(got)
        self._max_pages = max(self._max_pages, int(meta.get("pages") or 1))
        n = pq.ParquetFile(got).metadata.num_rows
        return {"status": "pulled" if env.trading_day(d) else "not_a_session", "prints": n, "pages": meta.get("pages")}

    def _dp_range(self, env: step7.Env, d: date, plan: BackfillPlan, ctx: Ctx, edge: date) -> dict[str, Any]:
        group = dp_range_groups([x for x in dp_todo(env, plan) if x >= d], edge)[0]
        assert group[0] == d, (group, d)
        budget = self._budget
        need = dp_group_pages(env, group, step7._pages_per_date(env), step7.dp_prints_per_date(env.paths), edge)
        budget.require(int(credits.DP_TRADES_PAGE * DP_PAGE_RESERVE_FACTOR * max(need, self._max_pages)),
                       f"dark pools {d}..{group[-1]}")  # don't start a range it can't end (pages paid stay lost)
        try:
            step7.pull_dp_range_raw(env, d, group[-1],
                                    before_page=lambda: budget.take(credits.DP_TRADES_PAGE, f"dark-pool page {d}.."))
        except DarkPoolFailed as e:  # its pages are kept: the group's other dates are recorded with it (D1)
            e.group = group
            raise
        except NEVER_SKIPPED as e:  # stopped mid-walk: the pages kept so far cover the group's dates too
            if getattr(e, "dp_pages", None) is not None:
                e.dp_pages.group = group
            raise
        return self._settle_range(env, d, d, group[-1], group, ctx)

    def _settle_range(self, env: step7.Env, d: date, start: date, end: date, dates: list[date],
                      ctx: Ctx) -> dict[str, Any]:
        """Split a kept range answer into its per-date files and record every date it settled (free)."""
        try:
            got = step7.pull_dp_range(env, start, end, dates)
        except NEVER_SKIPPED:
            raise
        except SkylitError as e:  # e.g. range_date: every date of the call is recorded with it, split again later
            e.group = list(dates)
            raise
        except Exception as e:  # noqa: BLE001 - the answer stays kept in darkpool/range/: split again for free (D1)
            if not env.paths.mounted():
                raise
            err = SkylitError(None, "range_split_failed", f"dark-pool range {start}..{end} couldn't be split "
                              f"({type(e).__name__}: {e}); the answer is kept in darkpool/range/", charged=True)
            err.group = list(dates)
            raise err from e
        statuses = {x: ("no_coverage" if p is None else "pulled" if env.trading_day(x) else "not_a_session")
                    for x, p in got.items()}
        record = ctx.out_dir / "dp_range" / f"{start.isoformat()}_{end.isoformat()}.json"
        before = json.loads(record.read_text(encoding="utf-8"))["dates"] if record.exists() else {}
        atomic_write_json(record, {"start": start.isoformat(), "end": end.isoformat(),
                                   "dates": {**before, **{x.isoformat(): st for x, st in statuses.items()}}})
        pages = int(read_meta(step7.dp_range_path(env, start, end)).get("pages") or 1)
        mine = env.store.dp_trades_path(d)
        return {"status": statuses.get(d, "cached"), "prints": pq.ParquetFile(mine).metadata.num_rows if mine.exists() else 0,
                "via_range": [start.isoformat(), end.isoformat()], "pages": pages}

    def _retry_pass(self, ctx: Ctx, when: str) -> None:
        """Try every recorded failure once, oldest first. Out of reach: lost. skylit_max_failures_in_row timeouts in
        a row (an outage) end the pass, not the run: the rest wait for the next pass. A plain refusal doesn't count,
        so windows Skylit always refuses never block the backfill."""
        todo = sorted((k for k, r in self._failed.items() if r["state"] == "failed"),
                      key=lambda k: (self._failed[k]["date"], self._failed[k].get("hhmm") or "", k))
        if not todo:
            return
        dropped = [k for k in todo if dropped_tries_left(self._failed[k], ctx.settings)]
        todo = [k for k in todo if k not in set(dropped) or effective_state(self.env(ctx), self._failed[k],
                                                                          self.env(ctx).now()) == "lost"]
        plan, env = self.plan(ctx), self.env(ctx)
        ctx.log.info("%s: retrying %d failed windows/dates (%s of the run), oldest first", JOB_NAME, len(todo), when)
        outcome: dict[str, str] = {}
        k = int(ctx.settings["skylit_max_failures_in_row"])
        self._timeouts = 0
        try:
            with env.client.tagged(job=JOB_NAME, plan=plan.plan_id, retry=when):
                for key in todo:
                    reason = ctx.should_stop()
                    if reason:
                        raise StopChunk(reason)
                    outcome[key] = self._retry_one(env, plan, ctx, key, when)
                    if self._timeouts >= k:
                        ctx.log.warning("%s: %d retries in a row timed out (Skylit down?): the rest wait for the "
                                        "next pass", JOB_NAME, k)
                        break
                if when == "end":
                    outcome.update(self._dropped_rounds(env, ctx, [x for x in dropped if x not in outcome]))
        except OutOfCredits as e:
            raise StopChunk(f"Skylit answered 402 out of credits ({e.message}): top up, then re-run nqbot run "
                            f"{JOB_NAME}", status="credits") from None
        finally:
            if outcome:
                append_jsonl(job_dir(ctx.paths) / "retries.jsonl",
                             {"at": env.now().isoformat(), "when": when, "outcome": outcome})
                write_coverage(env, plan, ctx.out_dir, self._budget)

    def _retry_one(self, env: step7.Env, plan: BackfillPlan, ctx: Ctx, key: str, when: str) -> str:
        rec = self._failed[key]
        d = date.fromisoformat(rec["date"])
        error, changes = None, {}
        state = effective_state(env, rec, env.now())
        if state == "lost":
            pass  # left the reach while it waited: never callable again
        elif rec["charged"] and not rec.get("raw") and rec["kind"] == "window":
            state = "failed"  # paid, nothing kept: never called again (rule 2), but a dropped one has its extra tries
            # (_dropped_rounds); lost once it leaves the reach
        else:
            try:
                if rec["kind"] == "window":
                    state = self._retry_window(env, ctx, key, d, margin_min(env.settings))
                elif rec["kind"] == "tempest":
                    state = self._retry_tempest(env, plan, ctx, key)
                else:
                    state = self._retry_dp(env, key, d, plan, ctx)
            except NEVER_SKIPPED:
                raise
            except CacheMismatch as e:  # a stored file with other parameters: stays failed, the owner is told
                state, error = "failed", f"CacheMismatch: {e} (ask Claude Code; nothing is deleted)"[:500]
            except SkylitError as e:  # still failing: tried again at the next pass while it is in reach
                state, error = "failed", f"{type(e).__name__}: {e}"[:500]
                self._timeouts = self._timeouts + 1 if e.code in TRANSIENT_CODES else 0
                if getattr(e, "charged", False):  # the retry paid for an answer it couldn't use: keep where it is
                    raw = getattr(e, "raw_path", None)
                    changes = {"charged": True, "raw": str(raw) if raw else rec.get("raw"), "code": e.code,
                               **complete_of(e)}
            if state == "recovered":
                self._timeouts = 0
        ctx.log.log(30 if state == "failed" else 20, "retry of %s: %s%s", key, state, f" ({error})" if error else "")
        rec = self._failed[key]  # re-read: a re-pull records itself before its call
        self._record(ctx, key, {**rec, **changes, "retries": rec.get("retries", 0) + 1,
                                "last_retry": f"{when} {env.now().isoformat()}", "state": state,
                                **({"retry_error": error} if error else {})})
        return state

    def _dropped_rounds(self, env: step7.Env, ctx: Ctx, keys: list[str]) -> dict[str, str]:
        """Owner 2026-10-06: a window whose connection dropped gets up to skylit_dropped_extra_tries more tries while
        it is in reach, in rounds (oldest first, DROPPED_ROUND_WAIT_SEC before each round), each try sent once (25
        credits at most), all within skylit_dropped_retry_credits_per_night. Every try is recorded with its
        credits (coverage.md lists them)."""
        cap = int(ctx.settings["skylit_dropped_retry_credits_per_night"])
        margin = margin_min(env.settings)
        out: dict[str, str] = {}
        for wait in DROPPED_ROUND_WAIT_SEC:
            left = [k for k in keys if dropped_tries_left(self._failed[k], ctx.settings)]
            if not left:
                break
            if self._night_retry_spent + credits.RANGE_CALL > cap:
                ctx.log.warning("%s: tonight's cap for dropped-window retries (%d credits) is used up: %d window(s) "
                                "wait for the next night", JOB_NAME, cap, len(left))
                break
            ctx.log.info("%s: %d dropped window(s) get another try in %ss", JOB_NAME, len(left), wait)
            self._wait(ctx, wait)
            for key in left:
                if self._night_retry_spent + credits.RANGE_CALL > cap:
                    break
                out[key] = self._dropped_try(env, ctx, key, margin)
        return out

    def _dropped_try(self, env: step7.Env, ctx: Ctx, key: str, margin: float) -> str:
        rec = self._failed[key]
        d = date.fromisoformat(rec["date"])
        w = _windows_by_hhmm(d, env.cal).get(rec["hhmm"])
        if w is None or not window_reachable(w, env.now(), margin):
            self._record(ctx, key, {**rec, "state": "lost"})
            return "lost"
        result, paid = SessionResult(d), []

        def gate(win: Window) -> bool:
            if not window_reachable(win, env.now(), margin):
                return False
            self._budget.take(credits.RANGE_CALL, f"dropped-window retry {win.day} {win.hhmm}")
            self._night_retry_spent += credits.RANGE_CALL
            paid.append(credits.RANGE_CALL)
            return True

        error, update = None, {}
        try:
            pull_window(env.client, env.store, w, result=result, log=env.log, gate=gate, now=env.now, drop_retries=0)
            state = "lost" if result.lost else "recovered" if (result.pulled or result.empty) else "failed"
        except NEVER_SKIPPED:
            raise
        except SkylitError as e:
            state, error = "failed", f"{type(e).__name__}: {e}"[:300]
            if e.code != DROPPED:  # answered this time: now an ordinary failure (refused, or paid and kept)
                raw = getattr(e, "raw_path", None)
                update = {"code": e.code, "status": e.status, "charged": bool(e.charged), **complete_of(e),
                          "raw": str(raw) if raw else rec.get("raw")}
        tries = [*rec.get("dropped_tries", []), {"at": env.now().isoformat(), "credits": sum(paid),
                                                 "outcome": state if error is None else error}]
        self._record(ctx, key, {**rec, **update, "dropped_tries": tries, "state": state})
        ctx.log.log(30 if state == "failed" else 20, "dropped-window retry %d of %s: %s", len(tries), key,
                    error or state)
        return state

    def _retry_window(self, env: step7.Env, ctx: Ctx, key: str, d: date, margin: float) -> str:
        rec = self._failed[key]
        w = _windows_by_hhmm(d, env.cal).get(rec["hhmm"])
        if w is None:  # the calendar no longer has this window (e.g. an edited early close): nothing to pull
            return "lost"
        if env.store.range_cached(d, w.hhmm, range_params_from_settings(env.settings, w)):
            return "recovered"
        if rec["charged"]:  # paid already: converted again from the kept answer (free)
            if copy_usable(rec):
                raw = Path(rec["raw"])
                try:
                    body = json.loads(raw.read_bytes())
                except (OSError, ValueError):  # the kept copy itself is unreadable: one paid re-pull (owner)
                    self._record(ctx, key, {**rec, "complete": False})
                else:
                    record = SimpleNamespace(at=rec["at"], credits_remaining=None, charged=None,
                                             bytes=raw.stat().st_size)
                    root_to_map = {v: k for k, v in env.settings["skylit_symbols"].items()}
                    store_window(env.store, w, body, record, range_params_from_settings(env.settings, w), root_to_map,
                                 result=SessionResult(d), raw_path=raw)
                    return "recovered"
            if needs_repull(self._failed[key]):
                return self._repull(env, ctx, key, lambda: self._pull_retry_window(env, w, d, margin, drop_retries=0),
                                    credits.RANGE_CALL, gone=not window_reachable(w, env.now(), margin))
            return "failed"  # paid, nothing usable kept and its re-pull used: never called again (rule 2)
        return self._pull_retry_window(env, w, d, margin)

    def _pull_retry_window(self, env: step7.Env, w: Window, d: date, margin: float,
                           drop_retries: int | None = None) -> str:
        """drop_retries=0 for the one re-pull: a dropped connection is never re-sent (it may have been charged)."""
        result = SessionResult(d)

        def gate(win: Window) -> bool:
            if not window_reachable(win, env.now(), margin):
                return False
            self._budget.take(credits.RANGE_CALL, f"retry of window {win.day} {win.hhmm}")
            return True

        kw = {"drop_retries": drop_retries} if drop_retries is not None else {}
        pull_window(env.client, env.store, w, result=result, log=env.log, gate=gate, now=env.now, **kw)
        if result.lost:
            return "lost"
        return "recovered" if (result.pulled or result.empty) else "failed"

    def _retry_dp(self, env: step7.Env, key: str, d: date, plan: BackfillPlan, ctx: Ctx) -> str:
        rec = self._failed[key]
        if env.store.dp_trades_path(d).exists() or d in dp_range_covered(env.paths):
            return "recovered"
        if copy_usable(rec):  # its pages are kept whole: converted again, for free (rule 2)
            return self._reconvert_dp(env, rec, d, ctx)
        prefix = kept_range_prefix(rec)
        if prefix is not None and d in prefix[1]:  # a cut-off 31-day answer that holds this whole date: free
            return self._salvage_dp(env, rec, d, prefix[0])
        if needs_repull(rec):  # the kept copy is cut off, out of order or unreadable: one paid re-pull (owner)
            return self._repull(env, ctx, key, lambda: self._dp_status(self._dp_date(env, d, plan, ctx, single=True)),
                                credits.DP_TRADES_PAGE * self._max_pages, gone=d <= dp_reach_edge(env.now()))
        if rec["charged"] and kept_range_covering(env, d) is None:
            return "failed"  # paid, nothing usable kept and its re-pull used: never called again (rule 2)
        if d <= dp_reach_edge(env.now()) and not rec["charged"]:
            return "lost"
        return self._dp_status(self._dp_date(env, d, plan, ctx, single=True))  # a kept range is split again, free

    @staticmethod
    def _dp_status(out: dict[str, Any]) -> str:
        return {"out_of_reach": "lost", "no_coverage": "no_coverage"}.get(out["status"], "recovered")

    def _salvage_dp(self, env: step7.Env, rec: dict[str, Any], d: date, prints: list[dict[str, Any]]) -> str:
        """Store one whole date from a cut-off 31-day answer's kept pages (free; owner 2026-10-06)."""
        rows = [p for p in prints if step7.dp_trade_date(p) == d]
        try:
            got = step7.store_dp_date(env, d, DarkPoolPull(rows, 0, [], [], {"date": d.isoformat()}),
                                      keep_empty_trading_day=False, raw_path=Path(rec["raw"]))
        except DarkPoolFailed as e:  # a conversion bug: the kept copy stays a cut-off one, salvaged again later
            e.complete = False
            raise
        return "recovered" if got is not None else "no_coverage"

    def _reconvert_dp(self, env: step7.Env, rec: dict[str, Any], d: date, ctx: Ctx) -> str:
        """A kept dark-pool answer converted and stored again (free); a 31-day one is then split for its dates."""
        raw = Path(rec["raw"])
        kept = kept_copy(rec)
        span = kept["span"]
        try:
            prints = dark_pool_prints(kept["pages"], "..".join(span.values()), span)
        except DarkPoolFailed as e:
            raise step7.keep_dp_failure(env, e, raw) from None
        pull = DarkPoolPull(prints, len(kept["pages"]), [], kept["pages"], span)
        if "date" in span:
            got = step7.store_dp_date(env, d, pull, keep_empty_trading_day=False, raw_path=raw)
            return "recovered" if got is not None else "no_coverage"
        start, end = date.fromisoformat(span["date_start"]), date.fromisoformat(span["date_end"])
        step7.store_dp_range_raw(env, start, end, pull, raw_path=raw)
        dates = sorted({d, *(date.fromisoformat(x) for x in rec.get("group", []))})
        return self._dp_status(self._settle_range(env, d, start, end, dates, ctx))

    def _repull(self, env: step7.Env, ctx: Ctx, key: str, call: Callable[[], str], need: int, gone: bool) -> str:
        """The one paid re-pull of a window, dark-pool date or Tempest range whose kept copy is unusable (owner 2026-10-06):
        while in reach, within skylit_dropped_retry_credits_per_night (else it waits for the next night). It is
        marked used before the call (a crash never allows a second one) and listed with its credits in coverage.md."""
        if gone:
            return "lost"
        cap = int(ctx.settings["skylit_dropped_retry_credits_per_night"])
        if self._night_retry_spent + need > cap:
            ctx.log.warning("%s: tonight's retry cap (%d credits) can't cover the re-pull of %s (~%d): it waits for "
                            "the next night", JOB_NAME, cap, key, need)
            return "failed"
        before = self._failed[key]
        at = env.now().isoformat()
        self._record(ctx, key, {**before, "repull": {"at": at, "credits": None, "outcome": "started"}})
        n = len(self._budget.taken)
        outcome, used, update = "failed", True, {}
        try:
            outcome = call()
            return outcome
        except NEVER_SKIPPED as e:  # our stop (approval, balance): the re-pull stays unused; paid pages are kept
            outcome, used = "stopped", False
            pages = getattr(e, "dp_pages", None)
            if pages is not None and pages.raw_path is not None:
                update = {"raw": str(pages.raw_path), "complete": False, "code": pages.code}
            raise
        except SkylitError as e:
            outcome = f"{type(e).__name__}: {e}"[:300]
            used = bool(e.charged) or e.code == DROPPED  # refunded (outage, refusal): it is still to come
            raise
        finally:
            spent = sum(self._budget.taken[n:])
            cur = {k: v for k, v in self._failed[key].items() if k != "repull"}
            if used:
                over = max(0, self._night_retry_spent + spent - cap) - max(0, self._night_retry_spent - cap)
                self._night_retry_spent += spent  # a date in progress is finished; any overage is counted and listed
                if over:
                    ctx.log.warning("%s: the re-pull of %s went %d credits over tonight's retry cap (%d) to finish "
                                    "its date", JOB_NAME, key, over, cap)
                self._record(ctx, key, {**cur, "repull": {"at": at, "credits": spent, "outcome": outcome,
                                                          **({"over_cap": over} if over else {})}})
            else:
                tries = [*cur.get("repull_attempts", []), {"at": at, "credits": spent, "outcome": outcome}]
                self._record(ctx, key, {**cur, **update, "repull_attempts": tries})

    # Tempest, once
    def _tempest(self, env: step7.Env, plan: BackfillPlan, ctx: Ctx) -> dict[str, Any]:
        """Only the range not stored yet (a re-plan later in the month adds just the new tail). A failure follows the
        window rule (D1): recorded, its answer kept, retried by the retry passes, never called again here."""
        todo = tempest_todo(env.store, plan)
        waiting = sorted(k for k, r in self._failed.items() if r["kind"] == "tempest" and r["state"] == "failed")
        if todo and not waiting:
            attempt = 0
            while True:
                try:
                    self._tempest_call(env, plan, todo)
                    self._succeeded()
                    break
                except NEVER_SKIPPED:
                    raise
                except SkylitError as e:
                    if self._on_error(ctx, tempest_key(*todo), "tempest", todo[0], None, e, attempt,
                                      {"to": todo[1].isoformat()}):
                        attempt += 1
                        continue
                    break
        return self._tempest_summary(env, plan)

    def _tempest_call(self, env: step7.Env, plan: BackfillPlan, todo: tuple[date, date], body: Any = None,
                      raw_path: Path | None = None) -> None:
        """Pull (or, with `body`, convert a kept answer again for free) and store one Tempest range."""
        symbols = plan.tempest_symbols
        label = f"vol_history_{todo[0].isoformat()}_{todo[1].isoformat()}"
        if body is None:
            self._budget.take(vol_history_cost(len(symbols), *todo), "Tempest history")
            try:
                with env.client.tagged(tempest="backfill"):
                    body, _ = env.client.vol_history(symbols, *todo)
            except SkylitError as e:
                if not (e.charged and e.raw):
                    raise
                path = env.store.save_failed(e.raw, label + "_unreadable")  # paid, unreadable: one re-pull (owner)
                raise TempestFailed(e.code, f"Tempest {todo[0]}..{todo[1]}: {e.message}; kept in {path}", path,
                                    complete=False) from e

        def keep() -> Path | None:
            if raw_path is not None:
                return raw_path
            try:
                return env.store.save_failed(json.dumps(body).encode(), label)
            except Exception:  # noqa: BLE001 - e.g. the SSD is gone: the original error is the one to report
                return None

        try:
            table = vol_history_to_table(body)
            missing = (body.get("data") or {}).get("missing")
        except Exception as e:  # noqa: BLE001 - any conversion failure: the paid answer is kept (D1)
            path = keep()
            raise TempestFailed("convert_failed", f"Tempest {todo[0]}..{todo[1]}: answer couldn't be converted "
                                f"({type(e).__name__}: {e}); kept in {path}", path, complete=True) from e
        pulled = env.now().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        meta = {"params": {"symbols": symbols, "from": todo[0].isoformat(), "to": todo[1].isoformat()},
                "backfill_from": plan.tempest_from.isoformat(), "pulled_at": env.now().isoformat(), "missing": missing,
                **({"converted_from": raw_path.name} if raw_path is not None else {})}
        try:
            write_parquet(env.store.vol_path(*todo, pulled), table, meta)
        except Exception as e:  # noqa: BLE001 - a failed write must not lose the paid answer (D1)
            if not env.paths.mounted():
                raise
            path = keep()
            raise TempestFailed("write_failed", f"Tempest {todo[0]}..{todo[1]}: couldn't be stored "
                                f"({type(e).__name__}: {e}); kept in {path}", path, complete=True) from e

    def _retry_tempest(self, env: step7.Env, plan: BackfillPlan, ctx: Ctx, key: str) -> str:
        rec = self._failed[key]
        todo = (date.fromisoformat(rec["date"]), date.fromisoformat(rec["to"]))
        if not any(read_meta(p)["params"]["from"] == rec["date"] for p in tempest_files(env.store, plan.tempest_from)):
            if copy_usable(rec):  # converted again from the kept answer, for free
                self._tempest_call(env, plan, todo, body=kept_copy(rec), raw_path=Path(rec["raw"]))
            elif needs_repull(rec):  # the kept copy is unreadable: one paid re-pull (owner 2026-10-06)
                state = self._repull(env, ctx, key, lambda: self._tempest_call(env, plan, todo) or "recovered",
                                     vol_history_cost(len(plan.tempest_symbols), *todo), gone=False)
                if state != "recovered":
                    return state
            elif rec["charged"]:
                return "failed"  # paid, nothing usable kept and its re-pull used: never called again (rule 2)
            else:
                self._tempest_call(env, plan, todo)  # refunded: one more call
        atomic_write_json(job_dir(env.paths) / "tempest.json", self._tempest_summary(env, plan))
        return "recovered"

    def _tempest_summary(self, env: step7.Env, plan: BackfillPlan) -> dict[str, Any]:
        files = tempest_files(env.store, plan.tempest_from)
        rows = [r for p in files for r in pq.read_table(p, columns=["symbol", "date"]).to_pylist()]
        first = {s: min((r["date"] for r in rows if r["symbol"] == s), default=None) for s in plan.tempest_symbols}
        return {"files": [p.name for p in files], "needed_from": plan.tempest_from.isoformat(),
                "first_row": {s: d.isoformat() if d else None for s, d in first.items()},
                "covered": {s: d is not None and step7._sessions_between(env, plan.tempest_from, d) <= 1
                            for s, d in first.items()},
                "missing": [m for p in files for m in (read_meta(p).get("missing") or [])],
                "failed": sorted(k for k, r in self._failed.items() if r["kind"] == "tempest"
                                 and r["state"] == "failed")}


# --- sample days pulled before skylit_max_strikes was raised (presd_fixes issue 10) -----------------------------

def replace_raised_sample_day(env: step7.Env, d: date) -> dict[str, Any]:
    """Windows of `d` cached with an older maxStrikes: move them aside (never deleted), then copy the matching
    `strike_window_raise` re-pull in their place, or leave the window to be pulled again (rule-2 exception)."""
    raise_base = env.store.base(RAISE, pulled=f"max{env.settings['skylit_max_strikes']}")
    raised = env.store.stored_windows(d, raise_base)
    if not env.store.stored_windows(d) and not raised:
        return {}
    old = {hhmm: have for hhmm, (state, have) in param_state(env, d).items() if state == "strikes_only"}
    aside_root = env.store.root / "repull" / RAISE
    set_aside = {p.name.split("_")[0] for p in aside_root.glob(f"superseded_max*/range/{d.isoformat()}/*_boards.parquet")}
    if not old and not set_aside:
        return {}
    by_hhmm = _windows_by_hhmm(d, env.cal)
    moved, copied = [], []
    for hhmm, have in sorted(old.items()):
        aside = env.store.base(RAISE, pulled=f"superseded_max{have.get('maxStrikes')}")
        for src, dst in zip(env.store.range_paths(d, hhmm), env.store.range_paths(d, hhmm, aside)):
            if dst.exists():
                raise BackfillRefused(f"{dst} already exists: won't overwrite a moved-aside window")
            dst.parent.mkdir(parents=True, exist_ok=True)
            replace_retry(src, dst)
        moved.append(hhmm)
    for hhmm in sorted(set(moved) | set_aside):  # also windows moved by an earlier run that crashed before copying
        b_dst, s_dst = env.store.range_paths(d, hhmm)
        if b_dst.exists() or s_dst.exists() or hhmm not in by_hhmm:
            continue
        if _raise_copy_ok(env, d, hhmm, by_hhmm[hhmm], raise_base):
            b_src, s_src = env.store.range_paths(d, hhmm, raise_base)
            atomic_write_bytes(s_dst, s_src.read_bytes())  # strikes first, boards last (the partial-write rule)
            atomic_write_bytes(b_dst, b_src.read_bytes())
            copied.append(hhmm)
    missing = sorted((set(moved) | set_aside) - set(env.store.stored_windows(d)))
    env.log.info("%s: %d windows pulled with an older maxStrikes moved aside; %d replaced by their %s copies, %d to "
                 "pull again", d, len(moved), len(copied), RAISE, len(missing))
    return {"moved_aside": moved, "copied_from_raise": copied, "pull_again": bool(missing)}


def stored_resolutions(store: SkylitStore, d: date) -> dict[str, str | None]:
    out = {}
    for hhmm in store.stored_windows(d):
        meta = read_meta(store.range_paths(d, hhmm)[0])
        out[hhmm] = (meta.get("meta") or {}).get("resolution") if meta.get("n_boards", 1) else None
    return out


# --- coverage report ---------------------------------------------------------------------------------------

def coverage_dir(paths: Paths) -> Path:
    return paths.reports_dir / "step10"


def _day_records(out_dir: Path) -> list[dict[str, Any]]:
    folder = out_dir / "days"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))] if folder.exists() else []


def coverage(env: step7.Env, plan: BackfillPlan, out_dir: Path, budget: Budget | None = None) -> dict[str, Any]:
    recs = _day_records(out_dir)
    now = env.now()
    failed = {k: {**r, "state": effective_state(env, r, now)} for k, r in load_failed(env.paths).items()}
    failed_lost: dict[str, set[str]] = {}
    for r in failed.values():
        if r["kind"] == "window" and r["state"] == "lost":
            failed_lost.setdefault(r["date"], set()).add(r["hhmm"])
    sky = {r["date"]: r["skylit"] for r in recs if "skylit" in r}
    dps = {r["date"]: r["dp"] for r in recs if "dp" in r}
    def lost_of(d: str) -> list[str]:  # out of reach when planned or pulled, or failed until it left the reach
        return sorted(set(sky.get(d, {}).get("lost", [])) | failed_lost.get(d, set()))

    def nothing_stored(d: str) -> bool:
        rec = sky.get(d, {})
        return not rec.get("pulled") and not rec.get("cached") and not env.store.stored_windows(date.fromisoformat(d))

    fully_lost = sorted({d.isoformat() for d in plan.lost_days if not env.store.stored_windows(d)} | {
        d for d in sky if lost_of(d) and nothing_stored(d)
        and set(lost_of(d)) >= {w.hhmm for w in session_windows(date.fromisoformat(d), env.cal)}})
    partial_all = {d: lost_of(d) for d in sky if lost_of(d) and not nothing_stored(d)}

    def listable(d: str) -> bool:
        """§7 rule 2b: dates are listed only for development months and the days before month 1; the
        walk-forward and sealed months appear as counts."""
        n = months_mod.month_index(date.fromisoformat(d), env.settings)
        return n is None and date.fromisoformat(d) < plan.first or n in months_mod.DEVELOPMENT

    def month(d: str) -> str:
        n = months_mod.month_index(date.fromisoformat(d), env.settings)
        return f"month {n}" if n is not None else "outside"

    sessions = {d: s for d, s in sky.items() if env.trading_day(date.fromisoformat(d))}
    with_boards = {d: s for d, s in sessions.items() if any(v for v in s["resolutions"].values())}
    one_min = sorted(d for d, s in with_boards.items() if "1m" in s["resolutions"].values())
    windows = [v for s in with_boards.values() for v in s["resolutions"].values() if v]
    per_month: dict[str, dict[str, int]] = {}
    for d, s in with_boards.items():
        m = per_month.setdefault(month(d), {"sessions": 0, "sessions_1m": 0})
        m["sessions"] += 1
        m["sessions_1m"] += int(d in one_min)

    dp_status: dict[str, int] = {}
    for r in dps.values():
        dp_status[r["status"]] = dp_status.get(r["status"], 0) + 1
    bad = sorted(date.fromisoformat(d) for d, r in dps.items() if r["status"] in ("out_of_reach", "no_coverage"))
    hist = step7.dp_history_start(env)  # dates before Skylit's dark-pool history: never pulled, so dp_partial
    before = [d for d in step7._weekdays(step7._f4_lower(env, plan.first), hist - timedelta(days=1)) if env.trading_day(d)]
    with_prints = sorted(d for d, r in dps.items() if r.get("prints"))
    dp_partial = 0
    for d in plan.days:
        if not env.trading_day(d) or d.isoformat() not in sessions:
            continue
        prev = step7.dp.previous_session(d, env.features["dp_lag_sessions"], env.trading_day)
        lower = step7._f4_lower(env, d)
        dp_partial += any(lower <= b <= prev for b in bad) or lower < hist

    tempest_file = out_dir / "tempest.json"
    done = set(sky) | set(dps)
    todo = sorted({d.isoformat() for d in plan.days} | {d.isoformat() for d in plan.dp_dates})
    return {
        "_lost_all": {"fully_lost": fully_lost, "partial": partial_all},
        "plan": plan.plan_id, "window": [plan.first.isoformat(), plan.last.isoformat()],
        "progress": {"dates_done": len(done), "dates_planned": len(todo), "tempest_done": tempest_file.exists()},
        "skylit": {
            "beyond_365_days": {"fully_lost": [d for d in fully_lost if listable(d)], "fully_lost_count": len(fully_lost),
                                "partial_days": {d: w for d, w in partial_all.items() if listable(d)},
                                "partial_count": len(partial_all)},
            "sessions_with_boards": len(with_boards),
            "sessions_1m": len(one_min), "share_sessions_1m": round(len(one_min) / len(with_boards), 4) if with_boards else None,
            "windows_with_boards": len(windows),
            "share_windows_1m": round(sum(v == "1m" for v in windows) / len(windows), 4) if windows else None,
            "sessions_1m_dates": [d for d in one_min if listable(d)], "per_month": dict(sorted(per_month.items())),
            "empty_windows": sum(len(s["empty"]) for s in sky.values()),
            "reused_raise_copies": {d: s["reuse"] for d, s in sky.items() if s.get("reuse")},
        },
        "dark_pools": {"status_counts": dp_status, "first_date_with_prints": with_prints[0] if with_prints else None,
                       "not_pulled": [d.isoformat() for d in bad if listable(d.isoformat())],
                       "not_pulled_count": len(bad), "sessions_dp_partial": dp_partial,
                       "history_start": hist.isoformat(), "before_history_count": len(before)},
        "failed": _failed_section(failed, listable),
        "tempest": json.loads(tempest_file.read_text(encoding="utf-8")) if tempest_file.exists() else None,
        "credits": {"approved": budget.approved if budget else plan.approved_credits,
                    "spent_on_plan": budget.spent if budget else None, "balance": env.client.credits.remaining
                    if env._client is not None else None},
    }


def write_coverage(env: step7.Env, plan: BackfillPlan, out_dir: Path, budget: Budget | None = None) -> dict[str, Any]:
    cov = coverage(env, plan, out_dir, budget)
    atomic_write_json(lost_days_path(env.paths), cov.pop("_lost_all"))  # the data tree keeps the full lists
    folder = coverage_dir(env.paths)
    atomic_write_json(folder / "coverage.json", {"written": env.now().isoformat(), **cov})
    atomic_write_text(folder / "coverage.md", coverage_markdown(cov, env.now()))
    return cov


def coverage_markdown(cov: dict[str, Any], now: datetime) -> str:
    s, dp, p = cov["skylit"], cov["dark_pools"], cov["progress"]
    lost = s["beyond_365_days"]
    lines = [
        "# Step 10 Skylit backfill: coverage", "",
        f"Written {now:%Y-%m-%d %H:%M} ET. Window {cov['window'][0]}..{cov['window'][1]}; "
        f"{p['dates_done']}/{p['dates_planned']} dates done, Tempest {'done' if p['tempest_done'] else 'not yet'}.", "",
        "## Days beyond the 365-day limit",
        f"- Wholly lost (nothing pulled): {lost['fully_lost_count']}" + (f" ({', '.join(lost['fully_lost'])})"
                                                                         if lost["fully_lost"] else ""),
        f"- Partly lost (windows lost): {lost['partial_count']}" + "".join(
            f"\n  - {d}: {len(w)} windows ({w[0]}..{w[-1]})" for d, w in sorted(lost["partial_days"].items())), "",
        "## 1-minute resolution",
        f"- Sessions with boards: {s['sessions_with_boards']}; served at 1-minute resolution in any window: "
        f"{s['sessions_1m']} (share {s['share_sessions_1m']})",
        f"- Windows with boards: {s['windows_with_boards']}; share at 1-minute: {s['share_windows_1m']}",
    ]
    lines += [f"  - {m}: {v['sessions_1m']}/{v['sessions']} sessions at 1-minute" for m, v in s["per_month"].items()]
    lines += ["", "## Dark pools",
              f"- Dates by status: {dp['status_counts']}",
              f"- First date with prints: {dp['first_date_with_prints']}",
              f"- Dates not pulled (out of reach or no coverage): {dp['not_pulled_count']}",
              f"- Skylit's dark-pool history starts {dp['history_start']} (dp_history_start): "
              f"{dp['before_history_count']} trading dates before it are never pulled",
              f"- Sessions whose lookback touches one (dp_partial): {dp['sessions_dp_partial']}", "",
              *_failed_markdown(cov["failed"]),
              "## Tempest", "```json", json.dumps(cov["tempest"], indent=1), "```", "",
              "## Credits", f"- {cov['credits']}"]
    return "\n".join(lines) + "\n"


def _failed_section(failed: dict[str, dict[str, Any]], listable: Callable[[str], bool]) -> dict[str, Any]:
    """Windows and dark-pool dates that failed (decision 4): counts by kind and state; the ones still missing are
    listed with their error only for the development months and the days before month 1 (§7 rule 2b)."""
    counts: dict[str, int] = {}
    for r in failed.values():
        k = f"{r['kind']} {r['state']}"
        counts[k] = counts.get(k, 0) + 1
    missing = {k: r for k, r in sorted(failed.items()) if r["state"] != "recovered"}
    return {"counts": counts, "still_missing_count": len(missing),
            "still_missing": {k: {"error": f"{r.get('status')} {r.get('code')}", "state": r["state"],
                                  "charged": r["charged"], "retries": r.get("retries", 0), "raw": r.get("raw"),
                                  "usable": copy_usable(r), "repulled": bool(r.get("repull"))}
                              for k, r in missing.items() if listable(r["date"])},
            "not_retried_yet": sum(1 for r in failed.values() if r["state"] == "failed" and not r.get("retries")),
            "dropped_in_reach": len([r for r in failed.values() if r.get("code") == "dropped" and r["state"] == "failed"]),
            "dropped_retries": {
                "windows": sum(1 for r in failed.values() if r.get("dropped_tries")),
                "tries": sum(len(r.get("dropped_tries", [])) for r in failed.values()),
                "credits": sum(t["credits"] for r in failed.values() for t in r.get("dropped_tries", [])),
                "listed": {k: {"tries": len(r["dropped_tries"]), "credits": sum(t["credits"] for t in r["dropped_tries"]),
                               "state": r["state"]}
                           for k, r in sorted(failed.items()) if r.get("dropped_tries") and listable(r["date"])}},
            "repulls": {  # owner 2026-10-06: the one paid re-pull of an unusable kept copy
                "count": sum(1 for r in failed.values() if r.get("repull")),
                "credits": sum(r["repull"].get("credits") or 0 for r in failed.values() if r.get("repull")),
                "over_cap": sum(r["repull"].get("over_cap") or 0 for r in failed.values() if r.get("repull")),
                "unpaid_attempts": sum(len(r.get("repull_attempts", [])) for r in failed.values()),
                "listed": {k: {**r["repull"], "state": r["state"]} for k, r in sorted(failed.items())
                           if r.get("repull") and listable(r["date"])}}}


def _failed_markdown(f: dict[str, Any]) -> list[str]:
    lines = ["## Failed windows and dark-pool dates",
             f"- By kind and state: {f['counts'] or 'none'}",
             f"- Still missing: {f['still_missing_count']} ({f['not_retried_yet']} not retried yet; each failed one is "
             "tried again at the start and the end of every run until it leaves the 365-day reach, then it is lost)",
             f"- Dropped connections still in reach: {f['dropped_in_reach']} (each gets up to skylit_dropped_extra_tries "
             "more tries at the end of the night, within the nightly credit cap; lost once it leaves the reach or uses up its tries)"]
    d = f["dropped_retries"]
    lines.append(f"- Dropped-window retries: {d['windows']} window(s), {d['tries']} tries, {d['credits']} credits "
                 "(walk-forward and sealed months as counts only)")
    lines += [f"  - {k}: {v['tries']} tries, {v['credits']} credits, {v['state']}" for k, v in d["listed"].items()]
    r = f["repulls"]
    lines.append(f"- Paid re-pulls of a kept answer (window, dark-pool date or Tempest) that was cut off, out of order or unreadable "
                 f"(one each, within the nightly cap): {r['count']}, {r['credits']} credits"
                 + (f", {r['over_cap']} of them over the cap to finish a date in progress" if r["over_cap"] else "")
                 + (f"; {r['unpaid_attempts']} attempt(s) refunded or stopped (the re-pull is still to come)"
                    if r["unpaid_attempts"] else ""))
    lines += [f"  - {k}: {v['credits']} credits" + (f" ({v['over_cap']} over the cap)" if v.get("over_cap") else "")
              + f", {v['outcome']}, now {v['state']}" for k, v in r["listed"].items()]
    for k, v in f["still_missing"].items():
        if not v["charged"]:
            paid = ""
        elif not v["raw"]:
            paid = " (paid; never called again)"
        elif v["usable"]:
            paid = f" (paid; answer kept in {v['raw']}, converted again for free)"
        else:
            paid = f" (paid; the copy kept in {v['raw']} is cut off or unreadable: " + \
                ("its one re-pull is used)" if v["repulled"] else "one paid re-pull while in reach)")
        lines.append(f"  - {k}: {v['state']}, {v['error']}{paid}")
    return lines + [""]


def report_estimate(env: step7.Env, balance: int | None, symbols: list[str]) -> dict[str, Any]:
    """The step-7 report's backfill section: what a plan made now would pull and cost, and the shortfall."""
    now = env.now()
    plan = plan_backfill(env, now, symbols)
    est = estimate(env, plan, now)
    margin = float(env.settings["skylit_pack_margin"])
    need = int(math.ceil(est.total * (1 + margin)))
    shortfall = max(0, need - balance) if balance is not None else None
    packs = cheapest_packs(shortfall, env.settings["skylit_credit_packs"]) if shortfall else None
    return {"window": [plan.first.isoformat(), plan.last.isoformat()], "weekdays": len(plan.days),
            "lost_days": [d.isoformat() for d in plan.lost_days], "dark_pool_dates": len(plan.dp_dates),
            "tempest": {"from": plan.tempest_from.isoformat(), "symbols": plan.tempest_symbols},
            "estimate": est.lines, "total": est.total, "text": est.text(), "margin": margin,
            "with_margin": need, "balance": balance, "shortfall": shortfall, "packs": packs}


def cheapest_packs(shortfall: int, packs: list[dict[str, Any]]) -> dict[str, Any]:
    """The lowest-dollar set of packs covering `shortfall` credits (ties: fewer packs, then fewer credits)."""
    if shortfall <= 0:
        return {"buy": [], "usd": 0, "credits": 0, "packs": 0}
    if not packs:
        raise ValueError("settings.yaml skylit_credit_packs is empty: add the §3.1 price list")
    best: tuple[float, int, int, tuple[int, ...]] | None = None
    limits = [int(math.ceil(shortfall / p["credits"])) for p in packs]

    def walk(i: int, counts: tuple[int, ...]) -> None:
        nonlocal best
        if i == len(packs):
            got = sum(c * p["credits"] for c, p in zip(counts, packs))
            if got >= shortfall:
                key = (sum(c * p["usd"] for c, p in zip(counts, packs)), sum(counts), got, counts)
                best = key if best is None or key[:3] < best[:3] else best
            return
        for c in range(limits[i] + 1):
            walk(i + 1, counts + (c,))

    walk(0, ())
    assert best is not None
    usd, n, got, counts = best
    return {"buy": [{"credits": p["credits"], "usd": p["usd"], "count": c} for c, p in zip(counts, packs) if c],
            "usd": usd, "credits": got, "packs": n}
