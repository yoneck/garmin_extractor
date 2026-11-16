"""``gar context`` subcommand: build layered LLM context from the lake."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gar.context.builder import build_context, write_data_dictionary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gar context", description="Build LLM context from the lake.")
    parser.add_argument("--days", type=int, default=28)
    parser.add_argument("--token-budget", type=int, default=12000)
    parser.add_argument("--out", help="Output directory (default: <data>/context).")
    parser.add_argument("--data-dir", help="Override GARMIN_DATA_DIR.")
    parser.add_argument("--json", dest="as_json", action="store_true")
    return parser


def main(argv: list[str] | None = None, data_dir: Path | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.data_dir:
        data_dir = Path(args.data_dir)
    if data_dir is None:
        import os

        data_dir = Path(os.environ.get("GARMIN_DATA_DIR") or Path("garmin_data"))
    data_dir = Path(data_dir)
    out_dir = Path(args.out) if args.out else None
    package = build_context(data_dir, days=args.days, token_budget=args.token_budget, out_dir=out_dir)
    write_data_dictionary(out_dir or data_dir / "context")
    if args.as_json:
        print(
            json.dumps(
                {
                    "out_dir": str(out_dir or data_dir / "context"),
                    "estimated_tokens": package.total_tokens(),
                    "sections": [s.name for s in package.sections],
                },
                indent=2,
            )
        )
    else:
        print(
            f"context written to {out_dir or data_dir / 'context'} "
            f"({package.total_tokens()} estimated tokens, {len(package.sections)} sections)"
        )
    return 0