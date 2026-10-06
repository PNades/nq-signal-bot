"""Skylit data on the SSD: zstd parquet tables, the never-re-pull cache and the call ledger (SPEC §11 Storage).

Layout under <paths.raw_dir>/skylit/:
  range/<YYYY-MM-DD>/<HHMM>_boards.parquet   one row per board: as_of, symbol, api_symbol, spot, previous_close,
  range/<YYYY-MM-DD>/<HHMM>_strikes.parquet  resolution, axis_id, expirations, n_strikes, window_from/to;
                                             one row per strike: as_of, symbol, strike, value (float32)
  historical/<YYYYMMDDTHHMMSSZ>.parquet      /v1/historical instants (Skylit's nodeType kept, step-12 report)
  darkpool/trades/<YYYY-MM-DD>.parquet       every print of one ET trade date for the requested tickers
  darkpool/top_prints/<TICKER>_<YYYY-MM-DD>.parquet
  vol/history_<from>_<to>_<pulled>.parquet   Tempest daily rows (one per symbol and session)
  stream/<YYYY-MM-DD>/<HHMMSS>_{boards,strikes}.parquet + _events.jsonl   stream recordings (step 7)
  fullchain/range/<YYYY-MM-DD>/...           step-7 strike-window pulls (maxStrikes=all), same layout as range/
  repull/<reason>/<pulled>/...               the CLAUDE.md rule-2 re-pull exceptions, never over the original
  ledger.jsonl                               one line per HTTP call (credits, bytes, status, re-pull reason)
  ledger_<check>.jsonl                       the same, for each step-7 check that runs alongside the backfill without
                                             the Skylit lock (own files only, decision 2); ledger() reads them all

HHMM is the window's start in ET. The request parameters, response meta, pull time and credits sit in
each parquet's key-value metadata; a cached window is reused only if its parameters are identical.
"""

from __future__ import annotations

import io
import json
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from nqbot.clients.skylit import CallRecord
from nqbot.storage import Paths, append_jsonl, atomic_write_bytes, read_jsonl, replace_retry

META_KEY = b"nqbot"
COMPRESSION = "zstd"  # §11 Storage
REPULL_REASONS = frozenset({
    "parity",              # step-7 stream vs history parity (§3.1 Ingest)
    "late_print",          # step-7 dark-pool late-print check
    "tempest_recheck",     # step 7/22: do Tempest rows change on later pulls
    "strike_window_raise", # a step-7 sample day if skylit_max_strikes is raised
    "partial_write",       # a window whose two files weren't both written (crash between renames)
})
UTC_MS = pa.timestamp("ms", tz="UTC")
UTC_NS = pa.timestamp("ns", tz="UTC")

BOARD_SCHEMA = pa.schema([
    ("as_of", UTC_MS), ("symbol", pa.string()), ("api_symbol", pa.string()), ("spot", pa.float64()),
    ("previous_close", pa.float64()), ("resolution", pa.string()), ("axis_id", pa.int32()),
    ("expirations", pa.string()), ("n_strikes", pa.int32()), ("window_from", UTC_MS), ("window_to", UTC_MS),
])
STRIKE_SCHEMA = pa.schema([("as_of", UTC_MS), ("symbol", pa.string()), ("strike", pa.float32()), ("value", pa.float32())])
STREAM_BOARD_SCHEMA = pa.schema([
    ("as_of", UTC_MS), ("arrival", UTC_NS), ("symbol", pa.string()), ("api_symbol", pa.string()),
    ("spot", pa.float64()), ("previous_close", pa.float64()), ("price_change", pa.float64()),
    ("expirations", pa.string()), ("n_strikes", pa.int32()), ("lagged", pa.bool_()), ("event_id", pa.string()),
    ("connection", pa.int32()),
])
HISTORICAL_SCHEMA = pa.schema([
    ("as_of", UTC_MS), ("requested_at", UTC_MS), ("symbol", pa.string()), ("api_symbol", pa.string()),
    ("spot", pa.float64()), ("previous_close", pa.float64()), ("expirations", pa.string()), ("resolution", pa.string()),
    ("strike", pa.float32()), ("value", pa.float32()), ("node_type", pa.string()),
])
DP_SCHEMA = pa.schema([
    ("timestamp", UTC_MS), ("ticker", pa.string()), ("price", pa.float64()), ("size", pa.int64()),
    ("notional", pa.float64()), ("venue", pa.string()), ("pct_avg_vol", pa.float64()),
])


