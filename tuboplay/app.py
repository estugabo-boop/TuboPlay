"""Interfaz gráfica de TuboPlay (CustomTkinter)."""

from __future__ import annotations

import random
import re
import secrets
import time
import tkinter as tk
from tkinter import font as tkfont
from typing import Dict, List, Optional

import customtkinter as ctk
from PIL import Image, ImageTk
from tkinter import messagebox

from . import APP_NAME, __version__
from .extractor import fetch_title, probe, resolve, search
from .mixes import build_mixes, personal_mixes
from .models import (Tab, Track, canonical_url, format_time, is_mix_url, is_url,
                     video_id)
from .player import VlcPlayer, vlc_available
from .tabs import (browser_is_running, collect_tabs, start_browser_with_devtools,
                   stop_browser, youtube_tabs)
from .util import Runner, cached_image_path, icon_photo, icon_tk
from . import voice as voice_mod
from . import web as web_mod

ACCENT = "#e11d48"
ACCENT_HOVER = "#be123c"
PANEL = "#14171e"
PANEL_ALT = "#101219"
ROW_HOVER = "#1a1e28"
ROW_CURRENT = "#1c2231"
TEXT_DIM = "#8b93a7"
TEXT_BAD = "#f87171"
ROW_LIMIT = 60            # filas pintadas de golpe; el resto, con «mostrar más»
MIX_BATCH = 60            # vídeos de una mezcla que se piden para arrancar ya
MIX_EXTEND = 200          # tanda larga que llega en segundo plano
MIX_MAX = 300             # tope de la cola cuando la fuente es una mezcla

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")


class _Row(tk.Frame):
    """
    Fila clicable de las listas.

    Se dibuja con widgets **Tk puros**, no con CustomTkinter: crear un `CTkFrame`
    cuesta ~15 ms y con sus etiquetas la fila llegaba a 35 ms, de modo que la
    vista de 38 pestañas tardaba 2,2 s y una cola de 200 canciones 8,4 s en
    aparecer.  En Tk la misma fila son ~2 ms.
    """

    def __init__(self, master, command=None, base: str = PANEL,
                 hover: str = ROW_HOVER):
        super().__init__(master, height=62, bg=base, highlightthickness=0,
                         bd=0, relief="flat")
        self._command = command
        self._base = base
        self._hover = hover
        try:
            self.configure(cursor="hand2")
        except Exception:  # noqa: BLE001
            pass

    # -- apariencia ---------------------------------------------------------
    def paint(self, color: str) -> None:
        widgets = [self]
        stack = list(self.winfo_children())
        while stack:
            w = stack.pop()
            widgets.append(w)
            stack.extend(w.winfo_children())
        for w in widgets:
            try:
                w.configure(bg=color)
            except tk.TclError:
                pass

    def set_base(self, color: str) -> None:
        self._base = color
        self.paint(color)

    # -- interacción --------------------------------------------------------
    def bind_click(self, widget) -> None:
        """Clic y *hover* en la fila y en todos sus hijos."""
        widget.bind("<Button-1>", self._fire)
        widget.bind("<Enter>", self._hover_in)
        widget.bind("<Leave>", self._hover_out)
        for child in widget.winfo_children():
            self.bind_click(child)

    def _fire(self, _event=None):
        if self._command:
            self._command()

    def _hover_in(self, _event=None):
        self.paint(self._hover)

    def _hover_out(self, event=None):
        """Se apaga el resalte solo si el ratón se va de la fila de verdad.

        Al pasar de la fila a una etiqueta hija Tk manda un `<Leave>` a la
        fila; mirando dónde estaba el cursor evita el parpadeo.
        """
        if event is not None:
            try:
                x, y = event.x_root, event.y_root
                left, top = self.winfo_rootx(), self.winfo_rooty()
                if left <= x < left + self.winfo_width() \
                        and top <= y < top + self.winfo_height():
                    return
            except tk.TclError:
                pass
        self.paint(self._base)


def _fonts():
    """Fuentes de las filas, creadas una sola vez (crear una por fila es caro).

    Devuelve ``(título, título en negrita, subtítulo)``.
    """
    global _ROW_FONT, _ROW_FONT_B, _ROW_SUB_FONT
    if _ROW_FONT is None:
        try:
            family = tkfont.nametofont("TkDefaultFont").actual("family")
        except tk.TclError:
            family = "Segoe UI"
        _ROW_FONT = tkfont.Font(family=family, size=13)
        _ROW_FONT_B = tkfont.Font(family=family, size=13, weight="bold")
        _ROW_SUB_FONT = tkfont.Font(family=family, size=11)
    return _ROW_FONT, _ROW_FONT_B, _ROW_SUB_FONT


_ROW_FONT = None
_ROW_FONT_B = None
_ROW_SUB_FONT = None


def _photo(name: str, size: int = 26, color: str = "#ffffff"):
    """Icono como `PhotoImage` (lo que entienden los widget Tk de las listas)."""
    return icon_photo(name, size, color)


