"""Gameplan file -> Claude API (structured output, §5.6 schema) -> checks -> processed/gameplans/YYYY-MM-DD.json.

SPEC §3.4, §5.6, §12 step 9. One API call per file (cents); the cost estimate is printed first (CLAUDE.md
rule 2): `nqbot gameplan <file>` (daily use) proceeds when the worst case is within `gameplan_max_usd_per_file`,
else needs `--approve-usd`; `nqbot gameplan-batch` needs `--approve-usd` covering the batch's worst case.

Every paid answer is kept (responses/<sha256>.v<PARSER_VERSION>.json) before anything else is decided, so a file
is never sent twice (rule 2): dating, conflicts, re-dating with --date and the canonical copy are all rebuilt
from the saved answer for free. Only `--reparse` sends a file again (e.g. after PARSER_VERSION changes).

Files in processed/gameplans/:
  YYYY-MM-DD.json   the §5.6 fields + status/flags + `parse` (the model's audit fields) + `source`
  index.json        sha256 of every parsed file -> date, how it was dated, file names, `copy_of` for our copies
  calls.jsonl       one line per API call, failed ones included (usage, cost, request id)
  responses/        the raw answer of every successful call
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import anthropic

from nqbot import months
from nqbot.gameplan import validate
from nqbot.gameplan.convert import SUPPORTED_SUFFIXES, CannotConvert, Converted, convert
from nqbot.gameplan.schema import GAMEPLAN_SCHEMA, SPEC_FIELDS
from nqbot.storage import append_jsonl, atomic_write_json, atomic_write_text, replace_retry

PARSER_VERSION = 1  # bump when SYSTEM_PROMPT or GAMEPLAN_SCHEMA changes (then re-parse with --reparse)
MTOK = 1_000_000
CANONICAL = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})(?!\d)")  # "2026-10-06 gameplan.md" (owner's Drive names, §3.4)
HELD_BACK_MONTHS = range(1, 10)  # §5.6: a gameplan dated in ledger months 1-9 is held back until step 20

SYSTEM_PROMPT = """\
You extract a futures trader's morning gameplan for NQ (Nasdaq-100 E-mini futures) into JSON.
The gameplan was written before the 09:30 ET open. Fill in the schema from what the gameplan says, nothing else.

Rules:
- Never invent or compute a number. Copy prices exactly as written. A value the gameplan doesn't give is null.
- Zones, stops, targets, HVL and the regime line are NQ prices. ES gates (e.g. "only while ES < 7766") go in
  trigger.es_below / trigger.es_above. If a price is only given for another instrument (ES, SPX, QQQ), leave the
  NQ field null and say so in `uncertain`.
- zone = [low, high]; a zone given as one price is [price, price]; a zone with no NQ price is null.
  side = "sell" for a short/ceiling/resistance zone, "buy" for a long/floor/support zone.
  targets: in the order written (T1 first). Probabilities as fractions (50% -> 0.5).
- Several versions (e.g. "RTH v1", "RTH v2"): use only the latest version stamped before 09:30 ET; put its label in
  version_used and list the older ones in `dropped`.
- Anything describing price action after 09:30 ET (an "Observed" recap of the RTH session, after-the-fact notes,
  results) is not part of the plan: leave it out and list it in `dropped`. The overnight and 08:30 recap may be
  read for context but creates no zones.
- The gameplan's own news blackout notes are not used: list them in `dropped`.
- date: the trading day the gameplan is for, from the text; quote the words in date_evidence. null if not stated.
- Anything ambiguous (a range that could be read two ways, a stop or target you are not sure belongs to a zone, a
  scenario without a clear direction) goes in `uncertain` with the field path (e.g. zones[E3].stop) and why.
