"""Board ingest and the R in force (step 11 part 2; feature_spec F0.1, F1.1-F1.3, F1.7; owner 2026-10-31 Q-step11d
(4)-(5) for NDXP)."""

from datetime import date, time

import numpy as np
from pytest import approx

from nqbot.config import load_features, load_settings
from nqbot.features import boards as bd
from nqbot.features import session_tape as st

F = load_features()
S = load_settings()
NS = st.NS
PRE = date(2026, 3, 6)     # before the 2026-03-17 stamp change: QQQ content offset +740 ms
POST = date(2026, 3, 20)   # from it: QQQ -60 ms


def et(day, h, m, s=0.0):
    return st.et_ns(day, time(h, m)) + int(round(s * NS))


def build(name, day, as_of, spot, strikes, fut, **kw):
    """strikes: per board a {strike: value} dict; fut: [(ns, price)]."""
    s_as_of, s_k, s_v = [], [], []
    for a, tab in zip(as_of, strikes):
        for k, v in tab.items():
            s_as_of.append(a)
            s_k.append(k)
            s_v.append(v)
    ft = np.array([x[0] for x in fut], dtype=np.int64)
    fp = np.array([x[1] for x in fut], dtype=float)
    return bd.MapBoards.build(name, day, np.array(as_of, dtype=np.int64), np.array(spot, float), "1s",
                              np.array(s_as_of, dtype=np.int64), np.array(s_k, float), np.array(s_v, float), ft, fp,
                              F, S, et(day, 9, 30), et(day, 16, 0), **kw)


def test_f11_ratio_level_and_content_time():
    # QQQ strike 750, spot 749.12, F = 30,600 -> R = 40.84793..., level 30,635.95...
    a = et(POST, 10, 0)
    fut = [(a - 2 * NS, 30_600.0), (a - 60_000_000 + 1_000_000, 30_700.0)]   # the second trade is 1 ms after c
    mb = build("QQQ", POST, [a], [749.12], [{750.0: 1.0}], fut)
    assert len(mb) == 1
    assert mb.r[0] == approx(30_600 / 749.12) and mb.r[0] == approx(40.84793, abs=1e-5)
    assert 750 * mb.r[0] == approx(30_635.95, abs=0.01)


def test_f11_pre_stamp_change_board_reads_f_at_740ms_and_applies_after_latency():
    a = et(PRE, 10, 0)
    fut = [(a + 700_000_000, 30_600.0), (a + 741_000_000, 30_700.0)]
    mb = build("QQQ", PRE, [a], [750.0], [{750.0: 1.0}], fut)
    assert mb.r_board[0] == approx(30_600 / 750.0)
    assert mb.t_apply[0] == a + NS + int(round(S["map_latency_sec"] * NS))
    assert mb.tau[0] == a + NS


def test_f13_opening_skip_ndxp_not_qqq():
    a = et(POST, 9, 30, 30)
    fut = [(a - NS, 30_000.0)]
    q = build("QQQ", POST, [a], [750.0], [{750.0: 1.0}], fut)
    assert len(q) == 1                                         # QQQ at 09:30:30 is applied
    n = build("NDXP", POST, [a], [30_000.0], [{26_000.0: 1.0}], fut, qqq=q, k=41.0)
    assert len(n) == 0                                         # NDXP before 09:32:00 is not
    pre = build("QQQ", POST, [et(POST, 9, 29, 59)], [750.0], [{750.0: 1.0}], fut)
    assert len(pre) == 0                                       # a board stamped before 09:30 is never applied (F2.1)


def test_f17_trailing_median_wiring():
    # boards whose content times are 0, 30, 61, 62, 74 s (post era: c = asOf - 60 ms with asOf on whole seconds
    # + 60 ms) and ratios 41.0, 41.2, 41.1, 50.0, 41.3 -> R in force 41.0, 41.2, 41.1, 45.55, 41.3 (reset after > 10 s)
    base = et(POST, 10, 0)
    secs = [0, 30, 61, 62, 74]
    ratios = [41.0, 41.2, 41.1, 50.0, 41.3]
    as_of = [base + s * NS + 60_000_000 for s in secs]
    fut = [(base + s * NS - 100_000_000, 30_000.0) for s in secs]
    spot = [30_000.0 / r for r in ratios]
    mb = build("QQQ", POST, as_of, spot, [{750.0: 1.0}] * 5, fut)
    assert mb.r.tolist() == approx([41.0, 41.2, 41.1, 45.55, 41.3])


