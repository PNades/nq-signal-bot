"""Review rounds and answers on the SSD (SPEC §8; owner's plan 2026-11-15).

- A **round** is a frozen sample of cards from one build and labels key: `<data_dir>/reviews/rounds/<round>/round.json`
  (the build, the labels, the sampler's settings, the cards in order with their stratum) and one file per card
  (`cards/<card>.json`, `cards.card_payload`: its `pre` half, and the `post` half with the outcome).
- The **answers** (§8 "Saved to `data/reviews/*.parquet`"): `<data_dir>/reviews/answers_<round>.parquet`, one row per
  answered card, rewritten whole (atomically) on each answer; an answer is final once the outcome was shown.
- **§7 rule 2b:** review cards come from development months only. A round refuses any other day when it is drawn, and
  every card read checks its day again (`require_development`), so a round file edited by hand can't show a test month.
- **The decision-time cut (§8):** `public_card` gives the card without its `post` half and outcome until the card is
  answered; the API never sends anything else.
- **Preview rounds (owner 2026-11-16, SPEC §13 Q-review-sample):** a round drawn with `preview=True` lives apart, in
  `<data_dir>/reviews/preview/rounds/<round>/`, its answers in `<data_dir>/reviews/preview/answers_<round>.parquet`, every
  answer row marked `preview`; so `data/reviews/*.parquet` holds the real answers only. **Every touch a preview card can
  show** (its own and every earlier or later touch on its chart, before and after the answer) is left out of every real
  round drawn afterwards (`preview_touches`, `exclude_seen`): the owner may have seen its outcome. Builds differ (13c
  winners move touches by a few trades), so a candidate is left out when its touching trade is on the same day, in the
  same direction, within `PREVIEW_MATCH_SEC` of a shown touch.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from nqbot.months import DEVELOPMENT, month_index, require_development
from nqbot.review import sampler as smp
from nqbot.storage import Paths, atomic_write_bytes, atomic_write_text

ROUND_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
CARD_ID = re.compile(r"^c\d{2,4}$")
VERDICTS = ("take", "skip")
ENTRY_CHOICES = ("order_flow", "audit", "plain")     # §8: order-flow trigger / audit / plain at the level
REASON_MAX = 600                                     # §8: one or two sentences
CONFIDENCE = range(1, 6)                             # §8: confidence 1-5
ANSWER_COLUMNS = ("round", "preview", "card", "decision_id", "pair_id", "event_id", "day", "stratum", "why",
                  "answered_at", "verdict", "reason", "entry_choice", "stop", "target", "confidence", "result", "r",
                  "structure", "correct")
PREVIEW_MATCH_SEC = 60     # a real-round candidate this close to a touch a preview card showed is left out


class ReviewRefused(ValueError):
    """A request the review store refuses (unknown round or card, a test month, a second answer, a bad field)."""


def reviews_dir(paths: Paths) -> Path:
    return paths.data_dir / "reviews"


def _base(paths: Paths, preview: bool) -> Path:
    return reviews_dir(paths) / "preview" if preview else reviews_dir(paths)


def _check_id(rid: str) -> None:
    if not ROUND_ID.match(rid or ""):
        raise ReviewRefused(f"bad round id {rid!r}")


def new_round_dir(paths: Paths, rid: str, preview: bool) -> Path:
    _check_id(rid)
    return _base(paths, preview) / "rounds" / rid


def is_preview(paths: Paths, rid: str) -> bool:
    """Whether round `rid` is a preview round (a preview round's folder is under reviews/preview/)."""
    _check_id(rid)
    return (_base(paths, True) / "rounds" / rid / "round.json").is_file()


def round_dir(paths: Paths, rid: str) -> Path:
    return new_round_dir(paths, rid, is_preview(paths, rid))


def answers_path(paths: Paths, rid: str) -> Path:
    return _base(paths, is_preview(paths, rid)) / f"answers_{rid}.parquet"


# ── drawing a round ────────────────────────────────────────────────────────────────────────────────────────────────

def candidates(build_dir: Path, labels_key: str, days: list[date], settings: dict[str, Any]) -> list[smp.Candidate]:
    """One candidate per event of `days` (sampler module note), read-only from the build and its labels."""
    import pyarrow.parquet as pq

    from nqbot.review.cards import et_clock, feature_rows
    from nqbot.tuning import policy as pol

    order_type = {s: settings["entry_order_type"][s] for s in ("c", "d")}
    out: list[smp.Candidate] = []
    for d in days:
        iso = d.isoformat()
        dec = pq.read_table(build_dir / "decisions" / f"{iso}.parquet",
                            columns=["decision_id", "event_id", "touch_group_id", "style", "firing_n", "decision_ts",
                                     "touch_ts", "direction", "tradeable", "event_type"]).to_pylist()
        chosen = smp.choose_events(dec)
        if not chosen:
            continue
        ids = {c["decision_id"] for c in chosen.values()}
        rows = feature_rows(build_dir, iso, list(chosen.values()), ids)
        sub_of = dict(zip(*pq.read_table(build_dir / "levels" / f"{iso}.parquet", columns=["level_id", "sub_type"]).to_pydict().values()))
        pairs = [p for p in pq.read_table(build_dir / "pairs" / labels_key / f"{iso}.parquet").to_pylist()
                 if p["decision_id"] in ids]
        best = pol.pick_bootstrap(pairs)
        for c in chosen.values():
            p = best.get((c["decision_id"], order_type[c["style"]]))
            if p is None or p.get("fill_status") != "filled":
                continue
            r = rows.get(c["decision_id"], {})
            hh, mm = (int(x) for x in et_clock(int(c["decision_ts"])).split(":")[:2])
            stack = int(r.get("stack_count") or 1)
            wdd = r.get("with_day_direction")
            out.append(smp.Candidate(
                day=iso, decision_id=c["decision_id"], event_id=c["event_id"], touch_group_id=c["touch_group_id"],
                style=c["style"], decision_ts=int(c["decision_ts"]), minute_et=hh * 60 + mm, stack_count=stack,
                # the primary member's type, else (no primary: the members rank equally) the first founding member's
                level_group=smp.level_group(r.get("primary_member_type") or next(
                    (sub_of.get(x) for x in (r.get("founding_level_ids") or []) if sub_of.get(x)), None), c.get("event_type")),
                lost=p.get("out_none") == "stop", never_take=stack <= 1 and wdd is not None and not bool(wdd),
                weak=stack <= 1, extra={"pair_id": p["pair_id"], "touch_ts": int(c["touch_ts"]),
                                        "direction": int(c["direction"])}))
    return out


def preview_touches(paths: Paths) -> list[tuple[str, int, int]]:
    """(day, touch time in s, direction) of every touch any preview card can show, before and after its answer."""
    root = _base(paths, True) / "rounds"
    out: set[tuple[str, int, int]] = set()
    if root.is_dir():
        for f in sorted(root.glob("*/cards/*.json")):
            c = json.loads(f.read_text(encoding="utf-8"))
            for half in ("pre", "post"):
                for e in (c.get(half) or {}).get("events") or []:
                    out.add((c["day"], int(e["t"]), int(e["dir"])))
    return sorted(out)


def exclude_seen(cands: list[smp.Candidate], seen: list[tuple[str, int, int]],
                 within_sec: int = PREVIEW_MATCH_SEC) -> tuple[list[smp.Candidate], int]:
    """The candidates whose touch no preview card showed (same day and direction, touching trades within
    `within_sec`), and how many were left out."""
    by_day: dict[tuple[str, int], list[int]] = {}
    for day, t, d in seen:
        by_day.setdefault((day, d), []).append(t)
    keep = []
    for c in cands:
        t = int(c.extra["touch_ts"]) // 1_000_000_000
        if any(abs(t - s) <= within_sec for s in by_day.get((c.day, int(c.extra["direction"])), [])):
            continue
        keep.append(c)
    return keep, len(cands) - len(keep)


def development_days(build_dir: Path, settings: dict[str, Any]) -> list[date]:
    """The build's sessions in development months (§7 rule 2b), in order."""
    plan = json.loads((build_dir / "build_plan.json").read_text(encoding="utf-8"))
    days = [date.fromisoformat(s if isinstance(s, str) else s["day"]) for s in plan["sessions"]]
    return [d for d in days if month_index(d, settings) in DEVELOPMENT]


def write_round(paths: Paths, rid: str, meta: dict[str, Any], cards: list[dict[str, Any]], settings: dict[str, Any],
                preview: bool = False) -> Path:
    """Write a round (refusing any card outside the development months); a preview round apart from the real ones."""
    rd = new_round_dir(paths, rid, preview)
    if any((new_round_dir(paths, rid, p) / "round.json").exists() for p in (False, True)):
        raise ReviewRefused(f"round {rid} exists already (rounds are never redrawn in place)")
    for c in cards:
        require_development(date.fromisoformat(c["day"]), settings)
    for c in cards:
        atomic_write_text(rd / "cards" / f"{c['id']}.json", json.dumps(c, separators=(",", ":")))
    meta = {**meta, "id": rid, "preview": preview, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cards": [{"id": c["id"], "day": c["day"], "decision_id": c["decision_id"], "pair_id": c["pair_id"],
                       "stratum": c.get("stratum"), "why": c.get("why")} for c in cards]}
    atomic_write_text(rd / "round.json", json.dumps(meta, indent=1))
    return rd


