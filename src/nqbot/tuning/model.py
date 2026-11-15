"""Out-of-fold P(held) of a build's events from the provisional structure model (`provisional.cv_predict`), cached per
build key and model settings (owner 2026-11-14, SPEC §13 Q-step13c)."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from nqbot.storage import atomic_write_json
from nqbot.tuning import provisional as prov
from nqbot.tuning.compare import model_ok
from nqbot.tuning.data import Build

MODEL_VERSION = 1   # bump when the provisional model's inputs or procedure change (a new cache)


def settings_digest(settings: dict[str, Any]) -> str:
    blob = json.dumps({"tune_13": settings["tune_13"], "lgbm": settings["lgbm_grid"]["fixed"],
                       "inner": prov.INNER_FRAC, "embargo": prov.EMBARGO_SESSIONS, "v": MODEL_VERSION},
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:10]


def group_weight(build: Build) -> dict[str, float]:
    """1 / the touch group's model events (§5.4: a touch group counts once), for the model events."""
    n = Counter(e["touch_group_id"] for e in build.events if model_ok(e))
    return {e["event_id"]: 1.0 / n[e["touch_group_id"]] for e in build.events if model_ok(e)}


def phold(build: Build, settings: dict[str, Any], num_threads: int, cache_dir: Path | None = None
          ) -> tuple[dict[str, float], dict[str, Any]]:
    path = None if cache_dir is None else cache_dir / f"phold_{build.key}_{settings_digest(settings)}.json"
    if path is not None and path.exists():
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc["p"], doc["info"]
    pos = {d: i for i, d in enumerate(build.days)}
    w_of = group_weight(build)
    session_pos = np.array([pos[e["day"]] for e in build.events])
    fold = np.array([e["month"] for e in build.events])
    y = np.array([1.0 if e["held"] else 0.0 for e in build.events])
    w = np.array([w_of.get(e["event_id"], 0.0) for e in build.events])
    trainable = np.array([model_ok(e) for e in build.events])
    p, folds = prov.cv_predict(build.X, y, w, session_pos, fold, trainable, build.cat_idx, settings, num_threads,
                               feature_names=[f"f{i}" for i in range(build.X.shape[1])])
    out = {e["event_id"]: float(p[i]) for i, e in enumerate(build.events)}
    info = {"folds": folds, "inputs": len(build.feature_names), "categorical": len(build.cat_idx),
            "model_events": int(trainable.sum()), "events": len(build.events)}
    if path is not None:
        atomic_write_json(path, {"p": out, "info": info})
    return out, info
