"""`nqbot backup`: copy the Skylit history to the owner's Google Drive with rclone and verify it (SPEC §11 Storage).

Skylit history older than 365 days can never be pulled again, and a copy on the same SSD is not a backup. So
`data/raw/skylit/` (the range windows, the dark-pool prints, Tempest, the call ledger) is copied straight from
the SSD to `<backup.rclone_remote>:<backup.drive_folder>/raw/skylit/` with `rclone copy --checksum`, then
verified with `rclone check --one-way` (MD5 on both sides). Run it twice: once the warm-up and months 1-6 are
pulled, and again when step 10 finishes (§11); any later run copies only what changed.

It is a resumable job (`nqbot backup --until 07:30` = `nqbot run skylit_backup`): one chunk per folder (one per
month under `range/`), each chunk id carrying a fingerprint of that folder's files, so an unchanged folder is
skipped and a folder that gained files is copied and checked again. It holds the Skylit job lock (never while the
Skylit backfill writes; the Databento backfill may run alongside).

Owner, once: install rclone and run `rclone config` to authorise the Google Drive remote (the OAuth token stays in
rclone's own config, outside the repo).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from nqbot.jobs.lock import SKYLIT
from nqbot.jobs.runner import Ctx, StopChunk
from nqbot.storage import atomic_write_json, atomic_write_text

JOB_NAME = "skylit_backup"
ROOT_FILES = "_files"  # the chunk for the files directly in raw/skylit/ (the call ledger)
RANGE = "range"  # raw/skylit/range/<YYYY-MM-DD>/: one chunk per month
LAST_PARTS = (ROOT_FILES, "vol", "stream")  # written by checks that don't take the Skylit lock (tempest-poll, report)
TEMP_FILTER = "- .*.tmp"  # atomic-write temp files (storage.atomic_path) are never copied
PREFLIGHT_TIMEOUT_SEC = 60
POLL_SEC = 1.0  # how often a running rclone is checked against ctx.should_stop()
TERMINATE_WAIT_SEC = 30
LOG_TAIL_LINES = 20


class BackupRefused(Exception):
    pass


class Rclone(Protocol):
    def quick(self, args: list[str]) -> tuple[int, str]: ...

    def run(self, args: list[str], should_stop: Callable[[], str | None]) -> int | None: ...


@dataclass
class SubprocessRclone:
    """The real rclone (tests inject a fake with the same two methods)."""

    exe: str = "rclone"

    def quick(self, args: list[str]) -> tuple[int, str]:
        """A short command (version, listremotes): exit code and output. Not installed -> (-1, reason)."""
        try:
            done = subprocess.run([self.exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=PREFLIGHT_TIMEOUT_SEC, check=False)  # rclone writes UTF-8, not cp1252
        except (OSError, subprocess.SubprocessError) as e:
            return -1, str(e)
        return done.returncode, done.stdout + done.stderr

    def run(self, args: list[str], should_stop: Callable[[], str | None]) -> int | None:
        """A long copy or check (its output goes to --log-file). None = stopped early (rclone terminated)."""
        proc = subprocess.Popen([self.exe, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        while proc.poll() is None:
            if should_stop():
                proc.terminate()
                try:
                    proc.wait(TERMINATE_WAIT_SEC)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                return None
            time.sleep(POLL_SEC)
        return proc.returncode


def settings_of(settings: dict[str, Any]) -> dict[str, str]:
    b = settings["backup"]
    return {"remote": b["rclone_remote"], "folder": b["drive_folder"], "exe": b["rclone_exe"]}


def preflight(rclone: Rclone, remote: str) -> str:
    """rclone installed and the remote configured; returns the rclone version line."""
    code, out = rclone.quick(["version"])
    if code != 0:
        raise BackupRefused(f"rclone isn't installed or doesn't run ({out.strip()[:200]}). Owner, once: install "
                            "rclone (https://rclone.org/install/) so `rclone version` works in this terminal")
    code, remotes = rclone.quick(["listremotes"])
    if code != 0 or f"{remote}:" not in remotes.split():
        raise BackupRefused(f"rclone has no remote named {remote!r} (has: {remotes.split() or 'none'}). Owner, once: "
                            f"`rclone config` -> new remote {remote!r}, type drive, authorise Google Drive in the "
                            "browser (backup.rclone_remote in settings.yaml)")
    return out.strip().splitlines()[0] if out.strip() else ""


@dataclass(frozen=True)
class Part:
    """One chunk: a folder of raw/skylit/ (or one month of range/, or the root files)."""

    name: str  # "_files", "darkpool", "range/2025-10", ...
    src: Path  # the folder rclone copies from
    rel: str  # its path under raw/skylit/ (remote side)
    filters: tuple[str, ...]  # rclone --filter rules, in order

    def files(self) -> list[Path]:
        if self.name == ROOT_FILES:
            return sorted(p for p in self.src.iterdir() if p.is_file() and not _temp(p))
        month = self.name.split("/", 1)[1] if self.name.startswith(f"{RANGE}/") else None
        return sorted(p for p in self.src.rglob("*") if p.is_file() and not _temp(p)
                      and (month is None or p.relative_to(self.src).parts[0].startswith(month)))


def _temp(p: Path) -> bool:
    return p.name.startswith(".") and p.name.endswith(".tmp")


def parts(skylit: Path) -> list[Part]:
    """range/ months first (the history that can never be pulled again), then the other folders; last the folders a
    running check may still be writing (LAST_PARTS: the call ledger, Tempest), so a file changing under rclone
    stops the run only after everything else is copied."""
    out: list[Part] = []
    for d in sorted(p for p in skylit.iterdir() if p.is_dir()):
        if d.name == RANGE:
            months = sorted({c.name[:7] for c in d.iterdir() if c.is_dir()})
            out += [Part(f"{RANGE}/{m}", d, RANGE, (TEMP_FILTER, f"+ /{m}-*/**", "- *")) for m in months]
        else:
            out.append(Part(d.name, d, d.name, (TEMP_FILTER,)))
    if any(p.is_file() and not _temp(p) for p in skylit.iterdir()):
        out.append(Part(ROOT_FILES, skylit, "", (TEMP_FILTER, "+ /*", "- *")))
    return sorted(out, key=lambda p: (p.name in LAST_PARTS, not p.name.startswith(RANGE)))


def fingerprint(part: Part) -> str:
    h = hashlib.sha256()
    for p in part.files():
        st = p.stat()
        h.update(f"{p.relative_to(part.src).as_posix()}|{st.st_size}|{st.st_mtime_ns}\n".encode())
    return h.hexdigest()[:12]


def remote_path(cfg: dict[str, str], rel: str) -> str:
    return f"{cfg['remote']}:{cfg['folder']}/raw/skylit" + (f"/{rel}" if rel else "")


def rclone_args(verb: str, part: Part, dst: str, log_file: Path) -> list[str]:
    args = [verb, str(part.src), dst]
    for f in part.filters:
        args += ["--filter", f]
    if verb == "copy":
        args += ["--checksum"]  # compare size + MD5, not modification times
    else:
        args += ["--one-way"]  # every SSD file is on Drive with the same MD5 (Drive may hold older extras)
    return args + ["--log-file", str(log_file), "--log-level", "INFO"]


def _tail(path: Path) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-LOG_TAIL_LINES:])
    except OSError:
        return ""


class SkylitBackupJob:
    name = JOB_NAME
    locks = (SKYLIT,)

    def __init__(self, rclone_factory: Callable[[str], Rclone] = SubprocessRclone):
        self.rclone_factory = rclone_factory
        self._rclone: Rclone | None = None
        self._parts: dict[str, Part] = {}

    def _skylit(self, ctx: Ctx) -> Path:
        return ctx.paths.raw_dir / "skylit"

    def chunks(self, ctx: Ctx) -> list[str]:
        cfg = settings_of(ctx.settings)
        skylit = self._skylit(ctx)
        if not skylit.is_dir() or not any(skylit.iterdir()):
            raise BackupRefused(f"nothing to back up: {skylit} is empty (Skylit history comes with steps 7 and 10)")
        self._rclone = self.rclone_factory(cfg["exe"])
        version = preflight(self._rclone, cfg["remote"])
        ctx.log.info("backup: %s -> %s (%s)", skylit, remote_path(cfg, ""), version)
        self._parts = {f"{p.name}@{fingerprint(p)}": p for p in parts(skylit)}
        return list(self._parts)

    def run_chunk(self, chunk_id: str, ctx: Ctx) -> None:
        cfg, part = settings_of(ctx.settings), self._parts[chunk_id]
        assert self._rclone is not None, "chunks() runs first"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        log_file = ctx.paths.log_dir / f"rclone_{stamp}_{part.name.replace('/', '_')}.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        dst = remote_path(cfg, part.rel)
        files = part.files()
        record: dict[str, Any] = {"part": part.name, "chunk": chunk_id, "remote": dst, "files": len(files),
                                  "bytes": sum(p.stat().st_size for p in files), "log": str(log_file)}
        for verb in ("copy", "check"):
            code = self._rclone.run(rclone_args(verb, part, dst, log_file), ctx.should_stop)
            if code is None:
                raise StopChunk(f"rclone {verb} of {part.name} stopped early ({ctx.should_stop()})")
            if code != 0 and not ctx.paths.mounted():  # the SSD went away under rclone: the runner's clean stop
                raise StopChunk(f"SSD unplugged during rclone {verb} of {part.name}")
            if code != 0:
                record.update({"status": f"{verb}_failed", "exit_code": code, "checked_at": None})
                self._save(ctx, record)
                ctx.log.error("backup: rclone %s %s failed (exit %s):\n%s", verb, part.name, code, _tail(log_file))
                what = "differ from or are missing on Drive" if verb == "check" else "couldn't all be copied"
                raise StopChunk(f"files of {part.name} {what} (rclone {verb} exit {code}; see {log_file}): "
                                "re-run nqbot backup; ask Claude Code if it repeats", status="backup_failed")
        record.update({"status": "verified", "checked_at": datetime.now(timezone.utc).isoformat()})
        self._save(ctx, record)

    def _save(self, ctx: Ctx, record: dict[str, Any]) -> None:
        atomic_write_json(ctx.out_dir / "parts" / f"{record['part'].replace('/', '_')}.json", record)
        write_report(ctx, settings_of(ctx.settings))


def write_report(ctx: Ctx, cfg: dict[str, str]) -> Path:
    """<reports_dir>/backup/backup.md: the latest result per folder (rewritten after every chunk)."""
    records = [json.loads(p.read_text(encoding="utf-8"))
               for p in sorted((ctx.out_dir / "parts").glob("*.json"))]
    gb = sum(r["bytes"] for r in records) / 1024**3
    lines = ["# Skylit backup (SPEC §11 Storage)", "",
             f"- Source: `{ctx.paths.raw_dir / 'skylit'}`",
             f"- Google Drive: `{remote_path(cfg, '')}` (rclone copy --checksum, then rclone check --one-way)",
             f"- Folders: {len(records)}, {sum(r['files'] for r in records):,} files, {gb:.2f} GB; "
             f"verified: {sum(r['status'] == 'verified' for r in records)}", "",
             "| Folder | Files | MB | Status | Checked (UTC) |", "|---|---:|---:|---|---|"]
    lines += [f"| {r['part']} | {r['files']:,} | {r['bytes'] / 1024**2:,.1f} | {r['status']} | {r['checked_at'] or '-'} |"
              for r in records]
    path = ctx.paths.reports_dir / "backup" / "backup.md"
    atomic_write_text(path, "\n".join(lines) + "\n")
    return path
