@echo off
setlocal

set "RUC_HOME=%~dp0"
cd /d "%RUC_HOME%"

if exist "%RUC_HOME%.venv\Scripts\python.exe" (
    set "RUC_PYTHON=%RUC_HOME%.venv\Scripts\python.exe"
) else (
    set "RUC_PYTHON=python"
)

echo Running RUC regression tests...
"%RUC_PYTHON%" -m unittest discover -s tests -p "test_*.py" -v
set "RUC_TEST_EXIT_CODE=%ERRORLEVEL%"

if "%RUC_TEST_EXIT_CODE%"=="0" (
    echo RUC regression tests PASSED.
) else (
    echo RUC regression tests FAILED with exit code %RUC_TEST_EXIT_CODE%.
)

exit /b %RUC_TEST_EXIT_CODE%
