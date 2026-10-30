"""Databento historical client: contract resolution, cost estimates and downloads (SPEC §3.2, CLAUDE.md rule 2).

A thin wrapper over `databento.Historical` (injectable, so tests pass a fake: no network, no charges).
- `resolve_front`: two free `symbology.resolve` calls, continuous (`NQ.v.0`) -> instrument_id -> raw_symbol.
- `cost`: free `metadata.get_cost` (printed before anything is bought).
- `download`: `timeseries.get_range` streamed to a temp file in the destination folder, fsynced and renamed,
  so a killed download leaves nothing partial (§11 Overnight jobs) and `mbp-1` is never held whole in RAM.
Every call (paid or failed) is reported to `on_call`, which the store appends to its ledger.
"""

from __future__ import annotations

import logging
import time as _time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from nqbot.secrets import get_secret
from nqbot.storage import atomic_path, is_device_drop, log_ssd_event, remove_stale_temps

DATASET = "GLBX.MDP3"  # §3.2
TRADES, MBP1, OHLCV_1S = "trades", "mbp-1", "ohlcv-1s"  # §3.2: the v1 schemas (L1 only, no depth)
OHLCV_1H = "ohlcv-1h"  # §3.2 Front month: CME-day volumes of the candidate contracts (owner 2026-10-30)
STYPE_RAW = "raw_symbol"


class DatabentoError(Exception):
    pass


@dataclass
class CallRecord:
    kind: str  # resolve | cost | download
    at: str
    ok: bool
    sec: float
    schema: str | None = None
    symbols: list[str] = field(default_factory=list)
    start: str | None = None
    end: str | None = None
    usd: float | None = None
    bytes: int | None = None
    error: str | None = None
    context: dict[str, Any] = field(default_factory=dict)


def utc_iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat()


def utc_ns(t: datetime) -> int:
    return int(t.astimezone(timezone.utc).timestamp()) * 1_000_000_000 + t.microsecond * 1000


