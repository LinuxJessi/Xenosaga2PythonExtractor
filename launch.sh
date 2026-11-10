#!/usr/bin/env bash
# Xenosaga II Extractor — Linux launcher.
# Run: ./launch.sh   (or double-click where your file manager allows it)
# The kit needs numpy (requirements.txt). If the Python found here lacks it,
# the first run makes a private environment in .venv/ beside this file and
# installs numpy there; later runs use that environment.
set -e
cd "$(dirname "$0")"
BASE=""
if command -v python3 >/dev/null 2>&1; then BASE=python3
elif command -v python >/dev/null 2>&1; then BASE=python
fi
PY="$BASE"
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi
if [ -z "$PY" ]; then
    echo "Python 3 is not installed. Install it with your package manager"
    echo "(e.g. sudo apt install python3) and run this launcher again."
    exit 1
fi
if ! "$PY" -c "import numpy" >/dev/null 2>&1; then
    echo "Setting up numpy (the music renderer needs it) in .venv/ ..."
    if ! { "${BASE:-$PY}" -m venv .venv && .venv/bin/python -m pip install --quiet -r requirements.txt; }; then
        echo "Could not install numpy automatically. Install it yourself, e.g."
        echo "    ${BASE:-python3} -m pip install -r requirements.txt"
        echo "or with your package manager (Debian/Ubuntu: sudo apt install python3-numpy),"
        echo "then run this launcher again."
        exit 1
    fi
    PY=.venv/bin/python
fi
exec "$PY" gui.py "$@"