class CacheMismatch(Exception):
    """A cached file exists but was pulled with different parameters: never silently reuse or overwrite."""


def ms(ts: str) -> int:
    """RFC3339 (…Z, any sub-second precision) -> integer ms since the epoch, exactly (no float rounding)."""
    return int(np.datetime64(ts.replace("Z", "").replace("+00:00", ""), "ms").astype(np.int64))


def utc_ms(t: datetime) -> int:
    return int(np.datetime64(t.astimezone(timezone.utc).replace(tzinfo=None), "ms").astype(np.int64))


# --- conversion (pure: response JSON -> arrow tables) -----------------------------------------------

def range_to_tables(body: dict[str, Any], root_to_map: dict[str, str], window_from: datetime,
                    window_to: datetime) -> tuple[pa.Table, pa.Table]:
    """One /v1/historical/range response -> (boards, strikes). Values are aligned with each frame's axis."""
    resolution = body["meta"]["resolution"]
    wf, wt = utc_ms(window_from), utc_ms(window_to)
    b: dict[str, list[Any]] = {name: [] for name in BOARD_SCHEMA.names}
    s_asof: list[np.ndarray] = []
    s_sym: list[str] = []
    s_counts: list[int] = []
    s_strike: list[np.ndarray] = []
    s_value: list[np.ndarray] = []
    for sym in body["data"]["symbols"]:
        api_symbol = sym["symbol"]
        name = _map_name(api_symbol, root_to_map)
        axes = {a["id"]: (np.asarray(a["strikes"], dtype=np.float64), ",".join(a["expirations"])) for a in sym["axes"]}
        for frame in sym["frames"]:
            strikes, expirations = axes[frame["axis"]]
            values = np.asarray(frame["values"], dtype=np.float64)
            if len(values) != len(strikes):
                raise ValueError(f"{api_symbol} {frame['asOf']}: {len(values)} values for {len(strikes)} strikes")
            t = ms(frame["asOf"])
            for key, val in (("as_of", t), ("symbol", name), ("api_symbol", api_symbol), ("spot", frame["spot"]),
                             ("previous_close", frame["previousClose"]), ("resolution", resolution),
                             ("axis_id", frame["axis"]), ("expirations", expirations), ("n_strikes", len(strikes)),
                             ("window_from", wf), ("window_to", wt)):
                b[key].append(val)
            s_asof.append(np.full(len(strikes), t, dtype=np.int64))
            s_sym.append(name)
            s_counts.append(len(strikes))
            s_strike.append(strikes)
            s_value.append(values)
    boards = pa.Table.from_pydict(b, schema=BOARD_SCHEMA)
    strikes_tbl = _strike_table(s_asof, s_sym, s_counts, s_strike, s_value)
    return boards, strikes_tbl


def _strike_table(asof: list[np.ndarray], syms: list[str], counts: list[int], strikes: list[np.ndarray],
                  values: list[np.ndarray]) -> pa.Table:
    if not asof:
        return STRIKE_SCHEMA.empty_table()
    symbol = pa.DictionaryArray.from_arrays(
        pa.array(np.repeat(np.arange(len(syms), dtype=np.int32), counts)), pa.array(syms)
    ).cast(pa.string())
    return pa.Table.from_arrays([
        pa.array(np.concatenate(asof), type=pa.int64()).cast(UTC_MS),
        symbol,
        pa.array(np.concatenate(strikes).astype(np.float32)),
        pa.array(np.concatenate(values).astype(np.float32)),
    ], schema=STRIKE_SCHEMA)


