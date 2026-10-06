"""Skylit live maps: /v1/stream, SSE format v2, all four maps on one connection (SPEC §3.1 Endpoints).

Built against docs/skylit/api-streams-and-limits.md. Events: connected, snapshot (one per symbol on
connect, then one per board change), velocity, symbol_unavailable, credits, resumed, reconnect, closed,
and the `: ping` comment every 15 s. Streams end after one hour with `reconnect`: reconnect at once with
Last-Event-ID (the server then sends `resumed` and the latest board per symbol; boards published in the
gap are not replayed). Dropped connections reconnect with backoff. `closed` ends the stream unless its
reason is credit_check_failed.

This module only reads and parses the stream and keeps the liveness clock (any event or ping). Applying
boards (map_latency_sec, the 09:30 rule), blocking entries when stale and gap fills are the live
engine's job (step 22) and use `is_stale()` and the event arrival times.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

import httpx

from nqbot.clients.skylit import BACKOFF_BASE_SEC, BACKOFF_CAP_SEC, BASE_URL, CreditTracker
from nqbot.secrets import Secret

STREAM_PATH = "/v1/stream"
READ_TIMEOUT_SEC = 60.0  # Skylit docs: treat 60 s of silence as a dropped connection (pings come every 15 s)
CONNECT_TIMEOUT_SEC = 15.0
STOP_REASONS = frozenset({"insufficient_credits", "account_suspended", "key_revoked", "monthly_cap_reached",
                          "daily_cap_reached", "access_withdrawn"})  # `closed` reasons: stop and tell the owner
NO_RETRY_STATUS = frozenset({400, 401, 402, 403, 404})

log_default = logging.getLogger("nqbot.skylit.stream")


class StreamStopped(Exception):
    """The stream can't continue (closed for credits/key/account, or a non-retryable HTTP status)."""


@dataclass
class StreamEvent:
    kind: str  # connected | snapshot | velocity | symbol_unavailable | credits | resumed | reconnect | closed | ping
    data: dict[str, Any] | None
    event_id: str | None
    arrival_ns: int  # local UTC time the event was read (injectable clock)
    connection: int  # 0 for the first connection, +1 per reconnect


@dataclass
class _Raw:
    kind: str
    data: str
    event_id: str | None


class SSEParser:
    """Line-by-line Server-Sent Events parser that also reports comment lines (`: ping`) as `ping` events."""

    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> None:
        self.kind: str | None = None
        self.data: list[str] = []
        self.event_id: str | None = None

    def feed(self, line: str) -> _Raw | None:
        line = line.rstrip("\r")
        if line == "":
            if self.kind is None and not self.data:
                return None
            raw = _Raw(self.kind or "message", "\n".join(self.data), self.event_id)
            self._reset()
            return raw
        if line.startswith(":"):
            return _Raw("ping", line[1:].strip(), None)
        name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if name == "event":
            self.kind = value
        elif name == "data":
            self.data.append(value)
        elif name == "id":
            self.event_id = value
        return None  # `retry:` and unknown fields are ignored


