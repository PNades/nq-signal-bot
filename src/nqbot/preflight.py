"""`nqbot preflight` (owner 2026-10-14, SPEC §9 pre-open): the pre-session machine check. On 10-14 Windows Update
restarted the laptop at 04:50 ET, Windows sat at the sign-in screen and the 09:27 recording never started.

Warns (exit EXIT_WARN, Discord from step 22) if a restart is pending, Windows Update isn't paused through the end of
today's session (or the pause ends within PAUSE_WARN_DAYS), the SSD isn't reachable and writable, the laptop is on
battery, or the SSD logged a drop since the last check. Reads only (the registry, the SSD probe); writes its report to
<reports_dir>/preflight/ (or the internal-drive fallback when the SSD is gone)."""

from __future__ import annotations

import ctypes
import os
from datetime import datetime, time, timedelta, timezone
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
               drops_since: Callable[[datetime], int], last_check: datetime | None) -> dict[str, Any]:
    warn: list[str] = []
    ok: list[str] = []
    pending = [k for k in REBOOT_KEYS if key_exists(k)]
    if pending:
        warn.append("a restart is pending (" + "; ".join(k.rsplit("\\", 1)[-1] for k in pending) + "): restart now, "
                    "outside the session, or it may restart on its own")
    else:
        ok.append("no restart pending")
    end = pause_until(read)
    session_end = datetime.combine(now.astimezone(ET).date(), SESSION_END_ET, tzinfo=ET)
    if end is None:
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
    good, why = ssd_ok()
    (ok if good else warn).append("SSD reachable and writable" if good else f"SSD not usable: {why}")
    n = drops_since(last_check or now - timedelta(days=1))
    (warn if n else ok).append(f"SSD drops logged since the last check: {n}")
    return {"checked_at": now.isoformat(), "warnings": warn, "ok": ok,
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
    from nqbot.fallback import fallback_paths, ssd_writable
    from nqbot.storage import SSD_EVENT_LOG, SSDNotFound, atomic_write_json, resolve_paths

    now = datetime.now(timezone.utc)
    try:
        paths = resolve_paths(settings)
        ssd_ok = lambda: ssd_writable(paths)  # noqa: E731
    except SSDNotFound as e:
        paths = None
        ssd_ok = lambda: (False, str(e))  # noqa: E731
    state = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "nqbot" / "preflight_last.txt"
    try:
        last = datetime.fromisoformat(state.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        last = None
    rep = run_checks(now, key_exists=_hklm_key_exists, read=_hklm_value, on_ac=_on_ac, ssd_ok=ssd_ok,
                     drops_since=lambda since: drops_since(SSD_EVENT_LOG, since), last_check=last)
    for line in rep["warnings"]:
        echo(f"WARN {line}")
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
