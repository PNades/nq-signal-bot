"""One warm-up count for the Databento pull and the Tempest start (presd_fixes issue 8, SPEC §3.2)."""

from nqbot.checks import step7
from nqbot.clients import databento_pull
from nqbot.config import load_features
from nqbot.warmup import warmup_sessions


def test_warmup_sessions_is_the_longest_lookback_or_20_plus_one_close():
    features = load_features()
    assert warmup_sessions(features) == 21  # 20 returns for rv_nq need 21 closes (owner 2026-10-05)
    assert warmup_sessions({**features, "atr_period": 30}) == 31
    assert warmup_sessions({**features, "ib_avg_sessions": 25}) == 26


def test_databento_and_step7_use_the_same_function():
    assert databento_pull.warmup_sessions is warmup_sessions
    assert step7.warmup_sessions is warmup_sessions
