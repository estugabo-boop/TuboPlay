"""Instalador de TuboPlay para Windows.

No necesita permisos de administrador: se instala en ``%LOCALAPPDATA%\\TuboPlay``
y sólo crea accesos directos en el escritorio y en el Menú Inicio.

Se empaqueta con PyInstaller (ver ``packaging/build.py``) junto con la carpeta
``TuboPlay`` generada, de modo que el resultado es **un único .exe** que se
copia a una máquina nueva sin instalar nada más.

También se puede ejecutar desde el código fuente para probarlo::

    python packaging/instalador.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox

APP_NAME = "TuboPlay"
EXE_NAME = "TuboPlay.exe"
FALLBACK_VERSION = "1.0.0"

BG = "#101219"
PANEL = "#14171e"
ACCENT = "#e11d48"
ACCENT_HOVER = "#be123c"
DIM = "#8b93a7"
GOOD = "#4ade80"
BAD = "#f87171"


# ---------------------------------------------------------------------------
# utilidades
# ---------------------------------------------------------------------------
def version() -> str:
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, root)
        from tuboplay import __version__  # noqa: WPS433
        return __version__
    except Exception:  # noqa: BLE001
        return FALLBACK_VERSION


def source_dir() -> str:
    """Carpeta con la aplicación empaquetada (o la de ``dist`` al probar)."""
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, APP_NAME)  # noqa: SLF001
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "dist", APP_NAME)


def install_dir() -> str:
    """
    Dónde se instala el programa.

    Va en una subcarpeta ``app`` dentro de la carpeta de datos
    (``%LOCALAPPDATA%\\TuboPlay``), porque en esa misma carpeta se guarda el
    modelo de voz descargado: si instaláramos en la raíz, desinstalar borraría
    también el modelo (39 MB) y habría que volver a descargarlo.
    """
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_NAME, "app")


def data_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_NAME)


def clean_old_layout() -> None:
    """Quita la instalación antigua (programa directamente en la raíz)."""
    root = data_dir()
    if os.path.abspath(install_dir()) == os.path.abspath(root):
        return
    for name in (EXE_NAME, "_internal", "Desinstalar.bat"):
        path = os.path.join(root, name)
        try:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            elif os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass


def desktop_dir() -> str:
    return os.path.join(os.path.expanduser("~"), "Desktop")


def start_menu_dir() -> str:
    return os.path.join(os.environ.get("APPDATA", ""),
                        "Microsoft", "Windows", "Start Menu",
                        "Programs")


def vlc_installed() -> str:
    """Ruta de libvlc.dll si VLC está instalado, o '' si no."""
    pf = [os.environ.get("ProgramFiles", r"C:\Program Files"),
          os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
          os.environ.get("ProgramW6432", r"C:\Program Files")]
    for base in dict.fromkeys(pf):          # sin duplicados, manteniendo orden
        for name in ("VLC", r"VideoLAN\VLC"):
            dll = os.path.join(base, name, "libvlc.dll")
            if os.path.isfile(dll):
                return dll
    return ""


def _shortcut(path: str, target: str, workdir: str, icon: str,
              desc: str) -> None:
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
          "$s.TargetPath='%s';$s.WorkingDirectory='%s';"
          "$s.IconLocation='%s,0';$s.Description='%s';$s.Save()")
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         ps % (path, target, workdir, icon, desc)],
        check=False, creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def copy_tree(src: str, dst: str, log) -> int:
    """Copia ``src`` en ``dst`` contando los archivos (para la barra)."""
    files = []
    for base, _dirs, names in os.walk(src):
        for name in names:
            files.append(os.path.join(base, name))
    total = len(files) or 1
    if os.path.isdir(dst):
        shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(dst, exist_ok=True)

    for i, full in enumerate(files, 1):
        rel = os.path.relpath(full, src)
        out = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copy2(full, out)
        if i % 150 == 0 or i == total:
            log("   … %d / %d archivos" % (i, total), pct=100.0 * i / total)
    return total


UNINSTALL = """@echo off
cd /d "%TEMP%"
del "%USERPROFILE%\\Desktop\\{name}.lnk" >nul 2>&1
del "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\{name}.lnk" >nul 2>&1
rmdir /s /q "%LOCALAPPDATA%\\{low}\\app"
echo TuboPlay se ha desinstalado.
echo Tus datos (el modelo de voz) siguen en %LOCALAPPDATA%\\{low}
timeout /t 5 >nul
""".format(name=APP_NAME, low=APP_NAME)


def silent_install() -> int:
    """Instala sin ventana.  Devuelve el código de salida (0 = bien)."""
    log_path = os.path.join(os.environ.get("TEMP", "."),
                            "tuboplay-instalador.log")
    try:
        os.remove(log_path)
    except OSError:
        pass

    def log(text: str, pct: float = None, style: str = None) -> None:
        with open(log_path, "a", encoding="utf-8", errors="replace") as fh:
            fh.write(text + "\n")

    src = source_dir()
    dest = install_dir()
    if not os.path.isfile(os.path.join(src, EXE_NAME)):
        log("falta la carpeta de la aplicación: %s" % src)
        return 2
    try:
        log("instalando en %s" % dest)
        clean_old_layout()
        total = copy_tree(src, dest, log)
        icon = os.path.join(dest, EXE_NAME)
        desc = "Reproductor de música de tus pestañas de YouTube"
        _shortcut(os.path.join(desktop_dir(), "%s.lnk" % APP_NAME),
                  icon, dest, icon, desc)
        _shortcut(os.path.join(start_menu_dir(), "%s.lnk" % APP_NAME),
                  icon, dest, icon, desc)
        with open(os.path.join(dest, "Desinstalar.bat"), "w",
                  encoding="cp1252", errors="ignore") as fh:
            fh.write(UNINSTALL)
        log("%d archivos instalados en %s" % (total, dest))
        if not vlc_installed():
            log("AVISO: falta VLC")
        return 0
    except Exception as exc:  # noqa: BLE001
        log("ERROR: %s" % exc)
        return 1



class Installer(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("%s — Instalador" % APP_NAME)
        self.configure(bg=BG)
        self.resizable(False, False)
        w, h = 520, 540
        self.geometry("%dx%d+%d+%d" % (w, h,
                                       (self.winfo_screenwidth() - w) // 2,
                                       (self.winfo_screenheight() - h) // 2))
        self.dest = install_dir()
        self.done = False
        self._build()

    # -- interfaz ---------------------------------------------------------
    def _build(self) -> None:
        tk.Label(self, text="🎧  %s" % APP_NAME, bg=BG, fg="#ffffff",
                 font=("Segoe UI", 20, "bold")).pack(padx=24, pady=(26, 0),
                                                     anchor="w")
        tk.Label(self, bg=BG, fg=DIM, font=("Segoe UI", 11), anchor="w",
                 justify="left",
                 text=("Reproductor de música de tus pestañas de YouTube · "
                       "versión %s" % version())
                 ).pack(padx=24, pady=(2, 0), anchor="w")

        box = tk.Frame(self, bg=PANEL)
        box.pack(fill="both", expand=False, padx=24, pady=(18, 0))

        self._field(box, "Se instalará en:", self.dest)

        vlc = vlc_installed()
        if vlc:
            self._field(box, "VLC encontrado:", vlc, ok=True)
        else:
            self._field(box, "VLC no encontrado:",
                        "TuboPlay necesita VLC para reproducir: "
                        "descárgalo de videolan.org", ok=False)

        self.chk_desk = tk.BooleanVar(value=True)
        self.chk_menu = tk.BooleanVar(value=True)
        for var, text in ((self.chk_desk, "Crear acceso directo en el escritorio"),
                          (self.chk_menu, "Añadir al Menú Inicio")):
            tk.Checkbutton(box, text=text, variable=var, bg=PANEL, fg="#ffffff",
                           selectcolor=BG, activebackground=PANEL,
                           activeforeground="#ffffff", anchor="w",
                           font=("Segoe UI", 11)
                           ).pack(fill="x", padx=14, pady=(8, 0))
        tk.Frame(box, bg=PANEL).pack(pady=10)

        self.progress = tk.Frame(self, bg="#22262f", height=6)
        self.progress.pack(fill="x", padx=24, pady=(16, 0))
        self.progress.pack_propagate(False)
        self.fill = tk.Frame(self.progress, bg=ACCENT, width=0, height=6)
        self.fill.place(x=0, y=0, relheight=1.0)

        self.log_box = tk.Text(self, height=7, bg="#0e1117", fg=DIM,
                               font=("Consolas", 10), relief="flat",
                               padx=10, pady=8, state="disabled",
                               highlightthickness=1,
                               highlightbackground="#262b38")
        self.log_box.pack(fill="both", expand=True, padx=24, pady=(10, 0))
        self.log_box.tag_configure("ok", foreground=GOOD)
        self.log_box.tag_configure("bad", foreground=BAD)
        self.log_box.tag_configure("title", foreground="#ffffff")

        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=24, pady=16)

        self.btn_install = tk.Button(
            row, text="Instalar", bg=ACCENT, fg="#ffffff", activebackground=ACCENT_HOVER,
            activeforeground="#ffffff", relief="flat", font=("Segoe UI", 12, "bold"),
            height=2, cursor="hand2", command=self.install)
        self.btn_install.pack(side="left", fill="x", expand=True)

        self.btn_open = tk.Button(
            row, text="Abrir TuboPlay", bg=PANEL, fg="#ffffff",
            activebackground="#242a38", activeforeground="#ffffff",
            relief="flat", font=("Segoe UI", 12, "bold"), height=2,
            cursor="hand2", state="disabled", command=self.open_app)
        self.btn_open.pack(side="left", fill="x", expand=True, padx=(8, 0))

        self.btn_close = tk.Button(
            row, text="Cerrar", bg=PANEL, fg=DIM, activebackground="#242a38",
            activeforeground="#ffffff", relief="flat",
            font=("Segoe UI", 12), height=2, cursor="hand2",
            command=self.destroy)
        self.btn_close.pack(side="left", fill="x", expand=True, padx=(8, 0))

        tk.Label(self, bg=BG, fg="#4c5466", font=("Segoe UI", 9),
                 text=("No se toca el registro ni hace falta administrador.  "
                       "Para desinstalar: %s" %
                       os.path.join(install_dir(), "Desinstalar.bat"))
                 ).pack(padx=24, pady=(0, 14))

    def _field(self, parent, label: str, value: str, ok: bool = True) -> None:
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", padx=14, pady=(10, 0))
        tk.Label(row, text=label, bg=PANEL, fg=DIM, font=("Segoe UI", 10),
                 anchor="w").pack(side="left")
        tk.Label(row, text=value, bg=PANEL,
                 fg=GOOD if ok else BAD, font=("Segoe UI", 10, "bold"),
                 anchor="e").pack(side="right")

    # -- registro ---------------------------------------------------------
    def log(self, text: str, pct: float = None, style: str = None) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n", style or "")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        if pct is not None:
            self.fill.configure(width=int(
                self.progress.winfo_width() * min(100.0, pct) / 100.0))
        self.update_idletasks()

    # -- instalación ------------------------------------------------------
    def install(self) -> None:
        src = source_dir()
        if not os.path.isfile(os.path.join(src, EXE_NAME)):
            messagebox.showerror(
                "Falta la aplicación",
                "No encuentro la carpeta con TuboPlay:\n\n%s\n\n"
                "Si has descargado el instalador suelto, debe ir junto a la "
                "carpeta «%s»." % (src, APP_NAME), parent=self)
            return
        self.btn_install.configure(state="disabled", text="Instalando…")
        self.update_idletasks()
        try:
            self._install_body(src)
        except Exception as exc:  # noqa: BLE001
            self.log("ERROR: %s" % exc, style="bad")
            self.btn_install.configure(state="normal", text="Reintentar")
            messagebox.showerror("No se pudo instalar", str(exc), parent=self)
            return
        self.done = True
        self.btn_install.configure(state="disabled", text="Instalado ✓")
        self.btn_open.configure(state="normal")

    def _install_body(self, src: str) -> None:
        dest = self.dest
        self.log("Instalando TuboPlay…", style="title")
        self.log("   destino: %s" % dest)
        clean_old_layout()
        n = copy_tree(src, dest, self.log)
        self.log("   %d archivos copiados." % n, pct=100.0, style="ok")

        icon = os.path.join(dest, EXE_NAME)
        target = icon
        workdir = dest

        if self.chk_desk.get():
            _shortcut(os.path.join(desktop_dir(), "%s.lnk" % APP_NAME),
                      target, workdir, icon,
                      "Reproductor de música de tus pestañas de YouTube")
            self.log("   acceso directo en el escritorio", style="ok")
        if self.chk_menu.get():
            _shortcut(os.path.join(start_menu_dir(), "%s.lnk" % APP_NAME),
                      target, workdir, icon,
                      "Reproductor de música de tus pestañas de YouTube")
            self.log("   acceso directo en el Menú Inicio", style="ok")

        with open(os.path.join(dest, "Desinstalar.bat"), "w",
                  encoding="cp1252", errors="ignore") as fh:
            fh.write(UNINSTALL)
        self.log("   desinstalador en Desinstalar.bat", style="ok")

        if not vlc_installed():
            self.log("   AVISO: falta VLC — la música no sonará sin él.",
                     style="bad")
        self.log("")
        self.log("Instalado. Ya puedes abrir TuboPlay.", style="title")

    def open_app(self) -> None:
        exe = os.path.join(self.dest, EXE_NAME)
        if not os.path.isfile(exe):
            return
        if vlc_installed():
            os.startfile(exe)
            self.destroy()
        else:
            if messagebox.askyesno(
                    "Falta VLC",
                    "TuboPlay necesita VLC para reproducir música y ahora "
                    "mismo no está instalado.\n\n¿Abrir videolan.org para "
                    "descargarlo?", parent=self):
                os.startfile("https://www.videolan.org/")
            os.startfile(exe)
            self.destroy()


if __name__ == "__main__":
    if "--auto" in sys.argv:            # instalación silenciosa
        sys.exit(silent_install())
    app = Installer()
    app.log("Listo para instalar. Pulsa «Instalar».", style="title")
    app.mainloop()
