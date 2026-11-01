"""Per-board map structure of one Skylit map and session (step 11 part 2; SPEC §5.2; feature_spec F2.1-F2.5, F2.8,
F2.9, F2.12-F2.17).

Everything here is computed per applied board from that board alone (plus earlier boards), with the board's own spot
(F2.1 two clocks; NDXP's spot = QQQ spot × k, Q-step11d (4)), and is known at the board's `t_apply`:
- % of King against the board's own King (F2.3, `mapping.pct_of_king`), relevance with the 25% / 20% hysteresis
  (F2.4, `nodes.relevance`), sign and |value| (F2.2);
- the confirmed King by lead time (F2.16, `node_switch.ConfirmedNode` with `king_switch_min_sec`) and its migration
  features (`node_switch.KingMigration`); node types against the confirmed King (F2.5, `node_switch.node_types` rule);
- velocity and magnitude velocity against the latest board with τ′ <= τ − `velocity_lookback_sec` (F2.8, same strike,
  present on both boards), `lean_raw` (F2.9, `lean.lean_raw` rule) and the time-weighted `lean` (`lean.lean`);
- regime (F2.12), strike step and rug / reverse rug (F0.10, F2.13, `rug`), pika clouds (F2.14), air pockets (F2.15),
  King dominance (F2.16), rainbow-road counts (F2.17) and the relevant mass above / below spot (F2.11 compression).
A strike missing from a board is unknown on that board: it has no row there (F2.1). Mapping to futures prices (R) and
the trade-driven parts (crosses, rolls, magnets, pockets vs price, Trinity) are in `node_levels` / `skylit_session`.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from nqbot.features import lean as ln
from nqbot.features import mapping as mp
from nqbot.features import nodes
from nqbot.features import node_switch as nsw
from nqbot.features import rug as rg
from nqbot.features.boards import MapBoards

NS = mp.NS
KING, GATEKEEPER, SIGNIFICANT, NORMAL = "king", "gatekeeper", "significant", "normal"
TYPE_CODES = (KING, GATEKEEPER, SIGNIFICANT, NORMAL)


@dataclass
class Run:
    """A run of adjacent listed strikes (F0.10): a pika cloud or an air pocket, in the underlying's units."""
    lo: float
    hi: float
    n: int
    sum_pct: float
    max_pct: float
    neg_share: float      # Σ|v| of negative strikes ÷ Σ|v| (air pockets; 0 for a pika cloud)


