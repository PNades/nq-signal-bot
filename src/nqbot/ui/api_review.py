"""The Review page's JSON API (SPEC §8, §9b item 5; build stage 4).

- `GET /api/review[?round=R]`: the rounds and the chosen one (default the newest): its cards in order with which are
  answered (a card's stratum only once answered: "lost" would give its outcome away), the running score.
- `GET /api/review/card?round=R&card=C`: one card as the owner may see it (`store.public_card`): until it is answered,
  only what was known at its decision time (§8: the chart stops there and the outcome stays hidden).
- `POST /api/review/answer` (token): Take / Skip, the reason, the optional entry / stop / target / confidence; stored
  once in `answers_<round>.parquet`, then the whole card (the rest of the path and the outcome) comes back.

`nqbot ui --synthetic` serves hand-built synthetic rounds (`review/synthetic.py`) whose answers stay in memory only,
never in the owner's answer files.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from nqbot.months import NotDevelopmentMonth
from nqbot.review import store as rs
from nqbot.storage import Paths


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status, headers={"cache-control": "no-store"})


def _ok(data: Any) -> JSONResponse:
    return JSONResponse(data, headers={"cache-control": "no-store"})


class SyntheticRounds:
    """Synthetic rounds for `nqbot ui --synthetic`: built once, answers kept in memory."""

    def __init__(self) -> None:
        self._rounds: dict[str, tuple[dict[str, Any], dict[str, dict[str, Any]]]] | None = None
        self.answers: dict[str, dict[str, dict[str, Any]]] = {}

    def rounds(self) -> dict[str, tuple[dict[str, Any], dict[str, dict[str, Any]]]]:
        if self._rounds is None:
            from nqbot.review import synthetic

            meta, cards = synthetic.synthetic_round()
            self._rounds = {meta["id"]: (meta, {c["id"]: c for c in cards})}
        return self._rounds

    def summary(self, rid: str) -> dict[str, Any]:
        meta, cards = self.rounds()[rid]
        ans = self.answers.get(rid, {})
        rows = []
        for c in meta["cards"]:
            a = ans.get(c["id"])
            row = {**c, "answered": a is not None, "verdict": a and a["verdict"], "correct": a and a["correct"]}
            if a is None:
                row.pop("stratum", None)
                row.pop("why", None)
            rows.append(row)
        return {"id": rid, "build_key": meta.get("build_key"), "labels_key": meta.get("labels_key"),
                "created": meta.get("created"), "months": meta.get("months"), "n": len(rows), "answered": len(ans),
                "correct": sum(1 for a in ans.values() if a.get("correct")), "cards": rows, "synthetic": True}


def routes(paths_fn: Callable[[], Paths | None], settings: dict[str, Any],
           require_token: Callable[[str | None], Response | None], *, synthetic: bool = False) -> list[Route]:
    syn = SyntheticRounds() if synthetic else None

    def paths_or_none() -> Paths | None:
        return None if synthetic else paths_fn()

    async def index(request: Request) -> Response:
        rid = request.query_params.get("round") or None
        if syn is not None:
            ids = list(syn.rounds())
            rid = rid if rid in ids else ids[0]
            return _ok({"synthetic": True, "rounds": [{"id": i, "n": len(syn.rounds()[i][0]["cards"])} for i in ids],
                        "round": syn.summary(rid), "empty": None})
        paths = paths_or_none()
        if paths is None:
            return _ok({"synthetic": False, "rounds": [], "round": None,
                        "empty": "The SSD isn't connected, so no review round can be read."})
        rounds = rs.list_rounds(paths)
        if not rounds:
            return _ok({"synthetic": False, "rounds": [], "round": None,
                        "empty": "No review round yet. Draw one with: uv run nqbot review round-new --build-key <key> "
                                 "--labels-key <key>"})
        ids = [m["id"] for m in rounds]
        rid = rid if rid in ids else ids[0]
        try:
            summ = rs.summary(paths, rid)
        except rs.ReviewRefused as e:
            return _err(404, str(e))
        return _ok({"synthetic": False, "rounds": [{"id": m["id"], "n": len(m.get("cards") or []), "preview": bool(m.get("preview")),
                                                    "build_key": m.get("build_key"), "created": m.get("created")}
                                                   for m in rounds],
                    "round": summ, "empty": None})

    async def card(request: Request) -> Response:
        rid, cid = request.query_params.get("round") or "", request.query_params.get("card") or ""
        if syn is not None:
            r = syn.rounds().get(rid)
            if r is None or cid not in r[1]:
                return _err(404, f"no card {cid} in round {rid}")
            return _ok(rs.public_card(r[1][cid], syn.answers.get(rid, {}).get(cid)))
        paths = paths_or_none()
        if paths is None:
            return _err(503, "The SSD isn't connected.")
        try:
            c = rs.load_card(paths, rid, cid, settings)
            return _ok(rs.public_card(c, rs.read_answers(paths, rid).get(cid)))
        except NotDevelopmentMonth as e:
            return _err(403, f"Refused: {e}")
        except rs.ReviewRefused as e:
            return _err(404, str(e))

    async def answer(request: Request) -> Response:
        refused = require_token(request.headers.get("x-cf-token"))
        if refused:
            return refused
        try:
            body = await request.json()
        except ValueError:
            return _err(400, "the request body isn't JSON")
        if not isinstance(body, dict):
            return _err(400, "the request body isn't an object")
        rid, cid = str(body.get("round") or ""), str(body.get("card") or "")
        try:
            if syn is not None:
                r = syn.rounds().get(rid)
                if r is None or cid not in r[1]:
                    return _err(404, f"no card {cid} in round {rid}")
                have = syn.answers.setdefault(rid, {})
                if cid in have:
                    raise rs.ReviewRefused(f"card {cid} is answered already (an answer is final once its outcome was shown)")
                a = rs.validate_answer(body)
                c = r[1][cid]
                out = c.get("outcome") or {}
                row = {"round": rid, "card": cid, "answered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       **a, "result": out.get("result"), "r": out.get("r"), "structure": out.get("structure"),
                       "correct": rs.correct(a["verdict"], out.get("r"))}
                have[cid] = row
                return _ok({"answer": row, "card": rs.public_card(c, row)})
            paths = paths_or_none()
            if paths is None:
                return _err(503, "The SSD isn't connected, so the answer can't be saved.")
            row = rs.answer(paths, rid, cid, body, settings)
            return _ok({"answer": row, "card": rs.public_card(rs.load_card(paths, rid, cid, settings), row)})
        except NotDevelopmentMonth as e:
            return _err(403, f"Refused: {e}")
        except rs.ReviewRefused as e:
            return _err(409 if "answered already" in str(e) else 400, str(e))

    return [Route("/api/review", index, methods=["GET"]),
            Route("/api/review/card", card, methods=["GET"]),
            Route("/api/review/answer", answer, methods=["POST"])]
