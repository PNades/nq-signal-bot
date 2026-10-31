"""Multi-expiration GEX snapshots (owner 2026-10-23, SPEC §3.1 Multi-expiration snapshots): raw store only.

One `/v1/historical` call per session and tier: QQQ, NDXP, NDX, SPY, SPXW and SPX together, gamma, `layout=matrix`
(each strike's value per expiration), every expiration, every strike, 5 credits a call. Sessions: the warm-up
sessions before month 1, every session of the window and every later session up to `--end` (stored like any top-up
data: sealed, §7 rule 2b). Tiers in the owner's priority order, each across all sessions: 09:25 ET, then 10:00 ET.

    uv run nqbot skylit multiexp-plan --tiers 0925,1000 --end YYYY-MM-DD [--approve-credits N]
    uv run nqbot run skylit_multiexp --until HH:MM
    uv run nqbot skylit multiexp-coverage

Every answer is kept as delivered (zstd JSON with the call record) under `raw/skylit/multiexp/<tier>/<date>.json.zst`
and never pulled again (CLAUDE.md rule 2). Nothing reads it into features until the owner approves the design.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import zstandard

from nqbot import calendar as cal_mod
from nqbot import months as months_mod
from nqbot.clients.skylit import AccessDenied, OutOfCredits, SkylitClient, SkylitError, iso_utc
from nqbot.clients.skylit_store import SkylitStore
from nqbot.jobs.lock import SKYLIT
from nqbot.jobs.runner import Ctx, StopChunk, record_plan
from nqbot.storage import Paths, append_jsonl, atomic_write_bytes, atomic_write_json, atomic_write_text
from nqbot.warmup import warmup_sessions

ET = ZoneInfo("America/New_York")
JOB_NAME = "skylit_multiexp"
LEDGER = "ledger_multiexp.jsonl"
ROOTS = ("QQQ", "NDXP", "NDX", "SPY", "SPXW", "SPX")  # owner 2026-10-23; one call, 0.7 MB, < 2 s (test 2026-10-23)
PARAMS = {"metric": "gamma", "layout": "matrix", "maxExpirations": "all", "maxStrikes": "all"}  # owner 2026-10-23
TIERS = {"0925": time(9, 25), "1000": time(10, 0)}  # owner 2026-10-23, in priority order
CREDITS_PER_CALL = 5  # §3.1 Endpoints: /v1/historical
HARD_CAP_CREDITS = 10_000  # owner 2026-10-23: this pull never spends more
TEST_CALL = re.compile(r"(\d{4}-\d{2}-\d{2})_(\d{4})_" + "-".join(ROOTS) + r"\.json$")


class MultiExpRefused(Exception):
    pass


def base(paths: Paths) -> Path:
    return paths.raw_dir / "skylit" / "multiexp"


def job_dir(paths: Paths) -> Path:
    return paths.data_dir / "jobs" / JOB_NAME


def snapshot_path(paths: Paths, tier: str, day: date) -> Path:
    return base(paths) / tier / f"{day.isoformat()}.json.zst"


def at_of(tier: str, day: date) -> datetime:
    return datetime.combine(day, TIERS[tier], tzinfo=ET)


def sessions(settings: dict[str, Any], features: dict[str, Any], end: date,
             cal: dict[str, Any] | None = None) -> list[date]:
    """The warm-up sessions before month 1, then every trading day from month 1's first day to `end`."""
    first, _ = months_mod.span(settings)
    out: list[date] = []
    d = first - timedelta(days=1)
    while len(out) < warmup_sessions(features):
        if cal_mod.is_trading_day(d, cal):
            out.append(d)
        d -= timedelta(days=1)
    out.reverse()
    d = first
    while d <= end:
        if cal_mod.is_trading_day(d, cal):
            out.append(d)
        d += timedelta(days=1)
    return out


def write_snapshot(path: Path, body: dict[str, Any], call: dict[str, Any], requested_at: datetime) -> None:
    doc = {"requested_at": requested_at.isoformat(), "params": {"symbols": ",".join(ROOTS), **PARAMS},
           "call": call, "body": body}
    atomic_write_bytes(path, zstandard.ZstdCompressor(level=10).compress(json.dumps(doc).encode()))


def read_snapshot(path: Path) -> dict[str, Any]:
    return json.loads(zstandard.ZstdDecompressor().decompress(path.read_bytes(), max_output_size=1 << 30))


def import_test_calls(paths: Paths) -> list[str]:
    """The owner-requested test calls (same symbols and parameters) join the store, so they are never paid again."""
    done = []
    test_dir = base(paths) / "test"
    for f in sorted(test_dir.glob("*.json")) if test_dir.exists() else []:
        m = TEST_CALL.match(f.name)
        if not m or m.group(2) not in TIERS:
            continue
        day = date.fromisoformat(m.group(1))
        dst = snapshot_path(paths, m.group(2), day)
        if not dst.exists():
            write_snapshot(dst, json.loads(f.read_text(encoding="utf-8")), {"source": f"test call {f.name}"},
                           at_of(m.group(2), day))
            done.append(f"{m.group(2)}:{day}")
    return done