class TuboPlayApp(ctk.CTk):
    TICK_MS = 200
    PUMP_MS = 60
    TAB_REFRESH_MS = 8000

    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        # se ajusta al tamaño real de la pantalla
        self.update_idletasks()
        screen_w = max(self.winfo_screenwidth(), 1024)
        screen_h = max(self.winfo_screenheight(), 700)
        width = min(1120, int(screen_w * 0.94))
        height = min(740, int(screen_h * 0.90))
        self.geometry("%dx%d+%d+%d" % (width, height,
                                       (screen_w - width) // 2,
                                       (screen_h - height) // 2))
        self.minsize(940, 620)

        self.runner = Runner(max_workers=4)
        # cola aparte para extraer audio: así las miniaturas o los títulos de
        # las pestañas nunca retrasan el momento de arrancar a sonar
        self.audio_runner = Runner(max_workers=3)
        self.player: Optional[VlcPlayer] = None
        self.vlc_ok, self.vlc_msg = vlc_available()

        # estado
        self.tabs: List[Tab] = []
        self.mixes: List[Tab] = []          # pestaña «Mix»
        self.mixes_ready = False
        self._mixes_busy = False
        self._mixes_dirty = False
        self.queue: List[Track] = []
        self.index: int = -1
        self.shuffle = False
        self.loop_mode = "off"          # off | all | one
        self.view = "tabs"              # tabs | mix | queue
        self.playing = False
        self.voice = voice_mod.VoiceListener()
        self._voice_last_noise = 0.0
        self._voice_install_running = False
        self._seeking = False
        self._seek_value = 0.0
        self._seek_last_move = 0.0
        self._play_token = 0
        self._failures = 0
        self._tab_signature = ""
        self._tabs_busy = False
        self._tab_refresh_scheduled = False
        self._thumb_refs: list = []
        self._queue_rows: list = []        # filas de la cola ya construidas
        self._queue_len = 0                # pistas en cola al pintar
        self._rows_shown = 0               # filas dibujadas realmente
        self._row_limit = ROW_LIMIT        # cuántas filas se pintan como máximo
        self._mix_extending = False        # pidiendo el resto de una mezcla
        self._icons: Dict[str, ctk.CTkImage] = {}

        # control desde el móvil (servidor web local, ver web.py)
        self._web: Optional[web_mod.WebServer] = None
        self._web_url = ""
        self._web_state: Dict[str, object] = {}
        self._web_state_at = 0.0
        self._web_token = secrets.token_urlsafe(12)

        self._make_icons()
        self._build_ui()
        self._start_web()

        if not self.vlc_ok:
            self._status("VLC no está disponible: %s" % self.vlc_msg, error=True)

        self._pump()
        self._tick()
        self.refresh_tabs()
        self._schedule_tab_refresh()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        for key, fn in (("<space>", lambda e: self.toggle_play()),
                        ("<Left>", lambda e: self._nudge(-5)),
                        ("<Right>", lambda e: self._nudge(5)),
                        ("<Up>", lambda e: self._nudge_volume(5)),
                        ("<Down>", lambda e: self._nudge_volume(-5))):
            self.bind(key, self._guarded(fn))

    # ------------------------------------------------------------------ #
    #  utilidades de eventos
    # ------------------------------------------------------------------ #
    def _guarded(self, fn):
        """Ejecuta ``fn`` salvo que el foco esté en el cuadro de texto."""

        def handler(_event=None):
            try:
                if self.focus_get() is self.entry:
                    return
                fn()
            except Exception:  # noqa: BLE001
                pass

        return handler

    def _make_icons(self) -> None:
        for name in ("play", "pause", "prev", "next", "shuffle", "repeat",
                     "repeat_one", "volume", "plus", "refresh", "close",
                     "tab", "play_circle", "list", "music", "search", "mic",
                     "phone"):
            self._icons[name] = icon_tk(name, 26, "#ffffff")
        self._icons["play_lg"] = icon_tk("play", 34, "#ffffff")
        self._icons["pause_lg"] = icon_tk("pause", 34, "#ffffff")
        self._icons["shuffle_on"] = icon_tk("shuffle", 26, ACCENT)
        self._icons["repeat_all"] = icon_tk("repeat", 26, ACCENT)
        self._icons["accent_music"] = icon_tk("music", 44, ACCENT)
        self._icons["mic_on"] = icon_tk("mic", 26, ACCENT)
        self._icons["mic_dim"] = icon_tk("mic", 26, TEXT_DIM)

    # ------------------------------------------------------------------ #
    #  interfaz
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        side = ctk.CTkFrame(self, width=360, corner_radius=0, fg_color=PANEL_ALT)
        side.grid(row=0, column=0, sticky="nsew")
        side.grid_propagate(False)
        side.grid_rowconfigure(3, weight=1)
        side.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(side, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(18, 12))
        ctk.CTkLabel(head, text="", image=self._icons["accent_music"]).pack(side="left")
        ctk.CTkLabel(head, text=APP_NAME,
                     font=ctk.CTkFont(size=22, weight="bold")).pack(side="left", padx=(10, 0))
        ctk.CTkLabel(head, text="v" + __version__, font=ctk.CTkFont(size=11),
                     text_color=TEXT_DIM).pack(side="left", padx=(8, 0), pady=(8, 0))

        entry_box = ctk.CTkFrame(side, fg_color="transparent")
        entry_box.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 12))
        entry_box.grid_columnconfigure(0, weight=1)
        self.entry = ctk.CTkEntry(entry_box, height=38,
                                  placeholder_text="URL de YouTube o texto a buscar…",
                                  fg_color=PANEL, border_color="#262b38")
        self.entry.grid(row=0, column=0, sticky="ew")
        self.entry.bind("<Return>", lambda e: self.on_add_url())
        ctk.CTkButton(entry_box, text="", width=42, height=38,
                      image=self._icons["plus"], fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, corner_radius=10,
                      command=self.on_add_url).grid(row=0, column=1, padx=(8, 0))

        tools = ctk.CTkFrame(side, fg_color="transparent")
        tools.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))
        tools.grid_columnconfigure(0, weight=1)
        self.segment = ctk.CTkSegmentedButton(tools, values=["Pestañas", "Mix", "Cola"],
                                              command=self._on_view,
                                              selected_color=ACCENT,
                                              selected_hover_color=ACCENT_HOVER)
        self.segment.set("Pestañas")
        self.segment.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(tools, text="", width=40, image=self._icons["refresh"],
                      fg_color=PANEL, hover_color="#242a38",
                      command=self.on_refresh).grid(row=0, column=1, padx=(8, 0))
        self.btn_mobile = ctk.CTkButton(
            tools, text="", width=40, image=self._icons["phone"],
            fg_color=PANEL, hover_color="#242a38",
            command=self.on_mobile)
        self.btn_mobile.grid(row=0, column=2, padx=(8, 0))

        self.listframe = ctk.CTkScrollableFrame(side, fg_color="transparent",
                                                label_text="", corner_radius=0)
        self.listframe.grid(row=3, column=0, sticky="nsew", padx=6, pady=(0, 4))

        self.side_note = ctk.CTkLabel(side, text="Buscando pestañas abiertas…",
                                      font=ctk.CTkFont(size=11), text_color=TEXT_DIM,
                                      justify="left", anchor="w", wraplength=324)
        self.side_note.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 10))

        self.btn_connect = ctk.CTkButton(
            side, text="Conectar navegador (pestañas en vivo)", height=32,
            font=ctk.CTkFont(size=12), fg_color=PANEL, hover_color="#242a38",
            command=self.on_connect_browser)
        self.btn_connect.grid(row=5, column=0, sticky="ew", padx=16, pady=(0, 14))

        # ---------------- panel derecho ----------------
        main = ctk.CTkFrame(self, corner_radius=0, fg_color=PANEL_ALT)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_rowconfigure(3, weight=1)
        main.grid_columnconfigure(0, weight=1)

        art_box = ctk.CTkFrame(main, fg_color="transparent")
        art_box.grid(row=0, column=0, sticky="ew", padx=40, pady=(34, 8))
        art_box.grid_columnconfigure(1, weight=1)

        self.art_bg = ctk.CTkFrame(art_box, width=200, height=200, corner_radius=18,
                                   fg_color="#0b0d12")
        self.art_bg.grid(row=0, column=0, rowspan=3, sticky="n", padx=(0, 30))
        self.art_bg.grid_propagate(False)
        self.art_label = ctk.CTkLabel(self.art_bg, text="",
                                      image=self._icons["accent_music"])
        self.art_label.place(relx=0.5, rely=0.5, anchor="center")

        self.title_lbl = ctk.CTkLabel(art_box, text="Nada en reproducción",
                                      font=ctk.CTkFont(size=24, weight="bold"),
                                      anchor="w", justify="left", wraplength=640)
        self.title_lbl.grid(row=0, column=1, sticky="ew", pady=(14, 3))
        self.meta_lbl = ctk.CTkLabel(art_box,
                                     text="Añade una URL o pulsa una pestaña abierta",
                                     font=ctk.CTkFont(size=14), anchor="w",
                                     text_color=TEXT_DIM, wraplength=640)
        self.meta_lbl.grid(row=1, column=1, sticky="ew")
        self.source_lbl = ctk.CTkLabel(art_box, text="", font=ctk.CTkFont(size=12),
                                       anchor="w", text_color=TEXT_DIM, wraplength=640)
        self.source_lbl.grid(row=2, column=1, sticky="ew", pady=(3, 12))

        prog = ctk.CTkFrame(main, fg_color="transparent")
        prog.grid(row=1, column=0, sticky="ew", padx=40, pady=(6, 4))
        prog.grid_columnconfigure(1, weight=1)
        self.time_now = ctk.CTkLabel(prog, text="0:00", width=56, anchor="w",
                                     font=ctk.CTkFont(size=12), text_color=TEXT_DIM)
        self.time_now.grid(row=0, column=0, sticky="w")
        self.seek = ctk.CTkSlider(prog, from_=0.0, to=1.0, number_of_steps=1000,
                                  button_color=ACCENT, button_hover_color=ACCENT_HOVER,
                                  progress_color=ACCENT, command=self._on_seek_change)
        self.seek.grid(row=0, column=1, sticky="ew", padx=12)
        self.seek.set(0.0)
        self.time_left = ctk.CTkLabel(prog, text="-0:00", width=56, anchor="e",
                                      font=ctk.CTkFont(size=12), text_color=TEXT_DIM)
        self.time_left.grid(row=0, column=2, sticky="e")

        ctrl = ctk.CTkFrame(main, fg_color="transparent")
        ctrl.grid(row=2, column=0, sticky="ew", padx=40, pady=(14, 4))
        ctrl.grid_columnconfigure(5, weight=1)

        self.btn_shuffle = ctk.CTkButton(
            ctrl, text="", width=44, height=44, image=self._icons["shuffle"],
            fg_color="transparent", hover_color="#1d2230", command=self.toggle_shuffle)
        self.btn_shuffle.grid(row=0, column=0, padx=(0, 6))
        ctk.CTkButton(ctrl, text="", width=44, height=44, image=self._icons["prev"],
                      fg_color="transparent", hover_color="#1d2230",
                      command=self.prev_track).grid(row=0, column=1, padx=6)
        self.btn_play = ctk.CTkButton(
            ctrl, text="", width=76, height=56, image=self._icons["play_lg"],
            fg_color=ACCENT, hover_color=ACCENT_HOVER, corner_radius=16,
            command=self.toggle_play)
        self.btn_play.grid(row=0, column=2, padx=12)
        ctk.CTkButton(ctrl, text="", width=44, height=44, image=self._icons["next"],
                      fg_color="transparent", hover_color="#1d2230",
                      command=self.next_track).grid(row=0, column=3, padx=6)
        self.btn_loop = ctk.CTkButton(
            ctrl, text="", width=44, height=44, image=self._icons["repeat"],
            fg_color="transparent", hover_color="#1d2230", command=self.toggle_loop)
        self.btn_loop.grid(row=0, column=4, padx=(6, 0))

        vol_box = ctk.CTkFrame(ctrl, fg_color="transparent")
        vol_box.grid(row=0, column=6, sticky="e")
        ctk.CTkLabel(vol_box, text="", image=self._icons["volume"]).pack(side="left",
                                                                        padx=(0, 6))
        self.vol = ctk.CTkSlider(vol_box, from_=0, to=100, width=150,
                                 button_color=ACCENT, button_hover_color=ACCENT_HOVER,
                                 command=self._on_volume)
        self.vol.pack(side="left")
        self.vol.set(80)
        self.btn_voice = ctk.CTkButton(
            vol_box, text="", width=44, height=44, image=self._icons["mic"],
            fg_color="transparent", hover_color="#1d2230",
            command=self.on_voice)
        self.btn_voice.pack(side="left", padx=(14, 0))
        try:
            self.btn_voice.configure(cursor="hand2")
        except Exception:  # noqa: BLE001
            pass

        self.now_hint = ctk.CTkLabel(main, text="", font=ctk.CTkFont(size=12),
                                     text_color=TEXT_DIM, anchor="w", justify="left",
                                     wraplength=800)
        self.now_hint.grid(row=3, column=0, sticky="new", padx=40, pady=(20, 0))

        status = ctk.CTkFrame(main, height=36, corner_radius=0, fg_color=PANEL)
        status.grid(row=4, column=0, sticky="sew")
        status.grid_columnconfigure(0, weight=1)
        self.status_var = ctk.StringVar(value="Listo.")
        self.status_lbl = ctk.CTkLabel(status, textvariable=self.status_var, anchor="w",
                                       font=ctk.CTkFont(size=12), text_color=TEXT_DIM)
        self.status_lbl.grid(row=0, column=0, sticky="ew", padx=20, pady=7)

        self._render_list()

    # ------------------------------------------------------------------ #
    #  estado y mensajes
    # ------------------------------------------------------------------ #
    def _status(self, text: str, error: bool = False) -> None:
        try:
            self.status_var.set(text)
            self.status_lbl.configure(text_color=TEXT_BAD if error else TEXT_DIM)
        except Exception:  # noqa: BLE001
            pass

    def _nudge(self, seconds: int) -> None:
        if not self.player:
            return
        total = self.player.length_ms()
        current = self.player.time_ms()
        if total <= 0:
            return
        self.player.seek_ms(max(0, min(total, current + seconds * 1000)))

    def _nudge_volume(self, delta: int) -> None:
        value = int(self.vol.get())
        value = max(0, min(100, value + delta))
        self.vol.set(value)
        if self.player:
            self.player.set_volume(value)

    # ------------------------------------------------------------------ #
    #  pestañas del navegador
    # ------------------------------------------------------------------ #
    def refresh_tabs(self) -> None:
        if self._tabs_busy:
            return
        self._tabs_busy = True

        def work():
            return collect_tabs(include_guarded=True)

        def ok(result):
            self._tabs_busy = False
            tabs, notes = result
            self.tabs = tabs
            signature = "|".join(sorted("%s\t%s" % (t.browser, t.url) for t in tabs))
            changed = signature != self._tab_signature
            self._tab_signature = signature
            if self.view == "tabs" and changed:
                self._render_list()
            yt = len(youtube_tabs(tabs))
            live = sum(1 for t in tabs if t.source == "en vivo")
            bits = ["%d pestañas · %d reproducibles" % (len(tabs), yt)]
            if live:
                bits.append("%d en vivo" % live)
            elif tabs:
                bits.append("ninguna en vivo")
            if notes:
                bits.append(notes[0])
            self.side_note.configure(text="\n".join(bits))
            self._fill_missing_titles()
            if self._mixes_busy:
                # las mezclas personales salen de estas pestañas: se refrescan
                # cuando termine la carga en marcha
                self._mixes_dirty = True
            elif self.mixes or self.mixes_ready:
                self._refresh_personal_mixes(tabs)

        def err(exc):
            self._tabs_busy = False
            self.side_note.configure(text="No se pudo leer el navegador: %s" % exc)

        self.runner.submit(work, on_ok=ok, on_err=err)

    def _fill_missing_titles(self) -> None:
        """Completa los títulos de las pestañas con la API oEmbed de YouTube."""
        pending = [t for t in self.tabs if t.is_youtube and not (t.title or "").strip()
                   and t.playable]
        if not pending:
            return
        remaining = {"n": len(pending)}
        for tab in pending[:16]:

            def work(t=tab):
                return fetch_title(t.playable)

            def ok(result, t=tab):
                title, author = result
                if title:
                    t.title = title
                    t.uploader = author
                remaining["n"] -= 1
                if remaining["n"] <= 0:
                    if self.view == "tabs":
                        self._render_list()
                    if self.mixes and not self._mixes_busy:
                        # los «Mix» se nombran con el título del vídeo
                        self._refresh_personal_mixes(self.tabs)

            def err(_e, t=tab):
                remaining["n"] -= 1

            self.runner.submit(work, on_ok=ok, on_err=err)
    def _schedule_tab_refresh(self) -> None:
        if self._tab_refresh_scheduled:
            return
        self._tab_refresh_scheduled = True

        def fire():
            self._tab_refresh_scheduled = False
            self.refresh_tabs()
            self._schedule_tab_refresh()

        self.after(self.TAB_REFRESH_MS, fire)

    def on_connect_browser(self) -> None:
        options = ["Chrome", "Edge", "Brave", "Vivaldi", "Opera", "Opera GX", "Chromium"]
        win = ctk.CTkToplevel(self)
        win.title("Conectar navegador")
        win.geometry("470x430")
        win.attributes("-topmost", True)
        win.grab_set()
        ctk.CTkLabel(win, text="Pestañas en vivo",
                     font=ctk.CTkFont(size=18, weight="bold")
                     ).pack(padx=22, pady=(22, 6), anchor="w")
        ctk.CTkLabel(
            win, justify="left", anchor="w", wraplength=424,
            font=ctk.CTkFont(size=13), text_color=TEXT_DIM,
            text=("Los navegadores basados en Chromium solo enseñan sus pestañas "
                  "abiertas si se lanzan con el puerto de depuración. Si eliges uno, "
                  "TuboPlay cerrará sus ventanas y lo abrirá de nuevo con esa "
                  "opción y con la sesión restaurada; verás tus pestañas actuales "
                  "al instante. Si prefieres no cerrarlo, cancela: seguirás viendo "
                  "las pestañas ya detectadas.")
        ).pack(padx=22, anchor="w")
        chosen = ctk.StringVar(value="Chrome")
        box = ctk.CTkFrame(win, fg_color="transparent")
        box.pack(padx=22, pady=10, anchor="w")
        for opt in options:
            ctk.CTkRadioButton(box, text=opt, variable=chosen, value=opt,
                               fg_color=ACCENT, hover_color=ACCENT_HOVER
                               ).pack(anchor="w", pady=1)

        def do_start() -> None:
            name = chosen.get()
            win.destroy()
            if browser_is_running(name):
                if not messagebox.askyesno(
                    "Cerrar " + name,
                    "%s está abierto ahora mismo.\n\n"
                    "TuboPlay necesita cerrarlo para relanzarlo con la lectura de "
                    "pestañas activada. Se restaurará tu sesión al volver a abrirlo.\n\n"
                    "¿Cerrar %s y continuar?" % (name, name),
                    parent=self,
                ):
                    self._status("Conexión cancelada: %s sigue abierto tal cual." % name)
                    return
                self._status("Cerrando %s…" % name)
                self.update_idletasks()
                ok, msg = stop_browser(name)
                if not ok:
                    self._status(msg, error=True)
                    return
            ok, msg = start_browser_with_devtools(name)
            self._status(msg, error=not ok)
            if ok:
                self.after(5000, self.refresh_tabs)

        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(8, 18))
        ctk.CTkButton(row, text="Cancelar", fg_color=PANEL, hover_color="#242a38",
                      command=win.destroy).pack(side="right", padx=(8, 0))
        ctk.CTkButton(row, text="Abrir con lectura de pestañas", fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, command=do_start).pack(side="right")

    # ------------------------------------------------------------------ #
    #  listas
    # ------------------------------------------------------------------ #
    def _on_view(self, value: str) -> None:
        self.view = {"Pestañas": "tabs", "Mix": "mix", "Cola": "queue"}[value]
        if self.view == "mix" and not self.mixes_ready:
            self._load_mixes()
        self._render_list()

    def on_refresh(self) -> None:
        self.refresh_tabs()
        if self.view == "mix":
            self._load_mixes(force=True)

    # ------------------------------------------------------------------ #
    #  control desde el móvil (servidor web local, ver web.py)
    # ------------------------------------------------------------------ #
    def _start_web(self) -> None:
        """Arranca el servidor local.  Si falla, la app sigue funcionando igual."""
        try:
            server = web_mod.WebServer(self._web_token)
            url = server.start()
        except Exception as exc:  # noqa: BLE001
            print("[TuboPlay] servidor web local:", exc)
            return
        if not url:
            print("[TuboPlay] servidor web local: ningún puerto libre")
            return
        self._web = server
        self._web_url = url
        self._web.state = self._build_web_state()
        print("[TuboPlay] control desde el móvil:", url)

    def _stop_web(self) -> None:
        if self._web is not None:
            try:
                self._web.stop()
            except Exception:  # noqa: BLE001
                pass
            self._web = None

    def _drain_web(self) -> None:
        """Vacía las órdenes del móvil y actualiza su estado (hilo de Tk)."""
        server = self._web
        if server is None:
            return
        now = time.time()
        # sólo calculamos el estado si alguien está mirando la página:
        # sin visitas no cuesta absolutamente nada
        if server.last_hit and now - server.last_hit < 180 \
                and now - self._web_state_at > 0.4:
            self._web_state_at = now
            server.state = self._build_web_state()
        while server.actions:
            try:
                action = server.actions.popleft()
            except IndexError:
                break
            try:
                self._web_action(action)
            except Exception as exc:  # noqa: BLE001
                print("[TuboPlay] orden recibida del móvil:", exc)

    def _build_web_state(self) -> Dict[str, object]:
        """Instantánea inmutable que lee el servidor desde su propio hilo."""
        tabs: List[Dict[str, object]] = []
        for tab in self.tabs:
            playable = tab.playable or ""
            if playable:
                sub = "▶ %s · %s" % (tab.uploader or tab.browser, tab.source)
                icon = "play_circle"
            elif tab.is_youtube:
                sub = "no reproducible · %s" % tab.browser
                icon = "tab"
            else:
                sub = "otra web · %s" % tab.browser
                icon = "tab"
            tabs.append({"label": (tab.label or tab.url or "Pestaña")[:80],
                         "sub": sub, "url": playable, "icon": icon})

        mixes: List[Dict[str, object]] = []
        for mix in self.mixes:
            mixes.append({"label": (mix.label or mix.url or "Mezcla")[:80],
                          "sub": mix.source or "mezcla",
                          "url": mix.playable or "", "icon": "music"})

        queue: List[Dict[str, object]] = []
        for i, track in enumerate(self.queue[:60]):
            queue.append({"label": (track.title or track.url or "…")[:80],
                          "sub": self._track_sub(track) or track.source,
                          "url": "", "icon": "music", "rm": True,
                          "cur": i == self.index})

        # huella de las listas: la página sólo repinta cuando algo cambia
        sig = 0x1F2E3D4C
        for group in (tabs, mixes, queue):
            for item in group:
                sig = (sig * 31 + hash((item["label"], item["url"]))) & 0xFFFFFFFF
        sig = (sig * 31 + (self.index + 1)) & 0xFFFFFFFF

        title = uploader = ""
        if 0 <= self.index < len(self.queue):
            track = self.queue[self.index]
            title = track.title or track.url
            uploader = track.uploader
        try:
            volume = int(self.vol.get())
        except Exception:  # noqa: BLE001
            volume = 50
        pos = dur = 0
        if self.player is not None:
            pos = int(self.player.time_ms())
            dur = int(self.player.length_ms())

        return {
            "sig": sig,
            "view": self.view,
            "playing": bool(self.playing),
            "title": title,
            "uploader": uploader,
            "pos": pos,
            "dur": dur,
            "volume": volume,
            "tabs": tabs,
            "mix": mixes,
            "queue": queue,
            "empty": {
                "tabs": "No se han detectado pestañas de YouTube abiertas.",
                "mix": "Buscando las mezclas de YouTube…",
                "queue": "La cola está vacía. Pega una URL aquí abajo.",
            },
        }

    def _web_action(self, action: Dict[str, object]) -> None:
        name = str(action.get("name") or "")
        arg = action.get("arg")
        if name == "toggle":
            self.toggle_play()
        elif name == "next":
            self.next_track()
        elif name == "prev":
            self.prev_track()
        elif name == "vol_up":
            self._nudge_volume(10)
        elif name == "vol_down":
            self._nudge_volume(-10)
        elif name == "remove":
            self.remove_track(int(arg or 0))
        elif name == "play_index":
            i = int(arg or 0)
            if 0 <= i < len(self.queue):
                self.play_index(i)
        elif name == "set_view":
            label = {"tabs": "Pestañas", "mix": "Mix",
                     "queue": "Cola"}.get(str(arg))
            if label and label != self.segment.get():
                self.segment.set(label)
                self._on_view(label)
        elif name == "add":
            text = str(action.get("text") or "").strip()
            if text:
                self.enqueue(text, autoplay=True)
        elif name == "play_url":
            self._play_web_url(str(arg or ""))

    def _play_web_url(self, url: str) -> None:
        if not url:
            return
        for tab in list(self.tabs) + list(self.mixes):
            if tab.url == url or tab.playable == url:
                self.play_tab(tab)
                return
        for i, track in enumerate(self.queue):
            if track.url == url:
                self.play_index(i)
                return
        self.enqueue(url, autoplay=True)

    def on_mobile(self) -> None:
        """Ventana con la dirección que hay que abrir en el teléfono."""
        if not self._web_url:
            messagebox.showinfo(
                "Control desde el móvil",
                "El servidor local no está activo.\n\n"
                "Cierra TuboPlay, comprueba que otro programa no use el "
                "puerto 8765 y vuelve a abrirlo.", parent=self)
            return
        win = ctk.CTkToplevel(self)
        win.title("Control desde el móvil")
        win.geometry("480x470")
        win.transient(self)
        win.configure(fg_color=PANEL_ALT)
        win.resizable(False, False)
        x = self.winfo_rootx() + max(0, (self.winfo_width() - 480) // 2)
        y = self.winfo_rooty() + 60
        win.geometry("+%d+%d" % (x, y))

        pad = {"padx": 22}
        ctk.CTkLabel(win, text="📱  Reproduce en el ordenador, manda desde el móvil",
                     font=ctk.CTkFont(size=15, weight="bold"),
                     ).pack(pady=(24, 6), **pad)
        ctk.CTkLabel(
            win, justify="left", anchor="w", wraplength=430, text_color=TEXT_DIM,
            font=ctk.CTkFont(size=13),
            text=("Abre esta dirección en el navegador del teléfono, con el "
                  "móvil y el PC en la misma red Wi‑Fi:\n\n"
                  "1.  Copia la dirección de abajo.\n"
                  "2.  En el móvil: navegador → pegar → ir.\n"
                  "3.  Opción → «Añadir a pantalla de inicio» y quedará como "
                  "una app.")
        ).pack(fill="x", **pad)

        field = ctk.CTkEntry(win, height=38, font=ctk.CTkFont(size=13))
        field.pack(fill="x", padx=22, pady=(14, 8))
        field.insert(0, self._web_url)
        field.configure(state="readonly")

        def copy_url() -> None:
            win.clipboard_clear()
            win.clipboard_append(self._web_url)
            self._status("Dirección copiada: pégala en el móvil.")
            btn_copy.configure(text="Copiada ✓")

        btn_copy = ctk.CTkButton(
            win, text="Copiar dirección", height=34, fg_color=ACCENT,
            hover_color=ACCENT_HOVER, command=copy_url)
        btn_copy.pack(fill="x", padx=22)

        ctk.CTkLabel(
            win, justify="left", anchor="w", wraplength=430, text_color=TEXT_DIM,
            font=ctk.CTkFont(size=12),
            text=("La dirección lleva una clave privada: sin ella no se ve nada. "
                  "Si cambias de red, vuelve a abrir esta ventana. El servidor "
                  "se apaga al cerrar TuboPlay.")
        ).pack(fill="x", padx=22, pady=(14, 0))

        ctk.CTkButton(win, text="Cerrar", height=34, fg_color=PANEL,
                      hover_color="#242a38", command=win.destroy
                      ).pack(fill="x", padx=22, pady=(18, 20))
        win.focus_force()

    def _render_list(self) -> None:
        for child in self.listframe.winfo_children():
            child.destroy()
        self._thumb_refs.clear()
        self._queue_rows = []
        self._rows_shown = 0
        if self.view == "tabs":
            self._render_tabs()
        elif self.view == "mix":
            self._render_mixes()
        else:
            self._render_queue()

    @staticmethod
    def _fonts():
        """Fuentes Tk de las filas: ``(título, título negrita, subtítulo)``."""
        return _fonts()

    def _refresh_rows(self) -> None:
        """
        Actualiza el estado de la cola (qué suena, cuál está en pausa) **sin
        reconstruir las filas**: repintar 200 filas se notaba en cada cambio
        de canción.  Solo se reconstruye si cambió el número de pistas.
        """
        if self.view != "queue" or self._queue_len != len(self.queue):
            self._render_list()
            return
        for i, handles in enumerate(self._queue_rows):
            self._update_queue_row(i, handles)

    def _section(self, text: str, count: int) -> None:
        label = "%s (%d)" % (text, count) if count else text
        tk.Label(self.listframe, text=label, anchor="w", bg=PANEL_ALT, fg=TEXT_DIM,
                 font=self._fonts()[2]
                 ).pack(fill="x", padx=10, pady=(12, 3))

    def _empty(self, text: str) -> None:
        tk.Label(self.listframe, text=text, anchor="center", justify="center",
                 bg=PANEL_ALT, fg=TEXT_DIM, font=self._fonts()[0], wraplength=290
                 ).pack(fill="x", padx=16, pady=40)

    def _more_rows(self, shown: int, total: int) -> None:
        """Pie con el botón «mostrar más» cuando la lista está recortada."""
        if shown >= total:
            return
        rest = total - shown
        label = tk.Label(self.listframe, text="↓  Mostrar %d más" % rest,
                         anchor="center", bg=PANEL, fg=TEXT_DIM,
                         font=self._fonts()[2], cursor="hand2", pady=7)
        label.pack(fill="x", padx=6, pady=(6, 10))

        def over(_e):
            label.configure(fg=ACCENT, bg=ROW_HOVER)

        def out(_e):
            label.configure(fg=TEXT_DIM, bg=PANEL)

        label.bind("<Enter>", over)
        label.bind("<Leave>", out)
        label.bind("<Button-1>", lambda _e: self._show_more())

    def _show_more(self) -> None:
        self._row_limit += ROW_LIMIT
        self._render_list()

    def _render_tabs(self) -> None:
        if not self.tabs:
            self._empty("No se han detectado pestañas.\n\nAbre YouTube en tu navegador "
                        "y pulsa ↻, usa el botón de abajo, o pega una URL arriba.")
            return
        youtube = [t for t in self.tabs if t.is_youtube]
        others = [t for t in self.tabs if not t.is_youtube]
        shown = 0
        limit = self._row_limit
        if youtube:
            self._section("Con música / YouTube", len(youtube))
            for tab in youtube:
                if shown >= limit:
                    break
                self._tab_row(tab)
                shown += 1
        else:
            self._section("Ninguna pestaña de YouTube abierta", 0)
        if others and shown < limit:
            self._section("Otras pestañas", len(others))
            for tab in others:
                if shown >= limit:
                    break
                self._tab_row(tab)
                shown += 1
        self._rows_shown = shown
        self._more_rows(shown, len(self.tabs))

    def _tab_row(self, tab: Tab) -> None:
        can_play = bool(tab.playable)
        row = _Row(self.listframe,
                   command=(lambda t=tab: self.play_tab(t)) if can_play else None)
        row.pack(fill="x", padx=6, pady=4)
        tk.Label(row, image=_photo("play_circle" if can_play else "tab"),
                 bg=PANEL, bd=0, cursor="hand2" if can_play else ""
                 ).pack(side="left", padx=(12, 10), pady=8)
        body = tk.Frame(row, bg=PANEL, bd=0)
        body.pack(side="left", fill="both", expand=True, pady=8)
        title_font, _bold, sub_font = self._fonts()
        tk.Label(body, text=(tab.label or tab.url)[:70], anchor="w", justify="left",
                 bg=PANEL, fg="#ffffff", font=title_font, wraplength=232
                 ).pack(fill="x")
        if can_play:
            sub = "▶ reproducir · %s · %s" % (tab.uploader or tab.browser, tab.source)
        elif tab.is_youtube:
            sub = "no reproducible · %s" % tab.browser
        else:
            sub = "otra web · %s · %s" % (tab.browser, tab.source)
        tk.Label(body, text=sub, anchor="w", bg=PANEL, fg=TEXT_DIM, font=sub_font
                 ).pack(fill="x")
        row.bind_click(row)

    def _load_mixes(self, force: bool = False) -> None:
        """
        Carga la pestaña «Mix».

        Las mezclas personales salen al instante (salen de las pestañas que ya
        tienes abiertas); las que propone YouTube piden una petición HTTP y
        llegan después.
        """
        if self._mixes_busy:
            return
        if self.mixes_ready and not force:
            return
        self.mixes = personal_mixes(self.tabs)
        self.mixes_ready = bool(self.mixes)
        if self.view == "mix":
            self._render_list()
        self._mixes_busy = True

        def work():
            # se leen las pestañas al ejecutar: así recogen las que hayan
            # llegado mientras se preparaba la petición
            return build_mixes(list(self.tabs))

        def ok(result):
            self._mixes_busy = False
            personal, proposed = result
            if self._mixes_dirty:
                # llegaron pestañas mientras cargaba: se vuelven a montar
                self._mixes_dirty = False
                personal = personal_mixes(self.tabs)
            merged = self._merge_mixes(personal, proposed)
            self.mixes = merged
            self.mixes_ready = True
            if self.view == "mix":
                self._render_list()
            if not merged:
                self._status("YouTube no devolvió mezclas.", error=True)

        def err(exc):
            self._mixes_busy = False
            self.mixes_ready = bool(self.mixes)
            if self._mixes_dirty:
                self._mixes_dirty = False
                self.mixes = self._merge_mixes(personal_mixes(self.tabs), self.mixes)
            if self.view == "mix":
                self._render_list()
            self._status("No se pudieron leer las mezclas de YouTube: %s" % exc,
                         error=True)

        self.runner.submit(work, on_ok=ok, on_err=err)

    def _render_mixes(self) -> None:
        if not self.mixes:
            if self._mixes_busy:
                self._empty("Buscando las mezclas de YouTube…")
            else:
                self._empty("No hay mezclas todavía.\n\nPulsa ↻ para volver a "
                            "buscar, o abre YouTube en tu navegador para que "
                            "TuboPlay pueda armar «Mix» con tus vídeos.")
            return
        personal = [m for m in self.mixes if m.source == "de tus pestañas abiertas"]
        proposed = [m for m in self.mixes if m.source == "que propone YouTube"]
        shown = 0
        limit = self._row_limit

        def add(group):
            nonlocal shown
            for tab in group:
                if shown >= limit:
                    return
                self._tab_row(tab)
                shown += 1

        if personal:
            self._section("Tus mezclas (sacadas de tus pestañas)", len(personal))
            add(personal)
        if proposed and shown < limit:
            self._section("Mezclas que te propone YouTube", len(proposed))
            add(proposed)
        if not personal and not proposed:
            self._section("Tus mezclas", len(self.mixes))
            add(self.mixes)
        self._rows_shown = shown
        self._more_rows(shown, len(self.mixes))
        if shown:
            tk.Label(self.listframe,
                     text="Di «pon play» al micrófono (🎯) para que empiece "
                          "una de estas mezclas.",
                     anchor="w", justify="left", wraplength=300, bg=PANEL_ALT,
                     fg=TEXT_DIM, font=self._fonts()[2]
                     ).pack(fill="x", padx=12, pady=(14, 8))

    # -- cola de la vista «Cola» ------------------------------------------ #
    @staticmethod
    def _track_sub(track: Track) -> str:
        parts = []
        if track.uploader:
            parts.append(track.uploader)
        dur = track.duration_label()
        if dur:
            parts.append(dur)
        if track.error:
            parts.append("⚠ " + track.error)
        elif not track.ready:
            parts.append("preparando…")
        elif track.source:
            parts.append(track.source)
        return " · ".join(parts)

    def _render_queue(self) -> None:
        self._queue_len = len(self.queue)
        if not self.queue:
            self._empty("La cola está vacía.\n\nAñade una URL o pulsa una pestaña "
                        "abierta en la vista “Pestañas”.")
            return
        self._section("Cola de reproducción", len(self.queue))
        limit = self._row_limit
        if 0 <= self.index < limit:      # la pista sonando siempre se ve
            limit = max(limit, self.index + 12)
        shown = 0
        for i, track in enumerate(self.queue):
            if i >= limit:
                break
            self._queue_row(i, track)
            shown += 1
        self._rows_shown = shown
        self._more_rows(shown, len(self.queue))

    def _queue_row(self, i: int, track: Track) -> None:
        current = i == self.index
        base = ROW_CURRENT if current else PANEL
        f_title, f_bold, f_sub = self._fonts()
        row = _Row(self.listframe, command=lambda k=i: self.play_index(k),
                   base=base)
        row.pack(fill="x", padx=6, pady=4)

        art = tk.Label(row, image=_photo("music", 30), bg=base, bd=0,
                       cursor="hand2", width=44)
        art.pack(side="left", padx=(10, 10), pady=8)
        if track.thumb:
            self._thumb_into(art, track.thumb)

        body = tk.Frame(row, bg=base, bd=0)
        body.pack(side="left", fill="both", expand=True, pady=8)
        title = tk.Label(body, text=(track.title or track.url)[:70], anchor="w",
                         justify="left", bg=base, fg="#ffffff",
                         font=f_bold if current else f_title, wraplength=210)
        title.pack(fill="x")
        sub = tk.Label(body, text=self._track_sub(track), anchor="w", bg=base,
                       fg=TEXT_BAD if track.error else TEXT_DIM, font=f_sub)
        sub.pack(fill="x")

        marker = tk.Label(row, text="▶", bg=base, fg=ACCENT, font=f_bold,
                          width=3, cursor="hand2")
        close = tk.Label(row, image=_photo("close", 16), bg=base, bd=0,
                         cursor="hand2", width=30)
        close.bind("<Button-1>", lambda _e, k=i: self.remove_track(k))
        if current and self.playing:
            marker.pack(side="right", padx=(0, 6), pady=14)
        else:
            close.pack(side="right", padx=(0, 8), pady=14)
        row.bind_click(row)
        # el clic de la ✕ no debe arrancar también la pista
        close.bind("<Button-1>", lambda _e, k=i: self.remove_track(k))
        self._queue_rows.append({"row": row, "title": title, "sub": sub,
                                 "marker": marker, "close": close,
                                 "side": marker if (current and self.playing)
                                 else close})

    def _update_queue_row(self, i: int, h: dict) -> None:
        """Cambia el estado de una fila ya construida (barato: sin recrear)."""
        track = self.queue[i]
        current = i == self.index
        base = ROW_CURRENT if current else PANEL
        f_title, f_bold, f_sub = self._fonts()
        h["row"].set_base(base)
        h["title"].configure(font=f_bold if current else f_title, bg=base,
                             fg="#ffffff")
        h["sub"].configure(text=self._track_sub(track), bg=base,
                           fg=TEXT_BAD if track.error else TEXT_DIM)
        side = h["marker"] if (current and self.playing) else h["close"]
        if side is not h["side"]:
            h["side"].pack_forget()
            if side is h["marker"]:
                side.pack(side="right", padx=(0, 6), pady=14)
            else:
                side.pack(side="right", padx=(0, 8), pady=14)
            h["side"] = side

    def _thumb_into(self, label: tk.Label, url: str) -> None:
        if not url:
            return

        def work():
            return cached_image_path(url)

        def ok(path):
            if not path:
                return
            try:
                if not label.winfo_exists():
                    return
                img = ImageTk.PhotoImage(Image.open(path).resize(
                    (44, 44), Image.LANCZOS))
                self._thumb_refs.append(img)
                label.configure(image=img, text="")
            except Exception:  # noqa: BLE001
                pass

        self.runner.submit(work, on_ok=ok)

    # ------------------------------------------------------------------ #
    #  cola de reproducción
    # ------------------------------------------------------------------ #
    def on_add_url(self) -> None:
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self.enqueue(text, autoplay=True)

    def enqueue(self, text: str, source: str = "", title: str = "",
                autoplay: bool = False) -> None:
        url = canonical_url(text) if is_url(text) else None
        is_search = False
        if url is None:
            if is_url(text):
                self._status("Esa URL no es un vídeo ni una playlist de YouTube.",
                             error=True)
                return
            is_search = True

        self._status("Leyendo %s…" % (url or "“%s”" % text))
        is_mix = bool(url) and is_mix_url(url)
        kind = source or ("mix" if is_mix else "URL")

        def work():
            if is_search:
                return search(text, source="búsqueda")
            vid = video_id(url)
            if vid and not re.search(r"/playlist", url) and not is_mix:
                # vídeo suelto: el título llegará al resolverlo (una sola llamada)
                return [Track(url=url, title=title or "",
                              source=source or "URL")]
            # playlist normal o mezcla infinita: hay que expandirla.
            # Las mezclas entran por tandas: 200 vídeos cuestan ~13 s y el
            # resto de la tanda llega después, en segundo plano.
            return probe(url, source=kind,
                         limit=MIX_BATCH if is_mix else None)

        def ok(tracks: List[Track]):
            if not tracks:
                self._status("No se encontró nada.", error=True)
                return
            first_index = -1
            added = 0
            for track in tracks:
                existing = next((i for i, t in enumerate(self.queue)
                                 if t.url == track.url), -1)
                if existing >= 0:
                    if first_index < 0:
                        first_index = existing
                    continue
                if first_index < 0:
                    first_index = len(self.queue)
                self.queue.append(track)
                added += 1
            if first_index < 0:
                first_index = 0
            self._status("%d pista(s) nueva(s) en la cola." % added)
            if self.view == "queue":
                self._refresh_rows()
            if is_mix:
                self._extend_mix(url, kind)
            if autoplay:
                self.play_index(first_index)

        def err(exc: BaseException):
            self._status("No se pudo añadir: %s" % exc, error=True)

        self.audio_runner.submit(work, on_ok=ok, on_err=err)

    def _extend_mix(self, url: str, kind: str) -> None:
        """
        Pide el resto de la mezcla **en segundo plano**.

        La primera tanda (`MIX_BATCH`) ya está sonando; esta segunda petición
        añade al final las que falten para que la mezcla no se quede corta.
        """
        if self._mix_extending or len(self.queue) >= MIX_MAX:
            return
        self._mix_extending = True

        def work():
            return probe(url, source=kind, limit=MIX_EXTEND)

        def ok(more: List[Track]):
            self._mix_extending = False
            if not more:
                return
            have = {t.url for t in self.queue}
            extra = [t for t in more if t.url not in have]
            extra = extra[:max(0, MIX_MAX - len(self.queue))]
            if not extra:
                return
            self.queue.extend(extra)
            if self.view == "queue":
                self._refresh_rows()
            self._status("Mezcla completa: %d canciones en la cola."
                         % len(self.queue))

        def err(_exc):
            self._mix_extending = False

        self.audio_runner.submit(work, on_ok=ok, on_err=err)

    def play_tab(self, tab: Tab) -> None:
        playable = tab.playable
        if not playable:
            self._status("Esa pestaña no es un vídeo reproducible.", error=True)
            return
        for i, track in enumerate(self.queue):
            if track.url == playable:
                self.play_index(i)
                return
        self.enqueue(playable, source="pestaña %s" % tab.browser, title=tab.label,
                     autoplay=True)

    def remove_track(self, i: int) -> None:
        if not (0 <= i < len(self.queue)):
            return
        was_current = i == self.index
        self.queue.pop(i)
        if i < self.index:
            self.index -= 1
        elif was_current:
            self._play_token += 1
            if self.player:
                self.player.stop()
            self.playing = False
            self.btn_play.configure(image=self._icons["play_lg"])
            if self.queue:
                self.index = min(i, len(self.queue) - 1)
                self.play_index(self.index)
            else:
                self.index = -1
                self._update_now_playing()
        if self.view == "queue":
            self._refresh_rows()
        self._update_now_hint()

    def clear_queue(self) -> None:
        self.queue.clear()
        self.index = -1
        self._play_token += 1
        if self.player:
            self.player.stop()
        self.playing = False
        self.btn_play.configure(image=self._icons["play_lg"])
        self._render_list()
        self._update_now_playing()

    # ------------------------------------------------------------------ #
    #  reproducción
    # ------------------------------------------------------------------ #
    def _ensure_player(self) -> bool:
        if self.player is not None:
            return True
        if not self.vlc_ok:
            self._status("No hay motor de audio (VLC): %s" % self.vlc_msg, error=True)
            return False
        try:
            self.player = VlcPlayer()
            return True
        except Exception as exc:  # noqa: BLE001
            self.vlc_ok = False
            self.vlc_msg = str(exc)
            self._status("No se pudo iniciar VLC: %s" % exc, error=True)
            return False

    def play_index(self, i: int) -> None:
        if not (0 <= i < len(self.queue)):
            return
        if not self._ensure_player():
            return
        self.index = i
        track = self.queue[i]
        self._play_token += 1
        token = self._play_token
        self._update_now_playing(preparing=not track.ready)
        if self.view == "queue":
            self._refresh_rows()

        if track.ready:
            self._start_playback(track, token)
            return

        self._status("Preparando “%s”…" % track.title)

        def work():
            return resolve(track)

        def ok(resolved: Track):
            if token != self._play_token or self.index != i:
                return
            self._start_playback(resolved, token)

        def err(exc: BaseException):
            if token != self._play_token:
                return
            track.error = str(exc)
            self._status("Error: %s" % track.error, error=True)
            self._failures += 1
            if self.view == "queue":
                self._refresh_rows()
            if self._failures <= max(1, len(self.queue)):
                self.after(800, lambda: self.next_track(manual=False))

        self.audio_runner.submit(work, on_ok=ok, on_err=err)

    def _start_playback(self, track: Track, token: int) -> None:
        if token != self._play_token:
            return
        try:
            self.player.play(track.stream_url, track.stream_headers,
                             start_volume=int(self.vol.get()))
        except Exception as exc:  # noqa: BLE001
            self._status("VLC no pudo reproducir: %s" % exc, error=True)
            return
        self._failures = 0
        self.playing = True
        self.btn_play.configure(image=self._icons["pause_lg"])
        self._update_now_playing()
        if self.view == "queue":
            self._refresh_rows()
        self._status("Reproduciendo: %s" % track.title)
        self._update_now_hint()
        self._prefetch_neighbours()

    def _prefetch_neighbours(self) -> None:
        if self.loop_mode == "one":
            return
        order = self._neighbour_order()
        if not order:
            return
        nxt = order[0]
        if 0 <= nxt < len(self.queue) and not self.queue[nxt].ready:
            track = self.queue[nxt]

            def work():
                return resolve(track)

            def ok(_t):
                if self.view == "queue":
                    self._refresh_rows()

            def err(_e):
                pass

            self.audio_runner.submit(work, on_ok=ok, on_err=err)

    def _neighbour_order(self) -> List[int]:
        n = len(self.queue)
        if n == 0:
            return []
        if self.shuffle and n > 1:
            others = [i for i in range(n) if i != self.index]
            random.shuffle(others)
            return others
        if self.index < 0:
            return list(range(n))
        if self.loop_mode == "all":
            return [(self.index + 1) % n]
        return list(range(self.index + 1, n))

    def next_track(self, manual: bool = True) -> None:
        if not self.queue:
            self.playing = False
            self.btn_play.configure(image=self._icons["play_lg"])
            return
        if not manual and self.loop_mode == "one" and self.index >= 0:
            self.play_index(self.index)
            return
        order = self._neighbour_order()
        if not order:
            self.playing = False
            self.btn_play.configure(image=self._icons["play_lg"])
            self._status("Fin de la cola.")
            return
        self.play_index(order[0])

    def prev_track(self) -> None:
        if not self.queue:
            return
        if self.player and self.player.time_ms() > 4000:
            self.player.seek_ms(0)
            return
        if self.index > 0:
            self.play_index(self.index - 1)
        else:
            self.play_index(0)

    def toggle_play(self) -> None:
        if not self.queue:
            self._pon_play()
            return
        if self.index < 0:
            self.play_index(0)
            return
        if not self._ensure_player():
            return
        track = self.queue[self.index]
        if not self.playing and not track.ready:
            self.play_index(self.index)
            return
        playing = self.player.toggle()
        self.playing = playing
        self.btn_play.configure(image=self._icons["pause_lg"] if playing
                                else self._icons["play_lg"])
        self._status("En pausa." if not playing else "Reproduciendo: %s" % track.title)
        if self.view == "queue":
            self._refresh_rows()

    # ------------------------------------------------------------------ #
    #  «pon play»: arranca una mezcla
    # ------------------------------------------------------------------ #
    def _pon_play(self) -> None:
        """Sin cola: carga la primera mezcla y la pone a sonar."""
        if self.mixes:
            self.play_tab(self.mixes[0])
            return
        open_tabs = youtube_tabs(self.tabs)
        self._status("Buscando las mezclas de YouTube…")

        def work():
            return build_mixes(self.tabs)

        def ok(result):
            personal, proposed = result
            merged = self._merge_mixes(personal, proposed)
            self.mixes = merged
            self.mixes_ready = True
            if self.view == "mix":
                self._render_list()
            if merged:
                self.play_tab(merged[0])
            elif open_tabs:
                self.play_tab(open_tabs[0])
            else:
                self._status("No hay nada que reproducir todavía.", error=True)

        def err(exc):
            if open_tabs:
                self._status("Sin mezclas (%s); uso tu pestaña abierta." % exc)
                self.play_tab(open_tabs[0])
            else:
                self._status("No se pudieron cargar las mezclas: %s" % exc, error=True)

        self.runner.submit(work, on_ok=ok, on_err=err)

    @staticmethod
    def _merge_mixes(*groups) -> List[Tab]:
        seen, out = set(), []
        for group in groups:
            for item in group:
                if item.url in seen:
                    continue
                seen.add(item.url)
                out.append(item)
        return out

    def _refresh_personal_mixes(self, tabs: List[Tab]) -> None:
        """Vuelve a montar las mezclas personales cuando cambian las pestañas
        (o cuando llega el título real de un vídeo)."""
        personal = personal_mixes(tabs)
        proposed = [m for m in self.mixes if m.source == "que propone YouTube"]
        merged = self._merge_mixes(personal, proposed)
        key = lambda t: (t.url, t.title)  # noqa: E731
        if [key(m) for m in merged] == [key(m) for m in self.mixes]:
            return
        self.mixes = merged
        if self.view == "mix":
            self._render_list()

    def toggle_shuffle(self) -> None:
        self.shuffle = not self.shuffle
        self.btn_shuffle.configure(image=self._icons["shuffle_on" if self.shuffle
                                                    else "shuffle"])
        self._status("Aleatorio activado." if self.shuffle else "Aleatorio desactivado.")
        self._update_now_hint()

    def toggle_loop(self) -> None:
        self.loop_mode = {"off": "all", "all": "one", "one": "off"}[self.loop_mode]
        image = {"off": "repeat", "all": "repeat_all", "one": "repeat_one"}[self.loop_mode]
        self.btn_loop.configure(image=self._icons[image])
        self._status({"off": "Repetición desactivada.",
                      "all": "Repetir toda la cola.",
                      "one": "Repetir la pista actual."}[self.loop_mode])
        self._update_now_hint()

    # ------------------------------------------------------------------ #
    #  control por voz
    # ------------------------------------------------------------------ #
    def on_voice(self) -> None:
        """Alterna el micrófono (instala antes lo que haga falta)."""
        if self.voice.listening:
            self.voice.stop()
            self.btn_voice.configure(image=self._icons["mic"])
            self._status("Voz desactivada.")
            return
        if voice_mod.missing():
            self._install_voice()
            return
        self._start_voice()

    def _start_voice(self) -> None:
        ok, msg = self.voice.start()
        if not ok:
            self.btn_voice.configure(image=self._icons["mic"])
            self._status(msg, error=True)
            return
        self.btn_voice.configure(image=self._icons["mic_on"])
        self._status("Voz activa: di «pon play», «pausa», «siguiente»…")

    def _install_voice(self) -> None:
        if self._voice_install_running:
            return
        if not messagebox.askyesno(
            "Activar control por voz",
            "Para escucharte TuboPlay necesita instalar el reconocimiento de "
            "voz (unos 40 MB, se hace una sola vez y funciona sin conexión).\n\n"
            "¿Descargarlo ahora?",
            parent=self,
        ):
            self._status("Control por voz cancelado.")
            return
        self._voice_install_running = True
        self.btn_voice.configure(image=self._icons["mic_dim"])
        self._status("Instalando el control por voz…")

        def work():
            return voice_mod.install()

        def ok(result):
            self._voice_install_running = False
            good, msg = result
            self._status(msg, error=not good)
            if good:
                self._start_voice()

        def err(exc):
            self._voice_install_running = False
            self._status("No se pudo activar la voz: %s" % exc, error=True)

        self.runner.submit(work, on_ok=ok, on_err=err)

    def _drain_voice(self) -> None:
        for kind, text in self.voice.poll():
            if kind == "error":
                self.btn_voice.configure(image=self._icons["mic"])
                self._status("Voz: %s" % text, error=True)
                continue
            if not text:
                continue
            command = voice_mod.parse_command(text)
            if command:
                self._status("Voz: «%s»" % text)
                self._run_voice_command(command)
            else:
                # solo avisamos de vez en cuando: el micrófono oye mucho ruido
                now = time.time()
                if now - self._voice_last_noise > 4.0:
                    self._voice_last_noise = now
                    self._status("Voz: «%s» (no era una orden)" % text)

    def _run_voice_command(self, command: str) -> None:
        if command == "play":
            self.toggle_play()
        elif command == "pause":
            if self.queue and self.playing:
                self.toggle_play()
        elif command == "next":
            self.next_track()
        elif command == "prev":
            self.prev_track()
        elif command == "vol_up":
            self._nudge_volume(10)
        elif command == "vol_down":
            self._nudge_volume(-10)

    # ------------------------------------------------------------------ #
    #  progreso y volumen
    # ------------------------------------------------------------------ #
    def _on_seek_change(self, value: float) -> None:
        self._seeking = True
        self._seek_value = float(value)
        self._seek_last_move = time.time()

    def _on_volume(self, value: float) -> None:
        if self.player:
            self.player.set_volume(int(value))

    # ------------------------------------------------------------------ #
    #  bucles de actualización
    # ------------------------------------------------------------------ #
    def _pump(self) -> None:
        try:
            self.runner.pump()
            self.audio_runner.pump()
        finally:
            self.after(self.PUMP_MS, self._pump)

    def _tick(self) -> None:
        try:
            self._tick_body()
        except Exception as exc:  # noqa: BLE001
            print("[TuboPlay] tick:", exc)
        finally:
            self.after(self.TICK_MS, self._tick)

    def _tick_body(self) -> None:
        self._drain_voice()
        self._drain_web()

        # se aceptó una búsqueda manual en la barra de progreso
        if self._seeking and time.time() - self._seek_last_move > 0.25:
            self._seeking = False
            if self.player:
                self.player.seek_fraction(self._seek_value)

        if self.player is None:
            return

        event = self.player.poll()
        if event == "ended":
            if self.loop_mode == "one" and 0 <= self.index < len(self.queue):
                self.play_index(self.index)
            else:
                self.next_track(manual=False)
            return
        if event == "error":
            self._failures += 1
            if 0 <= self.index < len(self.queue):
                track = self.queue[self.index]
                track.error = "no se pudo reproducir"
                if self.view == "queue":
                    self._refresh_rows()
            self._status("No se pudo reproducir; pasando a la siguiente…", error=True)
            if self._failures <= max(1, len(self.queue)):
                self.after(600, lambda: self.next_track(manual=False))
            else:
                self.playing = False
                self.btn_play.configure(image=self._icons["play_lg"])
            return

        total = self.player.length_ms()
        current = self.player.time_ms()

        if self._seeking:
            if total > 0:
                self.time_now.configure(text=format_time(self._seek_value * total / 1000))
            return
        if total > 0:
            self.seek.set(current / total)
            self.time_now.configure(text=format_time(current / 1000))
            self.time_left.configure(text="-%s" % format_time((total - current) / 1000))
            if 0 <= self.index < len(self.queue) and self.queue[self.index].duration <= 0:
                self.queue[self.index].duration = total / 1000

    # ------------------------------------------------------------------ #
    #  panel "ahora suena"
    # ------------------------------------------------------------------ #
    def _update_now_playing(self, preparing: bool = False) -> None:
        if not (0 <= self.index < len(self.queue)):
            self.title_lbl.configure(text="Nada en reproducción")
            self.meta_lbl.configure(text="Añade una URL o pulsa una pestaña abierta")
            self.source_lbl.configure(text="")
            self.art_label.configure(image=self._icons["accent_music"], text="")
            self.seek.set(0.0)
            self.time_now.configure(text="0:00")
            self.time_left.configure(text="-0:00")
            self._update_now_hint()
            return
        track = self.queue[self.index]
        display = track.title or "YouTube"
        if preparing:
            display = "Preparando…"
        self.title_lbl.configure(text=display)
        meta = track.uploader or ""
        dur = track.duration_label()
        if dur:
            meta = ("%s · %s" % (meta, dur)).strip(" ·")
        self.meta_lbl.configure(text=meta or "YouTube")
        self.source_lbl.configure(text=track.source or "")
        self._load_art(track)
        self._update_now_hint()

    def _load_art(self, track: Track) -> None:
        if not track.thumb:
            self.art_label.configure(image=self._icons["accent_music"], text="")
            return

        def work():
            return cached_image_path(track.thumb)

        def ok(path):
            if not path:
                return
            try:
                if not (0 <= self.index < len(self.queue)) or self.queue[self.index] is not track:
                    return
                img = ctk.CTkImage(light_image=Image.open(path),
                                   dark_image=Image.open(path), size=(200, 200))
                self._thumb_refs.append(img)
                self.art_label.configure(image=img, text="")
            except Exception:  # noqa: BLE001
                pass

        self.runner.submit(work, on_ok=ok)

    def _update_now_hint(self) -> None:
        if not self.queue:
            self.now_hint.configure(text="")
            return
        start = max(0, min(self.index, len(self.queue) - 1))
        upcoming = [t.title for t in self.queue[start + 1:start + 4]]
        if upcoming:
            text = "A continuación: " + ", ".join(upcoming)
        else:
            text = "Última de la cola: %s" % self.queue[start].title
        flags = []
        if self.shuffle:
            flags.append("aleatorio")
        if self.loop_mode == "all":
            flags.append("repetir cola")
        elif self.loop_mode == "one":
            flags.append("repetir pista")
        if flags:
            text += "   (" + " · ".join(flags) + ")"
        self.now_hint.configure(text=text)

    # ------------------------------------------------------------------ #
    def _on_close(self) -> None:
        self._play_token += 1
        try:
            self.voice.stop()
        except Exception:  # noqa: BLE001
            pass
        self._stop_web()
        for fn in (self.runner.shutdown, self.audio_runner.shutdown):
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass
        if self.player:
            try:
                self.player.release()
            except Exception:  # noqa: BLE001
                pass
        self.destroy()
