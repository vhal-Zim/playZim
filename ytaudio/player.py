"""Motor de reproducción: controla mpv headless vía su socket JSON-IPC.

Este es el módulo más importante del paquete: lanza `mpv --idle --no-terminal
--no-video` y le habla por su socket unix nativo (`--input-ipc-server`), de
forma que una interfaz Textual (construida después, en otra etapa) pueda
tener sus propios controles interactivos sin que mpv se apodere de la
terminal.

Desacoplamiento: este módulo NO importa `ytaudio.library`, `ytaudio.history`
ni `ytaudio.cache` — `PlayerEngine` recibe esos tres colaboradores ya
instanciados por duck-typing (solo se documentan/usan sus métodos:
`library.upsert(track)`, `history.record(track)`,
`cache.resolve_playable(track) -> PlayableSource` con `.path`/`.is_local`).
Esto permite testear `PlayerEngine` con fakes propios sin depender de que
esos archivos existan todavía.
"""

from __future__ import annotations

import itertools
import json
import queue
import random
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from ytaudio.audiocap import AudioLevelCapture
from ytaudio.config import AUDIO_CLIENT_NAME, MPV_SOCKET
from ytaudio.models import PlaybackState, PlayerStatus, RepeatMode, Track


def _log(msg: str) -> None:
    print(f"[ytaudio.player] {msg}", file=sys.stderr)


# --------------------------------------------------------------------------
# Capa de transporte: socket JSON-IPC de mpv
# --------------------------------------------------------------------------


class MpvIPCError(RuntimeError):
    """Un comando mpv devolvió error, o no se pudo hablar con mpv."""


