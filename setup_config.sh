#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "--- Garmin Extractor Setup ---"

# 1. Check for Python
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 is not installed. Please install Python 3.x before running this script."
    exit 1
fi

# 2. Interactive Credentials
if [ -f ".env" ] && [ "$1" != "--force" ]; then
    read -p "Keep existing .env? (y/n): " confirm
    if [[ $confirm == [yY] || $confirm == [yY][eE] ]]; then
        echo ".env already exists. Skipping credentials setup."
    else
        echo "Proceeding with new credentials setup..."
    fi
else
    read -p "Enter Garmin Connect Email: " gar_email
    read -s -p "Enter Garmin Connect Password: " gar_password
    echo "" # New line after password input

    cat > ".env" << ENV
GARMIN_EMAIL=$gar_email
GARMIN_PASSWORD=$gar_password
GARMIN_DATA_DIR=garmin_data
GARMIN_LOG_DIR=.gar_logs
GARMIN_SESSION_FILE=garmin_session.json
GARMIN_CREDENTIAL_FILE=garmin_credentials.json
GARMIN_MFA_PROMPT=false
ENV
    chmod 600 ".env"
    echo "Created .env with new credentials."
fi

# 3. Setup Virtual Environment and Dependencies
if [ ! -d "venv" ]; then
    echo "Creating virtual environment in venv/..."
    python3 -m venv venv
fi

echo "Installing/Updating dependencies from requirements.txt..."
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

echo "-------------------------------------------------------"
echo "Setup Complete!"
echo "To run the extractor, use: ./run.sh"
echo "-------------------------------------------------------"
