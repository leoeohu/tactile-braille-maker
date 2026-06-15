"""Resolve a Python interpreter that actually has the project's dependencies.

The GUI may be launched with a bare system Python (e.g. by double-clicking or the
wrong `python3`). Subprocesses (tactile.py, batch.py, pdf_*.py) still need PIL / numpy /
PyMuPDF, so we pick an interpreter that can import them instead of blindly using
sys.executable.

Order: $TACTILE_PYTHON, the current interpreter, ~/gemini-tex/.venv, a .venv next to the
project. Override anytime with the TACTILE_PYTHON env var.
"""
import os
import subprocess
import sys
from pathlib import Path


def python_with_deps(require: str = "PIL, numpy") -> str:
    cands = []
    if os.environ.get("TACTILE_PYTHON"):
        cands.append(os.environ["TACTILE_PYTHON"])
    cands.append(sys.executable)
    cands.append(str(Path.home() / "gemini-tex" / ".venv" / "bin" / "python"))
    cands.append(str(Path(__file__).resolve().parent / ".venv" / "bin" / "python"))
    seen = set()
    for c in cands:
        if not c or c in seen or not Path(c).exists():
            continue
        seen.add(c)
        try:
            if subprocess.run([c, "-c", f"import {require}"],
                              capture_output=True).returncode == 0:
                return c
        except Exception:
            pass
    return sys.executable   # best effort; will surface a clear ImportError if truly missing
