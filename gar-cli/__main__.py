"""Garmin Connect data extractor CLI entry point.

Thin launcher so both ``python -m gar`` and ``python gar-cli`` work.
"""

import sys

import gar.main as m


def main() -> int:
    return m.main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
