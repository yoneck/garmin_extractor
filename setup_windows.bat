@echo off
setlocal enabledelayedexpansion

echo --- Garmin Extractor Windows Setup (CMD) ---
echo.

:: 1. Check for Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: Python was not found in PATH. Please install Python from python.org first.
    pause
    exit /b 1
)

:: 2. Interactive Credentials
if exist .env (
    set /p "choice=An .env file already exists. Keep it? (y/n): "
    if /I "!choice!" neq "y" (
        echo Proceeding with new credentials setup...
    ) else (
        echo .env already exists. Skipping credentials setup.
        goto :setup_venv
    )
)

set /p "gar_email=Enter Garmin Connect Email: "
set /p "gar_password=Enter Garmin Connect Password: "

(
echo # Garmin Connect credentials
echo GARMIN_EMAIL=%gar_email%
echo GARMIN_PASSWORD=%gar_password%
echo GARMIN_DATA_DIR=garmin_data
echo GARMIN_LOG_DIR=.gar_logs
echo GARMIN_SESSION_FILE=garmin_session.json
echo GARMIN_CREDENTIAL_FILE=garmin_credentials.json
echo GARMIN_MFA_PROMPT=false
) > .env

echo Created .env with new credentials.

:setup_venv
:: 3. Setup Virtual Environment and Dependencies
if not exist venv (
    echo Creating virtual environment in venv/...
    python -m venv venv
)

echo Installing/Updating dependencies from requirements.txt...
call venv\Scripts\python.exe -m pip install --upgrade pip
call venv\Scripts\pip install -r requirements.txt

echo -------------------------------------------------------
echo Setup Complete!
echo To run the extractor, use: run_windows.bat (CMD) or run_windows.ps1 (PS)
echo -------------------------------------------------------
pause
