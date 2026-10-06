"""The warm-up session count (SPEC §3.2): one function shared by the Databento pull and the step-7/10 Tempest start.

warmup_sessions = max(20, the longest session lookback in features.yaml) + 1. Derived, not a config key.
"""

from __future__ import annotations

from typing import Any

MIN_WARMUP_SESSIONS = 20  # §3.2
# §3.2 (owner 2026-10-05): one more session than the longest lookback, because N close-to-close returns (rv_nq over
# rv_lookback_sessions, ATR's previous close) need N + 1 closes, so every rolling statistic is complete on day 1.
WARMUP_EXTRA_CLOSE = 1
WARMUP_LOOKBACK_KEYS = ("atr_period", "rv_lookback_sessions", "big_trade_lookback_sessions", "ib_avg_sessions",
                        "retest_lookback_sessions")  # §3.2, features.yaml


def warmup_sessions(features: dict[str, Any]) -> int:
    """§3.2: max(20, the lookbacks) + 1 (21 with the approved features.yaml)."""
    return max([MIN_WARMUP_SESSIONS, *(int(features[k]) for k in WARMUP_LOOKBACK_KEYS)]) + WARMUP_EXTRA_CLOSE
