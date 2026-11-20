"""`nqbot status`: time left and a finish date (owner 2026-11-19, SPEC §13 Q-status-eta; display only, outside both
keys).

Three things the plain "chunks left × p90 chunk" estimate missed:
- **two builds at once:** the step-13c driver builds a row's challengers two at a time (`program.BATCH`); a row with
  an odd number of challengers (three: r06, r14a, r14b) ends with one build alone, which takes less than a pair but
  far more than half of one. Each remaining chunk of `step13_tune` is priced from the measured chunks of its own kind:
  a pair, a single build, a labels set, a score. A feature build is priced by pass (pass-1 sessions one at a time,
  pass-2 sessions in the pool) from its own chunks measured the same way it runs now: alone, or while the other build
  slot runs ("together");
- **the daily stop:** a job run by `nqbot resume` stops at `--until` (07:30) and starts again at the next resume
  (07:35); a chunk that doesn't fit (the p90 rule) waits for the restart, a long chunk the stop splits (the 13c
  driver's builds, a pass-2 pool) redoes some work after it. The stop time, the gap to the restart and the work
  redone are measured from the job's own runs;
- **a finish date:** the remaining chunks laid out from now on that daily schedule.

Only chunks that ran in one piece are measured (the first chunk of a run is a resumed one if the run before it spent
more than `RESUME_SLACK_MIN` after its last finished chunk). Nothing here reads data files or writes anything.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
RESTART_DEFAULT_MIN = 5.0   # the PC's resume task restarts 5 min after the default --until 07:30 (register_tasks_pc.ps1)
RESUME_SLACK_MIN = 5.0      # a run that spent longer than this after its last finished chunk had one in flight
STOP_AT_UNTIL_MIN = 30.0    # a run counts as stopped by the clock if it ended within this of its --until
BUILD_START_MARGIN_MIN = 10.0   # = step13c.MIN_MINUTES_LEFT: no 13c child build starts closer than this to --until
TUNE_JOB = "step13_tune"
TUNE_BATCH = 2              # = tuning.program.BATCH (tested)
BUILD_SLOTS = ("feature_build", "feature_build_2")
RECENT_P1 = 50              # pass-1 sessions measured: the most recent of the matching kind
RECENT_P2 = 5               # pass-2 chunks measured: the most recent of the matching kind


def _ts(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _median(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None


# --- the daily schedule ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Schedule:
    until: time | None        # the daily stop (ET wall time); None: the job runs on without one
    restart_min: float        # minutes from the stop to the next run's start
    cost_min: float = 0.0     # work redone after a stop splits a chunk
    measured: bool = False    # restart_min from the job's own runs

    def text(self) -> str:
        if self.until is None:
            return "no daily stop"
        out = f"daily stop {self.until:%H:%M}, restart +{_dur(self.restart_min / 60)}"
        if not self.measured:
            out += " (assumed)"
        if self.cost_min:
            out += f", ~{_dur(self.cost_min / 60)} redone per stop"
        return out


def schedule_of(state: dict[str, Any], queued: bool) -> Schedule | None:
    """The stop time of the last run's `--until`; the restart gap = the median over past runs stopped by the clock of
    (the next run's start − that run's --until); without one: 5 min if the job is queued for `nqbot resume`, else the
    median run window's complement (a nightly job)."""
    runs = state.get("runs") or []
    if not runs:
        return None
    last_until = _ts(runs[-1].get("until"))
    if last_until is None:
        return None
    until = last_until.astimezone(ET).time().replace(second=0, microsecond=0)
    gaps = []
    for r, nxt in zip(runs, runs[1:]):
        u, end, start = _ts(r.get("until")), _ts(r.get("end")), _ts(nxt.get("start"))
        if r.get("status") == "until" and u and end and start and end >= u - timedelta(minutes=STOP_AT_UNTIL_MIN):
            gaps.append(max(0.0, (start - u).total_seconds() / 60))
    if gaps:
        return Schedule(until, statistics.median(gaps), measured=True)
    if queued:
        return Schedule(until, RESTART_DEFAULT_MIN)
    windows = [(_ts(r["until"]) - _ts(r["start"])).total_seconds() / 60 for r in runs if r.get("start") and r.get("until")]
    windows = [w for w in windows if 0 < w < 24 * 60]
    return Schedule(until, 24 * 60 - statistics.median(windows)) if windows else None


