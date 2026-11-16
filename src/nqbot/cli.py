"""The `nqbot` command (run as `uv run nqbot ...`). Later steps add their commands here."""

from __future__ import annotations

import argparse
import re
import sys
from contextlib import ExitStack
from datetime import date, datetime, time
from time import monotonic

from nqbot.config import ConfigError, load_settings
from nqbot.jobs.lock import (ALL_LOCKS, DATABENTO, SKYLIT, LockHeld, force_unlock, holder_alive, job_locks, lock_path,
                             read_lock)
from nqbot.jobs.registry import JOBS, get_job
from nqbot.jobs.runner import DEFAULT_UNTIL, ET, parse_until, run_job
from nqbot.jobs.status import status_lines
from nqbot.log import setup_logging
from nqbot.storage import SSDNotFound, resolve_paths

EXIT_REFUSED = 2
EXIT_LOCKED = 3
EXIT_WARN = 5  # preflight found something to fix before the session (owner 2026-10-14)
EXIT_INCOMPLETE = 4  # stream-session ended before the close (owner 2026-10-10: the task's exit code is the recorder's)


def _until(text: str) -> str:
    try:
        parse_until(text, datetime.now(ET))
    except ValueError:
        raise argparse.ArgumentTypeError(f"--until wants HH:MM (24-hour ET), got {text!r}") from None
    return text


def main(argv: list[str] | None = None) -> int:
    from nqbot.console import resilient_console
    from nqbot.power import keep_awake

    # owner 2026-10-16: a task's console log lives on the SSD; a pulled SSD must not crash the run on its next print
    with resilient_console(), keep_awake():  # owner 2026-10-11: every command, so Modern Standby can't drop the SSD
        return _main(argv)


def _resolve_paths(settings: dict):
    """resolve_paths; under a scheduled task (nqbot_task.ps1 sets NQBOT_TASK_LOG) a missing SSD is waited for up to
    SSD_DROP_WAIT_SEC first (owner 2026-10-16: on 10-15 the 08:40 task started during a 4-s drop and refused)."""
    import os
    from pathlib import Path

    from nqbot import storage
    from nqbot.console import TASK_LOG_ENV

    try:
        return resolve_paths(settings)
    except SSDNotFound as e:
        if not os.environ.get(TASK_LOG_ENV):
            raise
        first = e
    where = Path(str((settings.get("paths") or {}).get("root") or "."))
    storage.log_ssd_event("task start", where, f"SSD not found ({first}); waiting up to {storage.SSD_DROP_WAIT_SEC:.0f} s")
    waited = 0.0
    while waited < storage.SSD_DROP_WAIT_SEC:
        storage._sleep(storage.SSD_DROP_POLL_SEC)
        waited += storage.SSD_DROP_POLL_SEC
        try:
            paths = resolve_paths(settings)
        except SSDNotFound:
            continue
        storage.log_ssd_event("task start", paths.root, f"SSD found after {waited:.1f} s")
        return paths
    storage.log_ssd_event("task start", where, f"SSD still not found after {storage.SSD_DROP_WAIT_SEC:.0f} s")
    raise first


def _add_ui(sub) -> None:
    ui = sub.add_parser("ui", help="CreamFlow, the Control Panel (SPEC §9b): serve it on 127.0.0.1:<ui_port>; "
                                   "`ui build` builds its app (npm ci, npm audit, vite build)")
    ui.add_argument("--synthetic", action="store_true",
                    help="show the synthetic ledger and review cards (labelled SYNTHETIC DATA) instead of real ones")
    ui.add_argument("--no-open", action="store_true", help="don't open the browser")
    ui_sub = ui.add_subparsers(dest="action")
    ui_sub.add_parser("build", help="npm ci from the committed lockfile, npm audit (fails on high or critical), "
                                    "vite build into the git-ignored ui/web/dist/")
    ui_sub.add_parser("backup", help="copy the panel's files (<data_dir>/panel/: accounts, presets, UI settings) to "
                                     "Google Drive <drive_folder>/panel/ with rclone, then check the copy")


def _run_ui(args, settings: dict) -> int:
    from nqbot.storage import SSDNotFound

    if args.action == "build":
        from nqbot.ui.build import build

        return build()
    if args.action == "backup":
        from nqbot.ui.panel_backup import backup

        try:
            paths = resolve_paths(settings)
        except SSDNotFound as e:
            print(f"refused: {e}", file=sys.stderr)
            return EXIT_REFUSED
        return backup(paths, settings)
    from nqbot.ui.app import PanelConfig
    from nqbot.ui.serve import BindRefused, run

    def paths_fn():
        try:
            return resolve_paths(settings)
        except SSDNotFound:
            return None

    from nqbot.config import load_features

    cfg = PanelConfig(port=int(settings["ui_port"]), paths_fn=paths_fn, synthetic=args.synthetic,
                      settings=settings, features=load_features())
    try:
        return run(cfg, open_browser=not args.no_open)
    except BindRefused as e:
        print(f"refused: {e}", file=sys.stderr)
        return EXIT_REFUSED

