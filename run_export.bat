@echo off
REM Windows one-click launcher for the LearnWorlds Assessment Responses Exporter.
REM Double-click in File Explorer.
REM
REM This script ONLY prepares the environment and runs the Python exporter.
REM It contains NO credentials — all configuration lives in the .env file.

setlocal enableextensions

REM Change into this script's own folder (handles spaces / accented chars).
cd /d "%~dp0"

echo ============================================================
echo  LearnWorlds Assessment Responses Exporter (Windows)
echo  Folder: %CD%
echo ============================================================

REM Find a Python launcher.
where py >nul 2>&1
if %ERRORLEVEL%==0 (
    set "PYTHON=py -3"
) else (
    where python >nul 2>&1
    if %ERRORLEVEL%==0 (
        set "PYTHON=python"
    ) else (
        echo ERROR: Python 3 is not installed. Install it from https://www.python.org/downloads/
        echo Make sure to tick "Add Python to PATH" during installation.
        pause
        exit /b 1
    )
)

REM Warn (do not block) if .env is missing.
if not exist "%~dp0.env" (
    echo WARNING: no .env file found. Copy .env.example to .env first.
    echo          ^(EXPORT_MODE=offline works out of the box.^)
    echo.
)

REM Create the virtual environment on first run.
if not exist "%~dp0.venv" (
    echo Creating virtual environment ^(.venv^)...
    %PYTHON% -m venv "%~dp0.venv"
    if errorlevel 1 (
        echo ERROR: failed to create the virtual environment.
        pause
        exit /b 1
    )
)

REM Activate it.
call "%~dp0.venv\Scripts\activate.bat"

REM Install / update dependencies.
echo Installing/updating dependencies...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo ERROR: failed to install dependencies from requirements.txt.
    pause
    exit /b 1
)

echo.
echo Running exporter...
echo ------------------------------------------------------------
python "%~dp0export_assessment_responses.py"
set "STATUS=%ERRORLEVEL%"
echo ------------------------------------------------------------

if "%STATUS%"=="0" (
    echo Finished successfully. Output files are in the "output" folder.
) else (
    echo Finished with errors ^(exit code %STATUS%^). See the messages above.
)

echo.
pause
endlocal
