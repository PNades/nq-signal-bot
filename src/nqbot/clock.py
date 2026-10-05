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
