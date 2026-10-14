"""Per-map board freshness (owner 2026-10-14, SPEC §9 Skylit failure, §3.1 Ingest, §13 Q-mapstale). Live only.

The Skylit stream drops board updates per map (10-12 12:30-12:45 ET: NDXP 249 and SPY 82 changed boards that history
holds were never sent; `lagged` never true, our reader kept up), and §9's `skylit_stale_sec` counts any event on the
connection, so one map can go silent unnoticed while pings and the other maps arrive. A map is **stale** while its
newest board arrived more than `skylit_map_stale_sec` ago (local arrival clock):

- no new entries on a zone with a level from a stale map (other maps trade on);
- pending entries on such a zone stay (they were placed on a fresh map, bracketed), unless the map stays stale past
  `skylit_stale_sec`: then the feed-failure rule (§9: pending entries removed) applies to that map;
- every block is logged (map, start, length) and counted per session.

History holds a board every second, so the backtest never sees a stale map: a known live-vs-backtest difference
(SPEC §9). The recording report counts blocks with this same class over the recorded arrivals (one code path).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

NS = 1_000_000_000
log = logging.getLogger("nqbot.live.map_freshness")


@dataclass(frozen=True)
class Block:
    map: str
    start_ns: int  # newest board's arrival + stale_s
    end_ns: int  # the next board's arrival (or the session end)

    @property
    def length_s(self) -> float:
        return (self.end_ns - self.start_ns) / NS


class MapFreshness:
    """Feed it every board's arrival (`on_board`) and the clock (`poll`); ask `entry_allowed` / `keep_pending`."""

    def __init__(self, maps: Iterable[str], stale_s: float, feed_fail_s: float, session_start_ns: int):
        if not 0 < stale_s < feed_fail_s:
            raise ValueError(f"need 0 < skylit_map_stale_sec ({stale_s}) < skylit_stale_sec ({feed_fail_s})")
        self.stale_ns = int(stale_s * NS)
        self.fail_ns = int(feed_fail_s * NS)
        self.last: dict[str, int] = {m: session_start_ns for m in maps}  # no board yet: counted from the start
        self.open: dict[str, int] = {}  # map -> block start
        self.blocks: list[Block] = []

    def on_board(self, map_: str, arrival_ns: int) -> Block | None:
        """A board arrived: ends the map's block if it had one (logged), returns it."""
        prev = self.last.get(map_)
        self.last[map_] = arrival_ns
        if prev is None or arrival_ns - prev <= self.stale_ns:
            self.open.pop(map_, None)
            return None
        block = Block(map_, self.open.pop(map_, prev + self.stale_ns), arrival_ns)
        self.blocks.append(block)
        log.info("map %s stale for %.1f s (no board for %.1f s): entries on its zones were blocked", map_,
                 block.length_s, (arrival_ns - prev) / NS)
        return block

    def poll(self, now_ns: int) -> list[str]:
        """The maps stale now; a map that just turned stale opens a block (logged)."""
        stale = []
        for m, t in self.last.items():
            if now_ns - t > self.stale_ns:
                stale.append(m)
                if m not in self.open:
                    self.open[m] = t + self.stale_ns
                    log.warning("map %s stale: no board for %.1f s, new entries on its zones blocked", m,
                                (now_ns - t) / NS)
        return stale

    def stale(self, map_: str, now_ns: int) -> bool:
        return now_ns - self.last.get(map_, now_ns - self.stale_ns - 1) > self.stale_ns

    def failed(self, map_: str, now_ns: int) -> bool:
        """Stale past skylit_stale_sec: the feed-failure rule applies to this map (§9)."""
        return now_ns - self.last.get(map_, now_ns - self.fail_ns - 1) > self.fail_ns

    def entry_allowed(self, zone_maps: Iterable[str], now_ns: int) -> bool:
        """A new entry on a zone whose levels come from these maps (chart levels: none) is allowed now."""
        return not any(self.stale(m, now_ns) for m in zone_maps)

    def keep_pending(self, zone_maps: Iterable[str], now_ns: int) -> bool:
        """A pending entry on this zone stays unless one of its maps has failed (stale past skylit_stale_sec)."""
        return not any(self.failed(m, now_ns) for m in zone_maps)

    def end_session(self, end_ns: int) -> None:
        """Close the blocks still open at the session end."""
        for m, t in self.last.items():
            if end_ns - t > self.stale_ns:
                self.blocks.append(Block(m, self.open.pop(m, t + self.stale_ns), end_ns))
        self.open.clear()

    def summary(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {m: {"blocks": 0, "total_s": 0.0, "longest_s": 0.0} for m in self.last}
        for b in self.blocks:
            s = out.setdefault(b.map, {"blocks": 0, "total_s": 0.0, "longest_s": 0.0})
            s["blocks"] += 1
            s["total_s"] = round(s["total_s"] + b.length_s, 3)
            s["longest_s"] = round(max(s["longest_s"], b.length_s), 3)
        return out


def blocks_from_arrivals(arrivals: Iterable[tuple[str, int]], maps: Iterable[str], stale_s: float,
                         feed_fail_s: float, start_ns: int, end_ns: int) -> MapFreshness:
    """Replay recorded (map, arrival_ns) pairs through MapFreshness, in arrival order (the recording report)."""
    f = MapFreshness(maps, stale_s, feed_fail_s, start_ns)
    for m, t in sorted(arrivals, key=lambda x: x[1]):
        if start_ns <= t <= end_ns:
            f.on_board(m, t)
    f.end_session(end_ns)
    return f
