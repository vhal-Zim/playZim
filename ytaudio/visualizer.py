"""Ecualizador animado: reacciona a audio real cuando hay datos (ver docstring de clase)."""

from __future__ import annotations

import random

from rich.style import Style
from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

# 9 niveles: índice = octavos de bloque llenos (0 = vacío, 8 = bloque completo).
_BLOCK_LEVELS = " ▁▂▃▄▅▆▇█"

_IDLE_TARGET = 0.04
_MAX_TARGET_AMPLITUDE = 0.92
_STEP = 0.05
_PULL = 0.18

_PEAK_DECAY = 0.02  # fracción por tick de redibujo — decae más lento que el nivel


def _color_for_fraction(frac: float) -> str:
    """Gradiente verde→amarillo→rojo tipo VU-meter clásico, por altura de fila."""
    frac = max(0.0, min(1.0, frac))
    if frac < 0.5:
        t = frac / 0.5
        r, g, b = (int(40 + t * (210 - 40)), 200, 40)
    else:
        t = (frac - 0.5) / 0.5
        r, g, b = (int(210 + t * (225 - 210)), int(200 - t * (200 - 40)), 40)
    return f"#{r:02x}{g:02x}{b:02x}"


class EqualizerWidget(Widget):
    """Barras verticales que reaccionan al audio real cuando hay datos disponibles.

    Arranca en modo simulado (random walk con reversión a la media — evita
    saltos bruscos y sigue `playing`/`volume` de forma suave). En cuanto
    llega el primer llamado a `set_levels()` (niveles reales por banda,
    calculados por `ytaudio/audiocap.py` a partir de una captura de audio
    real vía PipeWire) pasa a modo real de forma permanente para esa
    instancia — si `audiocap`/`pw-record` no está disponible en el sistema,
    `set_levels()` simplemente nunca se llama y el widget sigue en modo
    simulado para siempre, sin necesitar ninguna bandera extra.

    Ambos modos comparten un peak-hold por barra (marca fina que sube
    rápido y decae lento, estilo VU-meter de hardware).
    """

    DEFAULT_CSS = """
    EqualizerWidget {
        height: 8;
        width: 1fr;
    }
    """

    playing: reactive[bool] = reactive(False)
    volume: reactive[int] = reactive(100)

    def __init__(self, bars: int = 12, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._bars = max(1, bars)
        # Fracción de altura (0.0-1.0) por barra; walk y render trabajan sobre esto,
        # independiente del alto real en filas (se re-escala en render()).
        self._levels: list[float] = [_IDLE_TARGET] * self._bars
        # Sesgo fijo por barra para que no todas persigan exactamente el mismo objetivo
        # (aspecto más parecido a bandas de frecuencia distintas).
        self._bias: list[float] = [random.uniform(0.65, 1.35) for _ in range(self._bars)]
        self._real_mode = False
        self._peaks: list[float] = [0.0] * self._bars

    def on_mount(self) -> None:
        self.set_interval(1 / 12, self._tick)

    def watch_playing(self) -> None:
        self.refresh()

    def watch_volume(self) -> None:
        self.refresh()

    def _update_peak(self, index: int, level: float) -> None:
        if level >= self._peaks[index]:
            self._peaks[index] = level
        else:
            self._peaks[index] = max(level, self._peaks[index] - _PEAK_DECAY)

    def set_levels(self, levels: list[float]) -> None:
        """Aplica niveles reales por banda (0.0-1.0) y fija el modo real.

        `levels` puede traer más o menos entradas que `self._bars` (la
        resolución interna de `audiocap.py` es fija e independiente de
        cuántas barras dibuje este widget) — se remapea por índice más
        cercano, suficiente para uso puramente visual.
        """
        self._real_mode = True
        n_src = len(levels)
        if n_src == self._bars:
            mapped = list(levels)
        else:
            mapped = [levels[min(n_src - 1, int(i * n_src / self._bars))] for i in range(self._bars)]
        for i, level in enumerate(mapped):
            self._levels[i] = level
            self._update_peak(i, level)
        self.refresh()

    def _target_for(self, bar_index: int) -> float:
        if not self.playing:
            return _IDLE_TARGET
        base = max(0.0, min(1.0, self.volume / 130)) * _MAX_TARGET_AMPLITUDE
        return max(0.0, min(1.0, base * self._bias[bar_index]))

    def _tick(self) -> None:
        if self._real_mode:
            return  # los niveles reales llegan vía set_levels(), no por random walk
        for i, level in enumerate(self._levels):
            target = self._target_for(i)
            # Paso proporcional al objetivo: casi sin jitter en reposo (se ve "congelado"),
            # movimiento más vivo cuanto más alto el volumen.
            step_amplitude = _STEP * (0.25 + 0.75 * target)
            level += random.uniform(-step_amplitude, step_amplitude)
            level += (target - level) * _PULL
            level = max(0.0, min(1.0, level))
            self._levels[i] = level
            self._update_peak(i, level)
        self.refresh()

    def _smoothed_levels(self) -> list[float]:
        """Promedio ponderado con las barras vecinas, solo para dibujar.

        `self._levels`/`self._peaks` guardan el valor real sin suavizar
        (lo que importa para el peak-hold); esto es puramente cosmético,
        para que el movimiento se vea como una "ola" continua en vez de
        columnas independientes titilando cada una por su cuenta.
        """
        n = len(self._levels)
        out = []
        for i in range(n):
            left = self._levels[i - 1] if i > 0 else self._levels[i]
            right = self._levels[i + 1] if i < n - 1 else self._levels[i]
            out.append(0.5 * self._levels[i] + 0.25 * left + 0.25 * right)
        return out

    def render(self) -> Text:
        rows = self.size.height or 8
        width = self.size.width or (self._bars * 2 - 1)
        max_level = rows * 8

        smoothed = self._smoothed_levels()
        columns = [smoothed[i] * max_level for i in range(self._bars)]
        peak_rows = [int(self._peaks[i] * max_level) // 8 for i in range(self._bars)]

        text = Text(no_wrap=True, overflow="crop")
        for row_from_top in range(rows):
            row_from_bottom = rows - 1 - row_from_top
            color = _color_for_fraction(row_from_bottom / max(1, rows - 1))
            style = Style(color=color)
            line_len = 0
            for i, level in enumerate(columns):
                remaining = level - row_from_bottom * 8
                block_index = max(0, min(8, round(remaining)))
                if block_index == 0 and peak_rows[i] == row_from_bottom:
                    # Marca de peak-hold: solo se dibuja donde la barra no llega ya.
                    text.append("▔", style=Style(color="#f0f0f0"))
                else:
                    char = _BLOCK_LEVELS[block_index]
                    text.append(char, style=style if block_index else None)
                line_len += 1
                if i != self._bars - 1:
                    text.append(" ")
                    line_len += 1
                if line_len >= width:
                    break
            if row_from_top != rows - 1:
                text.append("\n")
        return text
