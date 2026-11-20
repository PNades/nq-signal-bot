"""The stop floor read from the settings (owner 2026-11-20, pre-13d; SPEC §13 Q13-vol, Q-step13c): `stop_floor_vol` in
settings.yaml decides the floor, fixed (`null`, the `stop_buffer_floor` per source) or volatility-scaled (a window and
one {min, k} per source), so the configuration alone decides it.

The digests below were taken from the labeler as it was before the setting existed (branch `pre-13d` at c2047f9,
where the floor was an override only), on the synthetic session built here (pairs, fills and paths, every field):
- `DIGEST_DEFAULT`: `lp.params(S, F)`, the committed settings: the labels every build has had;
- `DIGEST_FIXED_B1`: `lp.params(S, F, {"stop_buffer": 1})`, a row-13 point on the fixed floor;
- `DIGEST_VOL_B1`: `lp.params(S, F, {"stop_buffer": 1, "stop_floor_vol": VOL})`, the run-time override 13c's tune job
  labels row 13's vol-floor point at buffer 1 with (`step13c.stop_over` -> `labels_plan_for` -> `labels-day`).
The settings path must give each exactly, and the same labels key as the override path."""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest

from nqbot.config import load_features, load_settings
from nqbot.features import session_tape as st
from nqbot.labeler import labeler as lb
from nqbot.labeler import params as lp
from tests.test_labeler import at, dp, meta
from tests.test_touches_labels import DAY

S, F = load_settings(), load_features()
VOL = {"window_min": 15, "QQQ": {"min": 2.79, "k": 0.195}}     # the shape `tuning.volfloor` writes as `override`

DIGEST_DEFAULT = "30883e6ca93fa3246fe7484fe57fdcdf0506eb69c320426098eba1296b70c6dc"
DIGEST_FIXED_B1 = "52ff8e4758c4fda6e398d42e47e14c45392009e1bb5e0dba393feed48fe30fc6"
DIGEST_VOL_B1 = "805c19cf89268b954b17eecc184022ee74c34614f3a90ee358e65ffb895addc3"


def _session():
    """A random-walk NQ tape from 09:31 to 11:30, three decision points (style d and c, long and short) at QQQ, NDXP and
    chart zones, so the floor of each source and the trailing range both matter."""
    rng = np.random.default_rng(20261120)
    n = 9000
    ts = at(9, 31) + np.cumsum(rng.integers(200, 1400, n)) * 1_000_000
    px = 102 + np.round(np.cumsum(rng.normal(0, 0.9, n)) / 0.25) * 0.25
    tape = st.Tape.from_arrays(DAY, ts, px, size=np.ones(n))
    zs = [60.0, 80.0, 94.0, 112.0, 121.0, 135.0]
    srcs = ["QQQ", "chart", "NDXP", "QQQ", "chart", "NDXP"]
    common = dict(z_id=[f"z{k}" for k in range(6)], z_kind=["level"] * 6, z_lo=zs, z_hi=[z + 2 for z in zs],
                  z_lo_src=srcs, z_hi_src=srcs)
    decs = [dp(decision_id="E1-d1", event_id="E1", decision_ts=at(10, 0), zone_lo_src="QQQ", zone_hi_src="QQQ", **common),
            dp(decision_id="E2-c1", event_id="E2", touch_group_id="g2", style="c", decision_ts=at(10, 20),
               zone_lo_src="NDXP", zone_hi_src="QQQ", trigger_bar_high=103.0, trigger_bar_low=101.0, **common),
            dp(decision_id="E3-d1", event_id="E3", touch_group_id="g3", decision_ts=at(10, 40), direction=-1,
               zone_lo=100.0, zone_hi=102.0, near_edge=100.0, price_at_decision=99.5, swing_high=101.0, swing_low=90.0,
               zone_lo_src="QQQ", zone_hi_src="QQQ", **common)]
    return decs, tape, meta(flat_ns=at(11, 30), entry_end_ns=at(11, 15))


def _digest(out) -> str:
    blob = json.dumps({k: out[k] for k in ("pairs", "fills", "paths")}, sort_keys=True,
                      default=lambda o: o.item() if hasattr(o, "item") else repr(o))
    return hashlib.sha256(blob.encode()).hexdigest()


