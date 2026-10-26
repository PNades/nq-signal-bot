"""F3.2b previous-session profile window, with the `prev_profile_anchor` step-13 variant (owner 2026-10-26, SPEC §13
Q-vaedge): `globex_1800_1700` (current) = the previous session's CME day [18:00 ET of the day before, 17:00);
`rth_0930_1600` = its RTH [09:30, 16:00) (an early close: [09:30, `early_close_et`)). The POC, `prev_poc_naked`, VAH/VAL
and the F3.12 value-context features all read the profile built from that window, on that day's own front contract
(F0.4); it is known at the window's end (the naked flag counts trades from then).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
GLOBEX, RTH = "globex_1800_1700", "rth_0930_1600"
ANCHORS = (GLOBEX, RTH)


def profile_window(prev_session: date, anchor: str, early_close: time | None = None) -> tuple[datetime, datetime]:
    """[start, end) in ET of the previous session's profile."""
    if anchor == GLOBEX:
        return (datetime.combine(prev_session - timedelta(days=1), time(18, 0), ET),
                datetime.combine(prev_session, time(17, 0), ET))
    if anchor == RTH:
        return (datetime.combine(prev_session, time(9, 30), ET),
                datetime.combine(prev_session, early_close or time(16, 0), ET))
    raise ValueError(f"prev_profile_anchor must be one of {ANCHORS}, not {anchor!r}")


def volume_by_price(trades, window: tuple[datetime, datetime]) -> dict:
    """Σ size per price over the trades (ts, price, size) inside [start, end); the F3.2 / F3.3 rules read this."""
    start, end = window
    out: dict = defaultdict(float)
    for ts, price, size in trades:
        if start <= ts < end:
            out[price] += size
    return dict(out)
