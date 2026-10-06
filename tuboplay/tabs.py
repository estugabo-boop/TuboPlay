"""Detección de las pestañas abiertas en los navegadores.

Se soportan tres mecanismos, del más fiable al más simple:

1. **Puerto de depuración (DevTools)** de los navegadores basados en
   Chromium (Chrome, Edge, Brave, Vivaldi, Opera).  Devuelve las pestañas
   *en vivo*, con título y URL.  Solo funciona si el navegador se lanzó con
   ``--remote-debugging-port``; la app sabe activarlo.
2. **Archivo de sesión de Firefox** (``sessionstore.jsonlz4``).  Firefox lo
   reescribe cada ~15 segundos mientras está abierto, así que las pestañas
   que devuelve están prácticamente al día.
3. **Archivos de sesión de Chromium** (``Sessions/Session_*``).  Es el
   respaldo: contienen la última sesión guardada (puede estar algo antiguo
   si el navegador sigue abierto, porque el fichero en vivo está bloqueado).
"""

from __future__ import annotations

import glob
import json
import os
import re
import socket
import struct
import sys
import time
from typing import Dict, List, Optional, Tuple

from .models import Tab, is_youtube

# ---------------------------------------------------------------------------
# 1. Puerto de depuración (Chromium y cualquier navegador CDP)
# ---------------------------------------------------------------------------

DEVTOOLS_PORTS = range(9222, 9235)


