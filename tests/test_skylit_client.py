import logging
from datetime import date, datetime, timezone

import pytest

from nqbot.clients import skylit_credits as credits
from nqbot.clients.skylit import (
    MAX_ATTEMPTS, AccessDenied, OutOfCredits, RateLimiter, SkylitError, iso_utc, vol_history_cost,
)
from nqbot.secrets import MissingSecret, Secret, get_secret, read_env_file
from tests.skylit_fakes import KEY, FakeClock, FakeSkylit, make_client


def test_bearer_auth_and_key_never_logged(caplog):
    fake = FakeSkylit()
    client = make_client(fake)
    with caplog.at_level(logging.DEBUG):
        client.account()
        fake.queue["/v1/symbols"] = [(500, {"error": {"code": "INTERNAL_ERROR", "message": "x"}}, {})]
        client.symbols()
    assert fake.calls[0].headers["Authorization"] == f"Bearer {KEY}"
    assert KEY not in caplog.text
    assert KEY not in repr(Secret(KEY)) and KEY not in str(Secret(KEY))


def test_account_sets_rate_limit_and_balance():
    fake = FakeSkylit(credits=4321)
    fake.routes["/v1/account"] = lambda r: {"data": {"creditsBalance": 4321, "limits": {"requestsPerMinute": 60}}}
    client = make_client(fake)
    client.account()
    assert client.limiter.rpm == 60 and client.credits.remaining == 4321


def test_credit_tracking_records_each_charge():
    fake = FakeSkylit(credits=1000)
    client = make_client(fake)
    client.account()
    t0 = datetime(2026, 3, 5, 14, 30, tzinfo=timezone.utc)
    _, rec = client.historical_range(["QQQ"], t0, t0.replace(minute=44, second=59))
    assert rec.credits_remaining == 975 and rec.charged == 25
    assert rec.params["maxExpirations"] == 1 and rec.params["metric"] == "gamma" and rec.params["maxStrikes"] == 200


def test_low_credit_warning(caplog):
    fake = FakeSkylit(credits=9000)
    client = make_client(fake, credit_warn=10_000)
    with caplog.at_level(logging.WARNING, logger="nqbot.skylit"):
        client.account()
    assert "credits low" in caplog.text


