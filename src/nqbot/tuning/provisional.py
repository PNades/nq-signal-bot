"""The provisional structure model of the step-13 tuning (SPEC §7.1 Tuning detector parameters, §12 step 13; owner
2026-11-09, SPEC §13 Q-step13-0 (c), and 2026-11-14, Q-step13c).

LightGBM at one `lgbm_grid` point (`tune_13.provisional_model` with `lgbm_grid.fixed`, no search), binary label held = 1,
broke and timeout = 0 (owner 2026-11-14), trained on the tradeable, undamaged events with each touch group weighted
as one (1 / its trained events, §5.4). Day-blocked cross-validation with one fold per ledger month of months 1-4
(`tune_13.cv_folds`): each month is predicted by a model trained on the other months, purged with a one-session
embargo on each side of the predicted month (§7 rule 2: the sessions next to the boundary are left out of training).
Early stopping (`lgbm_grid.fixed.early_stopping_rounds`) on an inner block, the last `INNER_FRAC` of the training
sessions, never on the predicted month; the model is then refit on all the training sessions with that round count.
Every event gets an out-of-fold P(held).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

INNER_FRAC = 0.20     # owner 2026-11-14 (Q-step13c): the inner early-stopping block, the last 20% of training sessions
EMBARGO_SESSIONS = 1  # §7 rule 2: one session's gap at each train/test boundary


def lgb_params(settings: dict[str, Any], num_threads: int) -> tuple[dict[str, Any], int, int]:
    """(LightGBM parameters, max rounds, early-stopping rounds) from `tune_13.provisional_model` + `lgbm_grid.fixed`."""
    fx = dict(settings["lgbm_grid"]["fixed"])
    pm = dict(settings["tune_13"]["provisional_model"])
    params = {"objective": "binary", "verbose": -1, "num_threads": int(num_threads), "force_col_wise": True,
              "num_leaves": int(pm["num_leaves"]), "min_data_in_leaf": int(pm["min_data_in_leaf"]),
              "learning_rate": float(fx["learning_rate"]), "feature_fraction": float(fx["feature_fraction"]),
              "bagging_fraction": float(fx["bagging_fraction"]), "bagging_freq": int(fx["bagging_freq"]),
              "lambda_l2": float(fx["lambda_l2"]), "max_bin": int(fx["max_bin"]),
              "deterministic": bool(fx["deterministic"]), "seed": int(fx["seed"])}
    return params, int(fx["num_boost_round_max"]), int(fx["early_stopping_rounds"])


def train_mask(session_pos: np.ndarray, test_lo: int, test_hi: int, embargo: int = EMBARGO_SESSIONS) -> np.ndarray:
    """Events whose session lies outside [test_lo - embargo, test_hi + embargo] (session positions in day order)."""
    return (session_pos < test_lo - embargo) | (session_pos > test_hi + embargo)


def cv_predict(X: np.ndarray, y: np.ndarray, w: np.ndarray, session_pos: np.ndarray, fold: np.ndarray,
               trainable: np.ndarray, cat_idx: list[int], settings: dict[str, Any], num_threads: int,
               feature_names: list[str] | None = None) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Out-of-fold P(held) for every row, and one info dict per fold (rows, best rounds)."""
    import lightgbm as lgb

    params, rounds_max, early = lgb_params(settings, num_threads)
    p = np.full(len(y), np.nan)
    info = []
    for k in sorted(set(fold.tolist())):
        test = fold == k
        lo, hi = int(session_pos[test].min()), int(session_pos[test].max())
        tr = trainable & train_mask(session_pos, lo, hi)
        days = np.unique(session_pos[tr])
        n_inner = max(1, math.ceil(INNER_FRAC * len(days)))
        inner = np.isin(session_pos, days[-n_inner:]) & tr
        fit = tr & ~inner

        def ds(m):
            return lgb.Dataset(X[m], label=y[m], weight=w[m], categorical_feature=cat_idx or "auto",
                               feature_name=feature_names or "auto", free_raw_data=False)

        d_fit = ds(fit)
        d_inner = lgb.Dataset(X[inner], label=y[inner], weight=w[inner], reference=d_fit,
                              categorical_feature=cat_idx or "auto", feature_name=feature_names or "auto")
        m1 = lgb.train(params, d_fit, num_boost_round=rounds_max, valid_sets=[d_inner],
                       callbacks=[lgb.early_stopping(early, verbose=False)])
        best = max(1, int(m1.best_iteration or rounds_max))
        m2 = lgb.train(params, ds(tr), num_boost_round=best)
        p[test] = m2.predict(X[test])
        info.append({"fold": int(k), "test_rows": int(test.sum()), "train_rows": int(tr.sum()),
                     "inner_rows": int(inner.sum()), "best_rounds": best})
    return p, info


def weighted_logloss(p: np.ndarray, y: np.ndarray, eps: float = 1e-15) -> np.ndarray:
    q = np.clip(p, eps, 1 - eps)
    return -(y * np.log(q) + (1 - y) * np.log(1 - q))