def draw_round(env, build_key: str, labels_key: str, n: int, seed: int, rid: str | None = None,
               echo=print, preview: bool = False) -> Path:
    """Sample and write a round from build `build_key` / labels `labels_key` (development months only); a real round
    leaves out every touch a preview card showed."""
    from nqbot.checks import feature_build as fb
    from nqbot.review import cards as cd

    build_dir = fb.out_dir(env, build_key)
    if not (build_dir / "pairs" / labels_key).is_dir():
        raise ReviewRefused(f"no labels {labels_key} in build {build_key} ({build_dir})")
    days = development_days(build_dir, env.settings)
    if not days:
        raise ReviewRefused(f"build {build_key} has no development-month sessions")
    cands = candidates(build_dir, labels_key, days, env.settings)
    excluded = 0
    if not preview:
        cands, excluded = exclude_seen(cands, preview_touches(env.paths))
        echo(f"{excluded} candidates left out: their touch was shown in a preview card")
    picks = smp.sample(cands, n, seed)
    echo(f"{len(cands)} candidates over {len(days)} development sessions; {len(picks)} cards")
    rid = rid or f"{'p' if preview else 'r'}{datetime.now().strftime('%Y%m%d')}-{build_key[:6]}-s{seed}"
    by_day: dict[str, list[tuple[int, smp.Pick]]] = {}
    for i, p in enumerate(picks):
        by_day.setdefault(p.cand.day, []).append((i, p))
    cards: list[dict[str, Any] | None] = [None] * len(picks)
    for k, (iso, items) in enumerate(sorted(by_day.items()), 1):
        dd = cd.load_day(env, build_dir, labels_key, date.fromisoformat(iso), {p.cand.decision_id for _, p in items},
                         {p.cand.extra["pair_id"] for _, p in items})
        for i, p in items:
            cards[i] = cd.card_payload(dd, f"c{i + 1:02d}", p.cand.decision_id, p.cand.extra["pair_id"], p.stratum, p.why)
        echo(f"  {k}/{len(by_day)} {iso}: {len(items)} card(s)")
        del dd
    meta = {"build_key": build_key, "labels_key": labels_key, "n": len(picks), "seed": seed,
            "sampler": {"lost_share": smp.LOST_SHARE, "never_share": smp.NEVER_SHARE, "never_min": smp.NEVER_MIN,
                        "max_per_day": smp.MAX_PER_DAY},
            "coverage": smp.coverage(picks), "months": sorted({month_index(d, env.settings) for d in days}),
            "excluded_preview_touches": excluded}
    return write_round(paths=env.paths, rid=rid, meta=meta, cards=[c for c in cards if c is not None],
                       settings=env.settings, preview=preview)


