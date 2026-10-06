#!/usr/bin/env python3
"""Punto de entrada de TuboPlay."""

from __future__ import annotations

import argparse
import os
import sys


def _enable_dpi_awareness() -> None:
    """Texto nítido en pantallas de alta resolución (Windows)."""
    if os.name != "nt":
        return
    import ctypes

    try:
        # PROCESS_PER_MONITOR_DPI_AWARE
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


def _selftest() -> int:
    """Comprueba dependencias sin abrir la interfaz."""
    from . import __version__

    print("TuboPlay", __version__)
    ok = True

    for module in ("yt_dlp", "customtkinter", "PIL", "lz4.block", "requests"):
        try:
            __import__(module)
            print("  [ok]   " + module)
        except Exception as exc:  # noqa: BLE001
            ok = False
            print("  [FALLO] %s -> %s" % (module, exc))

    try:
        from .player import vlc_available

        good, msg = vlc_available()
        print("  [%s] VLC -> %s" % ("ok" if good else "FALLO", msg))
        ok = ok and good
    except Exception as exc:  # noqa: BLE001
        ok = False
        print("  [FALLO] VLC -> %s" % exc)

    try:
        from .tabs import collect_tabs

        tabs, notes = collect_tabs()
        print("  [ok]   pestañas detectadas: %d" % len(tabs))
        for note in notes:
            print("         - " + note)
        for tab in tabs[:10]:
            print("         · [%s] %s | %s" % (tab.browser, tab.label[:50], tab.url[:70]))
    except Exception as exc:  # noqa: BLE001
        ok = False
        print("  [FALLO] pestañas -> %s" % exc)

    try:
        from . import voice

        missing = voice.missing()
        if missing:
            print("  [·]    control por voz: pendiente de instalar (%s)"
                  % ", ".join(missing))
            print("         - se instala desde el propio programa con el "
                  "botón del micrófono")
        else:
            print("  [ok]   control por voz -> %s" % voice.describe())
    except Exception as exc:  # noqa: BLE001
        print("  [·]    control por voz -> %s" % exc)

    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tuboplay", description="Reproductor de música de YouTube.")
    parser.add_argument("--version", action="store_true", help="muestra la versión")
    parser.add_argument("--selftest", action="store_true",
                        help="comprueba dependencias y pestañas sin abrir la ventana")
    parser.add_argument("--headless", action="store_true",
                        help="comprueba el motor de audio sin abrir la ventana")
    parser.add_argument("--play", metavar="URL_O_TEXTO", default=None,
                        help="reproduce esa URL de YouTube (o busca ese texto) al arrancar")
    parser.add_argument("--install-voice", action="store_true",
                        help="instala el reconocimiento de voz (micrófono) y termina")
    args = parser.parse_args(argv)

    if args.version:
        from . import __version__

        print("TuboPlay", __version__)
        return 0
    if args.selftest:
        return _selftest()
    if args.install_voice:
        return _install_voice()
    if args.headless:
        return _headless()

    _enable_dpi_awareness()
    from .app import TuboPlayApp

    app = TuboPlayApp()
    if args.play:
        app.after(700, lambda: app.enqueue(args.play, autoplay=True))
    app.mainloop()
    return 0


def _install_voice() -> int:
    """Instala el reconocimiento de voz sin abrir la ventana."""
    from . import voice

    print("Instalando el control por voz (una sola vez)…")
    ok, msg = voice.install(lambda text: print("  " + text, flush=True))
    print(msg)
    return 0 if ok else 1


def _headless() -> int:
    """Comprueba que VLC puede abrir un stream de YouTube (sin ventana)."""
    import time

    from .extractor import resolve
    from .models import Track
    from .player import VlcPlayer, vlc_available

    good, msg = vlc_available()
    print("VLC:", msg)
    if not good:
        return 1
    track = Track(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    print("Extrayendo audio…")
    resolve(track)
    print("Stream:", track.title, track.stream_url[:80], "…")
    player = VlcPlayer()
    player.set_volume(20)
    player.play(track.stream_url, track.stream_headers)
    deadline = time.time() + 25
    state = ""
    while time.time() < deadline:
        time.sleep(0.5)
        state = player.state()
        if state in ("Playing", "Error", "Ended"):
            break
    print("Estado:", state, "tiempo:", player.time_ms(), "ms")
    time.sleep(2)
    after = player.time_ms()
    print("2 s después:", after, "ms")
    player.release()
    return 0 if state == "Playing" and after > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