def next_stop(t: datetime, until: time) -> datetime:
    """The first --until strictly after t (ET wall time, so daylight saving keeps 07:30 at 07:30)."""
    lt = t.astimezone(ET)
    for k in range(3):
        s = datetime.combine(lt.date() + timedelta(days=k), until, tzinfo=ET)
        if s > lt:
            return s
    raise AssertionError("unreachable")


@dataclass
class Seg:
    hours: float
    split: bool = False       # a stop may split it (it resumes after, redoing `Schedule.cost_min`)
    margin_min: float = 0.0   # it won't start closer than this to the stop
    started: bool = False     # in flight now: no start rule


def finish(now: datetime, segs: list[Seg], sch: Schedule | None) -> tuple[datetime, int]:
    """(finish time, stops crossed): the segments one after another from now on the daily schedule."""
    t = now
    stops = 0
    if sch is None or sch.until is None:
        return t + timedelta(hours=sum(s.hours for s in segs)), 0
    restart = timedelta(minutes=sch.restart_min)
    window_h = 24 - sch.restart_min / 60
    for s in segs:
        left = s.hours
        first = not s.started
        while left > 1e-9:
            stop = next_stop(t, sch.until)
            avail = (stop - t).total_seconds() / 3600
            fits = left <= avail
            if first and (avail * 60 < s.margin_min or (not s.split and not fits and left <= window_h)):
                t, stops = stop + restart, stops + 1       # waits for the restart
                continue
            first = False
            if fits:
                t += timedelta(hours=left)
                left = 0.0
            else:
                left = left - avail + sch.cost_min / 60
                t, stops = stop + restart, stops + 1
    return t, stops


# --- measured chunks ------------------------------------------------------------------------------------------------

def clean_chunks(state: dict[str, Any]) -> tuple[set[str], dict[str, float]]:
    """(chunks that ran in one piece, {resumed chunk: the work the run before it spent on it, in seconds})."""
    done = state.get("done") or {}
    runs = state.get("runs") or []
    resumed: dict[str, float] = {}
    prev_slack = 0.0
    for r in runs:
        ch = [c for c in r.get("chunks") or [] if c in done]
        start, end = _ts(r.get("start")), _ts(r.get("end"))
        if not ch:   # a run that finished nothing spent its whole time on the chunk the next run finishes
            prev_slack += (end - start).total_seconds() if end and start else 0.0
            continue
        if prev_slack > RESUME_SLACK_MIN * 60:
            resumed[ch[0]] = prev_slack
        last = _ts(done[ch[-1]]["finished"])
        prev_slack = (end - last).total_seconds() if end and last else 0.0
    return {c for c in done if c not in resumed}, resumed


def _intervals(state: dict[str, Any]) -> list[tuple[datetime, datetime]]:
    out = []
    for r in state.get("runs") or []:
        a, b = _ts(r.get("start")), _ts(r.get("end"))
        if a and b:
            out.append((a, b))
    return out


def _together(chunk: dict[str, Any], other: list[tuple[datetime, datetime]]) -> bool:
    b = _ts(chunk.get("finished"))
    if b is None:
        return False
    a = b - timedelta(seconds=float(chunk.get("sec") or 0))
    return any(x < b and a < y for x, y in other)


def running(state: dict[str, Any]) -> bool:
    """The job's last run has not ended (the runner writes its status at the end)."""
    runs = state.get("runs") or []
    return bool(runs) and runs[-1].get("status") is None


def _in_flight_elapsed_h(state: dict[str, Any], now: datetime) -> float:
    r = (state.get("runs") or [{}])[-1]
    done = state.get("done") or {}
    fins = [_ts(done[c]["finished"]) for c in r.get("chunks") or [] if c in done]
    since = max([x for x in fins if x] or [_ts(r.get("start")) or now])
    return max(0.0, (now - since).total_seconds() / 3600)


def _dur(h: float) -> str:
    return f"{h * 60:.0f} min" if h < 1 else f"{h:.1f} h"


def _when(t: datetime) -> str:
    return (t + timedelta(seconds=30)).astimezone(ET).strftime("%a %Y-%m-%d %H:%M ET")   # to the nearest minute


# --- step 13c --------------------------------------------------------------------------------------------------------