def _labels(p) -> str:
    decs, tape, m = _session()
    return _digest(lb.label_session(decs, tape, None, m, p))


def test_the_committed_settings_give_the_labels_of_the_code_before_the_setting():
    assert S["stop_floor_vol"] is None                  # committed: the fixed floor (until step 6 after 13c)
    p = lp.params(S, F)
    assert p.stop_floor_vol is None and p.raw["stop_floor_vol"] is None
    assert _labels(p) == DIGEST_DEFAULT
    assert _labels(lp.params({**S, "stop_buffer": 1}, F)) == DIGEST_FIXED_B1
    # the values the labels key hashes are the ones the old code hashed (it put `stop_floor_vol: None` in itself), so
    # the key moves only with the labeler's code
    old_keys = [k for k in lp.SETTING_KEYS if k != "stop_floor_vol"]
    old_raw = {**{k: S[k] for k in old_keys}, "stop_merge_ticks": F["stop_merge_ticks"],
               "entry_order_types": F["tune_grids_13"]["execution"]["entry_order_type"], "stop_floor_vol": None}
    assert json.dumps(p.raw, sort_keys=True, default=str) == json.dumps(old_raw, sort_keys=True, default=str)


def test_the_vol_floor_setting_gives_the_labels_13cs_override_gave():
    s_vol = {**S, "stop_buffer": 1, "stop_floor_vol": VOL}
    by_setting = lp.params(s_vol, F)
    by_override = lp.params(S, F, {"stop_buffer": 1, "stop_floor_vol": VOL})     # 13c's row-13 point
    assert by_setting.stop_floor_vol == by_override.stop_floor_vol == VOL
    assert _labels(by_setting) == _labels(by_override) == DIGEST_VOL_B1
    assert DIGEST_VOL_B1 != DIGEST_FIXED_B1             # the floor changes this session's stops
    assert lp.label_key("K", by_setting) == lp.label_key("K", by_override)
    assert lp.label_key("K", by_setting) != lp.label_key("K", lp.params({**S, "stop_buffer": 1}, F))


def test_a_fixed_override_on_a_vol_setting_gives_the_fixed_labels():
    s_vol = {**S, "stop_floor_vol": VOL}
    assert _labels(lp.params(s_vol, F, {"stop_buffer": 1, "stop_floor_vol": None})) == DIGEST_FIXED_B1


def test_the_floor_setting_is_checked_and_outside_the_build_key():
    from nqbot.checks import feature_build as fb

    assert "stop_floor_vol" in lp.SETTING_KEYS and "stop_floor_vol" in fb.NON_BUILD_SETTING_KEYS
    for bad in ({"window_min": 15}, {"window_min": 0, "QQQ": {"min": 1.0, "k": 0.5}},
                {"window_min": 15, "QQQ": {"min": 1.0}}, "vol"):
        with pytest.raises(ValueError):
            lp.params({**S, "stop_floor_vol": bad}, F)
    with pytest.raises(KeyError):
        lp.params({k: v for k, v in S.items() if k != "stop_floor_vol"}, F)   # the configuration must say it


def test_13cs_fixed_point_forces_the_fixed_floor_once_vol_is_committed(tmp_path, monkeypatch):
    import json as _json

    from nqbot.checks import step13c as s13c
    from nqbot.tuning import program as prog

    vol = tmp_path / "volfloor.json"
    vol.write_text(_json.dumps({"override": VOL}), encoding="utf-8")
    monkeypatch.setattr(s13c, "vol_fit_file", lambda env: vol)
    r13 = next(r for r in prog.rows(F, S) if r.kind == "exec_stop")

    class E:
        features, settings = F, S

    assert s13c.stop_over(E(), r13, {}, "fixed", r13.start) == {}          # committed fixed: as 13c ran it
    assert s13c.stop_over(E(), r13, {}, "vol", r13.start) == {"stop_floor_vol": VOL}
    E.settings = {**S, "stop_floor_vol": VOL}
    assert s13c.stop_over(E(), r13, {}, "fixed", r13.start) == {"stop_floor_vol": None}
