"""A hand-built fake of the Skylit API for unit tests (httpx.MockTransport): no network, ever."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

from nqbot.clients.skylit import SkylitClient
from nqbot.secrets import Secret

SYMBOLS_MAP = {"QQQ": "QQQ", "NDXP": "NDXP", "SPY": "SPY", "SPX": "SPXW"}
SPOTS = {"QQQ": 600.0, "NDXP": 24000.0, "SPY": 650.0, "SPXW": 6500.0}
STEP = {"QQQ": 1.0, "NDXP": 10.0, "SPY": 1.0, "SPXW": 5.0}
COSTS = {"/v1/historical/range": 25, "/v1/historical": 5, "/v1/dark-pool/trades": 5, "/v1/vol/history": 1}
KEY = "sk-test-0123456789"
ET = ZoneInfo("America/New_York")


def z(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_z(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def ladder(root: str, n: int = 11) -> list[float]:
    spot, step = SPOTS[root], STEP[root]
    return [spot + (i - n // 2) * step for i in range(n)]


def board_values(root: str, strikes: list[float], k: int) -> list[float]:
    """Deterministic values: a King two steps above spot, a big negative node below, small elsewhere."""
    out = []
    for i, s in enumerate(strikes):
        mid = len(strikes) // 2
        v = 1000.0 + 10 * i + k
        if i == mid + 2:
            v = 1_000_000.0 + k
        elif i == mid - 3:
            v = -400_000.0 - k
        out.append(v)
    return out


def range_body(roots: list[str], start: datetime, end: datetime, step_sec: int = 60, resolution: str = "1s",
               n_strikes: int = 11) -> dict[str, Any]:
    symbols = []
    for root in roots:
        strikes = ladder(root, n_strikes)
        frames = []
        t, k = start, 0
        while t <= end:
            frames.append({"asOf": z(t), "axis": 0, "spot": SPOTS[root] + 0.01 * k,
                           "previousClose": SPOTS[root] - 1.0, "values": board_values(root, strikes, k)})
            t += timedelta(seconds=step_sec)
            k += 1
        symbols.append({"symbol": root, "axes": [{"id": 0, "strikes": strikes,
                                                  "expirations": [start.astimezone(ET).date().isoformat()]}],
                        "frames": frames})
    return {"data": {"from": z(start), "to": z(end), "symbols": symbols},
            "meta": {"metric": "gamma", "resolution": resolution, "mode": "historical", "cached": False}}


class FakeSkylit:
    """Routes by path. `queue[path]` holds canned responses served first (status, body, headers)."""

    def __init__(self, credits: int = 100_000, step_sec: int = 300):
        self.credits = credits
        self.step_sec = step_sec
        self.calls: list[httpx.Request] = []
        self.queue: dict[str, list[tuple[int, Any, dict[str, str]]]] = {}
        self.routes: dict[str, Callable[[httpx.Request], Any]] = {}
        self.dp_prints: list[dict[str, Any]] = []
        self.dp_range_drops = 0  # > 0: a date_start/date_end call loses that many prints (the step-7 comparison)

    @staticmethod
    def dp_trade_date(p: dict[str, Any]) -> str:
        """A print's ET trade date (what `date` / `date_start`..`date_end` filter on)."""
        return parse_z(p["timestamp"]).astimezone(ZoneInfo("America/New_York")).date().isoformat()

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def paths(self) -> list[str]:
        return [r.url.path for r in self.calls]

    def _reply(self, status: int, body: Any, headers: dict[str, str] | None = None, path: str = "") -> httpx.Response:
        h = {"X-RateLimit-Limit": "120", "X-RateLimit-Remaining": "100", "X-RateLimit-Reset": "0", **(headers or {})}
        if status < 400:
            self.credits -= COSTS.get(path, 0) if not path.startswith("/v1/dark-pool/top-prints") else 3
        h["X-Credits-Remaining"] = str(self.credits)
        return httpx.Response(status, json=body, headers=h)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if self.queue.get(path):
            status, body, headers = self.queue[path].pop(0)
            return self._reply(status, body, headers, path)
        if path in self.routes:
            return self._reply(200, self.routes[path](request), path=path)
        q = request.url.params
        if path == "/v1/account":
            return self._reply(200, {"data": {"creditsBalance": self.credits, "limits": {"requestsPerMinute": 120}}})
        if path == "/v1/symbols":
            return self._reply(200, {"data": {"symbols": []}})
        if path == "/v1/vol/symbols":
            return self._reply(200, {"data": {"symbols": [], "missing": []}, "meta": {}})
        if path == "/v1/historical/range":
            roots = q["symbols"].split(",")
            return self._reply(200, range_body(roots, parse_z(q["from"]), parse_z(q["to"]), self.step_sec), path=path)
        if path == "/v1/dark-pool/trades" and self.dp_range_drops and "date_start" in q:  # a range that loses prints
            page = self._dp_page(q)
            page["data"] = page["data"][self.dp_range_drops:]
            return self._reply(200, page, path=path)
        if path == "/v1/dark-pool/trades":
            return self._reply(200, self._dp_page(q), path=path)
        if path.startswith("/v1/dark-pool/top-prints/"):
            return self._reply(200, {"data": [], "meta": {}}, path=path)
        return httpx.Response(404, json={"error": {"code": "not_found", "message": path}})

    def _dp_page(self, q: httpx.QueryParams) -> dict[str, Any]:
        lo, hi = (q["date"], q["date"]) if "date" in q else (q["date_start"], q["date_end"])
        rows = [p for p in self.dp_prints if lo <= self.dp_trade_date(p) <= hi and p["ticker"] in q["tickers"].split(",")]
        rows.sort(key=lambda p: p["timestamp"])
        limit = int(q.get("limit", 500))
        start = int(q.get("cursor") or 0)
        page = rows[start:start + limit]
        return {"data": page, "meta": {"limit": limit, "count": len(page), "hasMore": len(page) == limit,
                                       "nextCursor": str(start + limit)}}


