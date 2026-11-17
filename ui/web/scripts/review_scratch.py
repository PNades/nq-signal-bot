"""A scratch Review round for the stage-4 shoot checks that need an answered card (owner 2026-11-16, round 9).

    uv run python ui/web/scripts/review_scratch.py serve --round preview-b958 --port 8735
    uv run python ui/web/scripts/review_scratch.py cleanup <scratch dir>

`serve` copies the round's `round.json` and cards (never its answers) into a fresh folder under the SSD's tmp
folder (`<tmp_dir>/review_check_<time>/data/reviews/rounds/scratch-<round>/`, a real-round layout, so it is never
under any `reviews/preview/`), prints that folder, and serves the panel on `--port` with its data root there: a
throwaway answer given on that panel lands in the scratch folder only. `cleanup` removes such a folder (refusing any
path that isn't a `review_check_*` folder directly under the tmp folder). The caller stops the panel by its PID first.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from nqbot.config import load_features, load_settings
from nqbot.review import store as rs
from nqbot.storage import resolve_paths

PREFIX = "review_check_"


def _guard(real, scratch: Path) -> None:
    scratch = scratch.resolve()
    if scratch.parent != Path(real.tmp_dir).resolve() or not scratch.name.startswith(PREFIX):
        raise SystemExit(f"refused: {scratch} is not a {PREFIX}* folder under {real.tmp_dir}")


def serve(round_id: str, port: int) -> None:
    from nqbot.ui.app import PanelConfig
    from nqbot.ui.serve import run

    settings = load_settings()
    real = resolve_paths(settings)
    src = rs.round_dir(real, round_id)
    scratch = Path(real.tmp_dir) / f"{PREFIX}{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    _guard(real, scratch)
    sid = f"scratch-{round_id}"[:64]
    dst = scratch / "data" / "reviews" / "rounds" / sid
    shutil.copytree(src / "cards", dst / "cards")
    meta = json.loads((src / "round.json").read_text(encoding="utf-8"))
    meta.update(id=sid, preview=False, scratch_of=round_id)
    (dst / "round.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    paths = dataclasses.replace(real, data_dir=scratch / "data", log_dir=scratch / "logs")
    assert "preview" not in rs.answers_path(paths, sid).relative_to(scratch).parts
    print(f"scratch: {scratch}", flush=True)
    run(PanelConfig(port=port, paths_fn=lambda: paths, synthetic=False, settings=settings, features=load_features()),
        open_browser=False)


def cleanup(folder: str) -> None:
    real = resolve_paths(load_settings())
    p = Path(folder)
    _guard(real, p)
    if p.exists():
        shutil.rmtree(p)
    print(f"removed: {p}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--round", required=True)
    s.add_argument("--port", type=int, required=True)
    c = sub.add_parser("cleanup")
    c.add_argument("folder")
    a = ap.parse_args()
    serve(a.round, a.port) if a.cmd == "serve" else cleanup(a.folder)
    sys.exit(0)