- source_quote: the gameplan line(s) each zone was read from, verbatim.
"""


class GameplanRefused(Exception):
    """Refused before any API call (no approval, over the per-file cap, a date conflict without --replace)."""


@dataclass
class Estimate:
    input_tokens: int
    usd_input: float
    usd_worst: float  # input + gameplan_max_tokens of output (thinking included)


@dataclass
class Result:
    file: str
    status: str  # ok | flagged | cached | failed | unconvertible | conflict
    date: date | None = None
    date_source: str | None = None
    flags: list[dict[str, str]] = field(default_factory=list)
    message: str = ""
    cost_usd: float = 0.0


class GameplanStore:
    def __init__(self, raw_dir: Path, processed_dir: Path, reports_dir: Path):
        self.raw_dir = raw_dir
        self.processed_dir = processed_dir
        self.reports_dir = reports_dir

    @classmethod
    def from_paths(cls, paths) -> GameplanStore:
        return cls(paths.raw_dir / "gameplans", paths.data_dir / "processed" / "gameplans",
                   paths.reports_dir / "gameplans")

    def json_path(self, day: date) -> Path:
        return self.processed_dir / f"{day.isoformat()}.json"

    @property
    def index_path(self) -> Path:
        return self.processed_dir / "index.json"

    @property
    def lock_path(self) -> Path:
        """The gameplan commands' own lock: only they write here, so a long `nqbot run` job never blocks them."""
        return self.processed_dir / ".gameplan.lock"

    @property
    def calls_path(self) -> Path:
        return self.processed_dir / "calls.jsonl"

    def response_path(self, sha: str) -> Path:
        return self.processed_dir / "responses" / f"{sha}.v{PARSER_VERSION}.json"

    def load_index(self) -> dict[str, dict[str, Any]]:
        if not self.index_path.exists():
            return {}
        return json.loads(self.index_path.read_text(encoding="utf-8"))

    def save_index(self, index: dict[str, dict[str, Any]]) -> None:
        atomic_write_json(self.index_path, index)

    def load(self, day: date) -> dict[str, Any] | None:
        p = self.json_path(day)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def load_response(self, sha: str) -> dict[str, Any] | None:
        p = self.response_path(sha)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def parsed_days(self) -> list[date]:
        return sorted(date.fromisoformat(p.stem) for p in self.processed_dir.glob("????-??-??.json"))


def make_client(settings: dict[str, Any]):
    """The real Anthropic client; ANTHROPIC_API_KEY from .env (tests replace this).

    The TLS context is passed in explicitly: databento installs pip-system-certs on Windows, whose startup hook
    swaps ssl.SSLContext for pip's truststore, and the SDK's own truststore setup (httpx2) then recurses forever
    ("Connection error" on every call). ssl.create_default_context() gives the patched, verifying context
    (Windows certificate store) there, and the standard one everywhere else."""
    import ssl

    from nqbot.secrets import get_secret

    return anthropic.Anthropic(api_key=get_secret("ANTHROPIC_API_KEY").reveal(),
                               http_client=anthropic.DefaultHttpxClient(verify=ssl.create_default_context()))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def filename_date(path: Path) -> date | None:
    m = DATE_PREFIX.match(path.stem)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def _messages(conv: Converted, name: str) -> list[dict[str, Any]]:
    ask = {"type": "text", "text": f"File name: {name}\nExtract this gameplan into the schema."}
    return [{"role": "user", "content": [*conv.blocks, ask]}]


def _prices(settings: dict[str, Any]) -> tuple[float, float]:
    p = settings["gameplan_usd_per_mtok"]
    return float(p["input"]), float(p["output"])


def _api_error(e: anthropic.APIError) -> str:
    status = getattr(e, "status_code", None)
    return f"API error {type(e).__name__}{f' {status}' if status else ''}: {e}"


def estimate(client, settings: dict[str, Any], conv: Converted, name: str) -> Estimate:
    """Free token count of the request; worst case adds gameplan_max_tokens of output."""
    count = client.messages.count_tokens(
        model=settings["gameplan_model"], system=SYSTEM_PROMPT, messages=_messages(conv, name),
        output_config={"format": {"type": "json_schema", "schema": GAMEPLAN_SCHEMA}},
    )
    usd_in, usd_out = _prices(settings)
    usd_input = count.input_tokens * usd_in / MTOK
    return Estimate(count.input_tokens, usd_input, usd_input + settings["gameplan_max_tokens"] * usd_out / MTOK)


def _cost(usage: Any, settings: dict[str, Any]) -> float:
    usd_in, usd_out = _prices(settings)
    tokens_in = (getattr(usage, "input_tokens", 0) or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0) \
        + (getattr(usage, "cache_read_input_tokens", 0) or 0)
    return (tokens_in * usd_in + (getattr(usage, "output_tokens", 0) or 0) * usd_out) / MTOK


