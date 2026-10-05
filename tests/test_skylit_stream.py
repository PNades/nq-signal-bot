import asyncio
import json

import httpx
import pytest

from nqbot.clients.skylit_stream import SSEParser, SkylitStream, StreamStopped
from nqbot.secrets import Secret
from tests.skylit_fakes import KEY, SYMBOLS_MAP, sse

SNAP = {"symbol": "QQQ", "asOf": "2026-03-05T14:30:01.000Z", "spot": 512.4, "previousClose": 510.0, "priceChange": 2.4,
        "priceChangePercent": 0.47, "expirations": ["2026-03-05"], "lagged": False,
        "strikes": [{"strike": 512, "value": 1500200, "nodeType": "king"}]}


class Clock:
    def __init__(self):
        self.t = 0

    def __call__(self):
        self.t += 1_000_000_000
        return self.t


def make_stream(responses, **kw):
    """responses: list of (status, bytes, headers) served one per connection."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status, body, headers = responses.pop(0)
        return httpx.Response(status, content=body, headers={"Content-Type": "text/event-stream", **headers})

    slept = []

    async def sleep(sec):
        slept.append(sec)

    stream = SkylitStream(Secret(KEY), symbols_map=SYMBOLS_MAP, maps=["QQQ", "NDXP", "SPY", "SPX"], metric="gamma",
                          max_expirations=1, max_strikes=200, transport=httpx.MockTransport(handler), clock_ns=Clock(),
                          sleep=sleep, rng=lambda: 0.5, **kw)
    return stream, seen, slept


async def collect(stream, n):
    out = []
    async for ev in stream.events():
        out.append(ev)
        if len(out) == n:
            break
    await stream.aclose()
    return out


def test_parser_handles_events_comments_and_multiline_data():
    p = SSEParser()
    lines = ["event: snapshot", "id: QQQ:1", "data: {\"a\":", "data: 1}", "", ": ping", "retry: 5", ""]
    out = [r for r in (p.feed(line) for line in lines) if r]
    assert out[0].kind == "snapshot" and out[0].event_id == "QQQ:1" and json.loads(out[0].data) == {"a": 1}
    assert out[1].kind == "ping"
    assert len(out) == 2


def test_events_parsed_with_request_params_and_credits():
    body = sse(("connected", {"format": "v2", "creditsRemaining": 500}, None), ("snapshot", SNAP, "QQQ:1772721001000"),
               ("ping", None, None), ("credits", {"remaining": 496, "charged": 4}, None),
               ("velocity", {"symbol": "QQQ", "nodes": []}, "QQQ:1772721001000"))
    stream, seen, _ = make_stream([(200, body, {})])
    evs = asyncio.run(collect(stream, 5))
    assert [e.kind for e in evs] == ["connected", "snapshot", "ping", "credits", "velocity"]
    q = seen[0].url.params
    assert q["symbols"] == "QQQ,NDXP,SPY,SPXW" and q["maxExpirations"] == "1" and q["maxStrikes"] == "200"
    assert q["format"] == "v2" and seen[0].headers["Authorization"] == f"Bearer {KEY}"
    assert stream.credits.remaining == 496 and stream.last_event_id == "QQQ:1772721001000"
    assert evs[1].data["spot"] == 512.4 and evs[1].connection == 0


def test_reconnect_event_resumes_at_once_with_last_event_id():
    first = sse(("snapshot", SNAP, "QQQ:1"), ("reconnect", {"reason": "max_duration"}, "QQQ:1"))
    second = sse(("resumed", {"gapCovered": True, "stateRestored": True, "symbols": []}, "QQQ:1"), ("snapshot", SNAP, "QQQ:2"))
    stream, seen, slept = make_stream([(200, first, {}), (200, second, {})])
    evs = asyncio.run(collect(stream, 4))
    assert [e.kind for e in evs] == ["snapshot", "reconnect", "resumed", "snapshot"]
    assert seen[1].headers["Last-Event-ID"] == "QQQ:1" and slept == []
    assert evs[3].connection == 1


def test_requested_reconnect_for_the_step7_test():
    stream, seen, _ = make_stream([(200, sse(("snapshot", SNAP, "QQQ:1"), ("ping", None, None)), {}),
                                   (200, sse(("snapshot", SNAP, "QQQ:2")), {})])

    async def run():
        out = []
        async for ev in stream.events():
            out.append(ev)
            if len(out) == 1:
                stream.request_reconnect()
            if len(out) == 2:
                break
        await stream.aclose()
        return out

    evs = asyncio.run(run())
    assert evs[1].connection == 1 and seen[1].headers["Last-Event-ID"] == "QQQ:1"


def test_dropped_connection_backs_off_and_counts_failures():
    stream, seen, slept = make_stream([(503, b"busy", {"Retry-After": "5"}), (200, sse(("snapshot", SNAP, "QQQ:1")), {})])
    evs = asyncio.run(collect(stream, 1))
    assert evs[0].kind == "snapshot" and slept and slept[0] >= 5


def test_closed_reasons():
    stream, _, _ = make_stream([(200, sse(("closed", {"reason": "insufficient_credits"}, None)), {})])
    with pytest.raises(StreamStopped, match="insufficient_credits"):
        asyncio.run(collect(stream, 5))
    stream, seen, _ = make_stream([(200, sse(("closed", {"reason": "credit_check_failed"}, None)), {}),
                                   (200, sse(("snapshot", SNAP, "QQQ:1")), {})])
    evs = asyncio.run(collect(stream, 2))
    assert [e.kind for e in evs] == ["closed", "snapshot"] and len(seen) == 2


def test_non_retryable_status_stops():
    stream, _, _ = make_stream([(402, b'{"error":{"code":"insufficient_credits"}}', {})])
    with pytest.raises(StreamStopped, match="402"):
        asyncio.run(collect(stream, 1))


def test_gives_up_after_max_failures():
    stream, _, _ = make_stream([(503, b"", {}), (503, b"", {}), (503, b"", {})], max_failures=2)
    with pytest.raises(StreamStopped, match="failed 3 times"):
        asyncio.run(collect(stream, 1))


def test_lagged_boards_counted_and_staleness():
    lagged = {**SNAP, "lagged": True}
    stream, _, _ = make_stream([(200, sse(("snapshot", lagged, "QQQ:1")), {})])
    assert not stream.is_stale(45)  # nothing received yet: the engine's connect timeout handles that
    evs = asyncio.run(collect(stream, 1))
    assert stream.lagged_count == 1
    t = evs[0].arrival_ns
    assert not stream.is_stale(45, now_ns=t + 45 * 10**9)
    assert stream.is_stale(45, now_ns=t + 46 * 10**9)