def _port_open(port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_json(url: str, timeout: float = 1.0):
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "TuboPlay/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def devtools_tabs() -> List[Tab]:
    """Pestañas en vivo leyendo la API remota de depuración."""
    out: List[Tab] = []
    for port in DEVTOOLS_PORTS:
        if not _port_open(port):
            continue
        browser = "Chromium"
        try:
            info = _http_json("http://127.0.0.1:%d/json/version" % port, timeout=0.7)
            browser = (info.get("Browser") or browser).split("/")[0]
        except Exception:  # noqa: BLE001
            pass
        try:
            pages = _http_json("http://127.0.0.1:%d/json/list" % port, timeout=1.0)
        except Exception:  # noqa: BLE001
            continue
        for page in pages:
            if page.get("type") != "page":
                continue
            url = page.get("url") or ""
            if not url.startswith(("http://", "https://")):
                continue
            out.append(Tab(browser, page.get("title") or "", url, "en vivo", ":%d" % port))
        if out:
            break
    return out


# ---------------------------------------------------------------------------
# 2. Firefox
# ---------------------------------------------------------------------------


def _firefox_profile_roots() -> List[str]:
    home = os.path.expanduser("~")
    if os.name == "nt":
        appdata = os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming"))
        return [os.path.join(appdata, "Mozilla", "Firefox", "Profiles")]
    if sys.platform == "darwin":
        return [os.path.join(home, "Library", "Application Support", "Firefox", "Profiles")]
    return [os.path.join(home, ".mozilla", "firefox")]


def _firefox_profiles() -> List[str]:
    profiles: List[str] = []
    for root in _firefox_profile_roots():
        if os.path.isdir(root):
            profiles.extend(p for p in glob.glob(os.path.join(root, "*")) if os.path.isdir(p))
        # profiles.ini permite rutas personalizadas
        ini = os.path.join(os.path.dirname(root.rstrip(os.sep)), "profiles.ini")
        if os.path.isfile(ini):
            try:
                with open(ini, "r", encoding="utf-8", errors="replace") as fh:
                    cur = ""
                    for line in fh:
                        line = line.strip()
                        if line.startswith("Path="):
                            cur = line.split("=", 1)[1].strip()
                        elif line.startswith("IsRelative=1") and cur:
                            cand = os.path.join(root, cur)
                            if os.path.isdir(cand):
                                profiles.append(cand)
                            cur = ""
            except OSError:
                pass
    # sin duplicados, conservando el orden
    seen, uniq = set(), []
    for p in profiles:
        rp = os.path.realpath(p)
        if rp not in seen:
            seen.add(rp)
            uniq.append(p)
    return uniq


def _read_mozlz4(path: str) -> Optional[dict]:
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if data[:8] != b"mozLz40\x00":
        return None
    try:
        import lz4.block

        return json.loads(lz4.block.decompress(data[8:]).decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None


def firefox_tabs() -> List[Tab]:
    out: List[Tab] = []
    for profile in _firefox_profiles():
        candidates = [
            os.path.join(profile, "sessionstore.jsonlz4"),
            os.path.join(profile, "sessionstore-backups", "recovery.jsonlz4"),
            os.path.join(profile, "sessionstore-backups", "previous.jsonlz4"),
        ]
        # nos quedamos con el más reciente de ese perfil
        candidates = [c for c in candidates if os.path.isfile(c)]
        if not candidates:
            continue
        candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        session = None
        for cand in candidates:
            session = _read_mozlz4(cand)
            if session:
                break
        if not session:
            continue
        name = os.path.basename(profile)
        for window in session.get("windows", []):
            for tab in window.get("tabs", []):
                entries = tab.get("entries") or []
                if not entries:
                    continue
                idx = tab.get("index") or 1
                try:
                    entry = entries[min(max(int(idx), 1), len(entries)) - 1]
                except (ValueError, TypeError):
                    entry = entries[-1]
                url = entry.get("url") or ""
                if not url.startswith(("http://", "https://")):
                    continue
                title = (entry.get("title") or tab.get("title") or "").strip()
                out.append(Tab("Firefox", title, url, "en vivo", name))
    return out


# ---------------------------------------------------------------------------
# 3. Archivos de sesión de Chromium (respaldo)
# ---------------------------------------------------------------------------

def chromium_user_data_dirs() -> Dict[str, str]:
    home = os.path.expanduser("~")
    if os.name == "nt":
        la = os.environ.get("LOCALAPPDATA", "")
        ro = os.environ.get("APPDATA", "")
        return {
            "Chrome": os.path.join(la, "Google", "Chrome", "User Data"),
            "Edge": os.path.join(la, "Microsoft", "Edge", "User Data"),
            "Brave": os.path.join(la, "BraveSoftware", "Brave-Browser", "User Data"),
            "Vivaldi": os.path.join(la, "Vivaldi", "User Data"),
            "Opera": os.path.join(ro, "Opera Software", "Opera Stable"),
            "Opera GX": os.path.join(ro, "Opera Software", "Opera GX Stable"),
            "Chromium": os.path.join(la, "Chromium", "User Data"),
        }
    if sys.platform == "darwin":
        app = os.path.join(home, "Library", "Application Support")
        return {
            "Chrome": os.path.join(app, "Google", "Chrome"),
            "Edge": os.path.join(app, "Microsoft Edge"),
            "Brave": os.path.join(app, "BraveSoftware", "Brave-Browser"),
            "Chromium": os.path.join(app, "Chromium"),
            "Opera": os.path.join(app, "com.operasoftware.Opera"),
        }
    cfg = os.path.join(home, ".config")
    return {
        "Chrome": os.path.join(cfg, "google-chrome"),
        "Edge": os.path.join(cfg, "microsoft-edge"),
        "Brave": os.path.join(cfg, "BraveSoftware", "Brave-Browser"),
        "Chromium": os.path.join(cfg, "chromium"),
        "Vivaldi": os.path.join(cfg, "vivaldi"),
    }


def _session_files(profile_dir: str) -> List[str]:
    files: List[str] = []
    sessions = os.path.join(profile_dir, "Sessions")
    if os.path.isdir(sessions):
        for name in os.listdir(sessions):
            if name.startswith(("Session_", "Tabs_")):
                files.append(os.path.join(sessions, name))
    for name in ("Current Session", "Current Tabs", "Last Session", "Last Tabs"):
        p = os.path.join(profile_dir, name)
        if os.path.isfile(p):
            files.append(p)
    files = [f for f in files if os.path.isfile(f)]
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files


_U32 = struct.Struct("<I")


def _u32(buf: bytes, off: int) -> int:
    return _U32.unpack_from(buf, off)[0]


def _decode_title(raw: bytes) -> str:
    """
    Interpreta el título que acompaña a una URL en un fichero SNSS de Chrome.

    Chrome lo escribe en UTF‑16, pero en muchos registros lo que hay es texto
    UTF‑8 plano (o la propia URL repetida).  Leídos como UTF‑16 salían títulos
    incomprensibles como «潹瑵扵…», que además Tk tarda casi un segundo en
    dibujar.  Dos claves para acertar:

    * un texto guardado en UTF‑16 con letras ASCII siempre lleva bytes
      ``\\x00``; si no los tiene, es texto plano y hay que leerlo como UTF‑8;
    * un título jamás empieza por «http»: eso es la URL, no el título.
    """
    if len(raw) < 2:
        return ""
    text = ""
    if b"\x00" in raw:
        try:
            text = raw.decode("utf-16-le")
        except UnicodeDecodeError:
            text = ""
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = ""
        if not text and len(raw) % 2 == 0:   # UTF‑16 sin caracteres ASCII
            try:
                text = raw.decode("utf-16-le")
            except UnicodeDecodeError:
                text = ""
    text = text.strip()
    if len(text) < 2 or any(ord(ch) < 32 for ch in text):
        return ""
    if text.lower().startswith(("http://", "https://", "ftp://")):
        return ""
    return text


def _extract_url_title_pairs(data: bytes) -> List[Tuple[str, str]]:
    """
    Los ficheros SNSS de Chromium guardan cada URL precedida de su longitud
    como ``uint32`` en orden little-endian, y algo más adelante el título.
    No hace falta un parser de protobuf: con buscar ``http://`` y leer los
    4 bytes anteriores obtenemos la URL exacta.
    """
    pairs: List[Tuple[str, str]] = []
    limit = len(data)
    for match in re.finditer(rb"https?://", data):
        i = match.start()
        if i < 4:
            continue
        length = _u32(data, i - 4)
        if length == 0 or length > 2048 or i + length > limit:
            continue
        raw_url = data[i:i + length]
        if any(b < 0x20 or b > 0x7E for b in raw_url):
            continue
        url = raw_url.decode("ascii", "replace")
        title = ""
        base = i + length
        for offset in (4, 0, 8):
            if base + offset + 4 > limit:
                continue
            tlen = _u32(data, base + offset)
            if tlen < 2 or tlen > 600 or base + offset + 4 + tlen > limit:
                continue
            title = _decode_title(data[base + offset + 4:base + offset + 4 + tlen])
            if title:
                break
        pairs.append((url, title))
    return pairs


def chromium_tabs(min_age_hours: float = 72.0) -> List[Tab]:
    """Pestañas deducidas de los archivos de sesión (última sesión guardada)."""
    out: List[Tab] = []
    for browser, root in chromium_user_data_dirs().items():
        if not os.path.isdir(root):
            continue
        try:
            profiles = [p for p in glob.glob(os.path.join(root, "*")) if os.path.isdir(p)]
        except OSError:
            continue
        ordered: List[str] = []
        for profile in profiles:
            if os.path.basename(profile) in ("System Profile", "Guest Profile", "Crashpad"):
                continue
            ordered.extend(_session_files(profile))
        if not ordered:
            continue
        # ordenados del más reciente al más antiguo
        ordered.sort(key=lambda p: os.path.getmtime(p), reverse=True)

        # el fichero más reciente suele estar bloqueado por el propio
        # navegador mientras está abierto: probamos con el siguiente
        data = None
        used = None
        for path in ordered:
            try:
                with open(path, "rb") as fh:
                    candidate = fh.read()
            except OSError:
                continue  # bloqueado
            if candidate[:4] == b"SNSS":
                data, used = candidate, path
                break
        if data is None or used is None:
            continue
        seen: Dict[str, str] = {}
        order: List[str] = []
        for url, title in _extract_url_title_pairs(data):
            if not is_youtube(url):
                continue
            if url not in seen:
                order.append(url)
                seen[url] = ""
            if title:
                seen[url] = title
        profile_name = os.path.basename(os.path.dirname(os.path.dirname(used)))
        for url in order:
            out.append(Tab(browser, seen.get(url, ""), url, "sesión guardada",
                           profile_name))
    return _dedupe(out)


# ---------------------------------------------------------------------------
# Combinación de fuentes
# ---------------------------------------------------------------------------


def collect_tabs(include_guarded: bool = True) -> Tuple[List[Tab], List[str]]:
    """
    Devuelve ``(pestañas, avisos)``.

    Las pestañas en vivo (Firefox y puerto de depuración) salen primero; las
    de sesión guardada se añaden detrás y solo si no están ya cubiertas.
    """
    live: List[Tab] = []
    notes: List[str] = []

    try:
        live.extend(devtools_tabs())
    except Exception as exc:  # noqa: BLE001
        notes.append("No se pudo leer el puerto de depuración: %s" % exc)

    try:
        live.extend(firefox_tabs())
    except Exception as exc:  # noqa: BLE001
        notes.append("No se pudo leer la sesión de Firefox: %s" % exc)

    guarded: List[Tab] = []
    if include_guarded:
        try:
            guarded = chromium_tabs()
        except Exception as exc:  # noqa: BLE001
            notes.append("No se pudieron leer los archivos de sesión de Chromium: %s" % exc)

    known = {t.url for t in live}
    merged = list(live)
    for tab in guarded:
        if tab.url not in known:
            merged.append(tab)

    if not live and not guarded:
        notes.append("No se ha detectado ningún navegador abierto.")
    elif not live and guarded:
        notes.append("Sesiones guardadas (no en vivo): usa «Conectar navegador».")
    return merged, notes


def youtube_tabs(tabs: List[Tab]) -> List[Tab]:
    """Pestañas cuya URL se puede reproducir directamente."""
    return [t for t in tabs if t.playable]


def _dedupe(tabs: List[Tab]) -> List[Tab]:
    """
    Quita duplicados.

    Se compara por la URL *reproducible* (si la hay): dos pestañas que apuntan
    al mismo vídeo con parámetros distintos de playlist son la misma canción.
    """
    seen = set()
    out: List[Tab] = []
    for tab in tabs:
        key = tab.playable or tab.url
        if key in seen:
            continue
        seen.add(key)
        out.append(tab)
    return out


# ---------------------------------------------------------------------------
# Activación del puerto de depuración
# ---------------------------------------------------------------------------


def browser_executable(name: str) -> Optional[str]:
    """Ruta al ejecutable de un navegador conocido."""
    if os.name == "nt":
        la = os.environ.get("LOCALAPPDATA", "")
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        ro = os.environ.get("APPDATA", "")
        candidates = {
            "Chrome": [os.path.join(pf, "Google", "Chrome", "Application", "chrome.exe"),
                       os.path.join(pf86, "Google", "Chrome", "Application", "chrome.exe"),
                       os.path.join(la, "Google", "Chrome", "Application", "chrome.exe")],
            "Edge": [os.path.join(pf86, "Microsoft", "Edge", "Application", "msedge.exe"),
                     os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe")],
            "Brave": [os.path.join(pf, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
                      os.path.join(la, "BraveSoftware", "Brave-Browser", "Application", "brave.exe")],
            "Vivaldi": [os.path.join(la, "Vivaldi", "Application", "vivaldi.exe"),
                        os.path.join(pf, "Vivaldi", "Application", "vivaldi.exe")],
            "Opera": [os.path.join(la, "Programs", "Opera", "launcher.exe"),
                      os.path.join(pf, "Opera", "launcher.exe")],
            "Opera GX": [os.path.join(la, "Programs", "Opera GX", "launcher.exe"),
                         os.path.join(ro, "Opera Software", "Opera GX Stable", "opera.exe")],
            "Chromium": [os.path.join(la, "Chromium", "Application", "chrome.exe")],
        }
    elif sys.platform == "darwin":
        apps = {
            "Chrome": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "Edge": "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "Brave": "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "Chromium": "/Applications/Chromium.app/Contents/MacOS/Chromium",
        }
        candidates = {k: [v] for k, v in apps.items()}
    else:
        paths = {
            "Chrome": ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"],
            "Edge": ["/usr/bin/microsoft-edge", "/usr/bin/microsoft-edge-stable"],
            "Brave": ["/usr/bin/brave-browser"],
            "Chromium": ["/usr/bin/chromium", "/usr/bin/chromium-browser"],
            "Vivaldi": ["/usr/bin/vivaldi-stable"],
        }
        candidates = paths

    for path in candidates.get(name, []):
        if os.path.isfile(path):
            return path
    # último recurso: que lo resuelva el sistema
    import shutil

    for cmd in {"Chrome": "chrome", "Edge": "msedge", "Brave": "brave",
                "Chromium": "chromium", "Vivaldi": "vivaldi", "Opera": "opera"}.get(name, []):
        found = shutil.which(cmd)
        if found:
            return found
    return None


def browser_process_names(name: str) -> List[str]:
    """Nombres de los procesos de un navegador, según el sistema."""
    table = {
        "Chrome": ["chrome.exe"] if os.name == "nt" else ["chrome", "google-chrome", "google-chrome-stable"],
        "Edge": ["msedge.exe"] if os.name == "nt" else ["msedge", "microsoft-edge"],
        "Brave": ["brave.exe"] if os.name == "nt" else ["brave", "brave-browser"],
        "Vivaldi": ["vivaldi.exe"] if os.name == "nt" else ["vivaldi"],
        "Opera": ["opera.exe"] if os.name == "nt" else ["opera"],
        "Opera GX": ["opera.exe"] if os.name == "nt" else ["opera"],
        "Chromium": ["chrome.exe"] if os.name == "nt" else ["chromium", "chromium-browser"],
    }
    return table.get(name, [])


def browser_is_running(name: str) -> bool:
    """¿Hay algún proceso de ese navegador en marcha?"""
    return bool(list_browser_pids(name))


def list_browser_pids(name: str) -> List[int]:
    names = {n.lower() for n in browser_process_names(name)}
    if not names:
        return []
    import subprocess

    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, timeout=10).stdout
            pids = []
            for line in out.splitlines():
                parts = [p.strip().strip('"') for p in line.split('","')]
                if parts and parts[0].lower() in names:
                    try:
                        pids.append(int(parts[1]))
                    except (IndexError, ValueError):
                        pass
            return pids
        out = subprocess.run(["pgrep", "-f", "|".join(sorted(names))],
                             capture_output=True, text=True, timeout=10).stdout
        return [int(x) for x in out.split() if x.strip().isdigit()]
    except Exception:  # noqa: BLE001
        return []


def stop_browser(name: str) -> Tuple[bool, str]:
    """Cierra todas las ventanas del navegador (para poder relanzarlo)."""
    import subprocess

    pids = list_browser_pids(name)
    if not pids:
        return True, "%s no estaba abierto." % name
    names = browser_process_names(name)
    try:
        if os.name == "nt":
            for proc in names:
                subprocess.run(["taskkill", "/IM", proc, "/F"],
                               capture_output=True, timeout=20)
        else:
            for proc in names:
                subprocess.run(["pkill", "-f", proc], capture_output=True, timeout=20)
    except Exception as exc:  # noqa: BLE001
        return False, "No se pudo cerrar %s: %s" % (name, exc)
    # Esperamos a que los procesos desaparezcan de verdad: si el perfil sigue
    # bloqueado, el nuevo lanzamiento fallaría silenciosamente.
    for _ in range(30):
        if not list_browser_pids(name):
            break
        time.sleep(0.2)
    return True, "%s se ha cerrado (%d proceso(s))." % (name, len(pids))


def start_browser_with_devtools(name: str) -> Tuple[bool, str]:
    """(Re)lanza un navegador con el puerto de depuración activado."""
    import subprocess

    exe = browser_executable(name)
    if not exe:
        return False, "No se ha encontrado el ejecutable de %s." % name
    args = [exe, "--remote-debugging-port=9222", "--remote-allow-origins=*",
            "--restore-last-session"]
    try:
        if os.name == "nt":
            subprocess.Popen(args, close_fds=True,
                             creationflags=subprocess.DETACHED_PROCESS)
        else:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return False, "No se pudo iniciar %s: %s" % (name, exc)
    return True, "%s se está iniciando con la lectura de pestañas activada." % name
