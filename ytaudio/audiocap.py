"""Captura de audio real del stream de mpv (PipeWire) para el ecualizador.

Best-effort: si `pw-record` no está en el sistema (no-PipeWire / no-Linux) o
falla en cualquier punto, `start()` simplemente no hace nada — el llamador
no recibe niveles nunca y el ecualizador se queda en su animación simulada
de fallback. Nunca debe lanzar una excepción hacia el proceso principal.

Cómo funciona: se lanza `pw-record --target <nombre-de-cliente-de-mpv>`
para capturar EXACTAMENTE lo que mpv está reproduciendo (tap directo del
stream de PipeWire, no una descarga/decodificación paralela) — esto
garantiza sincronía perfecta y que los niveles reflejen el volumen real
configurado (mpv aplica su propio volumen antes de que la señal llegue a
sus puertos de salida de PipeWire; verificado empíricamente).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from typing import Callable, Optional

import numpy as np

PW_RECORD = "pw-record"
SAMPLE_RATE = 44100
# 2048 muestras (~46ms a 44.1kHz): con menos resolución de frecuencia (1024)
# las bandas graves más bajas del rango log-espaciado quedan sin ningún bin
# de la FFT adentro (banda "vacía" siempre en 0) — verificado empíricamente.
CHUNK_SAMPLES = 2048
BANDS = 24  # resolución interna, independiente de cuántas barras dibuje el widget
FMIN_HZ = 40.0
FMAX_RATIO = 0.9  # hasta el 90% de Nyquist, evita el borde ruidoso del espectro

# Rango de dB -> 0..1 calibrado empíricamente contra audio real (ver TASKS.md):
# con un piso/techo más ajustado casi todas las bandas quedaban saturadas en 1.0
# la mayor parte del tiempo (canción sonando "siempre al máximo", sin dinámica).
_DB_FLOOR = -40.0
_DB_CEIL = 30.0

_ATTACK = 0.6  # que tan rápido sube una barra al detectar un pico (ataque)
_RELEASE = 0.22  # que tan rápido decae (release) — más lento que el ataque, como un VU-meter


def _log_band_edges(bands: int, sample_rate: int, fft_size: int) -> list[tuple[int, int]]:
    freqs = np.fft.rfftfreq(fft_size, d=1.0 / sample_rate)
    fmax = sample_rate / 2 * FMAX_RATIO
    edges_hz = np.geomspace(FMIN_HZ, fmax, bands + 1)
    edges_idx = [int(np.searchsorted(freqs, f)) for f in edges_hz]
    return list(zip(edges_idx[:-1], edges_idx[1:]))


class AudioLevelCapture:
    """Lee PCM real de mpv vía PipeWire y entrega niveles por banda (0.0-1.0)."""

    def __init__(self, client_name: str, on_levels: Callable[[list[float]], None]) -> None:
        self._client_name = client_name
        self._on_levels = on_levels
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._band_edges = _log_band_edges(BANDS, SAMPLE_RATE, CHUNK_SAMPLES)
        self._smoothed = [0.0] * BANDS
        self._window = np.hanning(CHUNK_SAMPLES)

    @staticmethod
    def available() -> bool:
        return shutil.which(PW_RECORD) is not None

    def start(self) -> None:
        if self._proc is not None or not self.available():
            return
        try:
            self._proc = subprocess.Popen(
                [
                    PW_RECORD,
                    "--target",
                    self._client_name,
                    "--format",
                    "f32",
                    "--rate",
                    str(SAMPLE_RATE),
                    "--channels",
                    "1",
                    "-a",
                    "-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
        except OSError as exc:
            print(f"[ytaudio.audiocap] no se pudo iniciar pw-record: {exc}", file=sys.stderr)
            self._proc = None
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._read_loop, daemon=True, name="audiocap-reader")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:  # noqa: BLE001 - herramienta personal
                try:
                    self._proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            self._proc = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        bytes_per_chunk = CHUNK_SAMPLES * 4  # float32
        buf = b""
        try:
            while not self._stop_event.is_set():
                chunk = self._proc.stdout.read(bytes_per_chunk - len(buf))
                if not chunk:
                    break
                buf += chunk
                if len(buf) < bytes_per_chunk:
                    continue
                samples = np.frombuffer(buf, dtype="<f4")
                buf = b""
                try:
                    levels = self._compute_bands(samples)
                    self._on_levels(levels)
                except Exception as exc:  # noqa: BLE001 - no debe tumbar el hilo lector
                    print(f"[ytaudio.audiocap] error calculando bandas: {exc}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - herramienta personal
            print(f"[ytaudio.audiocap] hilo de captura terminó con error: {exc}", file=sys.stderr)

    def _compute_bands(self, samples: "np.ndarray") -> list[float]:
        windowed = samples * self._window
        spectrum = np.abs(np.fft.rfft(windowed))
        for i, (lo, hi) in enumerate(self._band_edges):
            mag = float(spectrum[lo:hi].mean()) if hi > lo else 0.0
            db = 20.0 * np.log10(mag + 1e-6)
            target = max(0.0, min(1.0, (db - _DB_FLOOR) / (_DB_CEIL - _DB_FLOOR)))
            current = self._smoothed[i]
            rate = _ATTACK if target > current else _RELEASE
            self._smoothed[i] = current + (target - current) * rate
        return list(self._smoothed)
