# PowerShell config setup
$root = Split-Path -Parent $PSScriptRoot
$env_file = Join-Path $root ".env"

Write-Host "--- Garmin Extractor Windows Setup ---" -ForegroundColor Cyan

# 1. Check for Python
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Error: Python was not found. Please install Python from python.org or Microsoft Store before running this script." -ForegroundColor Red
    exit 1
}

# 2. Interactive Credentials
if ((Test-Path $env_file) -and ($args -notcontains "--force")) {
    $confirm = Read-Host "An .env file already exists. Keep it? (y/n)"
    if ($confirm -ne "y") {
        Write-Host "Proceeding with new credentials setup..." -ForegroundColor Yellow
    }
} else {
    $gar_email = Read-Host "Enter Garmin Connect Email"
    $gar_password = Read-Host "Enter Garmin Connect Password" -AsSecureString
    
    # Convert secure string to plain text for the .env file
    $BSTR = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($gar_password)
    $plain_password = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR)

    $example_env = @"
# Garmin Connect credentials
GARMIN_EMAIL=$gar_email
GARMIN_PASSWORD=$plain_password
GARMIN_DATA_DIR=garmin_data
GARMIN_LOG_DIR=.gar_logs
GARMIN_SESSION_FILE=garmin_session.json
GARMIN_CREDENTIAL_FILE=garmin_credentials.json
GARMIN_MFA_PROMPT=false
"@
    Set-Content -Path $env_file -Value $example_env -Encoding UTF8
    Write-Host "Created $env_file with new credentials." -ForegroundColor Green
}

# 3. Setup Virtual Environment and Dependencies
if (-not (Test-Path (Join-Path $root "venv"))) {
    Write-Host "Creating virtual environment in venv/..." -ForegroundColor Cyan
    python -m venv venv
}

Write-Host "Installing/Updating dependencies from requirements.txt..." -ForegroundColor Cyan
& "$root\venv\Scripts\python.exe" -m pip install --upgrade pip
& "$root\venv\Scripts\pip.exe" install -r "$root\requirements.txt"

# 4. Create easy PowerShell runner
$run_ps1 = "`$env:PYTHONPATH = '`".$PSScriptRoot.TrimEnd('\').TrimEnd('/')`'; & `".\venv\Scripts\python.exe`" gar/cli/main.py `$args`npause"
Set-Content -Path (Join-Path $root "run_windows.ps1") -Value $run_ps1 -Encoding UTF8
Write-Host "Created run_windows.ps1 for easy PowerShell execution." -ForegroundColor Green

Write-Host "-------------------------------------------------------" -ForegroundColor Cyan
Write-Host "Setup Complete!" -ForegroundColor Green
Write-Host "To run the extractor, use: ./run_windows.bat (CMD) or ./run_windows.ps1 (PS)" -ForegroundColor Green
Write-Host "-------------------------------------------------------" -ForegroundColor Cyan


