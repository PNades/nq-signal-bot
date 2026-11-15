"""Day-block bootstrap intervals for the step-13 tuning (SPEC §7.1 Tuning detector parameters; `tune_13`, owner
2026-11-09, SPEC §13 Q-step13-0 (c)): whole sessions are resampled with replacement, the same draw for both values of a
comparison (a per-day difference is resampled), `tune_13.bootstrap_replicates` replicates, a percentile 95% interval.
"""

from __future__ import annotations

import numpy as np

CI_LEVEL = 0.95     # §7.1: the 95% day-block bootstrap interval
SEED = 13           # fixed, so a report re-run gives the same interval


def draws(n_days: int, reps: int, seed: int = SEED) -> np.ndarray:
    """(reps, n_days) day indices, drawn with replacement."""
    return np.random.default_rng(seed).integers(0, n_days, size=(reps, n_days))


def per_month_ci(diff_by_day: np.ndarray, n_months: float, reps: int, seed: int = SEED) -> tuple[float, float, float]:
    """(point, lo, hi) of the total per month of a per-day series (e.g. net R of value v minus the start value)."""
    x = np.asarray(diff_by_day, dtype=np.float64)
    point = float(x.sum() / n_months)
    if len(x) == 0:
        return point, point, point
    boot = x[draws(len(x), reps, seed)].sum(axis=1) / n_months
    a = (1 - CI_LEVEL) / 2
    return point, float(np.quantile(boot, a)), float(np.quantile(boot, 1 - a))


def excess_boot(rc: np.ndarray, nc: np.ndarray, rs: np.ndarray, ns: np.ndarray, n_months: float,
                idx: np.ndarray | None) -> np.ndarray | float:
    """Excess net R per month of a value over the start value (owner 2026-11-14, SPEC §13 Q-step13c (5)):
    (R_value - R_start) - rbar_start x (N_value - N_start), rbar_start = the start run's mean net R per trade; R and N
    per day. With `idx` (replicates x days), one value per replicate, rbar recomputed inside each replicate."""
    if idx is None:
        RS, NS, RC, NC = rs.sum(), ns.sum(), rc.sum(), nc.sum()
    else:
        RS, NS, RC, NC = rs[idx].sum(1), ns[idx].sum(1), rc[idx].sum(1), nc[idx].sum(1)
    with np.errstate(divide="ignore", invalid="ignore"):
        rbar = np.where(NS > 0, RS / np.where(NS > 0, NS, 1), 0.0)
    return (RC - RS - rbar * (NC - NS)) / n_months


def excess_ci(rc, nc, rs, ns, n_months: float, reps: int, seed: int = SEED) -> tuple[float, float, float]:
    """(point, lo, hi) of the excess net R per month (whole days resampled, the same draw for both runs)."""
    rc, nc, rs, ns = (np.asarray(v, dtype=np.float64) for v in (rc, nc, rs, ns))
    point = float(excess_boot(rc, nc, rs, ns, n_months, None))
    if len(rc) == 0:
        return point, point, point
    boot = excess_boot(rc, nc, rs, ns, n_months, draws(len(rc), reps, seed))
    a = (1 - CI_LEVEL) / 2
    return point, float(np.quantile(boot, a)), float(np.quantile(boot, 1 - a))


def excess_lead_ci(win: tuple, runner: tuple | None, n_months: float, reps: int, seed: int = SEED,
                   sign: float = 1.0) -> tuple[float, float, float]:
    """(point, lo, hi) of sign x (excess of `win` - excess of `runner`), each a (rc, nc, rs, ns) day series against
    its own start run; `runner` None: the start value itself (excess 0). The same day draw for both."""
    w = [np.asarray(v, dtype=np.float64) for v in win]
    r = None if runner is None else [np.asarray(v, dtype=np.float64) for v in runner]
    idx = draws(len(w[0]), reps, seed)

    def ex(x, i):
        return 0.0 if x is None else excess_boot(*x, n_months, i)

    point = float(sign * (ex(w, None) - ex(r, None)))
    boot = sign * (ex(w, idx) - (ex(r, idx) if r is not None else 0.0))
    a = (1 - CI_LEVEL) / 2
    return point, float(np.quantile(boot, a)), float(np.quantile(boot, 1 - a))


def ratio_diff_ci(num_a: np.ndarray, den_a: np.ndarray, num_b: np.ndarray, den_b: np.ndarray, reps: int,
                  seed: int = SEED) -> tuple[float, float, float]:
    """(point, lo, hi) of sum(num_a)/sum(den_a) - sum(num_b)/sum(den_b) with whole days resampled (the weighted log
    loss of two values on the same events)."""
    na, da, nb, db = (np.asarray(v, dtype=np.float64) for v in (num_a, den_a, num_b, den_b))
    point = float(na.sum() / da.sum() - nb.sum() / db.sum())
    idx = draws(len(na), reps, seed)
    boot = na[idx].sum(1) / da[idx].sum(1) - nb[idx].sum(1) / db[idx].sum(1)
    a = (1 - CI_LEVEL) / 2
    return point, float(np.quantile(boot, a)), float(np.quantile(boot, 1 - a))