def _main(argv: list[str] | None = None) -> int:
    # The Windows console is cp1252: never crash on a character it can't show.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(prog="nqbot")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run a long job in resumable chunks (SPEC section 11, Overnight jobs)")
    run.add_argument("job", choices=sorted(JOBS))
    run.add_argument("--until", default=DEFAULT_UNTIL, type=_until,
                     help="stop before this ET time (HH:MM, default 07:30)")
    backup = sub.add_parser("backup", help="copy data/raw/skylit/ to Google Drive with rclone and verify the "
                                           "checksums (SPEC section 11 Storage; = nqbot run skylit_backup)")
    backup.add_argument("--until", default=DEFAULT_UNTIL, type=_until,
                        help="stop before this ET time (HH:MM, default 07:30); the next run resumes")
    sub.add_parser("status", help="SSD use, job locks and progress, the resume queue and the clock check")
    res = sub.add_parser("resume", help="run the queued jobs (nqbot queue) from where they stopped; the scheduled "
                                        "task on the build PC runs it at startup, 07:35 and hourly (SPEC section 11)")
    res.add_argument("--until", default=DEFAULT_UNTIL, type=_until,
                     help="stop before this ET time (HH:MM, default 07:30)")
    q = sub.add_parser("queue", help="the job queue nqbot resume runs: show, add JOB, remove JOB, release [JOB]")
    q.add_argument("action", choices=["show", "add", "remove", "release"])
    q.add_argument("job", nargs="?", default=None)
    dr = sub.add_parser("drill", help="the resume drill job dummy_drill: setup --chunks N --sleep S, or check")
    dr.add_argument("action", choices=["setup", "check"])
    dr.add_argument("--chunks", type=int, default=300)
    dr.add_argument("--sleep", type=float, default=10.0, help="seconds per chunk")
    sub.add_parser("status-push", help="write nqbot status to <log_dir>/status_<host>.txt and copy it to Google "
                                       "Drive with rclone (hourly task on the build PC)")
    sub.add_parser("keys-check", help="one free authenticated call per service (Skylit, Databento, Anthropic, "
                                      "Discord) to confirm this machine's .env keys work; prints no key")
    v = sub.add_parser("verify", help="read back every file under the root written since a date and list anything "
                                      "damaged (owner 2026-10-11; reads only, report in <reports_dir>/verify/)")
    v.add_argument("--since", type=lambda t: datetime.fromisoformat(t), required=True, help="YYYY-MM-DD (local time)")
    sub.add_parser("preflight", help="pre-session machine check: restart pending, Windows Update paused through the "
                                     "session, SSD writable, AC power, SSD drops (exit 5 on any warning; §9)")
    sub.add_parser("fallback-move", help="move a session recording kept on the internal drive (stream-session "
                                         "--fallback-local) onto the SSD, checked file by file")
    sub.add_parser("test", help="run the test suite (serial, or parallel with test_parallel: true in config/local.yaml: "
                                "pytest-xdist, all cores but 2; --parallel / --serial override); the whole "
                                "output goes to <root>/logs/pytest_<date-time>.txt, ending with 'PYTEST EXIT CODE: N'; "
                                "any further arguments go to pytest; a full run (no pytest arguments) on a clean commit "
                                "that passes is recorded, and long jobs run only a recorded commit (CLAUDE.md rule 14)")
    stop = sub.add_parser("stop", help="ask a running job to stop after the call it is in (its answer saved), "
                                       "unlike Ctrl-C (SPEC section 11)")
    stop.add_argument("job", choices=sorted(JOBS))
    unlock = sub.add_parser("unlock", help="remove a job lock left on the SSD by a job that is no longer running")
    unlock.add_argument("--yes", action="store_true", help="confirm that job is no longer running anywhere")
    which = unlock.add_mutually_exclusive_group()
    which.add_argument("--skylit", action="store_true", help="only the Skylit job lock")
    which.add_argument("--databento", action="store_true", help="only the Databento job lock")
    which.add_argument("--gameplan", action="store_true", help="the gameplan commands' lock instead of the job locks")
    setup = sub.add_parser("setup-ssd", help="step 4b: find and check the SSD, write paths.root, the marker and the "
                                             "month ledger (prints the diff; --yes writes)")
    setup.add_argument("--drive", default=None, help="the SSD's drive letter (needed if several drives are plugged in)")
    setup.add_argument("--root", default=None, help="folder on the SSD for all project data (default nqbot/)")
    setup.add_argument("--rewrite-ledger", action="store_true",
                       help="when step 10 starts: re-check the month-1 merge and rewrite only the ledger")
    setup.add_argument("--yes", action="store_true", help="write it (after the owner saw the diff)")
    _add_step7(sub)
    _add_step8b(sub)
    _add_step11(sub)
    _add_step12(sub)
    _add_features(sub)
    _add_step13(sub)
    bp = sub.add_parser("build-pin", help="the build worktree pin (Q-dryrun13 (1)): set (run inside the worktree, by "
                                          "scripts/build_worktree.ps1), show, clear")
    bp.add_argument("action", choices=("set", "show", "clear", "can-move", "move-begin", "move-end"))
    bp.add_argument("--pid", type=int, help="move-begin: the moving script's own process id")
    _add_skylit(sub)
    _add_databento(sub)
    _add_gameplan(sub)
    _add_ui(sub)
    args, extra = parser.parse_known_args(argv)
    if extra and args.command != "test":  # only `nqbot test` passes unknown arguments on (to pytest)
        parser.error(f"unrecognized arguments: {' '.join(extra)}")

    try:
        settings = load_settings()
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_REFUSED
    if args.command == "preflight":  # before resolve_paths: it must report a missing SSD, not refuse
        from nqbot.preflight import preflight

        return EXIT_WARN if preflight(settings)["warnings"] else 0
    if args.command == "ui":  # before resolve_paths: the panel and its /emergency page work without the SSD
        return _run_ui(args, settings)
    if args.command == "setup-ssd":  # before resolve_paths: paths.root and the marker don't exist yet
        return _run_setup_ssd(args, settings)
    if args.command == "keys-check":  # needs no SSD
        from nqbot.keys_check import run_checks

        results = run_checks(settings)
        for r in results:
            print(f"{'ok  ' if r.ok else 'FAIL' if r.ok is False else '--  '} {r.name}: {r.detail}")
        failed = [r.name for r in results if r.ok is False]
        print("all set keys work" if not failed else f"FAILED: {', '.join(failed)}")
        return 1 if failed else 0
    recording_fallback = (args.command == "step7" and getattr(args, "check", None) == "stream-session"
                          and args.fallback_local)
    try:
        paths = _resolve_paths(settings)
    except SSDNotFound as e:
        if not recording_fallback:
            print(f"SSD not found: {e}", file=sys.stderr)
            return EXIT_REFUSED
        paths, why = None, str(e)
    if recording_fallback:
        paths = _choose_recording_target(args, settings, paths, why if paths is None else None)
        if args.check_only:
            return 0

    if args.command == "status":
        from nqbot.clock import clock_checks
        from nqbot.jobs.queue import queue_lines

        print("\n".join(status_lines(paths) + queue_lines(paths, settings)))
        warn, ok = clock_checks(settings)  # owner 2026-10-28: the clock on whichever machine holds the SSD
        print("\n".join([f"clock WARN {w}" for w in warn] + [f"clock ok   {o}" for o in ok]))
        return 0

    if args.command == "queue":
        return _queue(args, paths, settings)

    if args.command == "drill":
        from nqbot.jobs import drill

        if args.action == "setup":
            moved = drill.setup(paths, args.chunks, args.sleep)
            print(f"dummy_drill set up: {args.chunks} chunks x {args.sleep:g} s"
                  + (f" (the previous drill moved to {moved})" if moved else "")
                  + "; queue it with: uv run nqbot queue add dummy_drill")
            return 0
        r = drill.check(paths)
        print(f"dummy_drill: {r['finished']}/{r['total']} chunks finished, finished twice: "
              f"{r['finished_twice'] or 'none'}, {r['runs']} run(s), {r['processes']} process(es), {r['boots']} "
              f"boot(s): {'CLEAN' if r['clean'] else 'NOT CLEAN'}")
        return 0 if r["clean"] else 1

    if args.command == "unlock":
        return _unlock(args, paths)

    if args.command == "stop":
        return _stop(args, paths)

    log = setup_logging(paths.log_dir)
    if args.command == "verify":
        from nqbot.verify import run_verify

        res, _ = run_verify(paths, args.since)
        return 1 if res.damaged or res.size_mismatch or res.unreadable else 0
    if args.command == "fallback-move":
        from nqbot.fallback import fallback_paths, move_fallback

        res = move_fallback(fallback_paths(settings["paths"]["root"]), paths)
        return 1 if res.conflicts else 0
    if args.command == "test":
        from nqbot import testpass
        from nqbot.testrun import pytest_cmd, run_tests

        full = pytest_cmd(extra)[1]
        state = testpass.before_run() if full else None
        t0 = monotonic()
        code = run_tests(paths.log_dir, extra, default_parallel=settings.get("test_parallel") is True)[0]
        if state is not None:  # owner 2026-10-29: a full-suite pass on a clean commit lets jobs run it (rule 14 (f))
            print(testpass.record(paths, state, code, monotonic() - t0))
        return code
    if args.command == "status-push":
        from nqbot.clock import clock_checks
        from nqbot.status_push import push

        code = push(paths, settings, clock=lambda: clock_checks(settings))
        # the panel's files ride along hourly (owner 2026-11-15); a failure is printed, status-push's own code stands
        from nqbot.ui.panel_backup import backup as panel_backup

        panel_backup(paths, settings)
        return code
    if args.command == "resume":
        from nqbot.jobs.queue import resume
        from nqbot.verify import run_verify

        until = parse_until(args.until, datetime.now(ET))
        if not _passed_full_suite(paths, log, "nqbot resume"):
            return EXIT_REFUSED  # = resume's "waiting for the owner"
        head0 = _head_or_none()
        _print_power_warnings()

        def verify(since: datetime) -> list[str]:
            res, _ = run_verify(paths, since)
            return [r["path"] for r in res.damaged + res.size_mismatch + res.unreadable]

        from nqbot.checks import feature_build_job as fbj

        many = {}
        if fbj.parallel_on():   # owner 2026-11-12: two builds at once (SPEC §13 Q-step13b (f) (2))
            many = dict(together=lambda job: fbj.slot_of(job) is not None,
                        run_many=lambda names: (_resume_may_start(paths, head0),
                                                _run_many_for_resume(names, args.until, log, out_dir=paths.log_dir))[1],
                        running_now=lambda job: fbj.running_holder(paths, job) is not None)
        return resume(paths, settings, run=lambda name: _run_for_resume(name, settings, paths, until, log, head0),
                      verify=verify, **many)
    if args.command == "step7":
        return _run_step7(args, settings, paths, log)
    if args.command == "step8b":
        return _run_step8b(args, settings, paths, log)
    if args.command == "step11":
        return _run_step11(args, settings, paths, log)
    if args.command == "step12":
        return _run_step12(args, settings, paths, log)
    if args.command == "features":
        return _run_features(args, settings, paths, log)
    if args.command == "step13":
        return _run_step13(args, settings, paths, log)
    if args.command == "build-pin":
        from nqbot import build_pin

        try:
            if args.action == "set":
                build_pin.set_pin(paths)
            elif args.action == "clear":
                build_pin.clear_pin(paths)
            elif args.action == "can-move":   # scripts/build_worktree.ps1 asks before it touches the worktree
                held = build_pin._live_locks(paths)
                if held:
                    raise build_pin.PinRefused(f"a job is running ({', '.join(held)}): the pin moves only between jobs")
                print("no job running: the build worktree may move")
            elif args.action == "move-begin":   # owner 2026-11-13 review: jobs refuse from here to move-end
                if not args.pid:
                    raise build_pin.PinRefused("move-begin needs --pid (the script's own process id)")
                build_pin.begin_move(paths, args.pid)
            elif args.action == "move-end":
                build_pin.end_move(paths, print)
            else:
                pin = build_pin.read_pin(paths)
                print("no build pin" if pin is None else f"build pin: {pin['path']} at {pin['commit'][:7]} on "
                                                          f"{pin['host']} ({build_pin.check(paths).message})")
        except build_pin.PinRefused as e:
            print(f"refused: {e}", file=sys.stderr)
            return EXIT_REFUSED
        return 0
    if args.command == "skylit":
        return _run_skylit(args, settings, paths, log)
    if args.command == "databento":
        return _run_databento(args, settings, paths, log)
    if args.command in ("gameplan", "gameplan-batch"):
        return _run_gameplan(args, settings, paths, log)
    from nqbot.backup import BackupRefused
    from nqbot.clients.skylit import OutOfCredits, SkylitError
    from nqbot.clients.skylit_backfill import BackfillRefused
    from nqbot.clients.skylit_credits import CreditRefused
    from nqbot.secrets import MissingSecret

    job = get_job("skylit_backup" if args.command == "backup" else args.job)
    until = parse_until(args.until, datetime.now(ET))
    if not _passed_full_suite(paths, log, job.name):
        return EXIT_REFUSED
    _print_power_warnings()
    try:
        result = run_job(job, settings, paths, until=until, log=log)
    except LockHeld as e:
        log.error("%s", e)
        return EXIT_LOCKED
    except (BackfillRefused, BackupRefused, CreditRefused, MissingSecret) as e:
        print(f"refused: {e}", file=sys.stderr)
        return EXIT_REFUSED
    except OutOfCredits as e:  # a 402 outside a chunk (a chunk turns it into the clean "credits" stop)
        log.error("%s stopped: Skylit is out of credits: %s", job.name, e)
        print(f"stopped: Skylit is out of credits ({e}); top up (with the owner's OK), then run: "
              f"uv run nqbot run {job.name}", file=sys.stderr)
        return EXIT_REFUSED
    except SkylitError as e:  # e.g. an undecodable answer (kept in skylit/failed/) or a 4xx: the chunk re-runs
        log.error("%s stopped on a Skylit error: %s", job.name, e)
        print(f"stopped: Skylit error {e}; the chunk re-runs next time (ask Claude Code if it repeats)",
              file=sys.stderr)
        return EXIT_REFUSED
    message = f" - {result.message}" if result.message else ""
    print(f"{job.name}: {result.status} ({result.done}/{result.total} chunks done){message}")
    if result.status == "credits":
        print(f"stopped cleanly on credits: top up (or re-plan with the owner's OK), then run: uv run nqbot run {job.name}")
    if result.status == "failures":
        print(f"stopped cleanly: too many windows or dates failed in a row. Check Skylit's status, then run: uv run "
              f"nqbot run {job.name} (failures already recorded are skipped; ask Claude Code if it repeats)")
    if result.status == "stopped":
        print(f"stopped on request (nqbot stop); resume with: uv run nqbot run {job.name}")
    if job.name == "skylit_backup":
        print(f"backup report: {paths.reports_dir / 'backup' / 'backup.md'}")
    if result.status == "budget_stall":
        print(f"stopped cleanly: the two-builds budget mutex stayed locked (resumed by itself: nqbot resume, or run: "
              f"uv run nqbot run {job.name})")
    return 0 if result.status in ("complete", "until", "stopped", "budget_stall") else EXIT_REFUSED