def call_api(client, settings: dict[str, Any], conv: Converted, name: str) -> tuple[str | None, dict]:
    """One parse call, never retried (the SDK's own retries are off: a timed-out call may still be billed).

    No server-side fallback model: the worst-case estimate must hold (rule 2); a refusal is just a failure
    listed for the owner. Returns (the JSON text or None, call record for calls.jsonl)."""
    record: dict[str, Any] = {"model_requested": settings["gameplan_model"]}
    try:
        response = client.with_options(max_retries=0).messages.create(
            model=settings["gameplan_model"],
            max_tokens=settings["gameplan_max_tokens"],
            system=SYSTEM_PROMPT,
            messages=_messages(conv, name),
            output_config={"effort": settings["gameplan_effort"],
                           "format": {"type": "json_schema", "schema": GAMEPLAN_SCHEMA}},
        )
    except anthropic.APIError as e:
        record.update(request_id=getattr(e, "request_id", None), cost_usd=0.0, error=_api_error(e))
        return None, record
    except ValueError as e:  # refused by the SDK before sending (e.g. gameplan_max_tokens needs streaming)
        record.update(request_id=None, cost_usd=0.0, error=f"not sent: {e}")
        return None, record
    usage = response.usage
    record.update({
        "request_id": getattr(response, "_request_id", None),
        "model_served": response.model,
        "stop_reason": response.stop_reason,
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "cost_usd": round(_cost(usage, settings), 6),
    })
    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        record["error"] = f"refused ({getattr(details, 'category', None)}): {getattr(details, 'explanation', '')}"
        return None, record
    if response.stop_reason == "max_tokens":
        record["error"] = "output cut off at gameplan_max_tokens"
        return None, record
    text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), None)
    try:
        parsed = json.loads(text) if text is not None else None
    except json.JSONDecodeError as e:
        record["error"] = f"not valid JSON: {e}"
        return None, record
    if not isinstance(parsed, dict):
        record["error"] = "no JSON object in the response"
        return None, record
    return text, record


# --- canonical copies and dates -------------------------------------------------------------------------------

def _copy_dest(store: GameplanStore, conv: Converted, day: date) -> Path:
    return store.raw_dir / f"{day.isoformat()}{'.pdf' if conv.kind == 'pdf' else '.md'}"


def _copy_bytes(file: Path, conv: Converted) -> bytes:
    """What the canonical copy holds: the PDF itself, else the text sent to the API (UTF-8, no BOM)."""
    return file.read_bytes() if conv.kind == "pdf" else (conv.markdown or "").encode("utf-8")


def _copy_ok(dest: Path, file: Path, conv: Converted) -> bool:
    """The canonical name is free, is this file, or already holds exactly this file's copy."""
    return not dest.exists() or dest.resolve() == file.resolve() or dest.read_bytes() == _copy_bytes(file, conv)


def _blocked(store: GameplanStore, day: date, sha: str, file: Path, conv: Converted) -> str | None:
    """Why `file` can't become `day`'s gameplan without --replace (another file owns that date)."""
    existing = store.load(day)
    if existing is not None and existing.get("source", {}).get("sha256") != sha:
        return f"{day} already parsed from {existing.get('source', {}).get('file')!r}"
    dest = _copy_dest(store, conv, day)
    if not _copy_ok(dest, file, conv):
        return f"raw/gameplans/{dest.name} already holds a different file"
    return None


def _move_aside(store: GameplanStore, path: Path) -> None:
    """--replace never deletes the owner's file: it goes to raw/gameplans/replaced/."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    dest = store.raw_dir / "replaced" / f"{path.stem}.{stamp}{path.suffix}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    replace_retry(path, dest)  # same drive (raw/gameplans/): a rename, retried if antivirus holds the file


def _retire_date(store: GameplanStore, day: date, file: Path, conv: Converted, index: dict, keep: str) -> None:
    """--replace: forget every other file's claim on `day` and move its files in raw/gameplans/ aside (its JSON is
    then overwritten), so later batches don't list them as conflicts. This file and its own copy stay."""
    retired = {h: index.pop(h) for h in [h for h, e in index.items() if e.get("date") == day.isoformat()
                                         and h != keep]}
    candidates = {store.raw_dir / name for e in retired.values() for name in e.get("files", [])}
    candidates |= {store.raw_dir / f"{day.isoformat()}{suffix}" for suffix in (".md", ".pdf")}
    for path in sorted(candidates):
        if not path.is_file() or path.resolve() == file.resolve():
            continue
        if path == _copy_dest(store, conv, day) and path.read_bytes() == _copy_bytes(file, conv):
            continue  # already holds exactly this file's copy
        if path.parent == store.raw_dir and (path.stem == day.isoformat() or sha256(path) in retired):
            _move_aside(store, path)


