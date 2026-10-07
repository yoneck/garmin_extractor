# PowerShell config setup
$root = Split-Path -Parent $PSScriptRoot
Write-Host "Created $root\.env. Edit it and set your GARMIN_EMAIL and GARMIN_PASSWORD."
$example = "# Garmin Connect credentials`nGARMIN_EMAIL=`nGARMIN_PASSWORD=`nGARMIN_DATA_DIR=garmin_data`nGARMIN_LOG_DIR=.gar_logs`nGARMIN_SESSION_FILE=garmin_session.json`nGARMIN_CREDENTIAL_FILE=garmin_credentials.json`nGARMIN_MFA_PROMPT=false"
Set-Content -Path "$root\.env" -Value $example -Encoding UTF8
$access = (Get-Acl "$root\.env").Access
$access | ForEach-Object { $_.AccessControlType }