def _head_or_none() -> str | None:
    from nqbot import testpass

    try:
        return testpass.head()
    except Exception:   # git unreadable: the full-suite check has already warned
        return None


def _resume_may_start(paths, head0: str | None) -> None:
    """Before each job of one `nqbot resume` (owner 2026-11-13 review): no pin move in progress, and the worktree still
    at the commit this resume started on (its code is loaded); else LockHeld, so resume ends with "a job lock is held"
    and the next resume, on the new code, runs the job."""
    from nqbot import build_pin

    mv = build_pin.moving(paths)
    if mv is not None:
        raise LockHeld(build_pin.move_path(paths), mv)
    head = _head_or_none()
    if head0 is not None and head is not None and head != head0:
        raise LockHeld(build_pin.pin_path(paths), {"job": f"the pin moved ({head0[:7]} -> {head[:7]}) since this resume "
                                                          "started; the next resume runs it"})


def _run_for_resume(name: str, settings: dict, paths, until: datetime, log, head0: str | None = None):
    """`nqbot run <name>` for nqbot resume: the RunResult; a refusal before the job started becomes status `refused`
    (resume then waits for the owner); LockHeld goes up (the job is already running, or the pin is moving)."""
    _resume_may_start(paths, head0)
    from nqbot.backup import BackupRefused
    from nqbot.clients.skylit import OutOfCredits, SkylitError
    from nqbot.clients.skylit_backfill import BackfillRefused
    from nqbot.clients.skylit_credits import CreditRefused
    from nqbot.jobs.runner import RunResult
    from nqbot.secrets import MissingSecret

    try:
        return run_job(get_job(name), settings, paths, until=until, log=log)
    except (BackfillRefused, BackupRefused, CreditRefused, MissingSecret, OutOfCredits, SkylitError) as e:
        log.error("%s refused: %s", name, e)
        return RunResult("refused", 0, 0, message=f"{type(e).__name__}: {e}")


RUN_SUMMARY = re.compile(r"^(?P<job>\S+): (?P<status>[a-z_]+) \((?P<done>\d+)/(?P<total>\d+) chunks done\)(?: - (?P<msg>.*))?$")


def _run_many_for_resume(names: list[str], until_text: str, log, popen=None, poll_sec: float = 5.0,
                         out_dir=None) -> dict:
    """`nqbot run <name> --until` for each name at once, each in its own process (owner 2026-11-12: two builds at
    once), waiting for all. Each result: the RunResult from the child's summary line (`<job>: <status> (d/t chunks
    done)`); `LockHeld` if it exited 3 (already running); else status `refused` (exit 2) or `died` (anything else)."""
    import os
    import subprocess
    import tempfile
    import time as _t
    from pathlib import Path

    # each child's output under <root>/logs (rule 13: nothing on the internal drive; owner 2026-11-13 review)
    base = Path(out_dir) if out_dir is not None else Path(tempfile.gettempdir())
    base.mkdir(parents=True, exist_ok=True)

    from nqbot.jobs.lock import LockHeld
    from nqbot.jobs.runner import RunResult

    popen = popen or subprocess.Popen
    procs = {}
    for name in names:
        out = base / f"resume_{name}_{datetime.now(ET):%Y%m%d-%H%M%S}_{os.getpid()}.txt"
        fh = open(out, "w", encoding="utf-8")
        argv = [sys.executable, "-c", "import sys; from nqbot.cli import main; sys.exit(main(sys.argv[1:]))",
                "run", name, "--until", until_text]
        procs[name] = (popen(argv, stdout=fh,
                             stderr=subprocess.STDOUT, env={**os.environ, "PYTHONUTF8": "1"}), fh, out)
        log.info("resume: started %s (pid %s), output %s", name, getattr(procs[name][0], "pid", "?"), out)
    while any(p.poll() is None for p, _, _ in procs.values()):
        _t.sleep(poll_sec)
    results = {}
    for name, (p, fh, out) in procs.items():
        fh.close()
        text = out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""
        for line in text.splitlines():
            log.info("[%s] %s", name, line)
        m = next((m for m in (RUN_SUMMARY.match(x.strip()) for x in reversed(text.splitlines())) if m
                  and m["job"] == name), None)
        if m:
            results[name] = RunResult(m["status"], int(m["done"]), int(m["total"]), message=m["msg"] or "")
        elif p.returncode == EXIT_LOCKED:
            results[name] = LockHeld(Path(name), None)
        else:
            tail = " | ".join(text.strip().splitlines()[-3:])
            results[name] = RunResult("refused" if p.returncode == EXIT_REFUSED else "died", 0, 0,
                                      message=f"exit {p.returncode}: {tail}")
    return results


def _passed_full_suite(paths, log, what: str) -> bool:
    """Owner 2026-10-29 (CLAUDE.md rule 14 (f)): a long job runs only a commit that passed the full suite."""
    from nqbot import testpass

    def echo(msg: str) -> None:
        (log.error if msg.startswith("refused") else log.warning)("%s", msg)
        print(msg, file=sys.stderr)

    return testpass.require(paths, echo, what)


def _queue(args: argparse.Namespace, paths, settings: dict) -> int:
    from nqbot.jobs import queue

    names = queue.read_queue(paths)
    if args.action in ("add", "remove"):
        if args.job not in JOBS:
            print(f"refused: unknown job {args.job!r}; known jobs: {', '.join(sorted(JOBS))}", file=sys.stderr)
            return EXIT_REFUSED
        if args.action == "add" and args.job not in names:
            from nqbot import testpass

            if not testpass.require(paths, lambda m: print(m, file=sys.stderr), f"queue add {args.job}"):
                return EXIT_REFUSED
        if args.action == "add" and args.job not in names:
            names.append(args.job)
        elif args.action == "remove":
            names = [n for n in names if n != args.job]
        queue.write_queue(paths, names)
    elif args.action == "release":
        cleared = queue.release(paths, args.job)
        print(f"released: {', '.join(cleared) if cleared else 'nothing was held'}")
    print("\n".join(queue.queue_lines(paths, settings)))
    return 0


def _stop(args: argparse.Namespace, paths) -> int:
    """Ask a running job to stop between calls (decision 2): it finishes and saves the call it is in."""
    from nqbot.jobs.runner import stop_request_path
    from nqbot.storage import atomic_write_json

    from nqbot.jobs.queue import display_holder

    # owner 2026-11-13 review: a build with parallel_builds holds only shared data locks and its run lock
    if display_holder(paths, args.job) is None:
        print(f"refused: {args.job} isn't running (no job lock names it; see uv run nqbot status)", file=sys.stderr)
        return EXIT_REFUSED
    atomic_write_json(stop_request_path(paths, args.job), {"requested": datetime.now(ET).isoformat()})
    print(f"{args.job} stops after the call or window it is in (its answer is saved): check with uv run nqbot "
          f"status; resume later with uv run nqbot run {args.job}")
    return 0


def _unlock(args: argparse.Namespace, paths) -> int:
    """Show the job locks (or the gameplan lock) and, with --yes, remove them; never one a live process here holds."""
    if args.gameplan:
        from nqbot.gameplan.parser import GameplanStore

        locks, flag = [("gameplan lock", GameplanStore.from_paths(paths).lock_path)], " --gameplan"
    else:
        from nqbot.jobs.lock import holders

        kinds = [SKYLIT] if args.skylit else [DATABENTO] if args.databento else list(ALL_LOCKS)
        # the exclusive lock and any shared holds (two builds at once, owner 2026-11-12)
        locks = [(f"{k} job lock" + (" (shared)" if mode == "shared" else ""), path)
                 for k in kinds for path, _, mode in holders(paths.root, k)]
        locks = locks or [(f"{k} job lock", lock_path(paths.root, k)) for k in kinds]
        if not (args.skylit or args.databento):   # the builds' own run locks
            locks += [(f"{p.parent.name} run lock", p) for p in sorted((paths.data_dir / "jobs").glob("*/run.lock"))]
        flag = " --skylit" if args.skylit else " --databento" if args.databento else ""
    held = [(what, lock, read_lock(lock)) for what, lock in locks if lock.exists()]
    if not held:
        print(f"no {' or '.join(what for what, _ in locks)} on the SSD")
        return 0
    for what, _, holder in held:
        print(f"{what} held by: {holder or 'unreadable lock file'}")
    alive = [what for what, _, holder in held if holder_alive(holder)]
    if alive:
        print(f"refused: {', '.join(alive)} belongs to a job still running on this machine: stop it first "
              "(or wait for it to finish); --skylit or --databento removes only the other one")
        return EXIT_LOCKED
    if not args.yes:
        print(f"Check that job is not running on any machine, then run: uv run nqbot unlock{flag} --yes")
        return EXIT_LOCKED
    for what, lock, _ in held:
        force_unlock(lock)
        print(f"{what} removed")
    if not args.gameplan:
        print("the job resumes from its last finished chunk on the next nqbot run")
    return 0


