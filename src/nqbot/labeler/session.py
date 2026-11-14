"""One built session through the labeler (step 13b; SPEC §13 Q-step13b (1)): which inputs it reads and how they are
checked, inside the labels key with the rest of `labeler/` (owner 2026-11-13, SPEC §13 Q-step13-review: was in
`checks/step13b.py`, outside it).

Inputs: the build's `decisions/<D>.parquet` and its metadata (the session clock, the NQ contract), the stored NQ
trades of that contract (refused if they are another contract's) and its `mbp-1` file (None if absent: `no_book`).
"""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from nqbot.events.common import read_meta


class WrongInputs(Exception):
    """The stored inputs don't belong to the build (the caller refuses the session)."""


def read_decisions(path: Path) -> list[dict]:
    return pq.read_table(path).to_pylist() if path.exists() else []


def label_built(db, data_plan, base: Path, day: date, p, identity: bool = True) -> dict[str, Any]:
    """Label one built session from `base` (decisions/ + its metadata); returns the outputs and their counts. With
    `identity`, the session is labelled again without the low-RR extension and the >= `pair_build_min_rr` pairs must
    be identical (Q-flexfarm (1))."""
    from nqbot.clients import mbp1
    from nqbot.features import session_tape as st
    from nqbot.labeler import labeler as lb

    iso = day.isoformat()
    dpath = base / "decisions" / f"{iso}.parquet"
    meta = read_meta(dpath)
    decisions = read_decisions(dpath)
    t0 = time.monotonic()
    tape = st.load_tape(db, data_plan, "NQ", day)
    if tape is None or tape.symbol != meta.get("nq_symbol"):
        raise WrongInputs(f"{day}: the stored NQ trades ({None if tape is None else tape.symbol}) are not the build's "
                          f"({meta.get('nq_symbol')})")
    book = mbp1.load(db.raw_path("mbp-1", tape.symbol, day))
    t_load = time.monotonic() - t0
    out = lb.label_session(decisions, tape, book, meta, p)
    if identity:
        t1 = time.monotonic()
        off = lb.label_session(decisions, tape, book, meta, replace(p, extension=False))
        out["info"].update(lb.identity_check(out["pairs"], off["pairs"]),
                           identity_sec=round(time.monotonic() - t1, 1))
    out["info"].update(load_sec=round(t_load, 1), book=book is not None, decisions=len(decisions))
    return out
