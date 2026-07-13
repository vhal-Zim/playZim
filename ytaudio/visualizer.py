"""Ecualizador animado decorativo (no analiza audio real, ver docstring de clase)."""

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
    """Barras verticales simuladas (random walk), sin relación con el audio real.

    El "random walk con reversión a la media" (en vez de ruido puro) evita
    saltos bruscos entre frames consecutivos y a la vez sigue de forma suave
    los cambios de `playing`/`volume` en pocos ticks, en lugar de saltar
    instantáneamente al nuevo valor objetivo.
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

    def on_mount(self) -> None:
        self.set_interval(1 / 12, self._tick)

    def watch_playing(self) -> None:
        self.refresh()

    def watch_volume(self) -> None:
        self.refresh()

    def _target_for(self, bar_index: int) -> float:
        if not self.playing:
            return _IDLE_TARGET
        base = max(0.0, min(1.0, self.volume / 130)) * _MAX_TARGET_AMPLITUDE
        return max(0.0, min(1.0, base * self._bias[bar_index]))

    def _tick(self) -> None:
        for i, level in enumerate(self._levels):
            target = self._target_for(i)
            # Paso proporcional al objetivo: casi sin jitter en reposo (se ve "congelado"),
            # movimiento más vivo cuanto más alto el volumen.
            step_amplitude = _STEP * (0.25 + 0.75 * target)
            level += random.uniform(-step_amplitude, step_amplitude)
            level += (target - level) * _PULL
            self._levels[i] = max(0.0, min(1.0, level))
        self.refresh()

    def render(self) -> Text:
        rows = self.size.height or 8
        width = self.size.width or (self._bars * 2 - 1)
        max_level = rows * 8

        columns = []
        for i in range(self._bars):
            level = self._levels[i] * max_level
            columns.append(level)

        text = Text(no_wrap=True, overflow="crop")
        for row_from_top in range(rows):
            row_from_bottom = rows - 1 - row_from_top
            color = _color_for_fraction(row_from_bottom / max(1, rows - 1))
            style = Style(color=color)
            line_len = 0
            for i, level in enumerate(columns):
                remaining = level - row_from_bottom * 8
                block_index = max(0, min(8, round(remaining)))
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
