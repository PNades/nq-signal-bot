"""CME-day volume per contract from Databento `ohlcv-1h`, the input of the front rule (owner 2026-10-30, SPEC §3.2
Front month, §13 Q-frontroll; `databento_contracts.front_by_volume`).

`nqbot databento volumes` pulls, for every quarterly contract the rule can need, its hourly bars over the sessions
it is one of the `CANDIDATES` nearest unexpired quarterlies (the front is one of the two nearest; after a switch and
before the old contract expires the rule also needs the next quarterly of the new one). The CME day [18:00, 17:00)
ET is whole hours in UTC, so a session's volume is the sum of its hourly bars. Hourly bars must cost $0 by
`metadata.get_cost` (an OHLCV schema of the Standard plan); a priced request is refused, never bought.

Layout under <paths.raw_dir>/databento/:
  ohlcv-1h/<raw_symbol>/<first CME day>_<last CME day>.dbn.zst   one request as delivered; never re-pulled
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from nqbot import calendar as cal_mod
from nqbot.clients import databento_cost as cost_mod
from nqbot.clients.databento_client import OHLCV_1H, DatabentoClient, utc_ns
from nqbot.clients.databento_contracts import (Roll, cme_day_span, cme_roll_date, session_before,
                                               unexpired_quarterlies)
from nqbot.clients.databento_store import DatabentoStore, read_metadata, read_records

ROOTS = ("NQ", "ES")  # §3.2, §3.3; MNQ follows the NQ contract
# The front is one of the two nearest unexpired quarterlies; between a switch and the old contract's expiry the
# rule compares the new front with its own next quarterly, the third nearest.
CANDIDATES = 3


class VolumesRefused(Exception):
    pass


def volume_sessions(days: Sequence[date], cal: dict[str, Any] | None = None) -> list[date]:
    """The sessions whose volumes decide `days`: the session before the first day, then every session in `days`."""
    days = sorted(days)
    return [session_before(days[0], cal)] + [d for d in days if cal_mod.is_trading_day(d, cal)]


def contract_sessions(sessions: Sequence[date]) -> dict[str, dict[str, list[date]]]:
    """{root: {raw symbol: [sessions it is a candidate on]}}."""
    out: dict[str, dict[str, list[date]]] = {}
    for root in ROOTS:
        per: dict[str, list[date]] = {}
        for s in sorted(sessions):
            for raw in unexpired_quarterlies(root, s, CANDIDATES):
                per.setdefault(raw, []).append(s)
        out[root] = per
    return out


def volume_dir(store: DatabentoStore, raw: str) -> Path:
    return store.root / OHLCV_1H / raw


def volume_path(store: DatabentoStore, raw: str, first: date, last: date) -> Path:
    return volume_dir(store, raw) / f"{first.isoformat()}_{last.isoformat()}.dbn.zst"


def _covered(store: DatabentoStore, raw: str) -> list[tuple[int, int]]:
    """[start ns, end ns) of every stored hourly file of `raw` (from its own DBN metadata)."""
    folder = volume_dir(store, raw)
    if not folder.exists():
        return []
    out = []
    for p in sorted(folder.glob("*.dbn.zst")):
        md = read_metadata(p)
        out.append((int(md.start), int(md.end)))
    return out


def _session_ns(day: date) -> tuple[int, int]:
    a, b = cme_day_span(day)
    return utc_ns(a), utc_ns(b)


def _is_covered(spans: list[tuple[int, int]], day: date) -> bool:
    a, b = _session_ns(day)
    return any(s <= a and b <= e for s, e in spans)


@dataclass(frozen=True)
class VolumeRequest:
    root: str
    raw: str
    first: date
    last: date

    def span(self) -> tuple[datetime, datetime]:
        return cme_day_span(self.first)[0], cme_day_span(self.last)[1]


def missing_requests(store: DatabentoStore, sessions: Sequence[date]) -> list[VolumeRequest]:
    """One request per contract, from its first to its last candidate session not yet covered by a stored file."""
    out = []
    for root, per in contract_sessions(sessions).items():
        for raw, days in sorted(per.items()):
            spans = _covered(store, raw)
            todo = [d for d in days if not _is_covered(spans, d)]
            if todo:
                out.append(VolumeRequest(root, raw, todo[0], todo[-1]))
    return out


def pull_volumes(client: DatabentoClient, store: DatabentoStore, days: Sequence[date], *,
                 cal: dict[str, Any] | None = None, echo: Callable[[str], None] = print) -> list[Path]:
    """Print the get_cost estimate of the hourly bars still missing, then pull them (all must be $0)."""
    todo = missing_requests(store, volume_sessions(days, cal))
    if not todo:
        echo("hourly volumes: all stored")
        return []
    est = cost_mod.Estimate(f"front-rule volumes, {len(todo)} {OHLCV_1H} requests")
    priced = []
    for rq in todo:
        usd = client.cost(OHLCV_1H, [rq.raw], *rq.span())
        est.add(f"{rq.root} {OHLCV_1H} {rq.raw} {rq.first}..{rq.last}", len(cal_sessions(rq, cal)), usd, usd > 0)
        if usd > 0:
            priced.append(rq.raw)
    echo(est.text())
    if priced:
        raise VolumesRefused(f"get_cost prices the hourly bars of {', '.join(priced)}: not bought (they should be $0 "
                             "with the Standard plan); ask the owner")
    out = []
    for rq in todo:
        path = volume_path(store, rq.raw, rq.first, rq.last)
        size = client.download(OHLCV_1H, rq.raw, *rq.span(), path, item="front_volumes", payg=False)
        echo(f"  {rq.raw} {rq.first}..{rq.last}: {size / 1e3:.1f} kB")
        out.append(path)
    return out


def cal_sessions(rq: VolumeRequest, cal: dict[str, Any] | None) -> list[date]:
    d, out = rq.first, []
    while d <= rq.last:
        if cal_mod.is_trading_day(d, cal):
            out.append(d)
        d += timedelta(days=1)
    return out


def load_volumes(store: DatabentoStore, sessions: Sequence[date]) -> dict[str, dict[str, dict[date, int]]]:
    """{root: {raw: {session: CME-day volume}}} for every candidate session a stored file covers (a session with no
    hourly bar in a covering file has volume 0); an uncovered session is left out (front_by_volume refuses it)."""
    out: dict[str, dict[str, dict[date, int]]] = {}
    for root, per in contract_sessions(sessions).items():
        out[root] = {}
        for raw, days in per.items():
            folder = volume_dir(store, raw)
            vols: dict[date, int] = {}
            for p in sorted(folder.glob("*.dbn.zst")) if folder.exists() else []:
                md, a = read_metadata(p), read_records(p)
                ts = np.asarray(a["ts_event"], dtype=np.int64) if len(a) else np.zeros(0, dtype=np.int64)
                order = np.argsort(ts, kind="stable")
                ts, cum = ts[order], np.concatenate([[0], np.cumsum(np.asarray(a["volume"], dtype=np.int64)[order])
                                                     if len(a) else []]).astype(np.int64)
                for d in days:
                    s, e = _session_ns(d)
                    if int(md.start) <= s and e <= int(md.end):
                        i, j = np.searchsorted(ts, s, side="left"), np.searchsorted(ts, e, side="left")
                        vols[d] = int(cum[j] - cum[i])
            out[root][raw] = vols
    return out


def roll_table(rolls: Sequence[Roll], volumes: dict[str, dict[str, dict[date, int]]]) -> list[str]:
    """One line per roll: the first session on the new contract, the deciding session's volumes, CME's roll date."""
    rows = []
    for r in rolls:
        v = volumes.get(r.root) or {}
        old_v, new_v = (v.get(r.old) or {}).get(r.prev_session), (v.get(r.new) or {}).get(r.prev_session)
        rows.append(f"roll {r.root}: {r.old} -> {r.new} from {r.roll_day} (decided on {r.prev_session}: "
                    f"{r.new} {new_v:,} > {r.old} {old_v:,}; CME roll date {cme_roll_date(r.old, r.root, r.roll_day)})")
    return rows
