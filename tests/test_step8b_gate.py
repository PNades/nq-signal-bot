"""Step-8b gate conditions (owner 2026-10-08) and `recorded-day`: hand-built boards, DBN trades and closes on a
temporary SSD; no network."""

import json
from datetime import date, datetime, time, timedelta, timezone

import numpy as np
import pyarrow as pa
import pytest

from nqbot.checks import step8b, step8b_gate, step8b_recorded
from nqbot.clients.databento_client import TRADES
from nqbot.clients.skylit_store import write_parquet
from nqbot.features import mapping as mp
from nqbot.features import ndxp
from tests.databento_fakes import INSTRUMENT, dbn_bytes, trade
from tests.test_step8b import DAY, ET, NS, _setup, env  # noqa: F401 (env is a fixture)


# --- F0.1, F1.1, F1.7 -----------------------------------------------------------------------------------

def test_effective_and_content_times():
    a = np.array([int(datetime(2026, 3, 5, 10, tzinfo=ET).timestamp() * 1000 + 999) * 1_000_000])  # 10:00:00.999
    assert mp.effective_tau_ns(a, "1s")[0] == mp.floor_second_ns(a)[0] + NS  # τ = floor(asOf) + 1 s
    assert mp.effective_tau_ns(a, "1m")[0] == mp.floor_second_ns(a)[0] + 60 * NS
    assert mp.content_time_ns(a, 740)[0] - mp.floor_second_ns(a)[0] == 740_000_000  # F1.1 test: 10:00:00.740
    stamp = date(2026, 3, 17)
    assert mp.board_era(date(2026, 3, 16), stamp) == mp.ERA_BEFORE
    assert mp.board_era(date(2026, 3, 17), stamp) == mp.ERA_FROM
    assert mp.board_era(date(2026, 3, 16), stamp, live=True) == mp.ERA_LIVE


def test_trailing_median_f17_example():
    # F1.7 test: content times 0, 30, 61, 62, 74 s, ratios 41.0, 41.2, 41.1, 50.0, 41.3
    t = np.array([0, 30, 61, 62, 74], dtype=np.int64) * NS
    r = np.array([41.0, 41.2, 41.1, 50.0, 41.3])
    assert mp.gap_segments(t, 10).tolist() == [0, 1, 2, 2, 3]  # every gap > 10 s resets
    assert np.allclose(mp.ratio_in_force(t, r, 60, 10, 3, 3), [41.0, 41.2, 41.1, 45.55, 41.3])
    assert np.allclose(mp.ratio_in_force(t, r, 60, 60, 3, 3), [41.0, 41.1, 41.15, 41.2, 41.25])  # reset at 60 s


def test_per_board_ratio_uses_the_outlier_rule():
    t = np.arange(7, dtype=np.int64) * NS
    r = np.array([40.80] * 5 + [40.95, 40.80])  # 36.8 bps: rejected, the previous R stays in force
    assert np.allclose(mp.ratio_in_force(t, r, 0, 10, 3, 3), [40.80] * 7)


# --- (a), (b), alignment on a hand-built day -------------------------------------------------------------

def test_bias_pass_rule():
    assert step8b_gate.bias_pass(-0.1, 0.3)  # contains 0
    assert step8b_gate.bias_pass(0.05, 0.25)  # within one tick
    assert not step8b_gate.bias_pass(0.05, 0.30)
    assert not step8b_gate.bias_pass(None, None)
    med, lo, hi = step8b_gate.bootstrap_median([np.array([1.0, 2.0]), np.array([3.0])], resamples=200)
    assert med == 2.0 and 1.0 <= lo <= hi <= 3.0


def _whole_second_content(env):
    """The hand-built boards hold the futures trade of their own whole second (offset 0..999 ms)."""
    env.features["mapping"]["board_content_offset_ms"]["before_stamp_change"].update(QQQ=0, SPY=0, SPX=0)


def test_gate_checks_pass_on_a_clean_day(env):
    _setup(env, noise=1e-6)
    _whole_second_content(env)
    bias = step8b_gate.check_bias(env)
    assert set(bias["per_chain"]) == {"QQQ", "SPY", "SPX"}  # NDXP pending its method
    assert all(c["n"] > 10 for c in bias["per_chain"].values())
    assert bias["pass"], bias["per_chain"]
    est = step8b_gate.check_estimator(env)
    assert est["pass"], est["per_chain"]
    assert est["per_chain"]["QQQ"]["estimator"] == "trailing 60 s median"
    assert est["per_chain"]["SPX"]["estimator"] == "per board (F1.2)"
    cached = list((step8b.reports_dir(env.paths) / "gate_days").glob("*.npz"))
    assert len(cached) == 1  # one file per day, reused by the second check
    al = step8b_gate.check_alignment(env)
    assert al["pass"] and all(0 <= o["offset_ms"] < 1000 for o in al["per_day"][DAY.isoformat()].values())
    text = step8b.check_report(env).read_text(encoding="utf-8")
    for title in ("(a) bias", "(b) the chosen estimator", "alignment: fitted content time"):
        line = next(x for x in text.splitlines() if x.startswith(f"- {title}"))
        assert "**PASS**" in line, line
    assert "not passed yet" in text  # the Atlas tables are still missing