class DatabentoClient:
    def __init__(self, historical: Any, *, on_call: Callable[[CallRecord], None] | None = None,
                 log: logging.Logger | None = None, dataset: str = DATASET):
        self.hist = historical
        self.on_call = on_call
        self.log = log or logging.getLogger("nqbot.databento")
        self.dataset = dataset

    @classmethod
    def from_env(cls, **kw: Any) -> DatabentoClient:
        import databento

        return cls(databento.Historical(get_secret("DATABENTO_API_KEY").reveal()), **kw)

    def _call(self, rec: CallRecord, fn: Callable[[], Any]) -> Any:
        t0 = _time.monotonic()
        try:
            out = fn()
            rec.ok = True
            return out
        except Exception as e:
            rec.error = f"{type(e).__name__}: {e}"
            raise
        finally:
            rec.sec = round(_time.monotonic() - t0, 3)
            if self.on_call is not None:
                self.on_call(rec)

    def _rec(self, kind: str, **kw: Any) -> CallRecord:
        return CallRecord(kind=kind, at=datetime.now(timezone.utc).isoformat(), ok=False, sec=0.0, **kw)

    # --- symbology (free) ------------------------------------------------------------------------------
    def _resolve(self, symbols: list[str], stype_in: str, stype_out: str, start: date, end: date) -> dict[str, Any]:
        rec = self._rec("resolve", symbols=symbols, start=start.isoformat(), end=end.isoformat(),
                        context={"stype_in": stype_in, "stype_out": stype_out})
        return self._call(rec, lambda: self.hist.symbology.resolve(
            dataset=self.dataset, symbols=symbols, stype_in=stype_in, stype_out=stype_out,
            start_date=start, end_date=end))

    def resolve_front(self, continuous: dict[str, str], start: date, end: date
                      ) -> tuple[dict[str, list[tuple[date, date, str]]], list[dict[str, Any]]]:
        """{root: [(d0, d1, raw_symbol)]} over [start, end) (UTC dates), plus the raw answers (kept on disk)."""
        first = self._resolve(list(continuous.values()), "continuous", "instrument_id", start, end)
        _check_resolve(first, list(continuous.values()))
        ids = sorted({iv["s"] for name in continuous.values() for iv in first["result"][name]})
        second = self._resolve(ids, "instrument_id", STYPE_RAW, start, end)
        # An expired contract resolves for part of the range only (Databento lists it in `partial`); coverage of
        # each continuous interval is checked below, raw symbol by raw symbol.
        _check_resolve(second, ids, partial_ok=True)
        out: dict[str, list[tuple[date, date, str]]] = {}
        for root, name in continuous.items():
            spans = []
            for iv in first["result"][name]:
                d0, d1 = date.fromisoformat(iv["d0"]), date.fromisoformat(iv["d1"])
                raws = {r["s"] for r in second["result"][iv["s"]]
                        if date.fromisoformat(r["d0"]) < d1 and date.fromisoformat(r["d1"]) > d0}
                if len(raws) != 1:
                    raise DatabentoError(f"{name} {d0}..{d1}: instrument {iv['s']} maps to {sorted(raws)}")
                spans.append((d0, d1, raws.pop()))
            out[root] = sorted(spans)
        return out, [first, second]

    # --- cost (free) -----------------------------------------------------------------------------------
    def dataset_end(self) -> datetime:
        """The end of the data Databento has published for the dataset (UTC): a day is complete once it is past it."""
        rec = self._rec("dataset_range")

        def run() -> datetime:
            end = str(self.hist.metadata.get_dataset_range(dataset=self.dataset)["end"])
            return datetime.fromisoformat(end.replace("Z", "+00:00"))

        return self._call(rec, run)

    def dataset_condition(self, start: date, end: date) -> list[dict[str, Any]]:
        """Free `metadata.get_dataset_condition` for UTC dates [start, end] (inclusive): one row per date with
        `condition` (available | degraded | pending | missing | intraday) and `last_modified_date` (§3.2)."""
        rec = self._rec("condition", start=start.isoformat(), end=end.isoformat())

        def run() -> list[dict[str, Any]]:
            return [dict(r) for r in self.hist.metadata.get_dataset_condition(
                dataset=self.dataset, start_date=start.isoformat(), end_date=end.isoformat())]

        return self._call(rec, run)

    def cost(self, schema: str, symbols: list[str], start: datetime, end: datetime) -> float:
        rec = self._rec("cost", schema=schema, symbols=symbols, start=utc_iso(start), end=utc_iso(end))

        def run() -> float:
            rec.usd = float(self.hist.metadata.get_cost(dataset=self.dataset, symbols=symbols, schema=schema,
                                                        stype_in=STYPE_RAW, start=utc_iso(start), end=utc_iso(end)))
            return rec.usd

        return self._call(rec, run)

    # --- download (may be charged) -------------------------------------------------------------------------
    def download(self, schema: str, symbol: str, start: datetime, end: datetime, path: Path,
                 **context: Any) -> int:
        """Stream one request into `path` atomically; returns the file size in bytes."""
        rec = self._rec("download", schema=schema, symbols=[symbol], start=utc_iso(start), end=utc_iso(end),
                        context=dict(context))

        def run() -> int:
            remove_stale_temps(path)  # left by a download that died on an earlier run (e.g. an unplugged SSD)
            try:
                with atomic_path(path) as tmp:
                    self.hist.timeseries.get_range(dataset=self.dataset, symbols=[symbol], schema=schema,
                                                   stype_in=STYPE_RAW, start=utc_iso(start), end=utc_iso(end),
                                                   path=tmp)
            except OSError as e:
                # logged, never re-sent here: a stream is billed for the data sent (owner 2026-10-11)
                if is_device_drop(e, path):
                    log_ssd_event("databento download", path, f"device dropped ({type(e).__name__}: {e}); not retried")
                raise
            size = path.stat().st_size
            rec.bytes = size
            return size

        return self._call(rec, run)


def _check_resolve(answer: dict[str, Any], wanted: list[str], partial_ok: bool = False) -> None:
    missing = [s for s in wanted if s not in (answer.get("result") or {})]
    if missing or answer.get("not_found"):
        raise DatabentoError(f"symbology: not resolved {missing or answer.get('not_found')}")
    if answer.get("partial") and not partial_ok:
        raise DatabentoError(f"symbology: only partly resolved {answer['partial']} (dates outside the dataset?)")


def record_dict(rec: CallRecord) -> dict[str, Any]:
    row = asdict(rec)
    context = row.pop("context") or {}
    clash = set(context) & set(row)
    if clash:
        raise ValueError(f"call context keys {sorted(clash)} would overwrite ledger fields")
    return {**row, **context}
