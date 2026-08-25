@echo off
setlocal

set "RUC_HOME=%~dp0"
cd /d "%RUC_HOME%"

if not exist "%RUC_HOME%.venv\Scripts\python.exe" (
    echo RUC backup failed: the local virtual environment was not found.
    echo Expected: %RUC_HOME%.venv\Scripts\python.exe
    exit /b 1
)

if not exist "%RUC_HOME%backup_ruc.py" (
    echo RUC backup failed: backup_ruc.py was not found.
    exit /b 1
)

echo Starting RUC disaster-recovery backup...
"%RUC_HOME%.venv\Scripts\python.exe" "%RUC_HOME%backup_ruc.py" %*
set "RUC_BACKUP_EXIT_CODE=%ERRORLEVEL%"

if "%RUC_BACKUP_EXIT_CODE%"=="0" (
    echo RUC disaster-recovery backup completed successfully.
) else (
    echo RUC disaster-recovery backup failed with exit code %RUC_BACKUP_EXIT_CODE%.
)

exit /b %RUC_BACKUP_EXIT_CODE%