class _MpvIPC:
    """Maneja el proceso mpv headless y su socket JSON-IPC.

    Dos hilos daemon:
      - "reader": lee líneas del socket sin parar, resuelve respuestas de
        comandos pendientes y encola eventos en una `queue.Queue`.
      - "dispatcher": consume esa cola y llama al callback `on_event` del
        usuario. Está separado del "reader" a propósito: si `on_event`
        necesita mandar un comando síncrono a mpv (p.ej. tras "file-loaded"),
        el hilo "reader" tiene que seguir libre para leer la respuesta de ese
        comando — si el callback corriera dentro del hilo "reader" mismo,
        un comando síncrono lanzado desde el callback se colgaría para
        siempre esperando una respuesta que nadie leería.
    """

    def __init__(
        self,
        socket_path: Path,
        on_event: Callable[[dict], None],
        mpv_binary: str = "mpv",
    ) -> None:
        self._socket_path = socket_path
        self._on_event = on_event
        self._mpv_binary = mpv_binary

        self._proc: Optional[subprocess.Popen] = None
        self._sock: Optional[socket.socket] = None
        self._sockfile = None

        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[int, dict[str, Any]] = {}
        self._id_counter = itertools.count(1)

        self._event_queue: "queue.Queue[Optional[dict]]" = queue.Queue()
        self._reader_thread: Optional[threading.Thread] = None
        self._dispatcher_thread: Optional[threading.Thread] = None
        self._closed = False

    # -- ciclo de vida ---------------------------------------------------

    def start(self, connect_timeout: float = 5.0) -> None:
        self._socket_path.unlink(missing_ok=True)

        self._proc = subprocess.Popen(
            [
                self._mpv_binary,
                "--idle",
                "--no-terminal",
                "--no-video",
                "--no-input-terminal",
                f"--input-ipc-server={self._socket_path}",
                f"--audio-client-name={AUDIO_CLIENT_NAME}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        deadline = time.monotonic() + connect_timeout
        sock: Optional[socket.socket] = None
        last_exc: Optional[Exception] = None
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise MpvIPCError(
                    f"mpv terminó antes de exponer el socket IPC (returncode={self._proc.returncode})"
                )
            if self._socket_path.exists():
                try:
                    candidate = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    candidate.connect(str(self._socket_path))
                    sock = candidate
                    break
                except OSError as exc:
                    last_exc = exc
            time.sleep(0.05)

        if sock is None:
            raise MpvIPCError(f"no se pudo conectar al socket de mpv: {last_exc}")

        self._sock = sock
        self._sockfile = sock.makefile("r", encoding="utf-8", newline="\n")

        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True, name="mpv-ipc-reader")
        self._dispatcher_thread = threading.Thread(
            target=self._dispatch_loop, daemon=True, name="mpv-ipc-dispatcher"
        )
        self._reader_thread.start()
        self._dispatcher_thread.start()

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None and not self._closed

    def close(self, timeout: float = 3.0) -> None:
        if self._closed:
            return
        self._closed = True

        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                try:
                    self._proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    pass
            except Exception as exc:  # noqa: BLE001 - herramienta personal
                _log(f"error al terminar el proceso mpv: {exc}")

        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass

        # despertar a cualquier command() bloqueado esperando respuesta
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for entry in pending:
            entry["response"] = {"error": "mpv cerrado"}
            entry["event"].set()

        self._event_queue.put(None)  # señal de fin para el dispatcher

        if self._reader_thread is not None:
            self._reader_thread.join(timeout=timeout)
        if self._dispatcher_thread is not None:
            self._dispatcher_thread.join(timeout=timeout)

        self._socket_path.unlink(missing_ok=True)

    # -- comandos ---------------------------------------------------------

    def command(self, *args: Any, timeout: float = 5.0) -> Any:
        if self._sock is None or self._closed:
            raise MpvIPCError("mpv IPC no está conectado")

        req_id = next(self._id_counter)
        ev = threading.Event()
        with self._pending_lock:
            self._pending[req_id] = {"event": ev, "response": None}

        payload = json.dumps({"command": list(args), "request_id": req_id}) + "\n"
        try:
            with self._write_lock:
                self._sock.sendall(payload.encode("utf-8"))
        except OSError as exc:
            with self._pending_lock:
                self._pending.pop(req_id, None)
            raise MpvIPCError(f"fallo al escribir en el socket de mpv: {exc}") from exc

        got = ev.wait(timeout)
        with self._pending_lock:
            entry = self._pending.pop(req_id, None)

        if not got or entry is None:
            raise MpvIPCError(f"timeout esperando respuesta de mpv para {args!r}")

        response = entry["response"] or {}
        if response.get("error") not in ("success", None):
            raise MpvIPCError(f"comando mpv {args!r} falló: {response.get('error')}")
        return response.get("data")

    def get_property(self, name: str) -> Any:
        return self.command("get_property", name)

    def set_property(self, name: str, value: Any) -> None:
        self.command("set_property", name, value)

    # -- hilos internos -----------------------------------------------------

    def _read_loop(self) -> None:
        try:
            assert self._sockfile is not None
            for line in self._sockfile:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if "event" in msg:
                    self._event_queue.put(msg)
                elif "request_id" in msg:
                    rid = msg["request_id"]
                    with self._pending_lock:
                        entry = self._pending.get(rid)
                    if entry is not None:
                        entry["response"] = msg
                        entry["event"].set()
        except OSError:
            pass
        finally:
            if not self._closed:
                # el socket se cerró sin que nosotros lo pidiéramos: mpv murió
                self._event_queue.put({"event": "__disconnected__"})

    def _dispatch_loop(self) -> None:
        while True:
            msg = self._event_queue.get()
            if msg is None:
                break
            try:
                self._on_event(msg)
            except Exception as exc:  # noqa: BLE001 - herramienta personal
                _log(f"error manejando evento mpv {msg!r}: {exc}")


# --------------------------------------------------------------------------
# PlayerEngine
# --------------------------------------------------------------------------


class PlayerEngine:
    """Motor de reproducción: cola, shuffle, repeat, y control de mpv.

    `library`, `history` y `cache` se reciben por duck-typing (ver el
    docstring del módulo). `settings` solo se usa para los valores
    iniciales de volumen/shuffle/repeat; no se persiste de vuelta acá.
    """

    def __init__(
        self,
        library: Any,
        history: Any,
        cache: Any,
        settings: Any,
        observers: Optional[list] = None,
        *,
        socket_path: Path = MPV_SOCKET,
        mpv_binary: str = "mpv",
    ) -> None:
        self._library = library
        self._history = history
        self._cache = cache
        self._socket_path = socket_path
        self._mpv_binary = mpv_binary

        self._observers: list = list(observers) if observers else []

        self._lock = threading.RLock()

        # Cola visible: lista estable de Track, nunca reordenada por shuffle.
        self._queue: list[Track] = []
        # Orden de selección: permutación de índices de self._queue.
        # Cuando shuffle está apagado, es simplemente range(len(queue)).
        self._order: list[int] = []
        # Posición dentro de self._order que corresponde a la pista actual
        # (-1 si no hay ninguna pista actual).
        self._pos: int = -1

        self._current_track: Optional[Track] = None
        self._pending_start_track: Optional[Track] = None
        self._state: PlaybackState = PlaybackState.STOPPED

        self._volume: int = getattr(settings, "volume", 100)
        self._shuffle: bool = getattr(settings, "shuffle", False)
        self._repeat: RepeatMode = getattr(settings, "repeat", RepeatMode.OFF)

        self._ipc: Optional[_MpvIPC] = None

        # Ids de track para los que ya se intentó el fallback de descarga
        # tras un fallo de streaming en esta sesión — evita reintentar en
        # bucle si la descarga también falla (ver _retry_with_download).
        self._fallback_attempted: set = set()

        self._level_listeners: list[Callable[[list], None]] = []
        self._level_capture = AudioLevelCapture(AUDIO_CLIENT_NAME, on_levels=self._dispatch_levels)

    # -- observers ---------------------------------------------------------

    def add_observer(self, obs: Any) -> None:
        self._observers.append(obs)

    def add_level_listener(self, callback: Callable[[list], None]) -> None:
        """Registra un callback para niveles de audio reales por banda (0.0-1.0).

        Se llama desde el hilo lector de `audiocap.py` (no el hilo principal
        ni ningún loop de asyncio) — quien se suscriba debe marshalear de
        vuelta a su propio hilo/loop si hace falta (igual que hace
        `mpris.py` con `call_soon_threadsafe`).
        """
        self._level_listeners.append(callback)

    def _dispatch_levels(self, levels: list) -> None:
        for cb in list(self._level_listeners):
            try:
                cb(levels)
            except Exception as exc:  # noqa: BLE001 - herramienta personal
                _log(f"level_listener falló: {exc}")

    def _notify_status(self) -> None:
        status = self.status
        for obs in list(self._observers):
            try:
                obs.on_status_changed(status)
            except Exception as exc:  # noqa: BLE001 - herramienta personal
                _log(f"observer.on_status_changed falló: {exc}")

    def _notify_track_started(self, track: Track) -> None:
        for obs in list(self._observers):
            try:
                obs.on_track_started(track)
            except Exception as exc:  # noqa: BLE001
                _log(f"observer.on_track_started falló: {exc}")

    # -- arranque perezoso de mpv --------------------------------------------

    def _ensure_started(self) -> None:
        if self._ipc is not None and self._ipc.is_alive():
            return
        self._ipc = _MpvIPC(self._socket_path, on_event=self._handle_mpv_event, mpv_binary=self._mpv_binary)
        self._ipc.start()
        try:
            self._ipc.set_property("volume", self._volume)
        except MpvIPCError as exc:
            _log(f"no se pudo aplicar el volumen inicial: {exc}")

        if not self._level_capture.is_running():
            self._level_capture.start()  # best-effort: no-op si pw-record no está

    # -- cola ---------------------------------------------------------------

    @property
    def queue(self) -> list[Track]:
        with self._lock:
            return list(self._queue)

    def enqueue(self, track: Track) -> None:
        with self._lock:
            new_idx = len(self._queue)
            self._queue.append(track)
            if self._shuffle and 0 <= self._pos < len(self._order):
                insert_at = random.randint(self._pos + 1, len(self._order))
                self._order.insert(insert_at, new_idx)
            else:
                self._order.append(new_idx)

    def enqueue_many(self, tracks: list[Track]) -> None:
        for t in tracks:
            self.enqueue(t)

    def play_now(self, track: Track) -> None:
        """Reproduce `track` ya, sin esperar a que termine lo que sonaba.

        Decisión de diseño (el contrato admite ambas lecturas: "inserta al
        frente" / "la pone como actual"): si `track` ya está en la cola
        visible (mismo `id`), no se duplica — simplemente se la marca como
        actual y se reproduce, dejando la cola visible intacta. Si no está,
        se inserta una entrada nueva al frente de la cola. Esto evita que
        `enqueue(t); play_now(t)` deje `t` dos veces en la cola.

        Si `track` es la que YA está sonando (mismo id, estado
        PLAYING/PAUSED), no se recarga desde 0 — un click/Enter repetido
        sobre la fila actual no debe reiniciar la posición. Si estaba en
        pausa, reanuda (comportamiento esperable al re-seleccionarla,
        similar a la mayoría de reproductores); si ya estaba sonando, es
        no-op. Para reiniciar la posición explícitamente, ver
        `restart_current()`.
        """
        with self._lock:
            already_current = (
                self._current_track is not None
                and self._current_track.id == track.id
                and self._state in (PlaybackState.PLAYING, PlaybackState.PAUSED)
            )
            existing_idx = next((i for i, t in enumerate(self._queue) if t.id == track.id), None)
            if existing_idx is not None:
                queue_idx = existing_idx
                self._queue[queue_idx] = track
            else:
                self._queue.insert(0, track)
                self._order = [i + 1 for i in self._order]
                self._order.insert(0, 0)
                queue_idx = 0

            try:
                pos_in_order = self._order.index(queue_idx)
            except ValueError:
                pos_in_order = 0
                self._order.insert(0, queue_idx)
            self._pos = pos_in_order

        if already_current:
            if self._state == PlaybackState.PAUSED:
                self.toggle_pause()
            return
        self._load_and_play(queue_idx)

    def restart_current(self) -> None:
        """Vuelve la posición de la pista actual a 0, sin recargar el archivo."""
        with self._lock:
            track = self._current_track
        if track is None or self._ipc is None:
            return
        try:
            self._ipc.command("seek", 0, "absolute")
        except MpvIPCError as exc:
            _log(f"fallo al reiniciar la pista actual: {exc}")

    def remove_from_queue(self, index: int) -> None:
        with self._lock:
            if not (0 <= index < len(self._queue)):
                return
            current_queue_idx = self._current_queue_index()
            del self._queue[index]

            new_order: list[int] = []
            new_pos = -1
            for pos_in_order, qidx in enumerate(self._order):
                if qidx == index:
                    continue
                shifted = qidx - 1 if qidx > index else qidx
                new_order.append(shifted)
                if pos_in_order == self._pos:
                    new_pos = len(new_order) - 1
            self._order = new_order

            if current_queue_idx == index:
                # se eliminó la pista que estaba sonando de la cola; mpv
                # sigue reproduciéndola hasta que termine, pero deja de
                # estar "en la cola" — no tenemos una posición de cola
                # válida hasta el próximo avance.
                self._pos = min(new_pos, len(self._order) - 1) if self._order else -1
            else:
                self._pos = new_pos

    def clear_queue(self) -> None:
        with self._lock:
            self._queue = []
            self._order = []
            self._pos = -1
        self.stop()

    def _current_queue_index(self) -> Optional[int]:
        if 0 <= self._pos < len(self._order):
            return self._order[self._pos]
        return None

    # -- transporte -----------------------------------------------------------

    def _load_and_play(self, queue_idx: int) -> None:
        with self._lock:
            if not (0 <= queue_idx < len(self._queue)):
                return
            track = self._queue[queue_idx]
            self._pending_start_track = track

        try:
            self._ensure_started()
        except MpvIPCError as exc:
            _log(f"no se pudo arrancar mpv: {exc}")
            with self._lock:
                self._pending_start_track = None
                self._state = PlaybackState.STOPPED
            self._notify_status()
            return

        try:
            source = self._cache.resolve_playable(track)
        except Exception as exc:  # noqa: BLE001
            _log(f"fallo al resolver fuente para '{track.title}': {exc}")
            with self._lock:
                self._pending_start_track = None
            return

        assert self._ipc is not None
        try:
            self._ipc.command("loadfile", source.path, "replace")
        except MpvIPCError as exc:
            _log(f"fallo al cargar '{track.title}' en mpv: {exc}")
            with self._lock:
                self._pending_start_track = None
                self._state = PlaybackState.STOPPED
            self._notify_status()

    def toggle_pause(self) -> None:
        with self._lock:
            state = self._state
        if state == PlaybackState.PLAYING:
            new_state = PlaybackState.PAUSED
            want_pause = True
        elif state == PlaybackState.PAUSED:
            new_state = PlaybackState.PLAYING
            want_pause = False
        else:
            return  # nada cargado, no-op

        if self._ipc is not None:
            try:
                self._ipc.set_property("pause", want_pause)
            except MpvIPCError as exc:
                _log(f"fallo al pausar/reanudar: {exc}")
                return

        with self._lock:
            self._state = new_state
        self._notify_status()

    def stop(self) -> None:
        if self._ipc is not None:
            try:
                self._ipc.command("stop")
            except MpvIPCError as exc:
                _log(f"fallo al detener mpv: {exc}")
        with self._lock:
            self._state = PlaybackState.STOPPED
        self._notify_status()

    def _advance(self, auto: bool) -> None:
        """Avanza a la siguiente pista según el modo de repeat.

        `auto=True` significa que el avance lo disparó el fin natural de una
        pista (evento end-file/eof de mpv); `auto=False` significa que el
        usuario apretó "next" manualmente.

        Decisión de diseño (no especificada explícitamente en el contrato):
        el modo ONE ("repite la pista actual indefinidamente") solo aplica
        al avance automático — es el comportamiento estándar de cualquier
        reproductor: repeat-one hace que una pista se repita sola si nadie
        toca nada, pero un "next" manual del usuario siempre avanza a la
        siguiente pista de la cola, igual que en Spotify/VLC/etc.
        """
        with self._lock:
            if auto and self._repeat == RepeatMode.ONE:
                idx = self._current_queue_index()
                if idx is None:
                    return
                target = idx
            else:
                new_pos = self._pos + 1
                if new_pos < len(self._order):
                    self._pos = new_pos
                    target = self._order[self._pos]
                elif self._repeat == RepeatMode.ALL and self._order:
                    self._pos = 0
                    target = self._order[0]
                else:
                    self._state = PlaybackState.STOPPED
                    self._notify_status()
                    return
        self._load_and_play(target)

    def next(self) -> None:
        self._advance(auto=False)

    def previous(self) -> None:
        with self._lock:
            if not self._order:
                return
            new_pos = self._pos - 1
            if new_pos >= 0:
                self._pos = new_pos
                target = self._order[self._pos]
            elif self._repeat == RepeatMode.ALL:
                self._pos = len(self._order) - 1
                target = self._order[self._pos]
            else:
                return  # ya en la primera pista, no-op
        self._load_and_play(target)

    def seek(self, delta_seconds: float) -> None:
        if self._ipc is None:
            return
        try:
            self._ipc.command("seek", delta_seconds, "relative")
        except MpvIPCError as exc:
            _log(f"fallo al buscar posición: {exc}")

    def set_volume(self, vol: int) -> None:
        vol = max(0, min(130, vol))
        with self._lock:
            self._volume = vol
        if self._ipc is not None:
            try:
                self._ipc.set_property("volume", vol)
            except MpvIPCError as exc:
                _log(f"fallo al ajustar volumen: {exc}")
        self._notify_status()

    def set_shuffle(self, enabled: bool) -> None:
        with self._lock:
            cur_idx = self._current_queue_index()
            indices = list(range(len(self._queue)))
            if enabled:
                if cur_idx is not None:
                    indices.remove(cur_idx)
                    random.shuffle(indices)
                    self._order = [cur_idx] + indices
                    self._pos = 0
                else:
                    random.shuffle(indices)
                    self._order = indices
                    self._pos = -1
            else:
                self._order = indices  # range(len(queue)), ya ordenado
                self._pos = cur_idx if cur_idx is not None else -1
            self._shuffle = enabled
        self._notify_status()

    def set_repeat(self, mode: RepeatMode) -> None:
        with self._lock:
            self._repeat = mode
        self._notify_status()

    # -- estado ---------------------------------------------------------------

    @property
    def status(self) -> PlayerStatus:
        with self._lock:
            track = self._current_track
            state = self._state
            volume = self._volume
            shuffle = self._shuffle
            repeat = self._repeat

        position = 0.0
        duration = track.duration if track is not None else None

        if self._ipc is not None and self._ipc.is_alive():
            try:
                pos = self._ipc.get_property("time-pos")
                if pos is not None:
                    position = float(pos)
            except MpvIPCError:
                pass
            try:
                dur = self._ipc.get_property("duration")
                if dur is not None:
                    duration = float(dur)
            except MpvIPCError:
                pass

        return PlayerStatus(
            track=track,
            state=state,
            position=position,
            duration=duration,
            volume=volume,
            shuffle=shuffle,
            repeat=repeat,
        )

    # -- eventos de mpv ---------------------------------------------------------

    def _handle_mpv_event(self, msg: dict) -> None:
        event = msg.get("event")

        if event == "__disconnected__":
            _log("conexión con mpv perdida (el proceso terminó inesperadamente)")
            with self._lock:
                self._state = PlaybackState.STOPPED
            self._notify_status()
            return

        if event == "file-loaded":
            with self._lock:
                track = self._pending_start_track
                self._pending_start_track = None
            if track is None:
                return

            try:
                self._history.record(track)
            except Exception as exc:  # noqa: BLE001
                _log(f"history.record falló para '{track.title}': {exc}")
            try:
                self._library.upsert(track)
            except Exception as exc:  # noqa: BLE001
                _log(f"library.upsert falló para '{track.title}': {exc}")

            with self._lock:
                self._current_track = track
                self._state = PlaybackState.PLAYING

            self._notify_track_started(track)
            self._notify_status()
            return

        if event == "end-file":
            reason = msg.get("reason")
            if reason == "eof":
                self._advance(auto=True)
            elif reason == "error":
                with self._lock:
                    failed_track = self._pending_start_track
                if failed_track is not None and failed_track.id not in self._fallback_attempted:
                    # Streaming directo falló ANTES de que la pista llegara a
                    # sonar (nunca llegó "file-loaded" para este intento) —
                    # típico de videos donde YouTube firma la URL para un
                    # cliente/headers que el fetcher HTTP de mpv no replica
                    # (403 al abrir, aunque yt-dlp resolvió bien la URL).
                    # Reintentamos UNA vez descargando el archivo entero vía
                    # yt-dlp (que sí maneja esto correctamente de punta a
                    # punta) y reproduciendo desde el archivo local.
                    self._fallback_attempted.add(failed_track.id)
                    _log(f"streaming falló para '{failed_track.title}', reintentando con descarga local...")
                    self._retry_with_download(failed_track)
                else:
                    _log(f"error de mpv reproduciendo el archivo: {msg.get('file_error', 'desconocido')}")
                    with self._lock:
                        self._pending_start_track = None
                    self._advance(auto=True)
            # "stop"/"quit": ya manejamos el estado nosotros mismos.
            return

    def _retry_with_download(self, track: Track) -> None:
        def _on_done(t: Track, local_path: Path) -> None:
            if self._ipc is None:
                return
            with self._lock:
                self._pending_start_track = t
            try:
                self._ipc.command("loadfile", str(local_path), "replace")
            except MpvIPCError as exc:
                _log(f"fallo al cargar el archivo de respaldo para '{t.title}': {exc}")
                with self._lock:
                    self._pending_start_track = None
                self._advance(auto=True)

        def _on_error(t: Track, exc: Exception) -> None:
            _log(f"la descarga de respaldo también falló para '{t.title}': {exc}")
            with self._lock:
                self._pending_start_track = None
            self._advance(auto=True)

        self._cache.ensure_cached_async(track, on_done=_on_done, on_error=_on_error)

    # -- apagado ---------------------------------------------------------------

    def shutdown(self) -> None:
        self._level_capture.stop()
        if self._ipc is not None:
            self._ipc.close()
            self._ipc = None
        with self._lock:
            self._state = PlaybackState.STOPPED
