"""Integración MPRIS2 (D-Bus) best-effort para ytaudio.

Este módulo permite que teclas multimedia del escritorio (play/pausa,
siguiente, anterior) controlen la reproducción, exponiendo las interfaces
estándar ``org.mpris.MediaPlayer2`` y ``org.mpris.MediaPlayer2.Player`` sobre
el bus de sesión D-Bus.

Es BEST-EFFORT por diseño: si el paquete ``dbus-next`` no está instalado, si
no hay un bus de sesión D-Bus disponible, o si otra instancia ya registró el
nombre bien conocido, este módulo se degrada en silencio (loguea una línea
WARNING a stderr) y nunca hace crashear el arranque de la app. Ni siquiera
importar este módulo requiere que ``dbus-next`` esté instalado.

Dos caminos de uso
-------------------

1. Conveniencia SÍNCRONA, para apps no-async o para pruebas manuales/standalone::

       from ytaudio.mpris import try_start_mpris
       service = try_start_mpris(engine)  # MprisService o None

   Internamente arranca un hilo daemon con su propio loop asyncio dedicado
   (porque ``asyncio.run()`` cerraría el loop -y con él el socket D-Bus- en
   cuanto termine la corrutina de arranque). Ese hilo sigue vivo procesando
   mensajes D-Bus en segundo plano hasta que el proceso termina o se llama
   ``await service.stop()``.

   NO llamar ``try_start_mpris`` desde dentro de un loop asyncio que ya está
   corriendo (el manejo de hilo/loop propio es redundante y puede generar
   confusión sobre qué loop atiende al engine). En ese caso usar el camino 2.

2. Camino ASYNC directo, para integradores que YA tienen un loop asyncio
   corriendo (p. ej. una app Textual, en ``on_mount``)::

       from ytaudio.mpris import MprisService
       service = MprisService(engine)
       await service.start()   # nunca lanza: ver MprisService.start()
       ...
       await service.stop()

   Este camino evita duplicar loops y evita que las llamadas D-Bus terminen
   ejecutándose en un hilo distinto al del resto de la app.

Nota sobre reconexión: el best-effort aplica solo al arranque. Si el bus cae
después de una conexión exitosa, el fallo se loguea en el momento en que se
detecta (p. ej. al intentar emitir ``PropertiesChanged``) pero este módulo no
intenta reconectar automáticamente.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import threading
from typing import Any, Dict, Optional

from ytaudio.models import PlaybackState, PlayerStatus, RepeatMode, Track

BUS_NAME = "org.mpris.MediaPlayer2.ytaudio"
OBJECT_PATH = "/org/mpris/MediaPlayer2"
_NO_TRACK_ID = "/org/mpris/MediaPlayer2/TrackList/NoTrack"

_PLAYBACK_STATUS = {
    PlaybackState.PLAYING: "Playing",
    PlaybackState.PAUSED: "Paused",
    PlaybackState.STOPPED: "Stopped",
}

_LOOP_STATUS = {
    RepeatMode.OFF: "None",
    RepeatMode.ONE: "Track",
    RepeatMode.ALL: "Playlist",
}

_TRACKID_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_]")

# Importamos dbus-next en un único lugar, de forma perezosa a nivel de
# módulo: si el paquete no está instalado, `import ytaudio.mpris` debe seguir
# funcionando sin problema (solo fallará, en silencio, cuando se intente
# arrancar el servicio de verdad).
_DBUS_NEXT_IMPORT_ERROR: Optional[BaseException] = None
try:
    from dbus_next import Variant
    from dbus_next.aio import MessageBus
    from dbus_next.constants import BusType, NameFlag, PropertyAccess, RequestNameReply
    from dbus_next.service import ServiceInterface, dbus_property, method
except ImportError as _exc:  # pragma: no cover - depende del entorno
    _DBUS_NEXT_IMPORT_ERROR = _exc


def _warn(msg: str) -> None:
    print(f"ytaudio.mpris: WARNING: {msg}", file=sys.stderr)


def _track_object_path(track_id: str) -> str:
    """Convierte un Track.id arbitrario en un segmento de object-path D-Bus válido."""
    safe = _TRACKID_UNSAFE_RE.sub("_", track_id or "") or "unknown"
    return f"/org/mpris/MediaPlayer2/ytaudio/track/{safe}"


def _volume_to_mpris(vol: int) -> float:
    """mpv usa 0-130 (con overdrive); MPRIS2 espera un float en [0.0, 1.0]."""
    return max(0.0, min(1.0, vol / 100.0))


def _volume_from_mpris(vol: float) -> int:
    return max(0, min(130, round(vol * 100)))


def _build_metadata(track: Optional[Track], duration: Optional[float] = None) -> Dict[str, Any]:
    if track is None:
        return {"mpris:trackid": Variant("o", _NO_TRACK_ID)}

    meta: Dict[str, Any] = {
        "mpris:trackid": Variant("o", _track_object_path(track.id)),
        "xesam:title": Variant("s", track.title or ""),
        "xesam:url": Variant("s", track.url or ""),
    }
    if track.uploader:
        meta["xesam:artist"] = Variant("as", [track.uploader])
    length = duration if duration is not None else track.duration
    if length:
        meta["mpris:length"] = Variant("x", int(length * 1_000_000))
    return meta


if _DBUS_NEXT_IMPORT_ERROR is None:

    class _RootInterface(ServiceInterface):
        """org.mpris.MediaPlayer2 — lo mínimo para que un shell de escritorio
        reconozca el reproductor. ytaudio es una app de terminal: no puede
        "levantarse" a primer plano ni cerrarse desde acá, así que
        CanQuit/CanRaise son False y Raise/Quit son no-ops."""

        def __init__(self) -> None:
            super().__init__("org.mpris.MediaPlayer2")

        # NOTA: los métodos @method() de dbus-next inspeccionan la anotación
        # de retorno literal del decorado (parse_annotation) para deducir la
        # firma D-Bus; con `from __future__ import annotations` activo en
        # este archivo, escribir `-> None` explícito la convierte en el
        # string "None" y hace que dbus-next intente parsearla como texto de
        # firma D-Bus (falla). Por eso estos métodos sin valor de retorno
        # simplemente omiten la anotación (queda `inspect.Signature.empty`,
        # que dbus-next interpreta correctamente como "sin argumentos de
        # salida").
        @method()
        def Raise(self):
            return

        @method()
        def Quit(self):
            return

        @dbus_property(access=PropertyAccess.READ)
        def CanQuit(self) -> "b":
            return False

        @dbus_property(access=PropertyAccess.READ)
        def CanRaise(self) -> "b":
            return False

        @dbus_property(access=PropertyAccess.READ)
        def HasTrackList(self) -> "b":
            return False

        @dbus_property(access=PropertyAccess.READ)
        def Identity(self) -> "s":
            return "ytaudio"

        @dbus_property(access=PropertyAccess.READ)
        def SupportedUriSchemes(self) -> "as":
            return []

        @dbus_property(access=PropertyAccess.READ)
        def SupportedMimeTypes(self) -> "as":
            return []

    class _PlayerInterface(ServiceInterface):
        """org.mpris.MediaPlayer2.Player — delega los controles en `engine`."""

        def __init__(self, engine: Any) -> None:
            super().__init__("org.mpris.MediaPlayer2.Player")
            self._engine = engine
            self._playback_status = "Stopped"
            self._volume = 1.0
            self._loop_status = "None"
            self._shuffle = False
            self._position_us = 0
            self._track_id: Optional[str] = None
            self._metadata: Dict[str, Any] = _build_metadata(None)

        # -- helpers internos (no expuestos por D-Bus) ----------------------

        def _safe_call(self, fn, *args) -> None:
            try:
                fn(*args)
            except Exception as exc:  # nunca dejar que un fallo del engine
                # se propague como traceback crudo hacia el cliente D-Bus.
                _warn(f"fallo al invocar {getattr(fn, '__name__', fn)!r} desde MPRIS2: {exc!r}")

        def _set_track(self, track: Optional[Track], duration: Optional[float] = None) -> None:
            self._track_id = track.id if track is not None else None
            self._metadata = _build_metadata(track, duration)

        def apply_status(self, status: PlayerStatus) -> None:
            """Actualiza el estado cacheado y emite PropertiesChanged para lo
            que haya cambiado. Se asume que corre en el loop asyncio dueño
            del bus (ver MprisService.on_status_changed)."""
            changed: Dict[str, Any] = {}

            new_playback = _PLAYBACK_STATUS.get(status.state, "Stopped")
            if new_playback != self._playback_status:
                self._playback_status = new_playback
                changed["PlaybackStatus"] = new_playback

            new_volume = _volume_to_mpris(status.volume)
            if abs(new_volume - self._volume) > 1e-6:
                self._volume = new_volume
                changed["Volume"] = new_volume

            new_loop = _LOOP_STATUS.get(status.repeat, "None")
            if new_loop != self._loop_status:
                self._loop_status = new_loop
                changed["LoopStatus"] = new_loop

            if status.shuffle != self._shuffle:
                self._shuffle = status.shuffle
                changed["Shuffle"] = status.shuffle

            incoming_track_id = status.track.id if status.track is not None else None
            if incoming_track_id != self._track_id:
                self._set_track(status.track, status.duration)
                changed["Metadata"] = self._metadata

            self._position_us = int((status.position or 0.0) * 1_000_000)

            if changed:
                try:
                    self.emit_properties_changed(changed)
                except Exception as exc:
                    _warn(f"fallo al emitir PropertiesChanged en MPRIS2: {exc!r}")

        def apply_track(self, track: Track) -> None:
            if track is None or track.id == self._track_id:
                return
            self._set_track(track)
            try:
                self.emit_properties_changed({"Metadata": self._metadata})
            except Exception as exc:
                _warn(f"fallo al emitir PropertiesChanged (track) en MPRIS2: {exc!r}")

        # -- métodos D-Bus ---------------------------------------------------

        @method()
        def Next(self):
            self._safe_call(self._engine.next)

        @method()
        def Previous(self):
            self._safe_call(self._engine.previous)

        @method()
        def Pause(self):
            # El engine solo expone toggle_pause(); es la mejor aproximación
            # razonable a Pause() sin una API separada de play/pausa.
            self._safe_call(self._engine.toggle_pause)

        @method()
        def PlayPause(self):
            self._safe_call(self._engine.toggle_pause)

        @method()
        def Play(self):
            self._safe_call(self._engine.toggle_pause)

        @method()
        def Stop(self):
            self._safe_call(self._engine.stop)

        @method()
        def Seek(self, offset: "x"):
            self._safe_call(self._engine.seek, offset / 1_000_000.0)

        # -- propiedades D-Bus ------------------------------------------------

        @dbus_property(access=PropertyAccess.READ)
        def PlaybackStatus(self) -> "s":
            return self._playback_status

        @dbus_property(access=PropertyAccess.READ)
        def LoopStatus(self) -> "s":
            return self._loop_status

        @dbus_property(access=PropertyAccess.READ)
        def Shuffle(self) -> "b":
            return self._shuffle

        @dbus_property(access=PropertyAccess.READ)
        def Metadata(self) -> "a{sv}":
            return self._metadata

        @dbus_property(access=PropertyAccess.READWRITE)
        def Volume(self) -> "d":
            return self._volume

        @Volume.setter
        def Volume(self, value: "d") -> None:
            self._volume = max(0.0, min(1.0, value))
            self._safe_call(self._engine.set_volume, _volume_from_mpris(value))

        @dbus_property(access=PropertyAccess.READ)
        def Position(self) -> "x":
            return self._position_us

        @dbus_property(access=PropertyAccess.READ)
        def Rate(self) -> "d":
            return 1.0

        @dbus_property(access=PropertyAccess.READ)
        def MinimumRate(self) -> "d":
            return 1.0

        @dbus_property(access=PropertyAccess.READ)
        def MaximumRate(self) -> "d":
            return 1.0

        @dbus_property(access=PropertyAccess.READ)
        def CanGoNext(self) -> "b":
            return True

        @dbus_property(access=PropertyAccess.READ)
        def CanGoPrevious(self) -> "b":
            return True

        @dbus_property(access=PropertyAccess.READ)
        def CanPlay(self) -> "b":
            return True

        @dbus_property(access=PropertyAccess.READ)
        def CanPause(self) -> "b":
            return True

        @dbus_property(access=PropertyAccess.READ)
        def CanSeek(self) -> "b":
            return True

        @dbus_property(access=PropertyAccess.READ)
        def CanControl(self) -> "b":
            return True


class MprisService:
    """Servicio MPRIS2 para un `engine` duck-typed (ver módulo `player.py`).

    Implementa el Protocol `PlayerObserver` de `models.py` (on_status_changed,
    on_track_started) para poder registrarse vía `engine.add_observer(self)`.
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine
        self._bus: Optional["MessageBus"] = None
        self._root_iface: Optional[Any] = None
        self._player_iface: Optional[Any] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Solo usados por try_start_mpris() para el hilo/loop en background;
        # None si el servicio fue arrancado directamente vía `await start()`
        # dentro del loop de otro integrador.
        self._bg_loop: Optional[asyncio.AbstractEventLoop] = None
        self._bg_thread: Optional[threading.Thread] = None

    @property
    def is_running(self) -> bool:
        return self._bus is not None

    async def start(self) -> None:
        """Conecta al bus de sesión D-Bus, exporta las interfaces MPRIS2 y
        registra el nombre bien conocido `org.mpris.MediaPlayer2.ytaudio`.

        Best-effort: cualquier fallo (paquete dbus-next ausente, bus
        inalcanzable, nombre ya tomado por otra instancia) se loguea como una
        única línea WARNING a stderr y el método retorna normalmente SIN
        lanzar excepciones. Debe llamarse (con `await`) desde una corrutina
        que corre en un loop asyncio ya activo.
        """
        if _DBUS_NEXT_IMPORT_ERROR is not None:
            _warn(
                "dbus-next no está instalado, integración MPRIS2 deshabilitada: "
                f"{_DBUS_NEXT_IMPORT_ERROR!r}"
            )
            return

        if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            _warn(
                "DBUS_SESSION_BUS_ADDRESS no está seteado (sin bus de sesión "
                "D-Bus disponible); integración MPRIS2 deshabilitada"
            )
            return

        bus: Optional["MessageBus"] = None
        try:
            bus = await MessageBus(bus_type=BusType.SESSION).connect()

            root_iface = _RootInterface()
            player_iface = _PlayerInterface(self._engine)
            bus.export(OBJECT_PATH, root_iface)
            bus.export(OBJECT_PATH, player_iface)

            reply = await bus.request_name(BUS_NAME, NameFlag.DO_NOT_QUEUE)
            if reply != RequestNameReply.PRIMARY_OWNER:
                _warn(
                    f"no se pudo registrar el nombre {BUS_NAME} en D-Bus "
                    f"(reply={reply!r}); ¿otra instancia de ytaudio ya está "
                    "corriendo? Integración MPRIS2 deshabilitada"
                )
                try:
                    bus.disconnect()
                except Exception:
                    pass
                return
        except Exception as exc:
            _warn(f"no se pudo iniciar el servicio MPRIS2: {exc!r}")
            if bus is not None:
                try:
                    bus.disconnect()
                except Exception:
                    pass
            return

        self._bus = bus
        self._root_iface = root_iface
        self._player_iface = player_iface
        self._loop = asyncio.get_running_loop()

        try:
            self._engine.add_observer(self)
        except Exception as exc:
            _warn(f"no se pudo registrar MPRIS2 como observer del engine: {exc!r}")

        # Sembrar el estado inicial en las propiedades D-Bus.
        try:
            self.on_status_changed(self._engine.status)
        except Exception as exc:
            _warn(f"no se pudo obtener el estado inicial del engine para MPRIS2: {exc!r}")

    async def stop(self) -> None:
        """Desconecta del bus D-Bus (si estaba conectado) y detiene el hilo
        en background si `try_start_mpris` lo creó. No relanza excepciones."""
        bus, self._bus = self._bus, None
        if bus is not None:
            try:
                bus.disconnect()
            except Exception as exc:
                _warn(f"error al desconectar el servicio MPRIS2: {exc!r}")

        loop, self._bg_loop = self._bg_loop, None
        thread, self._bg_thread = self._bg_thread, None
        if loop is not None and thread is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
                # Si stop() fue programada (run_coroutine_threadsafe) sobre
                # el propio loop en background, esta corrutina corre DENTRO
                # de `thread` — join-earse a sí mismo lanzaría
                # "cannot join current thread". Solo esperamos el join desde
                # cualquier otro hilo (p. ej. el hilo principal).
                if threading.current_thread() is not thread:
                    thread.join(timeout=2.0)
            except Exception as exc:
                _warn(f"error al detener el hilo en background de MPRIS2: {exc!r}")

    # -- PlayerObserver ------------------------------------------------------

    def on_status_changed(self, status: PlayerStatus) -> None:
        if self._player_iface is None or self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._player_iface.apply_status, status)
        except Exception as exc:
            _warn(f"fallo al propagar cambio de estado a MPRIS2: {exc!r}")

    def on_track_started(self, track: Track) -> None:
        if self._player_iface is None or self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._player_iface.apply_track, track)
        except Exception as exc:
            _warn(f"fallo al propagar track nuevo a MPRIS2: {exc!r}")


