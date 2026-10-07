# PowerShell config setup
$root = Split-Path -Parent $PSScriptRoot
$env_file = Join-Path $root ".env"
$example_env = "# Garmin Connect credentials`nGARMIN_EMAIL=`nGARMIN_PASSWORD=`nGARMIN_DATA_DIR=garmin_data`nGARMIN_LOG_DIR=.gar_logs`nGARMIN_SESSION_FILE=garmin_session.json`nGARMIN_CREDENTIAL_FILE=garmin_credentials.json`nGARMIN_MFA_PROMPT=false"

if (-not (Test-Path $env_file)) {
    Set-Content -Path $env_file -Value $example_env -Encoding UTF8
    Write-Host "Created $env_file. Edit it and set your GARMIN_EMAIL and GARMIN_PASSWORD."
} else {
    Write-Host ".env already exists at $env_file"
}

# Also create a simple run_windows.ps1 for easy usage in PowerShell
$run_ps1 = "`$env:PYTHONPATH = '`".$PSScriptRoot`'; python gar/cli/main.py `$args`npause"
Set-Content -Path (Join-Path $root "run_windows.ps1") -Value $run_ps1 -Encoding UTF8
Write-Host "Created run_windows.ps1 for easy PowerShell execution."
