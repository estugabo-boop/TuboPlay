"""
Mezclas («Mix») de YouTube.

YouTube genera dos tipos de mezcla:

* **A partir de un vídeo** — ``watch?v=ID&list=RDID``. Es la radio infinita que
  aparece al pulsar «Mix» junto a cualquier vídeo: a partir de la canción que
  estás oyendo, YouTube arma una emisora sin fin.
* **Las que propone YouTube** — mezclas ya construidas (``list=RDeu…``) que
  aparecen en sus resultados y en la barra lateral.

La portada personal (``/``) **solo se muestra con sesión iniciada**: sin ella
YouTube devuelve un aviso («Busca algo para comenzar») y ningún vídeo, lo mismo
con los clientes ANDROID/IOS/TV.  Por eso TuboPlay monta la pestaña «Mix» con
las mezclas de los vídeos que tú tienes abiertos (tu gusto de verdad) y con las
que YouTube publica sin necesidad de sesión.
"""

from __future__ import annotations

import json
from typing import Dict, Iterable, List, Optional, Tuple

import requests

from .models import Tab, mix_url, playlist_id, video_id

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

SEARCH_URL = "https://www.youtube.com/results"
DEFAULT_QUERY = "mix"
MAX_PERSONAL = 12
MAX_YOUTUBE = 16


# --------------------------------------------------------------------------- #
#  HTTP
# --------------------------------------------------------------------------- #
def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        # SOCS=CAI evita la pantalla de consentimiento de cookies de la UE
        "Cookie": "SOCS=CAI",
    })
    return s


# --------------------------------------------------------------------------- #
#  lectura de ytInitialData
# --------------------------------------------------------------------------- #
def _initial_data(html: str) -> Optional[dict]:
    marker = "var ytInitialData"
    i = html.find(marker)
    if i < 0:
        i = html.find("ytInitialData")
    if i < 0:
        return None
    j = html.find("{", i)
    if j < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(html[j:])[0]
    except ValueError:
        return None


def _text(node) -> str:
    """Texto de un nodo de título (``simpleText``, ``runs`` o cadena suelta)."""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if isinstance(node.get("simpleText"), str):
            return node["simpleText"]
        if isinstance(node.get("content"), str):
            return node["content"]
        runs = node.get("runs")
        if isinstance(runs, list):
            return "".join(r.get("text", "") for r in runs if isinstance(r, dict))
        if isinstance(node.get("label"), str):
            return node["label"]
    return ""


def _find_title(node, depth: int = 0) -> str:
    """Busca el primer título del subárbol (los formatos cambian a menudo)."""
    if depth > 5 or node is None:
        return ""
    if isinstance(node, dict):
        for key in ("title", "headline", "metadata"):
            if key in node:
                found = _text(node[key]) or _find_title(node[key], depth + 1)
                if found:
                    return found
        for value in node.values():
            found = _find_title(value, depth + 1)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_title(value, depth + 1)
            if found:
                return found
    return ""


def _endpoint(node) -> Optional[dict]:
    """``watchEndpoint`` del nodo (o del que lo contiene)."""
    if not isinstance(node, dict):
        return None
    ne = node.get("navigationEndpoint")
    if isinstance(ne, dict) and isinstance(ne.get("watchEndpoint"), dict):
        return ne["watchEndpoint"]
    if isinstance(node.get("watchEndpoint"), dict):
        return node["watchEndpoint"]
    return None