def test_gateway_429_waits_until_reset():
    fake = FakeSkylit()
    clock = FakeClock(wall=1000.0)
    fake.queue["/v1/symbols"] = [(429, {"error": {"code": "rate_limited", "message": "slow"}},
                                  {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1030"})]
    fake.routes["/v1/symbols"] = lambda r: {"data": {"symbols": []}}
    client = make_client(fake, clock)
    client.symbols()
    assert len(fake.calls) == 2
    assert max(clock.slept) >= 30  # waited until X-RateLimit-Reset (plus jitter)


def test_api_429_honours_retry_after():
    fake = FakeSkylit()
    clock = FakeClock()
    fake.queue["/v1/symbols"] = [(429, {"error": {"code": "too_many_concurrent_requests", "message": "x"}},
                                  {"Retry-After": "7"})]
    fake.routes["/v1/symbols"] = lambda r: {"data": {"symbols": []}}
    make_client(fake, clock).symbols()
    assert 7 in clock.slept


def test_5xx_retried_then_gives_up():
    fake = FakeSkylit()
    fake.queue["/v1/symbols"] = [(503, {"error": {"code": "UNAVAILABLE", "message": "x"}}, {})] * MAX_ATTEMPTS
    clock = FakeClock()
    with pytest.raises(SkylitError, match="gave_up"):
        make_client(fake, clock).symbols()
    assert len(fake.calls) == MAX_ATTEMPTS
    assert len(clock.slept) == MAX_ATTEMPTS - 1


@pytest.mark.parametrize("status,exc", [(402, OutOfCredits), (403, AccessDenied), (401, AccessDenied), (400, SkylitError)])
def test_non_retryable_errors_are_not_retried(status, exc):
    fake = FakeSkylit()
    fake.queue["/v1/symbols"] = [(status, {"error": {"code": "x", "message": "no"}}, {})]
    with pytest.raises(exc):
        make_client(fake).symbols()
    assert len(fake.calls) == 1


def test_token_bucket_paces_requests():
    clock = FakeClock()
    limiter = RateLimiter(60, mono=clock.mono, wall=clock.wall, sleep=clock.sleep)
    for _ in range(60):
        limiter.acquire()
    assert clock.slept == []
    limiter.acquire()  # the 61st request within the minute waits about one second
    assert clock.slept and abs(clock.slept[0] - 1.0) < 1e-9


def test_server_says_window_nearly_used_up_sleeps_to_reset():
    fake = FakeSkylit()
    clock = FakeClock(wall=500.0)
    fake.routes["/v1/symbols"] = lambda r: {"data": {"symbols": []}}
    fake.queue["/v1/symbols"] = [(200, {"data": {"symbols": []}}, {"X-RateLimit-Remaining": "1", "X-RateLimit-Reset": "510"})]
    make_client(fake, clock).symbols()
    assert clock.slept and clock.slept[0] >= 10


def test_iso_utc_and_inclusive_window_format():
    t = datetime(2026, 3, 5, 9, 44, 59, 999000, tzinfo=timezone.utc)
    assert iso_utc(t) == "2026-03-05T09:44:59.999Z"
    with pytest.raises(ValueError):
        iso_utc(datetime(2026, 3, 5))


def test_symbol_roots_map_spx_to_spxw():
    fake = FakeSkylit()
    client = make_client(fake)
    assert client.roots(["QQQ", "SPX"]) == "QQQ,SPXW"
    assert client.map_name("SPXW") == "SPX"
    with pytest.raises(SkylitError):
        client.map_name("SPX")  # the monthlies root is never requested


def test_vol_history_cost_formula():
    # 1 symbol x a 23-weekday month = 3 credits (docs: "1 symbol x the default month is 3")
    assert vol_history_cost(1, date(2026, 3, 1), date(2026, 3, 31)) == 3
    assert vol_history_cost(1, date(2026, 3, 7), date(2026, 3, 8)) == 1  # weekend only: minimum 1
    assert vol_history_cost(10, date(2025, 1, 1), date(2025, 12, 31)) == 261


def test_credit_gate_prints_and_refuses_over_5000():
    lines = []
    est = credits.range_estimate("backfill", 300)  # 7,500 credits
    with pytest.raises(credits.CreditRefused, match="--approve-credits 7500"):
        credits.check(est, balance=100_000, approved=None, echo=lines.append)
    assert "7,500 credits" in "\n".join(lines)
    credits.check(est, balance=100_000, approved=7500, echo=lines.append)  # owner OK'd it
    with pytest.raises(credits.CreditRefused, match="balance"):
        credits.check(credits.range_estimate("small", 10), balance=200, approved=None, echo=lines.append)
    credits.check(credits.range_estimate("small", 10), balance=None, approved=None, echo=lines.append)  # 250: proceeds


def test_credit_estimates_per_endpoint():
    assert credits.historical_estimate("x", 40).total == 200
    assert credits.stream_estimate("x", 4, 15, reconnects=1).total == 4 * 2 + 4 * 16  # a reconnect starts a new minute
    assert credits.dark_pool_estimate("x", 45, 1.0, top_prints=6).total == 45 * 5 + 6 * 3
    assert credits.vol_history_estimate("x", 1, date(2026, 3, 1), date(2026, 3, 31)).total == 3


def test_env_file_parsing(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# c\nSKYLIT_API_KEY='abc def'\nexport OTHER=1\n\nBROKEN\n", encoding="utf-8")
    assert read_env_file(env) == {"SKYLIT_API_KEY": "abc def", "OTHER": "1"}
    monkeypatch.delenv("SKYLIT_API_KEY", raising=False)
    assert get_secret("SKYLIT_API_KEY", env).reveal() == "abc def"
    with pytest.raises(MissingSecret):
        get_secret("NOPE", env)
