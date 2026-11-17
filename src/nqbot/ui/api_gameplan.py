"""The Gameplan page's JSON API (SPEC §3.4, §5.6, §9b item 4; build stage 5).

- `GET /api/gameplan[?date=D]`: the day (default today, New York time): its session rules (setup windows, last entry,
  flat-by, an early close), the news blackouts with their releases, the effective gameplan (the parsed file with the
  owner's overrides on top, `GameplanStore.load_effective`), its flags, the owner's edits and confirmation, whether
  that confirmation counts for the day's trading (before 09:30 ET of that day), and the stored dates.
- `POST /api/gameplan/upload` (token; the file's bytes as the body, its name in `x-file-name`, the day in
  `x-gameplan-date`): kept in a staging folder (`<tmp_dir>/gameplan_uploads/`), converted (.md/.txt/.pdf/.docx,
  `gameplan/convert.py`), and its cost estimated with the free token count (rule 2); nothing is parsed yet.
- `POST /api/gameplan/parse` (token): the paid parse of a staged file, only with the owner's approval of the printed
  worst case (`approve_usd`, at most `gameplan_max_usd_per_file`; above it the CLI with `--approve-usd`). The same
  `parser.parse_file` as `nqbot gameplan <file>`, under the same gameplan lock; a gameplan already stored for that day
  is replaced only when the owner asks (`replace`).
- `POST /api/gameplan/edit` (token): one owner override (`gameplan/overrides.py`; a re-parse never loses it).
- `POST /api/gameplan/confirm` (token): the owner's confirmation. Only one given before 09:30 ET of the gameplan's own
  day is used for that day's live trading (§9b item 4); a later one, or an edit after it, is saved for training.
- `POST /api/gameplan/discard` (token): drop a staged upload.
- `POST /api/gameplan/accept` (token): the owner marks one review flag "OK as parsed".

Owner 2026-11-16 (stage-5 answers): ledger months 7-12 are hidden (left out of the stored days and refused by every
route) until gate 16 is recorded; a failed parse is never retried by itself (another click sends it again); before
09:30 ET of the gameplan's day a confirmation (for that day's trading) needs every review flag fixed or marked "OK as
parsed", after it (training only) open flags are allowed; each edit settles exactly the review flags shown on the
field it edits (`resolves`).

`nqbot ui --synthetic` uses a temporary gameplan store and an offline stand-in for the Claude API (a canned answer,
no network, no cost); the owner's gameplans are never touched.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from nqbot import calendar as cal_mod
from nqbot.gameplan import overrides as ov
from nqbot.gameplan.convert import MAX_PDF_BYTES, SUPPORTED_SUFFIXES, CannotConvert, convert
from nqbot.months import month_index
from nqbot.storage import Paths

ET = ZoneInfo("America/New_York")
RTH_OPEN = time(9, 30)
UPLOAD_MAX_BYTES = MAX_PDF_BYTES        # the Claude API's request size limit (convert.py)
SAFE_NAME = re.compile(r"[^A-Za-z0-9 ._()\-]+")
# the fields the page may edit (an override each); a zone entry's own fields, then the plan-wide ones
ENTRY_FIELDS = {"side": ["side"], "zone": ["zone"], "trigger": ["trigger", "nq_price"], "stop": ["stop"],
                "targets": ["targets"], "es_gates": ["trigger", "es_gates"], "invalidation": ["invalidation"]}
PLAN_FIELDS = {"hvl": ["hvl"], "hvl0": ["hvl0"], "regime_line": ["regime_line"]}
SCENARIO_FIELDS = ("prob", "bias", "desc")
BIASES = ("up", "down", "neutral", "mixed")
HIDDEN_MONTHS = range(7, 13)            # owner 2026-11-16: ledger months 7-12 stay off the page until gate 16
ENTRY_FLAG = re.compile(r"^zones\[([^\]]+)\](?:\.(.+))?$")
PRICE = r"(?:\d{1,3}(?:,\d{3})+|\d{3,})(?:\.\d+)?"
PRICE_RANGE = re.compile(rf"{PRICE}(?:\s*[-–—]\s*{PRICE})?")
ABOVE = re.compile(r">|\babove\b|\bover\b|\bholding\b|\breclaim", re.I)
BELOW = re.compile(r"<|\bbelow\b|\bunder\b|\blos(?:es|ing)\b|\breject|\bfail", re.I)


def hidden(day: date, settings: dict[str, Any], passed: frozenset[str] | None = None) -> bool:
    """A day in ledger months 7-12 while gate 16 is not recorded (`config/gate_record.json`)."""
    from nqbot.performance.guard import GATE_WALK_FORWARD, gates_passed

    return month_index(day, settings) in HIDDEN_MONTHS and GATE_WALK_FORWARD not in (gates_passed() if passed is None else passed)


def entry_col(sub: str) -> str:
    """The page's column of an entry flag's sub-field ("trigger.es_gates" -> es_gates, "trigger_level" -> trigger)."""
    if sub == "es_gates" or sub.startswith("trigger.es_gates"):
        return "es_gates"
    if sub.startswith("trigger"):
        return "trigger"
    return sub.split(".")[0]


def review_flags_for(doc: dict[str, Any], field: str, entry: str | None = None, scenario: str | None = None) -> list[str]:
    """The review flags shown on the field the owner edits (the ones that edit settles)."""
    out = set()
    for f in doc.get("flags") or []:
        if not f.get("review", True):
            continue
        fld = f.get("field", "")
        if field in PLAN_FIELDS:
            if fld == field or field in fld.split(","):
                out.add(fld)
        elif field in ENTRY_FIELDS:
            m = ENTRY_FLAG.match(fld)
            if m and m.group(1) == entry and entry_col(m.group(2) or "") == field:
                out.add(fld)
        elif scenario is not None and f"scenarios[{scenario}]" in fld:
            out.add(fld)
    return sorted(out)


def _items(v: Any) -> list[str]:
    return [t.strip() for t in re.split(r"\s*[;·|]\s*", str(v or "")) if t.strip()]


def _direction(t: str) -> str:
    a, b = bool(ABOVE.search(t)), bool(BELOW.search(t))
    return "above" if a and not b else "below" if b and not a else "unclear"


def _timeframe(t: str) -> str:
    low = t.lower()
    if "accept" in low:
        return "acceptance"
    if re.search(r"5\s*-?m|5-min|bar close|settled bar", low):
        return "settled_bar"
    if re.search(r"1\s*-?m|1-min|settled|close", low):
        return "1m_close"
    return "not_stated"


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status, headers={"cache-control": "no-store"})


def _ok(data: Any) -> JSONResponse:
    return JSONResponse(data, headers={"cache-control": "no-store"})


def today_et(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(ET).date()


def _hhmm(t: time | datetime) -> str:
    return t.strftime("%H:%M")


def day_rules(day: date, settings: dict[str, Any], features: dict[str, Any], cal: dict[str, Any]) -> dict[str, Any]:
    """The day's fixed session rules (SPEC §1.2, §9): setup windows, last entry and flat-by (an early close moves the
    flat-by to its close − 1 min), and the news blackouts with their releases (the live-only red-folder extras marked)."""
    from nqbot.ui.api_panel import windows

    w = windows(settings, features)
    trading = cal_mod.is_trading_day(day, cal)
    early = cal_mod.early_close_et(day, cal)
    if early is not None:
        flat = (datetime.combine(day, early) - timedelta(minutes=1))
        w = {**w, "flat_by": _hhmm(flat),
             "last_entry": _hhmm(flat - timedelta(minutes=int(settings["min_minutes_before_flat"])))}
    before, after = int(settings["blackout_before_min"]), int(settings["blackout_after_min"])
    lead = int(settings["news_flatten_lead_sec"])
    rel = []
    for r in cal_mod.releases(cal, live=True):
        if r.day != day or not r.blackout:
            continue
        at = datetime.combine(day, r.time_et)
        live_only = not any(e["date"] == day and e["name"] == r.name for e in (cal.get("events") or []))
        rel.append({"at": _hhmm(at), "name": r.name, "live_only": live_only,
                    "from": (at - timedelta(minutes=before, seconds=lead)).strftime("%H:%M:%S"),
                    "to": _hhmm(at + timedelta(minutes=after))})
    rel.sort(key=lambda x: x["at"])
    return {"trading_day": trading, "early_close": _hhmm(early) if early else None, "windows": w, "blackouts": rel,
            "blackout_before_min": before, "blackout_after_min": after}


def status(doc: dict[str, Any] | None, ovr: dict[str, Any], day: date, now: datetime | None = None) -> dict[str, Any]:
    """none | parsed | confirmed | changed (an edit after the confirmation); whether the confirmation counts for the
    day's trading (given before 09:30 ET of that day, §9b item 4)."""
    if doc is None:
        return {"state": "none"}
    flags = doc.get("flags") or []
    review = sum(1 for f in flags if f.get("review", True))
    opens = datetime.combine(day, RTH_OPEN, tzinfo=ET)
    out: dict[str, Any] = {"state": "parsed", "review_flags": review, "info_flags": len(flags) - review,
                           "parse_status": doc.get("status"), "edits": len(ovr.get("ops") or []),
                           "accepted": len(ovr.get("accepted") or []),
                           "before_open": (now or datetime.now(timezone.utc)) < opens}
    conf = ovr.get("owner_confirmed")
    if conf and conf.get("at"):
        at = datetime.fromisoformat(conf["at"]).astimezone(ET)
        changes = [*ov_ops(ovr), *(ovr.get("accepted") or [])]
        later_edit = [o for o in changes if o.get("at") and datetime.fromisoformat(o["at"]) > datetime.fromisoformat(conf["at"])]
        out.update(state="changed" if later_edit else "confirmed", confirmed_at=at.strftime("%H:%M"),
                   confirmed_date=at.date().isoformat(), confirmed_note=conf.get("note") or "",
                   counts_for_trading=at < opens and not later_edit)
    return out


