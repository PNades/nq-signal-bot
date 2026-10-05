"""Databento data on the SSD: raw DBN files, the never-re-pull cache and the call ledger (SPEC §11 Storage).

Layout under <paths.raw_dir>/databento/:
  <schema>/<raw_symbol>/<YYYY-MM-DD>.dbn.zst   one request as delivered (lossless, zstd DBN); the date is the
                                              CME trading day the request belongs to
  symbology/<pulled>.json                     the symbology.resolve answers the plan was built from
  ledger.jsonl                                one line per call (resolve, cost, download; failed ones too)
Processed (under <paths.data_dir>/processed/):
  bars/<ROOT>/<YYYY-MM-DD>_{1m,5m}.parquet    1- and 5-minute bars built from the session contract's trades
  rolls/<ROOT>_<roll day>.json                the roll spread measured at the previous session's 17:00 ET

A file is reused only if its own DBN metadata (dataset, schema, symbols, stype_in, start, end) matches the
request; a file pulled with different parameters raises CacheMismatch (never re-pulled or overwritten silently).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from nqbot.clients.databento_client import CallRecord, record_dict, utc_ns
from nqbot.clients.skylit_store import CacheMismatch
from nqbot.storage import Paths, append_jsonl, atomic_write_json, read_jsonl

__all__ = ["CacheMismatch", "DatabentoStore"]


class DatabentoStore:
    def __init__(self, paths: Paths):
        self.paths = paths
        self.root = paths.raw_dir / "databento"
        self.processed = paths.data_dir / "processed"

    # ledger
    def log_call(self, rec: CallRecord) -> None:
        append_jsonl(self.root / "ledger.jsonl", record_dict(rec))

    def ledger(self) -> list[dict[str, Any]]:
        return read_jsonl(self.root / "ledger.jsonl")

    def save_symbology(self, answers: list[dict[str, Any]]) -> Path:
        path = self.root / "symbology" / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.json"
        atomic_write_json(path, answers)
        return path

    # raw files
    def raw_path(self, schema: str, symbol: str, day: date) -> Path:
        return self.root / schema / symbol / f"{day.isoformat()}.dbn.zst"

    def cached(self, schema: str, symbol: str, day: date, start: datetime, end: datetime, dataset: str) -> bool:
        path = self.raw_path(schema, symbol, day)
        if not path.exists():
            return False
        md = read_metadata(path)
        want = {"dataset": dataset, "schema": schema, "symbols": [symbol], "stype_in": "raw_symbol",
                "start": utc_ns(start), "end": utc_ns(end)}
        have = {"dataset": md.dataset, "schema": str(md.schema), "symbols": list(md.symbols),
                "stype_in": str(md.stype_in), "start": int(md.start), "end": int(md.end)}
        if have != want:
            raise CacheMismatch(f"{path} was pulled with {have}, not {want}: never re-pulled silently")
        return True

    # processed
    def bars_path(self, root: str, day: date, minutes: int) -> Path:
        return self.processed / "bars" / root / f"{day.isoformat()}_{minutes}m.parquet"

    def roll_path(self, root: str, roll_day: date) -> Path:
        return self.processed / "rolls" / f"{root}_{roll_day.isoformat()}.json"

    def roll_error_path(self, root: str, roll_day: date) -> Path:
        return self.processed / "rolls" / "errors" / f"{root}_{roll_day.isoformat()}.json"

    def rolls(self) -> list[dict[str, Any]]:
        folder = self.processed / "rolls"
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))] if folder.exists() else []

    def roll_errors(self) -> list[dict[str, Any]]:
        """Rolls whose spread couldn't be measured: the roll day can't be shifted (F0.4) until this is resolved."""
        folder = self.processed / "rolls" / "errors"
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))] if folder.exists() else []


def read_metadata(path: Path) -> Any:
    from databento import DBNStore

    return DBNStore.from_file(path).metadata


def read_records(path: Path) -> np.ndarray:
    """All records of a stored file as a numpy structured array (fixed-point prices, 1e-9)."""
    from databento import DBNStore

    # databento 0.87 returns an empty file as shape (0, 1): always hand back a flat array.
    return DBNStore.from_file(path).to_ndarray().reshape(-1)
