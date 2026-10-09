"""Free daily closes for the NDXP methods (SPEC §13 Q8b-1, step-8b findings R6/R9): NDX official closes from FRED
(series NASDAQ100) and QQQ official closes and ex-dividend dates from Nasdaq's public quote API. No key, no cost.

Each fetch is kept under `<raw>/ndx/<fetch date>/` (the files change every day, so a new day fetches again; the same
day reads its copy).
"""

from __future__ import annotations

import csv
import io
import json
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from nqbot.storage import Paths, atomic_write_bytes

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=NASDAQ100&cosd={start}&coed={end}"
QQQ_HIST_URL = ("https://api.nasdaq.com/api/quote/QQQ/historical?assetclass=etf&fromdate={start}&todate={end}"
                "&limit=9999")
QQQ_DIV_URL = "https://api.nasdaq.com/api/quote/QQQ/dividends?assetclass=etf"
FILES = {"fred": "NASDAQ100_fred.csv", "qqq": "QQQ_nasdaq_hist.json", "div": "QQQ_nasdaq_div.json"}
HISTORY_START = date(2025, 8, 1)  # enough sessions before the window for k's smoothing
TIMEOUT_S = 60
# Nasdaq's API answers a browser user agent; FRED drops the connection on one and answers a plain one.
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
PLAIN_UA = "nqbot/1.0"

Fetch = Callable[[str], bytes]


def http_get(url: str) -> bytes:
    nasdaq = url.startswith("https://api.nasdaq.com/")
    headers = {"User-Agent": BROWSER_UA if nasdaq else PLAIN_UA, "Accept": "application/json" if nasdaq else "*/*"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 (fixed https URLs)
        return resp.read()


def folder(paths: Paths, fetched: date) -> Path:
    return paths.raw_dir / "ndx" / fetched.isoformat()


def fetch_all(paths: Paths, today: date, fetch: Fetch = http_get) -> dict[str, Any]:
    """The three files as of today (fetched once a day), parsed."""
    base = folder(paths, today)
    urls = {"fred": FRED_URL.format(start=HISTORY_START, end=today), "qqq": QQQ_HIST_URL.format(start=HISTORY_START, end=today),
            "div": QQQ_DIV_URL}
    raw = {}
    for key, name in FILES.items():
        path = base / name
        if not path.exists():
            atomic_write_bytes(path, fetch(urls[key]))
        raw[key] = path.read_bytes()
    return {"ndx": parse_fred(raw["fred"]), "qqq": parse_qqq_closes(raw["qqq"]), "div": parse_qqq_dividends(raw["div"]),
            "folder": str(base)}


def parse_fred(data: bytes) -> dict[date, float]:
    out = {}
    for row in csv.DictReader(io.StringIO(data.decode("utf-8"))):
        v = row.get("NASDAQ100", "")
        if v not in ("", "."):
            out[date.fromisoformat(row["observation_date"])] = float(v)
    return out


def _money(text: str) -> float:
    return float(text.replace("$", "").replace(",", ""))


def parse_qqq_closes(data: bytes) -> dict[date, float]:
    rows = (json.loads(data)["data"] or {}).get("tradesTable", {}).get("rows") or []
    return {datetime.strptime(r["date"], "%m/%d/%Y").date(): _money(r["close"]) for r in rows}


def parse_qqq_dividends(data: bytes) -> dict[date, float]:
    rows = ((json.loads(data)["data"] or {}).get("dividends") or {}).get("rows") or []
    return {datetime.strptime(r["exOrEffDate"], "%m/%d/%Y").date(): _money(r["amount"]) for r in rows
            if r.get("exOrEffDate") not in (None, "", "N/A")}
