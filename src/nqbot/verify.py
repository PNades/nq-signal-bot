"""`nqbot verify` (owner 2026-10-11, after the SSD kept dropping off the USB bus): read back every file written since
a date and list anything damaged. Reads only; writes its report to <reports_dir>/verify/.

Per file type: parquet read in full; Databento `.dbn.zst` decompressed in full (a cut-off or corrupt zstd stream
fails) and its size checked against the size the download ledger recorded; json/jsonl parsed (a leading UTF-8 BOM
accepted, owner 2026-10-31: two PowerShell-written study files were flagged for it); npz arrays loaded;
text decoded as UTF-8 or Windows-1252 and free of zero-byte runs (owner 2026-10-31: pytest logs written in the
console code page held a valid `§` = 0xA7 and were flagged; lost sectors read back as zeros). A read that fails because the drive dropped is retried after the drive comes back
(VERIFY_DROP_TRIES); a file still unreadable then is listed apart from a damaged one."""

from __future__ import annotations

import codecs
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

VERIFY_DROP_TRIES = 3  # reads of one file when the drive drops mid-read
VERIFY_DROP_WAIT_SEC = 60  # how long to wait for the drive to come back before a retry
READ_CHUNK = 1 << 22
TEXT_EXT = (".md", ".txt", ".log", ".html", ".csv")
ZERO_RUN = b"\0" * 16  # owner 2026-10-31: this many zero bytes in a row in a text file = damage (a lost sector)


@dataclass
class VerifyResult:
    checked: int = 0
    bytes: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    damaged: list[dict[str, Any]] = field(default_factory=list)
    unreadable: list[dict[str, Any]] = field(default_factory=list)
    size_mismatch: list[dict[str, Any]] = field(default_factory=list)
    drops_seen: int = 0


def _kind(p: Path) -> str:
    n = p.name
    if n.endswith(".dbn.zst"):
        return "dbn.zst"
    if n.endswith(".parquet"):
        return "parquet"
    if n.endswith(".jsonl"):
        return "jsonl"
    if n.endswith(".json"):
        return "json"
    if n.endswith(".npz"):
        return "npz"
    if n.endswith(TEXT_EXT):
        return "text"
    return "other"


def check_file(p: Path) -> None:
    """Raise if the file can't be read back in full as its type."""
    kind = _kind(p)
    if kind == "parquet":
        import pyarrow.parquet as pq

        f = pq.ParquetFile(p)
        for i in range(f.num_row_groups):
            f.read_row_group(i)
    elif kind == "dbn.zst":
        import zstandard

        # frame by frame: a stream cut off mid-frame never reaches the frame's end (the stream reader would just
        # stop quietly), and a corrupt block or checksum raises
        dctx = zstandard.ZstdDecompressor()
        obj, pending = dctx.decompressobj(write_size=READ_CHUNK), False
        with open(p, "rb") as fh:
            for data in iter(lambda: fh.read(READ_CHUNK), b""):
                while data:
                    obj.decompress(data)
                    pending = True
                    if obj.eof:
                        data = obj.unused_data
                        obj, pending = dctx.decompressobj(write_size=READ_CHUNK), False
                    else:
                        data = b""
        if pending:
            raise ValueError("zstd stream cut off: the last frame never ends")
    elif kind == "json":
        json.loads(p.read_text(encoding="utf-8-sig"))  # PowerShell's Out-File -Encoding utf8 writes a BOM
    elif kind == "jsonl":
        for i, line in enumerate(p.read_text(encoding="utf-8-sig").splitlines()):
            if line.strip():
                json.loads(line)
    elif kind == "npz":
        import numpy as np

        with np.load(p, allow_pickle=False) as z:
            for k in z.files:
                z[k]
    elif kind == "text":
        check_text(p.read_bytes())
    else:
        with open(p, "rb") as fh:
            while fh.read(READ_CHUNK):
                pass


