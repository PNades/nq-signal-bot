"""`nqbot test` (owner 2026-10-10): run the full pytest suite, save the whole output on the SSD and end with one line
"PYTEST EXIT CODE: N"; the command returns pytest's own exit code. Every commit runs it first and every report quotes
that line (CLAUDE.md rule 14), so a failure can never hide behind a pipe such as `| tail`. Just above it, the wall time
and the peak memory of the pytest process tree (owner 2026-10-28: compared between the laptop and the PC).

Owner 2026-10-29: `--parallel` uses pytest-xdist with `--dist load` and all cores but 2 as workers. Owner 2026-10-30
(after two full parallel passes on the PC): the default is per machine, `test_parallel` in config/local.yaml (true on
the PC; absent = serial, so the laptop, the live machine from step 21, never starves the live engine of memory by
default); `--parallel` / `--serial` override it (both given: serial);
temporary folders under BASETEMP (C:\\nqbot_pytest, excluded from Defender on each machine; pytest empties it at the
start of every run, so run one `nqbot test` at a time).

Owner 2026-10-30: the run stops at the start, before pytest, with one clear message if `nqbot test` runs elevated
(admin) or the basetemp folder exists but can't be listed and written (an admin run leaves it locked for the
normal user; before this every test errored)."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

import psutil

from nqbot.storage import GB, atomic_write_bytes

REPO = Path(__file__).resolve().parents[2]
EXIT_LINE = "PYTEST EXIT CODE: {code}"
WALL_LINE = "PYTEST WALL TIME: {sec:.0f} s ({hms})"
PEAK_LINE = "PYTEST PEAK MEMORY: {gb:.2f} GB"
SAMPLE_SEC = 0.5  # how often the pytest process tree's memory is read
BASETEMP = r"C:\nqbot_pytest"  # owner 2026-10-29 (name approved); never under a real data root (tests/conftest.py)
PARALLEL_FLAG = "--parallel"
SERIAL_FLAG = "--serial"
SPARE_CORES = 2  # owner 2026-10-29: workers = all cores but 2
NOT_RUN_CODE = 4  # pytest's own usage-error code: the run stopped before any test (never a recorded pass)
PROBE_NAME = ".nqbot_write_probe"
UNLOCK_CMD = (r"takeown /f {p} /r /d y; icacls {p} /reset /t /q; Remove-Item {p} -Recurse -Force; Test-Path {p}")


def workers(cpus: int | None = None) -> int:
    return max(1, (cpus if cpus is not None else os.cpu_count() or 1) - SPARE_CORES)


def pytest_cmd(extra: list[str] | None = None, cpus: int | None = None,
               default_parallel: bool = False) -> tuple[list[str], bool]:
    """The pytest command for `nqbot test [--parallel|--serial] [pytest args]`, and whether it is a full-suite run (no
    pytest arguments; only a full run can record a pass, CLAUDE.md rule 14 (c)). `default_parallel` = local.yaml
    `test_parallel`."""
    extra = extra or []
    rest = [a for a in extra if a not in (PARALLEL_FLAG, SERIAL_FLAG)]
    parallel = SERIAL_FLAG not in extra and (PARALLEL_FLAG in extra or default_parallel)
    par = ["-n", str(workers(cpus)), "--dist", "load"] if parallel else []
    return [sys.executable, "-m", "pytest", *par, "--basetemp", BASETEMP, *rest], not rest


def basetemp_of(cmd: list[str]) -> Path | None:
    """The basetemp pytest will use (the last `--basetemp`), or None."""
    out = None
    for i, a in enumerate(cmd):
        if a == "--basetemp" and i + 1 < len(cmd):
            out = cmd[i + 1]
        elif a.startswith("--basetemp="):
            out = a.split("=", 1)[1]
    return Path(out) if out else None


def is_elevated() -> bool:
    """True when this process runs as admin (Windows) or root."""
    if os.name == "nt":
        import ctypes

        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            return False
    return hasattr(os, "geteuid") and os.geteuid() == 0


def start_problem(cmd: list[str]) -> str | None:
    """Why the run must not start (owner 2026-10-30), or None."""
    if is_elevated():
        return ("nqbot test refused: it is running elevated (admin). Run it from a normal terminal: an admin run "
                "leaves its temporary folders locked for the normal user.")
    base = basetemp_of(cmd)
    if base is None or not base.exists():
        return None
    fix = f"In an admin PowerShell: {UNLOCK_CMD.format(p=base)} (prints False), then run nqbot test normally."
    if not base.is_dir():
        return f"nqbot test refused: the basetemp {base} exists but is not a folder. {fix}"
    try:
        with os.scandir(base) as it:
            next(it, None)
        probe = base / PROBE_NAME
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as e:
        return f"nqbot test refused: the basetemp {base} exists but isn't writable ({e.strerror or e}). {fix}"
    return None


class MemorySampler:
    """Peak memory of a process and its children while it runs: the largest sum over the tree of each process's
    peak working set on Windows (rss elsewhere), read every SAMPLE_SEC."""

    def __init__(self, pid: int):
        self.pid = pid
        self.peak_gb = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def sample(self) -> None:
        try:
            root = psutil.Process(self.pid)
            procs = [root, *root.children(recursive=True)]
        except psutil.Error:
            return
        total = 0
        for p in procs:
            try:
                info = p.memory_info()
            except psutil.Error:
                continue
            total += getattr(info, "peak_wset", None) or info.rss
        self.peak_gb = max(self.peak_gb, total / GB)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.sample()
            self._stop.wait(SAMPLE_SEC)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()


def _hms(sec: float) -> str:
    s = int(round(sec))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _close(f: BinaryIO) -> None:
    try:
        f.close()
    except OSError:
        pass
    return None


def log_path(log_dir: Path, now: datetime) -> Path:
    return log_dir / f"pytest_{now:%Y%m%d-%H%M%S}.txt"


def run_tests(log_dir: Path, extra: list[str] | None = None, *, cmd: list[str] | None = None,
              now: datetime | None = None, out: BinaryIO | None = None,
              default_parallel: bool = False) -> tuple[int, Path]:
    """Run pytest from the repo root, copy its output to the console and to the log as it arrives, append the exit
    line to both. `cmd` replaces the pytest command in tests."""
    cmd = cmd or pytest_cmd(extra, default_parallel=default_parallel)[0]
    out = out or sys.stdout.buffer
    path = log_path(log_dir, now or datetime.now())
    kept = [f"nqbot test {datetime.now():%Y-%m-%d %H:%M:%S}: {' '.join(cmd)}\n".encode()]
    # The log streams to the SSD as pytest runs; the whole output is also kept in memory, so if the SSD drops
    # (owner 2026-10-11) the run carries on and the full log is written at the end through the drop retry.
    f = None
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        f = path.open("wb")
        f.write(kept[0])
    except OSError:
        f = None
    if problem := start_problem(cmd):  # owner 2026-10-30: stop before pytest with one clear message
        line = f"{problem}\n{EXIT_LINE.format(code=NOT_RUN_CODE)} (no test ran)\n".encode()
        if f is not None:
            _close(f)
        atomic_write_bytes(path, kept[0] + line)
        out.write(line)
        out.write(f"full output: {path}\n".encode())
        out.flush()
        return NOT_RUN_CODE, path
    t0 = time.monotonic()
    # PYTHONUTF8 (owner 2026-10-31): pytest's output is UTF-8, not the console code page (a `§` in a failing test's
    # source made the laptop's logs Windows-1252, and nqbot verify flagged them)
    proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env={**os.environ, "PYTHONUTF8": "1"})
    sampler = MemorySampler(proc.pid)
    sampler.start()
    assert proc.stdout is not None
    for chunk in iter(lambda: proc.stdout.read1(4096), b""):
        kept.append(chunk)
        if f is not None:
            try:
                f.write(chunk)
            except OSError:
                f = _close(f)
        out.write(chunk)
        out.flush()
    code = proc.wait()
    sampler.stop()
    wall = time.monotonic() - t0
    # owner 2026-10-28: wall time and peak memory on each machine (laptop vs PC), just above the exit line
    line = (f"\n{WALL_LINE.format(sec=wall, hms=_hms(wall))}\n{PEAK_LINE.format(gb=sampler.peak_gb)}\n"
            + EXIT_LINE.format(code=code) + "\n").encode()
    kept.append(line)
    streamed = False
    if f is not None:
        try:
            f.write(line)
            f.close()
            streamed = True
        except OSError:
            _close(f)
    if not streamed:
        atomic_write_bytes(path, b"".join(kept))
    out.write(line)
    out.write(f"full output: {path}\n".encode())
    out.flush()
    return code, path
