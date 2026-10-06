"""The step-10 Skylit backfill (SPEC §3.1 Backfill, Dark pools, §12 step 10): plan, credit budget, the resumable
job `skylit_backfill` and its coverage report.

`nqbot skylit plan [--approve-credits N]` freezes what to pull and the owner's approval (after the step-7 report
and the pack purchase, decision 2 in docs/presd_fixes.md); `nqbot run skylit_backfill --until 07:30` pulls it,
oldest day first, one date per chunk:
- the Skylit RTH windows of every weekday of the ledger window up to yesterday (holiday probe, early closes to
  early_close_et plus a probe window: skylit_pull.session_windows). A window is pulled only while it is still
  inside Skylit's 365-day reach, checked just before its call; one that isn't is recorded as lost;
- that date's QQQ/SPY dark-pool prints, one date per call (the window plus dp_lookback_days before month 1);
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
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pyarrow.parquet as pq

from nqbot import months as months_mod
from nqbot.checks import step7
from nqbot.clients import skylit_credits as credits
from nqbot.clients.skylit import OutOfCredits, SkylitError, vol_history_cost
from nqbot.clients.skylit_pull import (Window, full_day_windows, make_client, pull_session,
                                       range_params_from_settings, session_windows, window_reachable)
from nqbot.clients.skylit_store import CacheMismatch, SkylitStore, _norm, read_meta, vol_history_to_table, write_parquet
from nqbot.jobs.runner import Ctx, StopChunk
from nqbot.storage import Paths, atomic_write_bytes, atomic_write_json, atomic_write_text

JOB_NAME = "skylit_backfill"
TEMPEST_CHUNK = "tempest"  # last: a Tempest problem never holds up the time-critical days
TEMPEST_ALWAYS = ("QQQ", "SPY")  # §12 step 10
TEMPEST_IF_SERVED = ("NDXP", "SPXW", step7.VIX)  # §12 step 10: if step 7 found them served
RAISE = "strike_window_raise"
# A chunk whose call gave up after the client's own retries (5xx, network) is tried again after these waits, in the
# same run: an overnight blip must not end the night while the oldest windows keep leaving the reach. Operational,
# not a SPEC number.
CHUNK_RETRY_SEC = (60, 300, 900)
TRANSIENT_CODES = frozenset({"gave_up"})
# Pages reserved before a dark-pool date starts: twice the most pages any date has needed, so a stop mid-date
# (which would throw paid pages away) needs a date with more than twice the largest seen.
DP_PAGE_RESERVE_FACTOR = 2
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
                "estimate_total": self.estimate_total}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> BackfillPlan:
        ds = lambda xs: [date.fromisoformat(x) for x in xs]  # noqa: E731
        return cls(first=date.fromisoformat(d["window"][0]), last=date.fromisoformat(d["window"][1]),
                   days=ds(d["days"]), lost_days=ds(d["lost_days"]), dp_dates=ds(d["dp_dates"]),
                   tempest_from=date.fromisoformat(d["tempest"]["from"]), tempest_symbols=list(d["tempest"]["symbols"]),
                   max_strikes=d["max_strikes"], created=d["created"], approved_credits=d["approved_credits"],
                   estimate_total=d.get("estimate_total", 0))


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
                        max_strikes=env.settings["skylit_max_strikes"])


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
    bad = [f"{d} {hhmm}: {have}" for d in days if env.store.stored_windows(d)
           for hhmm, (state, have) in param_state(env, d).items() if state == "other"]
    for d in dp_dates:
        try:
            step7.cached_or_mismatch(env.store.dp_trades_path(d), step7._dp_params(env, d))
        except CacheMismatch as e:
            bad.append(f"dark pools {d}: {e}")
    if bad:
        raise BackfillRefused("cached windows were pulled with other parameters (not just maxStrikes): "
                              + "; ".join(bad[:5]) + (" ..." if len(bad) > 5 else "") + ". Ask the owner (§3.1).")


def _raise_copy_ok(env: step7.Env, day: date, hhmm: str, w: Window, base: Path) -> bool:
    try:
        return env.store.range_cached(day, hhmm, range_params_from_settings(env.settings, w), base)
    except CacheMismatch:
        return False


def windows_todo(env: step7.Env, plan: BackfillPlan, now: datetime) -> int:
    """Range calls still needed: reachable windows not cached with the current parameters (a raised sample day's
    matching strike_window_raise copy counts as cached)."""
    margin = margin_min(env.settings)
    raise_base = env.store.base(RAISE, pulled=f"max{env.settings['skylit_max_strikes']}")
    n = 0
    for d in plan.days:
        states = param_state(env, d) if env.store.stored_windows(d) else {}
        for w in session_windows(d, env.cal):
            if not window_reachable(w, now, margin):
                continue
            state = states.get(w.hhmm, ("absent", {}))[0]
            if state == "match" or (state == "strikes_only" and _raise_copy_ok(env, d, w.hhmm, w, raise_base)):
                continue
            n += 1
    return n


def estimate(env: step7.Env, plan: BackfillPlan, now: datetime) -> credits.Estimate:
    dp_todo = [d for d in plan.dp_dates if not env.store.dp_trades_path(d).exists()]
    est = (credits.range_estimate(f"step-10 backfill {plan.first}..{plan.last}", windows_todo(env, plan, now))
           .add("/v1/dark-pool/trades pages", int(math.ceil(len(dp_todo) * step7._pages_per_date(env))),
                credits.DP_TRADES_PAGE))
    if not env.store.vol_files(plan.tempest_from, plan.last):
        est.add_total(f"/v1/vol/history {plan.tempest_from}..{plan.last} ({', '.join(plan.tempest_symbols)})", 1,
                      vol_history_cost(len(plan.tempest_symbols), plan.tempest_from, plan.last))
    return est


def plan_path(paths: Paths) -> Path:
    return paths.data_dir / "jobs" / JOB_NAME / "plan.json"


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


def spent_on_plan(store: SkylitStore, plan_id: str) -> int:
    return sum(max(r.get("charged") or 0, nominal_cost(r)) for r in store.ledger()
               if r.get("job") == JOB_NAME and r.get("plan") == plan_id and (r.get("status") or 999) < 400)


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
        self._max_pages = max(1, int(math.ceil(step7._pages_per_date(env))))
        dates = sorted(set(plan.days) | set(plan.dp_dates))
        return [d.isoformat() for d in dates] + [TEMPEST_CHUNK]

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
                if chunk_id == TEMPEST_CHUNK:
                    atomic_write_json(ctx.out_dir / "tempest.json", self._tempest(env, plan))
                else:
                    d = date.fromisoformat(chunk_id)
                    rec: dict[str, Any] = {"date": chunk_id}
                    if d in set(plan.days):
                        rec["skylit"] = self._skylit_day(env, plan, d, ctx)
                    if d in set(plan.dp_dates):
                        rec["dp"] = self._dp_date(env, d)
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

    # one dark-pool date
    def _dp_date(self, env: step7.Env, d: date) -> dict[str, Any]:
        path = env.store.dp_trades_path(d)
        if path.exists():
            step7.pull_dp_date(env, d)  # parameter check only (cached)
            return {"status": "cached", "prints": pq.ParquetFile(path).metadata.num_rows}
        probe = env.store.root / "darkpool" / "probe" / f"{d.isoformat()}.json"
        if probe.exists():  # step 7 already paid for this answer: not asked again (rule 2)
            return {"status": json.loads(probe.read_text(encoding="utf-8"))["status"], "prints": 0, "from_probe": True}
        budget = self._budget
        budget.require(credits.DP_TRADES_PAGE * DP_PAGE_RESERVE_FACTOR * self._max_pages,
                       f"dark pools {d}")  # don't start a date it can't end
        try:
            got = step7.pull_dp_date(env, d, keep_empty_trading_day=False,
                                     before_page=lambda: budget.take(credits.DP_TRADES_PAGE, f"dark-pool page {d}"))
        except OutOfCredits:
            raise
        except SkylitError as e:
            if not step7.dp_out_of_reach(e):
                raise
            env.log.warning("dark pools %s: answered %s %s: out of reach (not cached)", d, e.status, e.code)
            return {"status": "out_of_reach", "prints": 0}
        if got is None:
            return {"status": "no_coverage", "prints": 0}
        meta = read_meta(got)
        self._max_pages = max(self._max_pages, int(meta.get("pages") or 1))
        n = pq.ParquetFile(got).metadata.num_rows
        return {"status": "pulled" if env.trading_day(d) else "not_a_session", "prints": n, "pages": meta.get("pages")}

    # Tempest, once
    def _tempest(self, env: step7.Env, plan: BackfillPlan) -> dict[str, Any]:
        files = env.store.vol_files(plan.tempest_from, plan.last)
        if files:
            path = files[0]
        else:
            symbols = plan.tempest_symbols
            self._budget.take(vol_history_cost(len(symbols), plan.tempest_from, plan.last), "Tempest history")
            with env.client.tagged(tempest="backfill"):
                body, rec = env.client.vol_history(symbols, plan.tempest_from, plan.last)
            pulled = env.now().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            path = env.store.vol_path(plan.tempest_from, plan.last, pulled)
            write_parquet(path, vol_history_to_table(body),
                          {"params": {"symbols": symbols, "from": plan.tempest_from.isoformat(),
                                      "to": plan.last.isoformat()},
                           "pulled_at": env.now().isoformat(), "missing": (body.get("data") or {}).get("missing")})
        t = pq.read_table(path, columns=["symbol", "date"]).to_pylist()
        first = {s: min((r["date"] for r in t if r["symbol"] == s), default=None) for s in plan.tempest_symbols}
        return {"file": path.name, "needed_from": plan.tempest_from.isoformat(),
                "first_row": {s: d.isoformat() if d else None for s, d in first.items()},
                "covered": {s: d is not None and step7._sessions_between(env, plan.tempest_from, d) <= 1
                            for s, d in first.items()},
                "missing": read_meta(path).get("missing")}


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
            os.replace(src, dst)
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
    fully_lost = [d.isoformat() for d in plan.lost_days if not env.store.stored_windows(d)] + sorted(
        d for d, s in sky.items() if s["lost"] and not s["pulled"] and not s["cached"])
    partial = {d: s["lost"] for d, s in sky.items() if s["lost"] and (s["pulled"] or s["cached"])}

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
        "plan": plan.plan_id, "window": [plan.first.isoformat(), plan.last.isoformat()],
        "progress": {"dates_done": len(done), "dates_planned": len(todo), "tempest_done": tempest_file.exists()},
        "skylit": {
            "beyond_365_days": {"fully_lost": fully_lost, "partial_days": partial},
            "sessions_with_boards": len(with_boards),
            "sessions_1m": len(one_min), "share_sessions_1m": round(len(one_min) / len(with_boards), 4) if with_boards else None,
            "windows_with_boards": len(windows),
            "share_windows_1m": round(sum(v == "1m" for v in windows) / len(windows), 4) if windows else None,
            "sessions_1m_dates": one_min, "per_month": dict(sorted(per_month.items())),
            "empty_windows": sum(len(s["empty"]) for s in sky.values()),
            "reused_raise_copies": {d: s["reuse"] for d, s in sky.items() if s.get("reuse")},
        },
        "dark_pools": {"status_counts": dp_status, "first_date_with_prints": with_prints[0] if with_prints else None,
                       "not_pulled": [d.isoformat() for d in bad], "sessions_dp_partial": dp_partial},
        "tempest": json.loads(tempest_file.read_text(encoding="utf-8")) if tempest_file.exists() else None,
        "credits": {"approved": budget.approved if budget else plan.approved_credits,
                    "spent_on_plan": budget.spent if budget else None, "balance": env.client.credits.remaining
                    if env._client is not None else None},
    }


def write_coverage(env: step7.Env, plan: BackfillPlan, out_dir: Path, budget: Budget | None = None) -> dict[str, Any]:
    cov = coverage(env, plan, out_dir, budget)
    lost = cov["skylit"]["beyond_365_days"]
    atomic_write_json(lost_days_path(env.paths), {"fully_lost": lost["fully_lost"], "partial": lost["partial_days"]})
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
        f"- Wholly lost (nothing pulled): {len(lost['fully_lost'])}" + (f" ({', '.join(lost['fully_lost'])})"
                                                                      if lost["fully_lost"] else ""),
        f"- Partly lost (windows lost): {len(lost['partial_days'])}" + "".join(
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
              f"- Dates not pulled (out of reach or no coverage): {len(dp['not_pulled'])}",
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