def _run_setup_ssd(args: argparse.Namespace, settings: dict) -> int:
    from nqbot import setup_ssd
    from nqbot.config import CONFIG_DIR
    from nqbot.months import LedgerMissing

    try:
        setup_ssd.setup(settings, CONFIG_DIR / "settings.yaml", now=datetime.now(ET), drive_arg=args.drive,
                        root=args.root or setup_ssd.DEFAULT_ROOT, yes=args.yes, rewrite_ledger=args.rewrite_ledger)
    except (setup_ssd.SetupRefused, LedgerMissing, SSDNotFound) as e:
        print(f"refused: {e}", file=sys.stderr)
        return EXIT_REFUSED
    return 0


def _print_power_warnings() -> None:
    from nqbot.power import power_warnings

    for line in power_warnings():
        print(line, file=sys.stderr)


def _date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"want YYYY-MM-DD, got {text!r}") from None


def _dates(text: str) -> list[date]:
    return [_date(x.strip()) for x in text.split(",") if x.strip()]


def _hhmm(text: str) -> time:
    try:
        return time.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"want HH:MM (ET), got {text!r}") from None


def _add_step7(sub: argparse._SubParsersAction) -> None:
    """`nqbot step7 <check>`: the step-7 Skylit checks (SPEC §12 step 7). Each prints its credit estimate first."""
    s7 = sub.add_parser("step7", help="step-7 Skylit checks (SSD day)").add_subparsers(dest="check", required=True)

    def add(name: str, help_: str) -> argparse.ArgumentParser:
        p = s7.add_parser(name, help=help_)
        p.add_argument("--approve-credits", type=int, default=None,
                       help="owner-approved credits for a pull over 5,000 (CLAUDE.md rule 2)")
        return p

    add("symbols", "free: symbols, history dates, rate limit, balance, Tempest coverage")
    p = add("strike-window", "full-chain calls -> proposed skylit_max_strikes (6 x 25 credits)")
    p.add_argument("--busy-day", type=_date, required=True)
    p.add_argument("--sample-days", type=_dates, required=True, help="comma-separated, development month 6")
    p.add_argument("--start", type=_hhmm, default=None, help="15-minute window start, ET (default 09:30)")
    p = add("sample-days", "pull the 5 sample days (job skylit_sample_days, about 3,250 credits)")
    p.add_argument("--dates", type=_dates, required=True)
    p.add_argument("--raised", action="store_true",
                   help="re-pull them after skylit_max_strikes was raised (job skylit_sample_days_raise, rule-2 "
                        "exception, kept apart)")
    p.add_argument("--until", default=DEFAULT_UNTIL, type=_until)
    add("oldest-window", "one window (25 credits) on the oldest day still in reach: does its answer convert? "
                         "(after the strike-window check passes)")
    add("historical-instants", "40 /v1/historical instants over development months 1-6 (200 credits)")
    p = add("stream-record", "record 15 minutes of /v1/stream during a live session (about 70 credits)")
    p.add_argument("--force", action="store_true", help="record outside the session (testing only)")
    p = add("stream-session", "record /v1/stream for the given maps until today's close, in 15-minute parts "
                              "(about 395 credits per map for a full session; owner 2026-10-08)")
    p.add_argument("--maps", type=lambda s: [x.strip() for x in s.split(",") if x.strip()], default=["NDXP"],
                   help="comma-separated maps (default NDXP)")
    p.add_argument("--fallback-local", action="store_true",
                   help="if the SSD isn't reachable or writable, or fails during the session, record to the internal "
                        "drive and move it to the SSD after the close (owner 2026-10-11, recording only)")
    p.add_argument("--check-only", action="store_true",
                   help="with --fallback-local: say where the recording would go, test both folders, start no stream")
    p = add("parity", "pull the recorded minutes from history and compare (25 credits)")
    p.add_argument("--session", type=_date, required=True)
    p.add_argument("--tag", default=None, help="which recording of that session (default: the latest)")
    p = add("darkpool", "45 days of QQQ/SPY prints + top-prints cross-check")
    p.add_argument("--date", type=_date, required=True,
                   help="a development-month date in month 3 or later (its 45-day lookback must be in Skylit's reach)")
    p.add_argument("--check-dates", type=_dates, default=[])
    p.add_argument("--no-depth-probe", action="store_true")
    p.add_argument("--walk-forward", action="store_true",
                   help="dates in walk-forward months 7-9 (Skylit's dark-pool history starts in month 7): the report "
                        "keeps match / no-match only, no prices (owner 2026-10-06)")
    p = add("late-prints-morning", "pre-open pull of the previous session's prints + top-3 lists")
    p.add_argument("--force", action="store_true")
    add("late-prints-compare", "re-pull each morning >= 5 trading days later and compare")
    p = add("tempest-poll", "poll the free /v1/vol/symbols until the previous session's row appears")
    p.add_argument("--until", type=_hhmm, default=None, help="stop polling at this ET time (default 09:30)")
    p.add_argument("--today", action="store_true",
                   help="start during the session: wait for today's row (polls every minute), see if it exists or "
                        "changes before the close (owner 2026-10-07)")
    add("tempest-compare", "re-pull the oldest stored Tempest row not yet re-checked and diff it")
    p = add("stream-drops", "one /v1/historical/range window (25 credits) vs a session recording: each map's drop "
                            "rate (owner 2026-10-14, §13 Q-mapstale)")
    p.add_argument("--session", type=_date, required=True)
    p.add_argument("--window", type=_hhmm, default=None, help="15-minute window start, ET (default: the worst one)")
    p = add("tempest-morning", "pull a session's Tempest row at the pre-open of a later day into vol/ (1 credit; "
                               "owner 2026-10-09: the only copy the bot keeps)")
    p.add_argument("--date", type=_date, default=None, help="the session (default: the last one before today)")
    p = add("tempest-finality", "re-pull rows stored from a pre-open pull and compare with that first copy "
                                "(1 credit each; decides tempest_lag_sessions, owner 2026-10-09)")
    p.add_argument("--dates", type=_dates, required=True)
    add("tempest-depth", "does Tempest history reach back far enough before month 1 (1-2 credits)")
    add("sigma-check", "compare Tempest's live sigma lines (/v1/vol/cones) with the bot's expected-move lines "
                       "(1-2 credits; §13 Q-sigma, owner 2026-10-14)")
    add("dp-start-probe", "is dp_history_start still Skylit's first dark-pool date? (5 credits; owner 2026-10-07)")
    add("report", "write the parameter check for the owner (and the step-10 backfill estimate)")


def _choose_recording_target(args: argparse.Namespace, settings: dict, ssd, why: str | None):
    """stream-session --fallback-local (owner 2026-10-11): record on the SSD if it is reachable and writable, with
    the internal drive as the mid-session switch; else record on the internal drive from the start."""
    from nqbot.fallback import fallback_paths, folder_writable, ssd_writable
    from nqbot.storage import log_ssd_event

    local = fallback_paths(settings["paths"]["root"])
    if ssd is not None:
        ok, why = ssd_writable(ssd)
        if ok:
            args.switch_to = local
            print(f"recording target: the SSD ({ssd.root}); if it fails during the session: {local.root}")
            target = ssd
        else:
            ssd = None
    if ssd is None:
        args.switch_to = None
        log_ssd_event("stream-session", local.root, f"SSD not usable at the start ({why}): recording to {local.root}")
        print(f"recording target: the internal drive ({local.root}); SSD not usable: {why}")
        target = local
    if args.check_only:
        ok, msg = folder_writable(local.tmp_dir)
        print(f"internal drive folder writable: {ok} ({msg}; {local.root}); no stream started (--check-only)")
    return target


def _move_after_session(args: argparse.Namespace, settings: dict, rep: dict) -> None:
    """After the close: move a recording kept on the internal drive onto the SSD, if the SSD is usable now."""
    from nqbot.fallback import fallback_paths, move_fallback, ssd_writable

    local = fallback_paths(settings["paths"]["root"])
    if not local.root.exists():
        return
    try:
        ssd = resolve_paths(settings)
    except SSDNotFound as e:
        print(f"recording kept on the internal drive ({local.root}); SSD not found ({e}): run `uv run nqbot "
              "fallback-move` once it is back")
        return
    ok, why = ssd_writable(ssd)
    if not ok:
        print(f"recording kept on the internal drive ({local.root}); SSD not writable ({why}): run `uv run nqbot "
              "fallback-move` once it is back")
        return
    move_fallback(local, ssd)