def tune_kinds(plan: dict[str, Any], winners: dict[str, Any]) -> dict[str, tuple[str, int]]:
    """{chunk id: (kind, n)}: b (n builds at once), labels (n label sets to make), score, pre, end."""
    out: dict[str, tuple[str, int]] = {}
    top_n = (winners.get("r09") or {}).get("value") is not None
    for r in plan.get("program") or []:
        ch = [v for v in r["values"] if v != r["start"]]
        batches = [ch[i:i + TUNE_BATCH] for i in range(0, len(ch), TUNE_BATCH)]
        if r["kind"] in ("build", "logloss"):
            for i, b in enumerate(batches):
                out[f"{r['id']}:b{i + 1}"] = ("b", len(b))
            n = len(ch) if r["kind"] == "build" or top_n else 0
        elif r["kind"] == "exec_ttl":
            n = len(r["values"]) - 1               # the start ttl's labels are the row's base labels
        else:
            n = 2 * len(r["values"]) - 1           # buffers x {fixed, vol}; fixed at the start buffer = the base
        out[f"{r['id']}:labels"] = ("labels", n)
        out[f"{r['id']}:score"] = ("score", 1)
    for c in plan.get("chunks") or []:
        out.setdefault(c, ("end" if c.startswith("end") else "pre", 1))
    return out


def tune_line(state: dict[str, Any], plan: dict[str, Any], winners: dict[str, Any], now: datetime,
              queued: bool) -> str | None:
    """The 13c driver's time left, by chunk kind, and its finish date; None if nothing can be measured yet."""
    kinds = tune_kinds(plan, winners)
    done = state.get("done") or {}
    chunks = state.get("chunks") or plan.get("chunks") or []
    clean, resumed = clean_chunks(state)
    meas: dict[str, list[float]] = {"pair": [], "single": [], "labels": [], "score": []}
    for c in chunks:
        if c not in done or c not in clean or c not in kinds:
            continue
        kind, n = kinds[c]
        h = float(done[c]["sec"]) / 3600
        if kind == "b":
            meas["pair" if n >= 2 else "single"].append(h)
        elif kind == "labels" and n > 0:
            meas["labels"].append(h / n)
        elif kind in ("score", "end"):
            meas["score"].append(h)
    pair, single = _median(meas["pair"]), _median(meas["single"])
    if pair is None and single is None:
        return None
    single_note = ""
    if single is None:
        single, single_note = pair, " (not measured yet: as a pair)"
    pair = pair if pair is not None else single
    per_set, score = _median(meas["labels"]) or 0.0, _median(meas["score"]) or 0.0

    def expected(c: str) -> float:
        kind, n = kinds.get(c, ("pre", 1))
        if kind == "b":
            return pair if n >= 2 else single
        if kind == "labels":
            return per_set * n
        return score

    # work redone after a stop: a resumed build chunk's two pieces against a clean one of its size
    costs = [(resumed[c] / 3600 + float(done[c]["sec"]) / 3600 - expected(c)) * 60 for c in resumed
             if c in done and kinds.get(c, ("", 0))[0] == "b"]
    sch = schedule_of(state, queued)
    if sch is not None and costs:
        sch = Schedule(sch.until, sch.restart_min, max(0.0, statistics.median(costs)), sch.measured)
    pending = [c for c in chunks if c not in done]
    is_running = running(state)
    segs: list[Seg] = []
    n_pair = n_single = n_sets = n_score = 0
    for i, c in enumerate(pending):
        kind, n = kinds.get(c, ("pre", 1))
        h = expected(c)
        if i == 0 and is_running:
            h = max(0.0, h - _in_flight_elapsed_h(state, now))
        if kind == "b":
            n_pair, n_single = n_pair + (n >= 2), n_single + (n < 2)
        elif kind == "labels":
            n_sets += n
        else:
            n_score += 1
        segs.append(Seg(h, split=True, margin_min=BUILD_START_MARGIN_MIN if kind == "b" else 0.0,
                        started=i == 0 and is_running))
    work = sum(s.hours for s in segs)
    start = now if is_running else _next_start(state, sch, now)
    end, stops = finish(start, segs, sch)
    parts = []
    if n_pair:
        parts.append(f"{n_pair} chunk(s) of two builds at once (~{_dur(pair)} each)")
    if n_single:
        parts.append(f"{n_single} single build(s) alone (~{_dur(single)} each{single_note})")
    if n_sets:
        parts.append(f"labels for {n_sets} set(s) (~{_dur(per_set)} each)")
    if n_score:
        parts.append(f"{n_score} score/summary chunk(s)")
    line = f"; left: {', '.join(parts) or 'nothing'}; ~{_dur(work)} of work"
    if sch is not None and sch.until is not None:
        line += f", {stops} stop(s) ({sch.text()})"
    return line + f"; finishes ~{_when(end)}" + ("" if queued or is_running else " if run again")


