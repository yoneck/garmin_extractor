"""``python -m gar`` entry point."""

from __future__ import annotations

import sys

from gar.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
