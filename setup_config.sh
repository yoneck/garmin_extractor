#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$ROOT_DIR/.env" ]; then
  echo ".env already exists at $ROOT_DIR/.env"
  if [ -t 0 ]; then
    if [ "$1" != "--force" ]; then
      exit 0
    fi
  fi
  cp "$ROOT_DIR/.env" "$ROOT_DIR/.env.example"
fi

cat > "$ROOT_DIR/.example.env" << 'ENV'
# Garmin Connect credentials
GARMIN_EMAIL=
GARMIN_PASSWORD=

# Storage locations (relative to project path)
GARMIN_DATA_DIR=garmin_data
GARMIN_LOG_DIR=.gar_logs

# Session / token storage (kept private, git-ignored)
GARMIN_SESSION_FILE=garmin_session.json
GARMIN_CREDENTIAL_FILE=garmin_credentials.json

# MFA
GARMIN_MFA_PROMPT=false
ENV

chmod 600 "$ROOT_DIR/.example.env" 2>/dev/null || true
cp "$ROOT_DIR/.example.env" "$ROOT_DIR/.env"
chmod 600 "$ROOT_DIR/.env" 2>/dev/null || true
echo "Created $ROOT_DIR/.env. Edit it and set your GARMIN_EMAIL and GARMIN_PASSWORD."
