"""The `nqbot` command (run as `uv run nqbot ...`). Later steps add their commands here."""

from __future__ import annotations

import argparse
import sys
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


def _until(text: str) -> str:
    try:
        parse_until(text, datetime.now(ET))
    except ValueError:
        raise argparse.ArgumentTypeError(f"--until wants HH:MM (24-hour ET), got {text!r}") from None
    return text


def main(argv: list[str] | None = None) -> int:
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
    sub.add_parser("status", help="SSD use and job progress")
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
    _add_skylit(sub)
    _add_databento(sub)
    _add_gameplan(sub)
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_REFUSED
    if args.command == "setup-ssd":  # before resolve_paths: paths.root and the marker don't exist yet
        return _run_setup_ssd(args, settings)
    try:
        paths = resolve_paths(settings)
    except SSDNotFound as e:
        print(f"SSD not found: {e}", file=sys.stderr)
        return EXIT_REFUSED

    if args.command == "status":
        print("\n".join(status_lines(paths)))
        return 0

    if args.command == "unlock":
        return _unlock(args, paths)

    log = setup_logging(paths.log_dir)
    if args.command == "step7":
        return _run_step7(args, settings, paths, log)
    if args.command == "skylit":
        return _run_skylit(args, settings, paths, log)
    if args.command == "databento":
        return _run_databento(args, settings, paths, log)
    if args.command in ("gameplan", "gameplan-batch"):
        return _run_gameplan(args, settings, paths, log)
    from nqbot.clients.skylit import SkylitError
    from nqbot.clients.skylit_backfill import BackfillRefused
    from nqbot.clients.skylit_credits import CreditRefused
    from nqbot.secrets import MissingSecret

    job = get_job(args.job)
    until = parse_until(args.until, datetime.now(ET))
    _print_power_warnings()
    try:
        result = run_job(job, settings, paths, until=until, log=log)
    except LockHeld as e:
        log.error("%s", e)
        return EXIT_LOCKED
    except (BackfillRefused, CreditRefused, MissingSecret) as e:
        print(f"refused: {e}", file=sys.stderr)
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
    return 0 if result.status in ("complete", "until") else EXIT_REFUSED


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
                   help="re-pull them after skylit_max_strikes was raised (rule-2 exception, kept apart)")
    p.add_argument("--until", default=DEFAULT_UNTIL, type=_until)
    add("historical-instants", "40 /v1/historical instants over development months 1-6 (200 credits)")
    p = add("stream-record", "record 15 minutes of /v1/stream during a live session (about 70 credits)")
    p.add_argument("--force", action="store_true", help="record outside the session (testing only)")
    p = add("parity", "pull the recorded minutes from history and compare (25 credits)")
    p.add_argument("--session", type=_date, required=True)
    p.add_argument("--tag", default=None, help="which recording of that session (default: the latest)")
    p = add("darkpool", "45 days of QQQ/SPY prints + top-prints cross-check")
    p.add_argument("--date", type=_date, required=True)
    p.add_argument("--check-dates", type=_dates, default=[])
    p.add_argument("--no-depth-probe", action="store_true")
    p = add("late-prints-morning", "pre-open pull of the previous session's prints + top-3 lists")
    p.add_argument("--force", action="store_true")
    add("late-prints-compare", "re-pull each morning >= 5 trading days later and compare")
    p = add("tempest-poll", "poll the free /v1/vol/symbols until the previous session's row appears")
    p.add_argument("--until", type=_hhmm, default=None, help="stop polling at this ET time (default 09:30)")
    add("tempest-compare", "re-pull the oldest stored Tempest row not yet re-checked and diff it")
    add("tempest-depth", "does Tempest history reach back far enough before month 1 (1-2 credits)")
    add("report", "write the parameter check for the owner (and the step-10 backfill estimate)")


