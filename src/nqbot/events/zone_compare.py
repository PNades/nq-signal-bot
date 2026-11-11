"""The 13d count of the zone-book candidate-time fix (owner 2026-11-04, SPEC §13 Q-step12a (1); coded in 13b,
Q-dryrun13 (9)): the session's NQ zone book rebuilt with the legacy candidate times (each moving-band pair checked only
from one trade to the next) beside the build's book, compared at every NQ trade. A local diagnostic object: the build's
outputs never read it, and the diagnostic switches (`features.diagnostics`) stay at their defaults."""

from __future__ import annotations

import time
from bisect import bisect_right
from typing import Any

import numpy as np

from nqbot.features.zones import ZoneBook


def compare(sf) -> dict[str, Any]:
    t0 = time.monotonic()
    f = sf.f
    tol, maxw = float(f["confluence_tol"]), float(f["max_zone_width"])
    jb, qb = float(f["zone_join_buffer"]), float(f["zone_qualify_buffer"])
    legacy = ZoneBook([x.book_level() for x in sf.nq_levels.values()], sf.nq, lambda t: (tol, maxw, jb, qb),
                      end_ns=sf.end_ns, step_time_candidates=False).run()
    trades = differing_trades(sf.book, legacy, sf.nq.ts)
    return {"zone_diff_trades": trades, "zone_diff_any": trades > 0,
            "zone_compare_sec": round(time.monotonic() - t0, 1)}


def differing_trades(a: ZoneBook, b: ZoneBook, ts: np.ndarray) -> int:
    """Trades at which the two books' zones (ids and members) differ."""
    cuts = sorted(set(a.times) | set(b.times))
    if not cuts or not len(ts):
        return 0
    n = 0
    bounds = cuts + [int(ts[-1]) + 1]
    for t0, t1 in zip(bounds, bounds[1:]):
        za = a.snaps[bisect_right(a.times, t0) - 1] if bisect_right(a.times, t0) else {}
        zb = b.snaps[bisect_right(b.times, t0) - 1] if bisect_right(b.times, t0) else {}
        if za != zb:
            n += int(np.searchsorted(ts, t1, side="left") - np.searchsorted(ts, t0, side="left"))
    return n
