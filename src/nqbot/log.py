"""Logging: console always; a daily file in paths.log_dir only when the SSD is found.

Nothing is logged to the laptop's internal drive (§11 Storage). Timestamps are UTC (§11).
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

FORMAT = "%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s: %(message)s"
DATEFMT = "%Y-%m-%dT%H:%M:%S"


def setup_logging(log_dir: Path | None = None, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("nqbot")
    logger.setLevel(level)
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    formatter = logging.Formatter(FORMAT, DATEFMT)
    formatter.converter = time.gmtime
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    logger.addHandler(console)
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        file = logging.FileHandler(log_dir / f"nqbot_{day}.log", encoding="utf-8")
        file.setFormatter(formatter)
        logger.addHandler(file)
    logger.propagate = False
    return logger