def _collect_mixes(node, out: List[Tuple[str, str, str]]) -> None:
    """Recorre el árbol buscando (vídeo, lista RD, título)."""
    if isinstance(node, dict):
        ep = _endpoint(node)
        pid = None
        vid = None
        if ep:
            raw = ep.get("playlistId") or ""
            if isinstance(raw, str) and raw.startswith("RD"):
                pid = raw
                vid = ep.get("videoId")
        if not pid:
            raw = node.get("playlistId")
            if isinstance(raw, str) and raw.startswith("RD"):
                pid = raw
                vid = node.get("videoId")
                if not vid:
                    ep2 = _endpoint(node)
                    vid = ep2.get("videoId") if ep2 else None
        if pid and isinstance(vid, str) and len(vid) == 11:
            out.append((vid, pid, _find_title(node)))
        for value in node.values():
            _collect_mixes(value, out)
    elif isinstance(node, list):
        for value in node:
            _collect_mixes(value, out)


# --------------------------------------------------------------------------- #
#  título auxiliar
# --------------------------------------------------------------------------- #
def _clean_title(text: str, fallback: str = "") -> str:
    t = (text or "").strip()
    for suffix in (" - YouTube", " - YouTube Music", " | YouTube"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    t = " ".join(t.split())
    return t or fallback


def _short(text: str, n: int = 78) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


# --------------------------------------------------------------------------- #
#  fuentes
# --------------------------------------------------------------------------- #
def personal_mixes(tabs: Iterable[Tab]) -> List[Tab]:
    """
    Una mezcla por cada vídeo de YouTube que el usuario tiene abierto.

    Si la pestaña ya es un mix (``list=RD…``) se conserva tal cual —es la
    mezcla que estaba oyendo—; si no, YouTube genera una nueva a partir de ese
    vídeo.  Es lo más parecido a la portada personal sin necesidad de sesión.
    """
    out: List[Tab] = []
    seen: Dict[str, bool] = {}
    for tab in tabs:
        if not tab.is_youtube:
            continue
        url = tab.url
        vid = video_id(url)
        if not vid:
            continue
        pid = playlist_id(url) or ""
        if pid.startswith("RD"):
            target = "https://www.youtube.com/watch?v=%s&list=%s" % (vid, pid)
        else:
            target = mix_url(url)
        if not target or target in seen:
            continue
        seen[target] = True
        base = _clean_title(tab.title, "")
        if not base or base.lower().startswith("mix"):
            label = base or vid
        else:
            label = base
        out.append(Tab(
            browser="YouTube",
            title="Mix · " + _short(label),
            url=target,
            source="de tus pestañas abiertas",
            uploader="",
        ))
        if len(out) >= MAX_PERSONAL:
            break
    return out


def youtube_mixes(query: str = DEFAULT_QUERY, limit: int = MAX_YOUTUBE) -> List[Tab]:
    """Mezclas que YouTube enseña en sus resultados (sin necesidad de sesión)."""
    resp = _session().get(SEARCH_URL, params={"search_query": query, "hl": "es",
                                              "gl": "ES"}, timeout=25)
    resp.raise_for_status()
    data = _initial_data(resp.text)
    if not data:
        raise RuntimeError("YouTube no devolvió resultados legibles.")

    found: List[Tuple[str, str, str]] = []
    _collect_mixes(data, found)

    out: List[Tab] = []
    seen: Dict[str, bool] = {}
    for vid, pid, title in found:
        if pid in seen:
            continue
        seen[pid] = True
        nice = _clean_title(title, "")
        if not nice:
            # sin título legible: lo pedimos a la API oEmbed (una petición)
            from .extractor import fetch_title

            nice, author = fetch_title("https://www.youtube.com/watch?v=" + vid)
        else:
            author = ""
        if not nice:
            continue
        out.append(Tab(
            browser="YouTube",
            title="Mix · " + _short(nice),
            url="https://www.youtube.com/watch?v=%s&list=%s" % (vid, pid),
            source="que propone YouTube",
            uploader=author,
        ))
        if len(out) >= limit:
            break
    return out


def build_mixes(tabs: Iterable[Tab], query: str = DEFAULT_QUERY
                ) -> Tuple[List[Tab], List[Tab]]:
    """Devuelve ``(mezclas personales, mezclas de YouTube)``."""
    return personal_mixes(tabs), youtube_mixes(query)
