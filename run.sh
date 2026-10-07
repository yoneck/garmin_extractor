#!/usr/bin/env bash
#
# One-command launcher for the gar extractor.
# Sets up PYTHONPATH (so `from gar...` imports work) and forwards the arguments.
#
# Usage:
#   ./run.sh [--days N] [--force] [--activity-depth] [--no-session]
#
# Example:   ./run.sh --days 30
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ -f venv/bin/activate ]]; then
  source venv/bin/activate
fi

export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUTF8=1

exec python gar/cli/main.py "$@"