def restack_round(env, rid: str, echo=print) -> dict[str, int]:
    """Recompute only the `stack` field of a stored round's cards (owner 2026-11-16, round 5: the display rule for
    which levels are listed changed; SPEC §13 Q-review-stack). Not a redraw: the same cards, every other field kept
    byte-for-byte as stored (checked); the old card files are copied to `restack_backup/<time>/` first. Answers are
    untouched."""
    import shutil

    from nqbot.checks import feature_build as fb
    from nqbot.review import cards as cd

    meta = load_round(env.paths, rid)
    rd = round_dir(env.paths, rid)
    build_dir = fb.out_dir(env, meta["build_key"])
    backup = rd / "restack_backup" / datetime.now().strftime("%Y%m%d-%H%M%S")
    backup.mkdir(parents=True, exist_ok=False)
    by_day: dict[str, list[dict[str, Any]]] = {}
    for c in meta["cards"]:
        by_day.setdefault(c["day"], []).append(c)
    changed = same = 0
    for iso, cs in sorted(by_day.items()):
        require_development(date.fromisoformat(iso), env.settings)
        dd = cd.load_day(env, build_dir, meta["labels_key"], date.fromisoformat(iso), {c["decision_id"] for c in cs},
                         {c["pair_id"] for c in cs})
        for c in cs:
            f = rd / "cards" / f"{c['id']}.json"
            old = json.loads(f.read_text(encoding="utf-8"))
            dec = next(d for d in dd.decisions if d["decision_id"] == c["decision_id"])
            row = dd.rows.get(c["decision_id"], {})
            new_stack = cd.stack_payload(dd, row, int(dec["decision_ts"]), float(dec["zone_lo"]), float(dec["zone_hi"]),
                                         dec.get("event_type") == "level")
            shutil.copy2(f, backup / f.name)
            new = {**old, "stack": new_stack}
            assert {k: v for k, v in new.items() if k != "stack"} == {k: v for k, v in old.items() if k != "stack"}
            if new_stack != old.get("stack"):
                changed += 1
            else:
                same += 1
            atomic_write_text(f, json.dumps(new, separators=(",", ":")))
        echo(f"  {iso}: {len(cs)} card(s)")
        del dd
    echo(f"round {rid}: stacked lists recomputed, {changed} changed, {same} unchanged; old files in {backup}")
    return {"changed": changed, "same": same}


