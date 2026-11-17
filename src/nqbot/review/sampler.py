"""The step-14 review sample (SPEC §8; owner's plan 2026-11-15): before any model exists, a spread of decision points
across stack counts, level types, entry styles and times of day, plus valid setups that lost and a few setups the
owner would never take (to test the filters).

One candidate per event (§8): entry style (c) if an audit or sweep fired at the zone (its first firing), else (d); the
trade is the bootstrap pair (§6.3 v1: stop candidate 2 = far edge + `stop_buffer`, the nearest structural target at
net RR >= 1:1, `tuning/policy.py`) at the style's order type (`entry_order_type`), and only one whose entry filled
(an unfilled order has no outcome to reveal). Tradeable decision points only (setup windows, `entry_end`, blackouts).

The sample (seeded, so a round can be drawn again exactly):
- **lost**: `LOST_SHARE` of the cards from candidates whose bootstrap trade hit its stop;
- **never take**: `NEVER_SHARE` (at least `NEVER_MIN`) from candidates the owner would likely never take. Provisional
  definition (SPEC §13 Q-review-sample, for the owner): a single-level zone (stack count 1) traded against the day's
  direction so far (`with_day_direction` false); if there are too few, any single-level zone;
- **spread**: the rest, picked one at a time, each pick the candidate whose stack-count bucket, level group, entry
  style and time-of-day bucket are the least represented so far (seeded ties), so every value of each is covered.
At most one card per touch group (§5.4: a touch is traded at most once) and `MAX_PER_DAY` per session. The cards are
then shuffled (seeded), so their order says nothing about their stratum or outcome. Every pick records why it was drawn
(`Pick.why`, kept on the card and in the round), so the never-take cards can be redrawn once the owner defines them
(owner 2026-11-16, SPEC §13 Q-review-sample (1)).

**By month (real rounds; owner 2026-11-16):** a real round's cards are spread over the development months in proportion
to each month's sessions (`month_quotas`, largest remainder), each month's share drawn by `sample` from that month's
candidates, after the preview exclusion (`store.exclude_seen`): the exclusion empties most of the sessions a preview
card showed, so without the quotas the months it never touched would take a larger share (`sample_by_month`). A month
with fewer free candidates than its quota gives what it has; the shortfall is reported, not moved to another month.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Iterable

LOST_SHARE = 0.20          # §8 "plus valid setups that lost"
NEVER_SHARE = 0.10         # §8 "a few setups the owner would never take"
NEVER_MIN = 3
MAX_PER_DAY = 3            # spread over many sessions
DEFAULT_N = 60             # §8 / §12 step 14: 50-100 cards a round

# §8 strata: time-of-day buckets (ET minutes since midnight, [start, end))
TOD_BUCKETS = (("09:30-10:30", 570, 630), ("10:30-12:00", 630, 720), ("12:00-14:00", 720, 840),
               ("14:00-16:00", 840, 960))

# §8 strata: level groups by the primary member type (§5.2-§5.4 level families)
LEVEL_GROUPS = {
    "nodes": ("qqq_node", "ndxp_node", "spy_node", "spx_node"),
    "vwap": ("vwap18", "vwap0930", "vwap18_up1", "vwap18_up2", "vwap18_dn1", "vwap18_dn2", "vwap0930_up1",
             "vwap0930_up2", "vwap0930_dn1", "vwap0930_dn2"),
    "profile": ("poc_daily", "poc_weekly", "lvn_daily", "lvn_weekly", "prev_poc", "prev_vah", "prev_val",
                "single_print", "darkpool"),
    "highs/lows": ("pdh", "pdl", "on_high", "on_low", "ib_high", "ib_low", "prior_rth_high", "prior_rth_low",
                   "prior_week_high", "prior_week_low", "retested_extreme"),
    "expected move": ("em_up1", "em_up2", "em_dn1", "em_dn2"),
}


def level_group(primary_member_type: str | None, event_type: str | None = None) -> str:
    if event_type == "golden":
        return "golden zone"
    for g, types in LEVEL_GROUPS.items():
        if primary_member_type in types:
            return g
    return "other"


def stack_bucket(stack_count: int | None) -> str:
    n = int(stack_count or 0)
    return "1" if n <= 1 else "2" if n == 2 else "3+"


def tod_bucket(minute_of_day_et: int) -> str:
    for name, a, b in TOD_BUCKETS:
        if a <= minute_of_day_et < b:
            return name
    return "other"


@dataclass
class Candidate:
    """One event's card candidate: its decision point and bootstrap trade, with the facts the strata use."""
    day: str
    decision_id: str
    event_id: str
    touch_group_id: str
    style: str
    decision_ts: int
    minute_et: int
    stack_count: int
    level_group: str
    lost: bool
    never_take: bool
    weak: bool = False                       # a single-level zone (the never-take fallback)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def dims(self) -> tuple[str, str, str, str]:
        return (stack_bucket(self.stack_count), self.level_group, self.style, tod_bucket(self.minute_et))


WHY_LOST = "lost: its bootstrap trade hit the stop"
WHY_NEVER = "never take (stand-in): a single-level zone traded against the day's direction so far"
WHY_NEVER_FALLBACK = "never take (stand-in, fallback): a single-level zone"
WHY_SPREAD = "spread: the least-covered stack count, level group, entry style and time of day"


