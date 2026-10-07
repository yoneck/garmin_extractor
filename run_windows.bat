@echo off
setlocal
set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"

:: Set PYTHONPATH to include the current directory
set "PYTHONPATH=%ROOT_DIR%;%PYTHONPATH%"
set "PYTHONUTF8=1"

:: Check if venv exists and activate it
if exist "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
)

:: Run the extractor with all passed arguments
python gar/cli/main.py %*

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Extraction failed with error code %errorlevel%.
    pause
)
pause
