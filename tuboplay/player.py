"""Reproducción de audio con VLC (libvlc)."""

from __future__ import annotations

from typing import Dict, Optional

import vlc

# python-vlc define los estados como singletons sin atributo ``name``
_STATE_NAMES = {
    0: "NothingSpecial", 1: "Opening", 2: "Buffering", 3: "Playing",
    4: "Paused", 5: "Stopped", 6: "Ended", 7: "Error",
}
_STATE_ERROR = vlc.State.Error.value


class VlcPlayer:
    """
    Envuelve ``MediaPlayer`` de python-vlc.

    Los eventos de VLC llegan desde hilos nativos, así que los manejadores
    solo levantan una bandera; la interfaz los recoge con ``poll()`` desde su
    propio ``after()`` y decide qué hacer (pasar a la siguiente pista, etc.).
    Nunca se toca Tk desde un hilo de VLC.
    """

    def __init__(self) -> None:
        self._instance = vlc.Instance(
            "--no-video",
            "--quiet",
            "--no-stats",
            "--no-sub-autodetect-file",
            "--network-caching=4000",
            "--file-caching=4000",
        )
        if self._instance is None:
            raise RuntimeError("No se pudo inicializar VLC.")
        self._mp = self._instance.media_player_new()
        self._volume = 80
        self._callbacks: list = []  # evita que libvlc recoja los manejadores
        self._ended = False
        self._errored = False
        self._attach()

    # -- eventos ------------------------------------------------------------
    def _attach(self) -> None:
        manager = self._mp.event_manager()
        if manager is None:
            return
        for event_type, handler in (
            (vlc.EventType.MediaPlayerEndReached, self._on_end),
            (vlc.EventType.MediaPlayerEncounteredError, self._on_error),
            (vlc.EventType.MediaPlayerStopped, self._on_stop),
        ):
            try:
                manager.event_attach(event_type, handler)
                self._callbacks.append(handler)
            except Exception:  # noqa: BLE001
                pass

    def _on_end(self, _event) -> None:
        self._ended = True

    def _on_error(self, _event) -> None:
        self._errored = True

    def _on_stop(self, _event) -> None:
        pass

    # -- reproducción -------------------------------------------------------
    def play(self, url: str, headers: Optional[Dict[str, str]] = None,
             start_volume: Optional[int] = None) -> None:
        self._ended = False
        self._errored = False
        media = self._instance.media_new_location(url)
        headers = headers or {}
        ua = headers.get("User-Agent") or headers.get("user-agent")
        if ua:
            media.add_option(":http-user-agent=%s" % ua)
        ref = headers.get("Referer") or headers.get("referer")
        if ref:
            media.add_option(":http-referrer=%s" % ref)
        try:
            self._mp.stop()
        except Exception:  # noqa: BLE001
            pass
        self._mp.set_media(media)
        if start_volume is not None:
            self.set_volume(start_volume)
        else:
            self.set_volume(self._volume)
        self._mp.play()

    def stop(self) -> None:
        self._ended = False
        self._errored = False
        try:
            self._mp.stop()
        except Exception:  # noqa: BLE001
            pass

    def pause(self) -> None:
        try:
            self._mp.pause()
        except Exception:  # noqa: BLE001
            pass

    def resume(self) -> None:
        try:
            if self._mp.is_playing() == 0:
                self._mp.play()
            else:
                self._mp.pause()
        except Exception:  # noqa: BLE001
            pass

    def toggle(self) -> None:
        """Devuelve True si después de la llamada está sonando."""
        try:
            if self._mp.is_playing():
                self._mp.set_pause(1)
                return False
            self._mp.set_pause(0)
            return True
        except Exception:  # noqa: BLE001
            return False

    def is_playing(self) -> bool:
        try:
            return bool(self._mp.is_playing())
        except Exception:  # noqa: BLE001
            return False

    # -- estado -------------------------------------------------------------
    def poll(self) -> str:
        """
        Devuelve ``''``, ``'ended'`` o ``'error'``. Llamar desde el hilo
        principal para consumir las banderas de los hilos de VLC.
        """
        if self._errored:
            self._errored = False
            return "error"
        if self._ended:
            self._ended = False
            return "ended"
        # detecta estados de error que no llegan por evento
        try:
            value = self._mp.get_state().value
        except Exception:  # noqa: BLE001
            return ""
        if value == _STATE_ERROR:
            return "error"
        return ""

    def time_ms(self) -> int:
        try:
            value = self._mp.get_time()
            return max(0, value if value and value > 0 else 0)
        except Exception:  # noqa: BLE001
            return 0

    def length_ms(self) -> int:
        try:
            value = self._mp.get_length()
            return max(0, value if value and value > 0 else 0)
        except Exception:  # noqa: BLE001
            return 0

    def seek_ms(self, ms: int) -> None:
        try:
            self._mp.set_time(int(ms))
        except Exception:  # noqa: BLE001
            pass

    def seek_fraction(self, fraction: float) -> None:
        try:
            self._mp.set_position(float(max(0.0, min(1.0, fraction))))
        except Exception:  # noqa: BLE001
            pass

    def volume(self) -> int:
        try:
            return int(self._mp.audio_get_volume())
        except Exception:  # noqa: BLE001
            return self._volume

    def set_volume(self, value: int) -> None:
        self._volume = max(0, min(100, int(value)))
        try:
            self._mp.audio_set_volume(self._volume)
        except Exception:  # noqa: BLE001
            pass

    def state(self) -> str:
        try:
            return _STATE_NAMES.get(self._mp.get_state().value, "Unknown")
        except Exception:  # noqa: BLE001
            return "Unknown"

    def release(self) -> None:
        try:
            self._mp.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._mp.release()
        except Exception:  # noqa: BLE001
            pass
        self._callbacks.clear()
        try:
            vlc.libvlc_release(self._instance)
        except Exception:  # noqa: BLE001
            pass


def vlc_available() -> tuple:
    """(ok, mensaje)."""
    try:
        import vlc as _vlc

        version = _vlc.libvlc_get_version()
        if isinstance(version, bytes):
            version = version.decode("ascii", "replace")
        return True, str(version)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