@dataclass
class Pick:
    cand: Candidate
    stratum: str                             # "spread" | "lost" | "never take"
    why: str = ""                            # how it was picked (one of the WHY_ texts)


def choose_events(decisions: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """§8: per event, its first style-(c) decision point if an audit or sweep fired, else its (d) one (tradeable
    decision points only)."""
    best: dict[str, dict[str, Any]] = {}
    for d in decisions:
        if not d.get("tradeable") or d.get("style") not in ("c", "d"):
            continue
        b = best.get(d["event_id"])
        key = (0 if d["style"] == "c" else 1, int(d.get("firing_n") or 1), int(d["decision_ts"]))
        if b is None or key < b["_key"]:
            best[d["event_id"]] = {**d, "_key": key}
    return best


def sample(cands: list[Candidate], n: int = DEFAULT_N, seed: int = 1, *, lost_share: float = LOST_SHARE,
           never_share: float = NEVER_SHARE, never_min: int = NEVER_MIN, max_per_day: int = MAX_PER_DAY) -> list[Pick]:
    """The round's cards (see the module note), shuffled."""
    rng = random.Random(seed)
    pool = sorted(cands, key=lambda c: (c.day, c.decision_ts, c.decision_id))
    rng.shuffle(pool)
    used_groups: set[str] = set()
    per_day: dict[str, int] = {}
    picks: list[Pick] = []

    def free(c: Candidate) -> bool:
        return c.touch_group_id not in used_groups and per_day.get(c.day, 0) < max_per_day

    def take(c: Candidate, stratum: str, why: str) -> None:
        used_groups.add(c.touch_group_id)
        per_day[c.day] = per_day.get(c.day, 0) + 1
        picks.append(Pick(c, stratum, why))

    n = max(0, min(n, len(pool)))
    n_lost = round(n * lost_share)
    n_never = min(n - n_lost, max(never_min, round(n * never_share))) if n else 0
    for c in [c for c in pool if c.lost and not c.never_take]:
        if sum(p.stratum == "lost" for p in picks) >= n_lost:
            break
        if free(c):
            take(c, "lost", WHY_LOST)
    for want, why in ((lambda c: c.never_take, WHY_NEVER), (lambda c: c.weak, WHY_NEVER_FALLBACK)):
        for c in [c for c in pool if want(c)]:
            if sum(p.stratum == "never take" for p in picks) >= n_never:
                break
            if free(c):
                take(c, "never take", why)
    counts: list[dict[str, int]] = [{} for _ in range(4)]
    for p in picks:
        for i, v in enumerate(p.cand.dims):
            counts[i][v] = counts[i].get(v, 0) + 1
    rest = [c for c in pool if free(c)]
    while len(picks) < n and rest:
        scores = [sum(counts[i].get(v, 0) for i, v in enumerate(c.dims)) for c in rest]
        low = min(scores)
        idx = [i for i, s in enumerate(scores) if s == low]
        c = rest[rng.choice(idx)]
        take(c, "spread", WHY_SPREAD)
        for i, v in enumerate(c.dims):
            counts[i][v] = counts[i].get(v, 0) + 1
        rest = [x for x in rest if free(x)]
    rng.shuffle(picks)
    return picks


def coverage(picks: list[Pick]) -> dict[str, dict[str, int]]:
    """How many cards per value of each stratum dimension (for the round's summary)."""
    names = ("stack count", "level group", "entry style", "time of day")
    out: dict[str, dict[str, int]] = {k: {} for k in (*names, "stratum")}
    for p in picks:
        for k, v in zip(names, p.cand.dims):
            out[k][v] = out[k].get(v, 0) + 1
        out["stratum"][p.stratum] = out["stratum"].get(p.stratum, 0) + 1
    return out


def month_quotas(sessions_by_month: dict[int, int], n: int) -> dict[int, int]:
    """n cards over the months in proportion to their sessions (largest remainder; ties: the earlier month)."""
    total = sum(v for v in sessions_by_month.values() if v > 0)
    if n <= 0 or total <= 0:
        return {m: 0 for m in sessions_by_month}
    raw = {m: n * max(0, v) / total for m, v in sessions_by_month.items()}
    out = {m: int(x) for m, x in raw.items()}
    for m in sorted(raw, key=lambda m: (-(raw[m] - out[m]), m))[: n - sum(out.values())]:
        out[m] += 1
    return out


def sample_by_month(cands: list[Candidate], n: int, seed: int, month_of, sessions_by_month: dict[int, int],
                    **kw: Any) -> tuple[list[Pick], dict[int, dict[str, int]]]:
    """`sample` within each month, n spread by `month_quotas`; the picks shuffled together (seeded), and per month its
    quota, its candidates and the cards drawn."""
    quotas = month_quotas(sessions_by_month, n)
    by: dict[int, list[Candidate]] = {}
    for c in cands:
        by.setdefault(month_of(c.day), []).append(c)
    picks: list[Pick] = []
    report: dict[int, dict[str, int]] = {}
    for m in sorted(quotas):
        got = sample(by.get(m, []), quotas[m], seed * 100 + m, **kw) if quotas[m] else []
        picks += got
        report[m] = {"sessions": sessions_by_month[m], "quota": quotas[m], "candidates": len(by.get(m, [])), "cards": len(got)}
    random.Random(seed).shuffle(picks)
    return picks, report