# ── reading rounds, answering ──────────────────────────────────────────────────────────────────────────────────────

def list_rounds(paths: Paths) -> list[dict[str, Any]]:
    """Every round, the real ones first (newest first), then the preview ones."""
    out = []
    for preview in (False, True):
        root = _base(paths, preview) / "rounds"
        if not root.is_dir():
            continue
        for p in sorted(root.iterdir()):
            f = p / "round.json"
            if f.is_file():
                try:
                    m = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                out.append({**m, "preview": preview})
    out.sort(key=lambda m: m.get("created") or "", reverse=True)
    out.sort(key=lambda m: bool(m["preview"]))       # stable: real first, each newest first
    return out


def load_round(paths: Paths, rid: str) -> dict[str, Any]:
    f = round_dir(paths, rid) / "round.json"
    if not f.is_file():
        raise ReviewRefused(f"no review round {rid!r}")
    return json.loads(f.read_text(encoding="utf-8"))


def load_card(paths: Paths, rid: str, cid: str, settings: dict[str, Any]) -> dict[str, Any]:
    if not CARD_ID.match(cid or ""):
        raise ReviewRefused(f"bad card id {cid!r}")
    f = round_dir(paths, rid) / "cards" / f"{cid}.json"
    if not f.is_file():
        raise ReviewRefused(f"no card {cid} in round {rid}")
    card = json.loads(f.read_text(encoding="utf-8"))
    require_development(date.fromisoformat(card["day"]), settings)   # §7 rule 2b, on every read
    return card


def read_answers(paths: Paths, rid: str) -> dict[str, dict[str, Any]]:
    import pyarrow.parquet as pq

    p = answers_path(paths, rid)
    if not p.is_file():
        return {}
    return {r["card"]: r for r in pq.read_table(p).to_pylist()}


def public_card(card: dict[str, Any], answer: dict[str, Any] | None) -> dict[str, Any]:
    """§8: the card as the owner may see it: before an answer only its `pre` half (nothing after the decision time,
    no outcome, no stratum); after it, everything."""
    from nqbot.review.cards import HIDDEN_READING_LABELS

    if "readings" in card:
        card = {**card, "readings": [r for r in card["readings"] if r.get("label") not in HIDDEN_READING_LABELS]}
    if answer is None:
        hidden = {"post", "outcome", "stratum", "why"}
        return {**{k: v for k, v in card.items() if k not in hidden}, "answer": None}
    return {**card, "answer": answer}


def correct(verdict: str, r: float | None) -> bool | None:
    """Take and the trade made money, or Skip and it lost: the owner's call agreed with the outcome."""
    if r is None:
        return None
    return (r > 0) if verdict == "take" else (r <= 0)


