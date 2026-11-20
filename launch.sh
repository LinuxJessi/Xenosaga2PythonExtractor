#!/usr/bin/env bash
# Xenosaga II Extractor — Linux launcher.
# Run: ./launch.sh   (or double-click where your file manager allows it)
# The kit needs numpy, fontTools and uharfbuzz (requirements.txt). If the
# Python found here lacks any of them, the first run makes a private environment
# in .venv/ beside this file and installs them there; later runs use it.
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
if ! "$PY" -c "import numpy, fontTools, uharfbuzz" >/dev/null 2>&1; then
    echo "Setting up numpy, fontTools and uharfbuzz (music and new alphabets) in .venv/ ..."
    if ! { [ -x .venv/bin/python ] || "${BASE:-$PY}" -m venv .venv; } \
            || ! .venv/bin/python -m pip install --quiet -r requirements.txt; then
        echo "Could not install the requirements automatically. Install it yourself, e.g."
        echo "    ${BASE:-python3} -m pip install -r requirements.txt"
        echo "(Debian/Ubuntu may need: sudo apt install python3-venv python3-pip),"
        echo "then run this launcher again."
        exit 1
    fi
    PY=.venv/bin/python
fi
exec "$PY" gui.py "$@"