def _run_step7(args: argparse.Namespace, settings: dict, paths, log) -> int:
    from nqbot.checks import step7
    from nqbot.clients.skylit import AccessDenied, OutOfCredits
    from nqbot.clients.skylit_credits import CreditRefused
    from nqbot.clients.skylit_pull import SkylitPullJob
    from nqbot.config import load_features
    from nqbot.months import LedgerMissing, NotDevelopmentMonth
    from nqbot.secrets import MissingSecret

    refusals = (step7.CheckRefused, CreditRefused, OutOfCredits, AccessDenied, LedgerMissing, MissingSecret,
                LockHeld, NotDevelopmentMonth)

    env = step7.Env(settings=settings, features=load_features(), paths=paths, log=log)
    approve = args.approve_credits
    # One process spends Skylit credits at a time (shared rate limit; per-call charges read from the balance):
    # every check holds the Skylit job lock (not the Databento one, so the step-8 pull runs alongside), except the
    # hours-long Tempest poll (free polls, a 1-credit pull), the report (reads only) and sample-days, whose job run
    # takes the lock itself.
    lock = None
    if args.check not in ("tempest-poll", "sample-days", "report"):
        paths.root.mkdir(parents=True, exist_ok=True)
        lock = job_locks(paths.root, (SKYLIT,), f"step7 {args.check}")
    from nqbot.power import keep_awake

    awake = keep_awake()  # stream-record and tempest-poll run for a long time
    try:
        awake.__enter__()
        if lock is not None:
            lock.__enter__()
        match args.check:
            case "symbols":
                step7.check_symbols(env)
            case "strike-window":
                step7.check_strike_window(env, args.busy_day, args.sample_days,
                                          args.start or step7.STRIKE_WINDOW_DEFAULT_START, approve)
            case "sample-days":
                step7.plan_sample_days(env, args.dates, approve, raised=args.raised)
                result = run_job(SkylitPullJob("skylit_sample_days"), settings, paths,
                                 until=parse_until(args.until, datetime.now(ET)), log=log)
                print(f"skylit_sample_days: {result.status} ({result.done}/{result.total} sessions)")
                if result.status != "complete":
                    print("resume with: uv run nqbot run skylit_sample_days, then re-run this command for the report")
                    return EXIT_REFUSED
                step7.sample_days_report(env, args.dates)
            case "historical-instants":
                step7.check_historical_instants(env, approve)
            case "stream-record":
                step7.check_stream_record(env, approved=approve, force=args.force)
            case "parity":
                step7.check_parity(env, args.session, args.tag, approve)
            case "darkpool":
                step7.check_darkpool(env, args.date, args.check_dates, approve, depth_probe=not args.no_depth_probe)
            case "late-prints-morning":
                step7.check_late_prints_morning(env, approve, force=args.force)
            case "late-prints-compare":
                step7.check_late_prints_compare(env, approve)
            case "tempest-poll":
                step7.check_tempest_poll(env, args.until or step7.RTH_OPEN, approve)
            case "tempest-compare":
                step7.check_tempest_compare(env, approve)
            case "tempest-depth":
                step7.check_tempest_depth(env, approve)
            case "report":
                step7.check_report(env)
    except refusals as e:
        print(f"refused: {e}", file=sys.stderr)
        return EXIT_REFUSED
    finally:
        if lock is not None:
            lock.__exit__(None, None, None)
        awake.__exit__(None, None, None)
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


def _run_databento(args: argparse.Namespace, settings: dict, paths, log) -> int:
    from nqbot.clients.databento_client import DatabentoClient, DatabentoError
    from nqbot.clients.databento_pull import PlanRefused, make_plan
    from nqbot.clients.databento_store import DatabentoStore
    from nqbot.config import load_features
    from nqbot.months import LedgerMissing
    from nqbot.secrets import MissingSecret

    store = DatabentoStore(paths)
    paths.root.mkdir(parents=True, exist_ok=True)
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
                for f in r.flags:
                    print(f"  flag {f['field']} ({f['origin']}): {f['reason']}")
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
