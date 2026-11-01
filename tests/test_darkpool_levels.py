"""Dark-pool levels (feature_spec F4; built, out of the first model: `dp_in_model` false, owner 2026-10-06)."""

from datetime import date, datetime, time, timedelta

import numpy as np
from pytest import approx

from nqbot.config import load_features
from nqbot.features import darkpool_levels as dpl
from nqbot.features import level_state as ls
from nqbot.features import session_tape as st
from nqbot.features.darkpool import ET
from tests.test_map_state import mk

F = load_features()
DAY = date(2026, 3, 20)
NS = st.NS
T0 = st.et_ns(DAY, time(10, 0))


def is_td(d):
    return d.weekday() < 5


def pr(day, hh, mm, ss, price, notional):
    return {"ticker": "QQQ", "timestamp": datetime.combine(day, time(hh, mm, ss), tzinfo=ET), "price": price,
            "size": 1, "notional": notional, "pct_avg_vol": 0.5}


def test_f4_dp_in_model_stays_false():
    assert F["dp_in_model"] is False


def test_f4_levels_top_list_band_and_chart_cross():
    prev = date(2026, 3, 19)
    prints = [pr(prev, 15, 0, 0, 100.0, 9e9), pr(prev, 16, 0, 1, 200.0, 99e9),    # 16:00:01: after the cutoff
              pr(DAY - timedelta(days=46), 12, 0, 0, 300.0, 99e9),                   # 46 days old: out
              pr(prev, 11, 0, 0, 150.0, 5e9)]
    mb = mk([(0, 100, {100.0: 1.0}), (30, 100, {100.0: 1.0})], r=2.0)
    trades = [(-120, 188.0), (0, 188.0), (60, 201.2), (100, 201.5), (125, 201.4)]
    tape = st.Tape.from_arrays(DAY, [T0 + int(s * NS) for s, _ in trades], [p for _, p in trades])
    lv = dpl.build(prints, "QQQ", DAY, mb, tape, F, st.bars(tape, 1), st.bars(tape, 5), is_td,
                   end_ns=T0 + 600 * NS)
    assert [x.price for x in lv] == [100.0, 150.0]
    a = lv[0].at(T0 + 10 * NS)
    assert a["band"] == (approx((100 - F["dp_band"]) * 2.0), approx((100 + F["dp_band"]) * 2.0))
    assert a["dp_notional_rank"] == 1 and a["dp_age_days"] == 1 and a["dp_pct_avg_vol"] == 0.5
    assert lv[0].state.kind == ls.CHART
    # crossed only on a settled 1-minute candle: the 10:01 bar opens 201.2 and closes 201.5, both above 201
    assert not lv[0].at(T0 + 119 * NS)["crossed"] and lv[0].at(T0 + 120 * NS)["crossed"]
    assert np.all(lv[0].state.band.times == mb.t_apply)
