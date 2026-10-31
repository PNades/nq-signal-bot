"""`nqbot preflight` (owner 2026-10-14, SPEC §9 pre-open): the pre-session machine check. On 10-14 Windows Update
restarted the laptop at 04:50 ET, Windows sat at the sign-in screen and the 09:27 recording never started.

Warns (exit EXIT_WARN, Discord from step 22) if a restart is pending, Windows Update isn't paused through the end of
today's session (or the pause ends within PAUSE_WARN_DAYS; on Windows 10 past end of support, the home PC, info
only: no updates come, owner 2026-10-31), the SSD isn't reachable and writable, the laptop is on
battery, closing the lid on battery would sleep (owner 2026-10-19, §13 Q-power; skipped on a machine with no battery,
the home PC), the SSD logged a drop since the last check, or (owner 2026-10-28, both machines) the Windows time zone
isn't Eastern, automatic time sync is off or the clock is off by more than `clock_max_offset_ms`, or (owner
2026-10-30) rclone isn't found (the path it will use is listed, rclone_path.py). Reads only (the registry, the SSD probe); writes its report to
<reports_dir>/preflight/ (or the internal-drive fallback when the SSD is gone)."""

from __future__ import annotations

import ctypes
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
PAUSE_WARN_DAYS = 2  # warn this many days before the Windows Update pause runs out (renew it in Settings)
SESSION_END_ET = time(16, 15)  # the pause must last at least past today's session (+ the 16:15 evening jobs, §11)

REBOOT_KEYS = (  # a key that exists = a restart is pending (Windows Update / servicing)
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending",
    r"SOFTWARE\Microsoft\WindowsUpdate\Orchestrator\RebootRequired",
)
# Owner 2026-10-31 (SPEC §11 Two machines): the home PC runs Windows 10 past end of support and can't upgrade, so
# Windows Update can't be paused and delivers no update restarts. Windows 10 support ended 2025-10-14; the consumer
# Extended Security Updates year ran through 2026-10-13, the last day any Windows 10 Home machine got updates. After
# it the pause check is info only on Windows 10; Windows 11 (the laptop, build >= 22000) keeps the warning.
WIN11_FIRST_BUILD = 22000
WIN10_LAST_UPDATE_DAY = date(2026, 10, 13)
PAUSE_KEY = r"SOFTWARE\Microsoft\WindowsUpdate\UX\Settings"
PAUSE_VALUES = ("PauseUpdatesExpiryTime", "PauseQualityUpdatesEndTime", "PauseFeatureUpdatesEndTime")


def _hklm_key_exists(path: str) -> bool:
    import winreg

    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path))
        return True
    except OSError:
        return False


def _hklm_value(path: str, name: str) -> Any:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def _on_ac() -> bool | None:
    class SPS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte), ("BatteryLifePercent", ctypes.c_byte),
                    ("SystemStatusFlag", ctypes.c_byte), ("BatteryLifeTime", ctypes.c_ulong),
                    ("BatteryFullLifeTime", ctypes.c_ulong)]

    s = SPS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
        return None
    return {0: False, 1: True}.get(s.ACLineStatus)


def _has_battery() -> bool | None:
    from nqbot.power import has_battery  # one rule for preflight and `nqbot run`'s power check (owner 2026-10-31)

    return has_battery()


def _windows_build() -> int | None:
    return sys.getwindowsversion().build if os.name == "nt" else None


def win10_past_support(build: int | None, now: datetime) -> bool:
    """Windows 10 (a build below Windows 11's first) after its last update day: no updates, nothing to pause."""
    return build is not None and build < WIN11_FIRST_BUILD and now.astimezone(ET).date() > WIN10_LAST_UPDATE_DAY


def pause_until(read: Callable[[str, str], Any]) -> datetime | None:
    """When the Windows Update pause ends (the earliest of the pause values set), or None if not paused."""
    ends = []
    for name in PAUSE_VALUES:
        v = read(PAUSE_KEY, name)
        if v:
            try:
                ends.append(datetime.fromisoformat(str(v).replace("Z", "+00:00")))
            except ValueError:
                pass
    return min(ends) if ends else None


