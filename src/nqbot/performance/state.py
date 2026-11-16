"""An account's state today, where the real-trades Monte Carlo ("from today") starts (SPEC §9b item 6; owner
2026-11-15): its balance, high water and the floor in force.

From the engine's daily account state (`<live_dir>/account_state.json`, §6.7 Account state; written from step 22 as
{"accounts": {name: {"balance_usd", "high_water_usd", "floor_usd", "as_of"}}}) once it exists; otherwise from the
account's real trades in the ledger (demo, prop, live) under its own floor rule; under `nqbot ui --synthetic` those
trades are the synthetic ledger's, labelled as such. None when neither exists (before step 22).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .floor import Rule, floor_path
from .ledger import Trade
from .stats import days

STATE_FILE = "account_state.json"
REAL = ("demo", "prop", "live")


def engine_state(live_dir: Path | None, name: str) -> dict[str, Any] | None:
    if live_dir is None:
        return None
    try:
        doc = json.loads((live_dir / STATE_FILE).read_text(encoding="utf-8"))
        a = doc["accounts"][name]
        return {"balance_usd": float(a["balance_usd"]), "high_water_usd": float(a["high_water_usd"]),
                "floor_usd": float(a["floor_usd"]), "as_of": a.get("as_of"), "source": "engine"}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def from_trades(trades: Iterable[Trade], account: dict[str, Any], synthetic: bool) -> dict[str, Any] | None:
    mine = [t for t in trades if t.account == account.get("name") and t.source in REAL]
    if not mine:
        return None
    rule = Rule.of(account)
    ds = days(mine)
    bal, b = [], rule.start_usd
    for d in ds:
        b += d.usd
        bal.append(b)
    fp = floor_path(rule, bal)
    return {"balance_usd": round(bal[-1], 2), "high_water_usd": round(fp.high[-1], 2), "floor_usd": round(fp.floor[-1], 2),
            "as_of": ds[-1].date.isoformat(), "source": "synthetic" if synthetic else "ledger", "trades": len(mine),
            "floor_reached": fp.breach_at is not None}


def today(live_dir: Path | None, trades: Iterable[Trade], account: dict[str, Any], synthetic: bool) -> dict[str, Any] | None:
    if not synthetic:
        got = engine_state(live_dir, str(account.get("name")))
        if got:
            return got
    return from_trades(trades, account, synthetic)