def _run_step7(args: argparse.Namespace, settings: dict, paths, log) -> int:
    from nqbot.checks import step7
    from nqbot.clients.skylit import AccessDenied, OutOfCredits
    from nqbot.clients.skylit_credits import CreditRefused
    from nqbot.config import load_features
    from nqbot.months import LedgerMissing, NotDevelopmentMonth
    from nqbot.secrets import MissingSecret

    from nqbot.clients.skylit import SkylitError
    from nqbot.clients.skylit_store import CacheMismatch

    refusals = (step7.CheckRefused, CreditRefused, OutOfCredits, AccessDenied, LedgerMissing, MissingSecret,
                NotDevelopmentMonth)

    alongside = args.check in ALONGSIDE_CHECKS
    env = step7.Env(settings=settings, features=load_features(), paths=paths, log=log,
                    ledger_name=f"ledger_{args.check.replace('-', '_')}.jsonl" if alongside else "ledger.jsonl")
    approve = args.approve_credits
    # The checks that pull history or step-10 data hold the Skylit job lock (not the Databento one, so the step-8
    # pull runs alongside); sample-days' job run takes it itself and the report only reads. The short checks that
    # run alongside the step-10 backfill don't (decision 2, presd_fixes D3): each writes only its own files
    # (stream/, repull/<reason>/, vol/ under a new name, its own ledger file), and the backfill leaves
    # skylit_alongside_reserve_credits for them.
    lock = None
    if args.check not in ("sample-days", "report") and not alongside:
        paths.root.mkdir(parents=True, exist_ok=True)
        lock = job_locks(paths.root, (SKYLIT,), f"step7 {args.check}")
    from nqbot.power import keep_awake

    try:
        with ExitStack() as stack:
            # stream-record and tempest-poll run for a long time; a recording keeps the display on too (owner
            # 2026-10-19, §13 Q-power: screen off = Modern Standby, which cut 10-19's network)
            stack.enter_context(keep_awake(display=args.check in ("stream-session", "stream-record")))
            if lock is not None:
                stack.enter_context(lock)
            return _step7_check(args, settings, paths, log, env, approve)
    except LockHeld as e:  # like nqbot run: exit 3, the lock holder is named
        print(f"locked: {e}", file=sys.stderr)
        return EXIT_LOCKED
    except refusals as e:
        print(f"refused: {e}", file=sys.stderr)
        return EXIT_REFUSED
    except CacheMismatch as e:
        print(f"refused: {e}\nhint: a stored file was pulled with other parameters (e.g. skylit_max_strikes changed). "
              "Don't delete or move anything; ask Claude Code.", file=sys.stderr)
        return EXIT_REFUSED
    except SkylitError as e:
        print(f"stopped: Skylit answered {e}\nhint: {_step7_hint(args.check, e)}", file=sys.stderr)
        return EXIT_REFUSED


# The step-7 checks that run alongside the step-10 backfill without the Skylit lock (decision 2).
ALONGSIDE_CHECKS = frozenset({"stream-record", "stream-session", "parity", "late-prints-morning", "late-prints-compare", "tempest-poll",
                              "tempest-compare", "tempest-morning", "tempest-finality", "dp-start-probe",
                              "sigma-check", "stream-drops"})


def _step7_hint(check: str, e) -> str:
    """A plain next step for a Skylit error in a step-7 check (presd_fixes D8)."""
    if check == "darkpool" and e.status in (400, 404):
        return ("the date's 45-day lookback may reach past Skylit's 365-day history: pick a --date in development "
                "month 3 or later and run it again (refused calls are refunded)")
    if e.code == "dropped":
        return ("the connection dropped after the request was sent, twice (each may have been charged): check the "
                "balance, then run the same command again later (finished windows are cached, never paid twice)")
    if e.code == "gave_up":
        return ("Skylit kept timing out: wait a few minutes and run the same command again (failed calls are "
                "refunded; finished windows are cached, never paid twice)")
    return ("refused calls are refunded and finished pulls are cached: run the same command again later; ask "
            "Claude Code if it repeats")


def _step7_check(args: argparse.Namespace, settings, paths, log, env, approve) -> int:
    from nqbot.checks import step7
    from nqbot.clients.skylit_pull import SkylitPullJob

    match args.check:
        case "symbols":
            step7.check_symbols(env)
        case "strike-window":
            step7.check_strike_window(env, args.busy_day, args.sample_days,
                                      args.start or step7.STRIKE_WINDOW_DEFAULT_START, approve)
        case "sample-days":
            name = step7.sample_job_name(args.raised)
            step7.plan_sample_days(env, args.dates, approve, raised=args.raised)
            result = run_job(SkylitPullJob(name), settings, paths,
                             until=parse_until(args.until, datetime.now(ET)), log=log)
            print(f"{name}: {result.status} ({result.done}/{result.total} sessions)")
            if result.status != "complete":
                print(f"resume with: uv run nqbot run {name}, then re-run this command for the report")
                return EXIT_REFUSED
            step7.sample_days_report(env, args.dates, raised=args.raised)
        case "oldest-window":
            step7.check_oldest_window(env, approve)
        case "historical-instants":
            step7.check_historical_instants(env, approve)
        case "stream-record":
            step7.check_stream_record(env, approved=approve, force=args.force)
        case "stream-session":
            rep = step7.check_stream_session(env, args.maps, approved=approve,
                                             fallback=getattr(args, "switch_to", None))
            if getattr(args, "fallback_local", False):
                _move_after_session(args, settings, rep)
            if not rep["complete"]:
                return EXIT_INCOMPLETE
        case "parity":
            step7.check_parity(env, args.session, args.tag, approve)
        case "darkpool":
            step7.check_darkpool(env, args.date, args.check_dates, approve, depth_probe=not args.no_depth_probe,
                                 walk_forward=args.walk_forward)
        case "late-prints-morning":
            step7.check_late_prints_morning(env, approve, force=args.force)
        case "late-prints-compare":
            step7.check_late_prints_compare(env, approve)
        case "tempest-poll":
            step7.check_tempest_poll(env, args.until or step7.RTH_OPEN, approve, today=args.today)
        case "tempest-compare":
            step7.check_tempest_compare(env, approve)
        case "stream-drops":
            step7.check_stream_drops(env, args.session, args.window, approve)
        case "tempest-morning":
            step7.check_tempest_morning(env, args.date, approve)
        case "tempest-finality":
            step7.check_tempest_finality(env, args.dates, approve)
        case "sigma-check":
            step7.check_sigma(env, approve)
        case "tempest-depth":
            step7.check_tempest_depth(env, approve)
        case "dp-start-probe":
            step7.check_dp_start_probe(env, approve)
        case "report":
            step7.check_report(env)
    return 0


def _add_step8b(sub: argparse._SubParsersAction) -> None:
    """`nqbot step8b <check>`: the level-mapping precision gate (SPEC §5.1, §12 step 8b). Reads stored data only."""
    s8 = sub.add_parser("step8b", help="step-8b mapping precision gate (reads stored data only)").add_subparsers(
        dest="check", required=True)
    for name, help_ in (("precision", "mapping error per chain vs mapping_p95_max (the step-7 sample days)"),
                        ("spot-lag", "spot vs futures cross-correlation over +-120 s (diagnostic)"),
                        ("bias", "gate (a): median signed crossing error per chain, day-block bootstrap interval"),
                        ("estimator", "gate (b): the chosen R estimator's crossing p95 vs the best possible R"),
                        ("alignment", "gate: fitted content time per map never after floor(asOf) + 1 s"),
                        ("gate-day", "the gate's per-day work only (crossings, alignment fit), cached; the step8b_gate job"),
                        ("dislocations", "count fast moves of the mapping ratio (0.05/0.1/0.2%% in 2 min) and the guard's fires")):
        p = s8.add_parser(name, help=help_)
        p.add_argument("--dates", type=_dates, default=None,
                       help="comma-separated development-month dates (default: the step-7 sample days)")
        if name == "dislocations":
            p.add_argument("--months", choices=("development", "walk-forward"), default="development",
                           help="months 1-6 (default) or the walk-forward months 7-9 (automated counts only)")
    p = s8.add_parser("atlas", help="the QQQ/SPY nodes and strike crossings for the owner's Atlas replay check")
    p.add_argument("--date", type=_date, required=True, help="a sample day (YYYY-MM-DD)")
    p.add_argument("--minutes", type=lambda t: [_hhmm(x.strip()) for x in t.split(",") if x.strip()], required=True,
                   help="comma-separated ET minutes the owner picks (HH:MM,HH:MM)")
    p = s8.add_parser("atlas-recorded", help="the QQQ/SPY Atlas tables on a recorded session day (after recorded-day)")
    p.add_argument("date", type=_date, help="the recorded session (YYYY-MM-DD)")
    p = s8.add_parser("recorded-day", help="after a session recording: $0 NQ/ES trades, alignment, both NDXP methods")
    p.add_argument("date", type=_date, help="the recorded session (YYYY-MM-DD)")
    p.add_argument("--with-prev", action="store_true",
                   help="also pull the previous session's NQ trades ($0; for the basis method; owner's OK first)")
    s8.add_parser("ndxp-select", help="choose the NDXP method leave-one-day-out across the recorded days")
    s8.add_parser("report", help="write <reports_dir>/step8b/mapping_gate.md from the latest checks")


def _run_step8b(args: argparse.Namespace, settings, paths, log) -> int:
    from nqbot.checks import step8b, step8b_gate
    from nqbot.config import load_features
    from nqbot.months import LedgerMissing, NotDevelopmentMonth

    env = step8b.Env(settings=settings, features=load_features(), paths=paths, log=log)
    try:
        match args.check:
            case "precision":
                step8b.check_precision(env, args.dates)
            case "spot-lag":
                step8b.check_spot_lag(env, args.dates)
            case "bias":
                step8b_gate.check_bias(env, args.dates)
            case "estimator":
                step8b_gate.check_estimator(env, args.dates)
            case "alignment":
                step8b_gate.check_alignment(env, args.dates)
            case "gate-day":
                step8b_gate.check_gate_days(env, args.dates)
            case "dislocations":
                from nqbot.checks import step8b_disloc

                step8b_disloc.check_dislocations(env, args.dates, isolate=True, month_set=args.months)
            case "atlas":
                step8b.check_atlas(env, args.date, args.minutes)
            case "recorded-day":
                from nqbot.checks import step8b_recorded

                step8b_recorded.check_recorded_day(env, args.date, with_prev=args.with_prev)
            case "atlas-recorded":
                from nqbot.checks import step8b_recorded

                step8b_recorded.check_atlas_recorded(env, args.date)
            case "ndxp-select":
                from nqbot.checks import step8b_recorded

                step8b_recorded.check_ndxp_select(env)
            case "report":
                step8b.check_report(env)
    except (step8b.CheckRefused, LedgerMissing, NotDevelopmentMonth) as e:
        print(f"refused: {e}", file=sys.stderr)
        return EXIT_REFUSED
    except LockHeld as e:  # recorded-day takes the Databento lock (the nightly backfill holds it until 07:30)
        print(f"locked: {e}", file=sys.stderr)
        return EXIT_LOCKED
    return 0


