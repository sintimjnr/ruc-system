@echo off
setlocal

set "RUC_HOME=%~dp0"
cd /d "%RUC_HOME%"

if not exist "%RUC_HOME%.venv\Scripts\python.exe" (
    echo RUC startup failed: the local virtual environment was not found.
    echo Expected: %RUC_HOME%.venv\Scripts\python.exe
    exit /b 1
)

if not exist "%RUC_HOME%.env" (
    echo RUC startup failed: the local .env configuration file was not found.
    echo Create it from .env.example and enter local values before starting RUC.
    exit /b 1
)

echo Starting RUC System production server...
"%RUC_HOME%.venv\Scripts\python.exe" "%RUC_HOME%run_production.py"
set "RUC_EXIT_CODE=%ERRORLEVEL%"

if not "%RUC_EXIT_CODE%"=="0" (
    echo RUC production server stopped with exit code %RUC_EXIT_CODE%.
)

exit /b %RUC_EXIT_CODE%
