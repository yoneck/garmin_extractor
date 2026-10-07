"""Logging helpers: console + optional file logging.

Usage: call ``setup_logging`` once at startup.
"""

from __future__ import annotations

import logging
from logging import Logger
from pathlib import Path


def setup_logging(
    level: str = "INFO",
    log_dir: Path | None = None,
    filename: str = "gar_0.1.log",
) -> Logger:
    """Return a configured logger. Logs to console by default and to file in
    the given directory when ``log_dir`` is provided."""
    root = logging.getLogger("gar")
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))

    root.handlers.clear()
    console = logging.StreamHandler()
    console.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    root.addHandler(console)

    if log_dir is not None:
        log_path = Path(log_dir) / filename
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s %(message)s")
        )
        root.addHandler(file_handler)

    return root
