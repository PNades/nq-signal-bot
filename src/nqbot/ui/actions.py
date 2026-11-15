"""The one action path for KILL, FLAT ALL and PAUSE / RESUME (SPEC §9b kill/flat fallback, §10.3).

The SPA's controls, the no-JavaScript /emergency page and the fallback page all call `perform`. From step 22 it hands
the action to the live engine over 127.0.0.1 (and, with the engine down, sends `exit` to every account and ticker as
the guard does). Before then no engine exists on any machine, and this PC never trades (CLAUDE.md rule 13: no
TradersPost webhooks in its .env), so `perform` sends nothing, says so plainly, and logs the request.
"""

from __future__ import annotations

import logging
import socket
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal

from nqbot import storage
from nqbot.storage import Paths

log = logging.getLogger(__name__)

Action = Literal["flat", "kill", "pause", "resume"]
ACTIONS: tuple[Action, ...] = ("flat", "kill", "pause", "resume")
ACTION_LOG = "ui_actions.jsonl"
NO_ENGINE = (
    "No live engine on this machine, so there is nothing to flatten or stop "
    "(the live engine and its exit path come at step 22)."
)


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    action: Action
    message: str
    engine: str
    at_utc: str

    def to_dict(self) -> dict:
        return asdict(self)


def engine_present(paths: Paths | None) -> bool:
    """Whether a live engine runs for this machine. The engine and its lock file are built at step 22; until then
    there is none."""
    return False


def perform(action: Action, source: str, paths_fn: Callable[[], Paths | None]) -> ActionResult:
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    paths = paths_fn()
    if not engine_present(paths):
        result = ActionResult(ok=False, action=action, message=NO_ENGINE, engine="none", at_utc=now)
    else:  # pragma: no cover - step 22
        raise NotImplementedError("the live engine's action client is built at step 22")
    _log(paths, result, source)
    return result


def _log(paths: Paths | None, result: ActionResult, source: str) -> None:
    row = {**result.to_dict(), "source": source, "host": socket.gethostname()}
    log.warning("panel action %s from %s: %s", result.action, source, result.message)
    if paths is None:
        return
    try:
        storage.append_jsonl(paths.log_dir / ACTION_LOG, row)
    except OSError as e:  # the action itself must never fail because its log line couldn't be written
        log.error("couldn't write the panel action log: %s", e)
