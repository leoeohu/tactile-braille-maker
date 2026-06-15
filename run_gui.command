#!/bin/bash
# Double-click this to launch the GUI with the correct Python (the venv that has the deps).
cd "$(dirname "$0")"
PY="$HOME/gemini-tex/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
exec "$PY" src/gui.py