class SkylitStream:
    def __init__(
        self,
        api_key: Secret,
        *,
        symbols_map: dict[str, str],
        maps: list[str],
        metric: str,
        max_expirations: int,
        max_strikes: int,
        credits: CreditTracker | None = None,
        base_url: str = BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        log: logging.Logger = log_default,
        clock_ns: Callable[[], int] = time.time_ns,
        sleep: Callable[[float], Any] = asyncio.sleep,
        rng: Callable[[], float] = random.random,
        max_failures: int | None = None,
        max_reconnects: int | None = None,
    ):
        self.root_to_map = {v: k for k, v in symbols_map.items()}
        self.params = {"symbols": ",".join(symbols_map[m] for m in maps), "format": "v2", "metric": metric,
                       "maxExpirations": max_expirations, "maxStrikes": max_strikes}
        self.credits = credits or CreditTracker(None, log)
        self.log, self.clock_ns, self.sleep, self.rng = log, clock_ns, sleep, rng
        self.max_failures = max_failures
        self.max_reconnects = max_reconnects  # every reconnect counts (each costs a new open, §3.1); None = no cap
        self.last_event_id: str | None = None
        self.last_activity_ns: int | None = None
        self.connection = -1
        self.lagged_count = 0
        self._reconnect_requested = False
        self._client = httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key.reveal()}", "Accept": "text/event-stream"},
            timeout=httpx.Timeout(CONNECT_TIMEOUT_SEC, read=READ_TIMEOUT_SEC),
        )

    @classmethod
    def from_settings(cls, settings: dict[str, Any], api_key: Secret, maps: list[str], **kw: Any) -> SkylitStream:
        return cls(api_key, symbols_map=settings["skylit_symbols"], maps=maps, metric=settings["skylit_metric"],
                   max_expirations=settings["skylit_max_expirations"], max_strikes=settings["skylit_max_strikes"], **kw)

    async def aclose(self) -> None:
        await self._client.aclose()

    def request_reconnect(self) -> None:
        """Close the current connection after the next event and resume with Last-Event-ID (step-7 test)."""
        self._reconnect_requested = True

    def is_stale(self, stale_sec: float, now_ns: int | None = None) -> bool:
        """No event of any kind (boards, velocity, credits, ping) for stale_sec (§3.1, skylit_stale_sec)."""
        if self.last_activity_ns is None:
            return False
        return ((now_ns if now_ns is not None else self.clock_ns()) - self.last_activity_ns) / 1e9 > stale_sec

    def map_name(self, api_symbol: str) -> str:
        return self.root_to_map.get(api_symbol, api_symbol)

    def _backoff(self, failures: int, retry_after: str | None) -> float:
        wait = self.rng() * min(BACKOFF_CAP_SEC, BACKOFF_BASE_SEC * 2 ** failures)
        if retry_after is not None:
            wait = max(wait, float(retry_after))
        return wait

    async def events(self) -> AsyncIterator[StreamEvent]:
        """Every event, across reconnects, until the caller stops iterating or the stream is stopped."""
        failures = 0
        while True:
            headers = {"Last-Event-ID": self.last_event_id} if self.last_event_id else {}
            self.connection += 1
            if self.max_reconnects is not None and self.connection > self.max_reconnects:
                raise StreamStopped(f"stream reconnected {self.connection - 1} times: the cap is {self.max_reconnects}")
            self._reconnect_requested = False
            retry_after: str | None = None
            reconnect_now = False
            try:
                async with self._client.stream("GET", STREAM_PATH, params=self.params, headers=headers) as response:
                    if response.status_code >= 400:
                        body = (await response.aread()).decode("utf-8", "replace")[:300]
                        if response.status_code in NO_RETRY_STATUS:
                            raise StreamStopped(f"stream refused: HTTP {response.status_code} {body}")
                        retry_after = response.headers.get("Retry-After")
                        raise httpx.HTTPStatusError(f"HTTP {response.status_code}", request=response.request,
                                                    response=response)
                    parser = SSEParser()
                    async for line in response.aiter_lines():
                        raw = parser.feed(line)
                        if raw is None:
                            continue
                        event = self._event(raw)
                        failures = 0
                        yield event
                        if event.kind == "reconnect":
                            reconnect_now = True
                            break
                        if event.kind == "closed":
                            reason = (event.data or {}).get("reason")
                            if reason == "credit_check_failed":
                                self.log.warning("stream closed (credit_check_failed): reconnecting")
                                break
                            raise StreamStopped(f"stream closed by Skylit: {reason}")
                        if self._reconnect_requested:
                            reconnect_now = True
                            break
                    else:
                        self.log.warning("stream ended without a reconnect event: reconnecting")
            except StreamStopped:
                raise
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                self.log.warning("stream connection %d failed: %s", self.connection, type(e).__name__)
            if reconnect_now:
                continue  # reconnect at once with Last-Event-ID (§3.1)
            failures += 1
            if self.max_failures is not None and failures > self.max_failures:
                raise StreamStopped(f"stream failed {failures} times in a row")
            await self.sleep(self._backoff(failures - 1, retry_after))

    def _event(self, raw: _Raw) -> StreamEvent:
        now = self.clock_ns()
        self.last_activity_ns = now
        data: dict[str, Any] | None = None
        if raw.kind != "ping" and raw.data:
            try:
                data = json.loads(raw.data)
            except ValueError:
                self.log.error("stream %s event with undecodable data: %r", raw.kind, raw.data[:200])
        if raw.event_id:
            self.last_event_id = raw.event_id
        if raw.kind == "snapshot" and data and data.get("lagged"):
            self.lagged_count += 1
            self.log.warning("stream: lagged board for %s (older boards were skipped)", data.get("symbol"))
        elif raw.kind == "credits" and data:
            self.credits.update(data.get("remaining"))
        elif raw.kind == "connected" and data:
            self.credits.update(data.get("creditsRemaining"))
        elif raw.kind == "resumed" and data:
            self.log.info("stream resumed: gapCovered=%s stateRestored=%s %s", data.get("gapCovered"),
                          data.get("stateRestored"),
                          {s.get("symbol"): s.get("status") for s in data.get("symbols") or []})
        elif raw.kind == "symbol_unavailable" and data:
            self.log.warning("stream: %s unavailable (%s)", data.get("symbol"), data.get("reason"))
        return StreamEvent(raw.kind, data, raw.event_id, now, self.connection)
