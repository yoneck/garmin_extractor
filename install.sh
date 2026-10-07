#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
PY="$(command -v python3 || command -v python)"

echo "Using: $PY"
$PY -m pip install --user -r " $ROOT_DIR/requirements.txt" 2>/dev/null || \
    $PY -m venv "$VENV_DIR" && "$VENV_DIR/bin/pip" install -r "$ROOT_DIR/requirements.txt"

echo "Run the extractor with:  cd $ROOT_DIR && python -m gar.cli"
echo "Generate config files with:  bash setup_config.sh"
