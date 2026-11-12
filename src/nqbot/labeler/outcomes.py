"""Pair labels and path summaries (SPEC §5.4b, §5.5b, §6.4, §6.5, §6.6; feature_spec F7, F11.3).

From the fill, on the session's NQ trades in exchange sequence:
- the bracket rests at the broker from the fill: the **stop** fills on the first trade at or through it (the fill trade
  itself included: entry and stop on one print = filled and stopped, `ambiguous_tick`), at the worse of the stop ∓
  `exit_slippage_ticks` and the last print of that trade's sweep (every print at its `ts_event`, `sweep_end`): a gap
  never fills better than the actual trades (owner 2026-11-10/11, SPEC §13 Q-step13b (b) (vii), (d)); the **target**
  needs a trade 1 tick through it, after the fill trade (entry and target on one print = target not yet reached);
- **timeout**: the session flat-by, or the start of the next news blackout if earlier (the news flatten, SPEC §9), each
  taking effect `order_latency_sec` later, closed at market: the best bid (ask) at that time up to its size, the rest
  over the following trades in sequence (no book: the trades only, flagged `exit_no_book`), never better than the last
  print of the first sweep at or after that time (the same gap rule);
- **breakeven** rules (`be_trigger`, §6.4): `none`; `r<k>` = the first trade k x the stop distance in favour; `fs` =
  the close of the first 1-minute bar settled past the first structure (F11.3, its band frozen at `decision_ts`; none
  when the target is the first structure); the signal takes effect `order_latency_sec` later, then the stop moves to
  the fill + the MNQ breakeven offset (ceil(commission ÷ $0.50) + `be_offset_ticks` ticks) and a trade at or through it
  exits (`be`), at that price ∓ `exit_slippage_ticks` or the triggering sweep's last print if worse (the gap rule).
The gap rule's effect is recorded per exit (`gap_ticks_<rule>`, ticks of extra cost against the trade, 0 when it
changes nothing) and counted by the labeler.
R per outcome (per micro at default costs, `costs.Costs.realized_r`). Path summaries (§6.5, the `none` rule's trade):
MAE / MFE (and their times) to the exit; if stopped: the overshoot past the stop and whether the target was reached
afterwards (and how long after) before the RTH close; if the target was hit: the extension past it and whether price
returned to the entry afterwards.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from nqbot.features.session_tape import NS, TICK
from nqbot.labeler.costs import be_ticks

TARGET, STOP, BE, TIMEOUT = "target", "stop", "be", "timeout"
EPS = 1e-9


def timeout_at(fill_t: int, meta: dict, latency_ns: int) -> tuple[int, str]:
    occ, why = int(meta["flat_ns"]), "flat_by"
    for a, _b in meta["blackouts"]:
        if fill_t < a < occ:
            occ, why = int(a), "news_flatten"
            break
    return occ + latency_ns, why


def sweep_end(tape, k: int) -> int:
    """The last print of trade k's sweep: every print at trade k's `ts_event` (one aggressor's prints share it; the
    tape is in exchange sequence within a timestamp). The gap rule compares against its price (owner 2026-11-11, SPEC
    §13 Q-step13b (d): a stop is hit by the sweep's first print at the stop, and fills against what the sweep left)."""
    return int(np.searchsorted(tape.ts, tape.ts[k], side="right")) - 1


def market_exit(tape, book, t: int, d: int, size: float) -> tuple[float, bool]:
    """Closing a position of direction d at market at t: (average price, no_book), never better than the last print of
    the first sweep at or after t (the gap rule, owner 2026-11-10/11, SPEC §13 Q-step13b (b) (vii), (d))."""
    px, nb, _ = market_exit_gap(tape, book, t, d, size)
    return px, nb


def market_exit_gap(tape, book, t: int, d: int, size: float) -> tuple[float, bool, float]:
    """(price, no_book, the gap rule's extra cost in ticks) of a market exit at t."""
    raw, nb = _market_raw(tape, book, t, d, size)
    k = tape.idx_before(t)
    if k >= len(tape):
        return raw, nb, 0.0
    last = float(tape.price[sweep_end(tape, k)])
    px = min(raw, last) if d > 0 else max(raw, last)
    return px, nb, (raw - px) * d / TICK


def _market_raw(tape, book, t: int, d: int, size: float) -> tuple[float, bool]:
    k = tape.idx_before(t)
    q = book.at(t) if book is not None else None
    left, cost, nb = float(size), 0.0, True
    if q is not None:
        best, sz = (q[0], q[2]) if d > 0 else (q[1], q[3])
        if best == best and sz > 0:
            take = min(left, sz)
            cost += take * best
            left -= take
            nb = False
    while left > EPS and k < len(tape):
        take = min(left, float(tape.size[k]) or 1.0)
        cost += take * float(tape.price[k])
        left -= take
        k += 1
    if left > EPS:   # no trade left in the session: the last price
        cost += left * float(tape.price[-1])
    return cost / size, nb


class Labeler:
    def __init__(self, tape, path, bars_1m, book, meta: dict, p):
        self.tape, self.path, self.bars, self.book, self.meta, self.p = tape, path, bars_1m, book, meta, p
        self.close_i = tape.idx_before(int(meta["close_ns"]))
        self.be_off = be_ticks(p.costs.commission_micro, p.be_offset_ticks)
        self._stop_cache: dict = {}
        self._tgt_cache: dict = {}

    # --- hits ------------------------------------------------------------------------------------------------------
    def stop_hit(self, fill, stop: float, d: int, i_x: int) -> int | None:
        key = (fill.idx, stop, d, i_x)
        if key not in self._stop_cache:
            self._stop_cache[key] = self.path.first_beyond(fill.idx, stop, -d, i_x)
        return self._stop_cache[key]

    def target_hit(self, fill, target: float, d: int, j: int) -> int | None:
        i_t = fill.idx + (1 if fill.on_trade else 0)
        key = (i_t, target, d, j)
        if key not in self._tgt_cache:
            self._tgt_cache[key] = self.path.first_beyond(i_t, target + d * TICK, d, j)
        return self._tgt_cache[key]

    def _fs_signal(self, fill, zone: dict | None, d: int) -> int | None:
        if zone is None:
            return None
        b = self.bars
        L = b.length_ns
        k0 = int(np.searchsorted(b.start + L, fill.t, side="right"))
        if d > 0:
            hit = (b.open[k0:] > zone["hi"] + EPS) & (b.close[k0:] > zone["hi"] + EPS)
        else:
            hit = (b.open[k0:] < zone["lo"] - EPS) & (b.close[k0:] < zone["lo"] - EPS)
        k = np.flatnonzero(hit)
        return int(b.start[k0 + k[0]]) + L if len(k) else None

    # --- one pair ---------------------------------------------------------------------------------------------------
    def label(self, fill, stop: float, target: float, d: int, rpm: float, fs_zone: dict | None,
              size: float) -> dict[str, Any]:
        tape, p, c = self.tape, self.p, self.p.costs
        t_x, why_x = timeout_at(fill.t, self.meta, p.latency_ns)
        i_x = tape.idx_before(t_x)
        ks = self.stop_hit(fill, stop, d, i_x)
        kt = self.target_hit(fill, target, d, i_x)
        out: dict[str, Any] = {"ambiguous_tick": bool(fill.on_trade and ks == fill.idx), "timeout_reason": why_x}
        mx = None

        def timeout() -> tuple[str, int, float, float]:
            nonlocal mx
            if mx is None:
                mx = market_exit_gap(tape, self.book, t_x, d, size)
            return TIMEOUT, t_x, mx[0], mx[2]

        def stop_exit(o: str, k: int, price: float) -> tuple[str, int, float, float]:
            plain = c.stop_fill(price, d)
            px = c.stop_fill(price, d, float(tape.price[sweep_end(tape, k)]))
            return o, int(tape.ts[k]), px, (plain - px) * d / TICK

        def base() -> tuple[str, int, float, float, int | None]:
            if ks is not None and (kt is None or ks <= kt):
                return (*stop_exit(STOP, ks, stop), ks)
            if kt is not None:
                return TARGET, int(tape.ts[kt]), target, 0.0, kt
            return (*timeout(), None)

        o0, t0, px0, g0, k0 = base()
        rules = {}
        for rule in p.be_rules:
            name = _name(rule)
            if name == "none":
                rules[name] = (o0, t0, px0, g0)
                continue
            if name == "fs":
                t_sig = self._fs_signal(fill, fs_zone, d)
            else:
                k = float(rule["r_multiple"])
                trig = fill.px + d * k * abs(fill.px - stop)
                kb = self.path.first_beyond(fill.idx + (1 if fill.on_trade else 0), trig, d, i_x)
                t_sig = None if kb is None else int(tape.ts[kb])
            if t_sig is None:
                rules[name] = (o0, t0, px0, g0)
                continue
            t_be = t_sig + p.latency_ns
            i_be = tape.idx_before(t_be)
            if (k0 is not None and k0 < i_be) or t_be >= t_x:
                rules[name] = (o0, t0, px0, g0)
                continue
            be_px = _round_be(fill.px + d * self.be_off * TICK, d)
            kbx = self.path.first_beyond(i_be, be_px, -d, i_x)
            kt2 = kt if kt is not None and kt >= i_be else None
            if kbx is not None and (kt2 is None or kbx <= kt2):
                rules[name] = stop_exit(BE, kbx, be_px)
            elif kt2 is not None:
                rules[name] = (TARGET, int(tape.ts[kt2]), target, 0.0)
            else:
                rules[name] = timeout()
        for name, (o, t, px, g) in rules.items():
            out[f"out_{name}"] = o
            out[f"t_exit_{name}"] = int(t)
            out[f"r_{name}"] = c.realized_r(fill.px, px, d, rpm)
            out[f"gap_ticks_{name}"] = g
        out["exit_no_book"] = bool(mx is not None and mx[1])
        out["_path"] = (o0, k0, ks, kt, i_x)
        return out

    def path_summary(self, fill, stop: float, target: float, d: int, info: tuple) -> dict[str, Any]:
        tape, path = self.tape, self.path
        o0, k0, ks, kt, i_x = info
        end = (k0 + 1) if k0 is not None else i_x
        out: dict[str, Any] = {k: None for k in ("mae", "mae_t", "mfe", "mfe_t", "overshoot", "stopped_then_target",
                                                 "stt_delay_sec", "extension", "returned_to_entry", "rte_t")}
        adv = path.extreme(fill.idx, end, -d)
        fav = path.extreme(fill.idx, end, d)
        if adv is not None:
            out["mae"] = max(0.0, (fill.px - adv[0]) * d)
            out["mae_t"] = int(tape.ts[adv[1]])
        if fav is not None:
            out["mfe"] = max(0.0, (fav[0] - fill.px) * d)
            out["mfe_t"] = int(tape.ts[fav[1]])
        ic = self.close_i
        if o0 == STOP and ks is not None:
            x = path.extreme(ks, ic, -d)
            out["overshoot"] = None if x is None else max(0.0, (stop - x[0]) * d)
            k = path.first_beyond(ks + 1, target + d * TICK, d, ic)
            out["stopped_then_target"] = k is not None
            out["stt_delay_sec"] = None if k is None else (int(tape.ts[k]) - int(tape.ts[ks])) / NS
        if o0 == TARGET and kt is not None:
            x = path.extreme(kt, ic, d)
            out["extension"] = None if x is None else max(0.0, (x[0] - target) * d)
            k = path.first_beyond(kt + 1, fill.px, -d, ic)
            out["returned_to_entry"] = k is not None
            out["rte_t"] = None if k is None else int(tape.ts[k])
        return out

    def reached(self, fill, zones_ahead: list[dict], d: int) -> list[list]:
        """Structures reached in order after the fill (§6.5): [zone id, time] of each zone ahead whose near edge price
        reached before the RTH close."""
        out = []
        for z in zones_ahead:
            k = self.path.first_beyond(fill.idx, z["lo"] if d > 0 else z["hi"], d, self.close_i)
            if k is not None:
                out.append([z["id"], int(self.tape.ts[k])])
        out.sort(key=lambda x: x[1])
        return out


def _name(rule) -> str:
    from nqbot.labeler.params import be_name

    return be_name(rule)


def _round_be(px: float, d: int) -> float:
    """The breakeven stop on the tick grid, toward the entry (the conservative side)."""
    import math

    return math.floor(px / TICK + EPS) * TICK if d > 0 else math.ceil(px / TICK - EPS) * TICK
