#!/usr/bin/env sh
# Lanzador de TuboPlay para macOS y Linux.
set -e
cd "$(dirname "$0")"

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
    if command -v python3 >/dev/null 2>&1; then PYTHON=python3; else PYTHON=python; fi
fi

if ! "$PYTHON" -c "import customtkinter, yt_dlp, vlc, PIL, lz4, requests" >/dev/null 2>&1; then
    echo "Instalando dependencias..."
    "$PYTHON" -m pip install -r requirements.txt
fi

exec "$PYTHON" main.py "$@"
