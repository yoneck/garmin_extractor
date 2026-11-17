"""Migrate existing CSV extracts into the DuckDB lake.

Thin wrapper around ``gar.lake`` so the legacy command shape from the plan
(``gar migrate csv-to-lake``) keeps working.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from gar.lake.cli import main as lake_main


def main(argv: list[str] | None = None, data_dir: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gar migrate csv-to-lake")
    parser.add_argument("--data-dir", help="Override GARMIN_DATA_DIR.")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--export", action="store_true")
    args = parser.parse_args(argv)
    if args.data_dir:
        data_dir = Path(args.data_dir)
    lake_args = ["build"]
    if args.validate:
        lake_args.append("--validate")
    if args.export:
        lake_args.append("--export")
    return lake_main(lake_args, data_dir=data_dir)