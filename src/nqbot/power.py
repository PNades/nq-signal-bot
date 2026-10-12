"""Keep the laptop awake during overnight jobs, and warn about power settings that would stop them (SPEC §11).

Owner 2026-10-05 (presd_fixes decision 6): the laptop stays plugged in and on overnight. `keep_awake()` asks
Windows not to sleep while a job runs (`SetThreadExecutionState`); it does not stop lid-close sleep, so the lid
action on AC must be "Do nothing". `power_warnings()` reads the active power plan with `powercfg /qh` and
returns a warning for each AC setting that could still stop a job; it never changes anything. `/qh` is
`/query` including hidden settings (some laptops hide the lid action from `/query`).
"""

from __future__ import annotations

import logging
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
    # owner 2026-10-11: the SSD's JMS583 bridge resets under link power management (U1/U2); keep the link awake
    ("USB 3 link power management (on AC)", SUB_USB, "d4e98f31-5ffe-4ce1-be31-1b38b384c009", 0,
     "powercfg /setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 "
     "d4e98f31-5ffe-4ce1-be31-1b38b384c009 0 && powercfg /setactive SCHEME_CURRENT"),
)
# powercfg prints the AC index then the DC index as the last two "...: 0x........" lines; matched by position, so a
# non-English Windows (translated labels) is read too.
HEX_LINE = re.compile(r":\s*0x([0-9a-fA-F]+)\s*$", re.MULTILINE)


def _set_state(flags: int) -> None:
    import ctypes

    if not ctypes.windll.kernel32.SetThreadExecutionState(ctypes.c_uint(flags)):
        logging.getLogger("nqbot.power").warning("SetThreadExecutionState failed: Windows may sleep during the job")


_depth = 0  # keep_awake nests (every nqbot command holds it, and `nqbot run` / step7 hold it again inside)


@contextmanager
def keep_awake() -> Iterator[None]:
    """System sleep is held off until the outermost block ends (no-op off Windows).

    Owner 2026-10-11: held by every nqbot command, not only `nqbot run`. With the screen off a command that didn't
    hold it let the laptop enter Modern Standby, which powers down the Type-C controller, and the SSD dropped off the
    bus 1-2 minutes later (System log 2026-10-08..11: every drop followed a standby entry; none under `nqbot run`)."""
    global _depth
    if os.name != "nt":
        yield
        return
    if _depth == 0:
        _set_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    _depth += 1
    try:
        yield
    finally:
        _depth -= 1
        if _depth == 0:
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
        values = HEX_LINE.findall(query(subgroup, setting) or "")
        if len(values) < 2:
            warnings.append(f"power: couldn't read {what}; check it by hand ({fix})")
        elif int(values[-2], 16) != ok:  # the AC index
            warnings.append(f"power: {what} could stop an overnight job (value {int(values[-2], 16)}). Fix: {fix}")
    return warnings
