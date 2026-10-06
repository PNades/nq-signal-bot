"""`nqbot setup-ssd`: step 4b on SSD day (SPEC §12 step 4b, §11 Storage, §7 rule 2b; presd_fixes issue 6).

It finds the SSD (the drive already carrying `.nqbot_drive`, else the one named with --drive: a first setup always
names it, since an internal second partition looks like any other drive letter),
checks it is NTFS and has room, computes the 12-month window (the 12 complete calendar months ending with the last
full month before today, when the step-7 pull starts) and the month-1 merge (fewer than `min_month1_sessions` of
month 1's sessions still inside Skylit's 365-day reach: month 1 = its leftover plus the next calendar month, and the
window ends one month later), and prints the settings.yaml diff for `paths.root` and `months`. Nothing is written
without --yes; with it the marker and the root folder are created and settings.yaml is edited on just those two lines
(comments and everything else untouched, checked by loading it back). `--rewrite-ledger` re-checks the merge when step 10 starts (before its first plan) and
rewrites only the ledger. A session counts as inside the reach if its 09:30 window still is.
"""

from __future__ import annotations

import difflib
import io
import json
import re
import shutil
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

import psutil
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from nqbot import calendar as cal_mod
from nqbot import months as months_mod
from nqbot.storage import GB, MARKER, Paths, atomic_write_text, candidate_drives

DEFAULT_ROOT = "nqbot/"
FILE_SYSTEM = "NTFS"  # §11 Storage: journaled, so an unplug can't corrupt the file system


class SetupRefused(Exception):
    pass


class LedgerStale(months_mod.LedgerMissing):
    """The month-1 merge now applies but the ledger isn't merged: rewrite it before step 10's first plan."""


@dataclass
class Drive:
    root: Path
    fstype: str
    total_gb: float
    free_gb: float

    def line(self) -> str:
        return f"{self.root} ({self.fstype or 'unknown file system'}, {self.total_gb:.0f} GB, {self.free_gb:.0f} GB free)"


def drive_info(root: Path) -> Drive:
    fstype = ""
    for part in psutil.disk_partitions(all=False):
        if Path(part.mountpoint) == root:
            fstype = part.fstype
            break
    usage = shutil.disk_usage(root)
    return Drive(root, fstype, usage.total / GB, usage.free / GB)


def _norm(text: str) -> Path:
    t = text.strip().rstrip("\\/")
    return Path(t + ":\\" if len(t) == 1 else t + "\\" if t.endswith(":") else t)


def choose_drive(drive_arg: str | None, candidates: Callable[[], list[Path]] | None = None) -> Path:
    """The drive carrying the marker (a re-run), else the one named with --drive."""
    drives = (candidates or candidate_drives)()
    marked = [d for d in drives if (d / MARKER).exists()]
    if len(marked) > 1:
        raise SetupRefused(f"several drives carry {MARKER}: {[str(d) for d in marked]}; remove the stray ones")
    if marked:
        if drive_arg and _norm(drive_arg) != marked[0]:
            raise SetupRefused(f"{marked[0]} already carries {MARKER}; --drive {drive_arg} names another drive")
        return marked[0]
    if not drives:
        raise SetupRefused("no external drive found: plug the SSD into a USB-C port and try again")
    if drive_arg:
        want = _norm(drive_arg)
        if want not in drives:
            raise SetupRefused(f"--drive {drive_arg}: not a mounted external drive (found: {[str(d) for d in drives]}; "
                               "the system drive is never used)")
        return want
    # A first setup always names the drive: an internal second partition looks like any other drive letter, and all
    # data must go to the SSD (§11 Storage).
    listing = "\n  ".join(drive_info(d).line() for d in drives)
    raise SetupRefused(f"drives found (the system drive is never used):\n  {listing}\nre-run with the SSD's letter, "
                       "e.g. --drive E: (check its size first)")


