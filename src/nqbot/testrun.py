"""`nqbot test` (owner 2026-10-10): run the full pytest suite, save the whole output on the SSD and end with one line
"PYTEST EXIT CODE: N"; the command returns pytest's own exit code. Every commit runs it first and every report quotes
that line (CLAUDE.md rule 14), so a failure can never hide behind a pipe such as `| tail`. Just above it, the wall time
and the peak memory of the pytest process tree (owner 2026-10-28: compared between the laptop and the PC).

Owner 2026-10-29: serial by default; `--parallel` uses pytest-xdist with `--dist load` and all cores but 2 as
workers (opt-in until two full parallel runs pass on the PC),
temporary folders under BASETEMP (C:\\nqbot_pytest, excluded from Defender on each machine; pytest empties it at the
start of every run, so run one `nqbot test` at a time)."""

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
SPARE_CORES = 2  # owner 2026-10-29: workers = all cores but 2


def workers(cpus: int | None = None) -> int:
    return max(1, (cpus if cpus is not None else os.cpu_count() or 1) - SPARE_CORES)


def pytest_cmd(extra: list[str] | None = None, cpus: int | None = None) -> tuple[list[str], bool]:
    """The pytest command for `nqbot test [--parallel] [pytest args]`, and whether it is a full-suite run (no pytest
    arguments; only a full run can record a pass, CLAUDE.md rule 14 (c))."""
    rest = [a for a in (extra or []) if a != PARALLEL_FLAG]
    parallel = len(rest) != len(extra or [])
    par = ["-n", str(workers(cpus)), "--dist", "load"] if parallel else []
    return [sys.executable, "-m", "pytest", *par, "--basetemp", BASETEMP, *rest], not rest


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
              now: datetime | None = None, out: BinaryIO | None = None) -> tuple[int, Path]:
    """Run pytest from the repo root, copy its output to the console and to the log as it arrives, append the exit
    line to both. `cmd` replaces the pytest command in tests."""
    cmd = cmd or pytest_cmd(extra)[0]
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
    t0 = time.monotonic()
    proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
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
