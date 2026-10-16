"""The `nqbot` command (run as `uv run nqbot ...`). Later steps add their commands here."""

from __future__ import annotations

import argparse
import sys
from contextlib import ExitStack
from datetime import date, datetime, time

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
    sub.add_parser("status", help="SSD use and job progress")
    v = sub.add_parser("verify", help="read back every file under the root written since a date and list anything "
                                      "damaged (owner 2026-10-11; reads only, report in <reports_dir>/verify/)")
    v.add_argument("--since", type=lambda t: datetime.fromisoformat(t), required=True, help="YYYY-MM-DD (local time)")
    sub.add_parser("preflight", help="pre-session machine check: restart pending, Windows Update paused through the "
                                     "session, SSD writable, AC power, SSD drops (exit 5 on any warning; §9)")
    sub.add_parser("fallback-move", help="move a session recording kept on the internal drive (stream-session "
                                         "--fallback-local) onto the SSD, checked file by file")
    sub.add_parser("test", help="run the full test suite; the whole output goes to <root>/logs/pytest_<date-time>.txt, "
                                "ending with 'PYTEST EXIT CODE: N' (owner 2026-10-10: run before every commit); any "
                                "further arguments go to pytest (the commit check uses none)")
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
    _add_skylit(sub)
    _add_databento(sub)
    _add_gameplan(sub)
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
    if args.command == "setup-ssd":  # before resolve_paths: paths.root and the marker don't exist yet
        return _run_setup_ssd(args, settings)
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
        print("\n".join(status_lines(paths)))
        return 0

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
        from nqbot.testrun import run_tests

        return run_tests(paths.log_dir, extra)[0]
    if args.command == "step7":
        return _run_step7(args, settings, paths, log)
    if args.command == "step8b":
        return _run_step8b(args, settings, paths, log)
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
    return 0 if result.status in ("complete", "until", "stopped") else EXIT_REFUSED


def _stop(args: argparse.Namespace, paths) -> int:
    """Ask a running job to stop between calls (decision 2): it finishes and saves the call it is in."""
    from nqbot.jobs.runner import stop_request_path
    from nqbot.storage import atomic_write_json

    holders = [read_lock(lock_path(paths.root, k)) for k in ALL_LOCKS if lock_path(paths.root, k).exists()]
    if not any(h and h.get("job") == args.job for h in holders):
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
        kinds = [SKYLIT] if args.skylit else [DATABENTO] if args.databento else list(ALL_LOCKS)
        locks = [(f"{k} job lock", lock_path(paths.root, k)) for k in kinds]
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
            stack.enter_context(keep_awake())  # stream-record and tempest-poll run for a long time
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
                        ("dislocations", "count fast moves of the mapping ratio (0.05/0.1/0.2% in 2 min) and the guard's fires")):
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
            case "dislocations":
                from nqbot.checks import step8b_disloc

                step8b_disloc.check_dislocations(env, args.dates, isolate=True, month_set=args.months)
            case "atlas":
                step8b.check_atlas(env, args.date, args.minutes)
            case "recorded-day":
                from nqbot.checks import step8b_recorded

                step8b_recorded.check_recorded_day(env, args.date, with_prev=args.with_prev)
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


def _add_skylit(sub: argparse._SubParsersAction) -> None:
    """`nqbot skylit plan`: the step-10 backfill plan and the owner's credit approval (SPEC §3.1, §12 step 10)."""
    sk = sub.add_parser("skylit", help="step-10 Skylit backfill (SSD day)").add_subparsers(dest="action", required=True)
    p = sk.add_parser("plan", help="print the backfill estimate and write the plan for nqbot run skylit_backfill")
    p.add_argument("--approve-credits", type=int, default=None,
                   help="owner-approved credits for the whole backfill (CLAUDE.md rule 2), after the pack purchase")


def _run_skylit(args: argparse.Namespace, settings: dict, paths, log) -> int:
    from nqbot.checks import step7
    from nqbot.clients import skylit_backfill as bf
    from nqbot.clients.skylit import SkylitError
    from nqbot.clients.skylit_credits import CreditRefused
    from nqbot.config import load_features
    from nqbot.months import LedgerMissing
    from nqbot.secrets import MissingSecret

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


def _add_databento(sub: argparse._SubParsersAction) -> None:
    """`nqbot databento plan`: resolve contracts, print the cost estimate, write the step-8 pull plan (§12 step 8)."""
    db = sub.add_parser("databento", help="step-8 Databento pull (SSD day)").add_subparsers(dest="action", required=True)
    p = db.add_parser("plan", help="resolve front contracts, print metadata.get_cost, write the plan (no data pulled)")
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
    from nqbot.clients.databento_pull import PlanRefused, make_plan
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