def check_drive(info: Drive, min_free_gb: float) -> None:
    if info.fstype.upper() != FILE_SYSTEM:
        raise SetupRefused(f"{info.root} is {info.fstype or 'an unknown file system'}, not {FILE_SYSTEM}: format the SSD "
                           f"as {FILE_SYSTEM} once before use (§11 Storage; this erases it)")
    if info.free_gb < min_free_gb:
        raise SetupRefused(f"{info.root} has {info.free_gb:.0f} GB free, less than min_free_gb ({min_free_gb:g} GB)")


# --- window and ledger ---------------------------------------------------------------------------------------

def window_first_month(today: date) -> str:
    """Month 1 of the 12 complete calendar months ending with the last full month before `today`."""
    return months_mod.ym_add(f"{today.year:04d}-{today.month:02d}", -months_mod.LEDGER_MONTHS)


def month1_sessions(first_ym: str, now: datetime, settings: dict[str, Any], cal: dict[str, Any] | None = None
                    ) -> tuple[int, int]:
    """(sessions of the month whose 09:30 window is still inside Skylit's reach, sessions of the month)."""
    from nqbot.clients.skylit_pull import session_windows, window_reachable

    y, m = (int(x) for x in first_ym.split("-"))
    margin = float(settings["skylit_reach_margin_min"])
    days = (date(y, m, i) for i in range(1, monthrange(y, m)[1] + 1))
    sessions = [d for d in days if cal_mod.is_trading_day(d, cal)]
    in_reach = [d for d in sessions if window_reachable(session_windows(d, cal)[0], now, margin)]
    return len(in_reach), len(sessions)


def build_ledger(first_ym: str, merge: bool) -> dict[int, str | list[str]]:
    """12 ledger months from `first_ym`; with the merge, month 1 holds two calendar months (13 in all)."""
    out: dict[int, str | list[str]] = {1: [first_ym, months_mod.ym_add(first_ym, 1)] if merge else first_ym}
    shift = 1 if merge else 0
    for n in range(2, months_mod.LEDGER_MONTHS + 1):
        out[n] = months_mod.ym_add(first_ym, n - 1 + shift)
    return out


def merge_needed(first_ym: str, now: datetime, settings: dict[str, Any], cal: dict[str, Any] | None = None
                 ) -> tuple[bool, int, int]:
    in_reach, total = month1_sessions(first_ym, now, settings, cal)
    return in_reach < int(settings["min_month1_sessions"]), in_reach, total


def ledger_fixed(paths: Paths) -> bool:
    """The ledger is fixed once step 10 has started, i.e. the Skylit backfill has a plan (§12 step 4b). A Databento
    plan alone doesn't fix it: its window follows the ledger, so `databento plan` is simply run again after a
    rewrite."""
    from nqbot.clients import skylit_backfill

    return skylit_backfill.plan_path(paths).exists()


def check_month1(settings: dict[str, Any], now: datetime, cal: dict[str, Any] | None = None) -> None:
    """Called before step 10's (and step 8's) first plan: refuse if the merge now applies but isn't in the ledger."""
    led = months_mod.ledger(settings)
    if len(led[1]) > 1:
        return  # already merged (once only)
    needed, in_reach, total = merge_needed(led[1][0], now, settings, cal)
    if needed:
        raise LedgerStale(f"only {in_reach} of month 1's {total} sessions ({led[1][0]}) are still inside Skylit's reach "
                          f"(< min_month1_sessions {settings['min_month1_sessions']}): the month-1 merge applies "
                          "(§12 step 4b). Run: uv run nqbot setup-ssd --rewrite-ledger, then --yes after the owner's OK")


# --- settings.yaml -------------------------------------------------------------------------------------------

def _flow(months: dict[int, Any]) -> str:
    def one(v: Any) -> str:
        return "[" + ", ".join(f'"{x}"' for x in v) + "]" if isinstance(v, list) else f'"{v}"'

    return "{" + ", ".join(f"{n}: {one(v)}" for n, v in months.items()) + "}"


