"""Entry order types and fill rules (SPEC §5.5b; feature_spec F7; owner 2026-11-10, SPEC §13 Q-step13b (3)).

Reference prices (stop distance and RR are measured from them, §6.1):
- `limit`: a passive limit at the zone's near edge (rounded to the passive side); `limit_center`: at its centre (a single
  Skylit node: the mapped strike);
- `stop_limit`: the trigger 1 tick beyond the trigger bar's extreme in the trade direction (style (c)), or beyond the
  price at `decision_ts` (style (d)); the limit `max_entry_slip_ticks` beyond the trigger;
- `marketable_limit`: the best ask (buy) / bid (sell) from `mbp-1` at `decision_ts`; the limit `max_entry_slip_ticks`
  through it.
Arrival = `decision_ts` + `order_latency_sec`. Fills:
- passive limit: a trade strictly through the limit after arrival, filled at the limit (no queue model);
- stop-limit: triggered by the first trade at or through the trigger after arrival, then filled like a marketable limit
  at that trade's time, never worse than the limit; an unfilled rest becomes a resting limit at the limit price;
- marketable limit: at the best ask (bid) at arrival up to its displayed size, the rest at the limit (the pessimistic
  choice, no depth data); an ask beyond the limit leaves it resting at the limit.
Size = the default-risk NQ-equivalent size (`costs.Costs.size`). Removal (F7; each takes effect `order_latency_sec`
after it occurs, a fill before then counts): `entry_ttl_sec` after `decision_ts`, the zone crossed, vanished or its
reference point moved more than `entry_reprice_tol` (the snapshot's band track), a news blackout's start, `entry_end`.
**No mbp-1** (2025-10-01 to 10-06): a marketable limit is `no_book` (no fill, no label); a stop-limit fills at the
triggering trade's price (capped at the limit), flagged `entry_no_book`.
An unfilled order is recorded with the outcome a fill at its reference price at arrival would have had (report only).
"""

from __future__ import annotations

from dataclasses import dataclass

from nqbot.events.common import round_limit, round_trigger
from nqbot.features.session_tape import TICK

FILLED, UNFILLED, NO_BOOK = "filled", "unfilled", "no_book"
ORDER_TYPES = ("limit", "limit_center", "stop_limit", "marketable_limit")
EPS = 1e-9


@dataclass
class Fill:
    order_type: str
    ref: float
    limit: float | None
    status: str
    idx: int                 # first trade index of the path (the fill trade itself when `on_trade`)
    t: int                   # fill time (unfilled: arrival)
    px: float                # average fill price (unfilled: the reference price, the hypothetical fill)
    on_trade: bool
    removal_t: int           # when the removal takes effect
    removal_reason: str
    entry_no_book: bool = False

    @property
    def filled(self) -> bool:
        return self.status == FILLED


def reference(order_type: str, dp: dict, book, p) -> tuple[float | None, float | None]:
    """(reference price, limit price); (None, None) when it needs the book and there is none."""
    d = int(dp["direction"])
    slip = p.costs.max_entry_slip_ticks * TICK
    if order_type == "limit":
        x = round_limit(dp["near_edge"], d)
        return x, x
    if order_type == "limit_center":
        x = round_limit(dp["zone_center"], d)
        return x, x
    if order_type == "stop_limit":
        if dp["style"] == "c":
            base = dp["trigger_bar_high"] if d > 0 else dp["trigger_bar_low"]
        else:
            base = dp["price_at_decision"]
        s = round_trigger(base + d * TICK, d)
        return s, s + d * slip
    if order_type == "marketable_limit":
        q = book.at(int(dp["decision_ts"])) if book is not None else None
        a = None if q is None else (q[1] if d > 0 else q[0])
        if a is None or a != a:
            return None, None
        return a, a + d * slip
    raise ValueError(order_type)


