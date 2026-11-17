"""The §8 step-14 review sample (review/sampler.py), on synthetic candidates."""

import random

from nqbot.review import sampler as smp


def cand(i, *, day=None, group=None, stack=2, style="d", minute=600, lost=False, never=False, weak=False, lg="vwap"):
    return smp.Candidate(day=day or f"2026-01-{1 + i % 20:02d}", decision_id=f"d{i}", event_id=f"e{i}",
                         touch_group_id=group or f"g{i}", style=style, decision_ts=i, minute_et=minute,
                         stack_count=stack, level_group=lg, lost=lost, never_take=never, weak=weak,
                         extra={"pair_id": f"p{i}"})


def pool(n=400, seed=3):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        stack = rng.choice([1, 1, 2, 3, 4])
        out.append(cand(i, stack=stack, style=rng.choice("cdd"), minute=rng.randrange(570, 960),
                        lg=rng.choice(["vwap", "nodes", "profile", "highs/lows", "golden zone"]),
                        lost=rng.random() < 0.5, never=stack == 1 and rng.random() < 0.3, weak=stack == 1))
    return out


def test_choose_events_prefers_style_c_first_firing_and_tradeable_only():
    decs = [{"decision_id": "e1-d1", "event_id": "e1", "style": "d", "firing_n": 1, "decision_ts": 10, "tradeable": True},
            {"decision_id": "e1-c2", "event_id": "e1", "style": "c", "firing_n": 2, "decision_ts": 30, "tradeable": True},
            {"decision_id": "e1-c1", "event_id": "e1", "style": "c", "firing_n": 1, "decision_ts": 20, "tradeable": True},
            {"decision_id": "e2-d1", "event_id": "e2", "style": "d", "firing_n": 1, "decision_ts": 40, "tradeable": True},
            {"decision_id": "e3-d1", "event_id": "e3", "style": "d", "firing_n": 1, "decision_ts": 50, "tradeable": False}]
    got = smp.choose_events(decs)
    assert {k: v["decision_id"] for k, v in got.items()} == {"e1": "e1-c1", "e2": "e2-d1"}


def test_quotas_one_per_touch_group_and_per_day_cap():
    p = pool()
    picks = smp.sample(p, 60, seed=1)
    assert len(picks) == 60
    strata = [x.stratum for x in picks]
    assert strata.count("lost") == 12 and strata.count("never take") == 6
    assert all(x.cand.lost for x in picks if x.stratum == "lost")
    assert all(x.cand.never_take or x.cand.weak for x in picks if x.stratum == "never take")
    assert len({x.cand.touch_group_id for x in picks}) == 60
    per_day = {}
    for x in picks:
        per_day[x.cand.day] = per_day.get(x.cand.day, 0) + 1
    assert max(per_day.values()) <= smp.MAX_PER_DAY


def test_the_spread_covers_every_value_and_is_repeatable():
    p = pool()
    a, b = smp.sample(p, 60, seed=5), smp.sample(p, 60, seed=5)
    assert [x.cand.decision_id for x in a] == [x.cand.decision_id for x in b]
    assert [x.cand.decision_id for x in smp.sample(p, 60, seed=6)] != [x.cand.decision_id for x in a]
    cov = smp.coverage(a)
    assert set(cov["stack count"]) == {"1", "2", "3+"}
    assert set(cov["entry style"]) == {"c", "d"}
    assert set(cov["level group"]) == {"vwap", "nodes", "profile", "highs/lows", "golden zone"}
    assert set(cov["time of day"]) == {b[0] for b in smp.TOD_BUCKETS}
    # the spread balances: no value of a dimension holds more than half the cards
    assert max(cov["entry style"].values()) <= 40


def test_touch_groups_are_shared_by_events_of_one_touch():
    p = [cand(i, group="g-same" if i < 5 else None) for i in range(20)]
    picks = smp.sample(p, 20, seed=2)
    assert sum(1 for x in picks if x.cand.touch_group_id == "g-same") == 1


def test_never_take_falls_back_to_weak_zones_and_small_pools():
    p = [cand(i, stack=1, weak=True) for i in range(5)] + [cand(i + 5) for i in range(40)]
    picks = smp.sample(p, 30, seed=1)
    assert sum(1 for x in picks if x.stratum == "never take") == 3
    assert smp.sample(p[:4], 60, seed=1) and len(smp.sample(p[:4], 60, seed=1)) == 4
    assert smp.sample([], 60) == []


def test_buckets():
    assert smp.tod_bucket(9 * 60 + 30) == "09:30-10:30" and smp.tod_bucket(15 * 60 + 59) == "14:00-16:00"
    assert smp.stack_bucket(1) == "1" and smp.stack_bucket(2) == "2" and smp.stack_bucket(7) == "3+"
    assert smp.level_group("qqq_node") == "nodes" and smp.level_group(None, "golden") == "golden zone"
    assert smp.level_group("pdh") == "highs/lows" and smp.level_group("weird") == "other"


def test_every_pick_records_how_it_was_picked():
    p = [cand(i, stack=1, never=i % 2 == 0, weak=True, lost=i % 5 == 0) for i in range(60)] + [cand(i + 60) for i in range(60)]
    picks = smp.sample(p, 40, seed=3)
    whys = {x.stratum: set() for x in picks}
    for x in picks:
        whys[x.stratum].add(x.why)
    assert whys["lost"] == {smp.WHY_LOST} and whys["spread"] == {smp.WHY_SPREAD}
    assert whys["never take"] <= {smp.WHY_NEVER, smp.WHY_NEVER_FALLBACK}
    assert all(x.why == smp.WHY_NEVER for x in picks if x.stratum == "never take" and x.cand.never_take)


def test_month_quotas_follow_the_sessions():
    # owner 2026-11-16: a real round spreads its cards over months 1-6 in proportion to each month's sessions
    sess = {1: 23, 2: 19, 3: 22, 4: 20, 5: 19, 6: 22}
    q = smp.month_quotas(sess, 60)
    assert sum(q.values()) == 60
    assert q == {1: 11, 2: 9, 3: 11, 4: 10, 5: 9, 6: 10}          # 11.04 9.12 10.56 9.6 9.12 10.56, largest remainders
    assert smp.month_quotas({1: 1, 2: 1, 3: 1}, 2) == {1: 1, 2: 1, 3: 0}   # ties: the earlier month
    assert smp.month_quotas({1: 0, 2: 5}, 4) == {1: 0, 2: 4}
    assert smp.month_quotas({1: 3}, 0) == {1: 0}