def _add_step11(sub: argparse._SubParsersAction) -> None:
    """`nqbot step11 <check>`: step-11 checks on stored data (read-only, $0, no lock)."""
    s11 = sub.add_parser("step11", help="step-11 feature checks on stored data (read-only)").add_subparsers(
        dest="check", required=True)
    p = s11.add_parser("chart-day", help="smoke-run the chart-level engine on one development day (report only)")
    p.add_argument("date", type=_date, help="a development-month session (YYYY-MM-DD)")
    s11.add_parser("roll-report", help="F9.10 roll report: rvol_today / vol_tod_60 around each NQ roll, months 1-6")
    p = s11.add_parser("skylit-day", help="smoke-run the Skylit / cross-market engine on one development day (report "
                                          "only)")
    p.add_argument("date", type=_date, help="a development-month session (YYYY-MM-DD)")
    s11.add_parser("nodetype-check", help="F2.2 / F2.5: Skylit's nodeType vs the bot's King / Gatekeeper on the "
                                          "stored step-7 instants (report only)")
    p = s11.add_parser("build-day", help="smoke-run the part-3 session engine (zones, touches, labels, rows) on one "
                                         "development day, twice from the same state (report only)")
    p.add_argument("date", type=_date, help="a development-month session (YYYY-MM-DD)")
    p.add_argument("--full-warmup", action="store_true", help="build the warm-up sessions fully, as the job does")


def _run_step11(args: argparse.Namespace, settings, paths, log) -> int:
    from nqbot import calendar as cal_mod
    from nqbot.checks import step11
    from nqbot.checks.step8b import Env
    from nqbot.config import load_features
    from nqbot.months import LedgerMissing, NotDevelopmentMonth

    env = Env(settings=settings, features=load_features(), paths=paths, log=log, cal=cal_mod.load())
    try:
        if args.check == "chart-day":
            step11.chart_day(env, args.date)
        elif args.check == "roll-report":
            step11.roll_report(env)
        elif args.check == "skylit-day":
            step11.skylit_day(env, args.date)
        elif args.check == "nodetype-check":
            return step11.nodetype_check(env)
        elif args.check == "build-day":
            from nqbot.checks import feature_build as fb
            from nqbot.checks import session_build as sb
            from nqbot.features.session_features import NotWired
            try:
                fb.build_day_smoke(env, args.date, full_warmup=args.full_warmup)
            except (fb.BuildRefused, sb.Refused, NotWired) as e:
                print(f"refused: {e}", file=sys.stderr)
                return EXIT_REFUSED
    except (LedgerMissing, NotDevelopmentMonth, step11.Refused, step11.cl.RollSpreadMissing,
            step11.BoardsRefused) as e:
        print(f"refused: {e}", file=sys.stderr)
        return EXIT_REFUSED
    return 0


def _add_step12(sub: argparse._SubParsersAction) -> None:
    """`nqbot step12 <check>`: the step-12 visual check on stored data (read-only, $0, no lock; development months)."""
    s12 = sub.add_parser("step12", help="step-12 visual check: plots, Deepcharts reading tables, LVN lifetimes, "
                                        "node types, single prints (read-only)").add_subparsers(dest="check",
                                                                                                required=True)
    s12.add_parser("scan", help="which sample day has a 10:30 redraw / a flip; else the earliest development day")
    p = s12.add_parser("day", help="one day: reading table (CSV), plots (PNG + HTML) with events by label, day report")
    p.add_argument("date", type=_date, help="a sample day or a day the scan added (YYYY-MM-DD)")
    p = s12.add_parser("lvn", help="every daily / weekly LVN span of one development day with its lifetime")
    p.add_argument("date", type=_date, help="a development-month session (YYYY-MM-DD)")
    s12.add_parser("nodetype", help="node-type agreement on the stored step-7 instants (confusion tables)")
    s12.add_parser("single-prints", help="single-print counts over months 1-6 at sp_min_ticks 4 / 6 / 8 (report only)")
    s12.add_parser("compare", help="the owner's Deepcharts values against the bot's (after the CSVs are filled in)")
    s12.add_parser("events-breakdown", help="months 1-6 events by session part and zone makeup, LVN ages, held paths "
                                            "(report only)")
    s12.add_parser("all", help="scan, every day (own process), LVN lists, nodetype, single prints, summary")


def _run_step12(args: argparse.Namespace, settings, paths, log) -> int:
    from nqbot import calendar as cal_mod
    from nqbot.checks import feature_build as fb
    from nqbot.checks import session_build as sb
    from nqbot.checks import step12
    from nqbot.checks.step8b import Env
    from nqbot.config import load_features
    from nqbot.months import LedgerMissing, NotDevelopmentMonth

    env = Env(settings=settings, features=load_features(), paths=paths, log=log, cal=cal_mod.load())
    try:
        if args.check == "scan":
            step12.scan(env)
        elif args.check == "day":
            step12.day_cmd(env, args.date)
        elif args.check == "lvn":
            step12.lvn_cmd(env, args.date)
        elif args.check == "nodetype":
            return step12.nodetype_report(env)
        elif args.check == "single-prints":
            step12.single_prints(env)
        elif args.check == "compare":
            step12.compare(env)
        elif args.check == "events-breakdown":
            step12.events_breakdown(env)
        elif args.check == "all":
            return step12.all_cmd(env)
    except (LedgerMissing, NotDevelopmentMonth, step12.Refused, fb.BuildRefused, sb.Refused) as e:
        print(f"refused: {e}", file=sys.stderr)
        return EXIT_REFUSED
    return 0


def _add_features(sub: argparse._SubParsersAction) -> None:
    """`nqbot features <plan|build-day>`: the step-11 feature build (the job itself: `nqbot run feature_build`)."""
    fe = sub.add_parser("features", help="step 11: the feature build (plan, one session)").add_subparsers(
        dest="action", required=True)
    p = fe.add_parser("plan", help="write the feature-build plan for these ledger months (then: nqbot run "
                                   "feature_build --until 07:30)")
    p.add_argument("--months", required=True, help="ledger months, e.g. 1-12 or 1-4")
    p.add_argument("--batch", type=int, default=None, help="pass-2 sessions at once, the pool size (default: all cores but 2)")
    p.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
                   help="a run-time override of a features.yaml value for this build only (step-13 tuning on ledger "
                        "months 1-4 only; stored in the plan and in its key; a.b=v sets one entry of a mapping)")
    slot_help = ("build slot: 1 = job feature_build (default), 2 = job feature_build_2 with its own plan (owner "
                 "2026-11-12; both at once only with parallel_builds in config/local.yaml)")
    p.add_argument("--slot", type=int, default=1, choices=(1, 2), help=slot_help)
    p = fe.add_parser("build-warmup", help="the full warm-up before the plan's first session (the job's chunk p0)")
    p.add_argument("--slot", type=int, default=1, choices=(1, 2), help=slot_help)
    p = fe.add_parser("build-day", help="one session of the plan (the job runs this in its own process)")
    p.add_argument("date", type=_date)
    p.add_argument("--pass", dest="pass_no", type=int, choices=(1, 2), required=True)
    p.add_argument("--slot", type=int, default=1, choices=(1, 2), help=slot_help)


