"""Genera tuboplay.ico a partir del icono dibujado por la propia aplicación.

Uso:  python packaging/make_icon.py [ruta_de_salida]
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw  # noqa: E402

from tuboplay.util import draw_icon  # noqa: E402

BG = (16, 18, 25, 255)      # PANEL_ALT
ACCENT = "#e11d48"


def make_icon(path: str, size: int = 256) -> None:
    base = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(base)
    draw.rounded_rectangle([0, 0, size - 1, size - 1],
                           radius=int(size * 0.22), fill=BG)

    # la nota, centrada y en color de marca
    inner = int(size * 0.64)
    note = draw_icon("music", inner, ACCENT)
    off = (size - inner) // 2
    base.alpha_composite(note, (off, off))

    # pequeño triángulo de reproducción en la esquina inferior derecha
    badge = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bd = ImageDraw.Draw(badge)
    r = int(size * 0.30)
    cx, cy = int(size * 0.74), int(size * 0.74)
    bd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=BG, outline=ACCENT,
               width=max(2, size // 32))
    t = int(r * 0.55)
    bd.polygon([(cx - t * 0.7, cy - t), (cx - t * 0.7, cy + t),
                (cx + t * 1.0, cy)], fill=ACCENT)
    base.alpha_composite(badge)

    base.save(path, format="ICO",
              sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                     (64, 64), (128, 128), (256, 256)])
    print("icono:", path, os.path.getsize(path), "bytes")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "tuboplay.ico")
    make_icon(target)
