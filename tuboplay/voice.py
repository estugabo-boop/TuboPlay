"""
Control por voz con Vosk (funciona sin conexión y en español).

Dos piezas:

* ``sounddevice``  — captura el micrófono (trae PortAudio incluido en Windows
  y macOS; en Linux hace falta ``libportaudio2``).
* ``vosk``         — reconocedor *offline*.  Se baja un modelo español de
  ~40 MB una sola vez y desde ahí no consume red.

El hilo de audio **nunca** toca la interfaz: solo deja los textos reconocidos
en una cola que la ventana va leyendo con ``poll()`` desde su bucle de ticks,
igual que hace con los eventos de VLC.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import unicodedata
import zipfile
from collections import deque
from pathlib import Path
from typing import Callable, List, Optional, Tuple

MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip"
MODEL_DIRNAME = "vosk-model-small-es-0.42"
PACKAGES = ("vosk", "sounddevice")
SAMPLE_RATE = 16000
MIN_CONFIDENCE = 0.25


# --------------------------------------------------------------------------- #
#  dónde vive el modelo
# --------------------------------------------------------------------------- #
def app_dir() -> Path:
    """Carpeta de datos de TuboPlay (se crea bajo demanda)."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        root = Path(base)
    else:
        root = Path(os.path.expanduser("~"))
    return root / "TuboPlay"


def model_path() -> Path:
    return app_dir() / MODEL_DIRNAME