def try_start_mpris(engine: Any) -> Optional[MprisService]:
    """Intenta arrancar el servicio MPRIS2 sobre D-Bus para `engine`, de forma
    SÍNCRONA y segura de llamar incondicionalmente desde el arranque de la
    app (no-async).

    Internamente crea un hilo daemon con su propio loop asyncio dedicado para
    que la conexión D-Bus siga viva (y atendiendo llamadas entrantes como
    PlayPause/Next/Previous) después de que esta función retorne — un
    `asyncio.run()` común cerraría el loop, y con él el socket D-Bus, apenas
    terminase el `await service.start()`.

    En cualquier fallo (dbus-next ausente, sin bus de sesión D-Bus / socket
    inalcanzable, o nombre bien conocido ya tomado por otra instancia) se
    loguea una única línea WARNING a stderr y se devuelve `None`. Esta
    función NUNCA lanza una excepción hacia afuera.

    Si quien llama ya tiene su propio loop asyncio corriendo (p. ej. una app
    Textual dentro de `on_mount`), NO usar esta función: instanciar
    `MprisService(engine)` directamente y hacer `await service.start()` en
    ese loop, para evitar duplicar loops y evitar que el engine reciba
    llamadas D-Bus desde un hilo distinto al del resto de la app.
    """
    service = MprisService(engine)

    if _DBUS_NEXT_IMPORT_ERROR is not None:
        _warn(
            "dbus-next no está instalado, integración MPRIS2 deshabilitada: "
            f"{_DBUS_NEXT_IMPORT_ERROR!r}"
        )
        return None

    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        _warn(
            "DBUS_SESSION_BUS_ADDRESS no está seteado (sin bus de sesión "
            "D-Bus disponible); integración MPRIS2 deshabilitada"
        )
        return None

    loop = asyncio.new_event_loop()

    def _run_forever() -> None:
        asyncio.set_event_loop(loop)
        try:
            loop.run_forever()
        finally:
            loop.close()

    thread = threading.Thread(target=_run_forever, name="ytaudio-mpris-loop", daemon=True)

    try:
        thread.start()
        future = asyncio.run_coroutine_threadsafe(service.start(), loop)
        future.result(timeout=5.0)
    except Exception as exc:
        _warn(f"no se pudo iniciar el servicio MPRIS2: {exc!r}")
        try:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=2.0)
        except Exception:
            pass
        return None

    if not service.is_running:
        # start() ya logueó el motivo puntual (bus inalcanzable, nombre
        # tomado, etc.); acá solo limpiamos el hilo que quedó sin uso.
        try:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=2.0)
        except Exception:
            pass
        return None

    service._bg_loop = loop
    service._bg_thread = thread
    return service
