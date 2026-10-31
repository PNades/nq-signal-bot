"""Local clock offset against an NTP server (SPEC §9 Clock; settings `ntp_server`). Step 7 records it next to
the stream latency it measures, since map_latency_sec is arrival (local clock) - asOf (Skylit's clock).
Step 22 builds the live clock check on the same function."""

from __future__ import annotations

import socket
import struct
import time

NTP_PORT = 123
NTP_EPOCH_OFFSET = 2_208_988_800  # seconds from 1900-01-01 (NTP) to 1970-01-01 (Unix)
NTP_TIMEOUT_SEC = 3.0


def _ntp_time(data: bytes, offset: int) -> float:
    sec, frac = struct.unpack("!II", data[offset:offset + 8])
    return sec - NTP_EPOCH_OFFSET + frac / 2**32


def ntp_offset_ms(server: str, timeout: float = NTP_TIMEOUT_SEC) -> float:
    """Server time minus local time, in ms (SNTP, one exchange: ((t2 - t1) + (t3 - t4)) / 2)."""
    packet = b"\x1b" + 47 * b"\0"  # LI 0, version 3, mode 3 (client)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        t1 = time.time()
        s.sendto(packet, (server, NTP_PORT))
        data, _ = s.recvfrom(48)
        t4 = time.time()
    if len(data) < 48:
        raise OSError(f"short NTP answer from {server}")
    t2, t3 = _ntp_time(data, 32), _ntp_time(data, 40)
    return ((t2 - t1) + (t3 - t4)) / 2 * 1000.0


# --- machine clock check (owner 2026-10-28: preflight and status on both machines) ---------------------------------
TZ_KEY = r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation"
EASTERN_TZ = "Eastern Standard Time"  # Windows' name for America/New_York, summer and winter alike
W32TIME_KEY = r"SYSTEM\CurrentControlSet\Services\W32Time\Parameters"
SYNC_OFF = "NoSync"  # W32Time Type when "Set time automatically" is off
W32TIME_SERVICE = "w32time"


def _hklm(path: str, name: str):
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def w32time_status() -> str | None:
    """The Windows Time service's state ('running', 'stopped', ...), or None if it can't be read."""
    try:
        import psutil

        return psutil.win_service_get(W32TIME_SERVICE).status()
    except Exception:  # noqa: BLE001 - not Windows, no such service, access denied
        return None


def clock_checks(settings: dict, *, offset_ms=None, read=None, service=None) -> tuple[list[str], list[str]]:
    """(warnings, ok lines): the Windows time zone is Eastern, automatic time sync is on, the Windows Time service
    runs (owner 2026-10-31: on the PC it was stopped, so the setting alone said "on" while nothing synced), and the
    clock is within `clock_max_offset_ms` of `ntp_server` (SPEC §9 Clock)."""
    import os

    read = read or (_hklm if os.name == "nt" else (lambda path, name: None))
    service = service or w32time_status
    offset_ms = offset_ms or (lambda: ntp_offset_ms(settings["ntp_server"]))
    warn: list[str] = []
    ok: list[str] = []
    tz = read(TZ_KEY, "TimeZoneKeyName")
    if tz is None:
        warn.append("couldn't read the Windows time zone (Settings > Time & language > Date & time)")
    elif str(tz).strip("\0 ") != EASTERN_TZ:
        warn.append(f"the Windows time zone is {tz!s}, not {EASTERN_TZ} (Settings > Time & language > Date & time)")
    else:
        ok.append(f"time zone {EASTERN_TZ}")
    sync = read(W32TIME_KEY, "Type")
    if sync is None:
        warn.append("couldn't read the automatic time sync setting (Windows Time service)")
    elif str(sync) == SYNC_OFF:
        warn.append('automatic time sync is off (Settings > Time & language > Date & time > "Set time automatically")')
    else:
        ok.append(f"automatic time sync on ({sync})")
    state = service()
    if state == "running":
        ok.append("Windows Time service running")
    else:
        warn.append(f"the Windows Time service (w32time) is {state or 'unreadable'}, so nothing keeps the clock "
                    "synced: run scripts\\fix_time_sync.ps1 from an admin PowerShell")
    limit = float(settings["clock_max_offset_ms"])
    try:
        off = offset_ms()
    except OSError as e:
        warn.append(f"couldn't reach {settings['ntp_server']} to check the clock ({e})")
    else:
        line = f"clock offset {off:+.0f} ms vs {settings['ntp_server']} (limit {limit:.0f} ms)"
        if abs(off) > limit:
            warn.append(line + ': sync now (Date & time > "Sync now")')
        else:
            ok.append(line)
    return warn, ok
