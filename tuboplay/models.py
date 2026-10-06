"""Modelos de datos y utilidades sobre URL de YouTube."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Optional

_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.I)
_HOST_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://([^/?#]+)", re.I)
_YT_DOMAINS = ("youtube.com", "youtu.be", "youtube-nocookie.com")


def host_of(url: str) -> str:
    """Devuelve el host en minúsculas de una URL ('' si no se puede leer)."""
    m = _HOST_RE.match((url or "").strip())
    if not m:
        return ""
    authority = m.group(1)
    authority = authority.rsplit("@", 1)[-1]
    return authority.split(":", 1)[0].lower()


def is_url(text: str) -> bool:
    return bool(_SCHEME_RE.match((text or "").strip()))


def is_youtube(url: str) -> bool:
    h = host_of(url)
    if not h:
        return False
    return any(h == d or h.endswith("." + d) for d in _YT_DOMAINS)


def is_youtube_music(url: str) -> bool:
    return host_of(url).endswith("music.youtube.com")


def video_id(url: str) -> Optional[str]:
    """Extrae el id de un vídeo (watch, youtu.be, shorts, live, embed)."""
    u = (url or "").strip()
    m = re.search(r"[?&]v=([\w\-]{6,})", u)
    if m:
        return m.group(1)
    m = re.search(r"(?:youtu\.be/|/(?:embed|shorts|live|v)/)([\w\-]{6,})", u)
    if m:
        return m.group(1)
    return None


def playlist_id(url: str) -> Optional[str]:
    m = re.search(r"[?&]list=([\w\-]{5,})", url or "")
    return m.group(1) if m else None


def canonical_url(url: str) -> Optional[str]:
    """
    Convierte cualquier URL de YouTube en una URL canónica reproducible.

    watch?v=X[&list=Y]  -> https://www.youtube.com/watch?v=X
    youtu.be/X          -> https://www.youtube.com/watch?v=X
    /shorts/X, /live/X  -> https://www.youtube.com/watch?v=X
    playlist?list=Y     -> https://www.youtube.com/playlist?list=Y
    music.youtube.com   -> equivalente en youtube.com normal

    Devuelve ``None`` si no es una URL reproducible (canales, inicio, ...).

    Las mezclas (``list=RD…``) **sí** conservan su lista: si no, al «canonizar»
    se perdería el mix y solo sonaría la canción suelta.
    """
    u = (url or "").strip()
    if not is_url(u):
        return None
    if not is_youtube(u):
        return None
    vid = video_id(u)
    if vid:
        pid = playlist_id(u)
        if pid and pid.startswith("RD"):
            return "https://www.youtube.com/watch?v=%s&list=%s" % (vid, pid)
        return "https://www.youtube.com/watch?v=" + vid
    pid = playlist_id(u)
    if pid and re.search(r"/playlist\b", u):
        return "https://www.youtube.com/playlist?list=" + pid
    return None


def is_mix_url(url: str) -> bool:
    """
    ¿Es la URL de un «Mix» de YouTube (mezcla infinita / radio)?

    Las mezclas generadas por YouTube usan listas con prefijo ``RD``
    (p. ej. ``RD8Zc4EjdYHjk``).  Se distinguen de las playlists normales
    (``PL…``, ``UU…``) porque crean una emisora sin fin a partir de un vídeo.
    """
    pid = playlist_id(url or "")
    return bool(pid) and pid.startswith("RD")


def mix_url(url: str) -> Optional[str]:
    """URL del «Mix» que YouTube genera a partir de ese vídeo (None si no se puede)."""
    vid = video_id(url or "")
    if not vid:
        return None
    return "https://www.youtube.com/watch?v=%s&list=RD%s" % (vid, vid)


def thumbnail_url(url: str) -> str:
    vid = video_id(url)
    if vid:
        return "https://i.ytimg.com/vi/%s/hqdefault.jpg" % vid
    return ""


def format_time(seconds: float) -> str:
    """205 -> '3:25', 3725 -> '1:02:05'."""
    if seconds is None or seconds < 0:
        seconds = 0
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "%d:%02d:%02d" % (h, m, s)
    return "%d:%02d" % (m, s)


@dataclass
class Tab:
    """Una pestaña abierta en el navegador."""

    browser: str
    title: str
    url: str
    source: str = "en vivo"  # "en vivo", "sesión guardada", "devtools"
    profile: str = ""
    uploader: str = ""

    @property
    def playable(self) -> Optional[str]:
        """URL canónica si se puede reproducir, o None."""
        return canonical_url(self.url)

    @property
    def is_youtube(self) -> bool:
        return is_youtube(self.url)

    @property
    def label(self) -> str:
        t = (self.title or "").strip()
        if t:
            return t
        h = host_of(self.url)
        return h or self.url


@dataclass
class Track:
    """Una pista de la cola."""

    url: str  # URL canónica de la página de YouTube
    title: str = "Cargando…"
    duration: float = 0.0
    uploader: str = ""
    thumb: str = ""
    source: str = ""
    stream_url: str = ""
    stream_headers: Dict[str, str] = field(default_factory=dict)
    resolved_at: float = 0.0
    error: str = ""

    @property
    def key(self) -> str:
        return self.url

    @property
    def ready(self) -> bool:
        return bool(self.stream_url)

    @property
    def is_playlist(self) -> bool:
        return "/playlist" in self.url

    def duration_label(self) -> str:
        if self.is_playlist:
            return "playlist"
        if self.duration > 0:
            return format_time(self.duration)
        return ""
