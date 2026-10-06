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
new plan. Spending is counted at max(charged, list price) per call, so the approval is never exceeded.

Coverage report (counts only, §7 rule 2b): <reports_dir>/step10/coverage.{md,json}, rewritten after every chunk.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pyarrow.parquet as pq

from nqbot import months as months_mod
from nqbot.checks import step7
from nqbot.clients import skylit_credits as credits
from nqbot.clients.skylit import DP_PAGE_LIMIT, DP_RANGE_MAX_DAYS, OutOfCredits, SkylitError, vol_history_cost
from nqbot.clients.skylit_pull import (ET, REACH_ANSWER_TOLERANCE, SKYLIT_HISTORY, Window, full_day_windows,
                                       make_client, pull_session, range_params_from_settings, sample_days,
                                       session_windows, window_reachable)
from nqbot.clients.skylit_store import CacheMismatch, SkylitStore, _norm, read_meta, vol_history_to_table, write_parquet
from nqbot.jobs.lock import SKYLIT
from nqbot.jobs.runner import Ctx, StopChunk
from nqbot.storage import Paths, append_jsonl, atomic_write_bytes, atomic_write_json, atomic_write_text, replace_retry

JOB_NAME = "skylit_backfill"
TEMPEST_CHUNK = "tempest"  # last: a Tempest problem never holds up the time-critical days
TEMPEST_ALWAYS = ("QQQ", "SPY")  # §12 step 10
TEMPEST_IF_SERVED = ("NDXP", "SPXW", step7.VIX)  # §12 step 10: if step 7 found them served
RAISE = "strike_window_raise"
# §3.1 Backfill: a chunk whose call gave up after the client's own retries (5xx, network) is tried again after these
# waits (1, 5, 15 minutes), in the same run: an overnight blip must not end the night while the oldest windows keep
# leaving the reach.
CHUNK_RETRY_SEC = (60, 300, 900)
TRANSIENT_CODES = frozenset({"gave_up"})
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
    dp_dates = step7._weekdays(step7._f4_lower(env, first), last)
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
        try:
            step7.cached_or_mismatch(env.store.dp_trades_path(d), step7._dp_params(env, d))
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
    n = 0
    for d in plan.days:
        if d in done:
            continue
        states = param_state(env, d) if env.store.stored_windows(d) else {}
        for w in session_windows(d, env.cal):
            if not window_reachable(w, now, margin):
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
    return [d for d in plan.dp_dates if not env.store.dp_trades_path(d).exists() and d not in dp_done
            and d not in covered and not (env.store.root / "darkpool" / "probe" / f"{d.isoformat()}.json").exists()]


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
                dp_pages_estimate(env, plan, todo_dp, now), credits.DP_TRADES_PAGE))
    todo = tempest_todo(env.store, plan)
    if todo:
        est.add_total(f"/v1/vol/history {todo[0]}..{todo[1]} ({', '.join(plan.tempest_symbols)})", 1,
                      vol_history_cost(len(plan.tempest_symbols), *todo))
    return est


def job_dir(paths: Paths) -> Path:
    return paths.data_dir / "jobs" / JOB_NAME


def plan_path(paths: Paths) -> Path:
    return job_dir(paths) / "plan.json"


def lost_days_path(paths: Paths) -> Path:
    return paths.data_dir / "jobs" / JOB_NAME / "lost_days.json"


def fully_lost_days(paths: Paths) -> set[date]:
    """Days the backfill could not get any window of (read by the Databento plan: it doesn't buy them)."""
    path = lost_days_path(paths)
    if not path.exists():
        return set()
    return {date.fromisoformat(d) for d in json.loads(path.read_text(encoding="utf-8"))["fully_lost"]}