def _retire_own(store: GameplanStore, old_day: str, sha: str, file: Path, conv: Converted, index: dict) -> None:
    """This file was re-dated: remove its JSON and canonical copy under the old date (never another file's)."""
    old = date.fromisoformat(old_day)
    doc = store.load(old)
    if doc is not None and doc.get("source", {}).get("sha256") == sha:
        store.json_path(old).unlink()
    dest = _copy_dest(store, conv, old)
    if dest.exists() and dest.resolve() != file.resolve() and dest.read_bytes() == _copy_bytes(file, conv):
        dest.unlink()
    for h in [h for h, e in index.items() if e.get("copy_of") == sha and e.get("date") == old_day]:
        del index[h]


# --- one file -------------------------------------------------------------------------------------------------

def parse_file(client, settings: dict[str, Any], store: GameplanStore, file: Path, *, given_date: date | None = None,
               reparse: bool = False, replace: bool = False, cap_usd: float | None = None,
               conv: Converted | None = None, est: Estimate | None = None,
               out: Callable[[str], None] = print) -> Result:
    """Parse one file. `cap_usd`: refuse a call whose worst case is above it (None: the batch approved it)."""
    sha = sha256(file)
    index = store.load_index()
    entry = index.get(sha, {})
    if entry.get("copy_of"):
        original = index.get(entry["copy_of"], {}).get("files") or ["its original"]
        raise GameplanRefused(f"{file.name} is the copy saved from {', '.join(original)}: parse that file instead")
    asked_date = given_date
    if given_date is None and entry.get("given_date"):
        given_date = date.fromisoformat(entry["given_date"])  # an owner's --date correction is kept
    try:
        conv = conv or convert(file)
    except CannotConvert as e:
        return Result(file.name, "unconvertible", message=str(e))

    if not reparse and entry.get("date"):
        known_day = date.fromisoformat(entry["date"])
        done = store.load(known_day)
        if done is not None and done.get("source", {}).get("sha256") == sha:
            if asked_date is None or asked_date == known_day:
                return Result(file.name, "cached", known_day, entry.get("date_source"), done.get("flags", []),
                              message="already parsed (use --reparse to send it again)")
            if store.load_response(sha) is None:
                raise GameplanRefused(f"{file.name}: re-dating needs its saved answer, which is from another "
                                      "parser version: pass --reparse (a new paid call)")

    saved = None if reparse else store.load_response(sha)
    cost = 0.0
    if saved is None:
        pre_date = given_date or filename_date(file)
        problem = _blocked(store, pre_date, sha, file, conv) if pre_date and not replace else None
        if problem:
            raise GameplanRefused(f"{problem}; pass --replace to use {file.name} instead")
        try:
            est = est or estimate(client, settings, conv, file.name)
        except anthropic.APIError as e:
            return Result(file.name, "failed", message=f"token count: {_api_error(e)}")
        out(f"{file.name}: {est.input_tokens:,} input tokens ~ ${est.usd_input:.3f}; "
            f"worst case with {settings['gameplan_max_tokens']:,} output tokens ${est.usd_worst:.2f}")
        if cap_usd is not None and est.usd_worst > cap_usd:
            raise GameplanRefused(f"{file.name} may cost up to ${est.usd_worst:.2f}, over ${cap_usd:.2f}: "
                                  f"re-run with --approve-usd {est.usd_worst:.2f} after the owner's OK")
        text, record = call_api(client, settings, conv, file.name)
        append_jsonl(store.calls_path, {"at": datetime.now(timezone.utc).isoformat(), "file": file.name,
                                        "sha256": sha, "parser_version": PARSER_VERSION, **record})
        cost = record["cost_usd"]
        if text is None:
            return Result(file.name, "failed", message=f"{record['error']} (request {record['request_id']})",
                          cost_usd=cost)
        saved = {"text": text, "file": file.name, "at": datetime.now(timezone.utc).isoformat(), **record}
        atomic_write_json(store.response_path(sha), saved)  # kept before any check: never paid for twice
    parsed = json.loads(saved["text"])

    day, source, date_flags = validate.resolve_date(parsed.get("date"), filename_date(file), given_date)
    if day is None:
        return Result(file.name, "failed", message="no date in the file name or the content: pass --date "
                      "(the answer is saved, no new call)", cost_usd=cost)
    old_day = entry.get("date")
    if not replace:
        problem = _blocked(store, day, sha, file, conv)
        if problem:
            return Result(file.name, "conflict", day, source, message=problem + " (the answer is saved)",
                          cost_usd=cost)
    else:
        _retire_date(store, day, file, conv, index, keep=sha)
    if old_day and old_day != day.isoformat():
        _retire_own(store, old_day, sha, file, conv, index)

    flags = date_flags + validate.check(parsed)
    copy = _copy_dest(store, conv, day)
    if not copy.exists():
        if conv.kind == "pdf":
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, copy)
        else:
            atomic_write_text(copy, conv.markdown or "")

    doc = {k: parsed.get(k) for k in SPEC_FIELDS}
    doc["date"] = day.isoformat()
    doc["status"] = "flagged" if flags else "ok"
    doc["flags"] = flags
    doc["parse"] = {"date_source": source, "date_in_content": parsed.get("date"),
                    "date_evidence": parsed.get("date_evidence"), "version_used": parsed.get("version_used"),
                    "dropped": parsed.get("dropped") or []}
    doc["source"] = {"file": file.name, "sha256": sha, "kind": conv.kind, "canonical_copy": copy.name,
                     "given_date": given_date.isoformat() if given_date else None,
                     "parser_version": PARSER_VERSION, "built_at": datetime.now(timezone.utc).isoformat(),
                     **{k: saved.get(k) for k in ("request_id", "model_requested", "model_served", "input_tokens",
                                                  "output_tokens", "cost_usd")}}
    atomic_write_json(store.json_path(day), doc)

    keep_files = entry.get("files", []) if old_day == day.isoformat() else []
    index[sha] = {"date": day.isoformat(), "date_source": source,
                  "given_date": given_date.isoformat() if given_date else None,
                  "files": sorted({*keep_files, file.name}), "copy_of": None}
    copy_sha = sha256(copy)
    if copy_sha == sha:
        index[sha]["files"] = sorted({*index[sha]["files"], copy.name})
    else:
        index[copy_sha] = {"date": day.isoformat(), "date_source": source, "given_date": index[sha]["given_date"],
                           "files": [copy.name], "copy_of": sha}
    store.save_index(index)
    return Result(file.name, doc["status"], day, source, flags, cost_usd=cost)


