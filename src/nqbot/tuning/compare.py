"""One comparison of the step-13 tuning: a challenger value's build against the start value's (SPEC §7.1 Tuning
detector parameters; owner 2026-11-04/08, SPEC §13 Q-step12a (5), Q-step13plan (3), (4), (8); 2026-11-14, Q-step13c).

- **Shared events** (Q-step13plan (4)): the same touching trade, direction and at least one founding member
  (`shared_events.match`), matched session by session (a touch index is a session's own).
- **The bar** (Q-step12a (5) (b), Q-step13plan (3)): in each fold (ledger month), each build's bar is the median of its
  own out-of-fold P(held) over the shared events (both tradeable at the touch and undamaged), applied to all of that
  build's events: an event is allowed if its P(held) is above its fold's bar.
- **The score** (owner 2026-11-14, SPEC §13 Q-step13c (5), declared before any result): the excess net R per month of
  the challenger's sequential run (`policy.simulate`, all its trades) over the start value's, (R_c - R_s) - rbar_s x
  (N_c - N_s), rbar_s the start run's mean net R per trade, with a day-block bootstrap 95% interval (rbar recomputed
  in each replicate): under a losing policy the plain total rewarded trading less (the thinning test). Reported beside
  it: the total net R difference, net R per trade, the trade-count change, the row's subset.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

import numpy as np

from nqbot.tuning import policy as pol
from nqbot.tuning import shared_events as se
from nqbot.tuning import stats
from nqbot.tuning.data import Build

STYLE_SETS = {"combined": ("c", "d"), "c": ("c",), "d": ("d",)}


def shared(a: Build, b: Build) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for day in a.days:
        out += se.match(a.match_rows.get(day, []), b.match_rows.get(day, []))
    return out


def model_ok(e: dict[str, Any]) -> bool:
    """The events the structure model trains on and the bar is set on: tradeable at the touch, undamaged, labelled."""
    return e["tradeable"] and not e["damaged"] and e["labelled"]


def bars(build: Build, p: dict[str, float], eids: list[str]) -> dict[int, float]:
    """{fold: the median P(held) of `eids` (model events) in that fold}."""
    by: dict[int, list[float]] = defaultdict(list)
    for eid in eids:
        e = build.events[build.index[eid]]
        if model_ok(e):
            by[e["month"]].append(p[eid])
    return {k: float(np.median(v)) for k, v in by.items() if v}


def allowed(build: Build, p: dict[str, float], bar: dict[int, float]) -> set[str]:
    return {e["event_id"] for e in build.events
            if not e["damaged"] and e["month"] in bar and p[e["event_id"]] > bar[e["month"]]}


def candidates(picks: list[dict[str, Any]], order_types: dict[str, str], styles: tuple[str, ...],
               ok: set[str]) -> list[dict[str, Any]]:
    return [c for c in picks if c["style"] in styles and c["order_type"] == order_types[c["style"]]
            and c["event_id"] in ok]


def run(build: Build, picks: list[dict], p: dict[str, float], bar: dict[int, float], order_types: dict[str, str],
        styles: tuple[str, ...], rule: str) -> list[dict[str, Any]]:
    trades = pol.simulate(candidates(picks, order_types, styles, allowed(build, p, bar)), rule)
    for t in trades:
        t["subset"] = build.events[build.index[t["event_id"]]]["subset"]
    return trades


def summary(trades: list[dict], days: list[str], n_months: float, subset: str | None) -> dict[str, Any]:
    filled = [t for t in trades if t["filled"]]
    out = {"r_per_month": sum(t["r"] for t in filled) / n_months, "trades": len(filled),
           "sent": sum(1 for t in trades if not t.get("no_book")), "no_book": sum(1 for t in trades if t.get("no_book")),
           "by_style": {s: {"r_per_month": sum(t["r"] for t in filled if t["style"] == s) / n_months,
                            "trades": sum(1 for t in filled if t["style"] == s)} for s in ("c", "d")}}
    if subset:
        sub = [t for t in filled if t["subset"][subset]]
        out["subset"] = {"name": subset, "r_per_month": sum(t["r"] for t in sub) / n_months, "trades": len(sub)}
    return out


def day_series(trades: list[dict], days: list[str], keep=None) -> tuple[np.ndarray, np.ndarray]:
    """(net R, filled trades) per session of a run (`keep(trade)` selects a subset)."""
    r = np.array(pol.per_day(trades, days, keep))
    n = np.array(pol.per_day([{**t, "r": 1.0} for t in trades], days, keep))
    return r, n


def score(rc: np.ndarray, nc: np.ndarray, rs: np.ndarray, ns: np.ndarray, n_months: float, reps: int
          ) -> dict[str, Any]:
    """The decision score and what is reported beside it (owner 2026-11-14, SPEC §13 Q-step13c (5), (6)):
    `diff` / `lo` / `hi` = the excess net R per month over the start value with its 95% day-block interval (decides);
    `total` = the difference of total net R per month, `per_trade` = of mean net R per trade, `trades_change` =
    value - start filled trades (and as a share), each for information."""
    d, lo, hi = stats.excess_ci(rc, nc, rs, ns, n_months, reps)
    ns_, nc_ = float(ns.sum()), float(nc.sum())
    return {"diff": d, "lo": lo, "hi": hi, "total": list(stats.per_month_ci(rc - rs, n_months, reps)),
            "per_trade": list(stats.ratio_diff_ci(rc, nc, rs, ns, reps)) if ns_ > 0 and nc_ > 0 else None,
            "per_trade_start": rs.sum() / ns_ if ns_ else None, "per_trade_value": rc.sum() / nc_ if nc_ else None,
            "trades_change": nc_ - ns_, "trades_change_pct": (nc_ - ns_) / ns_ if ns_ else None,
            "series": [rc.tolist(), nc.tolist(), rs.tolist(), ns.tolist()]}


def compare(start: Build, chal: Build, p_start: dict[str, float], p_chal: dict[str, float],
            picks_start: list[dict], picks_chal: list[dict], order_types: dict[str, str], mode: str,
            subset: str | None, reps: int, rules=pol.RULES, shared_ids: list[tuple[str, str]] | None = None,
            extra_modes: tuple[str, ...] = ()) -> dict[str, Any]:
    """The challenger against the start value (both with the policy's `order_types`): per rule, both runs' summaries
    and the score (`score`: the excess net R decides); `extra_modes` adds the same for other style sets (reported for
    information)."""
    if start.days != chal.days:
        raise ValueError(f"builds {start.key} and {chal.key} cover different sessions")
    days = start.days
    n_months = float(len(set(start.month_of_day[d] for d in days)))
    sh = shared(start, chal) if shared_ids is None else shared_ids
    bar_s = bars(start, p_start, [a for a, _ in sh])
    bar_c = bars(chal, p_chal, [b for _, b in sh])
    out: dict[str, Any] = {"start": start.key, "challenger": chal.key, "mode": mode, "subset": subset,
                           "events": {"start": len(start.events), "challenger": len(chal.events), "shared": len(sh),
                                      "shared_model": sum(1 for a, b in sh if model_ok(start.events[start.index[a]])
                                                          and model_ok(chal.events[chal.index[b]]))},
                           "bars": {"start": bar_s, "challenger": bar_c}, "rules": {}}
    for m in (mode,) + tuple(extra_modes):
        for rule in rules:
            ts = run(start, picks_start, p_start, bar_s, order_types, STYLE_SETS[m], rule)
            tc = run(chal, picks_chal, p_chal, bar_c, order_types, STYLE_SETS[m], rule)
            rs, ns = day_series(ts, days)
            rc, nc = day_series(tc, days)
            res = {"start": summary(ts, days, n_months, subset), "challenger": summary(tc, days, n_months, subset),
                   **score(rc, nc, rs, ns, n_months, reps)}
            if subset:
                keep = lambda t: t["subset"][subset]   # noqa: E731
                ss, sns = day_series(ts, days, keep)
                sc, snc = day_series(tc, days, keep)
                res["subset_excess"] = list(stats.excess_ci(sc, snc, ss, sns, n_months, reps))
                res["subset_total"] = list(stats.per_month_ci(sc - ss, n_months, reps))
            out["rules"][rule if m == mode else f"{m}:{rule}"] = res
    return out


def fixed_events_series(build: Build, picks: list[dict], p: dict[str, float], bar: dict[int, float],
                        order_types: dict[str, str], styles: tuple[str, ...], rule: str
                        ) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Rows 12-13 (the events frozen): {style: (net R, trades) per day} of each style's own single-style run, plus
    "sum" over `styles`."""
    out: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    tot_r, tot_n = np.zeros(len(build.days)), np.zeros(len(build.days))
    for s in styles:
        r, n = day_series(run(build, picks, p, bar, order_types, (s,), rule), build.days)
        out[s] = (r, n)
        tot_r, tot_n = tot_r + r, tot_n + n
    out["sum"] = (tot_r, tot_n)
    return out


def lead(win: dict[str, Any] | None, runner: dict[str, Any] | None, n_months: float, reps: int) -> list[float]:
    """The winner's lead over the next-best grid point on the excess net R: (winner's excess over its start run) -
    (the runner-up's over its own), the same day draw for both; None for either = the start value (excess 0)."""
    if win is None:
        return list(stats.excess_lead_ci(runner["series"], None, n_months, reps, sign=-1.0))
    return list(stats.excess_lead_ci(win["series"], None if runner is None else runner["series"], n_months, reps))


def same_set(start: Build, chal: Build) -> bool:
    """True if both builds have exactly the same events and structure labels (the feature-only rows)."""
    if len(start.events) != len(chal.events):
        return False
    return all(chal.index.get(e["event_id"]) is not None
               and chal.events[chal.index[e["event_id"]]]["held"] == e["held"]
               and chal.events[chal.index[e["event_id"]]]["tradeable"] == e["tradeable"] for e in start.events)


def logloss_by_day(build: Build, p: dict[str, float], weight: Callable[[dict], float]) -> tuple[np.ndarray, np.ndarray]:
    """Per session: (sum of weight x log loss, sum of weights) over the model events (row 14)."""
    from nqbot.tuning.provisional import weighted_logloss

    num, den = defaultdict(float), defaultdict(float)
    for e in build.events:
        if not model_ok(e):
            continue
        w = weight(e)
        num[e["day"]] += w * float(weighted_logloss(np.array([p[e["event_id"]]]), np.array([float(e["held"])]))[0])
        den[e["day"]] += w
    return (np.array([num.get(d, 0.0) for d in build.days]), np.array([den.get(d, 0.0) for d in build.days]))
