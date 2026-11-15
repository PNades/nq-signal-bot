"""The bootstrap policy and the sequential simulator of the step-13 tuning (SPEC §6.3 v1 bootstrap, §7 rule 3b, §7.1
Tuning detector parameters and "Which entry style trades"; owner 2026-11-09, SPEC §13 Q-step13-0 (c), and 2026-11-14,
Q-step13c).

**Bootstrap pair** of a decision point and order type: stop candidate 2 (the entry zone's far edge + `stop_buffer`;
area events, which would use candidate 1, don't exist at step 13) with the nearest structural target whose net RR at
default costs is at least 1:1 (the fixed 1:2.5 baseline and the low-RR extension rows are not structural candidates).

**Sequential run** (§7 rule 3b), one account at default risk (`gate_account`: no daily caps, no lives), the days in
order, each day's decision points in time order (same moment: style (c) before (d), the owner's ranking, §7.1):
- a decision point trades only if its event is allowed (above its fold's bar, not damaged), its row is tradeable at
  `decision_ts` (setup windows, `entry_end`, news blackouts, dislocation blocks: the labeler's `tradeable`) and the
  account is free: no position open and no entry pending (else it is skipped, never queued);
- each touch group trades at most once (§5.4): after any fill its later decision points are skipped; an entry removed
  unfilled leaves the group free (§7.1 Which entry style trades);
- a sent entry keeps the account busy until it is removed (`removal_t`, which includes `order_latency_sec`) or, if
  filled, until its exit (`t_exit_<rule>`); its result is the stored `r_<rule>` (net R at default costs; the news
  flatten and flat-by are in the labels); an unfilled entry counts 0 R;
- a `no_book` entry (a marketable limit without `mbp-1`, 2025-10-01 to 10-06) can't be simulated: skipped, counted.
The breakeven rule that decides is `none` (owner 2026-11-14); `fs` is run for information.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

MIN_RR = 1.0               # §6.3: the nearest target at net RR >= 1:1
BOOTSTRAP_STOP = "2"       # §6.3: stop candidate 2
STYLE_RANK = {"a": 0, "b": 0, "c": 1, "d": 2}   # §7.1: order flow > audit / sweep (c) > plain (d), ties only
RULES = ("none", "fs")     # owner 2026-11-14: none decides, fs reported
EPS = 1e-9


def is_bootstrap_candidate(row: dict[str, Any]) -> bool:
    stops = str(row.get("stop_types") or "").split(",")
    return (BOOTSTRAP_STOP in stops and not row.get("rr_ext") and row.get("target_kind") != "baseline"
            and row.get("net_rr") is not None and float(row["net_rr"]) >= MIN_RR - EPS)


def pick_bootstrap(rows: Iterable[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """{(decision_id, order_type): its bootstrap pair} (nearest = the smallest target distance; ties by pair_id)."""
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        if not is_bootstrap_candidate(r):
            continue
        k = (r["decision_id"], r["order_type"])
        b = best.get(k)
        if b is None or (r["target_pts"], r["pair_id"]) < (b["target_pts"], b["pair_id"]):
            best[k] = r
    return best


def order_key(c: dict[str, Any]) -> tuple:
    return (int(c["decision_ts"]), STYLE_RANK[c["style"]], int(c["firing_n"]), c["decision_id"])


def simulate(cands: Iterable[dict[str, Any]], rule: str = "none") -> list[dict[str, Any]]:
    """The entries sent, in order: each with `day`, `decision_id`, `event_id`, `style`, `filled`, `r`. `cands`: one
    candidate per decision point (its bootstrap pair at the policy's order type) that may trade, with `day`,
    `decision_ts`, `style`, `firing_n`, `decision_id`, `event_id`, `touch_group_id`, `tradeable`, `fill_status`,
    `removal_t`, `t_exit_<rule>`, `r_<rule>`."""
    by_day: dict[str, list[dict]] = defaultdict(list)
    for c in cands:
        by_day[str(c["day"])].append(c)
    out: list[dict[str, Any]] = []
    for day in sorted(by_day):
        busy_until = None
        traded: set[str] = set()
        for c in sorted(by_day[day], key=order_key):
            t = int(c["decision_ts"])
            if not c["tradeable"] or (busy_until is not None and t < busy_until) or c["touch_group_id"] in traded:
                continue
            if c["fill_status"] == "no_book":
                out.append({"day": day, "decision_id": c["decision_id"], "event_id": c["event_id"],
                            "style": c["style"], "filled": False, "r": 0.0, "no_book": True})
                continue
            filled = c["fill_status"] == "filled"
            if filled:
                busy_until = int(c[f"t_exit_{rule}"])
                traded.add(c["touch_group_id"])
                r = float(c[f"r_{rule}"])
            else:
                busy_until = int(c["removal_t"])
                r = 0.0
            out.append({"day": day, "decision_id": c["decision_id"], "event_id": c["event_id"], "style": c["style"],
                        "filled": filled, "r": r, "no_book": False})
    return out


def per_day(trades: list[dict[str, Any]], days: list[str], keep=None) -> list[float]:
    """Net R per session (in `days` order) of the filled trades (`keep(trade)` selects a subset)."""
    tot: dict[str, float] = defaultdict(float)
    for t in trades:
        if t["filled"] and (keep is None or keep(t)):
            tot[t["day"]] += t["r"]
    return [tot.get(d, 0.0) for d in days]