def historical_to_table(body: dict[str, Any], root_to_map: dict[str, str], requested_at: datetime) -> pa.Table:
    rows: dict[str, list[Any]] = {name: [] for name in HISTORICAL_SCHEMA.names}
    req = utc_ms(requested_at)
    for sym in body["data"]["symbols"]:
        for node in sym["strikes"]:
            for key, val in (("as_of", ms(sym["asOf"])), ("requested_at", req), ("symbol", _map_name(sym["symbol"], root_to_map)),
                             ("api_symbol", sym["symbol"]), ("spot", sym["spot"]), ("previous_close", sym["previousClose"]),
                             ("expirations", ",".join(sym["expirations"])), ("resolution", body["meta"]["resolution"]),
                             ("strike", node["strike"]), ("value", node["value"]), ("node_type", node.get("nodeType"))):
                rows[key].append(val)
    return pa.Table.from_pydict(rows, schema=HISTORICAL_SCHEMA)


def stream_snapshots_to_tables(snaps: Iterable[dict[str, Any]], root_to_map: dict[str, str]) -> tuple[pa.Table, pa.Table]:
    """Recorded stream snapshots (payload + arrival_ns + event_id + connection) -> (boards, strikes)."""
    b: dict[str, list[Any]] = {name: [] for name in STREAM_BOARD_SCHEMA.names}
    s_asof, s_sym, s_counts, s_strike, s_value = [], [], [], [], []
    for snap in snaps:
        p = snap["payload"]
        name = _map_name(p["symbol"], root_to_map)
        strikes = np.asarray([n["strike"] for n in p["strikes"]], dtype=np.float64)
        values = np.asarray([n["value"] for n in p["strikes"]], dtype=np.float64)
        t = ms(p["asOf"])
        for key, val in (("as_of", t), ("arrival", snap["arrival_ns"]), ("symbol", name), ("api_symbol", p["symbol"]),
                         ("spot", p["spot"]), ("previous_close", p["previousClose"]), ("price_change", p.get("priceChange")),
                         ("expirations", ",".join(p.get("expirations") or [])), ("n_strikes", len(strikes)),
                         ("lagged", bool(p.get("lagged"))), ("event_id", snap.get("event_id")),
                         ("connection", snap.get("connection", 0))):
            b[key].append(val)
        s_asof.append(np.full(len(strikes), t, dtype=np.int64))
        s_sym.append(name)
        s_counts.append(len(strikes))
        s_strike.append(strikes)
        s_value.append(values)
    return pa.Table.from_pydict(b, schema=STREAM_BOARD_SCHEMA), _strike_table(s_asof, s_sym, s_counts, s_strike, s_value)


def prints_to_table(prints: list[dict[str, Any]]) -> pa.Table:
    return pa.Table.from_pydict({
        "timestamp": [ms(p["timestamp"]) for p in prints],
        "ticker": [p["ticker"] for p in prints],
        "price": [float(p["price"]) for p in prints],
        "size": [int(p["size"]) for p in prints],
        "notional": [float(p["notional"]) for p in prints],
        "venue": [p.get("venue") for p in prints],
        "pct_avg_vol": [p.get("pctAvgVol") for p in prints],
    }, schema=DP_SCHEMA)


def top_prints_to_table(ticker: str, rows: list[dict[str, Any]]) -> pa.Table:
    return prints_to_table([{**r, "ticker": ticker} for r in rows])


def vol_history_to_table(body: dict[str, Any]) -> pa.Table:
    """Tempest's columnar answer -> one row per (symbol, session date), one column per field (float64)."""
    fields = sorted({f for sym in body["data"]["symbols"] for f in sym["columns"]})
    out: dict[str, list[Any]] = {"symbol": [], "date": [], **{f: [] for f in fields}}
    for sym in body["data"]["symbols"]:
        n = len(sym["dates"])
        out["symbol"] += [sym["symbol"]] * n
        out["date"] += [date.fromisoformat(d) for d in sym["dates"]]
        for f in fields:
            col = sym["columns"].get(f) or [None] * n
            out[f] += [None if v is None else float(v) for v in col]
    schema = pa.schema([("symbol", pa.string()), ("date", pa.date32()), *[(f, pa.float64()) for f in fields]])
    return pa.Table.from_pydict(out, schema=schema)