def missing(paths: Paths, tiers: list[str], days: list[date]) -> list[tuple[str, date]]:
    return [(t, d) for t in tiers for d in days if not snapshot_path(paths, t, d).exists()]


def chunk_ids(plan: dict[str, Any]) -> list[str]:
    """One chunk `<tier>:<date>` per session and tier, tier by tier (job and re-plan alike)."""
    return [f"{t}:{d}" for t in plan["tiers"] for d in plan["days"]]


def make_plan(paths: Paths, settings: dict[str, Any], features: dict[str, Any], tiers: list[str], end: date,
              approve: int | None, balance: int | None, echo: Callable[[str], None] = print,
              cal: dict[str, Any] | None = None) -> dict[str, Any]:
    """Print the cost per tier, refuse what isn't approved or doesn't fit, write the plan."""
    unknown = [t for t in tiers if t not in TIERS]
    if unknown or not tiers:
        raise MultiExpRefused(f"unknown tiers {unknown}; known: {', '.join(TIERS)}")
    tiers = [t for t in TIERS if t in tiers]  # the owner's priority order
    imported = import_test_calls(paths)
    days = sessions(settings, features, end, cal)
    echo(f"sessions: {len(days)} ({days[0]} .. {days[-1]}); symbols {','.join(ROOTS)}; "
         f"{CREDITS_PER_CALL} credits a call; test calls reused: {', '.join(imported) or 'none new'}")
    total = 0
    for t in tiers:
        n = len(missing(paths, [t], days))
        total += n * CREDITS_PER_CALL
        echo(f"  tier {t} ET: {n} calls to pull x {CREDITS_PER_CALL} = {n * CREDITS_PER_CALL:,} credits "
             f"(cumulative {total:,})")
    echo(f"total {total:,} credits; hard cap {HARD_CAP_CREDITS:,}; balance {balance if balance is not None else '?'}")
    if total > HARD_CAP_CREDITS:
        raise MultiExpRefused(f"{total:,} credits is over the owner's hard cap of {HARD_CAP_CREDITS:,}: drop a tier")
    reserve = int(settings["skylit_alongside_reserve_credits"])
    if balance is not None and balance - total < reserve:
        raise MultiExpRefused(f"balance {balance:,} - {total:,} leaves less than the {reserve:,}-credit reserve")
    if approve is None or approve < total:  # owner 2026-10-23: the tiers wait for his OK, whatever the size
        raise MultiExpRefused(f"{total:,} credits needs the owner's OK: --approve-credits {total} (owner 2026-10-23)")
    plan = {"tiers": tiers, "days": [d.isoformat() for d in days], "end": end.isoformat(),
            "approved_credits": approve, "estimate": total,
            "plan_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}
    atomic_write_json(job_dir(paths) / "plan.json", plan)
    done, n = record_plan(paths, JOB_NAME, chunk_ids(plan))
    echo(f"plan written: {job_dir(paths) / 'plan.json'} ({done}/{n} chunks already done); run: "
         f"uv run nqbot run {JOB_NAME} --until HH:MM")
    return plan


def make_client(settings: dict[str, Any], store: SkylitStore, log: logging.Logger) -> SkylitClient:
    from nqbot.secrets import get_secret

    client = SkylitClient(get_secret("SKYLIT_API_KEY"), symbols_map={r: r for r in ROOTS},
                          credit_warn=settings.get("skylit_credit_warn"), log=log, on_call=store.log_call)
    client.account()  # free: limits and the balance header
    return client


class MultiExpJob:
    """`nqbot run skylit_multiexp`: one call per chunk `<tier>:<date>`, tier by tier, oldest session first."""

    name = JOB_NAME
    locks = (SKYLIT,)

    def __init__(self, client_factory: Callable[..., Any] = make_client):
        self.client_factory = client_factory
        self._client: Any = None
        self._plan: dict[str, Any] | None = None
        self._in_row = 0

    def plan(self, ctx: Ctx) -> dict[str, Any]:
        if self._plan is None:
            path = job_dir(ctx.paths) / "plan.json"
            if not path.exists():
                raise MultiExpRefused(f"no {path}: write it first with uv run nqbot skylit multiexp-plan")
            self._plan = json.loads(path.read_text(encoding="utf-8"))
        return self._plan

    def chunks(self, ctx: Ctx) -> list[str]:
        return chunk_ids(self.plan(ctx))

    def spent(self, ctx: Ctx) -> int:
        """Credits this plan's calls were charged (from the ledger; an unknown charge counts as a full call)."""
        path = ctx.paths.raw_dir / "skylit" / LEDGER
        if not path.exists():
            return 0
        pid = self.plan(ctx)["plan_id"]
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return sum((r["charged"] if r.get("charged") is not None else CREDITS_PER_CALL)
                   for r in rows if r.get("plan_id") == pid and (r.get("status") or 0) < 400)

    def client(self, ctx: Ctx) -> Any:
        if self._client is None:
            self._client = self.client_factory(ctx.settings, SkylitStore(ctx.paths, ledger_name=LEDGER), ctx.log)
        return self._client

    def run_chunk(self, chunk_id: str, ctx: Ctx) -> None:
        tier, day_s = chunk_id.split(":")
        day = date.fromisoformat(day_s)
        path = snapshot_path(ctx.paths, tier, day)
        if path.exists():
            return
        p = self.plan(ctx)
        if self.spent(ctx) + CREDITS_PER_CALL > int(p["approved_credits"]):
            raise StopChunk(f"approved {p['approved_credits']:,} credits used", status="credits")
        client = self.client(ctx)
        remaining = client.credits.remaining
        reserve = int(ctx.settings["skylit_alongside_reserve_credits"])
        if remaining is not None and remaining - CREDITS_PER_CALL < reserve:
            raise StopChunk(f"balance {remaining:,} at the {reserve:,}-credit reserve", status="credits")
        at = at_of(tier, day)
        params = {"symbols": ",".join(ROOTS), "at": iso_utc(at), **PARAMS}
        try:
            with client.tagged(plan_id=p["plan_id"], chunk=chunk_id):
                body, rec = client.get("/v1/historical", params)
        except (OutOfCredits, AccessDenied) as e:
            raise StopChunk(str(e), status="credits" if isinstance(e, OutOfCredits) else "api_error") from None
        except SkylitError as e:  # recorded, not retried by this job (a later plan re-tries what is missing)
            self._fail(ctx, chunk_id, e)
            return
        self._in_row = 0
        write_snapshot(path, body, asdict(rec), at)

    def _fail(self, ctx: Ctx, chunk_id: str, e: SkylitError) -> None:
        if e.raw:
            atomic_write_bytes(base(ctx.paths) / "failed" / f"{chunk_id.replace(':', '_')}.bin", e.raw)
        append_jsonl(job_dir(ctx.paths) / "failed.jsonl", {"chunk": chunk_id, "status": e.status, "code": e.code,
                                                            "message": str(e)[:500],
                                                            "at": datetime.now(timezone.utc).isoformat()})
        ctx.log.warning("%s: %s %s", chunk_id, e.status, e.code)
        self._in_row += 1
        if self._in_row >= int(ctx.settings["skylit_max_failures_in_row"]):
            raise StopChunk(f"{self._in_row} failures in a row", status="failures")

    def at_end(self, ctx: Ctx) -> None:
        atomic_write_text(ctx.paths.reports_dir / "skylit_multiexp" / "coverage.md", coverage(ctx.paths, self.plan(ctx)))


def coverage(paths: Paths, plan: dict[str, Any]) -> str:
    """Sessions stored / missing / failed per tier, credits used, symbols missing and each 09:25 board's asOf day."""
    days = [date.fromisoformat(d) for d in plan["days"]]
    failed: dict[str, dict[str, Any]] = {}
    fpath = job_dir(paths) / "failed.jsonl"
    if fpath.exists():
        for line in fpath.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                failed[r["chunk"]] = r
    ledger = paths.raw_dir / "skylit" / LEDGER
    rows = ([json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines() if x.strip()]
            if ledger.exists() else [])
    mine = [r for r in rows if r.get("plan_id") == plan["plan_id"]]
    lines = [f"# Multi-expiration snapshots: coverage (plan {plan['plan_id']})", "",
             f"Sessions {len(days)} ({days[0]} .. {days[-1]}); symbols {', '.join(ROOTS)}; "
             f"calls this plan {len(mine)}, credits charged {sum(r.get('charged') or 0 for r in mine):,} "
             f"(approved {plan['approved_credits']:,}).", ""]
    for tier in plan["tiers"]:
        stored, miss, symbols_missing, asof_days = [], [], [], {}
        for d in days:
            path = snapshot_path(paths, tier, d)
            if not path.exists():
                miss.append(d)
                continue
            stored.append(d)
            syms = {s["symbol"]: s for s in read_snapshot(path)["body"]["data"]["symbols"]}
            lacking = [r for r in ROOTS if r not in syms]
            if lacking:
                symbols_missing.append(f"{d} ({', '.join(lacking)})")
            for r, s in syms.items():
                kind = ("same day" if datetime.fromisoformat(s["asOf"].replace("Z", "+00:00")).astimezone(ET).date()
                        == d else "earlier day")
                asof_days.setdefault(r, {}).setdefault(kind, 0)
                asof_days[r][kind] += 1
        lines += [f"## Tier {tier} ET", "", f"- stored {len(stored)} of {len(days)}; missing {len(miss)}"
                  + (f": {', '.join(map(str, miss[:40]))}{' …' if len(miss) > 40 else ''}" if miss else ""),
                  f"- failed calls: {', '.join(c for c in failed if c.startswith(tier + ':')) or 'none'}",
                  f"- sessions with a symbol missing: {', '.join(symbols_missing) or 'none'}",
                  "- board time (asOf, ET) per symbol: " + "; ".join(
                      f"{r} {', '.join(f'{k} {v}' for k, v in sorted(asof_days[r].items()))}" for r in asof_days), ""]
    return "\n".join(lines)