def test_strikes_join_sorted_and_missing_board_strikes():
    a = et(POST, 10, 0)
    fut = [(a - NS, 30_000.0)]
    mb = build("QQQ", POST, [a, a + NS], [750.0, 750.0], [{752.0: 2.0, 748.0: 1.0}, {750.0: 3.0}], fut)
    k0, v0 = mb.strikes(0)
    assert k0.tolist() == [748.0, 752.0] and v0.tolist() == [1.0, 2.0]
    assert mb.strikes(1)[0].tolist() == [750.0]
    assert mb.s_board.tolist() == [0, 0, 1]
    assert mb.index_at(a) == -1 and mb.index_at(int(mb.t_apply[1])) == 1


def test_ndxp_r_and_spot_from_qqq_at_t_apply():
    # Q-step11d (5): R_NDXP = the QQQ R in force at the NDXP board's t_apply / k; (4) spot = QQQ spot x k
    k = 41.5
    a = et(POST, 10, 0)
    fut = [(a - NS, 30_000.0)]
    q = build("QQQ", POST, [a, a + NS], [750.0, 751.0], [{750.0: 1.0}] * 2, fut)
    n = build("NDXP", POST, [a + NS + 300_000_000], [30_010.0], [{31_000.0: 1.0}], fut, qqq=q, k=k)
    assert len(n) == 1
    j = q.index_at(int(n.t_apply[0]))
    assert j == 1
    assert n.r[0] == approx(q.r[1] / k) and n.spot[0] == approx(751.0 * k)
    assert np.isnan(n.r_board[0])


def test_ndxp_not_applied_when_qqq_board_is_stale():
    a = et(POST, 10, 0)
    fut = [(a - NS, 30_000.0)]
    q = build("QQQ", POST, [a], [750.0], [{750.0: 1.0}], fut)
    stale = min(S["skylit_map_stale_sec"], F["mapping"]["ratio_gap_reset_sec"])
    n = build("NDXP", POST, [a + int((stale + 1) * NS)], [30_000.0], [{31_000.0: 1.0}], fut, qqq=q, k=41.0)
    assert len(n) == 0
    assert len(build("NDXP", POST, [a], [30_000.0], [{31_000.0: 1.0}], fut, qqq=q, k=None)) == 0


def test_spx_outlier_rejection_count():
    base = et(POST, 10, 0)
    secs = list(range(8))
    ratios = [1.0010] * 5 + [1.0060, 1.0010, 1.0010]          # one board 50 bps off -> rejected, R carried
    as_of = [base + s * NS - 80_000_000 + NS for s in secs]   # SPX offset -80 ms: c = whole second
    fut = [(base + s * NS, 7000.0) for s in secs]
    spot = [7000.0 / r for r in ratios]
    mb = build("SPX", POST, as_of, spot, [{7000.0: 1.0}] * 8, fut)
    assert mb.rejected == 1
    assert mb.r[5] == approx(1.0010)


def test_stored_closes_and_k(tmp_path):
    from nqbot.clients import ndx_sources as nx
    folder = tmp_path / "ndx" / "2026-10-20"
    folder.mkdir(parents=True)
    rows = "observation_date,NASDAQ100\n2026-03-18,24000\n2026-03-19,24100\n"
    (folder / nx.FILES["fred"]).write_text(rows, encoding="utf-8")
    (folder / nx.FILES["qqq"]).write_text('{"data": {"tradesTable": {"rows": ['
                                          '{"date": "03/18/2026", "close": "$600.00"},'
                                          '{"date": "03/19/2026", "close": "$602.50"}]}}}', encoding="utf-8")
    (folder / nx.FILES["div"]).write_text('{"data": {"dividends": {"rows": []}}}', encoding="utf-8")
    (tmp_path / "ndx" / "not-a-date").mkdir()
    closes = bd.stored_closes(tmp_path)
    assert closes is not None and closes["folder"].endswith("2026-10-20")
    f = {**F, "mapping": {**F["mapping"], "ndxp_k_sessions": 2}}
    assert bd.ndxp_k(date(2026, 3, 20), closes, f) == approx((24000 / 600 + 24100 / 602.5) / 2)
    assert bd.ndxp_k(date(2026, 3, 19), closes, f) is None     # only one session before it
    assert bd.stored_closes(tmp_path / "nothing") is None
