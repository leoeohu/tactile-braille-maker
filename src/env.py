"""Shared config for the toolkit: paths, .env loading, and interpreter resolution.

All other modules in src/ import from here so paths and the API-key loading live in one
place. Scripts are run by path (python src/<x>.py) and are co-located, so a plain
`import env` works from any of them.
"""
import os
import subprocess
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent          # .../tactile/src
REPO_ROOT = SRC_DIR.parent                          # .../tactile (repo root)
OUT_DIR = REPO_ROOT / "out"                         # all generated STLs land here
BLENDER = os.environ.get("BLENDER", "/Applications/Blender.app/Contents/MacOS/Blender")
BLENDER_SCRIPT = SRC_DIR / "blender_displace.py"    # run by Blender, not python


def load_env() -> dict:
    """Read API keys from REPO_ROOT/.env (real env vars override)."""
    cfg = {}
    envp = REPO_ROOT / ".env"
    if envp.exists():
        for line in envp.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("POLLINATIONS_TOKEN", "GEMINI_API_KEY"):
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


def python_with_deps(require: str = "PIL, numpy") -> str:
    """Return a Python interpreter that can import the deps.

    The GUI may be launched with a bare system Python; subprocesses still need PIL /
    numpy / PyMuPDF, so pick an interpreter that has them instead of sys.executable.
    Order: $TACTILE_PYTHON, current, ~/gemini-tex/.venv, ./.venv. Override via TACTILE_PYTHON.
    """
    cands = []
    if os.environ.get("TACTILE_PYTHON"):
        cands.append(os.environ["TACTILE_PYTHON"])
    cands.append(sys.executable)
    cands.append(str(Path.home() / "gemini-tex" / ".venv" / "bin" / "python"))
    cands.append(str(REPO_ROOT / ".venv" / "bin" / "python"))
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
    return sys.executable   # best effort; surfaces a clear ImportError if truly missing