def _add_step13(sub: argparse._SubParsersAction) -> None:
    """`nqbot step13 <check-plan|check-day|jump-plot|check-report>`: step 13-0's check of the corrections code
    (development days only, the stored v36 build read-only; owner 2026-11-09)."""
    s13 = sub.add_parser("step13", help="step 13-0: the corrections check on the development days (then: nqbot run "
                                        "step13_check --until HH:MM)").add_subparsers(dest="check", required=True)
    p = s13.add_parser("check-plan", help="record the check's chunks (runs x days, the jump plot, the report)")
    p.add_argument("--redo", default=None, help="comma-separated runs to build again (their chunks, the plot and the "
                                                "report are marked not done)")
    p = s13.add_parser("check-day", help="one (run, day): build from the v36 start state, write to the scratch "
                                         "folder, diff with v36 (the job runs this in its own process)")
    p.add_argument("date", type=_date)
    p.add_argument("--run", required=True)
    s13.add_parser("jump-plot", help="the 2025-11-18 weekly POC jump plot with the fixed legend (Q-step12b (e))")
    s13.add_parser("check-report", help="the report from the chunks done so far")
    # step 13a (owner 2026-11-09/10, SPEC §13 Q-dryrun13 (3), Q-step13a): the reaction-distance report
    s13.add_parser("reactions-plan", help="step 13a: record the reaction job's chunks (months 1-4 sessions of the "
                                          "feature-build plan, then the report; then: nqbot run step13a_reactions)")
    p = s13.add_parser("reactions-day", help="step 13a: one session's approaches from its level timelines (the job "
                                             "runs this in its own process)")
    p.add_argument("date", type=_date)
    s13.add_parser("reactions-report", help="step 13a: the widths proposal, charts and tables (reactions.md)")
    # step 13b (owner 2026-11-10, SPEC §13 Q-step13b): smoke run, counts, slippage, labeler, budget
    p = s13.add_parser("events-day", help="step 13b: the engine stage and the labeler on one development session, "
                                          "twice, with times, pairs, disk and the extrapolation (events_day_<D>.md)")
    p.add_argument("date", type=_date)
    s13.add_parser("counts", help="step 13b: style-(c) firings, the flip checkpoint, golden-zone counts and the 13d "
                                  "build counts from the months 1-4 build (counts.md)")
    s13.add_parser("slippage-plan", help="step 13b: record the slippage job's chunks (then: nqbot run step13_slippage)")
    p = s13.add_parser("slippage-day", help="step 13b: one session's stop slippage from mbp-1 (the job runs this)")
    p.add_argument("date", type=_date)
    s13.add_parser("slippage-report", help="step 13b: the exit_slippage_ticks proposal and the be_offset_ticks re-check")
    p = s13.add_parser("labels-plan", help="step 13b: the labeler's plan and key (then: nqbot run step13_labels)")
    p.add_argument("--firings-ok", action="store_true", help="the owner has read the style-(c) firing examples "
                                                              "(more than 5 on an event; owner 2026-11-10)")
    p.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
                   help="a labeler setting for this run (13c: e.g. stop_buffer=8); part of the labels key")
    p = s13.add_parser("labels-day", help="step 13b: one session's pairs, fills and paths (the job runs this)")
    p.add_argument("date", type=_date)
    p.add_argument("--build-key", default=None, help="step 13c: label this build (its own labels plan) instead of the "
                                                     "slot-1 plan's")
    p.add_argument("--label-key", default=None, help="step 13c: the labels key of that build's plan")
    # step 13c (owner 2026-11-14, SPEC §13 Q-step13c): the tuning driver, rows 1-14
    p = s13.add_parser("tune-plan", help="step 13c: record the tuning program and its chunks (then: nqbot run "
                                         "step13_tune --until HH:MM, or nqbot queue add step13_tune)")
    p.add_argument("--reference-labels", default="8e9cdad94970",
                   help="the baseline's labels the relabel must equal (r00:check; empty: no check)")
    p = s13.add_parser("tune-row", help="step 13c: score one row again from its builds and labels (report + winner)")
    p.add_argument("row", help="row id, e.g. r01, r05b, r12, r14a")
    s13.add_parser("tune-report", help="step 13c: the summary of every winner so far (tune_summary.md)")
    p = s13.add_parser("vol-floor-day", help="step 13c: one session's QQQ crossings and trailing volatility for the "
                                             "Q13-vol re-fit (the driver runs this)")
    p.add_argument("date", type=_date)
    s13.add_parser("labels-report", help="step 13b: the labels report (identity check, RR bands, time, disk)")
    s13.add_parser("budget", help="step 13b: the step-13 budget re-estimated from the measured months 1-4 rebuild")


def _run_step13(args: argparse.Namespace, settings, paths, log) -> int:
    from nqbot import calendar as cal_mod
    from nqbot.checks import session_build as sb
    from nqbot.checks import feature_build as fb
    from nqbot.checks import step13 as s13
    from nqbot.checks import step13a as s13a
    from nqbot.checks import step13b as s13b
    from nqbot.checks import step13c as s13c
    from nqbot.checks.step8b import CheckRefused, Env
    from nqbot.config import load_features
    from nqbot.jobs.runner import record_plan
    from nqbot.months import NotDevelopmentMonth

    env = Env(settings=settings, features=load_features(), paths=paths, log=log, cal=cal_mod.load())
    try:
        if args.check == "events-day":
            print(s13b.events_day(env, args.date))
        elif args.check == "counts":
            print(s13b.counts(env))
        elif args.check == "slippage-plan":
            done, total = record_plan(paths, s13b.JOB_SLIP, s13b.slip_chunk_ids(env))
            print(f"{s13b.JOB_SLIP}: {done}/{total} chunks done; run: uv run nqbot run {s13b.JOB_SLIP} --until HH:MM")
        elif args.check == "slippage-day":
            s13b.slippage_day(env, args.date)
        elif args.check == "slippage-report":
            print(s13b.slippage_report(env))
        elif args.check == "labels-plan":
            plan = s13b.labels_plan(env, args.firings_ok, fb.parse_overrides(args.overrides) if args.overrides else None)
            done, total = record_plan(paths, s13b.JOB_LABELS, s13b.labels_chunk_ids(env))
            print(f"{s13b.JOB_LABELS}: labels key {plan['label_key']} on build {plan['build_key']}; {done}/{total} "
                  f"chunks done; run: uv run nqbot run {s13b.JOB_LABELS} --until HH:MM")
        elif args.check == "labels-day":
            if args.build_key or args.label_key:   # the step-13c driver (owner 2026-11-14): labels by build key
                if not (args.build_key and args.label_key):
                    raise s13b.Refused("--build-key and --label-key go together")
                s13b.labels_day_for(env, args.build_key, args.label_key, args.date)
            else:
                s13b.labels_day(env, args.date)
        elif args.check == "tune-plan":
            s13c.tune_plan(env, args.reference_labels or None)
        elif args.check == "tune-row":
            plan = s13c.load_plan(env)
            row = s13c.prog.row_of(s13c.program(env), args.row)
            s13c.score_row(env, plan, row)
            print(f"{args.row}: winner {s13c.winners(env)[args.row]['value']!r} (report in {s13c.rep_dir(env)})")
        elif args.check == "tune-report":
            print(s13c.summary(env, s13c.load_plan(env)))
        elif args.check == "vol-floor-day":
            print(s13c.volfloor_day(env, args.date))
        elif args.check == "labels-report":
            print(s13b.labels_report(env))
        elif args.check == "budget":
            print(s13b.budget(env))
        elif args.check == "reactions-plan":
            done, total = record_plan(paths, s13a.JOB, s13a.chunk_ids(env))
            print(f"{s13a.JOB}: {done}/{total} chunks done; run: uv run nqbot run {s13a.JOB} --until HH:MM")
        elif args.check == "reactions-day":
            s13a.reactions_day(env, args.date)
        elif args.check == "reactions-report":
            s13a.report(env)
        elif args.check == "check-plan":
            if args.redo:
                gone = s13.redo(paths, [r.strip() for r in args.redo.split(",") if r.strip()])
                print(f"{s13.JOB}: {len(gone)} chunks marked to build again")
            done, total = record_plan(paths, s13.JOB, s13.chunk_ids())
            print(f"{s13.JOB}: {done}/{total} chunks done; run: uv run nqbot run {s13.JOB} --until HH:MM")
        elif args.check == "check-day":
            s13.check_day(env, args.date, args.run)
        elif args.check == "jump-plot":
            s13.jump_plot(env)
        else:
            s13.report(env)
    except (s13.Refused, s13a.Refused, s13b.Refused, s13c.TuneRefused, fb.BuildRefused, sb.Refused, CheckRefused,
            NotDevelopmentMonth) as e:
        print(f"refused: {e}", file=sys.stderr)
        return EXIT_REFUSED
    return 0


def _run_features(args: argparse.Namespace, settings, paths, log) -> int:
    from nqbot import calendar as cal_mod
    from nqbot.checks import feature_build as fb
    from nqbot.checks import session_build as sb
    from nqbot.checks.step8b import CheckRefused, Env
    from nqbot.config import load_features
    from nqbot.features.chart_levels import RollSpreadMissing
    from nqbot.features.session_features import NotWired
    from nqbot.months import LedgerMissing

    env = Env(settings=settings, features=load_features(), paths=paths, log=log, cal=cal_mod.load())
    try:
        from nqbot.checks import feature_build_job as fbj

        if args.action == "plan":
            fbj.plan_slot(env, fb.parse_months(args.months), args.batch, fb.parse_overrides(args.overrides),
                          slot=args.slot)
            return 0
        with fbj.using_slot(args.slot):   # this process serves that slot only
            if args.action == "build-warmup":
                return fb.build_warmup_cmd(env)
            return fb.build_day_cmd(env, args.date, args.pass_no)
    except (fb.BuildRefused, sb.Refused, NotWired, LedgerMissing, RollSpreadMissing, CheckRefused) as e:
        print(f"refused: {e}", file=sys.stderr)
        return EXIT_REFUSED


def _add_skylit(sub: argparse._SubParsersAction) -> None:
    """`nqbot skylit plan`: the step-10 backfill plan and the owner's credit approval (SPEC §3.1, §12 step 10)."""
    sk = sub.add_parser("skylit", help="step-10 Skylit backfill (SSD day)").add_subparsers(dest="action", required=True)
    p = sk.add_parser("plan", help="print the backfill estimate and write the plan for nqbot run skylit_backfill")
    p.add_argument("--approve-credits", type=int, default=None,
                   help="owner-approved credits for the whole backfill (CLAUDE.md rule 2), after the pack purchase")
    m = sk.add_parser("multiexp-plan", help="multi-expiration snapshots (owner 2026-10-23): print each tier's cost, "
                      "write the plan for nqbot run skylit_multiexp")
    m.add_argument("--tiers", required=True, help="comma-separated, of 0925,1000 (pulled in that order)")
    m.add_argument("--end", required=True, type=date.fromisoformat, help="last session to pull (YYYY-MM-DD)")
    m.add_argument("--approve-credits", type=int, default=None, help="owner-approved credits (CLAUDE.md rule 2)")
    sk.add_parser("multiexp-coverage", help="write the multi-expiration coverage report from what is stored")