@dataclass
class MapState:
    mb: MapBoards
    features: dict
    pct: np.ndarray = field(default=None)          # per row
    relevant: np.ndarray = field(default=None)
    held: np.ndarray = field(default=None)
    sign: np.ndarray = field(default=None)
    vel: np.ndarray = field(default=None)           # NaN = null
    mvel: np.ndarray = field(default=None)
    ntype: np.ndarray = field(default=None)         # index into TYPE_CODES
    gk_vs_beyond: np.ndarray = field(default=None)  # NaN unless a Gatekeeper
    rug: np.ndarray = field(default=None)           # +1 rug, -1 reverse rug, 0 none
    rug_n: np.ndarray = field(default=None)
    rug_pct_sum: np.ndarray = field(default=None)
    rug_first_pct: np.ndarray = field(default=None)
    pika_member: np.ndarray = field(default=None)    # positive and relevant by the pika hysteresis (a candidate)
    in_cloud: np.ndarray = field(default=None)       # a member of a pika cloud on that board (F2.14)
    board: dict = field(default_factory=dict)       # per board arrays
    clouds: list = field(default_factory=list)      # per board: [Run]
    pockets: list = field(default_factory=list)     # per board: [Run]
    king_switches: list = field(default_factory=list)   # (t_apply_ns, old, new)

    @property
    def name(self) -> str:
        return self.mb.name

    @classmethod
    def build(cls, mb: MapBoards, features: dict) -> MapState:
        self = cls(mb, features)
        self._rows()
        self._boards()
        return self

    # --- per row (vectorised) ------------------------------------------------------------------------------------
    def _rows(self) -> None:
        mb, f = self.mb, self.features
        n = len(mb)
        b, k, v = mb.s_board, mb.s_strike, mb.s_value
        self.pct = mp.pct_of_king(b, v, n) if len(b) else np.zeros(0)
        ok = np.ones(n, dtype=bool)
        self.relevant, self.held = nodes.relevance(b, k, self.pct, ok, f["min_node_pct_of_king"], f["node_drop_pct"])
        self.sign = np.sign(v)
        pika_in, _ = nodes.relevance(b, k, self.pct, ok, f["pika_cloud_min_pct"], f["node_drop_pct"])
        self.pika_member = pika_in & (v > 0)
        # F2.8: the same strike on the latest board with τ′ <= τ − lookback (same session)
        self.vel = np.full(len(b), np.nan)
        self.mvel = np.full(len(b), np.nan)
        if len(b):
            uniq = np.unique(k)
            sid = np.searchsorted(uniq, k)
            u = len(uniq)
            key = b.astype(np.int64) * u + sid
            pb = np.searchsorted(mb.tau, mb.tau - int(round(f["velocity_lookback_sec"] * NS)), side="right") - 1
            prev = pb[b]
            pk = np.where(prev >= 0, prev, 0) * u + sid
            pos = np.clip(np.searchsorted(key, pk), 0, len(key) - 1)
            found = (prev >= 0) & (key[pos] == pk)
            vp = np.where(found, v[pos], np.nan)
            with np.errstate(divide="ignore", invalid="ignore"):
                good = found & (vp != 0)
                self.vel = np.where(good, (v - vp) / np.abs(vp), np.nan)
                self.mvel = np.where(good, (np.abs(v) - np.abs(vp)) / np.abs(vp), np.nan)

    # --- per board ---------------------------------------------------------------------------------------------
    def _boards(self) -> None:
        mb, f = self.mb, self.features
        n = len(mb)
        b, k, v = mb.s_board, mb.s_strike, mb.s_value
        av = np.abs(v)
        spot_r = mb.spot[b] if len(b) else np.zeros(0)
        # board King (F2.3): largest |value|, ties nearer spot, then the lower strike
        king = np.full(n, np.nan)
        king_abs = np.full(n, np.nan)
        second = np.full(n, np.nan)
        if len(b):
            o = np.lexsort((k, np.abs(k - spot_r), -av, b))
            first = np.r_[True, b[o][1:] != b[o][:-1]]
            king[b[o][first]] = k[o][first]
            king_abs[b[o][first]] = av[o][first]
            fo = np.flatnonzero(first)
            p2 = fo[fo + 1 < len(o)] + 1                      # the second row of each board with two or more
            p2 = p2[b[o][p2] == b[o][p2 - 1]]
            second[b[o][p2]] = av[o][p2]
        with np.errstate(divide="ignore", invalid="ignore"):
            dominance = np.where((second > 0), king_abs / second, np.nan)
        # confirmed King (F2.16) and migration
        cn = nsw.from_config(f, "king")
        mig = nsw.KingMigration()
        ck = np.full(n, np.nan)
        changes = np.zeros(n, dtype=np.int64)
        last_shift = np.full(n, np.nan)
        stair = np.zeros(n, dtype=np.int64)
        last_change = np.full(n, -1, dtype=np.int64)    # ns; -1: no change yet
        for i in range(n):
            ks, _ = mb.strikes(i)
            lead = None if np.isnan(king[i]) else float(king[i])
            sw = cn.on_board(mb.t_apply[i] / NS, lead, ks.tolist(), bool(mb.one_minute[i]), float(mb.spot[i]))
            if sw is not None:
                mig.on_switch(sw, ks.tolist())
                self.king_switches.append((int(mb.t_apply[i]), sw.old, sw.new))
            ck[i] = np.nan if cn.confirmed is None else cn.confirmed
            changes[i], stair[i] = mig.changes_today, mig.stairstep_count
            last_shift[i] = np.nan if mig.last_shift_strikes is None else mig.last_shift_strikes
            if sw is not None:
                last_change[i] = mb.t_apply[i]
            elif i:
                last_change[i] = last_change[i - 1]
        # node types against the confirmed King (F2.5)
        ckr = ck[b] if len(b) else np.zeros(0)
        is_king = k == ckr
        lo, hi = np.minimum(spot_r, ckr), np.maximum(spot_r, ckr)
        between = self.relevant & ~is_king & (k > lo) & (k < hi) & np.isfinite(ckr)
        self.ntype = np.where(is_king, 0, np.where(between, 1, np.where(self.relevant, 2, 3))).astype(np.int8)
        # lean_raw (F2.9): relevant, known magnitude velocity, not at spot
        use = self.relevant & np.isfinite(self.mvel) & (k != spot_r)
        contrib = np.where(use, np.where(k > spot_r, 1.0, -1.0) * np.nan_to_num(self.mvel) * self.pct, 0.0)
        lean_raw = np.bincount(b, weights=contrib, minlength=n) if len(b) else np.zeros(n)
        # regime (F2.12): strikes within ±regime_band % of spot
        near = np.abs(k - spot_r) <= f["regime_band"] / 100.0 * spot_r
        s_v = np.bincount(b, weights=np.where(near, v, 0.0), minlength=n) if len(b) else np.zeros(n)
        s_a = np.bincount(b, weights=np.where(near, av, 0.0), minlength=n) if len(b) else np.zeros(n)
        s_p = np.bincount(b, weights=np.where(near & (v > 0), av, 0.0), minlength=n) if len(b) else np.zeros(n)
        with np.errstate(divide="ignore", invalid="ignore"):
            regime_net = np.where(king_abs > 0, s_v / king_abs, np.nan)
            regime_pos = np.where(s_a > 0, s_p / s_a, np.nan)
        # rainbow counts (F2.17), relevant mass above / below spot (F2.11), gatekeepers to the King (F2.5)
        rel = self.relevant
        n_pos = np.bincount(b, weights=(rel & (v > 0)).astype(float), minlength=n) if len(b) else np.zeros(n)
        n_neg = np.bincount(b, weights=(rel & (v < 0)).astype(float), minlength=n) if len(b) else np.zeros(n)
        above = np.bincount(b, weights=np.where(rel & (k > spot_r), av, 0.0), minlength=n) if len(b) else np.zeros(n)
        below = np.bincount(b, weights=np.where(rel & (k < spot_r), av, 0.0), minlength=n) if len(b) else np.zeros(n)
        gk_n = np.bincount(b, weights=(self.ntype == 1).astype(float), minlength=n) if len(b) else np.zeros(n)
        # nearest relevant node above / below spot (underlying units)
        ceil_d = np.full(n, np.nan)
        floor_d = np.full(n, np.nan)
        if len(b):
            d = k - spot_r
            up_rows = rel & (d > 0)
            dn_rows = rel & (d < 0)
            np.fmin.at(ceil_d, b[up_rows], d[up_rows])
            np.fmin.at(floor_d, b[dn_rows], -d[dn_rows])
        self.board = {"king": king, "king_abs": king_abs, "second_abs": second, "king_dominance": dominance,
                      "confirmed_king": ck, "king_changes_today": changes, "last_king_shift_strikes": last_shift,
                      "king_stairstep_count": stair, "last_king_change_ns": last_change, "lean_raw": lean_raw,
                      "regime_net": regime_net, "regime_pos_share": regime_pos, "n_pos_relevant": n_pos,
                      "n_neg_relevant": n_neg, "above_mass": above, "below_mass": below, "gatekeepers_to_king": gk_n,
                      "ceiling_dist": ceil_d, "floor_dist": floor_d, "strike_step": np.full(n, np.nan)}
        self._per_board_runs()

    def _per_board_runs(self) -> None:
        """Rug (F2.13), Gatekeeper vs beyond (F2.5), pika clouds (F2.14) and air pockets (F2.15), one board at a time."""
        mb, f = self.mb, self.features
        n = len(mb)
        m_rows = len(mb.s_strike)
        self.gk_vs_beyond = np.full(m_rows, np.nan)
        self.rug = np.zeros(m_rows, dtype=np.int8)
        self.rug_n = np.zeros(m_rows, dtype=np.int64)
        self.rug_pct_sum = np.full(m_rows, np.nan)
        self.rug_first_pct = np.full(m_rows, np.nan)
        self.in_cloud = np.zeros(m_rows, dtype=bool)
        step_arr = self.board["strike_step"]
        min_nodes, pocket_max = int(f["pika_cloud_min_nodes"]), float(f["air_pocket_max_pct"])
        for i in range(n):
            a, z = int(mb.offsets[i]), int(mb.offsets[i + 1])
            if z == a:
                self.clouds.append([])
                self.pockets.append([])
                continue
            ks, vs = mb.strikes(i)
            pct = self.pct[a:z]
            rel = self.relevant[a:z]
            spot = float(mb.spot[i])
            step = rg.strike_step(ks.tolist(), spot, f["regime_band"])
            step_arr[i] = np.nan if step is None else step
            values = dict(zip(ks.tolist(), vs.tolist()))
            pos_of = {kk: j for j, kk in enumerate(ks.tolist())}
            for j in np.flatnonzero(rel & (vs > 0)):
                p = float(ks[j])
                partners = rg.rug_partners(values, p, spot, step, f["rug_gap_strikes"], True)
                if partners:
                    self.rug[a + j] = 1 if spot < p else -1
                    self.rug_n[a + j] = len(partners)
                    pp = [float(pct[pos_of[x]]) for x in partners]
                    self.rug_pct_sum[a + j] = sum(pp)
                    self.rug_first_pct[a + j] = pp[0]
            ck = self.board["confirmed_king"][i]
            types = self.ntype[a:z]
            for j in np.flatnonzero(types == 1):
                g = float(ks[j])
                lo, hi = (g, ck) if ck > g else (ck, g)
                mask = (ks > lo) & (ks <= hi) if ck > g else (ks >= lo) & (ks < hi)
                beyond = np.abs(vs[mask])
                if len(beyond) and beyond.max() > 0:
                    self.gk_vs_beyond[a + j] = abs(vs[j]) / beyond.max()
            clouds = _runs(ks, vs, pct, self.pika_member[a:z], min_nodes)
            for c in clouds:
                self.in_cloud[a:z] |= (ks >= c.lo) & (ks <= c.hi)
            self.clouds.append(clouds)
            self.pockets.append(_runs(ks, vs, pct, pct < pocket_max, 1))

    # --- queries -------------------------------------------------------------------------------------------------
    def index_at(self, t_ns: int) -> int:
        return self.mb.index_at(t_ns)

    def rows(self, i: int) -> slice:
        return slice(int(self.mb.offsets[i]), int(self.mb.offsets[i + 1]))

    def lean_at(self, t_ns: int) -> float | None:
        """F2.9: the time-weighted mean of `lean_raw` over [t − lean_window, t] (boards applied <= t)."""
        ta = self.mb.t_apply
        j = bisect_right(ta, t_ns)
        if j == 0:
            return None
        w = float(self.features["lean_window"]) * 60.0
        i0 = max(0, bisect_right(ta, t_ns - int(w * NS)) - 1)
        times = (ta[i0:j] / NS).tolist()
        return ln.lean(times, self.board["lean_raw"][i0:j].tolist(), t_ns / NS, w)

    def features_at(self, t_ns: int) -> dict[str, Any]:
        """The map's per-board features as of t (no R: these stay during a dislocation block, F1.8)."""
        i = self.index_at(t_ns)
        out: dict[str, Any] = {k: None for k in MAP_FEATURES}
        if i < 0:
            return out
        bd = self.board
        lean = self.lean_at(t_ns)
        ck = bd["confirmed_king"][i]
        rows = self.rows(i)
        ks = self.mb.s_strike[rows]
        kv = self.vel[rows][ks == ck] if np.isfinite(ck) else np.zeros(0)
        dom = _num(bd["king_dominance"][i])
        n_pos, n_neg = int(bd["n_pos_relevant"][i]), int(bd["n_neg_relevant"][i])
        lc = bd["last_king_change_ns"][i]
        f = self.features
        out.update(
            map_age_sec=(t_ns - int(self.mb.tau[i])) / NS, map_resolution_1m=bool(self.mb.one_minute[i]),
            king_strike=_num(ck), king_dominance=dom, king_velocity=_num(kv[0]) if len(kv) else None,
            king_changes_today=int(bd["king_changes_today"][i]),
            last_king_shift_strikes=_int(bd["last_king_shift_strikes"][i]),
            minutes_since_king_change=None if lc < 0 else (t_ns - int(lc)) / (60 * NS),
            king_stairstep_count=int(bd["king_stairstep_count"][i]),
            regime_net=_num(bd["regime_net"][i]), regime_pos_share=_num(bd["regime_pos_share"][i]),
            regime_long=None if np.isnan(bd["regime_net"][i]) else int(np.sign(bd["regime_net"][i])),
            n_pos_relevant=n_pos, n_neg_relevant=n_neg,
            sign_mix=0.0 if min(n_pos, n_neg) == 0 else min(n_pos, n_neg) / max(n_pos, n_neg),
            rainbow=bool(n_pos >= f["rainbow_min_nodes_per_sign"] and n_neg >= f["rainbow_min_nodes_per_sign"]
                         and dom is not None and dom <= f["rainbow_max_dominance"]),
            gatekeepers_to_king=int(bd["gatekeepers_to_king"][i]),
            lean=lean, bias=ln.bias(lean, f["bias_deadband"]),
            has_relevant_floor=bool(np.isfinite(bd["floor_dist"][i])),
            has_relevant_ceiling=bool(np.isfinite(bd["ceiling_dist"][i])))
        return out

    def nodes_at(self, i: int) -> list[dict[str, Any]]:
        """Every strike of board i with its per-board state (relevant or not)."""
        rows = self.rows(i)
        ks, vs = self.mb.s_strike[rows], self.mb.s_value[rows]
        out = []
        for j in range(len(ks)):
            r = rows.start + j
            out.append({"strike": float(ks[j]), "value": float(vs[j]), "pct_of_king": float(self.pct[r]),
                        "relevant": bool(self.relevant[r]), "node_sign": int(self.sign[r]),
                        "node_type": TYPE_CODES[self.ntype[r]], "velocity": _num(self.vel[r]),
                        "magnitude_velocity": _num(self.mvel[r]), "rug": int(self.rug[r]) == 1,
                        "reverse_rug": int(self.rug[r]) == -1, "rug_neg_count": int(self.rug_n[r]),
                        "rug_neg_pct_sum": _num(self.rug_pct_sum[r]), "rug_first_partner_pct": _num(self.rug_first_pct[r]),
                        "gatekeeper_vs_beyond": _num(self.gk_vs_beyond[r])})
        return out


