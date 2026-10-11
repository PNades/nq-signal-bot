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


def _recording(env, qqq_arrival_delay_s=None):
    """1-second boards 09:30-10:30 whose spots hold the futures price 60 ms before asOf (the live content time).
    With qqq_arrival_delay_s, an arrival column: QQQ boards arrive that late, the others 0.4 s after asOf."""
    nq_ts, nq = _walk(REC, 1, 24_000.0)
    es_ts, es = _walk(REC, 2, 6_000.0)
    as_of = np.arange(int(datetime.combine(REC, time(9, 30), tzinfo=ET).timestamp()) * NS,
                      int(datetime.combine(REC, time(10, 30), tzinfo=ET).timestamp()) * NS, NS, dtype=np.int64)
    f_nq = mp.last_price_at_or_before(nq_ts, nq, as_of - 60_000_000)
    f_es = mp.last_price_at_or_before(es_ts, es, as_of - 60_000_000)
    spots = {"NDXP": f_nq / RHO, "QQQ": f_nq / (RHO * K), "SPY": f_es / 10.0, "SPX": f_es / 1.001}
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
