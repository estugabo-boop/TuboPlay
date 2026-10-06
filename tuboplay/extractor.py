"""Extracción de metadatos y de la URL de audio con yt-dlp."""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

import yt_dlp

from .models import Track, thumbnail_url, video_id

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

BASE_OPTS: dict = {
    "quiet": True,
    "no_warnings": True,
    "noprogress": True,
    "skip_download": True,
    "socket_timeout": 20,
    "retries": 3,
    "format": "bestaudio[ext=m4a]/bestaudio/best",
    "noplaylist": False,
    "playlistend": 200,
    "http_headers": {
        "User-Agent": USER_AGENT,
        "Referer": "https://www.youtube.com/",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    },
}


class ExtractorError(Exception):
    """Error de extracción con un mensaje apto para el usuario."""


def _friendly(exc: BaseException) -> str:
    text = str(exc) or exc.__class__.__name__
    text = re.sub(r"^ERROR:\s*(\[youtube\]\s*)?", "", text).strip()
    lowered = text.lower()
    if "video unavailable" in lowered or "unavailable" in lowered:
        return "El vídeo no está disponible."
    if "private video" in lowered:
        return "El vídeo es privado."
    if "age" in lowered and "confirm" in lowered:
        return "El vídeo tiene restricción de edad."
    if "sign in" in lowered or "log in" in lowered:
        return "YouTube pide iniciar sesión para este vídeo."
    if "not available" in lowered:
        return "Formato de audio no disponible."
    if "reloaded" in lowered:
        return "YouTube pidió recargar la página; reintenta en unos segundos."
    if "unsupported url" in lowered:
        return "Esa URL no es compatible."
    return text[:180]


def _ydl(opts: dict) -> yt_dlp.YoutubeDL:
    return yt_dlp.YoutubeDL(opts)


def _thumb(info: dict, url: str) -> str:
    thumbs = info.get("thumbnails") or []
    if thumbs:
        pick = thumbs[-1].get("url")
        if pick:
            return pick
    return thumbnail_url(url)


def _track_from_info(info: dict, source: str = "") -> Track:
    url = info.get("webpage_url") or info.get("original_url") or ""
    vid = info.get("id") or video_id(url) or ""
    if not url and vid:
        url = "https://www.youtube.com/watch?v=" + vid
    return Track(
        url=url,
        title=(info.get("title") or vid or "Sin título").strip(),
        duration=float(info.get("duration") or 0.0),
        uploader=(info.get("uploader") or info.get("channel") or "").strip(),
        thumb=_thumb(info, url),
        source=source,
    )