def ov_ops(ovr: dict[str, Any]) -> list[dict[str, Any]]:
    return list(ovr.get("ops") or [])


def parse_range(v: Any) -> list[float] | None:
    """[low, high] from a number, "29,450" or "29,450-29,500" / "29,450–29,500"; None for "" (cleared)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, (int, float)):
        return [float(v), float(v)]
    if isinstance(v, list) and len(v) == 2:
        lo, hi = float(v[0]), float(v[1])
        return [min(lo, hi), max(lo, hi)]
    parts = [p for p in re.split(r"\s*[-–—]\s*|\s+to\s+", str(v).replace(",", "").strip()) if p]
    nums = [float(p) for p in parts]
    if len(nums) == 1:
        return [nums[0], nums[0]]
    if len(nums) == 2:
        return [min(nums), max(nums)]
    raise ValueError(f"not a price or a range: {v!r}")


def edit_value(field: str, value: Any) -> Any:
    """The stored shape (§5.6) of an edited field."""
    if field == "side":
        if value not in ("buy", "sell"):
            raise ValueError("side must be buy or sell")
        return value
    if field == "stop":
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return float(str(value).replace(",", ""))
    if field == "targets":
        items = [t for t in re.split(r"\s*(?:→|->|;|\|)\s*", str(value or "")) if t.strip()]
        out = []
        for t in items:
            lo, hi = parse_range(t)
            out.append({"as_written": t.strip(), "low": lo, "high": hi})
        return out
    if field in ("zone", "trigger", "hvl", "hvl0", "regime_line"):
        return parse_range(value)
    if field == "es_gates":
        out = []
        for t in _items(value):
            m = PRICE_RANGE.search(t)
            out.append({"as_written": t, "direction": _direction(t), "price": parse_range(m.group(0)) if m else None})
        return out
    if field == "invalidation":
        out = []
        for t in _items(value):
            m = re.search(PRICE, t)
            out.append({"as_written": t, "direction": _direction(t), "price": float(m.group(0).replace(",", "")) if m else None,
                        "timeframe": _timeframe(t), "instrument": "ES" if re.search(r"\bES\b", t) else "NQ",
                        "acceptance_minutes": None})
        return out
    if field == "prob":
        if value is None or not str(value).strip():
            return None
        x = float(str(value).replace("%", "").strip())
        x = x / 100 if x > 1 else x
        if not 0 <= x <= 1:
            raise ValueError("a probability is 0-100%")
        return round(x, 4)
    if field == "bias":
        if value not in BIASES:
            raise ValueError(f"bias must be one of {', '.join(BIASES)}")
        return value
    if field == "desc":
        t = str(value or "").strip()
        if not t:
            raise ValueError("the scenario's text can't be empty")
        return t[:2000]
    raise ValueError(f"not an editable field: {field}")


class SyntheticClient:
    """An offline stand-in for the Claude API on the synthetic panel: a fixed token count and a canned answer (the
    shape of a real one, `synthetic_gameplan.response.json`); never any network or cost."""

    CANNED = Path(__file__).with_name("synthetic_gameplan.response.json")

    failed: set[str] = set()      # shared by the panel's clients: "fail-once" files that failed already

    def __init__(self) -> None:
        self.messages = self

    def with_options(self, **_kw):
        return self

    def count_tokens(self, **_kw):
        from types import SimpleNamespace

        return SimpleNamespace(input_tokens=4200)

    def create(self, **kw):
        from types import SimpleNamespace

        text = self.CANNED.read_text(encoding="utf-8")
        # a file named "...fail-once..." fails its first parse (to show the page's no-retry path on the synthetic panel)
        last = str((kw.get("messages") or [{}])[-1].get("content", [{}])[-1].get("text", ""))
        name = last.splitlines()[0] if last else ""
        fail = "fail-once" in name and name not in self.failed
        if fail:
            self.failed.add(name)
        m = SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="max_tokens" if fail else "end_turn",
                            model="synthetic (no API call)", stop_details=None,
                            usage=SimpleNamespace(input_tokens=0, output_tokens=0, cache_creation_input_tokens=0,
                                                  cache_read_input_tokens=0))
        m._request_id = "synthetic"
        return m


@dataclass
class Staged:
    uid: str
    file: Path
    day: date
    worst_usd: float | None
    input_tokens: int | None
    cached: bool
    est: Any = None           # the parser's Estimate, so the parse doesn't count the tokens again


def routes(paths_fn: Callable[[], Paths | None], settings: dict[str, Any], features: dict[str, Any],
           require_token: Callable[[str | None], Response | None], *, synthetic: bool = False,
           now_fn: Callable[[], datetime] | None = None, client_fn: Callable[[], Any] | None = None) -> list[Route]:
    from nqbot.gameplan import parser as gp

    cal = cal_mod.load()
    now = now_fn or (lambda: datetime.now(timezone.utc))
    syn_root = Path(tempfile.mkdtemp(prefix="cf_gameplan_")) if synthetic else None
    staged: dict[str, Staged] = {}

    def store_or_none() -> tuple[gp.GameplanStore | None, Path | None, str | None]:
        if syn_root is not None:
            s = gp.GameplanStore(syn_root / "raw", syn_root / "processed", syn_root / "reports")
            return s, syn_root / "uploads", None
        paths = paths_fn()
        if paths is None:
            return None, None, "The SSD isn't connected, so no gameplan can be read or saved."
        return gp.GameplanStore.from_paths(paths), Path(paths.tmp_dir) / "gameplan_uploads", None

    def client():
        if client_fn is not None:
            return client_fn()
        return SyntheticClient() if synthetic else gp.make_client(settings)

    def day_of(request: Request, key: str = "date") -> date:
        v = request.query_params.get(key)
        return date.fromisoformat(v) if v else today_et(now())

    def payload(store: gp.GameplanStore, day: date) -> dict[str, Any]:
        doc = store.load_effective(day)
        ovr = ov.load(store.processed_dir, day)
        return {"date": day.isoformat(), "today": today_et(now()).isoformat(), "synthetic": synthetic,
                "rules": day_rules(day, settings, features, cal), "gameplan": doc,
                "status": status(doc, ovr, day, now()), "edits": ov_ops(ovr),
                "days": [d.isoformat() for d in store.parsed_days() if not hidden(d, settings)],
                "day_states": day_states(store),
                "cap_usd": float(settings["gameplan_max_usd_per_file"]),
                "accept": list(SUPPORTED_SUFFIXES)}

    def day_states(store: gp.GameplanStore) -> dict[str, dict[str, Any]]:
        """Each listed day's state for the Day picker (hidden months left out)."""
        out = {}
        for d in [*store.parsed_days(), today_et(now())]:
            if hidden(d, settings):
                continue
            stt = status(store.load_effective(d), ov.load(store.processed_dir, d), d, now())
            out[d.isoformat()] = {k: stt.get(k) for k in ("state", "counts_for_trading", "review_flags")}
        return out

    async def guarded(request: Request) -> tuple[dict[str, Any] | None, Response | None]:
        refused = require_token(request.headers.get("x-cf-token"))
        if refused:
            return None, refused
        try:
            body = await request.json()
        except ValueError:
            return None, _err(400, "the request body isn't JSON")
        return (body if isinstance(body, dict) else {}), None

    def refuse_hidden(day: date) -> Response | None:
        if hidden(day, settings):
            return _err(403, f"{day.isoformat()} is in ledger months 7-12, which stay off this page until gate 16 is "
                             "recorded (config/gate_record.json)")
        return None

    async def index(request: Request) -> Response:
        store, _, why = store_or_none()
        try:
            day = day_of(request)
        except ValueError:
            return _err(400, "the date must be YYYY-MM-DD")
        if (r := refuse_hidden(day)) is not None:
            return r
        if store is None:
            return _ok({"date": day.isoformat(), "today": today_et(now()).isoformat(), "synthetic": synthetic,
                        "rules": day_rules(day, settings, features, cal), "gameplan": None,
                        "status": {"state": "none"}, "edits": [], "days": [], "empty": why,
                        "cap_usd": float(settings["gameplan_max_usd_per_file"]), "accept": list(SUPPORTED_SUFFIXES)})
        return _ok(payload(store, day))

    async def upload(request: Request) -> Response:
        refused = require_token(request.headers.get("x-cf-token"))
        if refused:
            return refused
        store, up_dir, why = store_or_none()
        if store is None:
            return _err(503, why or "no store")
        name = SAFE_NAME.sub("_", unquote(request.headers.get("x-file-name") or "")).strip(" .") or "gameplan"
        if Path(name).suffix.lower() not in SUPPORTED_SUFFIXES:
            return _err(415, f"{name}: a gameplan is a .md, .pdf or .docx file")
        try:
            day = date.fromisoformat(request.headers.get("x-gameplan-date") or today_et(now()).isoformat())
        except ValueError:
            return _err(400, "the date must be YYYY-MM-DD")
        if (r := refuse_hidden(day)) is not None:
            return r
        body = await request.body()
        if not body:
            return _err(400, f"{name} is empty")
        if len(body) > UPLOAD_MAX_BYTES:
            return _err(413, f"{name} is over {UPLOAD_MAX_BYTES // (1024 * 1024)} MB")
        uid = uuid.uuid4().hex[:12]
        dest = up_dir / uid / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)

        def work() -> dict[str, Any]:
            try:
                conv = convert(dest)
            except CannotConvert as e:
                raise ValueError(str(e)) from e
            sha = gp.sha256(dest)
            entry = store.load_index().get(sha) or {}
            if entry.get("date") and store.load(date.fromisoformat(entry["date"])) is not None:
                return {"already": entry["date"]}
            if store.load_response(sha) is not None:
                return {"cached": True}
            est = gp.estimate(client(), settings, conv, name)
            return {"tokens": est.input_tokens, "worst": gp.cents_up(est.usd_worst), "input_usd": est.usd_input, "est": est}

        try:
            r = await run_in_threadpool(work)
        except ValueError as e:
            shutil.rmtree(dest.parent, ignore_errors=True)
            return _err(422, str(e))
        except Exception as e:  # the token count failed (no key, no network): nothing was parsed or paid
            shutil.rmtree(dest.parent, ignore_errors=True)
            return _err(502, f"the cost estimate failed, nothing was sent for parsing: {type(e).__name__}: {e}")
        if r.get("already"):
            shutil.rmtree(dest.parent, ignore_errors=True)
            return _ok({"already": r["already"], "name": name})
        staged[uid] = Staged(uid, dest, day, r.get("worst"), r.get("tokens"), bool(r.get("cached")), r.get("est"))
        exists = store.load(day) is not None
        return _ok({"upload": uid, "name": name, "date": day.isoformat(), "cached": bool(r.get("cached")),
                    "input_tokens": r.get("tokens"), "worst_usd": r.get("worst"),
                    "cap_usd": float(settings["gameplan_max_usd_per_file"]),
                    "over_cap": (r.get("worst") or 0) > float(settings["gameplan_max_usd_per_file"]),
                    "replaces": exists, "after_open": now() >= datetime.combine(day, RTH_OPEN, tzinfo=ET)})

    async def parse(request: Request) -> Response:
        body, refused = await guarded(request)
        if refused:
            return refused
        store, _, why = store_or_none()
        if store is None:
            return _err(503, why or "no store")
        st = staged.get(str(body.get("upload") or ""))
        if st is None:
            return _err(404, "that upload is gone: drop the file again")
        if (r := refuse_hidden(st.day)) is not None:
            return r
        cap = float(settings["gameplan_max_usd_per_file"])
        approve = body.get("approve_usd")
        if not st.cached:
            if st.worst_usd is None or approve is None or float(approve) + 1e-9 < st.worst_usd:
                return _err(400, f"approve the worst case (${st.worst_usd:.2f}) to parse")
            if st.worst_usd > cap:
                return _err(400, f"up to ${st.worst_usd:.2f} is over the ${cap:.2f} per-file limit: parse it with "
                                 f"`uv run nqbot gameplan <file> --approve-usd {st.worst_usd:.2f}` after your OK")
        replace = bool(body.get("replace"))
        if store.load(st.day) is not None and not replace:
            return _err(409, f"a gameplan for {st.day.isoformat()} is stored already: tick Replace to use this file")

        def work():
            from nqbot.jobs.lock import JobLock

            store.processed_dir.mkdir(parents=True, exist_ok=True)
            with JobLock(store.lock_path, "gameplan (Control Panel)", unlock_cmd="uv run nqbot unlock --gameplan"):
                return gp.parse_file(client(), settings, store, st.file, given_date=st.day, replace=replace,
                                     cap_usd=None if st.cached else max(st.worst_usd or 0, 0.0), est=st.est,
                                     out=lambda _m: None)

        try:
            res = await run_in_threadpool(work)
        except Exception as e:  # GameplanRefused, LockHeld, a missing key: nothing parsed
            return _err(409, f"{type(e).__name__}: {e}")
        if res.status not in ("ok", "flagged", "cached"):
            # never retried by itself (owner 2026-11-16): the staged file stays, and only another click sends it again
            again = "" if st.cached else f" (another paid call, up to ${st.worst_usd:.2f})"
            return JSONResponse({"error": f"{res.file}: the parse {res.status}: {res.message}. Nothing is retried "
                                          f"automatically: Parse again sends it again{again}.",
                                 "cost_usd": round(res.cost_usd, 4)}, status_code=422, headers={"cache-control": "no-store"})
        staged.pop(st.uid, None)
        shutil.rmtree(st.file.parent, ignore_errors=True)
        out = payload(store, res.date)
        out["parsed"] = {"file": res.file, "status": res.status, "cost_usd": round(res.cost_usd, 4),
                         "estimate_usd": None if st.cached else st.worst_usd}
        return _ok(out)

    async def discard(request: Request) -> Response:
        body, refused = await guarded(request)
        if refused:
            return refused
        st = staged.pop(str(body.get("upload") or ""), None)
        if st is not None:
            shutil.rmtree(st.file.parent, ignore_errors=True)
        return _ok({"ok": True})

    async def edit(request: Request) -> Response:
        body, refused = await guarded(request)
        if refused:
            return refused
        store, _, why = store_or_none()
        if store is None:
            return _err(503, why or "no store")
        try:
            day = date.fromisoformat(str(body.get("date") or ""))
        except ValueError:
            return _err(400, "the date must be YYYY-MM-DD")
        if (r := refuse_hidden(day)) is not None:
            return r
        doc = store.load_effective(day)
        if doc is None:
            return _err(404, f"no gameplan for {day.isoformat()}")
        field = str(body.get("field") or "")
        reason = (str(body.get("reason") or "").strip() or "edited on the Gameplan page")[:300]
        if field == "other_conditions":
            # the plan-wide conditions (owner 2026-11-17): one item edited (`index`), emptied (removed) or added (no
            # index); stored as one override of the whole list, so a re-parse never loses it
            items = [str(x) for x in (doc.get("other_conditions") or [])]
            text = str(body.get("value") or "").strip()[:1000]
            idx = body.get("index")
            if idx is None:
                if not text:
                    return _err(422, "an empty condition isn't added")
                items.append(text)
            elif isinstance(idx, int) and 0 <= idx < len(items):
                if text:
                    items[idx] = text
                else:
                    items.pop(idx)
            else:
                return _err(404, "no such condition (reload the page)")
            resolves = sorted({f.get("field", "") for f in doc.get("flags") or []
                               if f.get("review", True) and f.get("field", "").startswith("other_conditions")})
            ov.set_value(store.processed_dir, day, ["other_conditions"], items, reason, resolves=resolves)
            return _ok(payload(store, day))
        scen_field = field.removeprefix("scenario.") if field.startswith("scenario.") else None
        try:
            value = edit_value(scen_field or field, body.get("value"))
        except (ValueError, TypeError) as e:
            return _err(422, str(e))
        if field in PLAN_FIELDS:
            path: list[Any] = list(PLAN_FIELDS[field])
            resolves = review_flags_for(doc, field)
        elif field in ENTRY_FIELDS:
            zid = str(body.get("entry") or "")
            zones = doc.get("zones") or []
            z = next((x for x in zones if x.get("id") == zid), None)
            if z is None:
                return _err(404, f"no entry {zid} in the gameplan")
            path = ["zones", ov.entry_selector(z, zones), *ENTRY_FIELDS[field]]
            resolves = review_flags_for(doc, field, entry=zid)
        elif scen_field in SCENARIO_FIELDS:
            k, sid = body.get("set"), str(body.get("scenario") or "")
            sets = doc.get("scenario_sets") or []
            if not isinstance(k, int) or not 0 <= k < len(sets) or not any(x.get("id") == sid for x in sets[k].get("scenarios") or []):
                return _err(404, f"no scenario {sid} in that set")
            sel = {"frame": sets[k].get("frame"), "label_as_written": sets[k].get("label_as_written")}
            path = ["scenario_sets", sel, "scenarios", {"id": sid}, scen_field]
            resolves = review_flags_for(doc, field, scenario=sid)
        else:
            return _err(422, f"not an editable field: {field}")
        ov.set_value(store.processed_dir, day, path, value, reason, resolves=resolves)
        return _ok(payload(store, day))

    async def accept_flag(request: Request) -> Response:
        body, refused = await guarded(request)
        if refused:
            return refused
        store, _, why = store_or_none()
        if store is None:
            return _err(503, why or "no store")
        try:
            day = date.fromisoformat(str(body.get("date") or ""))
        except ValueError:
            return _err(400, "the date must be YYYY-MM-DD")
        if (r := refuse_hidden(day)) is not None:
            return r
        doc = store.load_effective(day)
        if doc is None:
            return _err(404, f"no gameplan for {day.isoformat()}")
        field, text = str(body.get("field") or ""), str(body.get("flag") or "")
        if not any(f.get("review", True) and f.get("field") == field and f.get("reason") == text for f in doc.get("flags") or []):
            return _err(404, "no such open review flag (it may have changed: reload the page)")
        ov.accept(store.processed_dir, day, field, text)
        return _ok(payload(store, day))

    async def confirm(request: Request) -> Response:
        body, refused = await guarded(request)
        if refused:
            return refused
        store, _, why = store_or_none()
        if store is None:
            return _err(503, why or "no store")
        try:
            day = date.fromisoformat(str(body.get("date") or ""))
        except ValueError:
            return _err(400, "the date must be YYYY-MM-DD")
        if (r := refuse_hidden(day)) is not None:
            return r
        doc = store.load_effective(day)
        if doc is None:
            return _err(404, f"no gameplan for {day.isoformat()}")
        open_flags = sum(1 for f in doc.get("flags") or [] if f.get("review", True))
        if open_flags and now() < datetime.combine(day, RTH_OPEN, tzinfo=ET):
            return _err(409, f"{open_flags} review flag{'s are' if open_flags > 1 else ' is'} still open: fix each one or mark it "
                             "OK as parsed (before 09:30 ET a confirmation for the day's trading needs none open)")
        ov.confirm(store.processed_dir, day, (str(body.get("note") or "").strip() or "confirmed on the Gameplan page")[:300])
        return _ok(payload(store, day))

    return [Route("/api/gameplan", index, methods=["GET"]),
            Route("/api/gameplan/upload", upload, methods=["POST"]),
            Route("/api/gameplan/parse", parse, methods=["POST"]),
            Route("/api/gameplan/discard", discard, methods=["POST"]),
            Route("/api/gameplan/edit", edit, methods=["POST"]),
            Route("/api/gameplan/confirm", confirm, methods=["POST"]),
            Route("/api/gameplan/accept", accept_flag, methods=["POST"])]


__all__ = ["routes", "day_rules", "status", "edit_value", "parse_range", "today_et", "hidden", "review_flags_for",
           "entry_col", "SyntheticClient"]
