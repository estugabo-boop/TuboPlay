#!/usr/bin/env python3
"""Lanzador de TuboPlay.

Uso::

    python main.py            # abre la interfaz
    python main.py --selftest # comprueba dependencias y pestañas
    python main.py --headless # comprueba que VLC reproduce un stream de YouTube
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tuboplay.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