def test_alignment_alerts_on_content_after_the_second(env):
    _setup(env, noise=1e-6, lag_s=-2)  # spot holds the futures price 2 s after asOf: lookahead
    al = step8b_gate.check_alignment(env)
    assert not al["pass"] and al["per_chain"]["QQQ"]["alerts"] == [DAY.isoformat()]
    assert al["per_day"][DAY.isoformat()]["QQQ"]["offset_ms"] == 2000


def test_gate_refuses_a_pending_offset(env):
    _setup(env, noise=1e-6)
    env.features["mapping"]["board_content_offset_ms"]["before_stamp_change"]["SPY"] = None
    with pytest.raises(step8b.CheckRefused, match="pending"):
        step8b_gate.check_bias(env)


# --- recorded-day ---------------------------------------------------------------------------------------

REC = date(2026, 3, 5)  # Thursday; previous session 2026-03-04
RHO, K = 1.004, 40.0  # the hand-built NQ / NDX basis and NDX / QQQ ratio


def _walk(d: date, seed: int, base: float) -> tuple[np.ndarray, np.ndarray]:
    """A trade every second at hh:mm:ss.5, 09:25-16:05 ET, 0.25 ticks."""
    rng = np.random.default_rng(seed)
    t0 = int(datetime.combine(d, time(9, 25), tzinfo=ET).timestamp()) * NS + NS // 2
    ts = np.arange(t0, t0 + 400 * 60 * NS, NS, dtype=np.int64)
    return ts, base + np.round(np.cumsum(rng.normal(0, 1, len(ts))) * 4) / 4


class FakeDatabento:
    def __init__(self, end: datetime, usd: float = 0.0):
        self.end, self.usd, self.costs, self.downloads = end, usd, [], []

    def dataset_end(self):
        return self.end

    def resolve_front(self, continuous, start, end):
        return {"NQ": [(date(2025, 12, 15), date(2026, 3, 13), "NQH6")],
                "ES": [(date(2025, 12, 15), date(2026, 3, 13), "ESH6")]}, []

    def dataset_condition(self, start, end):  # every pull records its days' condition (owner 2026-10-09, §3.2)
        self.conditions = (start, end)
        n = (end - start).days + 1
        return [{"date": (start + timedelta(days=i)).isoformat(), "condition": "available",
                 "last_modified_date": None} for i in range(n)]

    def cost(self, schema, symbols, start, end):
        self.costs.append((schema, symbols[0], start))
        return self.usd

    def download(self, schema, symbol, start, end, path, **context):
        d = date.fromisoformat(context["day"])
        ts, px = _walk(d, 1 if symbol.startswith("NQ") else 2, 24_000.0 if symbol.startswith("NQ") else 6_000.0)
        recs = [trade(INSTRUMENT[symbol], int(t), float(p), seq=i) for i, (t, p) in enumerate(zip(ts, px))]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(dbn_bytes(TRADES, symbol, int(start.timestamp() * NS), int(end.timestamp() * NS), recs))
        self.downloads.append((symbol, d))
        return path.stat().st_size


