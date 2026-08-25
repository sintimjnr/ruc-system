@echo off
setlocal

set "RUC_HOME=%~dp0"
cd /d "%RUC_HOME%"

if not exist "%RUC_HOME%.venv\Scripts\python.exe" (
    echo RUC restore test failed: the local virtual environment was not found.
    echo Expected: %RUC_HOME%.venv\Scripts\python.exe
    exit /b 1
)

if not exist "%RUC_HOME%restore_test_ruc.py" (
    echo RUC restore test failed: restore_test_ruc.py was not found.
    exit /b 1
)

echo Starting RUC recovery test...
"%RUC_HOME%.venv\Scripts\python.exe" "%RUC_HOME%restore_test_ruc.py" %*
set "RUC_RESTORE_EXIT_CODE=%ERRORLEVEL%"

if "%RUC_RESTORE_EXIT_CODE%"=="0" (
    echo RUC recovery test completed successfully.
) else if "%RUC_RESTORE_EXIT_CODE%"=="2" (
    echo RUC recovery test completed with a safe database-restore block.
    echo Configure approved PostgreSQL restore credentials to test temporary database restore.
) else (
    echo RUC recovery test failed with exit code %RUC_RESTORE_EXIT_CODE%.
)

exit /b %RUC_RESTORE_EXIT_CODE%
