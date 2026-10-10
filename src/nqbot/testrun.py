"""`nqbot test` (owner 2026-10-10): run the full pytest suite, save the whole output on the SSD and end with one line
"PYTEST EXIT CODE: N"; the command returns pytest's own exit code. Every commit runs it first and every report quotes
that line (CLAUDE.md rule 14), so a failure can never hide behind a pipe such as `| tail`."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

REPO = Path(__file__).resolve().parents[2]
EXIT_LINE = "PYTEST EXIT CODE: {code}"


def log_path(log_dir: Path, now: datetime) -> Path:
    return log_dir / f"pytest_{now:%Y%m%d-%H%M%S}.txt"


def run_tests(log_dir: Path, extra: list[str] | None = None, *, cmd: list[str] | None = None,
              now: datetime | None = None, out: BinaryIO | None = None) -> tuple[int, Path]:
    """Run pytest from the repo root, copy its output to the console and to the log as it arrives, append the exit
    line to both. `cmd` replaces the pytest command in tests."""
    cmd = cmd or [sys.executable, "-m", "pytest", *(extra or [])]
    out = out or sys.stdout.buffer
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_path(log_dir, now or datetime.now())
    with path.open("wb") as f:
        f.write(f"nqbot test {datetime.now():%Y-%m-%d %H:%M:%S}: {' '.join(cmd)}\n".encode())
        proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        assert proc.stdout is not None
        for chunk in iter(lambda: proc.stdout.read1(4096), b""):
            f.write(chunk)
            out.write(chunk)
            out.flush()
        code = proc.wait()
        line = ("\n" + EXIT_LINE.format(code=code) + "\n").encode()
        f.write(line)
    out.write(line)
    out.write(f"full output: {path}\n".encode())
    out.flush()
    return code, path