def _run_skylit(args: argparse.Namespace, settings: dict, paths, log) -> int:
    from nqbot.checks import step7
    from nqbot.clients import skylit_backfill as bf
    from nqbot.clients.skylit import SkylitError
    from nqbot.clients.skylit_credits import CreditRefused
    from nqbot.config import load_features
    from nqbot.months import LedgerMissing
    from nqbot.secrets import MissingSecret

    if args.action in ("multiexp-plan", "multiexp-coverage"):
        return _run_multiexp(args, settings, paths, log)
    env = step7.Env(settings=settings, features=load_features(), paths=paths, log=log)
    paths.root.mkdir(parents=True, exist_ok=True)
    try:
        with job_locks(paths.root, (SKYLIT,), "skylit plan"):
            bf.make_plan(env, args.approve_credits)
    except LockHeld as e:
        print(f"refused: {e}", file=sys.stderr)
        return EXIT_LOCKED
    except (bf.BackfillRefused, CreditRefused, SkylitError, LedgerMissing, MissingSecret, ValueError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return EXIT_REFUSED
    return 0


def _run_multiexp(args: argparse.Namespace, settings: dict, paths, log) -> int:
    import json as _json

    from nqbot.clients import skylit_multiexp as mx
    from nqbot.clients.skylit import SkylitError
    from nqbot.clients.skylit_store import SkylitStore
    from nqbot.config import load_features
    from nqbot.months import LedgerMissing
    from nqbot.secrets import MissingSecret

    if args.action == "multiexp-coverage":
        plan = _json.loads((mx.job_dir(paths) / "plan.json").read_text(encoding="utf-8"))
        out = paths.reports_dir / "skylit_multiexp" / "coverage.md"
        from nqbot.storage import atomic_write_text

        atomic_write_text(out, mx.coverage(paths, plan))
        print(out)
        return 0
    paths.root.mkdir(parents=True, exist_ok=True)
    try:
        with job_locks(paths.root, (SKYLIT,), "skylit multiexp-plan"):
            client = mx.make_client(settings, SkylitStore(paths, ledger_name=mx.LEDGER), log)
            mx.make_plan(paths, settings, load_features(), args.tiers.split(","), args.end, args.approve_credits,
                         client.credits.remaining)
    except LockHeld as e:
        print(f"refused: {e}", file=sys.stderr)
        return EXIT_LOCKED
    except (mx.MultiExpRefused, SkylitError, LedgerMissing, MissingSecret, ValueError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return EXIT_REFUSED
    return 0


def _add_databento(sub: argparse._SubParsersAction) -> None:
    """`nqbot databento plan`: resolve contracts, print the cost estimate, write the step-8 pull plan (§12 step 8)."""
    db = sub.add_parser("databento", help="step-8 Databento pull (SSD day)").add_subparsers(dest="action", required=True)
    v = db.add_parser("volumes", help="pull the candidate contracts' hourly bars the front rule needs ($0 by get_cost, "
                      "else refused) and print the rolls it gives (SPEC §3.2 Front month; no plan written)")
    v.add_argument("--today", type=_date, default=None, help=argparse.SUPPRESS)  # testing only
    p = db.add_parser("plan", help="decide front contracts (from the stored hourly volumes), print metadata.get_cost, "
                      "write the plan (no data pulled)")
    p.add_argument("--approve-usd", type=float, default=None,
                   help="owner-approved pay-as-you-go dollars (warm-up trades, days past the included window)")
    p.add_argument("--retry-failed-payg", action="store_true",
                   help="let broken pay-as-you-go downloads run once more (only after the owner checked billing)")
    p.add_argument("--today", type=_date, default=None, help=argparse.SUPPRESS)  # testing only
    c = db.add_parser("condition", help="record get_dataset_condition for the plan's days (warm-up included), measure "
                      "the non-available days' damage from stored files, write the step-8 report (free, no pull)")
    c.add_argument("--no-measure", action="store_true", help="record the conditions only")


def _run_databento(args: argparse.Namespace, settings: dict, paths, log) -> int:
    from nqbot.clients.databento_client import DatabentoClient, DatabentoError
    from nqbot.clients.databento_pull import PlanRefused, make_plan, pull_front_volumes
    from nqbot.clients.databento_volume import VolumesRefused
    from nqbot.clients.databento_store import DatabentoStore
    from nqbot.config import load_features
    from nqbot.months import LedgerMissing
    from nqbot.secrets import MissingSecret

    store = DatabentoStore(paths)
    paths.root.mkdir(parents=True, exist_ok=True)
    if args.action == "condition":
        from nqbot.clients.databento_pull import condition_report

        try:
            client = DatabentoClient.from_env(on_call=store.log_call, log=log)
            path = condition_report(client, store, settings, measure=not args.no_measure)
        except (MissingSecret, DatabentoError, ConfigError, ValueError, FileNotFoundError) as e:
            print(f"refused: {e}", file=sys.stderr)
            return EXIT_REFUSED
        print(f"report: {path}")
        return 0
    if args.action == "volumes":
        try:
            with job_locks(paths.root, (DATABENTO,), "databento volumes"):
                client = DatabentoClient.from_env(on_call=store.log_call, log=log)
                now = datetime.now(ET)
                pull_front_volumes(client, store, settings, load_features(), args.today or now.date(),
                                   now=None if args.today else now)
        except (LedgerMissing, MissingSecret, LockHeld, DatabentoError, PlanRefused, VolumesRefused, ConfigError,
                ValueError) as e:
            print(f"refused: {e}", file=sys.stderr)
            return EXIT_REFUSED
        return 0
    try:
        with job_locks(paths.root, (DATABENTO,), "databento plan"):
            client = DatabentoClient.from_env(on_call=store.log_call, log=log)
            now = datetime.now(ET)
            make_plan(client, store, settings, load_features(), args.today or now.date(),
                      approved_usd=args.approve_usd, now=None if args.today else now,
                      retry_failed_payg=args.retry_failed_payg)
    except (LedgerMissing, MissingSecret, LockHeld, DatabentoError, PlanRefused, ConfigError, ValueError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return EXIT_REFUSED
    return 0


def _add_gameplan(sub: argparse._SubParsersAction) -> None:
    """`nqbot gameplan <file>` and `nqbot gameplan-batch`: the step-9 parser (SPEC §3.4, §5.6)."""
    p = sub.add_parser("gameplan", help="parse one gameplan (.md/.pdf/.docx) into processed/gameplans/<date>.json")
    p.add_argument("file")
    p.add_argument("--date", type=_date, default=None, help="the gameplan's date when neither name nor text has it")
    p.add_argument("--reparse", action="store_true", help="send a file already parsed again (costs another call)")
    p.add_argument("--replace", action="store_true",
                   help="use this file for that date instead of the one parsed before (the old copy is moved aside)")
    p.add_argument("--approve-usd", type=float, default=None,
                   help="owner-approved dollars when the worst case is over gameplan_max_usd_per_file")
    p = sub.add_parser("gameplan-batch", help="parse every file in raw/gameplans/ and report gameplan days per month")
    p.add_argument("--approve-usd", type=float, default=None,
                   help="owner-approved dollars for the batch's worst-case API cost (CLAUDE.md rule 2)")
    p.add_argument("--reparse", action="store_true", help="send files already parsed again")


def _run_gameplan(args: argparse.Namespace, settings: dict, paths, log) -> int:
    from pathlib import Path

    from nqbot.gameplan import parser as gp
    from nqbot.jobs.lock import JobLock
    from nqbot.secrets import MissingSecret

    store = gp.GameplanStore.from_paths(paths)
    store.processed_dir.mkdir(parents=True, exist_ok=True)
    try:
        # Not the SSD job lock: a daily parse must not wait for an overnight pull; only the two gameplan
        # commands share this one (they both write the gameplan index).
        with JobLock(store.lock_path, args.command, unlock_cmd="uv run nqbot unlock --gameplan"):
            client = gp.make_client(settings)
            if args.command == "gameplan":
                file = Path(args.file)
                if not file.is_file():
                    print(f"refused: {file} not found", file=sys.stderr)
                    return EXIT_REFUSED
                cap = max(settings["gameplan_max_usd_per_file"], args.approve_usd or 0)
                r = gp.parse_file(client, settings, store, file, given_date=args.date, reparse=args.reparse,
                                  replace=args.replace, cap_usd=cap)
                print(f"{r.file}: {r.status} {r.date or ''} {r.message}".rstrip())
                for f in r.flags:  # info flags never need the owner's confirmation (SPEC §3.4)
                    kind = "review" if f.get("review", True) else "info"
                    print(f"  {kind} {f['field']} ({f['origin']}): {f['reason']}")
                if r.status in ("ok", "flagged", "cached"):
                    print(f"{store.json_path(r.date)} (cost ${r.cost_usd:.3f})")
                log.info("gameplan %s: %s %s %s", r.file, r.status, r.date, r.message)
                return 0 if r.status in ("ok", "flagged", "cached") else EXIT_REFUSED
            results, report = gp.batch(client, settings, store, approved_usd=args.approve_usd,
                                       reparse=args.reparse, still_mounted=paths.mounted)
            print(report.read_text(encoding="utf-8"))
            print(f"report: {report}; spent ${sum(r.cost_usd for r in results):.2f}")
            log.info("gameplan-batch: %d results, report %s", len(results), report)
    except LockHeld as e:
        print(f"refused: {e}", file=sys.stderr)
        return EXIT_LOCKED
    except (gp.GameplanRefused, MissingSecret) as e:
        print(f"refused: {e}", file=sys.stderr)
        return EXIT_REFUSED
    return 0


if __name__ == "__main__":
    sys.exit(main())
