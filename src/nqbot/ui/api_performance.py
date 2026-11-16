"""The Performance page's JSON API (SPEC §9b item 6; build stage 3). Read-only: every route reads the stored ledgers
(or, under `nqbot ui --synthetic`, the synthetic ledger) through the ledger guard; a request the guard refuses gets
403 with the reason and the missing gate. The CSV export goes through the same guard."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import date
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from nqbot.accounts.presets import ARCHIVED, account_kind, builtin_presets
from nqbot.performance import account_mc, acctypes, export, ledger, plans as lifecycle, pnl_calendar, view
from nqbot.performance.lifecycle import PayoutRules
from nqbot.performance import state as acct_state
from nqbot.performance import synthetic as synthetic_ledger
from nqbot.performance.floor import Rule
from nqbot.performance.guard import Guard, Refused, apply, require
from nqbot.performance.stats import days
from nqbot.storage import Paths
from nqbot.ui.panel_store import PanelStore, PanelUnavailable


def _err(status: int, msg: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": msg, **extra}, status_code=status)


def _date(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def routes(store: PanelStore, paths_fn: Callable[[], Paths | None], settings: dict[str, Any], *,
           synthetic: bool = False) -> list[Route]:
    return build(store, paths_fn, settings, synthetic=synthetic)[0]


def build(store: PanelStore, paths_fn: Callable[[], Paths | None], settings: dict[str, Any], *,
          synthetic: bool = False) -> tuple[list[Route], Callable[[str], int]]:
    """The routes, and how many trades an account has in the ledger (an account with trades is never deleted)."""
    synth = synthetic
    cache: dict[str, Any] = {}

    def accounts() -> list[dict[str, Any]]:
        try:
            accts = store.accounts().data["accounts"]
            labels = {p.id: p.label for p in store.all_presets()}
        except (PanelUnavailable, OSError, ValueError):
            return []
        return [{**a, "kind": account_kind(a, labels)} for a in accts if a.get("name")]

    def data() -> tuple[list[ledger.Trade], dict[str, str], list[dict[str, Any]]]:
        accs = accounts()
        if synth:
            key = hashlib.sha256(json.dumps([[a.get(k) for k in ("name", "start_balance_usd", "drawdown_usd",
                                              "risk_usd_per_trade")] for a in accs], default=str).encode()).hexdigest()
            if cache.get("synth_key") != key:
                cache.update(synth_key=key, synth=synthetic_ledger.ledger(settings, accs or None), mc={})
            names = {t.account for t in cache["synth"]}
            # the synthetic finished history (archived accounts, owner 2026-11-16) beside the panel's own accounts
            if "history" not in cache:
                labels = {p.id: p.label for p in builtin_presets(store.config_dir)}
                cache["history"] = [{**a, "kind": account_kind(a, labels)} for a in synthetic_ledger.history_accounts(settings)]
            base = accs if accs else synthetic_ledger.DEFAULT_ACCOUNTS
            have = {a.get("name") for a in base}
            shown = base + [h for h in cache["history"] if h["name"] not in have]
            status = {"backtest": "synthetic", "demo": "synthetic", "prop": "synthetic",
                      "live": "no live trades yet (synthetic ledger)"}
            return cache["synth"], status, \
                [a for a in shown if a["name"] in names] + [{"name": synthetic_ledger.DEMO_ACCOUNT, "kind": "Demo", "demo": True}]
        paths = paths_fn()
        got = ledger.load(paths.data_dir if paths else None, paths.live_dir if paths else None)
        return got.trades, got.status, accs

    def guard() -> Guard:
        return Guard.from_files(settings)

    def brief(a: dict[str, Any]) -> dict[str, Any]:
        rule = Rule.of(a).text() if a.get("drawdown_type") else None
        status = str(a.get("status") or "active")
        return {"name": a["name"], "kind": a.get("kind"), "phase": a.get("phase"), "demo": bool(a.get("demo")),
                "status": status, "archived": status in ARCHIVED,
                "draft": bool(a.get("draft")), "rule": rule}

    def params(request: Request) -> dict[str, Any]:
        q = request.query_params
        return {"account": q.get("account") or "all", "phase": q.get("phase") or "all",
                "start": _date(q.get("start")), "end": _date(q.get("end"))}

    def plans() -> list[lifecycle.Plan]:
        """Every plan (the built-in §6.7 presets and the owner's saved ones, grouped by name), evaluation and funded."""
        try:
            presets = store.all_presets()
        except (PanelUnavailable, OSError, ValueError):
            presets = builtin_presets(store.config_dir)   # no SSD: the built-in §6.7 presets only
        labels = {p.id: p.label for p in presets}
        return lifecycle.plans(presets, lambda pr: account_kind({"preset": pr.id, "firm": pr.firm}, labels))

    def plan_of(request: Request) -> lifecycle.Plan | None:
        """The plan the page prices the strategy's backtest trades by (`plan=`; owner 2026-11-15), or None."""
        want = request.query_params.get("plan")
        return next((pl for pl in plans() if pl.id == want), None) if want else None

    def priced(request: Request, trades: list[ledger.Trade]) -> list[ledger.Trade]:
        """Backtest trades in the chosen plan's dollars (its first stage's risk per trade and commissions); R unchanged."""
        pl = plan_of(request)
        return acctypes.priced(trades, pl.first) if pl else trades

    async def page(request: Request) -> Response:
        try:
            p = params(request)
        except ValueError:
            return _err(400, "start / end must be YYYY-MM-DD")
        if p["phase"] not in view.PHASES:
            return _err(400, f"unknown phase {p['phase']}")
        trades, status, accs = data()
        g = guard()
        try:
            if p["phase"] in ("walk_forward", "sealed"):
                require(g, "total", phases=[p["phase"]])
        except Refused as e:
            return _err(403, e.reason, gate=e.gate)
        pl = plan_of(request)
        body = view.page(priced(request, trades), g, accs, **p)
        if p["account"] == "all" and p["phase"] in acct_state.REAL:
            # every account's history in this real-trades phase, archived ones included (owner 2026-11-16)
            fields = {pr.id: pr.fields for pr in builtin_presets(store.config_dir)}
            body["breakdown"] = view.breakdown(apply(trades, "trade", g).trades, accs, phase=p["phase"], preset_fields=fields)
        body.update(accounts=[brief(a) for a in accs], sources=status, synthetic=synth,
                    plan=pl.brief() if pl else None, plans=[x.brief() for x in plans()],
                    gates_passed=sorted(g.passed), months={str(k): v for k, v in settings.get("months", {}).items()})
        return JSONResponse(body)

    # ── Drawdown and Monte Carlo (owner 2026-11-15): days drawn from the strategy's guarded backtest ledger, never one
    # account's own few trades. mode=plans: one plan's lifecycle with payouts (performance/lifecycle.py);
    # mode=account: one actual account "from today" (its current balance, high water and floor). ──
    def strategy(request: Request, trades: list[ledger.Trade], g: Guard) -> list[list[ledger.Trade]]:
        p = params(request)
        allowed = apply(view.select(trades, g, start=p["start"], end=p["end"]), "day", g).trades
        return acctypes.strategy_days(allowed)

    def source_label(sdays: list[list[ledger.Trade]]) -> str:
        if not sdays:
            return "no backtest days yet"
        return (f"{'synthetic ' if synth else ''}development backtest, {len(sdays)} days "
                f"({sdays[0][0].date.isoformat()} to {sdays[-1][0].date.isoformat()})")

    def mc_cached(key: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        mc = cache.setdefault("mc", {})
        if key not in mc:
            if len(mc) > 64:
                mc.clear()
            mc[key] = fn()
        return mc[key]

    def horizon_of(request: Request) -> int | None:
        """30 / 60 / 120 trading days (default 60) on every Monte Carlo and Drawdown view (owner 2026-11-15)."""
        try:
            h = int(request.query_params.get("horizon") or lifecycle.DEFAULT_HORIZON)
        except ValueError:
            return None
        return h if h in lifecycle.HORIZONS else None

    def real_trades(trades: list[ledger.Trade], g: Guard, name: str) -> list[ledger.Trade]:
        return [t for t in apply(trades, "day", g).trades if t.account == name and t.source in acct_state.REAL]

    async def monte_carlo(request: Request) -> Response:
        mode = request.query_params.get("mode") or "plans"
        trades, _, accs = data()
        g = guard()
        sdays = strategy(request, trades, g)
        dates = [d[0].date.isoformat() for d in sdays]
        tag = f"{len(sdays)}:{sum(t.r for d in sdays for t in d):.3f}:{dates[:1]}{dates[-1:]}"
        horizon = horizon_of(request)
        if horizon is None:
            return _err(400, "horizon is 30, 60 or 120")
        if mode == "plans":
            ps = plans()
            if not ps:
                return _err(404, "no plans (presets)")
            sel = next((pl for pl in ps if pl.id == request.query_params.get("plan")), ps[0])

            def mc(pl: lifecycle.Plan) -> dict[str, Any]:
                return mc_cached(f"plan:{pl.id}:{horizon}:{tag}:{pl.brief()}", lambda: lifecycle.monte_carlo(sdays, pl, horizon=horizon))
            compare = None
            if request.query_params.get("compare") == "1":
                compare = [{**pl.brief(), "numbers": mc(pl)["numbers"]} for pl in ps]
            return JSONResponse({"mode": "plans", "plans": [pl.brief() for pl in ps], "selected": sel.id, "horizon": horizon,
                                 "horizons": list(lifecycle.HORIZONS), "fan": mc(sel), "compare": compare,
                                 "source": source_label(sdays), "synthetic": synth})
        name = request.query_params.get("account") or ""
        acc = next((a for a in accs if a.get("name") == name), None)
        if acc is None or not acc.get("drawdown_type"):
            return _err(404, f"no account {name!r} with a drawdown rule")
        paths = paths_fn()
        st = acct_state.today(paths.live_dir if paths else None, apply(trades, "trade", g).trades, acc, synth)
        common = {"mode": "account", "account": brief(acc), "horizon": horizon, "horizons": list(lifecycle.HORIZONS),
                  "source": source_label(sdays), "synthetic": synth}
        if st is None:
            return JSONResponse({**common, "state": None, "reason": "no live balance yet: the engine supplies it from step 22, "
                                                                    "and this account has no real trades in the ledger"})
        # only the account's own stage, from where it stands today (owner 2026-11-15)
        real_days = [(d.date, d.usd) for d in days(real_trades(trades, g, name))]
        key = json.dumps([name, horizon, tag, st, sorted((k, str(v)) for k, v in acc.items())], default=str)
        fan = mc_cached(f"acct:{key}", lambda: account_mc.run(sdays, acc, st, real_days, horizon=horizon))
        return JSONResponse({**common, "state": st, "fan": fan})

    async def drawdown(request: Request) -> Response:
        mode = request.query_params.get("mode") or "plans"
        trades, _, accs = data()
        g = guard()
        horizon = horizon_of(request)
        if horizon is None:
            return _err(400, "horizon is 30, 60 or 120")
        if mode == "plans":
            ps = plans()
            if not ps:
                return _err(404, "no plans (presets)")
            sel = next((pl for pl in ps if pl.id == request.query_params.get("plan")), ps[0])
            sdays = strategy(request, trades, g)
            # the first `horizon` backtest days through the plan's lifecycle
            return JSONResponse({"mode": "plans", "plans": [pl.brief() for pl in ps], "selected": sel.id, "horizon": horizon,
                                 "horizons": list(lifecycle.HORIZONS), "replay": lifecycle.replay(sdays[:horizon], sel),
                                 "source": source_label(sdays[:horizon]), "synthetic": synth})
        name = request.query_params.get("account") or ""
        acc = next((a for a in accs if a.get("name") == name), None)
        if acc is None or not acc.get("drawdown_type"):
            return _err(404, f"no account {name!r} with a drawdown rule")
        common = {"mode": "account", "account": brief(acc), "horizon": horizon, "horizons": list(lifecycle.HORIZONS), "synthetic": synth}
        real = real_trades(trades, g, name)
        if not real:
            return JSONResponse({**common, "run": None, "reason": "no real trades on this account yet (prop trades from step 26)"})
        # one point per trade at its exit time, the last `horizon` trading days (owner 2026-11-16)
        run = view.account_trade_run(real, acc, horizon)
        # the stage's lines (the target while an evaluation, the payout line while funded)
        evaluation = account_mc.stage_of(acc) == "evaluation"
        rule_start = float(acc["start_balance_usd"])
        target = rule_start + float(acc["profit_target_usd"]) if evaluation and acc.get("profit_target_usd") else None
        po = None if evaluation else PayoutRules.of(acc)
        n = len(run["dates"])
        run.update(target=[target] * n, payout_line=[po.baseline_usd + po.threshold_profit if po else None] * n,
                   stage=["evaluation" if evaluation else "funded"] * n, payouts_taken=int(acc.get("payouts_taken") or 0))
        return JSONResponse({**common, "run": run, "stage": "evaluation" if evaluation else "funded",
                             "payout": po.brief() if po else None})

    async def calendar_month(request: Request) -> Response:
        q = request.query_params
        account, unit, phase = q.get("account") or "all", q.get("unit") or "usd", q.get("phase") or "all"
        if unit not in ("usd", "r"):
            return _err(400, "unit is usd or r")
        if phase not in view.PHASES:
            return _err(400, f"unknown phase {phase}")
        trades, _, _ = data()
        trades = priced(request, trades)
        g = guard()
        # the page's phase: each phase is viewed on its own (owner 2026-11-16), its months only
        allowed = apply(view.select(trades, g, account=account, phase=phase), "day", g).trades
        months = sorted({f"{t.date.year:04d}-{t.date.month:02d}" for t in allowed})
        ym = q.get("month") or (months[-1] if months else f"{date.today().year:04d}-{date.today().month:02d}")
        out = pnl_calendar.month(allowed, ym, unit)  # type: ignore[arg-type]
        # a month whose backtest days are held back (walk-forward / sealed) says so, with how many trades; live, demo
        # and prop trades are always shown, so a month of only those has no notice
        y, m = (int(x) for x in ym.split("-"))
        held = [t for t in view.select(trades, g, account=account, phase=phase)
                if t.source == "backtest" and t.date.year == y and t.date.month == m and g.why_not(g.phase_of(t), "day")]
        notice = None
        if held:
            phase = g.phase_of(held[0])
            reason = g.why_not(phase, "day")[0]  # type: ignore[index]
            notice = {"phase": phase, "count": len(held),
                      "reason": f"{len(held)} backtest trade{'s' if len(held) != 1 else ''} of this month not shown: {reason}."}
        out.update(months=months, notice=notice)
        return JSONResponse(out)

    async def day_rows(request: Request) -> Response:
        try:
            d = date.fromisoformat(request.query_params.get("date") or "")
        except ValueError:
            return _err(400, "date must be YYYY-MM-DD")
        account, phase = request.query_params.get("account") or "all", request.query_params.get("phase") or "all"
        if phase not in view.PHASES:
            return _err(400, f"unknown phase {phase}")
        trades, _, _ = data()
        trades = priced(request, trades)
        g = guard()
        allowed = apply(view.select(trades, g, account=account, phase=phase), "trade", g).trades
        return JSONResponse({"date": d.isoformat(), "trades": pnl_calendar.day_trades(allowed, d)})

    async def trade_rows(request: Request) -> Response:
        try:
            p = params(request)
        except ValueError:
            return _err(400, "start / end must be YYYY-MM-DD")
        trades, _, _ = data()
        trades = priced(request, trades)
        g = guard()
        try:
            if p["phase"] not in ("all", "demo", "prop", "live"):
                require(g, "trade", phases=[p["phase"]])
        except Refused as e:
            return _err(403, e.reason, gate=e.gate)
        kept = apply(view.select(trades, g, **p), "trade", g)
        return JSONResponse({"trades": [t.row() | {"phase": view.phase_of(t, g)} for t in kept.trades],
                             "held": kept.held})

    async def export_csv(request: Request) -> Response:
        try:
            p = params(request)
        except ValueError:
            return _err(400, "start / end must be YYYY-MM-DD")
        trades, _, _ = data()
        trades = priced(request, trades)
        g = guard()
        phases = [] if p["phase"] in ("all", "demo", "prop", "live") else [p["phase"]]
        try:
            text, held = export.csv_text(view.select(trades, g, **p), g, phases=phases)
        except Refused as e:
            return _err(403, e.reason, gate=e.gate)
        name = f"creamflow_trades_{p['account']}_{p['phase']}{'_synthetic' if synth else ''}.csv"
        return Response(text, media_type="text/csv", headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-CF-Held": json.dumps({k: v["count"] for k, v in held.items()})})

    def trade_count(name: str) -> int:
        trades, _, _ = data()
        return sum(1 for t in trades if t.account == name)

    return [
        Route("/api/performance", page, methods=["GET"]),
        Route("/api/performance/montecarlo", monte_carlo, methods=["GET"]),
        Route("/api/performance/drawdown", drawdown, methods=["GET"]),
        Route("/api/performance/calendar", calendar_month, methods=["GET"]),
        Route("/api/performance/day", day_rows, methods=["GET"]),
        Route("/api/performance/trades", trade_rows, methods=["GET"]),
        Route("/api/performance/export.csv", export_csv, methods=["GET"]),
    ], trade_count
