"""Utilidades: ejecución en segundo plano, caché de miniaturas, iconos."""

from __future__ import annotations

import os
import queue
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Ejecución en segundo plano con vuelta al hilo de la interfaz
# ---------------------------------------------------------------------------


class Runner:
    """
    Ejecuta funciones en un hilo de fondo y entrega los resultados en el
    hilo principal (el de Tk) usando ``root.after``.

    Tk **no** es seguro desde otros hilos, así que nunca se le toca fuera
    del hilo principal: los callbacks se encolan y se procesan con ``pump()``
    desde el bucle de la interfaz.
    """

    def __init__(self, max_workers: int = 4) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="tuboplay")
        self._q: "queue.Queue[Callable[[], None]]" = queue.Queue()

    # -- hilo principal -----------------------------------------------------
    def pump(self) -> int:
        """Ejecuta los callbacks pendientes. Devuelve cuántos ha ejecutado."""
        n = 0
        while True:
            try:
                fn = self._q.get_nowait()
            except queue.Empty:
                break
            n += 1
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - no dejamos morir la UI
                print("[TuboPlay] error en callback:", exc)
        return n

    def call(self, fn: Callable[[], None]) -> None:
        """Programa ``fn`` para que corra en el hilo principal."""
        self._q.put(fn)

    # -- hilos de fondo -----------------------------------------------------
    def submit(self, fn: Callable[[], Any], on_ok: Optional[Callable[[Any], None]] = None,
               on_err: Optional[Callable[[BaseException], None]] = None) -> None:
        def _work() -> None:
            try:
                result = fn()
            except BaseException as exc:  # noqa: BLE001
                if on_err is None:
                    self.call(lambda e=exc: print("[TuboPlay] error:", e))
                else:
                    self.call(lambda e=exc: on_err(e))
                return
            if on_ok is not None:
                self.call(lambda r=result: on_ok(r))

        self._pool.submit(_work)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------------------
# Miniaturas
# ---------------------------------------------------------------------------

_CACHE_DIR = os.path.join(tempfile.gettempdir(), "tuboplay_thumbs")
_locks: dict = {}
_locks_guard = threading.Lock()


def cached_image_path(url: str) -> Optional[str]:
    """Descarga una miniatura una sola vez y devuelve la ruta local."""
    if not url:
        return None
    os.makedirs(_CACHE_DIR, exist_ok=True)
    name = "".join(c if c.isalnum() else "_" for c in url)[-150:] + ".jpg"
    path = os.path.join(_CACHE_DIR, name)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path

    with _locks_guard:
        lock = _locks.setdefault(url, threading.Lock())
    with lock:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path
        try:
            import requests

            resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200 or not resp.content:
                return None
            tmp = path + ".part"
            with open(tmp, "wb") as fh:
                fh.write(resp.content)
            os.replace(tmp, path)
            return path
        except Exception:  # noqa: BLE001
            return None


# ---------------------------------------------------------------------------
# Iconos dibujados con Pillow (evitamos depender de emoji del sistema)
# ---------------------------------------------------------------------------