def _set_line(text: str, pattern: str, value: str, what: str) -> str:
    """Replace the value on the one line matching `pattern` (groups: key, old value, comment), keeping the comment."""
    lines = text.splitlines(keepends=True)
    hits = [i for i, line in enumerate(lines) if re.match(pattern, line)]
    if len(hits) != 1:
        raise SetupRefused(f"settings.yaml: expected one `{what}` line, found {len(hits)}: edit it by hand")
    line = lines[hits[0]]
    ending = line[len(line.rstrip("\r\n")):]
    m = re.match(pattern, line)
    key, comment = m.group(1), (m.group(3) or "").strip()
    body = f"{key} {value}"
    if comment:  # keep the comment in its column when the new value fits, else one space after it
        column = m.start(3) + len(m.group(3)) - len(m.group(3).lstrip())
        body += " " * max(1, column - len(body)) + comment
    lines[hits[0]] = body + ending
    return "".join(lines)


def render_settings(text: str, *, root: str | None = None, months: dict[int, Any] | None = None) -> str:
    """settings.yaml text with only the paths.root and/or months lines changed (comments and the rest untouched),
    checked by loading it back."""
    new = text
    if root is not None:
        new = _set_line(new, r"^(  root:)\s*(\S*?)(\s+#.*)?\s*$", f'"{root}"', "paths.root")
    if months is not None:
        new = _set_line(new, r"^(months:)\s*(.*?)(\s+#.*)?\s*$", _flow(months), "months")
    try:
        before, after = YAML(typ="safe").load(text), YAML(typ="safe").load(new)
    except YAMLError as e:
        raise SetupRefused(f"settings.yaml: the rewritten file doesn't load ({e}): edit it by hand") from None
    if root is not None:
        before["paths"]["root"] = root
    if months is not None:
        before["months"] = {n: list(v) if isinstance(v, list) else v for n, v in months.items()}
    if before != after:
        raise SetupRefused("settings.yaml: the rewritten file doesn't load back as intended: edit it by hand")
    return new


def diff(old: str, new: str, name: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"{name} (now)", f"{name} (new)"))


# --- the command ---------------------------------------------------------------------------------------------