def removal(dp: dict, order_type: str, meta: dict, p) -> tuple[int, str]:
    dts = int(dp["decision_ts"])
    ttl = int(round(float(p.ttl_sec[dp["style"]]) * 1e9))
    cands = [(dts + ttl, "ttl")]
    move = dp.get("t_move_center") if order_type == "limit_center" else dp.get("t_move_near")
    for t, why in ((dp.get("t_cross"), "crossed"), (dp.get("t_vanish"), "vanished"), (move, "moved")):
        if t is not None and t > dts:
            cands.append((int(t), why))
    for a, _b in meta["blackouts"]:
        if a > dts:
            cands.append((int(a), "blackout"))
            break
    if dts < meta["entry_end_ns"]:
        cands.append((int(meta["entry_end_ns"]), "entry_end"))
    t, why = min(cands)
    return t + p.latency_ns, why


def _book_fill(book, t: int, d: int, size: float, limit: float, trade_px: float | None):
    """Marketable-limit execution at t: (average price, no_book flag), or (None, flag) if the best price is beyond
    the limit (the order rests at the limit)."""
    q = book.at(t) if book is not None else None
    if q is None or (q[1] if d > 0 else q[0]) != (q[1] if d > 0 else q[0]):
        if trade_px is None:
            return None, True
        return (trade_px if (limit - trade_px) * d >= -EPS else None), True
    best, sz = (q[1], q[3]) if d > 0 else (q[0], q[2])
    if (limit - best) * d < -EPS:
        return None, False
    q1 = min(size, max(sz, 0.0))
    px = (q1 * best + (size - q1) * limit) / size
    if trade_px is not None:
        # never better than the trade that triggered it (owner 2026-11-13, SPEC §13 Q-step13-review L3: the book read
        # at that timestamp is after the whole sweep), nor worse than the limit
        px = min(max(px, trade_px), limit) if d > 0 else max(min(px, trade_px), limit)
    return px, False


def simulate(order_type: str, dp: dict, size: float, tape, path, book, meta: dict, p) -> Fill:
    d = int(dp["direction"])
    dts = int(dp["decision_ts"])
    arrival = dts + p.latency_ns
    rem_t, why = removal(dp, order_type, meta, p)
    ref, limit = reference(order_type, dp, book, p)
    ia, ib = tape.idx_before(arrival), tape.idx_before(rem_t)
    if ref is None:
        return Fill(order_type, float(dp["price_at_decision"]), None, NO_BOOK, ia, arrival,
                    float(dp["price_at_decision"]), False, rem_t, why)

    def rest(start: int, no_book: bool) -> Fill:
        k = path.first_le(start, limit - TICK / 2, ib) if d > 0 else path.first_ge(start, limit + TICK / 2, ib)
        if k is None:
            return Fill(order_type, ref, limit, UNFILLED, ia, arrival, ref, False, rem_t, why, no_book)
        return Fill(order_type, ref, limit, FILLED, k, int(tape.ts[k]), limit, True, rem_t, why, no_book)

    if order_type in ("limit", "limit_center"):
        return rest(ia, False)
    if order_type == "stop_limit":
        k = path.first_ge(ia, ref, ib) if d > 0 else path.first_le(ia, ref, ib)
        if k is None:
            return Fill(order_type, ref, limit, UNFILLED, ia, arrival, ref, False, rem_t, why)
        t = int(tape.ts[k])
        px, nb = _book_fill(book, t, d, size, limit, float(tape.price[k]))
        if px is None:
            return rest(k + 1, nb)
        return Fill(order_type, ref, limit, FILLED, k, t, px, True, rem_t, why, nb)
    if order_type == "marketable_limit":
        if arrival >= rem_t:
            return Fill(order_type, ref, limit, UNFILLED, ia, arrival, ref, False, rem_t, why)
        px, _ = _book_fill(book, arrival, d, size, limit, None)
        if px is None:
            return rest(ia, False)
        return Fill(order_type, ref, limit, FILLED, ia, arrival, px, False, rem_t, why)
    raise ValueError(order_type)