class FakeClock:
    """mono/wall/sleep for the client: sleeping advances both clocks."""

    def __init__(self, wall: float = 1_790_000_000.0):
        self.t = 0.0
        self.wall0 = wall
        self.slept: list[float] = []

    def mono(self) -> float:
        return self.t

    def wall(self) -> float:
        return self.wall0 + self.t

    def sleep(self, sec: float) -> None:
        self.slept.append(sec)
        self.t += sec


def make_client(fake: FakeSkylit, clock: FakeClock | None = None, **kw: Any) -> SkylitClient:
    clock = clock or FakeClock()
    return SkylitClient(Secret(KEY), symbols_map=SYMBOLS_MAP, transport=fake.transport(), mono=clock.mono,
                        wall=clock.wall, sleep=clock.sleep, rng=lambda: 0.5, **kw)


def sse(*events: tuple[str, dict[str, Any] | None, str | None]) -> bytes:
    """SSE bytes from (event, data, id) triples; event 'ping' becomes a comment line."""
    out = []
    for kind, data, eid in events:
        if kind == "ping":
            out.append(": ping\n\n")
            continue
        lines = [f"event: {kind}"]
        if eid:
            lines.append(f"id: {eid}")
        lines.append("data: " + json.dumps(data))
        out.append("\n".join(lines) + "\n\n")
    return "".join(out).encode()


def pass_strike_window(paths, settings, proposed=None, edge_violations=None, pulled_with=None):
    """Write the step-7 strike_window and sample_days reports so the D2 strike-window check passes (or, with the
    arguments, fails the way a test wants)."""
    from nqbot.checks import step7
    from nqbot.storage import atomic_write_json

    n = settings["skylit_max_strikes"]
    folder = step7.reports_dir(paths)
    atomic_write_json(folder / "strike_window_20000101T000000Z.json",
                      {"check": "strike_window", "proposed_skylit_max_strikes": proposed if proposed is not None else n})
    per_day = {"2026-03-02": {"edge_violations": edge_violations or {"QQQ": {"boards": 10, "violations": 0}}}}
    atomic_write_json(folder / "sample_days_20000101T000000Z.json",
                      {"check": "sample_days", "skylit_max_strikes": pulled_with if pulled_with is not None else n,
                       "per_day": per_day})