def _recording(env, qqq_arrival_delay_s=None, without=()):
    """1-second boards 09:30-10:30 whose spots hold the futures price 60 ms before asOf (the live content time).
    With qqq_arrival_delay_s, an arrival column: QQQ boards arrive that late, the others 0.4 s after asOf.
    without: maps left out of the recording (the real recordings carry no SPX)."""
    nq_ts, nq = _walk(REC, 1, 24_000.0)
    es_ts, es = _walk(REC, 2, 6_000.0)
    as_of = np.arange(int(datetime.combine(REC, time(9, 30), tzinfo=ET).timestamp()) * NS,
                      int(datetime.combine(REC, time(10, 30), tzinfo=ET).timestamp()) * NS, NS, dtype=np.int64)
    f_nq = mp.last_price_at_or_before(nq_ts, nq, as_of - 60_000_000)
    f_es = mp.last_price_at_or_before(es_ts, es, as_of - 60_000_000)
    spots = {"NDXP": f_nq / RHO, "QQQ": f_nq / (RHO * K), "SPY": f_es / 10.0, "SPX": f_es / 1.001}
    spots = {m: sp for m, sp in spots.items() if m not in without}
    rows = {"as_of": [], "symbol": [], "spot": []}
    for m, sp in spots.items():
        rows["as_of"] += [int(t // 1_000_000) for t in as_of]
        rows["symbol"] += [m] * len(as_of)
        rows["spot"] += sp.tolist()
    t = pa.table({"as_of": pa.array(rows["as_of"], pa.timestamp("ms", "UTC")), "symbol": rows["symbol"],
                  "spot": rows["spot"]})
    if qqq_arrival_delay_s is not None:
        delay = np.where(np.array(rows["symbol"]) == "QQQ", int(qqq_arrival_delay_s * NS), 400_000_000)
        arrival = np.array(rows["as_of"], dtype=np.int64) * 1_000_000 + delay
        t = t.append_column("arrival", pa.array(arrival, pa.timestamp("ns", "UTC")))
    folder = env.sky.stream_dir(REC) / "session_092700"
    write_parquet(folder / "part_000_boards.parquet", t, {})
    write_parquet(folder / "part_001_boards.parquet", t.slice(0, 100), {})  # a resume re-sends boards: deduped
    # NDXP strikes every 10 points around spot: the middle one the King, the outer two below 25% of King
    s_as, s_k, s_v = [], [], []
    for a, sp in zip(as_of, spots["NDXP"]):
        mid = round(sp / 10) * 10
        for j, v in zip((-2, -1, 0, 1, 2), (1e5, 6e5, -1e6, 4e5, 2e5)):
            s_as.append(int(a // 1_000_000))
            s_k.append(mid + j * 10.0)
            s_v.append(v)
    st = pa.table({"as_of": pa.array(s_as, pa.timestamp("ms", "UTC")), "symbol": ["NDXP"] * len(s_as),
                   "strike": pa.array(s_k, pa.float32()), "value": pa.array(s_v, pa.float32())})
    write_parquet(folder / "part_000_strikes.parquet", st, {})
    write_parquet(folder / "part_001_strikes.parquet", st.slice(0, 500), {})  # re-sent with its boards: deduped


def _fetch(url: str) -> bytes:
    sessions = [REC - timedelta(days=i) for i in range(1, 40) if (REC - timedelta(days=i)).weekday() < 5]
    if "fred" in url:
        return ("observation_date,NASDAQ100\n" + "".join(f"{s},{600.0 * K:.3f}\n" for s in sessions)).encode()
    if "historical" in url:
        rows = [{"date": s.strftime("%m/%d/%Y"), "close": "$600.00"} for s in sessions]
        return json.dumps({"data": {"tradesTable": {"rows": rows}}}).encode()
    return json.dumps({"data": {"dividends": {"rows": []}}}).encode()


def test_recorded_day_scores_both_ndxp_methods(env):
    _recording(env)
    fake = FakeDatabento(datetime(2026, 3, 6, tzinfo=timezone.utc))
    rep = step8b_recorded.check_recorded_day(env, REC, with_prev=True, client_factory=lambda: fake, fetch=_fetch)
    assert [c[1] for c in fake.costs] == ["NQH6", "ESH6", "NQH6"]  # priced first, $0
    assert fake.downloads == [("NQH6", REC), ("ESH6", REC), ("NQH6", date(2026, 3, 4))]
    al = rep["alignment"]
    assert all(al[m]["pass"] for m in ("QQQ", "SPY", "SPX", "NDXP")) and rep["alerts"] == []
    assert rep["spy_live_offset_ms"] is not None and rep["spy_live_offset_ms"] <= 1000
    methods = rep["ndxp"]["methods"]
    for n in step8b_recorded.QQQ_K_SESSIONS:
        q = methods[f"QQQ x k (N={n})"]
        assert q["k"] == pytest.approx(K) and q["abs_p95"] == pytest.approx(0, abs=1e-6)  # exact k: no error
    assert methods["basis+carry"]["n"] > 3000 and np.isfinite(methods["basis+carry"]["abs_p95"])
    saved = step8b_recorded._load_errors(step8b.reports_dir(env.paths) / "recorded_days" / f"{REC}.npz")
    assert set(saved) == set(step8b_recorded.candidates()) and len(saved["basis+carry"]) == methods["basis+carry"]["n"]
    # report only (owner 2026-10-11): the same methods at relevant-node crossings, on the NDXP board in force
    nx = rep["ndxp"]["crossings"]["n"]
    assert nx > 20 and rep["ndxp"]["crossings"]["report_only"]
    for c in step8b_recorded.candidates():
        assert methods[c]["crossings"]["n"] == nx and np.isfinite(methods[c]["crossings"]["abs_p95"])
    xs = step8b_recorded._load_errors(step8b.reports_dir(env.paths) / "recorded_days" / f"{REC}.npz", crossings=True)
    assert set(xs) == set(step8b_recorded.candidates()) and len(xs["basis+carry"]) == nx
    # a second run pulls nothing again (cached) and fetches the closes once a day
    fake2 = FakeDatabento(datetime(2026, 3, 6, tzinfo=timezone.utc))
    step8b_recorded.check_recorded_day(env, REC, with_prev=True, client_factory=lambda: fake2,
                                       fetch=lambda url: pytest.fail("fetched twice"))
    assert fake2.costs == [] and fake2.downloads == []


def test_recorded_day_waits_and_refuses(env):
    _recording(env)
    early = FakeDatabento(datetime(2026, 3, 5, 20, tzinfo=timezone.utc))  # the CME day ends 17:00 ET = 22:00 UTC
    with pytest.raises(step8b.CheckRefused, match="not published"):
        step8b_recorded.check_recorded_day(env, REC, client_factory=lambda: early, fetch=_fetch)
    priced = FakeDatabento(datetime(2026, 3, 6, tzinfo=timezone.utc), usd=1.5)
    with pytest.raises(step8b.CheckRefused, match=r"\$0 only"):
        step8b_recorded.check_recorded_day(env, REC, client_factory=lambda: priced, fetch=_fetch)
    assert priced.downloads == []
    with pytest.raises(step8b.CheckRefused, match="no stream recording"):
        step8b_recorded.check_recorded_day(env, date(2026, 3, 6), client_factory=lambda: early, fetch=_fetch)


def test_recorded_day_without_the_previous_session(env):
    _recording(env)
    fake = FakeDatabento(datetime(2026, 3, 6, tzinfo=timezone.utc))
    rep = step8b_recorded.check_recorded_day(env, REC, client_factory=lambda: fake, fetch=_fetch)
    assert "--with-prev" in rep["ndxp"]["methods"]["basis+carry"]["skipped"]
    assert rep["databento"]["dataset_condition"] == {"flagged": [], "upgrades": []}
    assert fake.conditions[1] == REC
    with pytest.raises(step8b.CheckRefused, match="needs 3"):
        step8b_recorded.check_ndxp_select(env)


def test_contract_expiry_and_k():
    assert ndxp.contract_expiry("NQZ6") == datetime(2026, 12, 18, 9, 30, tzinfo=ET)
    assert ndxp.contract_expiry("NQH6") == datetime(2026, 3, 20, 9, 30, tzinfo=ET)
    d1, d2, day = date(2026, 3, 19), date(2026, 3, 20), date(2026, 3, 23)
    k = ndxp.qqq_k(day, {d1: 24_000.0, d2: 24_000.0}, {d1: 600.0, d2: 601.0}, {date(2026, 3, 23): 1.0}, 2)
    assert k == pytest.approx((24_000 / 599.0 + 24_000 / 600.0) / 2)  # both closes moved to the ex basis
    assert ndxp.qqq_k(day, {d1: 1.0}, {d1: 1.0}, {}, 2) is None


def test_ndxp_select_leave_one_day_out(env):
    """Three recorded days: the basis method is good on two days and bad on one, QQQ x k (N=10) steady."""
    rng = np.random.default_rng(0)
    errs = {"2026-10-09": {"basis+carry": 2.0, "QQQ x k (N=5)": 6.0, "QQQ x k (N=10)": 3.0, "QQQ x k (N=20)": 5.0},
            "2026-10-12": {"basis+carry": 2.0, "QQQ x k (N=5)": 6.0, "QQQ x k (N=10)": 3.0, "QQQ x k (N=20)": 5.0},
            "2026-10-13": {"basis+carry": 20.0, "QQQ x k (N=5)": 6.0, "QQQ x k (N=10)": 3.0, "QQQ x k (N=20)": 5.0}}
    for day, scale in errs.items():
        step8b_recorded._save_errors(env, date.fromisoformat(day),
                                     {c: rng.uniform(-s, s, 1000) for c, s in scale.items()})
    rep = step8b_recorded.check_ndxp_select(env)
    picks = {r["held_out"]: r["picked"] for r in rep["rotations"]}
    assert picks["2026-10-13"] == "basis+carry"  # picked on the two good days, then fails on the held-out one
    assert rep["rotations"][2]["held_out_p95"] > 15
    assert picks["2026-10-09"] == picks["2026-10-12"] == "QQQ x k (N=10)"
    assert rep["winner"] == "QQQ x k (N=10)"
    assert rep["every_board_held_out_p95"] == rep["candidates"]["QQQ x k (N=10)"]["held_out_p95"] == pytest.approx(
        2.85, abs=0.1)
    # no crossing errors saved: no proposal until recorded-day is re-run for those days
    assert rep["proposed_stop_floor_nq"] is None and len(rep["days_without_crossings"]) == 3
    # owner 2026-10-11: the proposal is the larger of the every-board held-out p95 and the crossing p95
    for day, scale in errs.items():
        d = date.fromisoformat(day)
        step8b_recorded._save_errors(env, d, {c: rng.uniform(-s, s, 1000) for c, s in scale.items()},
                                     {c: rng.uniform(-2 * s, 2 * s, 200) for c, s in scale.items()})
    rep = step8b_recorded.check_ndxp_select(env)
    w = rep["candidates"][rep["winner"]]
    assert rep["crossing_p95"] == w["crossing_p95"] > rep["every_board_held_out_p95"]
    assert rep["proposed_stop_floor_nq"] == w["crossing_p95"] and w["crossings_n"] == 600
    step8b_recorded._save_errors(env, date(2026, 10, 14), {"QQQ x k (N=5)": np.zeros(3)})
    with pytest.raises(step8b.CheckRefused, match="not scored on every day"):
        step8b_recorded.check_ndxp_select(env)


def test_gate_report_flags_a_precision_report_from_before_q8b4(env):
    step8b.write_report(env, "precision", {"days": [DAY.isoformat()], "per_chain": {}, "pass": False,
                                           "stop_buffer_floor": 4.6})
    text = step8b.check_report(env).read_text(encoding="utf-8")
    assert "Precision: **out of date**" in text


def test_crossings_use_the_board_applied_by_then(env):
    """map_latency_sec delays every board (F0.1): the R at a crossing comes from the last board applied by t*."""
    _setup(env, noise=1e-6)
    _whole_second_content(env)
    front = step8b.front_symbols(env)
    maps, futs = step8b._day_inputs(env, DAY, front)
    md, fut = maps["SPY"], futs["ES"]
    env.settings["map_latency_sec"] = 0.0
    fast = step8b_gate.chain_crossings(env, DAY, md, fut)
    env.settings["map_latency_sec"] = 30.0  # a 30-s delay: every crossing uses a map at least ~30 s old
    slow = step8b_gate.chain_crossings(env, DAY, md, fut)
    assert len(fast["chosen"]) > 10 and len(slow["chosen"]) > 10
    assert len(fast["chosen"]) != len(slow["chosen"]) or not np.allclose(fast["chosen"], slow["chosen"])
    open_s = int(datetime.combine(DAY, time(9, 30), tzinfo=ET).timestamp())
    assert slow["t"].min() >= (open_s + 31) * NS  # nothing before the first board's apply time (09:30:01 + 30 s)


def test_apply_times_and_board_in_force():
    as_of = np.array([0, 1, 2, 3], dtype=np.int64) * NS + 250_000_000  # asOf with milliseconds
    t = mp.apply_times_ns(as_of, "1s", 0.59)
    assert t.tolist() == [int(1.59 * NS), int(2.59 * NS), int(3.59 * NS), int(4.59 * NS)]  # floor(asOf) + 1 s + 0.59 s
    late = mp.apply_times_ns(as_of, "1s", 0.59, np.array([0, 9 * NS, 0, 0]))  # board 1 arrives at 9 s
    assert late.tolist() == [int(1.59 * NS), 9 * NS, 9 * NS, 9 * NS]  # board 2 is not in force before board 1
    assert mp.in_force_index(late, np.array([NS, 2 * NS, 9 * NS])).tolist() == [-1, 0, 3]


def test_recorded_day_uses_the_qqq_board_in_force(env):
    """QQQ x k takes the QQQ R the bot had at each NDXP board: the last QQQ board applied by then (owner 2026-10-09,
    the same rule as the gate). QQQ boards arriving 200 s late leave the first NDXP boards without a QQQ R."""
    _recording(env, qqq_arrival_delay_s=200)
    fake = FakeDatabento(datetime(2026, 3, 6, tzinfo=timezone.utc))
    rep = step8b_recorded.check_recorded_day(env, REC, with_prev=True, client_factory=lambda: fake, fetch=_fetch)
    m = rep["ndxp"]["methods"]
    # NDXP boards start at 09:32:00 (open skip); the first QQQ board applies at 09:30:00 + 200 s = 09:33:20
    assert m["basis+carry"]["n"] - m["QQQ x k (N=5)"]["n"] == pytest.approx(80, abs=2)
    assert m["QQQ x k (N=5)"]["abs_p95"] == pytest.approx(0, abs=1e-6)


def test_recorded_day_leaves_out_boards_near_an_uncovered_gap(env):
    """Owner 2026-10-10: a resume with gapCovered=False leaves out the boards from 2 s before it to 60 s after it
    (QQQ's trailing window); a covered resume leaves nothing out."""
    _recording(env)
    at = int(datetime.combine(REC, time(10, 0), tzinfo=ET).timestamp()) * NS
    events = [{"kind": "resumed", "arrival_ns": at, "data": {"gapCovered": False, "symbols": [
                  {"symbol": "NDXP", "status": "advanced"}, {"symbol": "QQQ", "status": "unchanged"}]}},
              {"kind": "resumed", "arrival_ns": at + 3600 * NS, "data": {"gapCovered": True, "symbols": []}}]
    path = env.sky.stream_dir(REC) / "session_092700" / "part_000_events.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    fake = FakeDatabento(datetime(2026, 3, 6, tzinfo=timezone.utc))
    rep = step8b_recorded.check_recorded_day(env, REC, with_prev=True, client_factory=lambda: fake, fetch=_fetch)
    gaps = rep["ndxp"]["uncovered_gaps"]
    assert gaps["resumes"] == [{"at_et": "10:00:00", "advanced": ["NDXP"]}] and gaps["window_s"] == [-2, 60]
    # by content time (fitted NDXP offset -500 ms here): boards with asOf 09:59:59 ... 10:01:00
    assert gaps["boards_left_out"] == 62
    assert rep["ndxp"]["methods"]["QQQ x k (N=5)"]["n"] == rep["ndxp"]["methods"]["basis+carry"]["n"]


def test_recorded_day_never_carries_a_board_across_a_gap(env):
    """Owner 2026-10-14 (live stream drops): NDXP is scored only where its boards arrived. A 20-s hole in the QQQ boards
    leaves the NDXP boards in it without a QQQ R (QQQ x k not scored there), and a 20-s hole in the NDXP boards
    creates no crossing inside it (no NDXP board is carried across it)."""
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    _recording(env)
    fake = FakeDatabento(datetime(2026, 3, 6, tzinfo=timezone.utc))
    full = step8b_recorded.check_recorded_day(env, REC, with_prev=True, client_factory=lambda: fake, fetch=_fetch)
    folder = env.sky.stream_dir(REC) / "session_092700"
    t = pq.read_table(folder / "part_000_boards.parquet")
    a = int(datetime.combine(REC, time(10, 0), tzinfo=ET).timestamp()) * 1000
    hole = pc.and_(pc.greater_equal(pc.cast(t["as_of"], pa.int64()), a),
                   pc.less(pc.cast(t["as_of"], pa.int64()), a + 20_000))
    for m in ("QQQ", "NDXP"):
        drop = pc.and_(hole, pc.equal(t["symbol"], m))
        write_parquet(folder / "part_000_boards.parquet", t.filter(pc.invert(drop)), {})
        rep = step8b_recorded.check_recorded_day(env, REC, with_prev=True, client_factory=lambda: fake, fetch=_fetch)
        m_ = rep["ndxp"]["methods"]
        assert rep["ndxp"]["boards"]["no_carry_s"] == 5.0
        if m == "QQQ":  # NDXP boards whose QQQ board is more than 5 s old: about 15 of the 20
            assert m_["basis+carry"]["n"] - m_["QQQ x k (N=5)"]["n"] == pytest.approx(15, abs=2)
            assert rep["ndxp"]["boards"]["scored"]["QQQ x k (N=5)"] == m_["QQQ x k (N=5)"]["n"]
        else:
            assert rep["ndxp"]["boards"]["recorded"] == full["ndxp"]["boards"]["recorded"] - 20
            assert rep["ndxp"]["crossings"]["n"] < full["ndxp"]["crossings"]["n"]
        write_parquet(folder / "part_000_boards.parquet", t, {})


def test_one_chain_at_a_time_builds_the_same_maps(env):
    """Low memory (§11, owner 2026-10-16): reading one map's rows at a time gives the same MapDay as the whole day."""
    _setup(env, noise=1e-6)
    front = step8b.front_symbols(env)
    maps, futs = step8b._day_inputs(env, DAY, front)
    names = []
    for m, md in step8b.iter_day_maps(env, DAY, list(maps), step8b.day_futures(env, DAY, front)):
        names.append(m)
        ref = maps[m]
        for f in ("as_of", "spot", "applied", "s_board", "s_strike", "s_value", "s_pct"):
            assert np.array_equal(getattr(md, f), getattr(ref, f), equal_nan=f in ("spot", "s_pct")), (m, f)
        assert np.array_equal(md.r, ref.r, equal_nan=True) and md.resolution == ref.resolution
    assert names == list(maps)


def test_recorded_day_leaves_out_a_connection_stretch_and_lists_every_reconnect(env):
    """Owner 2026-10-18 (10-16, the owner's Wi-Fi): when no map's board arrives for more than skylit_map_stale_sec,
    the boards from 2 s before that stretch to 60 s after it are left out like an uncovered gap; a single map's drop
    is not a stretch. Every reconnect is listed, the failed connection attempts from the session log too."""
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    _recording(env, qqq_arrival_delay_s=0.4)
    folder = env.sky.stream_dir(REC) / "session_092700"
    t = pq.read_table(folder / "part_000_boards.parquet")
    a = int(datetime.combine(REC, time(10, 0), tzinfo=ET).timestamp()) * 1000
    ms = pc.cast(t["as_of"], pa.int64())
    hole = pc.and_(pc.greater_equal(ms, a), pc.less(ms, a + 20_000))  # every map silent 20 s
    one = pc.and_(pc.and_(pc.greater_equal(ms, a + 600_000), pc.less(ms, a + 620_000)), pc.equal(t["symbol"], "SPY"))
    write_parquet(folder / "part_000_boards.parquet", t.filter(pc.invert(pc.or_(hole, one))), {})
    write_parquet(folder / "part_001_boards.parquet", t.slice(0, 0), {})
    events = [{"kind": "reconnect", "arrival_ns": a * 1_000_000 + 1800 * NS, "data": {"reason": "max_duration"}},
              {"kind": "resumed", "arrival_ns": a * 1_000_000 + 1801 * NS, "data": {"gapCovered": True, "symbols": []}}]
    (folder / "part_000_events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    env.paths.log_dir.mkdir(parents=True, exist_ok=True)
    (env.paths.log_dir / f"stream_session_{REC:%Y%m%d}.txt").write_text(
        "2026-03-05T15:20:00.500Z [1] WARNING nqbot: stream connection 4 failed: ConnectError\n", encoding="utf-8")
    fake = FakeDatabento(datetime(2026, 3, 6, tzinfo=timezone.utc))
    rep = step8b_recorded.check_recorded_day(env, REC, with_prev=True, client_factory=lambda: fake, fetch=_fetch)
    cs = rep["ndxp"]["connection_stretches"]
    assert [(s["from_et"], s["to_et"], s["length_s"]) for s in cs["stretches"]] == [("09:59:59", "10:00:20", 21.0)]
    assert cs["window_s"] == [-2, 60] and cs["boards_left_out"] == pytest.approx(63, abs=3)
    assert [e["kind"] for e in rep["reconnects"]] == ["failed", "reconnect", "resumed"]  # in time order
    assert rep["reconnects"][0] == {"at_et": "10:20:00", "kind": "failed", "connection": 4, "error": "ConnectError"}
    assert rep["reconnects"][1]["reason"] == "max_duration" and rep["reconnects"][2]["gap_covered"] is True


def test_a_silent_end_is_a_stretch_only_when_the_recording_ran_to_the_close(env):
    """Owner 2026-10-19 (10-19: the connection went quiet at 15:45 and never came back): a recording that ran to the
    close (its log's `session ended ... (close`) has a stretch from its last arrival to the close; one that stopped
    early ends at its last board."""
    open_ns = int(datetime.combine(REC, time(9, 30), tzinfo=ET).timestamp()) * NS
    close_ns = int(datetime.combine(REC, time(16, 0), tzinfo=ET).timestamp()) * NS
    arr = [open_ns + s * NS for s in range(0, 600)]  # boards every second to 09:39:59, then nothing
    boards = pa.table({"arrival": pa.array(arr, pa.timestamp("ns", "UTC"))})
    assert step8b_recorded.connection_stretches(boards, open_ns, close_ns, 5.0) == []
    assert step8b_recorded.connection_stretches(boards, open_ns, close_ns, 5.0, to_close=True) == [
        {"from_ns": arr[-1], "to_ns": close_ns}]
    near = pa.table({"arrival": pa.array(arr + [close_ns - 2 * NS], pa.timestamp("ns", "UTC"))})
    assert step8b_recorded.connection_stretches(near, open_ns, close_ns, 5.0, to_close=True) == [
        {"from_ns": arr[-1], "to_ns": close_ns - 2 * NS}]  # boards up to 2 s before the close: no end stretch
    env.paths.log_dir.mkdir(parents=True, exist_ok=True)
    log = env.paths.log_dir / f"stream_session_{REC:%Y%m%d}.txt"
    assert not step8b_recorded.ran_to_close(env, REC)
    log.write_text("session ended 2026-03-05 13:55:47 ET (stopped: stream reconnected 12 times: the cap is 12, "
                   "INCOMPLETE): NDXP 1\n", encoding="utf-8")
    assert not step8b_recorded.ran_to_close(env, REC)
    log.write_text("x\nsession ended 2026-03-05 16:01:40 ET (close, complete): NDXP 1\n", encoding="utf-8")
    assert step8b_recorded.ran_to_close(env, REC)


def test_recorded_day_saves_the_board_times_with_the_errors(env):
    _recording(env)
    fake = FakeDatabento(datetime(2026, 3, 6, tzinfo=timezone.utc))
    step8b_recorded.check_recorded_day(env, REC, with_prev=True, client_factory=lambda: fake, fetch=_fetch)
    path = step8b.reports_dir(env.paths) / "recorded_days" / f"{REC}.npz"
    t = step8b_recorded._load_times(path)
    e = step8b_recorded._load_errors(path)
    assert t is not None and len(t) == len(e["basis+carry"]) and np.all(np.diff(t) > 0)
    assert len(step8b_recorded._load_times(path, crossings=True)) == len(
        step8b_recorded._load_errors(path, crossings=True)["basis+carry"])


def test_gate_days_fill_the_cache_the_checks_read(env):
    """Owner 2026-10-19 (the step8b_gate job): the per-day work is cached; the checks then read the same numbers."""
    _setup(env, noise=1e-6, lag_s=-2)
    step8b_gate.check_gate_days(env)
    folder = step8b.reports_dir(env.paths) / "gate_days"
    assert len(list(folder.glob("*_align_*.json"))) == 1 and len(list(folder.glob("*.npz"))) == 1
    al = step8b_gate.check_alignment(env)
    assert al["per_day"][DAY.isoformat()]["QQQ"]["offset_ms"] == 2000 and not al["pass"]
    env.settings["map_latency_sec"] = env.settings["map_latency_sec"] + 0.5  # a new config: a new cache entry
    step8b_gate.check_gate_days(env)
    assert len(list(folder.glob("*_align_*.json"))) == 2


def test_the_gate_job_runs_each_day_in_its_own_process_then_the_reports(env, monkeypatch):
    from types import SimpleNamespace

    from nqbot.checks import step8b_disloc
    from nqbot.jobs.registry import get_job

    job = get_job("step8b_gate")
    ctx = SimpleNamespace(settings=env.settings, paths=env.paths, log=env.log)
    chunks = job.chunks(ctx)
    key = step8b_gate._cfg_key(env)  # owner 2026-10-19: a changed setting renames every chunk, so all re-run
    assert len(chunks) == 23 and chunks[-1] == f"report:{key}" and chunks[0] == f"2025-10-15:{key}"
    assert chunks[-2] == f"2026-03-31:{key}"
    ran = []
    monkeypatch.setattr(step8b_disloc, "free_mb", lambda: 8000)
    monkeypatch.setattr(step8b_disloc, "run_cli_in_child", lambda argv: ran.append(argv) or 1.2)
    assert job.run_chunk("2025-10-15", ctx) == {"peak_gb": 1.2}
    assert ran == [["step8b", "gate-day", "--dates", "2025-10-15"]]
    called = []
    for name in ("check_bias", "check_estimator", "check_alignment"):
        monkeypatch.setattr(step8b_gate, name, lambda e, days, n=name: called.append((n, len(days))))
    assert job.run_chunk("report", ctx) is None
    assert called == [("check_bias", 22), ("check_estimator", 22), ("check_alignment", 22)]
    monkeypatch.setattr(step8b_disloc, "free_mb", lambda: 100)
    from nqbot.jobs.runner import StopChunk
    with pytest.raises(StopChunk):
        job.run_chunk("2025-10-29", ctx)


def test_atlas_tables_on_a_recorded_day(env):
    """Owner 2026-10-19 (§7 rule 2b step-8b exception, §13 Q-atlas-oct): the Atlas tables on a recorded session use the
    recorded boards (applied at max(arrival, tau + latency)), the live offsets and recorded-day's trades; the minutes
    are fixed by the clock; the reports stay out of the sample days' regression check."""
    _recording(env, qqq_arrival_delay_s=0.4)
    fake = FakeDatabento(datetime(2026, 3, 6, tzinfo=timezone.utc))
    with pytest.raises(step8b.CheckRefused, match="recorded-day"):
        step8b_recorded.check_atlas_recorded(env, REC)
    step8b_recorded.check_recorded_day(env, REC, with_prev=True, client_factory=lambda: fake, fetch=_fetch)
    rep = step8b_recorded.check_atlas_recorded(env, REC)
    # the recording runs 09:30-10:30: 10 minutes from 09:40 to 10:20 (each board arrives 0.4 s after asOf)
    assert rep["minutes"][0] == "09:40" and rep["minutes"][-1] == "10:19" and len(rep["minutes"]) == 10
    assert set(rep["maps"]) == {"QQQ", "SPY"} and rep["maps"]["QQQ"][0]["board"] is not None
    assert (step8b.reports_dir(env.paths) / f"atlas_recorded_{REC}.md").exists()
    assert step8b.latest_atlas_by_date(env.paths) == {}  # never in the sample days' regression check
    # owner 2026-10-22: NDXP and SPX spot checks, 3 clock minutes, levels and times only
    ndxp_rows = rep["spot_checks"]["NDXP"]
    assert [r["minute"] for r in ndxp_rows] == ["09:40", "09:59", "10:19"]  # the last board is 10:29:59
    for r in ndxp_rows:  # R_NDXP = the QQQ R in force / k = (RHO x K) / K
        assert abs(r["r"] - RHO) < 1e-3, r
        assert [n["strike"] for n in r["nodes"]] == sorted(n["strike"] for n in r["nodes"]) and len(r["nodes"]) >= 3
        assert all(n["level"] == round(n["strike"] * r["r"], 2) and "crossings" not in n for n in r["nodes"])
    assert rep["spx_source"] == "the session recording" and len(rep["spot_checks"]["SPX"]) == 3
    text = (step8b.reports_dir(env.paths) / f"atlas_recorded_{REC}.md").read_text(encoding="utf-8")
    assert "## NDXP -> NQ (spot check)" in text and "## SPX -> ES (spot check)" in text


def test_spx_spot_check_reads_the_stored_history_windows(env):
    """The recordings carry no SPX: its spot check reads the day's stored history windows (parity / stream-drops),
    one minute per window, 10 minutes into it."""
    _recording(env, qqq_arrival_delay_s=0.4, without=("SPX",))
    fake = FakeDatabento(datetime(2026, 3, 6, tzinfo=timezone.utc))
    step8b_recorded.check_recorded_day(env, REC, with_prev=True, client_factory=lambda: fake, fetch=_fetch)
    rep = step8b_recorded.check_atlas_recorded(env, REC)
    assert rep["spot_checks"]["SPX"] == [] and rep["spx_source"].startswith("none")
    es_ts, es = _walk(REC, 2, 6_000.0)
    as_of = np.arange(int(datetime.combine(REC, time(9, 45), tzinfo=ET).timestamp()) * NS,
                      int(datetime.combine(REC, time(10, 0), tzinfo=ET).timestamp()) * NS, NS, dtype=np.int64)
    spot = mp.last_price_at_or_before(es_ts, es, as_of - 80_000_000) / 1.001  # SPX history content: asOf - 80 ms
    folder = env.sky.root / "repull" / "parity" / f"{REC}_session_092700_0945" / "range" / REC.isoformat()
    b = pa.table({"as_of": pa.array(as_of // 1_000_000, pa.timestamp("ms", "UTC")), "symbol": ["SPX"] * len(as_of),
                  "spot": spot, "resolution": ["1s"] * len(as_of)})
    write_parquet(folder / "0945_boards.parquet", b, {})
    mids = np.round(spot / 5) * 5
    s = pa.table({"as_of": pa.array(np.repeat(as_of // 1_000_000, 2), pa.timestamp("ms", "UTC")),
                  "symbol": ["SPX"] * (2 * len(as_of)),
                  "strike": pa.array(np.ravel(np.column_stack([mids, mids + 5])), pa.float32()),
                  "value": pa.array(np.tile([1e6, 5e5], len(as_of)), pa.float32())})
    write_parquet(folder / "0945_strikes.parquet", s, {})
    rep = step8b_recorded.check_atlas_recorded(env, REC)
    rows = rep["spot_checks"]["SPX"]
    assert rep["spx_source"].startswith("the day's stored history windows")
    assert [r["minute"] for r in rows] == ["09:55"] and abs(rows[0]["r"] - 1.001) < 1e-3
    assert len(rows[0]["nodes"]) == 2


def test_atlas_minutes_are_spread_by_the_clock():
    tz = step8b.ET
    mins = step8b_recorded.atlas_minutes(datetime(2026, 10, 9, 9, 30, tzinfo=tz), datetime(2026, 10, 9, 16, 0, tzinfo=tz))
    # 09:40 to 15:50 in 9 equal steps of 41 min 6.7 s, each cut to its whole minute
    assert [m.strftime("%H:%M") for m in mins] == ["09:40", "10:21", "11:02", "11:43", "12:24", "13:05", "13:46",
                                                    "14:27", "15:08", "15:50"]