def draw_icon(name: str, size: int = 24, color: str = "#ffffff"):
    """Devuelve una ``PIL.Image`` con el icono pedido, en RGBA."""
    from PIL import Image, ImageDraw

    s = size * 4  # dibujamos a 4x y reducimos: queda antialiasing
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = color
    m = s * 0.16  # margen

    if name == "play":
        d.polygon([(m * 1.4, m), (s - m, s / 2), (m * 1.4, s - m)], fill=c)
    elif name == "pause":
        w = (s - 2 * m - s * 0.10) / 2
        d.rounded_rectangle([m, m, m + w, s - m], radius=s * 0.05, fill=c)
        d.rounded_rectangle([s - m - w, m, s - m, s - m], radius=s * 0.05, fill=c)
    elif name == "prev":
        # |◀
        bw = s * 0.15
        d.rounded_rectangle([m, m, m + bw, s - m], radius=s * 0.03, fill=c)
        d.polygon([(s * 0.44, s / 2), (s - m * 0.6, m), (s - m * 0.6, s - m)], fill=c)
    elif name == "next":
        # ▶|
        bw = s * 0.15
        d.rounded_rectangle([s - m - bw, m, s - m, s - m], radius=s * 0.03, fill=c)
        d.polygon([(s * 0.56, s / 2), (m * 0.6, m), (m * 0.6, s - m)], fill=c)
    elif name == "shuffle":
        # dos flechas cruzadas
        d.line([(m, s * 0.30), (s - m, s * 0.30)], fill=c, width=int(s * 0.07))
        d.line([(m, s * 0.70), (s - m, s * 0.70)], fill=c, width=int(s * 0.07))
        d.polygon([(s - m, s * 0.30), (s - m - s * 0.18, s * 0.17), (s - m - s * 0.18, s * 0.43)], fill=c)
        d.polygon([(s - m, s * 0.70), (s - m - s * 0.18, s * 0.57), (s - m - s * 0.18, s * 0.83)], fill=c)
    elif name in ("repeat", "repeat_one"):
        w = max(2, int(s * 0.08))
        d.arc([m, s * 0.16, s - m, s * 0.56], start=180, end=360, fill=c, width=w)
        d.arc([m, s * 0.44, s - m, s * 0.84], start=0, end=180, fill=c, width=w)
        d.polygon([(s * 0.70, s * 0.14), (s - m * 0.7, s * 0.34), (s * 0.70, s * 0.44)], fill=c)
        d.polygon([(s * 0.30, s * 0.86), (m * 0.7, s * 0.66), (s * 0.30, s * 0.56)], fill=c)
        if name == "repeat_one":
            d.polygon([(s * 0.44, s * 0.40), (s * 0.54, s * 0.34),
                       (s * 0.54, s * 0.66), (s * 0.44, s * 0.60)], fill=c)
            d.rounded_rectangle([s * 0.40, s * 0.62, s * 0.60, s * 0.66],
                                radius=s * 0.02, fill=c)
    elif name == "volume":
        d.polygon([(m, s * 0.38), (s * 0.34, s * 0.38), (s * 0.58, s * 0.18),
                   (s * 0.58, s * 0.82), (s * 0.34, s * 0.62), (m, s * 0.62)], fill=c)
        d.arc([s * 0.52, s * 0.24, s * 0.86, s * 0.76], start=-58, end=58,
              fill=c, width=int(s * 0.06))
    elif name == "plus":
        w = int(s * 0.10)
        d.rounded_rectangle([s / 2 - w / 2, m, s / 2 + w / 2, s - m], radius=w / 3, fill=c)
        d.rounded_rectangle([m, s / 2 - w / 2, s - m, s / 2 + w / 2], radius=w / 3, fill=c)
    elif name == "refresh":
        d.arc([m, m, s - m, s - m], start=35, end=315, fill=c, width=int(s * 0.10))
        d.polygon([(s - m * 0.4, s * 0.10), (s - m * 1.4, s * 0.34), (s - m * 2.4, s * 0.10)], fill=c)
    elif name == "close":
        w = int(s * 0.10)
        for a, b in ((m, m), (s - m, m)):
            d.line([(a, b), (s - a, s - b)], fill=c, width=w)
        d.line([(s - m, m), (m, s - m)], fill=c, width=w)
    elif name == "tab":  # ventana de navegador
        d.rounded_rectangle([m, s * 0.26, s - m, s - m], radius=s * 0.08,
                            outline=c, width=max(2, int(s * 0.08)))
        d.rounded_rectangle([m * 1.4, m * 0.9, s * 0.52, s * 0.34],
                            radius=s * 0.05, fill=c)
    elif name == "play_circle":
        w = max(2, int(s * 0.09))
        d.ellipse([m, m, s - m, s - m], outline=c, width=w)
        r = s * 0.17
        cx, cy = s * 0.53, s * 0.5
        d.polygon([(cx - r * 0.8, cy - r), (cx - r * 0.8, cy + r), (cx + r * 1.1, cy)],
                  fill=c)
    elif name == "list":
        for i in range(3):
            y = s * (0.26 + i * 0.24)
            d.ellipse([m, y, m + s * 0.13, y + s * 0.13], fill=c)
            d.rounded_rectangle([m + s * 0.22, y + s * 0.03, s - m, y + s * 0.10],
                                radius=s * 0.03, fill=c)
    elif name == "music":
        d.ellipse([m, s * 0.58, m + s * 0.34, s - m], fill=c)
        d.ellipse([s * 0.50, s * 0.48, s * 0.50 + s * 0.34, s * 0.48 + s * 0.34], fill=c)
        d.polygon([(m + s * 0.30, s * 0.62), (s - m, s * 0.42), (s - m, s * 0.54),
                   (m + s * 0.30, s * 0.74)], fill=c)
        d.rectangle([s - m - s * 0.06, s * 0.20, s - m, s * 0.60], fill=c)
    elif name == "mic":  # micro de control por voz
        # cápsula del micrófono
        d.rounded_rectangle([s * 0.37, m * 1.2, s * 0.63, s * 0.60],
                            radius=s * 0.13, fill=c)
        # arco inferior (soporte)
        d.arc([s * 0.22, s * 0.40, s * 0.78, s * 0.86], start=18, end=162,
              fill=c, width=max(2, int(s * 0.07)))
        # pie
        d.line([(s * 0.5, s * 0.84), (s * 0.5, s - m * 1.1)],
               fill=c, width=max(2, int(s * 0.07)))
    elif name == "search":
        r = s * 0.30
        d.ellipse([m, m, m + 2 * r, m + 2 * r], outline=c, width=int(s * 0.09))
        d.line([m + 2 * r * 0.74, m + 2 * r * 0.74, s - m, s - m], fill=c, width=int(s * 0.11))
    elif name == "phone":  # control desde el móvil
        d.rounded_rectangle([s * 0.30, m * 1.1, s * 0.70, s - m * 1.1],
                            radius=s * 0.10, outline=c, width=max(2, int(s * 0.07)))
        # antenita de conexión (ondas a la izquierda)
        d.arc([s * 0.06, s * 0.30, s * 0.42, s * 0.70], start=110, end=250,
              fill=c, width=max(2, int(s * 0.05)))
        # botón de inicio
        d.ellipse([s * 0.46, s * 0.83, s * 0.54, s * 0.91], fill=c)
    else:
        d.ellipse([m, m, s - m, s - m], fill=c)

    return img.resize((size, size), Image.LANCZOS)


def icon_tk(name: str, size: int = 24, color: str = "#ffffff"):
    """Icono listo para ``CTkImage``."""
    from customtkinter import CTkImage

    return CTkImage(light_image=draw_icon(name, size, color),
                    dark_image=draw_icon(name, size, color),
                    size=(size, size))


_PHOTO_CACHE: dict = {}


def icon_photo(name: str, size: int = 24, color: str = "#ffffff"):
    """
    Icono como ``PhotoImage`` de Tk, para los widget Tk puros (las filas de las
    listas).  Se cachea porque cada `PhotoImage` ocupa memoria y Tk exige
    mantener una referencia viva o la imagen desaparece.
    """
    from PIL import ImageTk

    key = (name, size, color)
    photo = _PHOTO_CACHE.get(key)
    if photo is None:
        photo = ImageTk.PhotoImage(draw_icon(name, size, color))
        _PHOTO_CACHE[key] = photo
    return photo