def _next_start(state: dict[str, Any], sch: Schedule | None, now: datetime) -> datetime:
    runs = state.get("runs") or []
    if not runs or sch is None or runs[-1].get("status") != "until":
        return now
    u = _ts(runs[-1].get("until"))
    return max(now, u + timedelta(minutes=sch.restart_min)) if u else now


# --- a feature build --------------------------------------------------------------------------------------------------

def build_line(state: dict[str, Any], other: dict[str, Any] | None, pool: dict[str, Any], now: datetime,
               queued: bool) -> str | None:
    """A feature build's time left by pass and its finish date: pass-1 sessions × the median measured pass-1 session,
    pass-2 sessions × the measured pass-2 wall time per session; both from chunks that ran the way this one runs now
    (alone, or while the other build slot ran), the matching kind if any was measured, else the other."""
    done = state.get("done") or {}
    chunks = state.get("chunks") or []
    if not chunks:
        return None
    other_iv = _intervals(other or {})
    together_now = running(state) and other is not None and running(other)
    clean, _ = clean_chunks(state)
    by_fin = sorted((c for c in done if c in clean), key=lambda c: done[c].get("finished") or "")

    def pick(prefix: str, recent: int, value) -> tuple[float | None, str]:
        same = [c for c in by_fin if c.startswith(prefix) and _together(done[c], other_iv) == together_now]
        rest = [c for c in by_fin if c.startswith(prefix)]
        use, how = (same, "two at once" if together_now else "alone") if same else (rest, "mixed")
        vals = [v for v in (value(c) for c in use[-recent:]) if v is not None]
        return _median(vals), how

    p1_h, p1_how = pick("p1:", RECENT_P1, lambda c: float(done[c]["sec"]) / 3600)
    p1_ids = {c for c in done if c.startswith("p1:")}

    def per_session(c: str) -> float | None:
        _, rng, key, root = c.split(":", 3)
        a, b = rng.split("~")
        n = sum(1 for x in p1_ids if x.split(":")[2] == key and x.split(":")[3] == root
                and a <= x.split(":")[1] <= b)
        return float(done[c]["sec"]) / 3600 / n if n else None

    p2_h, p2_how = pick("p2:", RECENT_P2, per_session)
    left_p1 = [c for c in chunks if c.startswith(("p0:", "p1:")) and c not in done]
    left_p2 = [c for c in chunks if c.startswith("p2:") and c not in done]
    n_p1 = sum(1 for c in chunks if c.startswith("p1:"))
    if pool.get("pass") == 2 and pool.get("pass_total") and left_p2:
        p2_sessions = int(pool["pass_total"]) - int(pool.get("pass_done") or 0)
    else:
        p2_sessions = n_p1 if left_p2 else 0
    if (left_p1 and p1_h is None) or (p2_sessions and p2_h is None):
        return None
    secs = pool.get("session_sec") or []
    sch = schedule_of(state, queued)
    if sch is not None and secs:
        sch = Schedule(sch.until, sch.restart_min, float(statistics.median(secs)) / 60, sch.measured)
    is_running = running(state)
    segs = [Seg(p1_h, margin_min=0.0) for _ in left_p1]
    if p2_sessions:
        segs.append(Seg(p2_h * p2_sessions, split=True, started=is_running and not left_p1))
    if segs and is_running and left_p1:
        segs[0] = Seg(max(0.0, segs[0].hours - _in_flight_elapsed_h(state, now)), started=True)
    work = sum(s.hours for s in segs)
    end, stops = finish(now if is_running else _next_start(state, sch, now), segs, sch)
    parts = []
    if left_p1:
        parts.append(f"pass 1: {len(left_p1)} session(s) × ~{_dur(p1_h)} ({p1_how})")
    if p2_sessions:
        parts.append(f"pass 2: {p2_sessions} session(s) × ~{_dur(p2_h)} wall ({p2_how})")
    line = f"; left: {', '.join(parts) or 'nothing'}; ~{_dur(work)} of work"
    if sch is not None and sch.until is not None:
        line += f", {stops} stop(s) ({sch.text()})"
    return line + f"; finishes ~{_when(end)}" + ("" if queued or is_running else " if run again")


def plain_finish(state: dict[str, Any], chunk_h: float, n_left: int, now: datetime, queued: bool) -> str | None:
    """The generic job's finish date: its chunks (each the p90 chunk) on its daily schedule, none split."""
    sch = schedule_of(state, queued)
    if sch is None or n_left <= 0:
        return None
    segs = [Seg(chunk_h) for _ in range(n_left)]
    end, _ = finish(now if running(state) else _next_start(state, sch, now), segs, sch)
    return f"finishes ~{_when(end)}"
