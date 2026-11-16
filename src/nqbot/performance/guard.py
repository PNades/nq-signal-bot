"""The ledger guard (SPEC §9b item 6, §7 rule 2b; enforced in code, not by convention).

Backtest trades are classed by their ledger month:
- development months 1-6: viewable at every level (trade, day, totals);
- walk-forward months 7-9: totals and gate metrics only, and only once the step-16 gate is recorded in
  `config/gate_record.json`; never at trade or day level (owner 2026-11-09, §13 Q-dryrun13 (16));
- sealed months 10-12 and every month after the window (the top-up months): totals only once the step-20 sealed score
  is recorded; never at trade or day level until §13 Q-ledger-after20 is decided;
- dates before the window: never (they aren't ledger months).
Live, demo and prop trades (the live journal) are the bot's actual trading and are always viewable.

`apply` filters a ledger for one view level and says what it held back and why (the page shows the notice); `require`
refuses an explicit request outright (the API answers 403 with the reason), which is what the CSV export uses.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Literal

from nqbot import months as ledger_months
from nqbot.config import CONFIG_DIR

from .ledger import Trade

Level = Literal["trade", "day", "total"]
Phase = Literal["development", "walk_forward", "sealed", "outside", "live"]
GATE_WALK_FORWARD = "16"
GATE_SEALED = "20"
GATE_RECORD = CONFIG_DIR / "gate_record.json"


class Refused(Exception):
    """An explicit request the guard refuses; `gate` names the missing gate when one would allow it."""

    def __init__(self, reason: str, gate: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.gate = gate


def gates_passed(path: Path = GATE_RECORD) -> frozenset[str]:
    """The gate ids recorded as passed. A missing or unreadable record counts as no gate passed (refuse, never allow)."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return frozenset(str(g["gate"]) for g in doc.get("gates", []))
    except (OSError, ValueError, KeyError, TypeError):
        return frozenset()


@dataclass(frozen=True)
class Guard:
    settings: dict[str, Any]
    passed: frozenset[str]

    @classmethod
    def from_files(cls, settings: dict[str, Any], record: Path = GATE_RECORD) -> "Guard":
        return cls(settings, gates_passed(record))

    def phase_of(self, t: Trade) -> Phase:
        if t.source != "backtest":
            return "live"
        return self.phase_of_date(t.date)

    def phase_of_date(self, day: date) -> Phase:
        n = ledger_months.month_index(day, self.settings)
        if n is None:
            first, last = ledger_months.span(self.settings)
            return "sealed" if day > last else "outside"
        if n in ledger_months.DEVELOPMENT:
            return "development"
        if n in ledger_months.WALK_FORWARD:
            return "walk_forward"
        return "sealed"

    def why_not(self, phase: Phase, level: Level) -> tuple[str, str | None] | None:
        """None if this phase may be shown at this level, else (reason, missing gate or None)."""
        if phase in ("development", "live"):
            return None
        if phase == "outside":
            return ("dates before the ledger window are not part of any ledger month", None)
        if phase == "walk_forward":
            if level != "total":
                return ("walk-forward months 7-9 are never shown trade by trade or day by day, only as totals and "
                        "gate metrics (§7 rule 2b; owner 2026-11-09)", None)
            if GATE_WALK_FORWARD not in self.passed:
                return ("walk-forward months 7-9 open only once the step-16 gate is recorded as passed "
                        "(config/gate_record.json)", GATE_WALK_FORWARD)
            return None
        # sealed
        if level != "total":
            return ("sealed months are never shown trade by trade or day by day (§7 rule 2b; after step 20: §13 "
                    "Q-ledger-after20)", None)
        if GATE_SEALED not in self.passed:
            return ("sealed months open only once the step-20 sealed score is recorded (config/gate_record.json)",
                    GATE_SEALED)
        return None


@dataclass
class Filtered:
    trades: list[Trade]
    # what was held back: {phase: {"count": n, "reason": ..., "gate": ...}}
    held: dict[str, dict[str, Any]] = field(default_factory=dict)


def apply(trades: Iterable[Trade], level: Level, guard: Guard) -> Filtered:
    """Keep only what this view level may show; count the rest per phase, with the reason."""
    out = Filtered([])
    for t in trades:
        phase = guard.phase_of(t)
        why = guard.why_not(phase, level)
        if why is None:
            out.trades.append(t)
        else:
            h = out.held.setdefault(phase, {"count": 0, "reason": why[0], "gate": why[1]})
            h["count"] += 1
    return out


def require(guard: Guard, level: Level, *, phases: Iterable[Phase] = (), days: Iterable[date] = ()) -> None:
    """Refuse an explicit request: any named phase, or any date's phase, that this level may not show."""
    asked = list(phases) + [guard.phase_of_date(d) for d in days]
    for phase in asked:
        why = guard.why_not(phase, level)
        if why is not None:
            raise Refused(why[0], why[1])