def make_plan(env: step7.Env, approved: int | None) -> BackfillPlan:
    """`nqbot skylit plan`: print the estimate, balance and shortfall; refuse over 5,000 credits without an
    approval at least as large (rule 2); write the plan."""
    now = env.now()
    from nqbot.setup_ssd import check_month1, ledger_fixed

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
        env.echo(f"  balance: {balance:,} credits" + (f" - SHORT by {est.total - balance:,}: the job stops cleanly when "
                                                      "it runs out (buy the pack the step-7 report names)"
                                                      if est.total > balance else ""))
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
    env.echo(f"plan written: {plan_path(env.paths)}; start it with: uv run nqbot run {JOB_NAME} --until 07:30")
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


@dataclass
class Budget:
    approved: int  # the owner's approval (or the 5,000-credit no-ask limit when none was needed)
    spent: int
    balance: Callable[[], int | None]
    taken: list[int] = field(default_factory=list)

    def require(self, cost: int, what: str) -> None:
        if self.spent + cost > self.approved:
            raise StopChunk(f"the approved {self.approved:,} credits are used up ({self.spent:,} spent; {what} needs "
                            f"{cost}): ask the owner, then re-plan with nqbot skylit plan --approve-credits",
                            status="credits")
        bal = self.balance()
        if bal is not None and bal < cost:
            raise StopChunk(f"Skylit balance {bal:,} can't cover {what} ({cost} credits): top up (or wait for the "
                            f"1st), then re-run nqbot run {JOB_NAME}", status="credits")

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
        if balance is not None and est.total > balance:
            env.log.warning("balance %s < remaining estimate %s: the job will stop cleanly when the balance runs "
                            "out; top up, then re-run", f"{balance:,}", f"{est.total:,}")
        self._budget = Budget(approved, spent, lambda: env.client.credits.remaining)
        self._max_pages = max_pages_stored(env.store)
        dates = sorted(set(plan.days) | set(plan.dp_dates))
        return [d.isoformat() for d in dates] + [f"{TEMPEST_CHUNK}_{plan.last.isoformat()}"]

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
                ctx.sleep(CHUNK_RETRY_SEC[attempt])
                reason = ctx.should_stop()
                if reason:
                    raise StopChunk(reason) from None

    def _run_chunk(self, chunk_id: str, ctx: Ctx) -> None:
        plan, env = self.plan(ctx), self.env(ctx)
        assert self._budget is not None, "chunks() runs first"
        try:
            with env.client.tagged(job=JOB_NAME, plan=plan.plan_id):
                if chunk_id.startswith(TEMPEST_CHUNK):
                    atomic_write_json(ctx.out_dir / "tempest.json", self._tempest(env, plan))
                else:
                    d = date.fromisoformat(chunk_id)
                    rec: dict[str, Any] = {"date": chunk_id}
                    if d in set(plan.days):
                        rec["skylit"] = self._skylit_day(env, plan, d, ctx)
                    if d in set(plan.dp_dates):
                        rec["dp"] = self._dp_date(env, d, plan, ctx)
                    atomic_write_json(ctx.out_dir / "days" / f"{chunk_id}.json", rec)
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

        res = pull_session(env.client, env.store, d, should_stop=ctx.should_stop, log=ctx.log, cal=env.cal,
                           repull=RAISE if reuse.get("pull_again") else None, gate=gate, now=env.now)
        return {"windows": len(session_windows(d, env.cal)), "pulled": res.pulled, "cached": res.cached,
                "empty": res.empty, "lost": res.lost, "reuse": reuse, "resolutions": stored_resolutions(env.store, d)}

    # one dark-pool date (or, in range mode, the 31-day call that starts at it)
    def _dp_date(self, env: step7.Env, d: date, plan: BackfillPlan, ctx: Ctx) -> dict[str, Any]:
        path = env.store.dp_trades_path(d)
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
            return self._settle_range(env, d, kept[0], kept[1], [x for x in dp_todo(env, plan) if kept[0] <= x <= kept[1]]
                                      or [d], ctx)
        budget = self._budget
        edge = dp_reach_edge(env.now())
        if plan.dp_range and d > edge + DP_RANGE_REACH_MARGIN:
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
        step7.pull_dp_range_raw(env, d, group[-1],
                                before_page=lambda: budget.take(credits.DP_TRADES_PAGE, f"dark-pool page {d}.."))
        return self._settle_range(env, d, d, group[-1], group, ctx)

    def _settle_range(self, env: step7.Env, d: date, start: date, end: date, dates: list[date],
                      ctx: Ctx) -> dict[str, Any]:
        """Split a kept range answer into its per-date files and record every date it settled (free)."""
        got = step7.pull_dp_range(env, start, end, dates)
        statuses = {x: ("no_coverage" if p is None else "pulled" if env.trading_day(x) else "not_a_session")
                    for x, p in got.items()}
        atomic_write_json(ctx.out_dir / "dp_range" / f"{start.isoformat()}_{end.isoformat()}.json",
                          {"start": start.isoformat(), "end": end.isoformat(),
                           "dates": {x.isoformat(): st for x, st in statuses.items()}})
        pages = int(read_meta(step7.dp_range_path(env, start, end)).get("pages") or 1)
        mine = env.store.dp_trades_path(d)
        return {"status": statuses.get(d, "cached"), "prints": pq.ParquetFile(mine).metadata.num_rows if mine.exists() else 0,
                "via_range": [start.isoformat(), end.isoformat()], "pages": pages}

    # Tempest, once
    def _tempest(self, env: step7.Env, plan: BackfillPlan) -> dict[str, Any]:
        todo = tempest_todo(env.store, plan)
        if todo:  # only the range not stored yet (a re-plan later in the month adds just the new tail)
            symbols = plan.tempest_symbols
            self._budget.take(vol_history_cost(len(symbols), *todo), "Tempest history")
            with env.client.tagged(tempest="backfill"):
                body, rec = env.client.vol_history(symbols, *todo)
            pulled = env.now().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            write_parquet(env.store.vol_path(*todo, pulled), vol_history_to_table(body),
                          {"params": {"symbols": symbols, "from": todo[0].isoformat(), "to": todo[1].isoformat()},
                           "backfill_from": plan.tempest_from.isoformat(),
                           "pulled_at": env.now().isoformat(), "missing": (body.get("data") or {}).get("missing")})
        files = tempest_files(env.store, plan.tempest_from)
        rows = [r for p in files for r in pq.read_table(p, columns=["symbol", "date"]).to_pylist()]
        first = {s: min((r["date"] for r in rows if r["symbol"] == s), default=None) for s in plan.tempest_symbols}
        return {"files": [p.name for p in files], "needed_from": plan.tempest_from.isoformat(),
                "first_row": {s: d.isoformat() if d else None for s, d in first.items()},
                "covered": {s: d is not None and step7._sessions_between(env, plan.tempest_from, d) <= 1
                            for s, d in first.items()},
                "missing": [m for p in files for m in (read_meta(p).get("missing") or [])]}


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
    sky = {r["date"]: r["skylit"] for r in recs if "skylit" in r}
    dps = {r["date"]: r["dp"] for r in recs if "dp" in r}
    fully_lost = sorted({d.isoformat() for d in plan.lost_days if not env.store.stored_windows(d)} | {
        d for d, s in sky.items() if s["lost"] and not s["pulled"] and not s["cached"]})
    partial_all = {d: s["lost"] for d, s in sky.items() if s["lost"] and (s["pulled"] or s["cached"])}

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
    with_prints = sorted(d for d, r in dps.items() if r.get("prints"))
    dp_partial = 0
    for d in plan.days:
        if not env.trading_day(d) or d.isoformat() not in sessions:
            continue
        prev = step7.dp.previous_session(d, env.features["dp_lag_sessions"], env.trading_day)
        lower = step7._f4_lower(env, d)
        dp_partial += any(lower <= b <= prev for b in bad)

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
                       "not_pulled_count": len(bad), "sessions_dp_partial": dp_partial},
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
              f"- Sessions whose lookback touches one (dp_partial): {dp['sessions_dp_partial']}", "",
              "## Tempest", "```json", json.dumps(cov["tempest"], indent=1), "```", "",
              "## Credits", f"- {cov['credits']}"]
    return "\n".join(lines) + "\n"


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
