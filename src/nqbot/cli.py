"""The `nqbot` command (run as `uv run nqbot ...`). Later steps add their commands here."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from nqbot.config import ConfigError, load_settings
from nqbot.jobs.lock import LOCK_NAME, LockHeld, force_unlock, read_lock
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
    unlock = sub.add_parser("unlock", help="remove the job lock left on the SSD by a job that is no longer running")
    unlock.add_argument("--yes", action="store_true", help="confirm that job is no longer running anywhere")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_REFUSED
    try:
        paths = resolve_paths(settings)
    except SSDNotFound as e:
        print(f"SSD not found: {e}", file=sys.stderr)
        return EXIT_REFUSED

    if args.command == "status":
        print("\n".join(status_lines(paths)))
        return 0

    if args.command == "unlock":
        lock = paths.root / LOCK_NAME
        holder = read_lock(lock)
        if not lock.exists():
            print("no job lock on the SSD")
            return 0
        print(f"job lock held by: {holder or 'unreadable lock file'}")
        if not args.yes:
            print("Check that job is not running on any machine, then run: uv run nqbot unlock --yes")
            return EXIT_LOCKED
        force_unlock(lock)
        print("job lock removed; the job resumes from its last finished chunk on the next nqbot run")
        return 0

    log = setup_logging(paths.log_dir)
    job = get_job(args.job)
    until = parse_until(args.until, datetime.now(ET))
    try:
        result = run_job(job, settings, paths, until=until, log=log)
    except LockHeld as e:
        log.error("%s", e)
        return EXIT_LOCKED
    message = f" - {result.message}" if result.message else ""
    print(f"{job.name}: {result.status} ({result.done}/{result.total} chunks done){message}")
    return 0 if result.status in ("complete", "until") else EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
