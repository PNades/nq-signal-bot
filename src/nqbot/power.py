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
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable, Iterator

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002  # owner 2026-10-19 (§13 Q-power): screen off = Modern Standby, so sessions keep it on
POWERCFG_TIMEOUT_SEC = 10

SUB_SLEEP = "238c9fa8-0aad-41ed-83f4-97be242c8f20"
SUB_BUTTONS = "4f971e89-eebd-4455-a8de-9e59040e7347"
SUB_USB = "2a737441-1930-4402-8d77-b2bebba308a3"
LID_ACTION = "5ca83367-6e45-459f-a27b-476b1d01c936"  # 0 = Do nothing
# (what, subgroup GUID, setting GUID, the AC value that is fine, the owner's fix)
CHECKS = (
    ("sleep after (on AC)", SUB_SLEEP, "29f6c1db-86da-48c5-9fdb-f2b67b1f44da", 0,
     "powercfg /change standby-timeout-ac 0"),
    ("hibernate after (on AC)", SUB_SLEEP, "9d7815a6-7ee4-497e-8888-515a05f02364", 0,
     "powercfg /change hibernate-timeout-ac 0"),
    ("lid close action (on AC)", SUB_BUTTONS, LID_ACTION, 0,
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

    fn = ctypes.windll.kernel32.SetThreadExecutionState
    fn.argtypes, fn.restype = [ctypes.c_uint], ctypes.c_uint
    if not fn(flags):
        logging.getLogger("nqbot.power").warning("SetThreadExecutionState failed: Windows may sleep during the job")


# keep_awake nests (every nqbot command holds it, and `nqbot run` / step7 hold it again inside). Windows keeps the
# state per thread, so the held blocks are per thread too: a thread that holds it sets and clears its own.
_held = threading.local()


def _flags(stack: list[int]) -> int:
    out = 0
    for f in stack:
        out |= f
    return out


@contextmanager
def keep_awake(display: bool = False) -> Iterator[None]:
    """System sleep is held off until the outermost block ends (no-op off Windows). display=True (recordings and
    jobs, later live sessions; owner 2026-10-19, §13 Q-power) also keeps the display on while that block runs.

    Owner 2026-10-11: held by every nqbot command, not only `nqbot run`. With the screen off a command that didn't
    hold it let the laptop enter Modern Standby, which powers down the Type-C controller, and the SSD dropped off the
    bus 1-2 minutes later (System log 2026-10-08..11: every drop followed a standby entry; none under `nqbot run`).

    Owner 2026-10-17: so recordings, jobs and (later) live trading can't be put to sleep by an idle timeout, whatever
    the power plan says by then. Released when the block ends, also on an exception, Ctrl-C or `nqbot stop`; a
    process that is killed (Task Scheduler's End, a crash of Python itself) loses it with its threads, which is how
    Windows ties the state. It doesn't stop a sleep someone asks for (lid, power button, Start > Sleep) or an update
    restart: those are the lid setting and the update pause (`nqbot preflight`).

    Owner 2026-10-19 (§13 Q-power): on 10-19 the laptop ran on battery, the display timed out and Windows entered
    Modern Standby with ES_SYSTEM_REQUIRED held (a desktop app's system-required request doesn't keep a Modern
    Standby PC out of standby once the screen is off); an hour later it cut the network. ES_DISPLAY_REQUIRED stops the
    display idle timeout, so the idle path into standby can't start; the lid on battery is checked by preflight."""
    if os.name != "nt":
        yield
        return
    stack = _held.__dict__.setdefault("stack", [])
    before = _flags(stack) if stack else None
    stack.append(ES_SYSTEM_REQUIRED | (ES_DISPLAY_REQUIRED if display else 0))
    now = _flags(stack)
    if now != before:
        _set_state(ES_CONTINUOUS | now)
    try:
        yield
    finally:
        stack.pop()
        after = _flags(stack) if stack else None
        if after != now:
            _set_state(ES_CONTINUOUS | (after or 0))


def powercfg_query(subgroup: str, setting: str) -> str | None:
    """The `powercfg /qh` text for one setting of the active plan, or None if it can't be read."""
    try:
        out = subprocess.run(["powercfg", "/qh", "SCHEME_CURRENT", subgroup, setting], capture_output=True,
                             text=True, timeout=POWERCFG_TIMEOUT_SEC, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def lid_on_battery_warning(query: Callable[[str, str], str | None] | None = None) -> str | None:
    """Owner 2026-10-19 (§13 Q-power): closing the lid on battery must be Do nothing (ES_DISPLAY_REQUIRED doesn't
    stop a lid close). A warning, or None if fine (or not Windows)."""
    if query is None:
        if os.name != "nt":
            return None
        query = powercfg_query
    values = HEX_LINE.findall(query(SUB_BUTTONS, LID_ACTION) or "")
    fix = 'Control Panel > Power Options > "Choose what closing the lid does": When I close the lid, On battery = Do nothing'
    if len(values) < 2:
        return f"couldn't read the lid close action on battery; check it by hand ({fix})"
    if int(values[-1], 16) != 0:  # the DC index
        return f"closing the lid on battery would put the laptop to sleep (value {int(values[-1], 16)}). Fix: {fix}"
    return None


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


# --- owner 2026-10-19 (§13 Q-power): the power-source watch for recordings (live sessions at step 22) ------------

def system_power_status() -> tuple[bool | None, int | None]:
    """(on AC?, battery %) from GetSystemPowerStatus; None where Windows doesn't know (or not Windows)."""
    if os.name != "nt":
        return None, None
    import ctypes

    class SPS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]

    s = SPS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
        return None, None
    return {0: False, 1: True}.get(s.ACLineStatus), (None if s.BatteryLifePercent == 255 else int(s.BatteryLifePercent))


def log_power_event(detail: str) -> None:
    """A WARNING log line and a line in the event log (the SSD event log on the internal drive)."""
    from nqbot import storage

    line = f"{datetime.now(timezone.utc).isoformat()} pid {os.getpid()} power: {detail}"
    try:
        storage.SSD_EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(storage.SSD_EVENT_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    logging.getLogger("nqbot.power").warning("power: %s", detail)


class PowerWatch:
    """Polls the power source during a session (owner 2026-10-19, §13 Q-power). Logged (and in the event log): the
    switch to battery, each further `warn_step_pct` drop below the level at the switch, the `floor_pct` line (live,
    step 22: no new entries below it) and AC coming back. Keeps each battery stretch for the session report."""

    def __init__(self, status: Callable[[], tuple[bool | None, int | None]] = system_power_status, *,
                 poll_s: float, warn_step_pct: int, floor_pct: int,
                 event: Callable[[str], None] = log_power_event, clock: Callable[[], float] = time.time) -> None:
        self.status, self.poll_s, self.step, self.floor = status, poll_s, warn_step_pct, floor_pct
        self.event, self.clock = event, clock
        self.stretches: list[dict] = []
        self._cur: dict | None = None
        self._polled = False
        self._next_warn: int | None = None
        self._floor_warned = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _at(t: float) -> str:
        return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")

    def poll(self) -> None:
        ac, pct = self.status()
        t = self.clock()
        shown = pct if pct is not None else "?"
        if ac is False and self._cur is None:
            self._cur = {"from": self._at(t), "to": None, "pct_from": pct, "pct_min": pct, "_t": t}
            self._next_warn = None if pct is None else pct - self.step
            self._floor_warned = False
            self.event(f"on battery {'at the start of the session' if not self._polled else 'now (AC unplugged)'}"
                       f" ({shown}%)")
        elif ac is False and pct is not None:
            c = self._cur
            c["pct_min"] = pct if c["pct_min"] is None else min(c["pct_min"], pct)
            if self._next_warn is not None and pct <= self._next_warn:
                self.event(f"battery down to {pct}% (on battery since {c['from']})")
                while pct <= self._next_warn:
                    self._next_warn -= self.step
        if ac is False and pct is not None and pct <= self.floor and not self._floor_warned:
            self._floor_warned = True
            self.event(f"battery at {pct}%, at or below the {self.floor}% line (live: no new entries)")
        if ac is True and self._cur is not None:
            c = self._cur
            c["to"], c["pct_to"], c["seconds"] = self._at(t), pct, round(t - c.pop("_t"))
            self.stretches.append(c)
            self._cur = None
            self.event(f"AC back ({shown}%) after {c['seconds']} s on battery")
        self._polled = True

    def summary(self) -> dict:
        out = [dict(s) for s in self.stretches]
        if self._cur is not None:
            c = {k: v for k, v in self._cur.items() if k != "_t"}
            out.append({**c, "seconds": round(self.clock() - self._cur["_t"])})
        return {"battery_stretches": out, "on_battery_s": sum(s["seconds"] for s in out)}

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception:  # noqa: BLE001  the watch never ends a recording
                logging.getLogger("nqbot.power").exception("power watch: poll failed")
            self._stop.wait(self.poll_s)

    def __enter__(self) -> "PowerWatch":
        self._thread = threading.Thread(target=self._run, name="power-watch", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.poll_s + 5)
        try:
            self.poll()  # the state at the end (an AC return in the last seconds)
        except Exception:  # noqa: BLE001
            pass