# --- the archive ----------------------------------------------------------------------------------------------

def gameplan_files(store: GameplanStore) -> list[Path]:
    if not store.raw_dir.exists():
        return []
    # Archived names first: when a canonical copy is byte-identical to its original, the original is the one sent.
    files = [p for p in store.raw_dir.iterdir() if p.is_file() and not p.name.startswith(".")]
    return sorted(files, key=lambda p: (CANONICAL.match(p.stem) is not None, p.name))


def batch(client, settings: dict[str, Any], store: GameplanStore, *, approved_usd: float | None,
          reparse: bool = False, still_mounted: Callable[[], bool] = lambda: True,
          out: Callable[[str], None] = print) -> tuple[list[Result], Path]:
    """Parse every file in raw/gameplans/ (resumable: saved answers are reused), then write the step-9 report.

    Each distinct file content is handled once; our own canonical copies are skipped (their original is the
    source)."""
    index = store.load_index()
    results: list[Result] = []
    todo: list[tuple[Path, Converted, Estimate | None]] = []
    seen: set[str] = set()
    for f in gameplan_files(store):
        if f.suffix.lower() not in SUPPORTED_SUFFIXES:
            results.append(Result(f.name, "unconvertible", message=f"{f.suffix or 'no extension'} is not supported"))
            continue
        h = sha256(f)
        if h in seen:
            continue
        seen.add(h)
        known = index.get(h, {})
        if known.get("copy_of"):
            continue
        if known and not reparse and store.json_path(date.fromisoformat(known["date"])).exists():
            continue
        try:
            conv = convert(f)
        except CannotConvert as e:
            results.append(Result(f.name, "unconvertible", message=str(e)))
            continue
        est = None
        if reparse or store.load_response(h) is None:
            try:
                est = estimate(client, settings, conv, f.name)
            except anthropic.APIError as e:
                results.append(Result(f.name, "failed", message=f"token count: {_api_error(e)}"))
                continue
        todo.append((f, conv, est))

    paid = [e for _, _, e in todo if e is not None]
    worst = sum(e.usd_worst for e in paid)
    out(f"{len(todo)} file(s) to build, {len(paid)} needing an API call: ~${sum(e.usd_input for e in paid):.2f} "
        f"input, worst case ${worst:.2f}")
    if paid and (approved_usd is None or approved_usd < worst):
        raise GameplanRefused(f"the batch may cost up to ${worst:.2f}: re-run with --approve-usd {worst:.2f} "
                              "after the owner's OK (CLAUDE.md rule 2)")

    for f, conv, est in todo:
        if not still_mounted():
            raise GameplanRefused("the SSD is no longer mounted: stopped (re-run to resume)")
        try:
            r = parse_file(client, settings, store, f, reparse=reparse, conv=conv, est=est, out=out)
        except GameplanRefused as e:  # a dated name whose day is already another file's
            r = Result(f.name, "conflict", filename_date(f), "filename", message=str(e))
        results.append(r)
        out(f"  {f.name}: {r.status} {r.date or ''} {r.message}".rstrip())
    return results, write_report(store, settings, results)


