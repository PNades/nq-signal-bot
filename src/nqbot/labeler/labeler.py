"""One session's pairs, fills and path summaries (SPEC §12 step 13, §6.6; owner 2026-11-10, SPEC §13 Q-step13b).

For each decision point (engine stage, `decisions/<D>.parquet`) x each order type of its style
(`tune_grids_13.execution.entry_order_type`) x each merged stop candidate x each target candidate:
- `pairs`: identity (`pair_id` = `<decision_id>-<order type>-s<k>-t<j>`), the stop / target, the reference price,
  net RR at default costs (`rr_band`: < 0.5, 0.5-0.8, >= 0.8; `rr_ext` = a low-RR extension pair, Q-flexfarm (1)),
  the fill (status, time, price), the labels under every breakeven rule (`out_<rule>`, `t_exit_<rule>`, `r_<rule>`),
  `ambiguous_tick`, `exit_no_book`, `tradeable` (the decision point's, §1.2);
- `fills`: one row per decision point x order type x stop (the fill depends on the size through the book), with the
  structures reached in order after it (§6.5);
- `paths`: the path summaries of each pair (MAE / MFE, overshoot, stopped-then-target, extension, returned to entry).
Unfilled orders keep their hypothetical outcome (a fill at the reference price at arrival), flagged `filled` false:
never labels for the scorer (§6.6). `no_book` rows (marketable limits without `mbp-1`) carry no outcome.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

from nqbot.features.session_tape import bars as make_bars
from nqbot.labeler import candidates as cand
from nqbot.labeler import fills as fl
from nqbot.labeler.firsthit import PathIndex
from nqbot.labeler.outcomes import Labeler
from nqbot.labeler.params import be_name

PAIR_LABEL_COLS = ("ambiguous_tick", "exit_no_book", "timeout_reason")


def label_session(decisions: list[dict], tape, book, meta: dict, p, path: PathIndex | None = None,
                  bars_1m=None) -> dict[str, Any]:
    t0 = time.monotonic()
    path = path or PathIndex(tape.price)
    bars_1m = bars_1m if bars_1m is not None else make_bars(tape, 1)
    lab = Labeler(tape, path, bars_1m, book, meta, p)
    pairs, fills, paths = [], [], []
    bands = Counter()
    gaps: dict = {}          # (exit kind) -> [exits, changed, extra ticks summed], every rule's exits of filled pairs
    for dp in decisions:
        d = int(dp["direction"])
        if p.stop_floor_vol is not None:   # Q13-vol: the trailing range at decision_ts (only bars closed by then)
            dp = {**dp, cand.VOL_KEY: cand.trailing_range(bars_1m, int(dp["decision_ts"]),
                                                          p.stop_floor_vol["window_min"])}
        for ot in p.order_types[dp["style"]]:
            ref0, _ = fl.reference(ot, dp, book, p)
            ref = ref0 if ref0 is not None else float(dp["price_at_decision"])
            for si, (stop, stypes) in enumerate(cand.stops(dp, ref, d, p)):
                stop_pts = abs(ref - stop)
                rpm = p.costs.risk_per_micro(stop_pts, ot)
                micros, size_nq, too_wide = p.costs.size(stop_pts, ot)
                fill = fl.simulate(ot, dp, float(size_nq), tape, path, book, meta, p)
                fid = f"{dp['decision_id']}-{ot}-s{si}"
                fills.append({"fill_id": fid, "decision_id": dp["decision_id"], "order_type": ot, "stop_k": si,
                              "status": fill.status, "ref": fill.ref, "limit": fill.limit, "t_fill": int(fill.t),
                              "fill_px": fill.px, "fill_idx": int(fill.idx), "on_trade": fill.on_trade,
                              "removal_t": int(fill.removal_t), "removal_reason": fill.removal_reason,
                              "entry_no_book": fill.entry_no_book, "size_nq": size_nq, "micros": micros,
                              "too_wide": too_wide, **_reached(lab, fill, dp, ref, d)})
                for ti, tg in enumerate(cand.targets(dp, ref, stop, d, ot, p)):
                    pid = f"{fid}-t{ti}"
                    band = cand.rr_band(tg["net_rr"], p.min_rr)
                    bands[(dp["style"], ot, band, tg["rr_ext"])] += 1
                    row = {"pair_id": pid, "fill_id": fid, "decision_id": dp["decision_id"], "event_id": dp["event_id"],
                           "touch_group_id": dp["touch_group_id"], "style": dp["style"], "firing_n": dp["firing_n"],
                           "order_type": ot, "decision_ts": int(dp["decision_ts"]), "tradeable": dp["tradeable"],
                           "stop": stop, "stop_types": stypes, "stop_pts": stop_pts, "ref": ref,
                           **tg, "target_pts": abs(tg["target"] - ref), "rr_band": band, "risk_per_micro": rpm,
                           "filled": fill.filled, "fill_status": fill.status}
                    if fill.status == fl.NO_BOOK:
                        pairs.append(row)
                        continue
                    fsz = cand.first_structure(dp, ref, tg["target"], d)
                    lb = lab.label(fill, stop, tg["target"], d, rpm, fsz, float(size_nq))
                    info = lb.pop("_path")
                    row.update(lb)
                    row["fs_zone"] = None if fsz is None else fsz["id"]
                    if fill.filled:
                        _count_gaps(gaps, row, p)
                    pairs.append(row)
                    paths.append({"pair_id": pid, "filled": fill.filled,
                                  **lab.path_summary(fill, stop, tg["target"], d, info)})
    info = {"pairs": len(pairs), "fills": len(fills), "label_sec": round(time.monotonic() - t0, 1),
            "rr_bands": {"|".join([s, o, b, "ext" if e else "base"]): n for (s, o, b, e), n in sorted(bands.items())},
            "be_rules": [be_name(r) for r in p.be_rules],
            "gap_rule": {k: {"exits": v[0], "changed": v[1], "mean_extra_ticks": (v[2] / v[1]) if v[1] else 0.0}
                         for k, v in sorted(gaps.items())}}
    return {"pairs": pairs, "fills": fills, "paths": paths, "info": info}


GAP_KIND = {"stop": "stop", "be": "stop", "timeout": "market"}


def _count_gaps(gaps: dict, row: dict, p) -> None:
    """The gap rule's effect (owner 2026-11-10, SPEC §13 Q-step13b (b) (vii)): stop and breakeven exits ('stop') and
    flat-by / news exits ('market') of every breakeven rule, and how many the rule changed with their extra cost."""
    for rule in p.be_rules:
        name = be_name(rule)
        kind = GAP_KIND.get(row.get(f"out_{name}"))
        if kind is None:
            continue
        g = float(row.get(f"gap_ticks_{name}") or 0.0)
        acc = gaps.setdefault(kind, [0, 0, 0.0])
        acc[0] += 1
        if g > 1e-9:
            acc[1] += 1
            acc[2] += g


def _reached(lab, fill, dp: dict, ref: float, d: int) -> dict[str, list]:
    got = lab.reached(fill, cand.ahead(dp, ref, d), d) if fill.status != fl.NO_BOOK else []
    return {"reached_zone": [z for z, _ in got], "reached_t": [int(t) for _, t in got]}


def base_rows(pairs: list[dict]) -> list[dict]:
    """The pairs of the unchanged set (no low-RR extension) with every label column, for the identity check."""
    return [r for r in pairs if not r.get("rr_ext")]


def identity_check(with_ext: list[dict], without_ext: list[dict]) -> dict[str, Any]:
    """Q-flexfarm (1): the >= 0.8 set (every pair not from the extension) and its labels identical with and without the
    extension."""
    a, b = base_rows(with_ext), without_ext
    same = len(a) == len(b) and all(_eq(x, y) for x, y in zip(a, b))
    return {"identity_ok": bool(same), "identity_rows": len(b), "ext_rows": len(with_ext) - len(a)}


def _eq(x: dict, y: dict) -> bool:
    if x.keys() != y.keys():
        return False
    for k in x:
        u, v = x[k], y[k]
        if isinstance(u, float) and isinstance(v, float) and u != u and v != v:
            continue
        if u != v:
            return False
    return True
