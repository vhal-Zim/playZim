"""Caché offline de audio descargado.

Si una pista ya fue descargada antes, se reproduce el archivo local (sin red).
Si no, se devuelve la URL de streaming original de inmediato y se dispara una
descarga en background para que la próxima reproducción sea local.

No importa `ytaudio.resolver` a nivel de módulo: la función de descarga se
inyecta (`download_audio_fn`) o, si no se provee, se resuelve perezosamente
dentro de `_download()` en el momento en que realmente hace falta descargar
algo. Esto permite testear este módulo de forma aislada, y funciona en
producción una vez que `resolver.py` exista.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from ytaudio.config import CACHE_AUDIO_DIR, CACHE_INDEX_FILE, atomic_write_json, read_json
from ytaudio.models import DownloadAudioFn, PlayableSource, Track


class AudioCache:
    def __init__(
        self,
        index_path: Path = CACHE_INDEX_FILE,
        audio_dir: Path = CACHE_AUDIO_DIR,
        download_audio_fn: Optional[DownloadAudioFn] = None,
    ):
        self._index_path = index_path
        self._audio_dir = audio_dir
        self._download_audio_fn = download_audio_fn
        self._lock = threading.Lock()
        self._index: dict = read_json(self._index_path, {})
        # Descargas en curso: track.id -> lista de (on_done, on_error) a
        # notificar cuando termine. Garantiza UNA sola descarga por track a
        # la vez — dos descargas simultáneas escriben el mismo `<id>.<ext>`
        # (y yt_dlp retoma el `.part` ajeno), dejando un archivo corrupto.
        self._inflight: dict[str, list] = {}

    # -- lectura -----------------------------------------------------

    def get_local_path(self, track: Track) -> Optional[Path]:
        """Devuelve la ruta local cacheada para `track`, o None si no está.

        Si el índice referencia un archivo que ya no existe en disco, la
        entry se elimina del índice (auto-reparación) y se devuelve None.
        """
        with self._lock:
            entry = self._index.get(track.id)
            if entry is None:
                return None
            path = Path(entry["path"])
            if path.exists():
                return path
            del self._index[track.id]
            self._persist_locked()
            return None

    def resolve_playable(self, track: Track, auto_cache: bool = True) -> PlayableSource:
        """Resuelve una fuente reproducible para `track`.

        Si ya está cacheada, devuelve la ruta local (is_local=True) sin
        tocar la red. Si no, devuelve la URL de streaming original
        (is_local=False) para que la reproducción arranque de inmediato, y
        si `auto_cache` es True dispara una descarga en background para
        poblar la caché para la próxima vez.
        """
        local_path = self.get_local_path(track)
        if local_path is not None:
            return PlayableSource(path=str(local_path), is_local=True)

        if auto_cache:
            self.ensure_cached_async(track)
        return PlayableSource(path=track.url, is_local=False)

    def usage_bytes(self) -> int:
        with self._lock:
            return sum(entry.get("size_bytes", 0) for entry in self._index.values())

    # -- escritura -----------------------------------------------------

    def ensure_cached_async(
        self,
        track: Track,
        on_done: Optional[Callable[[Track, Path], None]] = None,
        on_error: Optional[Callable[[Track, Exception], None]] = None,
    ) -> None:
        """Lanza una descarga en background si `track` no está cacheada aún.

        No bloquea. Los errores de descarga se loguean a stderr y, si se
        pasó `on_error`, también se entregan ahí (además del log) para que
        el llamador pueda reaccionar (ej. reintentar o avisar al usuario)
        en vez de quedarse esperando un `on_done` que nunca llega.

        Si la pista ya está cacheada, `on_done` se llama de inmediato (en el
        hilo del llamador). Si ya hay una descarga en curso para la misma
        pista, no se lanza otra: los callbacks se suman a los de esa descarga.
        """
        local_path = self.get_local_path(track)
        if local_path is not None:
            if on_done is not None:
                self._call_quiet(on_done, track, local_path)
            return

        with self._lock:
            waiters = self._inflight.get(track.id)
            if waiters is not None:
                waiters.append((on_done, on_error))
                return
            self._inflight[track.id] = [(on_done, on_error)]

        def _worker() -> None:
            local_path: Optional[Path] = None
            error: Optional[Exception] = None
            try:
                local_path = self._download(track)
            except Exception as exc:  # noqa: BLE001 - herramienta personal
                error = exc
                print(f"[ytaudio.cache] fallo al descargar '{track.title}' ({track.id}): {exc}", file=sys.stderr)

            with self._lock:
                waiters = self._inflight.pop(track.id, [])
            for done_cb, error_cb in waiters:
                if error is None and done_cb is not None:
                    self._call_quiet(done_cb, track, local_path)
                elif error is not None and error_cb is not None:
                    self._call_quiet(error_cb, track, error)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

    @staticmethod
    def _call_quiet(callback: Callable, track: Track, arg: object) -> None:
        try:
            callback(track, arg)
        except Exception as exc:  # noqa: BLE001
            print(f"[ytaudio.cache] fallo en callback para {track.id}: {exc}", file=sys.stderr)

    def purge(self, track_id: Optional[str] = None) -> None:
        """Borra la caché. Si track_id es None, borra todo; si no, solo esa entry."""
        with self._lock:
            if track_id is None:
                for entry in self._index.values():
                    self._unlink_quiet(Path(entry["path"]))
                self._index = {}
                self._persist_locked()
            else:
                entry = self._index.get(track_id)
                if entry is not None:
                    self._unlink_quiet(Path(entry["path"]))
                    del self._index[track_id]
                    self._persist_locked()

    # -- internos -----------------------------------------------------

    def _download(self, track: Track) -> Path:
        """Descarga `track` a `self._audio_dir` y actualiza el índice.

        Resuelve `self._download_audio_fn` de forma perezosa (import de
        `ytaudio.resolver`) si no se inyectó una función explícita.
        """
        self._audio_dir.mkdir(parents=True, exist_ok=True)

        download_fn = self._download_audio_fn
        if download_fn is None:
            from ytaudio.resolver import download_audio as download_fn

        local_path_str = download_fn(track, str(self._audio_dir))
        local_path = Path(local_path_str)
        if not self._is_playable(local_path):
            self._unlink_quiet(local_path)
            raise RuntimeError(f"el archivo descargado está corrupto o incompleto: {local_path}")
        size_bytes = os.path.getsize(local_path)
        entry = {
            "path": str(local_path),
            "ext": local_path.suffix.lstrip("."),
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "size_bytes": size_bytes,
        }
        with self._lock:
            self._index[track.id] = entry
            self._persist_locked()
        return local_path

    def _persist_locked(self) -> None:
        """Persiste el índice actual. Debe llamarse con self._lock tomado."""
        atomic_write_json(self._index_path, self._index)

    @staticmethod
    def _is_playable(path: Path) -> bool:
        """Chequea con ffprobe que el archivo sea un contenedor de audio legible.

        Si ffprobe no está disponible (o no responde), no bloquea: se asume
        que el archivo está bien, como antes de existir este chequeo.
        """
        ffprobe = shutil.which("ffprobe")
        if ffprobe is None:
            return True
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path.resolve())],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return True
        return result.returncode == 0

    @staticmethod
    def _unlink_quiet(path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            print(f"[ytaudio.cache] no se pudo borrar {path}: {exc}", file=sys.stderr)
