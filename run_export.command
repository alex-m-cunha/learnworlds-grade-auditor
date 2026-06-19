#!/bin/bash
# macOS one-click launcher for the LearnWorlds Assessment Responses Exporter.
# Double-click in Finder. If macOS blocks it, run once:  chmod +x run_export.command
#
# This script ONLY prepares the environment and runs the Python exporter.
# It contains NO credentials — all configuration lives in the .env file.

# Resolve this script's own folder robustly (handles spaces / accented chars).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || {
    echo "ERROR: could not change into the script folder."
    echo "Press any key to close..."
    read -n 1 -s
    exit 1
}

echo "============================================================"
echo " LearnWorlds Assessment Responses Exporter (macOS)"
echo " Folder: $SCRIPT_DIR"
echo "============================================================"

# Pick a Python 3 interpreter.
if command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON="python"
else
    echo "ERROR: Python 3 is not installed. Install it from https://www.python.org/downloads/"
    echo "Press any key to close..."
    read -n 1 -s
    exit 1
fi

# Warn (do not block) if .env is missing — the script gives the full message.
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo "WARNING: no .env file found. Copy .env.example to .env first."
    echo "         (EXPORT_MODE=offline works out of the box.)"
    echo
fi

# Create the virtual environment on first run.
if [ ! -d "$SCRIPT_DIR/.venv" ]; then
    echo "Creating virtual environment (.venv)..."
    "$PYTHON" -m venv "$SCRIPT_DIR/.venv" || {
        echo "ERROR: failed to create the virtual environment."
        echo "Press any key to close..."
        read -n 1 -s
        exit 1
    }
fi

# Activate it.
# shellcheck disable=SC1091
source "$SCRIPT_DIR/.venv/bin/activate"

# Install / update dependencies.
echo "Installing/updating dependencies..."
python -m pip install --upgrade pip >/dev/null 2>&1
python -m pip install -r "$SCRIPT_DIR/requirements.txt" || {
    echo "ERROR: failed to install dependencies from requirements.txt."
    echo "Press any key to close..."
    read -n 1 -s
    exit 1
}

echo
echo "Running exporter..."
echo "------------------------------------------------------------"
python "$SCRIPT_DIR/export_assessment_responses.py"
STATUS=$?
echo "------------------------------------------------------------"

if [ $STATUS -eq 0 ]; then
    echo "Finished successfully. Output files are in the 'output' folder."
else
    echo "Finished with errors (exit code $STATUS). See the messages above."
fi

echo
echo "Press any key to close this window..."
read -n 1 -s
