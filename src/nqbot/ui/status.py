"""The panel's status (GET /api/status and the /api/events stream; SPEC §9b, design-system/pages/shell.md).

Engine state, the SSD, alerts, whether Effects Auto must be Calm (the live engine running on this machine during
market hours), and the accounts the top bar lists. Built fresh for every frame from what exists now.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from nqbot.storage import Paths
from nqbot.ui import actions

ENGINE_NOTE_NONE = "No live engine on this machine (the engine is built at step 22)."


def build_status(paths_fn: Callable[[], Paths | None], *, synthetic: bool,
                 accounts_fn: Callable[[Paths | None], list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    paths = paths_fn()
    alerts: list[dict[str, str]] = []
    if paths is None:
        alerts.append({"id": "ssd-missing", "kind": "warn", "title": "SSD not found.",
                       "text": "Pages that read data can't load, and edits can't be saved. Kill and flat still work."})
    engine_here = actions.engine_present(paths)
    return {
        "server_time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": socket.gethostname(),
        "synthetic": synthetic,
        "ssd": {"found": paths is not None, "message": "SSD found" if paths is not None else "SSD not found"},
        "engine": {"state": "offline", "here": engine_here, "note": ENGINE_NOTE_NONE},
        # Effects Auto = Calm while the live engine runs here in market hours (MASTER §8); no engine before step 21
        "calm_forced": False,
        "alerts": alerts,
        "accounts": accounts_fn(paths) if accounts_fn else [],
    }