def validate_answer(body: dict[str, Any]) -> dict[str, Any]:
    verdict = body.get("verdict")
    if verdict not in VERDICTS:
        raise ReviewRefused("verdict must be take or skip")
    reason = str(body.get("reason") or "").strip()
    if not reason:
        raise ReviewRefused("the reason is required (one or two sentences, §8)")
    if len(reason) > REASON_MAX:
        raise ReviewRefused(f"the reason is longer than {REASON_MAX} characters")
    entry = body.get("entry_choice") or None
    if entry is not None and entry not in ENTRY_CHOICES:
        raise ReviewRefused(f"entry choice must be one of {', '.join(ENTRY_CHOICES)}")
    conf = body.get("confidence")
    if conf in ("", None):
        conf = None
    elif not isinstance(conf, int) or conf not in CONFIDENCE:
        raise ReviewRefused("confidence must be 1-5")

    def price(k: str) -> float | None:
        v = body.get(k)
        if v in (None, ""):
            return None
        try:
            x = float(v)
        except (TypeError, ValueError) as e:
            raise ReviewRefused(f"{k} must be a price") from e
        if not (0 < x < 1e6):
            raise ReviewRefused(f"{k} must be a price")
        return x

    return {"verdict": verdict, "reason": reason, "entry_choice": entry, "stop": price("stop"),
            "target": price("target"), "confidence": conf}


def answer(paths: Paths, rid: str, cid: str, body: dict[str, Any], settings: dict[str, Any], *, now=None) -> dict[str, Any]:
    """Store the owner's answer to a card (once) and return it with the outcome."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from nqbot.jobs.lock import JobLock, LockHeld

    a = validate_answer(body)
    card = load_card(paths, rid, cid, settings)
    rd = round_dir(paths, rid)
    rd.mkdir(parents=True, exist_ok=True)
    try:
        lock = JobLock(rd / ".answers.lock", "review answers", unlock_cmd="delete the answers lock only if no panel runs")
        lock.acquire()
    except LockHeld as e:
        raise ReviewRefused(f"another answer is being saved right now ({e}); try again") from e
    try:
        have = read_answers(paths, rid)
        if cid in have:
            raise ReviewRefused(f"card {cid} is answered already (an answer is final once its outcome was shown)")
        out = card.get("outcome") or {}
        row = {"round": rid, "preview": is_preview(paths, rid), "card": cid, "decision_id": card["decision_id"],
               "pair_id": card["pair_id"], "event_id": card["event_id"], "day": card["day"],
               "stratum": card.get("stratum"), "why": card.get("why"),
               "answered_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"), **a,
               "result": out.get("result"), "r": out.get("r"), "structure": out.get("structure"),
               "correct": correct(a["verdict"], out.get("r"))}
        rows = [*have.values(), row]
        rows.sort(key=lambda r: r["card"])
        table = pa.Table.from_pylist([{k: r.get(k) for k in ANSWER_COLUMNS} for r in rows], schema=ANSWER_SCHEMA)
        sink = pa.BufferOutputStream()
        pq.write_table(table, sink, compression="zstd")
        atomic_write_bytes(answers_path(paths, rid), sink.getvalue().to_pybytes())
    finally:
        lock.release()
    return row


def _schema():
    import pyarrow as pa

    return pa.schema([("round", pa.string()), ("preview", pa.bool_()), ("card", pa.string()), ("decision_id", pa.string()),
                      ("pair_id", pa.string()), ("event_id", pa.string()), ("day", pa.string()),
                      ("stratum", pa.string()), ("why", pa.string()), ("answered_at", pa.string()), ("verdict", pa.string()),
                      ("reason", pa.string()), ("entry_choice", pa.string()), ("stop", pa.float64()),
                      ("target", pa.float64()), ("confidence", pa.int64()), ("result", pa.string()),
                      ("r", pa.float64()), ("structure", pa.string()), ("correct", pa.bool_())])


ANSWER_SCHEMA = _schema()


def summary(paths: Paths, rid: str) -> dict[str, Any]:
    m = load_round(paths, rid)
    ans = read_answers(paths, rid)
    cards = [{**c, "answered": c["id"] in ans, "verdict": ans.get(c["id"], {}).get("verdict"),
              "correct": ans.get(c["id"], {}).get("correct")} for c in m["cards"]]
    for c in cards:
        if not c["answered"]:
            c.pop("stratum", None)      # the stratum hints at the outcome ("lost"): shown only after the answer
            c.pop("why", None)
    return {"id": rid, "build_key": m.get("build_key"), "labels_key": m.get("labels_key"), "created": m.get("created"),
            "months": m.get("months"), "n": len(cards), "answered": len(ans),
            "correct": sum(1 for a in ans.values() if a.get("correct")), "cards": cards,
            "synthetic": bool(m.get("synthetic")), "preview": is_preview(paths, rid)}


def asdict_pick(p: smp.Pick) -> dict[str, Any]:
    return {**asdict(p.cand), "stratum": p.stratum}