MAP_FEATURES = ("map_age_sec", "map_resolution_1m", "king_strike", "king_dominance", "king_velocity",
                "king_changes_today", "last_king_shift_strikes", "minutes_since_king_change", "king_stairstep_count",
                "regime_net", "regime_pos_share", "regime_long", "n_pos_relevant", "n_neg_relevant", "sign_mix",
                "rainbow", "gatekeepers_to_king", "lean", "bias", "has_relevant_floor", "has_relevant_ceiling")


def _num(x) -> float | None:
    return None if x is None or not np.isfinite(x) else float(x)


def _int(x) -> int | None:
    return None if x is None or not np.isfinite(x) else int(x)


def _runs(ks: np.ndarray, vs: np.ndarray, pct: np.ndarray, member: np.ndarray, min_n: int) -> list[Run]:
    """Maximal runs of adjacent listed strikes with `member` true, at least `min_n` long."""
    out = []
    j, m = 0, len(ks)
    while j < m:
        if not member[j]:
            j += 1
            continue
        e = j
        while e + 1 < m and member[e + 1]:
            e += 1
        if e - j + 1 >= min_n:
            av = np.abs(vs[j:e + 1])
            tot = float(av.sum())
            neg = float(av[vs[j:e + 1] < 0].sum())
            out.append(Run(float(ks[j]), float(ks[e]), e - j + 1, float(pct[j:e + 1].sum()), float(pct[j:e + 1].max()),
                           neg / tot if tot > 0 else 0.0))
        j = e + 1
    return out