def _ledger_note(day: date, settings: dict[str, Any]) -> str:
    try:
        n = months.month_index(day, settings)
    except months.LedgerMissing:
        return "ledger not written yet (step 4b)"
    if n is None:
        return "outside the window (top-up or later)"
    if n in HELD_BACK_MONTHS:
        return f"ledger month {n}: held back until step 20 (§5.6)"
    return f"ledger month {n}"


def write_report(store: GameplanStore, settings: dict[str, Any], results: list[Result]) -> Path:
    """reports/gameplans/step9_report.md: file -> date list, gameplan days per month, problems, flags."""
    days = store.parsed_days()
    docs = {d: store.load(d) for d in days}
    lines = ["# Step 9: gameplans", "", f"Written {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC.", "",
             "## File -> date (owner: please confirm)", "",
             "| File | Saved as | Date | Date from |", "|---|---|---|---|"]
    for d, doc in docs.items():
        src = doc["source"]
        lines.append(f"| {src['file']} | {src['canonical_copy']} | {d} | {doc['parse']['date_source']} |")

    lines += ["", "## Gameplan days per month", "", "| Month | Days | Ledger |", "|---|---|---|"]
    per_month: dict[str, list[date]] = {}
    for d in days:
        per_month.setdefault(f"{d:%Y-%m}", []).append(d)
    for ym, ds in sorted(per_month.items()):
        notes = sorted({_ledger_note(d, settings) for d in ds})
        lines.append(f"| {ym} | {len(ds)} | {'; '.join(notes)} |")
    lines.append(f"| total | {len(days)} | |")

    problems = [r for r in results if r.status in ("failed", "unconvertible", "conflict")]
    lines += ["", "## Not parsed", ""]
    lines += [f"- {r.file}: {r.status}: {r.message}" for r in problems] or ["- none"]

    lines += ["", "## Flags", ""]
    flagged = [(d, doc) for d, doc in docs.items() if doc.get("flags")]
    for d, doc in flagged:
        lines.append(f"- **{d}** ({doc['source']['file']}):")
        lines += [f"  - `{f['field']}` ({f['origin']}): {f['reason']}" for f in doc["flags"]]
    if not flagged:
        lines.append("- none")
    lines.append("")
    path = store.reports_dir / "step9_report.md"
    atomic_write_text(path, "\n".join(lines))
    return path