def _map_name(api_symbol: str, root_to_map: dict[str, str]) -> str:
    try:
        return root_to_map[api_symbol]
    except KeyError:
        raise ValueError(f"response symbol {api_symbol!r} is not one of the requested roots {sorted(root_to_map)}") from None


# --- files --------------------------------------------------------------------------------------------

def write_parquet(path: Path, table: pa.Table, meta: dict[str, Any]) -> None:
    table = table.replace_schema_metadata({**(table.schema.metadata or {}), META_KEY: json.dumps(meta, sort_keys=True, default=str).encode()})
    buf = io.BytesIO()
    pq.write_table(table, buf, compression=COMPRESSION)
    atomic_write_bytes(path, buf.getvalue())


def read_meta(path: Path) -> dict[str, Any]:
    md = pq.read_schema(path).metadata or {}
    return json.loads(md[META_KEY]) if META_KEY in md else {}


LEDGER = "ledger.jsonl"


class SkylitStore:
    def __init__(self, paths: Paths, ledger_name: str = LEDGER):
        self.paths = paths
        self.root = paths.raw_dir / "skylit"
        self.ledger_name = ledger_name  # a lock-free step-7 check writes its own ledger file (decision 2)

    def base(self, repull: str | None = None, pulled: str | None = None) -> Path:
        if repull is None:
            return self.root
        if repull not in REPULL_REASONS:
            raise ValueError(f"unknown re-pull reason {repull!r}; allowed: {sorted(REPULL_REASONS)} (CLAUDE.md rule 2)")
        return self.root / "repull" / repull / (pulled or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))

    # ledger
    def log_call(self, rec: CallRecord, **extra: Any) -> None:
        """Append one finished call (wired as the client's on_call, so every paid or failed call is logged)."""
        row = asdict(rec)
        row.update(row.pop("context") or {})
        append_jsonl(self.root / self.ledger_name, {**row, **extra})

    def ledger(self) -> list[dict[str, Any]]:
        """Every call of every ledger file (the main one and the alongside checks' own), oldest first."""
        files = sorted(self.root.glob("ledger*.jsonl")) if self.root.exists() else []
        rows = [r for f in files for r in read_jsonl(f)]
        return sorted(rows, key=lambda r: r.get("at") or "") if len(files) > 1 else rows

    # range windows
    def range_paths(self, day: date, hhmm: str, base: Path | None = None) -> tuple[Path, Path]:
        folder = (base or self.root) / "range" / day.isoformat()
        return folder / f"{hhmm}_boards.parquet", folder / f"{hhmm}_strikes.parquet"

    def range_cached(self, day: date, hhmm: str, params: dict[str, Any], base: Path | None = None) -> bool:
        """True if this window is stored with identical parameters; CacheMismatch if stored differently.

        A window with only one of its two files (a crash between the two renames) is not cached; the caller
        re-pulls it under the `partial_write` reason.
        """
        boards, strikes = self.range_paths(day, hhmm, base)
        if not boards.exists() and not strikes.exists():
            return False
        if boards.exists() != strikes.exists():
            return False
        for path in (boards, strikes):
            stored = read_meta(path).get("params")
            if stored != _norm(params):
                raise CacheMismatch(f"{path} was pulled with {stored}, not {_norm(params)}: never re-pulled silently")
        return True

    def range_partial(self, day: date, hhmm: str, base: Path | None = None) -> bool:
        boards, strikes = self.range_paths(day, hhmm, base)
        return boards.exists() != strikes.exists()

    def range_n_boards(self, day: date, hhmm: str, base: Path | None = None) -> int:
        """Boards in a cached window (0 for a stored empty probe), from the boards file's own row count."""
        return pq.ParquetFile(self.range_paths(day, hhmm, base)[0]).metadata.num_rows

    def quarantine_partial(self, day: date, hhmm: str, base: Path | None = None) -> Path | None:
        """Move the orphan file of an interrupted window write aside (never overwritten or deleted)."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        for path in self.range_paths(day, hhmm, base):
            if path.exists():
                dest = self.base("partial_write", pulled=stamp) / "orphans" / day.isoformat() / path.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                replace_retry(path, dest)
                return dest
        return None

    def save_failed(self, raw: bytes, label: str) -> Path:
        """Keep the body of a charged answer that couldn't be decoded or converted, so it is never paid for twice."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = self.root / "failed" / f"{label}_{stamp}.json"
        atomic_write_bytes(path, raw)
        return path

    def write_range(self, day: date, hhmm: str, boards: pa.Table, strikes: pa.Table, meta: dict[str, Any],
                    base: Path | None = None) -> tuple[Path, Path]:
        bpath, spath = self.range_paths(day, hhmm, base)
        meta = {**meta, "params": _norm(meta["params"])}
        write_parquet(spath, strikes, meta)
        write_parquet(bpath, boards, meta)  # boards last: both exist only once both are complete
        return bpath, spath

    def read_range(self, day: date, base: Path | None = None) -> tuple[pa.Table, pa.Table]:
        folder = (base or self.root) / "range" / day.isoformat()
        boards = sorted(folder.glob("*_boards.parquet"))
        strikes = sorted(folder.glob("*_strikes.parquet"))
        bt = pa.concat_tables([pq.read_table(p) for p in boards]) if boards else BOARD_SCHEMA.empty_table()
        st = pa.concat_tables([pq.read_table(p) for p in strikes]) if strikes else STRIKE_SCHEMA.empty_table()
        return bt.replace_schema_metadata(None), st.replace_schema_metadata(None)

    def stored_windows(self, day: date, base: Path | None = None) -> list[str]:
        folder = (base or self.root) / "range" / day.isoformat()
        return sorted(p.name.split("_")[0] for p in folder.glob("*_boards.parquet")) if folder.exists() else []

    # other tables
    @property
    def fullchain(self) -> Path:
        """Step-7 strike-window pulls (maxStrikes=all): their own tree, so they never meet the normal windows' cache."""
        return self.root / "fullchain"

    def historical_path(self, at: datetime, base: Path | None = None) -> Path:
        return (base or self.root) / "historical" / f"{at.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}.parquet"

    def dp_trades_path(self, day: date, base: Path | None = None) -> Path:
        return (base or self.root) / "darkpool" / "trades" / f"{day.isoformat()}.parquet"

    def dp_top_path(self, ticker: str, as_of: date, base: Path | None = None) -> Path:
        return (base or self.root) / "darkpool" / "top_prints" / f"{ticker}_{as_of.isoformat()}.parquet"

    def vol_path(self, start: date, end: date, pulled: str, base: Path | None = None) -> Path:
        return (base or self.root) / "vol" / f"history_{start.isoformat()}_{end.isoformat()}_{pulled}.parquet"

    def vol_files(self, start: date | None = None, end: date | None = None) -> list[Path]:
        folder = self.root / "vol"
        files = sorted(folder.glob("history_*.parquet")) if folder.exists() else []
        if start and end:
            files = [p for p in files if p.name.startswith(f"history_{start.isoformat()}_{end.isoformat()}_")]
        return files

    def stream_dir(self, session: date) -> Path:
        return self.root / "stream" / session.isoformat()


def cached_or_mismatch(path: Path, params: dict[str, Any]) -> bool:
    """For single-file caches (instants, dark-pool dates, top prints)."""
    if not path.exists():
        return False
    stored = read_meta(path).get("params")
    if stored != _norm(params):
        raise CacheMismatch(f"{path} was pulled with {stored}, not {_norm(params)}: never re-pulled silently")
    return True


def _norm(params: dict[str, Any]) -> dict[str, Any]:
    """JSON round trip so stored and requested parameters compare equal (ints vs strings, tuples vs lists)."""
    return json.loads(json.dumps({k: v for k, v in params.items() if v is not None}, sort_keys=True, default=str))
