"""Garmin Connect data extractor CLI entry point.

Thin launcher so both ``python -m gar`` and ``python gar-cli`` work.
"""

import sys

from gar.cli.main import main as _main


def main() -> int:
    return _main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