def have_package(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:  # noqa: BLE001
        return False


def missing() -> List[str]:
    """Qué falta para poder escuchar: paquetes y/o modelo."""
    out = [p for p in PACKAGES if not have_package(p)]
    if not (model_path() / "am").is_dir():
        out.append("modelo")
    return out


def ready() -> bool:
    return not missing()


def describe() -> str:
    gap = missing()
    if not gap:
        return "voz lista (Vosk, español, sin conexión)"
    names = {"modelo": "modelo de voz"}
    return "voz pendiente: " + ", ".join(names.get(g, g) for g in gap)


# --------------------------------------------------------------------------- #
#  instalación
# --------------------------------------------------------------------------- #
def install(on_progress: Optional[Callable[[str], None]] = None) -> Tuple[bool, str]:
    """
    Prepara el reconocimiento de voz: instala los paquetes que falten y
    descarga el modelo español.  Devuelve ``(ok, mensaje)``.
    """

    def progress(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    lack = [p for p in PACKAGES if not have_package(p)]
    if lack:
        progress("Instalando paquetes de voz (%s)…" % ", ".join(lack))
        cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
               "--no-input"] + lack
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        except Exception as exc:  # noqa: BLE001
            return False, "No se pudo ejecutar pip: %s" % exc
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
            return False, "pip falló: %s" % (" ".join(tail))[:300]
        for pkg in lack:
            if not have_package(pkg):
                return False, "El paquete «%s» sigue sin instalarse." % pkg

    target = model_path()
    if not (target / "am").is_dir():
        progress("Descargando modelo de voz en español (≈40 MB)…")
        try:
            _download_model(progress)
        except Exception as exc:  # noqa: BLE001
            return False, "No se pudo bajar el modelo: %s" % exc
        if not (target / "am").is_dir():
            return False, "El modelo descargado no es válido."

    return True, "Voz lista: ya puedes decir «pon play»."


def _download_model(progress: Callable[[str], None]) -> None:
    import requests

    root = app_dir()
    root.mkdir(parents=True, exist_ok=True)
    zip_path = root / (MODEL_DIRNAME + ".zip")

    with requests.get(MODEL_URL, stream=True, timeout=(15, 120),
                      headers={"User-Agent": "TuboPlay"}) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        chunk = 1 << 16
        last = -10
        with open(zip_path, "wb") as fh:
            for block in resp.iter_content(chunk):
                if not block:
                    continue
                fh.write(block)
                done += len(block)
                if total and done // (5 << 20) != last:
                    last = done // (5 << 20)
                    progress("Descargando modelo de voz… %d%%" % int(done * 100 / total))

    progress("Descomprimiendo modelo…")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(root)
    try:
        zip_path.unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------- #
#  órdenes
# --------------------------------------------------------------------------- #
def normalize(text: str) -> str:
    """Minúsculas, sin acentos ni puntuación: «Pón play, por favor» -> 'pon play por favor'."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = t.lower()
    t = re.sub(r"[^a-z0-9\s]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# Se comprueban en orden: el primero que encaja gana.
_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("vol_up", ("sube el volumen", "sube volumen", "mas volumen",
                "sube la musica", "subele")),
    ("vol_down", ("baja el volumen", "baja volumen", "menos volumen",
                  "baja la musica", "quitamos volumen")),
    ("next", ("siguiente", "la siguiente", "pasa cancion", "otra cancion",
              "salta", "next")),
    ("prev", ("anterior", "la anterior", "vuelve atras", "cancion anterior",
              "previous")),
    ("pause", ("pausa", "para", "detente", "stop", "silencia", "en pausa",
               "no suenes", "para la musica", "para la cancion")),
    ("play", ("pon play", "ponme play", "play", "reproduce", "reproducir",
              "que suene", "suena", "pon la musica", "pon musica",
              "pon el mix", "pon un mix", "dale", "continua", "sigue",
              "pon",
              # «play» es una palabra inglesa y el modelo español la devuelve
              # truncada según cómo suene: cubrimos las variantes reales
              "plei", "pley", "plai", "pléi", "comple", "complay",
              "onplay", "ponplay",
              "mix", "mezcla")),
)


def parse_command(text: str) -> Optional[str]:
    """
    Devuelve la orden (``play``/``pause``/``next``/…) o ``None``.

    Las palabras se buscan **enteras** (``\\b``): así «imponer» no activa el
    reproductor por contener «pon», pero «pón play» sí lo hace aunque el
    reconocedor lo transcriba con o sin tilde.
    """
    t = normalize(text)
    if not t:
        return None
    for command, keys in _RULES:
        for key in keys:
            if re.search(r"\b%s\b" % re.escape(key), t):
                return command
    return None


# --------------------------------------------------------------------------- #
#  escucha
# --------------------------------------------------------------------------- #
class VoiceListener:
    """
    Escucha el micrófono en un hilo aparte y va acumulando eventos que la
    interfaz recoge con ``poll()``.

    Eventos: ``("heard", texto)``, ``("error", motivo)``.
    """

    def __init__(self) -> None:
        self._events: deque = deque()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    @property
    def listening(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def poll(self) -> List[Tuple[str, str]]:
        with self._lock:
            if not self._events:
                return []
            items = list(self._events)
            self._events.clear()
        return items

    def _push(self, kind: str, text: str) -> None:
        with self._lock:
            self._events.append((kind, text))

    # -- arranque / parada --------------------------------------------------
    def start(self) -> Tuple[bool, str]:
        if self.listening:
            return True, "Ya estaba escuchando."
        gap = missing()
        if gap:
            return False, "Falta instalar: %s." % ", ".join(gap)
        try:
            import sounddevice as sd

            devices = sd.query_devices()
        except Exception as exc:  # noqa: BLE001
            return False, "No se pudo abrir el micrófono: %s" % exc
        inputs = [d for d in devices if d.get("max_input_channels", 0) > 0]
        if not inputs:
            return False, "No hay ningún micrófono disponible."

        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="tuboplay-voice",
                                        daemon=True)
        self._thread.start()
        return True, "Escuchando: di «pon play»."

    def stop(self) -> None:
        self._stop.set()
        self._thread = None

    # -- hilo de audio ------------------------------------------------------
    def _run(self) -> None:
        try:
            import queue as _queue

            import sounddevice as sd
            from vosk import KaldiRecognizer, Model, SetLogLevel

            SetLogLevel(-1)
            model = Model(str(model_path()))
            rec = KaldiRecognizer(model, SAMPLE_RATE)
            blocks = _queue.Queue()

            def on_audio(indata, frames, _time, _status):
                blocks.put(bytes(indata))

            with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=8000,
                                   dtype="int16", channels=1, callback=on_audio):
                while not self._stop.is_set():
                    try:
                        data = blocks.get(timeout=0.3)
                    except _queue.Empty:
                        continue
                    if rec.AcceptWaveform(data):
                        self._push("heard", _result_text(rec.Result()))
            self._push("heard", _result_text(rec.FinalResult()))
        except Exception as exc:  # noqa: BLE001
            self._push("error", str(exc))


def _result_text(payload: str) -> str:
    try:
        data = json.loads(payload or "{}")
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    conf = data.get("conf")
    if conf is not None and float(conf) < MIN_CONFIDENCE:
        return ""
    return (data.get("text") or "").strip()
