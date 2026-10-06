"""Databento history pull for step 8 (SPEC §3.2, §3.3, §12 step 8): plan, cost gate and the resumable job.

What is pulled, per CME trading day D ([18:00 ET of D−1, 17:00 ET of D), one contract per root, §3.2):
- window sessions (the ledger window, only days still inside Skylit's 365-day reach: days Skylit can no longer
  serve are not bought): NQ and ES `trades` + `ohlcv-1s` for the whole CME day, MNQ `trades` for the whole CME
  day (fill checks only; the NQ contract month), NQ `mbp-1` for 09:25–16:05 ET only (to `early_close_et` + 5 min
  on an early-close day);
- window weekdays that are calendar holidays but may have a Globex session: NQ and ES `trades` + `ohlcv-1s` only
  (the weekly profile and the overnight levels include their trades; they are not sessions);
- warm-up: NQ and ES `trades` + `ohlcv-1s` for the `warmup_sessions` (21) sessions before the first window session;
- rolls: the new contract's `ohlcv-1s` for the session before each switch, so the spread can be measured.

`nqbot databento plan` resolves the contracts, prints the `metadata.get_cost` estimate and writes the plan;
`nqbot run databento_backfill` pulls it oldest day first, one CME day per chunk. Pay-as-you-go items (warm-up
`trades`, days that left the included L1 window) are bought only if the plan lists them as approved (the owner's
`--approve-usd` covered them when it was written), in their own chunks, so the time-critical included pull
never waits for that decision and spending never exceeds the approved estimate.
After each chunk the day's 1- and 5-minute bars are built and any roll spread measurable that day is stored.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from databento.common.error import BentoServerError

from nqbot import calendar as cal_mod
from nqbot import months as months_mod
from nqbot.clients import databento_cost as cost_mod
from nqbot.clients.databento_client import MBP1, OHLCV_1S, TRADES, DatabentoClient
from nqbot.clients.databento_contracts import (CONTINUOUS, Roll, cme_day_span, front_by_day, micro_symbol,
                                               roll_days, roll_spread)
from nqbot.clients.databento_store import DatabentoStore, read_records
from nqbot.clients.skylit_pull import SKYLIT_HISTORY, day_reachable
from nqbot.features.bars import BAR_MINUTES, PRICE_SCALE, bars_from_records, write_bars
from nqbot.jobs.runner import Ctx, StopChunk
from nqbot.storage import Paths, atomic_write_json

ET = ZoneInfo("America/New_York")
JOB_NAME = "databento_backfill"
SKYLIT_HISTORY_DAYS = SKYLIT_HISTORY.days  # §3.1 / §12 Phase C: Skylit range history reaches 365 days back
MIN_WARMUP_SESSIONS = 20  # §3.2: warmup_sessions = max(20, the longest session lookback) + 1
# §3.2 (owner 2026-10-05): one more session than the longest lookback, because N close-to-close returns (rv_nq over
# rv_lookback_sessions, ATR's previous close) need N + 1 closes, so every rolling statistic is complete on day 1.
WARMUP_EXTRA_CLOSE = 1
WARMUP_LOOKBACK_KEYS = ("atr_period", "rv_lookback_sessions", "big_trade_lookback_sessions", "ib_avg_sessions",
                        "retest_lookback_sessions")  # §3.2, features.yaml
MBP1_OPEN = time(9, 25)  # §3.2: NQ mbp-1, RTH 09:25–16:05 ET only
MBP1_CLOSE = time(16, 5)
MBP1_EARLY_CLOSE_PAD = timedelta(minutes=5)  # §3.2: on early-close days to early_close_et + 5 min
# Waits before retrying a failed included download (transient network errors overnight). Operational, not a SPEC
# number; a pay-as-you-go download is never retried automatically (CLAUDE.md rule 2).
DOWNLOAD_RETRY_SEC = (30, 120, 300)
DOWNLOAD_RETRYABLE = (OSError, BentoServerError)  # network/socket errors and Databento 5xx; never 4xx
ROOTS = ("NQ", "ES")  # §3.2, §3.3; MNQ follows the NQ contract
WINDOW, HOLIDAY, WARMUP, ROLL = "window", "holiday", "warmup", "roll"


def warmup_sessions(features: dict[str, Any]) -> int:
    """§3.2: derived, not a config key."""
    return max([MIN_WARMUP_SESSIONS, *(int(features[k]) for k in WARMUP_LOOKBACK_KEYS)]) + WARMUP_EXTRA_CLOSE


def weekdays(start: date, end: date) -> list[date]:
    """Weekdays in [start, end] (CME days D run Sunday 18:00 -> Friday 17:00)."""
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def sessions_before(day: date, n: int, cal: dict[str, Any] | None = None) -> list[date]:
    """The n trading days right before `day` (holidays are not sessions, §1.2), oldest first."""
    out, d = [], day
    while len(out) < n:
        d -= timedelta(days=1)
        if cal_mod.is_trading_day(d, cal):
            out.append(d)
    return out[::-1]


def window_days(settings: dict[str, Any], today: date, now: datetime | None = None,
                skylit_lost: Iterable[date] = (), skylit_has: Callable[[date], bool] = lambda d: False,
                cal: dict[str, Any] | None = None) -> tuple[list[date], list[date]]:
    """(weekdays of the ledger window to pull, weekdays dropped as beyond Skylit's reach). Complete days only.

    The same timestamp rule as the Skylit backfill (presd_fixes issue 7): a day is kept only if its last Skylit
    window still starts after `now` - 365 days + skylit_reach_margin_min (default `now`: the start of `today`),
    and not if the step-10 backfill already recorded it as wholly lost. A day whose Skylit boards are already
    stored (`skylit_has`) is always kept, so a re-plan days later doesn't drop it."""
    first, last = months_mod.span(settings)
    now = now or datetime.combine(today, time(0), tzinfo=ET)
    margin, lost = float(settings["skylit_reach_margin_min"]), set(skylit_lost)
    days = weekdays(first, min(last, today - timedelta(days=1)))
    keep = [d for d in days if skylit_has(d) or (d not in lost and day_reachable(d, now, margin, cal))]
    return keep, [d for d in days if d not in set(keep)]


def mbp1_span(day: date, cal: dict[str, Any] | None = None) -> tuple[datetime, datetime]:
    early = cal_mod.early_close_et(day, cal)
    end = (datetime.combine(day, early, tzinfo=ET) + MBP1_EARLY_CLOSE_PAD if early is not None
           else datetime.combine(day, MBP1_CLOSE, tzinfo=ET))
    return datetime.combine(day, MBP1_OPEN, tzinfo=ET), end


@dataclass(frozen=True)
class PullItem:
    day: date  # CME trading day the request belongs to
    root: str  # NQ | ES | MNQ
    symbol: str  # raw contract, e.g. NQZ5
    schema: str
    start: datetime
    end: datetime
    kind: str  # window | holiday | warmup | roll

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        return {**d, "day": self.day.isoformat(), "start": self.start.isoformat(), "end": self.end.isoformat()}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> PullItem:
        return cls(date.fromisoformat(d["day"]), d["root"], d["symbol"], d["schema"],
                   datetime.fromisoformat(d["start"]), datetime.fromisoformat(d["end"]), d["kind"])

    def payg(self, today: date) -> bool:
        return cost_mod.is_payg(self.schema, self.start, today)

    @property
    def key(self) -> str:
        return f"{self.schema}|{self.symbol}|{self.day.isoformat()}"

    @property
    def label(self) -> str:
        return f"{self.day} {self.root} {self.schema} {self.symbol}"


def plan_items(window: list[date], warmup: list[date], front: dict[str, dict[date, str]], rolls: list[Roll],
               cal: dict[str, Any] | None = None) -> list[PullItem]:
    items: list[PullItem] = []

    def full_day(day: date, kind: str, schemas: Iterable[str], roots: Iterable[str] = ROOTS) -> None:
        start, end = cme_day_span(day)
        for root in roots:
            symbol = front[root].get(day)
            if symbol is None:  # only a non-session day can lack a mapping (front_by_day refuses a session)
                continue
            for schema in schemas:
                items.append(PullItem(day, root, symbol, schema, start, end, kind))

    for day in warmup:
        full_day(day, WARMUP, (TRADES, OHLCV_1S))
    for day in window:
        if not cal_mod.is_trading_day(day, cal):
            full_day(day, HOLIDAY, (TRADES, OHLCV_1S))
            continue
        full_day(day, WINDOW, (TRADES, OHLCV_1S))
        start, end = cme_day_span(day)
        items.append(PullItem(day, "MNQ", micro_symbol(front["NQ"][day]), TRADES, start, end, WINDOW))
        m0, m1 = mbp1_span(day, cal)
        items.append(PullItem(day, "NQ", front["NQ"][day], MBP1, m0, m1, WINDOW))
    pulled = set(warmup) | set(window)
    for r in rolls:
        if r.prev_session in pulled:
            start, end = cme_day_span(r.prev_session)
            items.append(PullItem(r.prev_session, r.root, r.new, OHLCV_1S, start, end, ROLL))
    return sorted(items, key=lambda i: (i.day, i.root, i.schema, i.symbol))


# --- cost ------------------------------------------------------------------------------------------------

def _groups(items: list[PullItem], today: date) -> list[list[PullItem]]:
    """Runs of consecutive weekdays with the same (root, schema, symbol, kind, payg): one get_cost call each.
    `mbp-1` covers only part of each day, so it is estimated day by day."""
    keyed: dict[tuple, list[PullItem]] = {}
    for it in sorted(items, key=lambda i: i.day):
        keyed.setdefault((it.root, it.schema, it.symbol, it.kind, it.payg(today)), []).append(it)
    out: list[list[PullItem]] = []
    for (_, schema, *_rest), its in keyed.items():
        run: list[PullItem] = []
        for it in its:
            if run and (schema == MBP1 or weekdays(run[-1].day + timedelta(days=1), it.day)[:1] != [it.day]):
                out.append(run)
                run = []
            run.append(it)
        if run:
            out.append(run)
    return out


def estimate(client: DatabentoClient, items: list[PullItem], today: date,
             what: str) -> tuple[cost_mod.Estimate, set[str]]:
    """(the printed estimate, the keys of every request that would be charged). A request is charged if its date
    is outside the included L1 window or get_cost prices it above $0: get_cost applies the plan's discounts, so a
    non-zero figure is a real charge even for data the date rule calls included (it then needs approval too)."""
    totals: dict[tuple, list[Any]] = {}  # key -> [days, usd, symbols]
    charged: set[str] = set()
    for run in _groups(items, today):
        first = run[0]
        usd = client.cost(first.schema, [first.symbol], first.start, run[-1].end)
        payg = first.payg(today) or usd > 0
        if payg:
            charged.update(it.key for it in run)
        acc = totals.setdefault((first.kind, first.root, first.schema, payg), [0, 0.0, set()])
        acc[0] += len(run)
        acc[1] += usd
        acc[2].add(first.symbol)
    est = cost_mod.Estimate(what)
    for (kind, root, schema, payg), (days, usd, symbols) in sorted(totals.items()):
        est.add(f"{kind} {root} {schema} ({', '.join(sorted(symbols))})", days, usd, payg)
    return est, charged


# --- plan ------------------------------------------------------------------------------------------------

class PlanRefused(Exception):
    pass


def plan_path(paths: Paths) -> Path:
    return paths.data_dir / "jobs" / JOB_NAME / "plan.json"


def make_plan(client: DatabentoClient, store: DatabentoStore, settings: dict[str, Any], features: dict[str, Any],
              today: date, *, approved_usd: float | None = None, cal: dict[str, Any] | None = None,
              echo: Callable[[str], None] = print, now: datetime | None = None) -> dict[str, Any]:
    """Resolve contracts, list the items, print the estimate, write the plan (no data is pulled here)."""
    from nqbot.clients.skylit_backfill import fully_lost_days
    from nqbot.clients.skylit_store import SkylitStore

    sky = SkylitStore(store.paths)
    window, cut = window_days(settings, today, now, fully_lost_days(store.paths),
                              skylit_has=lambda d: bool(sky.stored_windows(d)), cal=cal)
    sessions = [d for d in window if cal_mod.is_trading_day(d, cal)]
    if not sessions:
        raise PlanRefused("no window session to pull (is the ledger window inside Skylit's 365-day reach?)")
    warmup = sessions_before(sessions[0], warmup_sessions(features), cal)
    days = sorted(set(warmup) | set(window))
    mappings, answers = client.resolve_front(CONTINUOUS, days[0], days[-1] + timedelta(days=1))
    sym_file = store.save_symbology(answers)
    front = front_by_day(mappings, days, cal)
    rolls = roll_days(front, cal)
    items = plan_items(window, warmup, front, rolls, cal)

    echo(f"window: {len(window)} weekdays {window[0]}..{window[-1]} ({len(sessions)} sessions); "
         f"warm-up: {len(warmup)} sessions {warmup[0]}..{warmup[-1]}")
    if cut:
        echo(f"not bought (beyond Skylit's {SKYLIT_HISTORY_DAYS}-day reach): {len(cut)} weekdays {cut[0]}..{cut[-1]}")
    for r in rolls:
        echo(f"roll {r.root}: {r.old} -> {r.new} from {r.roll_day} (spread measured at 17:00 ET {r.prev_session})")
    todo = [it for it in items if not store.raw_path(it.schema, it.symbol, it.day).exists()]  # a re-plan: not yet bought
    est, charged = estimate(client, todo, today, f"step 8, {len(todo)} requests not yet pulled (of {len(items)})")
    payg_ok = cost_mod.check(est, approved_usd=approved_usd, echo=echo)
    plan = {
        "created": datetime.now(ET).isoformat(), "today": today.isoformat(), "approved_usd": approved_usd,
        # Requests that would be charged (by date or by a non-zero get_cost), and the exact ones this approval
        # covers: the job buys those and nothing else, so spending can't exceed the approved estimate even if more
        # days age out of the included window later.
        "payg_keys": sorted(charged),
        "approved_payg": sorted(charged) if payg_ok else [],
        "symbology_file": str(sym_file), "cut_days": [d.isoformat() for d in cut],
        "front": {root: {d.isoformat(): s for d, s in per.items()} for root, per in front.items()},
        "rolls": [{**asdict(r), "roll_day": r.roll_day.isoformat(), "prev_session": r.prev_session.isoformat()}
                  for r in rolls],
        "items": [it.to_json() for it in items],
    }
    atomic_write_json(plan_path(store.paths), plan)
    echo(f"plan written: {plan_path(store.paths)}; pull with: uv run nqbot run {JOB_NAME} --until 07:30")
    return plan


# --- job -------------------------------------------------------------------------------------------------

def _rolls(plan: dict[str, Any]) -> list[Roll]:
    return [Roll(r["root"], date.fromisoformat(r["roll_day"]), date.fromisoformat(r["prev_session"]), r["old"], r["new"])
            for r in plan["rolls"]]


def default_client(ctx: Ctx, store: DatabentoStore) -> DatabentoClient:
    return DatabentoClient.from_env(on_call=store.log_call, log=ctx.log)


class DatabentoPullJob:
    """`nqbot run databento_backfill`: one chunk per CME day and part, oldest first.

    Chunk ids are `<day>:incl:<h>` (requests inside the plan) and `<day>:payg:<h>` (the owner-approved
    pay-as-you-go requests), where <h> fingerprints the day's requests still missing, so a day that gains
    requests (a re-plan) or still lacks its bars or roll spread is listed again. Each chunk re-classifies its
    requests with the current date: one that left the included window mid-run is skipped, never bought
    unapproved; it waits for a re-plan with --approve-usd.
    """

    name = JOB_NAME

    def __init__(self, client_factory: Callable[[Ctx, DatabentoStore], DatabentoClient] = default_client,
                 today: Callable[[], date] = lambda: datetime.now(ET).date()):
        self.client_factory = client_factory
        self.today = today
        self._client: DatabentoClient | None = None

    def _plan(self, ctx: Ctx) -> dict[str, Any]:
        path = plan_path(ctx.paths)
        if not path.exists():
            raise SystemExit(f"{self.name}: no {path}; run `uv run nqbot databento plan` first")
        return json.loads(path.read_text(encoding="utf-8"))

    def _client_for(self, ctx: Ctx, store: DatabentoStore) -> DatabentoClient:
        if self._client is None:
            self._client = self.client_factory(ctx, store)
        return self._client

    @staticmethod
    def _part(it: PullItem, today: date, plan: dict[str, Any]) -> str | None:
        """incl | payg (approved) | None (charged, not approved: waits)."""
        if not it.payg(today) and it.key not in set(plan.get("payg_keys") or []):
            return "incl"
        return "payg" if it.key in set(plan.get("approved_payg") or []) else None

    def chunks(self, ctx: Ctx) -> list[str]:
        plan = self._plan(ctx)
        store = DatabentoStore(ctx.paths)
        today = self.today()
        plan = {**plan, "payg_keys": set(plan.get("payg_keys") or []), "approved_payg": set(plan.get("approved_payg") or [])}
        items = [PullItem.from_json(d) for d in plan["items"]]
        missing = [it for it in items if not store.raw_path(it.schema, it.symbol, it.day).exists()]
        waiting = [it for it in missing if self._part(it, today, plan) is None]
        if waiting:
            client = self._client_for(ctx, store)
            est, _ = estimate(client, waiting, today, f"{self.name}: {len(waiting)} pay-as-you-go requests not approved")
            cost_mod.check(est, approved_usd=None, echo=ctx.log.warning)
        for err in store.roll_errors():
            ctx.log.error("roll spread missing (the roll day can't be shifted until it is fixed): %s", err["error"])
        parts: dict[tuple[date, str], list[str]] = {}
        for it in missing:
            part = self._part(it, today, plan)
            if part is not None:
                parts.setdefault((it.day, part), []).append(it.key)
        for day in sorted({it.day for it in items}):
            if (day, "incl") not in parts and needs_outputs(store, plan, day):
                parts[(day, "incl")] = []
        order = {"payg": 0, "incl": 1}
        return [f"{day.isoformat()}:{part}:{_fingerprint(keys)}"
                for (day, part), keys in sorted(parts.items(), key=lambda kv: (kv[0][0], order[kv[0][1]]))]

    def run_chunk(self, chunk_id: str, ctx: Ctx) -> None:
        plan = self._plan(ctx)
        store = DatabentoStore(ctx.paths)
        client = self._client_for(ctx, store)
        day_text, part, _ = chunk_id.split(":")
        day = date.fromisoformat(day_text)
        today = self.today()
        plan = {**plan, "payg_keys": set(plan.get("payg_keys") or []), "approved_payg": set(plan.get("approved_payg") or [])}
        for it in (PullItem.from_json(d) for d in plan["items"]):
            if it.day != day:
                continue
            reason = ctx.should_stop()
            if reason:
                raise StopChunk(reason)
            if store.cached(it.schema, it.symbol, it.day, it.start, it.end, client.dataset):
                continue
            now_part = self._part(it, today, plan)
            if now_part != part:
                if part == "incl" and now_part != "incl":
                    ctx.log.warning("databento %s left the included window: not bought (needs approval)", it.label)
                continue
            size = self._download(client, store, it, part, ctx)
            ctx.log.info("databento %s: %.1f MB%s", it.label, size / 1e6, " (pay-as-you-go)" if part == "payg" else "")
        build_day_outputs(store, plan, day, ctx.log)


    @staticmethod
    def _download(client: DatabentoClient, store: DatabentoStore, it: PullItem, part: str, ctx: Ctx) -> int:
        """Included requests are retried after DOWNLOAD_RETRY_SEC waits on a failed download (nothing was stored,
        so this is not a re-pull); a pay-as-you-go one is never retried automatically (CLAUDE.md rule 2)."""
        waits = list(DOWNLOAD_RETRY_SEC) if part == "incl" else []
        while True:
            try:
                return client.download(it.schema, it.symbol, it.start, it.end,
                                       store.raw_path(it.schema, it.symbol, it.day),
                                       item=it.kind, payg=part == "payg", day=it.day.isoformat())
            except DOWNLOAD_RETRYABLE as e:
                if not waits:
                    raise
                wait = waits.pop(0)
                ctx.log.warning("databento %s failed (%s): retrying in %ds", it.label, e, wait)
                reason = ctx.should_stop()
                if reason:
                    raise StopChunk(reason) from e
                ctx.sleep(wait)


def _fingerprint(keys: list[str]) -> str:
    return hashlib.sha1("\n".join(sorted(keys)).encode()).hexdigest()[:8]


def _day_symbol(plan: dict[str, Any], root: str, day: date) -> str | None:
    return (plan["front"].get(root) or {}).get(day.isoformat())


def needs_outputs(store: DatabentoStore, plan: dict[str, Any], day: date) -> bool:
    """Trades stored but bars missing, or both contracts' 1-second bars stored but the roll spread missing."""
    for root in ROOTS:
        symbol = _day_symbol(plan, root, day)
        if symbol and store.raw_path(TRADES, symbol, day).exists() and not all(
                store.bars_path(root, day, m).exists() for m in BAR_MINUTES):
            return True
    return any(r.prev_session == day and not store.roll_path(r.root, r.roll_day).exists()
               and not store.roll_error_path(r.root, r.roll_day).exists()
               and store.raw_path(OHLCV_1S, r.old, day).exists() and store.raw_path(OHLCV_1S, r.new, day).exists()
               for r in _rolls(plan))


def build_day_outputs(store: DatabentoStore, plan: dict[str, Any], day: date, log: logging.Logger) -> None:
    """Bars for each root whose trades are stored, and any roll spread measured on this day (idempotent)."""
    for root in ROOTS:
        symbol = _day_symbol(plan, root, day)
        src = store.raw_path(TRADES, symbol, day) if symbol else None
        if src is None or not src.exists():
            continue
        records = None
        for minutes in BAR_MINUTES:
            out = store.bars_path(root, day, minutes)
            if out.exists():  # raw files never change once stored, so neither do their bars
                continue
            records = read_records(src) if records is None else records
            write_bars(out, bars_from_records(records, minutes), symbol=symbol, day=day, minutes=minutes)
    for r in _rolls(plan):
        out = store.roll_path(r.root, r.roll_day)
        if r.prev_session != day or out.exists():
            continue
        old, new = store.raw_path(OHLCV_1S, r.old, day), store.raw_path(OHLCV_1S, r.new, day)
        if not (old.exists() and new.exists()):
            continue
        a, b = read_records(old), read_records(new)
        try:
            rec = roll_spread(r, (a["ts_event"], a["close"] / PRICE_SCALE), (b["ts_event"], b["close"] / PRICE_SCALE))
        except ValueError as e:  # reported, never guessed: the roll day's carried levels need this spread
            log.error("%s", e)
            atomic_write_json(store.roll_error_path(r.root, r.roll_day), {"root": r.root, "roll_day": r.roll_day.isoformat(),
                                                                         "error": str(e)})
            continue
        store.roll_error_path(r.root, r.roll_day).unlink(missing_ok=True)
        atomic_write_json(out, rec)
        log.info("roll %s %s -> %s on %s: spread %+.2f", r.root, r.old, r.new, r.roll_day, rec["spread"])