def probe(url: str, source: str = "", limit: Optional[int] = None) -> List[Track]:
    """
    Devuelve las pistas que representa una URL (un vídeo o todos los
    vídeos de una playlist), **sin** resolver todavía el audio.

    ``limit`` recorta la playlist: pedir 200 vídeos de una mezcla cuesta
    ~13 s, así que el primer bloque es corto y el resto se pide aparte.
    """
    opts = dict(BASE_OPTS)
    opts["extract_flat"] = "in_playlist"
    if limit is not None:
        opts["playlistend"] = limit
    try:
        with _ydl(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise ExtractorError(_friendly(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise ExtractorError(_friendly(exc)) from exc

    if not info:
        raise ExtractorError("YouTube no devolvió nada para esa URL.")

    entries = info.get("entries")
    if entries is not None:
        tracks: List[Track] = []
        for entry in entries:
            if not entry:
                continue
            vid = entry.get("id") or video_id(entry.get("url") or "")
            if not vid:
                continue
            page = "https://www.youtube.com/watch?v=" + vid
            thumbs = entry.get("thumbnails") or []
            tracks.append(Track(
                url=page,
                title=(entry.get("title") or vid).strip(),
                duration=float(entry.get("duration") or 0.0),
                uploader=(entry.get("uploader") or entry.get("channel") or "").strip(),
                thumb=(thumbs[-1].get("url") if thumbs else "") or thumbnail_url(page),
                source=source,
            ))
        if not tracks:
            raise ExtractorError("La playlist está vacía.")
        return tracks

    return [_track_from_info(info, source)]


def _pick_stream(info: dict) -> Optional[Tuple[str, Dict[str, str]]]:
    """Elige la mejor pista de audio de la información ya extraída."""
    headers = dict(info.get("http_headers") or {})

    if info.get("url") and info.get("acodec") not in (None, "none"):
        return info["url"], headers

    requested = info.get("requested_formats") or []
    for fmt in requested:
        if fmt.get("url") and fmt.get("acodec") not in (None, "none"):
            return fmt["url"], dict(fmt.get("http_headers") or headers)

    candidates = [f for f in (info.get("formats") or [])
                  if f.get("url") and f.get("acodec") not in (None, "none")]
    if not candidates:
        candidates = [f for f in (info.get("formats") or []) if f.get("url")]
    if not candidates:
        return None

    # preferimos audio puro, después m4a y después el mayor bitrate
    candidates.sort(key=lambda f: (1.0 if f.get("vcodec") in (None, "none") else 0.0,
                                   1.0 if f.get("ext") == "m4a" else 0.0,
                                   float(f.get("abr") or f.get("tbr") or 0.0)))
    best = candidates[-1]
    return best["url"], dict(best.get("http_headers") or headers)


def resolve(track: Track) -> Track:
    """Rellena ``stream_url``/``stream_headers`` de una pista."""
    opts = dict(BASE_OPTS)
    try:
        with _ydl(opts) as ydl:
            info = ydl.extract_info(track.url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        track.error = _friendly(exc)
        raise ExtractorError(track.error) from exc
    except Exception as exc:  # noqa: BLE001
        track.error = _friendly(exc)
        raise ExtractorError(track.error) from exc

    if info and info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise ExtractorError("La playlist está vacía.")
        want = video_id(track.url)
        info = next((e for e in entries if e.get("id") == want), entries[0])

    picked = _pick_stream(info or {})
    if not picked:
        raise ExtractorError("YouTube no ha devuelto ningún formato de audio.")
    track.stream_url, track.stream_headers = picked
    if info:
        if not track.title or track.title == "Cargando…":
            track.title = (info.get("title") or track.title).strip()
        if not track.duration:
            track.duration = float(info.get("duration") or 0.0)
        if not track.uploader:
            track.uploader = (info.get("uploader") or info.get("channel") or "").strip()
        if not track.thumb:
            track.thumb = _thumb(info, track.url)
    track.error = ""
    return track


def fetch_title(url: str):
    """
    Título y autor de un vídeo usando la API *oEmbed* de YouTube.

    Mucho más rápida que yt-dlp (una petición HTTP) y sirve para poner nombre
    a las pestañas que vienen de los archivos de sesión del navegador.
    Devuelve ``(título, autor)``; vacíos si algo falla.
    """
    try:
        import requests

        resp = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": url, "format": "json"},
            timeout=10,
            headers={"User-Agent": USER_AGENT},
        )
        if resp.status_code != 200:
            return "", ""
        data = resp.json()
        return (data.get("title") or "", data.get("author_name") or "")
    except Exception:  # noqa: BLE001
        return "", ""


def search(query: str, limit: int = 8, source: str = "búsqueda") -> List[Track]:
    """Búsqueda en YouTube (cuando el texto de la entrada no es una URL)."""
    opts = dict(BASE_OPTS)
    opts["extract_flat"] = "in_playlist"
    try:
        with _ydl(opts) as ydl:
            info = ydl.extract_info("ytsearch%d:%s" % (limit, query), download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise ExtractorError(_friendly(exc)) from exc
    tracks: List[Track] = []
    for entry in (info or {}).get("entries") or []:
        if not entry:
            continue
        vid = entry.get("id")
        if not vid:
            continue
        page = "https://www.youtube.com/watch?v=" + vid
        thumbs = entry.get("thumbnails") or []
        tracks.append(Track(
            url=page,
            title=(entry.get("title") or vid).strip(),
            duration=float(entry.get("duration") or 0.0),
            uploader=(entry.get("uploader") or "").strip(),
            thumb=(thumbs[-1].get("url") if thumbs else "") or thumbnail_url(page),
            source=source,
        ))
    if not tracks:
        raise ExtractorError("No se encontró nada para “%s”." % query)
    return tracks
