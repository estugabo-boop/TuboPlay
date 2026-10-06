"""Genera la distribución de TuboPlay para Windows.

Produce dos archivos en ``dist/``:

``TuboPlay-Instalador.exe``
    Un único ejecutable que instala TuboPlay con doble clic (sin permisos de
    administrador, en ``%LOCALAPPDATA%\\TuboPlay\\app``) y crea los accesos
    directos del escritorio y del Menú Inicio.

``TuboPlay-portable.zip``
    La carpeta de la aplicación tal cual: se descomprime y se ejecuta
    ``TuboPlay\\TuboPlay.exe``.

Uso::

    python packaging/build.py             # todo (icono, .exe, instalador, zip)
    python packaging/build.py --zip-only  # sólo el .zip
"""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIST = os.path.join(ROOT, "dist")
APP_NAME = "TuboPlay"
INSTALLER_NAME = "TuboPlay-Instalador"
README_TXT = """TuboPlay {version}
===============

Reproductor de música que usa las pestañas de YouTube que ya tienes abiertas
en el navegador (Chrome, Edge o Firefox), con pestaña «Mix» y control por voz.


REQUISITOS
----------
- Windows 10 o 11
- VLC (videolan.org) — obligatorio para reproducir
- Para el control por voz: se descarga solo, con el botón del micrófono


CÓMO SE USA
-----------
1. Ejecuta TuboPlay.exe
2. Si no ves tus pestañas, pulsa «Conectar navegador»
3. Pulsa una pestaña o la pestaña «Mix» y a sonar


CONTROL DESDE EL MÓVIL
----------------------
Con el PC y el teléfono en la misma red Wi-Fi, pulsa el botón del teléfono
en la barra superior y abre la dirección que aparece en el navegador del
móvil.  La música sigue sonando en el PC; el teléfono es el mando.


DESINSTALAR
-----------
Cierra TuboPlay y ejecuta Desinstalar.bat (está en la misma carpeta).
"""


def run(cmd, **kw) -> None:
    print("\n$ " + " ".join(cmd), flush=True)
    result = subprocess.run(cmd, cwd=ROOT, **kw)
    if result.returncode:
        sys.exit(result.returncode)


def build_icon() -> None:
    run([sys.executable, os.path.join(HERE, "make_icon.py")])


def write_readme() -> None:
    """Deja el LEEME.txt dentro de la carpeta de la aplicación."""
    from tuboplay import __version__
    app_dir = os.path.join(DIST, APP_NAME)
    if not os.path.isdir(app_dir):
        return
    with open(os.path.join(app_dir, "LEEME.txt"), "w", encoding="utf-8") as fh:
        fh.write(README_TXT.format(version=__version__))


def build_app() -> None:
    run([sys.executable, "-m", "PyInstaller",
         "--noconfirm", "--clean", "--windowed",
         "--name", APP_NAME,
         "--icon", os.path.join(HERE, "tuboplay.ico"),
         "--paths", ROOT,
         "--collect-all", "yt_dlp",
         "--collect-all", "vosk",
         "--hidden-import", "vlc",
         "--hidden-import", "lz4",
         "--hidden-import", "lz4.block",
         "--hidden-import", "requests",
         os.path.join(ROOT, "main.py")])
    write_readme()          # antes de empaquetar el instalador


def build_installer() -> None:
    run([sys.executable, "-m", "PyInstaller",
         "--noconfirm", "--clean", "--windowed", "--onefile",
         "--name", INSTALLER_NAME,
         "--icon", os.path.join(HERE, "tuboplay.ico"),
         "--add-data", os.path.join(DIST, APP_NAME) + ";" + APP_NAME,
         "--hidden-import", "tkinter",
         os.path.join(HERE, "instalador.py")])


def build_zip() -> None:
    app_dir = os.path.join(DIST, APP_NAME)
    if not os.path.isfile(os.path.join(app_dir, APP_NAME + ".exe")):
        sys.exit("No existe %s: ejecuta el build completo." % app_dir)

    write_readme()

    out = os.path.join(DIST, "TuboPlay-portable.zip")
    if os.path.exists(out):
        os.remove(out)
    print("\nCreando %s…" % out, flush=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for base, dirs, names in os.walk(app_dir):
            dirs.sort()
            for name in sorted(names):
                full = os.path.join(base, name)
                z.write(full, os.path.relpath(full, os.path.dirname(app_dir)))
    print("   %.1f MB" % (os.path.getsize(out) / 1048576), flush=True)


def main() -> int:
    sys.path.insert(0, ROOT)
    if "--zip-only" in sys.argv:
        build_zip()
        return 0
    for step in (build_icon, build_app, build_installer, build_zip):
        step()

    print("\nListo. Archivos en dist/:", flush=True)
    for name in sorted(os.listdir(DIST)):
        path = os.path.join(DIST, name)
        if os.path.isfile(path):
            print("   %-28s %8.1f MB" % (name, os.path.getsize(path) / 1048576))
        else:
            print("   %-28s <carpeta>" % name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