def check_text(raw: bytes) -> None:
    """Raise if a text file is damaged: a run of ZERO_RUN zero bytes, or bytes that are neither UTF-8 nor
    Windows-1252 (owner 2026-10-31). Logs written by the old PowerShell task action are UTF-16 with a BOM (fixed
    2026-10-10), not damage."""
    if ZERO_RUN in raw:
        raise ValueError(f"a run of {len(ZERO_RUN)}+ zero bytes at byte {raw.index(ZERO_RUN)} (lost data)")
    if raw[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        raw.decode("utf-16")
        return
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as e:
        try:
            raw.decode("cp1252")  # a console-code-page log (pytest before 2026-10-31)
        except UnicodeDecodeError:
            raise ValueError(f"neither UTF-8 ({e.reason} at byte {e.start}) nor Windows-1252") from None


def databento_sizes(raw_dir: Path) -> dict[Path, int]:
    """Each Databento file's size as its download recorded it (the latest successful download per file)."""
    ledger = raw_dir / "databento" / "ledger.jsonl"
    out: dict[Path, int] = {}
    if not ledger.exists():
        return out
    for line in ledger.read_text(encoding="utf-8").splitlines():
        r = json.loads(line) if line.strip() else {}
        if r.get("kind") == "download" and r.get("ok") and r.get("bytes") is not None and r.get("day"):
            for sym in r.get("symbols") or []:
                out[raw_dir / "databento" / r["schema"] / sym / f"{r['day']}.dbn.zst"] = int(r["bytes"])
    return out


def verify_tree(root: Path, since: datetime, raw_dir: Path, drive_ok: Callable[[], bool],
                sleep: Callable[[float], None] = time.sleep, echo: Callable[[str], None] = print) -> VerifyResult:
    res = VerifyResult()
    sizes = databento_sizes(raw_dir)
    cutoff = since.timestamp()
    files = []
    for dp, _, fn in os.walk(root):
        for f in fn:
            p = Path(dp) / f
            try:
                st = p.stat()
            except OSError as e:
                res.unreadable.append({"path": str(p), "error": f"stat: {e}"})
                continue
            if st.st_mtime >= cutoff and not f.endswith(".tmp"):
                files.append((p, st.st_size))
    files.sort()
    echo(f"verifying {len(files):,} files ({sum(s for _, s in files) / 1e9:.1f} GB) written since {since:%Y-%m-%d}")
    for n, (p, size) in enumerate(files, 1):
        kind = _kind(p)
        res.by_type[kind] = res.by_type.get(kind, 0) + 1
        want = sizes.get(p)
        if want is not None and want != size:
            res.size_mismatch.append({"path": str(p), "size": size, "ledger_bytes": want})
        for attempt in range(1, VERIFY_DROP_TRIES + 1):
            try:
                check_file(p)
                break
            except OSError as e:
                if drive_ok() and attempt == VERIFY_DROP_TRIES:
                    res.damaged.append({"path": str(p), "kind": kind, "error": f"{type(e).__name__}: {e}"})
                    break
                res.drops_seen += 1
                echo(f"  read error on {p} ({e}); waiting for the drive (try {attempt}/{VERIFY_DROP_TRIES})")
                waited = 0.0
                while not drive_ok() and waited < VERIFY_DROP_WAIT_SEC:
                    sleep(1.0)
                    waited += 1.0
                if attempt == VERIFY_DROP_TRIES:
                    res.unreadable.append({"path": str(p), "kind": kind, "error": f"{type(e).__name__}: {e}"})
            except Exception as e:  # noqa: BLE001 - any decode failure is damage
                res.damaged.append({"path": str(p), "kind": kind, "error": f"{type(e).__name__}: {e}"[:300]})
                break
        res.checked += 1
        res.bytes += size
        if n % 2000 == 0:
            echo(f"  {n:,}/{len(files):,} files")
    return res


def to_markdown(res: VerifyResult, root: Path, since: datetime) -> str:
    lines = [f"# SSD integrity check", "", f"Written {datetime.now():%Y-%m-%d %H:%M} ET. Files under `{root}` modified "
             f"since {since:%Y-%m-%d}: {res.checked:,} ({res.bytes / 1e9:.1f} GB); by type: "
             + ", ".join(f"{k} {v:,}" for k, v in sorted(res.by_type.items())), "",
             f"- damaged (can't be read back as their type): **{len(res.damaged)}**",
             f"- Databento size differs from the download ledger: **{len(res.size_mismatch)}**",
             f"- unreadable after {VERIFY_DROP_TRIES} tries (drive kept dropping): **{len(res.unreadable)}**",
             f"- read errors that cleared on retry (drive drops during the check): {res.drops_seen}", ""]
    for title, rows in (("Damaged", res.damaged), ("Size mismatch", res.size_mismatch), ("Unreadable", res.unreadable)):
        if rows:
            lines += [f"## {title}", ""] + [f"- `{r['path']}`: {r.get('error') or r}" for r in rows] + [""]
    return "\n".join(lines) + "\n"


def run_verify(paths: Any, since: datetime, echo: Callable[[str], None] = print) -> tuple[VerifyResult, Path]:
    from nqbot.storage import atomic_write_json, atomic_write_text

    res = verify_tree(paths.root, since, paths.raw_dir, paths.mounted, echo=echo)
    out = paths.reports_dir / "verify"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    atomic_write_json(out / f"verify_{stamp}.json", {"since": since.isoformat(), **res.__dict__})
    md = out / "verify.md"
    atomic_write_text(md, to_markdown(res, paths.root, since))
    echo(f"damaged {len(res.damaged)}, size mismatches {len(res.size_mismatch)}, unreadable {len(res.unreadable)}; "
         f"report {md}")
    return res, md
