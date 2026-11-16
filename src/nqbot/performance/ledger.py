"""The trade ledger (SPEC §9b item 6 Data): one row per closed trade, from the backtest and simulator runs (step 15),
the live trade journal (step 22: demo from step 24, prop accounts from step 26) and imported fills (step 25).

This module fixes the format those steps write and finds what exists. Nothing writes a ledger before step 15, so every
source reports "no ... trades yet" with the step it comes from; `nqbot ui --synthetic` shows the synthetic ledger instead
(`synthetic.py`), labelled as such.

Dates are New York trading dates (a trade after 18:00 ET belongs to the next session's date, §1.2); money is net of
commissions; R is account R (§6.1).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

Source = Literal["backtest", "demo", "prop", "live"]
SOURCES: tuple[Source, ...] = ("backtest", "demo", "prop", "live")

# where each source is written (relative to paths.data_dir / paths.live_dir), and the step that first writes it
BACKTEST_GLOB = "backtest/runs/*/trades.jsonl"   # step 15: one file per simulator run
JOURNAL_GLOB = "journal/trades_*.jsonl"          # step 22: the live trade journal, one file per session
FIRST_STEP: dict[Source, str] = {"backtest": "step 15", "demo": "step 24", "prop": "step 26", "live": "step 24"}


@dataclass(frozen=True)
class Trade:
    id: str
    account: str
    source: Source
    date: date                 # New York trading date
    entry_time: str            # HH:MM:SS ET
    exit_time: str
    direction: Literal["long", "short"]
    style: str                 # entry style a-d (§5.5)
    minis: int
    micros: int
    entry: float
    exit: float
    net_usd: float             # net of commissions
    r: float                   # account R (§6.1)
    commission_usd: float
    mae_usd: float | None = None   # the worst open loss during the trade (≤ 0), for intraday drawdown; None if not stored
    run: str = ""              # the simulator run or journal file it came from

    @property
    def size(self) -> str:
        parts = ([f"{self.minis} NQ"] if self.minis else []) + ([f"{self.micros} MNQ"] if self.micros else [])
        return " + ".join(parts) or "0"

    def row(self) -> dict[str, Any]:
        d = asdict(self)
        d["date"] = self.date.isoformat()
        d["size"] = self.size
        return d


def trade_from(obj: dict[str, Any], source: Source, run: str = "") -> Trade:
    """One ledger line (JSON) to a Trade; unknown keys are ignored, missing required ones raise KeyError."""
    return Trade(
        id=str(obj["id"]), account=str(obj["account"]), source=source, date=date.fromisoformat(str(obj["date"])),
        entry_time=str(obj["entry_time"]), exit_time=str(obj["exit_time"]), direction=obj["direction"],
        style=str(obj["style"]), minis=int(obj.get("minis", 0)), micros=int(obj.get("micros", 0)),
        entry=float(obj["entry"]), exit=float(obj["exit"]), net_usd=float(obj["net_usd"]), r=float(obj["r"]),
        commission_usd=float(obj.get("commission_usd", 0.0)),
        mae_usd=None if obj.get("mae_usd") is None else float(obj["mae_usd"]), run=run or str(obj.get("run", "")),
    )


@dataclass
class Loaded:
    trades: list[Trade] = field(default_factory=list)
    # per source: "ok", or why it has nothing ("no backtest trades yet (from step 15)")
    status: dict[str, str] = field(default_factory=dict)
    synthetic: bool = False


def _read_jsonl(path: Path, source: Source) -> list[Trade]:
    out: list[Trade] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            obj = json.loads(line)
            out.append(trade_from(obj, obj.get("source", source) if source != "backtest" else "backtest", run=path.parent.name))
    return out


def load(data_dir: Path | None, live_dir: Path | None) -> Loaded:
    """Every stored ledger. Missing folders mean the source doesn't exist yet."""
    got = Loaded()
    bt = sorted(data_dir.glob(BACKTEST_GLOB)) if data_dir else []
    for p in bt:
        got.trades.extend(_read_jsonl(p, "backtest"))
    got.status["backtest"] = "ok" if bt else f"no backtest trades yet (from {FIRST_STEP['backtest']})"
    jr = sorted(live_dir.glob(JOURNAL_GLOB)) if live_dir else []
    live = [t for p in jr for t in _read_jsonl(p, "live")]
    got.trades.extend(live)
    for s in ("demo", "prop", "live"):
        got.status[s] = "ok" if any(t.source == s for t in live) else f"no {s} trades yet (from {FIRST_STEP[s]})"  # type: ignore[index]
    got.trades.sort(key=lambda t: (t.date, t.entry_time, t.id))
    return got
