"""Plans for the Performance page's strategy view (SPEC §9b item 6; owner 2026-11-15).

A plan is a firm's account plan with its stages (§6.7): MFFU Rapid EOD 25k, MFFU Builder 25k, MFFU Pro 50k, Lucid Flex
25k, plus the owner's saved presets (grouped by their name). Its lifecycle with payouts is `lifecycle.run` (one engine
for the page and the step 15-16 simulator):

- `monte_carlo`: 1,000 paths of the strategy's whole days over 30 / 60 / 120 trading days (drawn with replacement, the
  headline; the same days reordered = "order luck"). Per path: evaluations used, passes, days to pass, payouts taken
  and their total, fees paid, net cash (payouts − fees); each as a median with its 10-90% range. The fan is the
  account balance across the whole lifecycle.
- `replay`: the backtest days in their order through the same lifecycle (each stage's own floor, the evaluation target
  while in an evaluation, the payout line while funded) with its totals.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from . import lifecycle as L
from .acctypes import Rules, day_series, of
from .ledger import Trade

N_PATHS = 1000          # §9b item 6
HORIZONS = (30, 60, 120)  # trading days (owner 2026-11-15); 60 by default
DEFAULT_HORIZON = 60
STAGE_NAME = {L.EVAL: "evaluation", L.FUNDED: "funded"}


@dataclass(frozen=True)
class Plan:
    id: str
    label: str
    kind: str
    evaluation: Rules | None
    funded: Rules | None
    after_first: Rules | None          # the funded stage with its `after_first_payout` cap fields
    builtin: bool
    lifecycle: L.Lifecycle

    @property
    def first(self) -> Rules:
        r = self.evaluation or self.funded
        assert r is not None
        return r

    def brief(self) -> dict[str, Any]:
        po = self.lifecycle.payout
        return {"id": self.id, "label": self.label, "kind": self.kind, "builtin": self.builtin,
                "evaluation": self.evaluation.brief() if self.evaluation else None,
                "funded": self.funded.brief() if self.funded else None,
                "after_first": self.after_first.brief() if self.after_first else None,
                "min_trading_days": self.lifecycle.min_trading_days,
                "payout": po.brief() if po else None,
                "fees": {"evaluation_usd": self.lifecycle.fees.evaluation_usd, "activation_usd": self.lifecycle.fees.activation_usd},
                "fees_missing": self.lifecycle.fees.missing() if self.evaluation else []}


def plans(presets: list[Any], kind_of: Any) -> list[Plan]:
    """Group presets into plans by name: each plan's evaluation and funded stages (the funded stage carries its
    `after_first_payout` cap fields; the separate "after first payout" preset stage is the same account later)."""
    by: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for p in presets:
        if p.label not in by:
            by[p.label] = {}
            order.append(p.label)
        by[p.label].setdefault(p.stage, p)
    out = []
    for label in order:
        st = by[label]
        ev, fu = st.get("evaluation"), st.get("funded")
        if not (ev or fu):
            continue
        any_p = ev or fu
        evaluation = of(ev.fields, id=ev.id, label=label, stage="evaluation") if ev else None
        funded = of(fu.fields, id=fu.id, label=label, stage="funded") if fu else None
        after = None
        if fu is not None:
            afp = fu.fields.get("after_first_payout")
            after = of({**fu.fields, **afp}, id=f"{fu.id}:after_first_payout", label=label, stage="after first payout") if afp else funded
        fees = L.Fees(evaluation_usd=_num(ev.fields.get("evaluation_fee_usd")) if ev else 0.0,
                      activation_usd=_num(fu.fields.get("activation_fee_usd")) if (fu and ev) else 0.0)
        lc = L.Lifecycle(evaluation=evaluation, funded=funded,
                         min_trading_days=int(ev.fields.get("min_trading_days") or 0) if ev else 0,
                         payout=L.PayoutRules.of(fu.fields) if fu else None, fees=fees)
        out.append(Plan(id=f"plan:{label}", label=label, kind=kind_of(any_p), builtin=bool(any_p.builtin),
                        evaluation=evaluation, funded=funded, after_first=after, lifecycle=lc))
    return out


def _num(v: Any) -> float | None:
    return None if v is None else float(v)


def _days(trades_days: list[list[Trade]], plan: Plan) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    z = np.zeros(len(trades_days))
    ser = lambda r: np.asarray(day_series(trades_days, r), dtype=float) if r else z  # noqa: E731
    return ser(plan.evaluation), ser(plan.funded), ser(plan.after_first)


def _nan_none(a: np.ndarray) -> list[float | None]:
    return [None if math.isnan(x) else round(float(x), 2) for x in a]


def _numbers(sim: dict[str, Any], plan: Plan) -> dict[str, Any]:
    done = sim["passes"] + sim["eval_fails"]
    has_eval = plan.evaluation is not None
    pooled = float(sim["passes"].sum() / done.sum()) if (has_eval and done.sum()) else None
    ratio = (sim["passes"][done > 0] / done[done > 0]).astype(float)
    pr = L.summary(ratio, digits=4) if ratio.size else {"median": None, "lo": None, "hi": None}
    fees_known = not plan.lifecycle.fees.missing() or not has_eval
    po = plan.lifecycle.payout
    return {
        "evaluations": L.summary(sim["evaluations"], digits=0) if has_eval else None,
        "pass_rate": {"value": None if pooled is None else round(pooled, 4), "lo": pr["lo"], "hi": pr["hi"]} if has_eval else None,
        "days_to_pass": L.summary(sim["pass_lengths"], digits=0) if sim["pass_lengths"].size else None,
        "paths_passing": round(float((sim["passes"] > 0).mean()), 4) if has_eval else None,
        "payouts_n": L.summary(sim["payouts_n"], digits=0) if po else None,
        "payouts_gross": L.summary(sim["payouts_gross"]) if po else None,
        "payouts_share": L.summary(sim["payouts_share"]) if po else None,
        "paths_with_payout": round(float((sim["payouts_n"] > 0).mean()), 4) if po else None,
        "fees": L.summary(sim["fees"]) if fees_known else None,
        "net_cash": L.summary(sim["net_cash"]) if fees_known else None,
        "fees_missing": plan.lifecycle.fees.missing() if has_eval else [],
        "split_pct": po.split_pct if po else None,
        "ends_funded": round(float((sim["stage_end"] == L.FUNDED).mean()), 4) if plan.funded else None,
    }


def _trace(sim: dict[str, Any], p: int = 0) -> dict[str, Any]:
    tr = sim["trace"]
    return {"balance": [round(float(x), 2) for x in sim["balance"][p]],
            "floor": _nan_none(tr["floor"][p]), "target": _nan_none(tr["target"][p]), "payout_line": _nan_none(tr["payout_line"][p]),
            "stage": [STAGE_NAME.get(int(s), "ended") for s in tr["stage"][p]], "events": tr["events"][p]}


def monte_carlo(trades_days: list[list[Trade]], plan: Plan, *, horizon: int = DEFAULT_HORIZON, n: int = N_PATHS,
                seed: int = 7) -> dict[str, Any]:
    m = len(trades_days)
    if m == 0:
        return {"n": n, "horizon": horizon, "days": 0, "numbers": None, "order_luck": None, "bands": []}
    ev, fu, fa = _days(trades_days, plan)
    rng = np.random.default_rng(seed)
    boot = rng.integers(0, m, size=(n, horizon))
    # the same days reordered (tiled with fresh orders when the horizon is longer than the backtest)
    perm = np.stack([np.concatenate([rng.permutation(m) for _ in range(-(-horizon // m))])[:horizon] for _ in range(n)])
    cal = L.calendar_steps_typical(horizon)
    a = L.run(plan.lifecycle, ev, fu, fa, boot, cal)
    b = L.run(plan.lifecycle, ev, fu, fa, perm, cal)
    base = L.run(plan.lifecycle, ev, fu, fa, (np.arange(horizon) % m)[None, :], cal, trace=1)
    qs = np.quantile(a["balance"], [0.05, 0.25, 0.5, 0.75, 0.95], axis=0, method="lower")
    bands = [{"p5": round(float(qs[0, i]), 2), "p25": round(float(qs[1, i]), 2), "p50": round(float(qs[2, i]), 2),
              "p75": round(float(qs[3, i]), 2), "p95": round(float(qs[4, i]), 2)} for i in range(horizon + 1)]
    return {"n": n, "horizon": horizon, "days": m, "start_usd": plan.first.rule.start_usd,
            "numbers": _numbers(a, plan), "order_luck": _numbers(b, plan), "bands": bands, "base": _trace(base)}


def replay(trades_days: list[list[Trade]], plan: Plan) -> dict[str, Any]:
    """The backtest days in their order through the lifecycle (one path, its calendar from the real dates)."""
    dates = [d[0].date for d in trades_days]
    if not dates:
        return {"dates": [], "balance": [], "floor": [], "target": [], "payout_line": [], "stage": [], "events": [],
                "totals": None}
    ev, fu, fa = _days(trades_days, plan)
    sim = L.run(plan.lifecycle, ev, fu, fa, np.arange(len(dates))[None, :], L.calendar_steps_from_dates(dates), trace=1)
    tr = _trace(sim)
    events = [{**e, "date": dates[e["day"] - 1].isoformat()} for e in tr["events"]]
    fees_known = not plan.lifecycle.fees.missing() or plan.evaluation is None
    split = plan.lifecycle.payout.split_pct if plan.lifecycle.payout and plan.lifecycle.payout.split_pct is not None else 1.0
    totals = {"evaluations": int(sim["evaluations"][0]), "passes": int(sim["passes"][0]), "eval_fails": int(sim["eval_fails"][0]),
              "funded_fails": int(sim["funded_fails"][0]), "payouts_n": int(sim["payouts_n"][0]),
              "payouts_gross": round(float(sim["payouts_gross"][0]), 2), "payouts_share": round(float(sim["payouts_share"][0]), 2),
              "split_pct": split, "fees": round(float(sim["fees"][0]), 2) if fees_known else None,
              "net_cash": round(float(sim["net_cash"][0]), 2) if fees_known else None,
              "fees_missing": plan.lifecycle.fees.missing() if plan.evaluation else [],
              "ends_in": STAGE_NAME.get(int(sim["stage_end"][0]), "ended")}
    # one point per session (the day-0 start is the first account's opening balance, not a session)
    return {"dates": [d.isoformat() for d in dates], "start_usd": plan.first.rule.start_usd,
            "balance": tr["balance"][1:], "floor": tr["floor"][1:], "target": tr["target"][1:],
            "payout_line": tr["payout_line"][1:], "stage": tr["stage"][1:], "events": events, "totals": totals}