def run_checks(now: datetime, *, key_exists: Callable[[str], bool], read: Callable[[str, str], Any],
               on_ac: Callable[[], bool | None], ssd_ok: Callable[[], tuple[bool, str]],
               drops_since: Callable[[datetime], int], last_check: datetime | None,
               lid_warning: Callable[[], str | None] = lambda: None,
               has_battery: Callable[[], bool | None] = lambda: True,
               clock: Callable[[], tuple[list[str], list[str]]] = lambda: ([], []),
               rclone: Callable[[], tuple[bool, str]] | None = None,
               windows_build: Callable[[], int | None] = lambda: None) -> dict[str, Any]:
    warn: list[str] = []
    ok: list[str] = []
    info: list[str] = []
    pending = [k for k in REBOOT_KEYS if key_exists(k)]
    if pending:
        warn.append("a restart is pending (" + "; ".join(k.rsplit("\\", 1)[-1] for k in pending) + "): restart now, "
                    "outside the session, or it may restart on its own")
    else:
        ok.append("no restart pending")
    end = pause_until(read)
    session_end = datetime.combine(now.astimezone(ET).date(), SESSION_END_ET, tzinfo=ET)
    build = windows_build()
    if win10_past_support(build, now):  # the home PC (owner 2026-10-31): nothing to pause, no update restarts
        info.append(f"Windows 10 (build {build}) is past end of support (last updates {WIN10_LAST_UPDATE_DAY}): Windows "
                    "Update delivers no updates, so no pause is needed (Defender definitions still update)")
    elif end is None:
        warn.append("Windows Update is not paused (Settings > Windows Update > Pause updates)")
    elif end <= session_end:
        warn.append(f"the Windows Update pause ends {end.astimezone(ET):%Y-%m-%d %H:%M} ET, before today's session "
                    "ends: extend it now")
    elif end - now <= timedelta(days=PAUSE_WARN_DAYS):
        warn.append(f"the Windows Update pause ends {end.astimezone(ET):%Y-%m-%d %H:%M} ET (within "
                    f"{PAUSE_WARN_DAYS} days): extend it")
    else:
        ok.append(f"Windows Update paused until {end.astimezone(ET):%Y-%m-%d %H:%M} ET")
    ac = on_ac()
    (ok if ac else warn).append("on AC power" if ac else f"not on AC power ({'battery' if ac is False else 'unknown'})")
    if has_battery() is False:  # the home PC (owner 2026-10-28): no lid, no battery
        ok.append("no battery (desktop): lid check skipped")
    else:
        lid = lid_warning()  # owner 2026-10-19 (§13 Q-power): the lid on battery = Do nothing
        (warn if lid else ok).append(lid or "closing the lid on battery: Do nothing")
    clock_warn, clock_ok = clock()  # owner 2026-10-28: Eastern time zone, automatic sync, NTP offset
    warn += clock_warn
    ok += clock_ok
    good, why = ssd_ok()
    (ok if good else warn).append("SSD reachable and writable" if good else f"SSD not usable: {why}")
    n = drops_since(last_check or now - timedelta(days=1))
    (warn if n else ok).append(f"SSD drops logged since the last check: {n}")
    if rclone is not None:  # owner 2026-10-30: the rclone that status-push and backup will run
        found, line = rclone()
        (ok if found else warn).append(line)
    return {"checked_at": now.isoformat(), "warnings": warn, "ok": ok, "info": info,
            "pause_until": end.isoformat() if end else None}


def drops_since(log: Path, since: datetime) -> int:
    if not log.exists():
        return 0
    n = 0
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            at = datetime.fromisoformat(line.split(" ", 1)[0])
        except ValueError:
            continue
        if at >= since and "device dropped" in line:
            n += 1
    return n


def preflight(settings: dict[str, Any], echo: Callable[[str], None] = print) -> dict[str, Any]:
    from nqbot.clock import clock_checks
    from nqbot.fallback import fallback_paths, ssd_writable
    from nqbot.power import lid_on_battery_warning
    from nqbot.rclone_path import rclone_line
    from nqbot.storage import SSD_EVENT_LOG, SSDNotFound, atomic_write_json, resolve_paths

    now = datetime.now(timezone.utc)
    try:
        paths = resolve_paths(settings)
        ssd_ok = lambda: ssd_writable(paths)  # noqa: E731
    except SSDNotFound as e:
        paths = None
        missing = str(e)  # read now: Python unbinds `e` when the except block ends (the lambda runs later)
        ssd_ok = lambda: (False, missing)  # noqa: E731
    state = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "nqbot" / "preflight_last.txt"
    try:
        last = datetime.fromisoformat(state.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        last = None
    rep = run_checks(now, key_exists=_hklm_key_exists, read=_hklm_value, on_ac=_on_ac, ssd_ok=ssd_ok,
                     drops_since=lambda since: drops_since(SSD_EVENT_LOG, since), last_check=last,
                     lid_warning=lid_on_battery_warning, has_battery=_has_battery,
                     clock=lambda: clock_checks(settings), rclone=lambda: rclone_line(settings),
                     windows_build=_windows_build)
    for line in rep["warnings"]:
        echo(f"WARN {line}")
    for line in rep["info"]:
        echo(f"info {line}")
    for line in rep["ok"]:
        echo(f"ok   {line}")
    target = paths if paths is not None and ssd_ok()[0] else fallback_paths(settings["paths"]["root"])
    out = target.reports_dir / "preflight" / f"preflight_{now:%Y%m%dT%H%M%SZ}.json"
    atomic_write_json(out, rep)
    echo(f"report: {out}")
    try:
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(now.isoformat(), encoding="utf-8")
    except OSError:
        pass
    return rep
