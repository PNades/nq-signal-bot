"""Keep the laptop awake during overnight jobs, and warn about power settings that would stop them (SPEC §11).

Owner 2026-10-05 (presd_fixes decision 6): the laptop stays plugged in and on overnight. `keep_awake()` asks
Windows not to sleep while a job runs (`SetThreadExecutionState`); it does not stop lid-close sleep, so the lid
action on AC must be "Do nothing". `power_warnings()` reads the active power plan with `powercfg /qh` and
returns a warning for each AC setting that could still stop a job; it never changes anything. `/qh` is
`/query` including hidden settings (some laptops hide the lid action from `/query`).
"""

from __future__ import annotations

import os
import re
import subprocess
from contextlib import contextmanager
from typing import Callable, Iterator

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
POWERCFG_TIMEOUT_SEC = 10

SUB_SLEEP = "238c9fa8-0aad-41ed-83f4-97be242c8f20"
SUB_BUTTONS = "4f971e89-eebd-4455-a8de-9e59040e7347"
SUB_USB = "2a737441-1930-4402-8d77-b2bebba308a3"
# (what, subgroup GUID, setting GUID, the AC value that is fine, the owner's fix)
CHECKS = (
    ("sleep after (on AC)", SUB_SLEEP, "29f6c1db-86da-48c5-9fdb-f2b67b1f44da", 0,
     "powercfg /change standby-timeout-ac 0"),
    ("hibernate after (on AC)", SUB_SLEEP, "9d7815a6-7ee4-497e-8888-515a05f02364", 0,
     "powercfg /change hibernate-timeout-ac 0"),
    ("lid close action (on AC)", SUB_BUTTONS, "5ca83367-6e45-459f-a27b-476b1d01c936", 0,
     'Control Panel > Power Options > "Choose what closing the lid does": When I close the lid, Plugged in = Do nothing'),
    ("USB selective suspend (on AC)", SUB_USB, "48e6b7a6-50f5-4782-a5d4-53bb8f07e226", 0,
     'Power Options > Change plan settings > Change advanced power settings > USB settings > '
     'USB selective suspend setting, Plugged in = Disabled'),
)
AC_INDEX = re.compile(r"Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)")


def _set_state(flags: int) -> None:
    import ctypes

    ctypes.windll.kernel32.SetThreadExecutionState(flags)


@contextmanager
def keep_awake() -> Iterator[None]:
    """System sleep is held off until the block ends (no-op off Windows)."""
    if os.name != "nt":
        yield
        return
    _set_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    try:
        yield
    finally:
        _set_state(ES_CONTINUOUS)


def powercfg_query(subgroup: str, setting: str) -> str | None:
    """The `powercfg /qh` text for one setting of the active plan, or None if it can't be read."""
    try:
        out = subprocess.run(["powercfg", "/qh", "SCHEME_CURRENT", subgroup, setting], capture_output=True,
                             text=True, timeout=POWERCFG_TIMEOUT_SEC, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def power_warnings(query: Callable[[str, str], str | None] | None = None) -> list[str]:
    """One line per AC setting that could stop an overnight job (empty list: all fine, or not Windows)."""
    if query is None:
        if os.name != "nt":
            return []
        query = powercfg_query
    warnings = []
    for what, subgroup, setting, ok, fix in CHECKS:
        m = AC_INDEX.search(query(subgroup, setting) or "")
        if m is None:
            warnings.append(f"power: couldn't read {what}; check it by hand ({fix})")
        elif int(m.group(1), 16) != ok:
            warnings.append(f"power: {what} could stop an overnight job (value {int(m.group(1), 16)}). Fix: {fix}")
    return warnings