def setup(settings: dict[str, Any], settings_path: Path, *, now: datetime, drive_arg: str | None = None,
          root: str = DEFAULT_ROOT, yes: bool = False, rewrite_ledger: bool = False,
          echo: Callable[[str], None] = print, cal: dict[str, Any] | None = None,
          candidates: Callable[[], list[Path]] | None = None,
          power: Callable[[], list[str]] | None = None) -> bool:
    """Print what step 4b would write; write it with yes=True. Returns True if settings.yaml was written."""
    if power is None:
        from nqbot.power import power_warnings as power
    current_root = (settings.get("paths") or {}).get("root")
    current_months = settings.get("months")
    root = root.replace("\\", "/")  # `--root nqbot\` (Windows habit): YAML double quotes treat \ as an escape

    drive = choose_drive(drive_arg, candidates)
    try:
        Paths.under(drive, current_root or root)  # a bad --root is refused before anything is written
    except Exception as e:  # SSDNotFound
        raise SetupRefused(str(e)) from None
    info = drive_info(drive)
    echo(f"SSD: {info.line()}")
    check_drive(info, float(settings["min_free_gb"]))
    for line in power():
        echo(line)

    if rewrite_ledger:
        if not current_months or not current_root:
            raise SetupRefused("--rewrite-ledger needs the ledger and paths.root written first (run setup-ssd without it)")
        if ledger_fixed(Paths.under(drive, current_root)):
            raise SetupRefused("step 10 has started (the Skylit backfill has a plan): the ledger is fixed now "
                               "(§12 step 4b)")
        first = months_mod.ledger(settings)[1][0]
        root_to_write = None
    else:
        if current_root and current_root.rstrip("/\\") != root.rstrip("/\\"):
            raise SetupRefused(f"paths.root is already {current_root!r}: changing it would orphan the data under it")
        first = window_first_month(now.date())
        root_to_write = None if current_root else root

    merge, in_reach, total = merge_needed(first, now, settings, cal)
    if merge:
        second, _ = month1_sessions(months_mod.ym_add(first, 1), now, settings, cal)
        if in_reach + second < int(settings["min_month1_sessions"]):
            raise SetupRefused(f"even merged with {months_mod.ym_add(first, 1)}, month 1 has only {in_reach + second} "
                               "sessions inside Skylit's reach: the whole window must move later, which this command "
                               "doesn't do (ask Claude Code; §12 step 4b)")
    led = build_ledger(first, merge)
    echo(f"window: 12 complete months from {first}; month 1 ({first}): {in_reach} of {total} sessions still inside "
         f"Skylit's 365-day reach (min_month1_sessions {settings['min_month1_sessions']}) -> "
         + (f"MERGED with {months_mod.ym_add(first, 1)}; the window ends one month later (the new month 12 is pulled "
            "with the step-18 top-up once complete)" if merge else "no merge"))
    if current_months and months_mod.validate(current_months) == months_mod.validate(led):
        if root_to_write is None:
            echo("settings.yaml already holds this paths.root and ledger: nothing to write")
            _make_drive(drive, current_root, yes, echo)
            return False
        led_to_write = None
    elif current_months and not rewrite_ledger:
        raise SetupRefused("settings.yaml already holds a different ledger; it is rewritten only by --rewrite-ledger "
                           "when step 10 starts (§12 step 4b)")
    else:
        led_to_write = led
    if led_to_write is not None:
        sealed = months_mod.validate(led_to_write)[min(months_mod.SEALED)][0]
        echo(f"ledger: 1-6 development, 7-9 walk-forward test, 10-12 sealed (from {sealed}); archived gameplans dated "
             f"before {sealed} fall in months 1-9 and are held back until step 20 (§5.6)")
        if rewrite_ledger:
            _report_moves(settings, led_to_write, Paths.under(drive, current_root), echo)
            from nqbot.clients.databento_pull import plan_path as databento_plan

            if databento_plan(Paths.under(drive, current_root)).exists():
                echo("then re-run `uv run nqbot databento plan` (its window follows the new ledger)")

    old = settings_path.read_bytes().decode("utf-8")  # as is: a CRLF working copy stays CRLF
    new = render_settings(old, root=root_to_write, months=led_to_write)
    months_mod.validate(YAML(typ="safe").load(new)["months"], full=True)  # what is written must load
    echo(diff(old, new, settings_path.name))
    if not yes:
        echo("nothing written: check the diff with the owner, then re-run with --yes")
        return False
    _make_drive(drive, current_root or root, True, echo)
    atomic_write_text(settings_path, new)
    echo(f"written: {settings_path} (commit it); the marker {drive / MARKER}")
    return True


def _make_drive(drive: Path, root: str, yes: bool, echo: Callable[[str], None]) -> None:
    if not yes:
        return
    marker = drive / MARKER
    if not marker.exists():
        marker.write_text("nqbot SSD marker (SPEC §11): do not delete\n", encoding="utf-8")
    Paths.under(drive, root).root.mkdir(parents=True, exist_ok=True)


def _report_moves(settings: dict[str, Any], new: dict[int, Any], paths: Paths, echo: Callable[[str], None]) -> None:
    """Where the rewrite moves each calendar month, and the step-7 sample days (§7 rule 2b)."""
    from nqbot.clients.skylit_pull import SkylitPullJob

    old_l, new_l = months_mod.validate(settings["months"]), months_mod.validate(new)
    where = lambda led, ym: next((n for n, yms in led.items() if ym in yms), None)  # noqa: E731
    yms = sorted({ym for v in (*old_l.values(), *new_l.values()) for ym in v})
    echo("calendar month: ledger month now -> after the rewrite")
    for ym in yms:
        echo(f"  {ym}: {where(old_l, ym)} -> {where(new_l, ym)}")
    dates_file = SkylitPullJob.plan_path(paths, "skylit_sample_days")
    if dates_file.exists():
        days = json.loads(dates_file.read_text(encoding="utf-8")).get("dates") or []
        echo("step-7 sample days: " + ", ".join(f"{d} -> month {where(new_l, d[:7])}" for d in days))
