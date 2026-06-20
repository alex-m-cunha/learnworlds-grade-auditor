#!/bin/bash
# Lança a GUI de auditoria LearnWorlds. Duplo-clique para abrir.
cd "$(dirname "$0")"
PYTHON=""
for py in /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3 python3; do
    if "$py" -c "import tkinter" 2>/dev/null; then PYTHON="$py"; break; fi
done
if [ -z "$PYTHON" ]; then
    osascript -e 'display alert "Python com tkinter não encontrado." message "Instala com: brew install python-tk@3.12" as critical'
    exit 1
fi
exec "$PYTHON" run_audit_gui.py
