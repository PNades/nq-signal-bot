"""The per-session Skylit engine (step 11 part 2; SPEC §5.1, §5.2, §5.4; feature_spec F1.8, F2.1-F2.20, F5.16).

`SkylitSession` takes the session's applied boards per map (`boards.MapBoards`), the NQ and ES front-contract tapes and
the session context (EM / ATR from part 1's chart sessions, the ES ÷ NQ ratio at the open), and builds:
- the dislocation guard per QQQ / SPY / SPX chain (F1.8, `disloc_guard.run_session` -> `block_spans`; NDXP takes QQQ's
  blocks: its R comes from QQQ × k);
- per map the board structure (`map_state.MapState`) and the nodes on the futures (`node_levels.MapNodes`).
Queries, each from data with known time <= t:
- `registry_at(t)`: relevant QQQ / NDXP nodes as NQ registry levels; `es_registry_at(t)`: SPY / SPX nodes (ES
  cross-market levels, F0.9);
- `features_at(t, direction)`: every map's per-board features (F2.5, F2.9, F2.12, F2.16, F2.17), its R-dependent
  ones (King distance, magnets F2.10, rolls F2.11, pockets vs price F2.15, pika clouds F2.14; null inside the chain's
  block, F1.8) and the whipsaw / trinity-bias features (F2.18);
- event evaluators over [t0, t] (part 3's events call them): `trinity` (F2.19, the overlap rule with Q18 / Q19 and the
  block rule Q-step11d (6)-(7)), `es_at_level` (F5.16, `es_level.es_at_level`), `sp_path` (F2.20), `pockets`
  (F2.15 ahead / behind), `magnets` (F2.10 with / against), `pika` (F2.14 vs a zone).
Every time is int ns; prices on the map's instrument (NQ for Nasdaq maps, ES for S&P maps, F0.9).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from nqbot.features import disloc_guard as dg
from nqbot.features import es_level as esl
from nqbot.features import lean as ln
from nqbot.features.boards import CHAIN_ROOT, MAPS, MapBoards
from nqbot.features.map_state import TYPE_CODES, MapState
from nqbot.features.node_levels import FLIP_KEY, MapNodes, Scale, _track
from nqbot.features.session_tape import MIN_NS, Tape, bars
from nqbot.features.trinity import CEILING, FLOOR, RoleTrack, counts_in_role, entry_role

NASDAQ = ("QQQ", "NDXP")
SP = ("SPY", "SPX")
CHAIN = {"QQQ": "QQQ", "NDXP": "QQQ", "SPY": "SPY", "SPX": "SPX"}   # whose dislocation blocks apply (F1.8)
TEMPEST_OF = {"QQQ": "QQQ", "NDXP": "QQQ", "SPY": "SPY", "SPX": "SPY"}  # the EM unit's root (F0.8)
ENTRY_SCAN_TRADES = 4096    # trades read per step when looking back for a band entry (Q19)


@dataclass
class Context:
    """Session inputs known by the session start (F0.1)."""
    features: dict
    settings: dict
    em_nq: float | None = None        # EM points on NQ (F0.8), known from the pre-open
    em_es: float | None = None
    atr_nq: float | None = None
    atr_es: float | None = None
    em_frac: dict | None = None       # {'QQQ': m, 'SPY': m}: Tempest's em_1d_pct / 100 (F0.8)
    preopen_ns: int | None = None     # EM known from here
    scale: Scale | None = None        # ES ÷ NQ at the open (F0.11)

    def em(self, m: str, t: int) -> float | None:
        if self.preopen_ns is not None and t < self.preopen_ns:
            return None
        return self.em_nq if CHAIN_ROOT[m] == "NQ" else self.em_es

    def atr(self, m: str) -> float | None:
        return self.atr_nq if CHAIN_ROOT[m] == "NQ" else self.atr_es

    def es_prox(self, t: int) -> float:
        """`es_zone_proximity` = `zone_proximity` × ES ÷ NQ at the open (F0.11; before it, the previous session's)."""
        sc = self.scale
        if sc is None:
            raise ValueError("es_zone_proximity needs the ES ÷ NQ ratio at the open (F0.11): Context.scale is not set")
        k = sc.today if sc.switch_ns is not None and t >= sc.switch_ns else sc.prev
        return float(self.features["zone_proximity"]) * k

    def prox(self, m: str, t: int) -> float:
        return float(self.features["zone_proximity"]) if CHAIN_ROOT[m] == "NQ" else self.es_prox(t)


class SkylitSession:
    def __init__(self, boards: dict[str, MapBoards], nq: Tape, es: Tape, ctx: Context, *, normals: Any = None,
                 end_ns: int | None = None, es_levels: list | None = None):
        self.ctx, self.f = ctx, ctx.features
        self.tapes = {"NQ": nq, "ES": es}
        self.boards = boards
        self.end_ns = end_ns
        gp = dg.GuardParams.from_features(self.f)
        self.fires: dict[str, list] = {}
        self.blocks: dict[str, list] = {}
        for chain in self.f["mapping"]["disl_guard_maps"]:
            mb = boards.get(chain)
            fires = [] if mb is None or len(mb) == 0 else dg.run_session(chain, gp, mb.t_apply, mb.r_board, mb.r)
            self.fires[chain] = fires
            self.blocks[chain] = dg.block_spans(fires)
        bar_sets = {root: (bars(tp, 1), bars(tp, self.f["flip_bar_minutes"])) for root, tp in self.tapes.items()}
        self.ms: dict[str, MapState] = {}
        self.nodes: dict[str, MapNodes] = {}
        for m in MAPS:
            mb = boards.get(m)
            if mb is None:
                continue
            ms = self.ms[m] = MapState.build(mb, self.f)
            root = CHAIN_ROOT[m]
            b1, bf = bar_sets[root]
            self.nodes[m] = MapNodes(ms, self.tapes[root], self.f, b1, bf, blocks=self.map_blocks(m),
                                     normals=normals if root == "NQ" else None, end_ns=end_ns,
                                     scale=ctx.scale if root == "ES" else None)
        # ES cross-market chart levels (F5.1, part 1's ES ChartSession records), for es_at_level
        self.es_levels = [(rec.level_id, rec.sub_type, rec.state, _track(rec.state)) for rec in (es_levels or [])]
        self._role_arrays: dict[int, tuple] = {}

    # --- basics ---------------------------------------------------------------------------------------------------
    def map_blocks(self, m: str) -> list[tuple[int, int]]:
        return list(self.blocks.get(CHAIN[m], []))

    def blocked(self, m: str, t: int) -> bool:
        """The chain is blocked at t: the closed span [a, z], as `disloc_guard.in_block` (labels' `tradeable`)."""
        return any(a <= t <= z for a, z in self.map_blocks(m))

    def blocked_in(self, m: str, t0: int, t: int) -> bool:
        return any(a <= t and z >= t0 for a, z in self.map_blocks(m))

    def price(self, m_or_root: str, t: int) -> float | None:
        root = CHAIN_ROOT.get(m_or_root, m_or_root)
        return self.tapes[root].last_price_at(t)

    def board_at(self, m: str, t: int) -> int:
        ms = self.ms.get(m)
        return -1 if ms is None else ms.index_at(t)

    # --- registry --------------------------------------------------------------------------------------------------
    def _relevant_nodes(self, m: str, t: int) -> list[dict]:
        ms, mn = self.ms.get(m), self.nodes.get(m)
        i = self.board_at(m, t)
        if i < 0:
            return []
        rows = ms.rows(i)
        out = []
        em, atr = self.ctx.em(m, t), self.ctx.atr(m)
        for j in np.flatnonzero(ms.relevant[rows]):
            r = rows.start + int(j)
            k = float(ms.mb.s_strike[r])
            st = mn.node_at(k, t, atr, em)
            if st is None or st["band"] is None:
                continue
            out.append({"level_id": f"{m.lower()}_node-{ms.mb.day.isoformat()}-{k:g}", "type": "gex_node",
                        "sub_type": FLIP_KEY[m] if m in NASDAQ else f"{m.lower()}_node", "key": FLIP_KEY[m],
                        "map": m, "instrument": CHAIN_ROOT[m], "strike": k, "blocked": self.blocked(m, t),
                        "pct_of_king": float(ms.pct[r]), "node_type": TYPE_CODES[ms.ntype[r]],
                        "node_sign": int(ms.sign[r]), "node_abs_value": float(abs(ms.mb.s_value[r])),
                        "velocity": None if np.isnan(ms.vel[r]) else float(ms.vel[r]),
                        "rug": int(ms.rug[r]) == 1, "reverse_rug": int(ms.rug[r]) == -1,
                        "in_pika_cloud_member": bool(ms.in_cloud[r]),
                        "minutes_since_relevant": self._minutes_since_relevant(m, k, t), **st})
        return out

    def _minutes_since_relevant(self, m: str, k: float, t: int) -> float | None:
        nl = self.nodes[m].levels.get(k)
        return None if nl is None else (t - nl.relevant_since(t)) / MIN_NS

    def registry_at(self, t: int) -> list[dict]:
        return [x for m in NASDAQ for x in self._relevant_nodes(m, t)]

    def es_registry_at(self, t: int) -> list[dict]:
        return [x for m in SP for x in self._relevant_nodes(m, t)]

    # --- map features ------------------------------------------------------------------------------------------
    def features_at(self, t: int, direction: int | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {}
        bias: dict[str, str | None] = {}
        ranges: dict[str, tuple[bool, bool]] = {}
        for m in MAPS:
            p = m.lower() + "_"
            if m not in self.ms:
                continue
            base = self.ms[m].features_at(t)
            bias[m] = base["bias"]
            ranges[m] = (base["has_relevant_floor"], base["has_relevant_ceiling"])
            out.update({p + k: v for k, v in base.items()})
            out.update({p + k: v for k, v in self._unit_dists(m, t).items()})
            out.update({p + k: v for k, v in self._r_features(m, t, direction).items()})
        for nas in NASDAQ:   # F2.18, computed twice (QQQ and NDXP as the Nasdaq side, FQ-12)
            agree, conflict = ln.trinity_bias(bias.get("SPY"), bias.get("SPX"), bias.get(nas))
            fl, ce = ranges.get(nas, (False, False))
            out[f"trinity_bias_agree_{nas.lower()}"] = agree
            out[f"trinity_bias_conflict_{nas.lower()}"] = conflict
            out[f"whipsaw_{nas.lower()}"] = ln.whipsaw(bias.get("SPY"), bias.get("SPX"), bias.get(nas), fl, ce)
        return out

    def _unit_dists(self, m: str, t: int) -> dict[str, Any]:
        """F2.17 floor / ceiling distance from spot in EM units, in the underlying (spot × m): no R needed."""
        ms = self.ms[m]
        i = ms.index_at(t)
        out = {"floor_dist_em": None, "ceiling_dist_em": None, "above_mass_change_open": None,
               "below_mass_change_open": None}
        if i < 0:
            return out
        frac = (self.ctx.em_frac or {}).get(TEMPEST_OF[m])
        if frac and (self.ctx.preopen_ns is None or t >= self.ctx.preopen_ns):
            unit = float(ms.mb.spot[i]) * frac
            for name, key in (("floor_dist_em", "floor_dist"), ("ceiling_dist_em", "ceiling_dist")):
                d = ms.board[key][i]
                out[name] = None if not np.isfinite(d) or unit <= 0 else float(d / unit)
        for side in ("above", "below"):
            first = ms.board[f"{side}_mass"][0]
            out[f"{side}_mass_change_open"] = None if first <= 0 else float(ms.board[f"{side}_mass"][i] / first - 1)
        return out

    def _r_features(self, m: str, t: int, direction: int | None) -> dict[str, Any]:
        """Features that convert strikes with R: null inside the chain's dislocation block (F1.8)."""
        keys = R_FEATURES
        out = {k: None for k in keys}
        ms, mn = self.ms[m], self.nodes[m]
        i = ms.index_at(t)
        px = self.price(m, t)
        if i < 0 or px is None or self.blocked(m, t):
            return out
        r = float(ms.mb.r[i])
        em, atr = self.ctx.em(m, t), self.ctx.atr(m)
        ck = ms.board["confirmed_king"][i]
        if np.isfinite(ck):
            d = ck * r - px
            out["king_distance"] = float(d)
            out["king_distance_em"] = None if not em else float(d / em)
        out.update(self.magnets(m, t, direction))
        for side_name, mr in (("ceiling", mn.ceiling), ("floor", mn.floor)):
            s = mr.at(t)
            if s is None:
                continue
            out[f"rolling_{side_name}"] = s["rolling"]
            out[f"roll_paused_{side_name}"] = bool(s["paused"]) if s["episode"] else False
            steps = s["roll_steps"]
            out[f"roll_steps_{side_name}"] = steps
            if steps is not None and s["anchor"] is not None and s["confirmed"] is not None:
                out[f"roll_steps_{side_name}_pts"] = float(abs(s["anchor"] - s["confirmed"]) * r) * (1 if steps >= 0 else -1)
            for k in ("rolls_today", "rollbacks_today", "partial_rollbacks_today", "roll_breaks_today",
                      "last_roll_shrink_pct", "last_roll_growth_pct", "minutes_since_episode_began"):
                out[f"{k}_{side_name}"] = s[k]
            eb = s["episode_board"]
            mass = "above" if side_name == "ceiling" else "below"
            if s["episode"] and eb is not None and eb > 0:
                base = ms.board[f"{mass}_mass"][eb - 1]
                out[f"{mass}_mass_change_roll"] = None if base <= 0 else float(ms.board[f"{mass}_mass"][i] / base - 1)
        pk = self.pika(m, t)
        out.update(pk)
        pocket = self._pocket_at_price(m, t)
        out.update(pocket)
        return out

    # --- magnets (F2.10) ---------------------------------------------------------------------------------------
    def magnets(self, m: str, t: int, direction: int | None = None) -> dict[str, Any]:
        out = {"magnet_pull_above": None, "magnet_pull_below": None, "magnet_net": None, "magnet_pull_with": None,
               "magnet_pull_against": None}
        ms = self.ms.get(m)
        i = -1 if ms is None else ms.index_at(t)
        px, em = self.price(m, t), self.ctx.em(m, t)
        if i < 0 or px is None or not em or self.blocked(m, t):
            return out
        rows = ms.rows(i)
        rel = ms.relevant[rows]
        lv = ms.mb.s_strike[rows][rel] * float(ms.mb.r[i])
        pct = ms.pct[rows][rel]
        d = np.maximum(np.abs(lv - px) / em, float(self.f["magnet_min_dist_em"]))
        pull = pct / d
        above, below = float(pull[lv > px].sum()), float(pull[lv < px].sum())
        out.update(magnet_pull_above=above, magnet_pull_below=below, magnet_net=above - below)
        if direction:
            out["magnet_pull_with"], out["magnet_pull_against"] = (above, below) if direction > 0 else (below, above)
        return out

    # --- pika clouds (F2.14) -------------------------------------------------------------------------------------
    def pika(self, m: str, t: int, zone: tuple[float, float] | None = None) -> dict[str, Any]:
        out = {"in_pika_cloud": None, "pika_cloud_overlaps_zone": None, "pika_cloud_members": None,
               "pika_cloud_sum_pct": None, "pika_cloud_max_pct": None, "pika_cloud_width": None,
               "pika_cloud_width_em": None, "pika_cloud_edge_dist": None}
        ms = self.ms.get(m)
        i = -1 if ms is None else ms.index_at(t)
        px = self.price(m, t)
        if i < 0 or px is None or self.blocked(m, t):
            return out
        r, h = float(ms.mb.r[i]), float(self.f["node_band"][m])
        pad = 0.0 if m in NASDAQ else self.ctx.es_prox(t)
        clouds = [((c.lo - h) * r, (c.hi + h) * r, c) for c in ms.clouds[i]]
        out["in_pika_cloud"] = any(lo - pad <= px <= hi + pad for lo, hi, _ in clouds)
        if zone is not None and m in NASDAQ:
            out["pika_cloud_overlaps_zone"] = any(lo <= zone[1] and hi >= zone[0] for lo, hi, _ in clouds)
        if clouds:
            def dist(c):
                lo, hi, _ = c
                return 0.0 if lo <= px <= hi else min(abs(px - lo), abs(px - hi))
            lo, hi, c = min(clouds, key=dist)
            em = self.ctx.em(m, t)
            out.update(pika_cloud_members=c.n, pika_cloud_sum_pct=c.sum_pct, pika_cloud_max_pct=c.max_pct,
                       pika_cloud_width=hi - lo, pika_cloud_width_em=None if not em else (hi - lo) / em,
                       pika_cloud_edge_dist=dist((lo, hi, c)))
        return out

    # --- air pockets (F2.15) ---------------------------------------------------------------------------------
    def _pocket_bands(self, m: str, i: int) -> list[tuple[float, float, Any]]:
        ms = self.ms[m]
        r, h = float(ms.mb.r[i]), float(self.f["node_band"][m])
        return [((p.lo - h) * r, (p.hi + h) * r, p) for p in ms.pockets[i]]

    def _pocket_at_price(self, m: str, t: int) -> dict[str, Any]:
        out = {"price_in_pocket": None, "pocket_size_strikes": None, "pocket_size_pts": None, "pocket_size_em": None,
               "pocket_neg_share": None}
        ms = self.ms[m]
        i = ms.index_at(t)
        px = self.price(m, t)
        if i < 0 or px is None:
            return out
        hit = [x for x in self._pocket_bands(m, i) if x[0] <= px <= x[1]]
        out["price_in_pocket"] = bool(hit)
        if hit:
            lo, hi, p = hit[0]
            em = self.ctx.em(m, t)
            out.update(pocket_size_strikes=p.n, pocket_size_pts=hi - lo, pocket_size_em=None if not em else (hi - lo) / em,
                       pocket_neg_share=p.neg_share)
        return out

    def pockets(self, m: str, t: int, direction: int, reach_pts: float | None = None) -> dict[str, Any]:
        """F2.15 ahead (trade direction) and behind (stop side). Ahead: the first pocket starting within the gap to the
        next relevant node whose band lies entirely beyond price (Q-step11d (10)); with no such node, within
        `reach_pts` (structure model 1 EM, pair scorer the pair's target distance; null if none, Q15). Sizes in
        strikes and points; 0 when a node is ahead and no pocket starts before it."""
        out = {"pocket_ahead_size": None, "pocket_ahead_pts": None, "pocket_ahead_neg_share": None,
               "pocket_behind_size": None, "pocket_behind_pts": None, "pocket_behind_neg_share": None}
        ms = self.ms.get(m)
        i = -1 if ms is None else ms.index_at(t)
        px = self.price(m, t)
        if i < 0 or px is None or self.blocked(m, t) or not direction:
            return out
        r = float(ms.mb.r[i])
        rows = ms.rows(i)
        h = float(self.f["node_band"][m])
        rel_k = ms.mb.s_strike[rows][ms.relevant[rows]]
        lo_b, hi_b = (rel_k - h) * r, (rel_k + h) * r
        pockets = self._pocket_bands(m, i)
        if reach_pts is None and self.ctx.em(m, t):
            reach_pts = self.ctx.em(m, t)
        for name, d in (("ahead", direction), ("behind", -direction)):
            # Q-step11d (10): the next relevant node whose band lies entirely beyond price (the event's own node,
            # whose band holds price, doesn't cap the gap); the gap runs to that band's near edge
            near_edge = lo_b[lo_b > px] if d > 0 else hi_b[hi_b < px]
            nxt = near_edge
            limit = float(np.min(np.abs(nxt - px))) if len(nxt) else reach_pts
            if limit is None:
                continue
            starts = [((lo if d > 0 else hi) - px) * d for lo, hi, _ in pockets]
            cand = sorted(((s, i) for i, s in enumerate(starts) if 0 <= s <= limit))
            if cand:
                lo, hi, p = pockets[cand[0][1]]
                out[f"pocket_{name}_size"], out[f"pocket_{name}_pts"] = p.n, hi - lo
                out[f"pocket_{name}_neg_share"] = p.neg_share
            elif len(nxt):   # a relevant node ahead and no pocket before it: 0
                out[f"pocket_{name}_size"], out[f"pocket_{name}_pts"] = 0, 0.0
            # no relevant node ahead and no pocket within the reach: null (Q15)
        return out

    # --- S&P path (F2.20) ----------------------------------------------------------------------------------------
    def sp_path(self, t: int, nq_target: float, direction: int) -> dict[str, Any]:
        out = {"sp_path_count": None, "sp_path_max_pct": None, "sp_path_has_king": None,
               "sp_path_has_gatekeeper": None, "sp_path_first_dist_atr": None}
        nq, es = self.price("NQ", t), self.price("ES", t)
        if nq is None or es is None or any(self.blocked(m, t) for m in SP if m in self.ms):
            return out
        es_target = nq_target * es / nq
        lo_p, hi_p = sorted((es, es_target))
        found = []
        for m in SP:
            for n in self._relevant_nodes(m, t):
                b0, b1 = n["band"]
                if b0 > lo_p and b1 < hi_p:
                    found.append((abs((b0 if direction > 0 else b1) - es), n))
        out.update(sp_path_count=len(found), sp_path_max_pct=max((n["pct_of_king"] for _, n in found), default=None),
                   sp_path_has_king=any(n["node_type"] == "king" for _, n in found),
                   sp_path_has_gatekeeper=any(n["node_type"] == "gatekeeper" for _, n in found))
        if found and self.ctx.atr_es:
            out["sp_path_first_dist_atr"] = min(d for d, _ in found) / self.ctx.atr_es
        return out

    # --- Trinity (F2.19) ---------------------------------------------------------------------------------------
    def check_times(self, t0: int, t: int) -> np.ndarray:
        """τ ∈ [t0, t]: t0, every NQ and ES trade and every board apply in (t0, t] (F2.19)."""
        parts = [np.array([t0], dtype=np.int64)]
        for tp in self.tapes.values():
            parts.append(tp.ts[tp.idx_at(t0):tp.idx_at(t)])
        for mb in self.boards.values():
            ta = mb.t_apply
            parts.append(ta[(ta > t0) & (ta <= t)])
        return np.unique(np.concatenate(parts))

    def _roles(self, track: RoleTrack) -> tuple[np.ndarray, list]:
        key = id(track)
        if key not in self._role_arrays:
            self._role_arrays[key] = (np.array([r[0] for r in track.roles], dtype=np.int64), [r[1] for r in track.roles])
        return self._role_arrays[key]

    def _entered_from(self, tape: Tape, prox: float, tau: int, state) -> str | None:
        """Q19: the side the price last came from before the inside stretch that contains τ ('below' / 'above').
        Trades before the node's creation (its first relevant board) are judged against its band at creation, known
        at τ (the entry is a fact of the price path, which may come before the node became relevant); trades while
        the strike is missing from the board (band unknown) are skipped."""
        j = tape.idx_at(tau)
        start = tape.idx_before(int(state.band.start_ns))
        band0 = state.band.at(int(state.band.start_ns))
        while j > 0:
            i0 = max(0, j - ENTRY_SCAN_TRADES)
            lo, hi = state.band.at_trades(i0, j)
            if i0 < start and band0 is not None:
                pre = np.arange(i0, j) < start
                lo, hi = np.where(pre, band0[0], lo), np.where(pre, band0[1], hi)
            p = tape.price[i0:j]
            with np.errstate(invalid="ignore"):
                out = np.flatnonzero((p < lo - prox) | (p > hi + prox))
            if len(out):
                k = out[-1]
                return "below" if p[k] < lo[k] - prox else "above"
            j = i0
        return None

    def _node_overlaps(self, m: str, taus: np.ndarray, rho: str, t: int, extra: np.ndarray | None = None,
                       px: np.ndarray | None = None) -> list[tuple[int, float]]:
        """(first counting τ, strike) of every node of map m that overlaps in role ρ at some unblocked τ and still
        counts at t (Q18, Q19 on every map, Q-step11d (6)-(7)). `extra`: a further condition per τ (S&P maps: NQ
        inside a Nasdaq node in ρ at the same moment)."""
        mn = self.nodes.get(m)
        if mn is None:
            return []
        tape = self.tapes[CHAIN_ROOT[m]]
        if px is None:
            px = _prices(tape, taus)
        prox = np.array([self.ctx.prox(m, int(x)) for x in taus]) if CHAIN_ROOT[m] == "ES" else \
            np.full(len(taus), self.ctx.prox(m, int(taus[0])))
        unblocked = ~np.array([self.blocked(m, int(x)) for x in taus])
        out = []
        for k, nl in mn.levels.items():
            mask = self._inside(nl.state, taus, px, prox) & unblocked
            if extra is not None:
                mask &= extra
            if not mask.any():
                continue
            hit = self._first_counting(nl, taus, mask, rho, t, tape, prox)
            if hit is not None:
                out.append((hit, k))
        return sorted(out)

    def _inside(self, state, taus: np.ndarray, px: np.ndarray, prox: np.ndarray) -> np.ndarray:
        band = state.band
        kk = np.searchsorted(band.times, taus, side="right") - 1
        ok = kk >= 0
        kc = np.clip(kk, 0, None)
        lo, hi = band.lo[kc], band.hi[kc]
        with np.errstate(invalid="ignore"):
            inside = ok & np.isfinite(lo) & (px >= lo - prox) & (px <= hi + prox)
        if state.freezes:
            fz = np.zeros(len(taus), dtype=bool)
            for a, z in state.freezes:
                fz |= (taus >= a) & (taus < z)
            inside &= ~fz
        return inside

    def _first_counting(self, nl, taus, mask, rho, t, tape, prox) -> int | None:
        ts, roles = self._roles(nl.track)
        at_t = nl.track.role_at(t)
        if at_t is not None and at_t != rho:
            return None                       # Q18: it doesn't hold ρ at t, so no overlap of it counts
        last_x = max((x for x, r in nl.track.crosses if r == rho and x <= t), default=None)
        for idx in np.flatnonzero(mask):
            tau = int(taus[idx])
            if last_x is not None and tau < last_x:
                continue                      # crossed against ρ after this overlap
            pos = int(np.searchsorted(ts, tau, side="right")) - 1
            role_tau = roles[pos] if pos >= 0 else None
            entered = None
            if role_tau is None:
                entered = self._entered_from(tape, float(prox[idx]), tau, nl.state)
                if entry_role(entered) != rho:
                    continue
            elif role_tau != rho:
                continue
            if counts_in_role(nl.track, rho, tau, t, entered):
                return tau
        return None

    def trinity(self, t0: int, t: int, rho: str) -> dict[str, Any]:
        """F2.19 for an NQ event with role ρ touched at t0, evaluated at t (the structure model: t = t0)."""
        taus = self.check_times(t0, t)
        nq_px, es_px = _prices(self.tapes["NQ"], taus), _prices(self.tapes["ES"], taus)
        status: dict[str, bool | None] = {}
        first: dict[str, tuple[int, float] | None] = {}
        nas_inside = np.zeros(len(taus), dtype=bool)
        for m in NASDAQ:
            if self.board_at(m, t) < 0:
                status[m], first[m] = None, None
                continue
            ov = self._node_overlaps(m, taus, rho, t, px=nq_px)
            first[m] = ov[0] if ov else None
            status[m] = self._status(m, t0, t, bool(ov))
            nas_inside |= self._nasdaq_inside_role(m, taus, nq_px, rho)
        for m in SP:
            if self.board_at(m, t) < 0:
                status[m], first[m] = None, None
                continue
            ov = self._node_overlaps(m, taus, rho, t, extra=nas_inside, px=es_px)
            first[m] = ov[0] if ov else None
            status[m] = self._status(m, t0, t, bool(ov), also=NASDAQ)
        nas = _any3(status["QQQ"], status["NDXP"])
        parts = [nas, status["SPY"], status["SPX"]]
        count = None if any(p is None for p in parts) else int(sum(bool(p) for p in parts))
        here = None if any(status[m] is None for m in MAPS) else int(sum(bool(status[m]) for m in MAPS))
        out: dict[str, Any] = {"trinity_count": count, "trinity_aligned": None if count is None else count == 3,
                               "maps_with_node_here": here, **{f"trinity_{m.lower()}": status[m] for m in MAPS}}
        out.update(self._aligned_stats([(m, first[m]) for m in MAPS if status[m] and first[m] is not None]))
        return out

    def _status(self, m: str, t0: int, t: int, found: bool, also: tuple = ()) -> bool | None:
        """Q-step11d (7): null at a block at t; an overlap found counts; none found -> null if part of the window
        was blocked, else false. (8): for SPY / SPX the Nasdaq leg's chain counts too (`also`: an overlap needs NQ
        inside a QQQ / NDXP node at the same moment, so a QQQ block could have hidden one)."""
        if self.blocked(m, t):
            return None
        if found:
            return True
        hidden = self.blocked_in(m, t0, t) or any(self.blocked_in(x, t0, t) for x in also if x in self.ms)
        return None if hidden else False

    def _nasdaq_inside_role(self, m: str, taus: np.ndarray, px: np.ndarray, rho: str) -> np.ndarray:
        """NQ inside a relevant Nasdaq node holding ρ at τ (or unroled, entered from ρ's side), unblocked."""
        mn = self.nodes[m]
        out = np.zeros(len(taus), dtype=bool)
        prox = np.full(len(taus), self.ctx.prox(m, int(taus[0])))
        unblocked = ~np.array([self.blocked(m, int(x)) for x in taus])
        tape = self.tapes["NQ"]
        for nl in mn.levels.values():
            mask = self._inside(nl.state, taus, px, prox) & unblocked
            if not mask.any():
                continue
            ts, roles = self._roles(nl.track)
            for idx in np.flatnonzero(mask & ~out):
                tau = int(taus[idx])
                pos = int(np.searchsorted(ts, tau, side="right")) - 1
                role = roles[pos] if pos >= 0 else None
                if role is None:
                    role = entry_role(self._entered_from(tape, float(prox[idx]), tau, nl.state))
                out[idx] = role == rho
        return out

    def _aligned_stats(self, firsts: list) -> dict[str, Any]:
        """Smallest / mean % of King of the aligned nodes, King / Gatekeeper / rug / pika among them, each at its
        first overlap moment (F2.19)."""
        out = {"aligned_min_pct": None, "aligned_mean_pct": None, "aligned_has_king": None,
               "aligned_has_gatekeeper": None, "aligned_has_rug": None, "aligned_has_pika": None}
        if not firsts:
            return out
        pcts, types, rugs, pikas = [], [], [], []
        for m, (tau, k) in firsts:
            ms = self.ms[m]
            i = ms.index_at(tau)
            rows = ms.rows(i)
            j = np.flatnonzero(ms.mb.s_strike[rows] == k)
            if not len(j):
                continue
            r = rows.start + int(j[0])
            pcts.append(float(ms.pct[r]))
            types.append(TYPE_CODES[ms.ntype[r]])
            rugs.append(int(ms.rug[r]) != 0)
            pikas.append(bool(ms.in_cloud[r]))
        if pcts:
            out.update(aligned_min_pct=min(pcts), aligned_mean_pct=float(np.mean(pcts)),
                       aligned_has_king="king" in types, aligned_has_gatekeeper="gatekeeper" in types,
                       aligned_has_rug=any(rugs), aligned_has_pika=any(pikas))
        return out

    # --- es_at_level (F5.16) ---------------------------------------------------------------------------------
    def es_zones(self, nq_inside: Callable[[int], bool]) -> list[esl.EsZone]:
        """One-member ES zones: the ES chart levels and the relevant SPY / SPX nodes (part 3 merges them, F5.3)."""
        es = self.tapes["ES"]
        zones = []

        def chart_inside(state):
            def f(tau):
                b = state.band.at(tau)
                p = es.last_price_at(tau)
                return b is not None and p is not None and nq_inside(tau) and \
                    b[0] - self.ctx.es_prox(tau) <= p <= b[1] + self.ctx.es_prox(tau)
            return f
        for _lid, sub, state, track in self.es_levels:
            zones.append(esl.single(chart_inside(state), track, None, sub))
        for m in SP:
            mn = self.nodes.get(m)
            if mn is None:
                continue
            for nl in mn.levels.values():
                def f(tau, nl=nl, m=m):
                    if not nq_inside(tau) or self.blocked(m, tau):
                        return False
                    p = es.last_price_at(tau)
                    if p is None:
                        return False
                    return bool(self._inside(nl.state, np.array([tau], dtype=np.int64), np.array([p]),
                                             np.array([self.ctx.es_prox(tau)]))[0])
                zones.append(esl.single(f, nl.track, m, f"{m.lower()}_node"))
        return zones

    def es_at_level(self, t0: int, t: int, rho: str, nq_inside: Callable[[int], bool]) -> dict[str, Any]:
        taus = [int(x) for x in self.check_times(t0, t)]
        blocked = {m for m in SP if self.blocked_in(m, t0, t)}
        return esl.es_at_level(self.es_zones(nq_inside), rho, taus, t, blocked)


ROLL_KEYS = ("rolling", "roll_paused", "roll_steps", "rolls_today", "rollbacks_today", "partial_rollbacks_today",
             "roll_breaks_today", "last_roll_shrink_pct", "last_roll_growth_pct", "minutes_since_episode_began")
R_FEATURES = ("king_distance", "king_distance_em", "magnet_pull_above", "magnet_pull_below", "magnet_net",
              "magnet_pull_with", "magnet_pull_against",
              *(f"{k}_{s}" for s in ("ceiling", "floor") for k in ROLL_KEYS),
              "roll_steps_ceiling_pts", "roll_steps_floor_pts", "above_mass_change_roll", "below_mass_change_roll",
              "in_pika_cloud", "pika_cloud_overlaps_zone", "pika_cloud_members", "pika_cloud_sum_pct",
              "pika_cloud_max_pct", "pika_cloud_width", "pika_cloud_width_em", "pika_cloud_edge_dist",
              "price_in_pocket", "pocket_size_strikes", "pocket_size_pts", "pocket_size_em", "pocket_neg_share")


def _prices(tape: Tape, taus: np.ndarray) -> np.ndarray:
    """The last trade at or before each τ (NaN before the first)."""
    j = np.searchsorted(tape.ts, taus, side="right") - 1
    return np.where(j >= 0, tape.price[np.clip(j, 0, None)] if len(tape) else np.nan, np.nan)


def _any3(a: bool | None, b: bool | None) -> bool | None:
    if a or b:
        return True
    if a is None or b is None:
        return None
    return False


__all__ = ["Context", "SkylitSession", "CEILING", "FLOOR", "R_FEATURES"]
