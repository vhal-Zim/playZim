"""Ecualizador animado a todo el ancho: reacciona a audio real cuando hay datos.

Rediseño hi-fi (ver `Rediseño barra de buffers CLI/design_handoff_playzim_barra/`):
espejo vertical con el eje de progreso en el medio, paleta mono ceniza→rojo por
amplitud, y una dinámica exagerada (ataque de un frame, caída larga, silencio
real).

Modo real vs. modo simulado usan formas distintas: `audiocap.py` ya entrega
niveles por banda suavizados y normalizados contra un rango de dB fijo
(calibrado ahí mismo para no saturar) — a esos se les aplica gate + una gamma
por banda (graves más agresiva) + ataque/caída. La señal sintética (sin
`pw-record`) es cruda, así que a esa sí se le aplica la normalización por
banda con memoria corta que le da vida sin depender de un pico global.
"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np
from rich.style import Style
from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

# 9 niveles: índice = octavos de bloque llenos (0 = vacío, 8 = bloque completo).
_BLOCK_LEVELS = " ▁▂▃▄▅▆▇█"

# -- dinámica (§3 del handoff) -----------------------------------------------
ATTACK = 0.62  # subida: golpe casi instantáneo
RELEASE = 0.10  # bajada: cola visible
GAMMA = 0.55  # <1 expande la zona baja — modo simulado (señal cruda)
NOISE_GATE = 0.07  # silencio = suelo real — modo simulado
PEAK_DECAY = 0.022  # gravedad de las crestas
BAND_MEMORY = 0.55  # olvido del máximo por banda (1/s) — solo modo simulado

# `audiocap.py` entrega bandas ya normalizadas contra un rango de dB fijo, así
# que en las partes fuertes de un tema casi todas terminan altas al mismo
# tiempo — sin nada más, eso se ve como una pared pareja en vez de barras que
# se mueven. Para el modo real usamos un piso más alto (silencio real más
# agresivo) y GAMMA > 1: en vez de expandir la zona baja (como en el modo
# simulado), esto comprime lo que está apenas encendido y expande el tramo
# alto — durante un pasaje fuerte, sigue habiendo diferencia visible entre
# "bastante fuerte" y "a tope" en vez de que todo se plante en el techo.
NOISE_GATE_REAL = 0.40
GAMMA_REAL_BASE = 1.6
# Los graves son los que más se ven "fijos" (el bombo/bajo suele tener
# energía sostenida) — se les aplica una gamma bastante más alta que decae
# hacia GAMMA_REAL_BASE a medida que sube la frecuencia, así solo un golpe de
# verdad los hace saltar en vez de vivir a media altura todo el tiempo.
GAMMA_REAL_BASS_BOOST = 1.8
GAMMA_REAL_BASS_TAPER = 0.22  # fracción del espectro (0=grave) donde el boost cae a ~1/e

_gamma_curve_cache: dict[int, np.ndarray] = {}


def _real_gamma_curve(n_bands: int) -> np.ndarray:
    """Gamma variable por banda: más alta en graves, baja hacia GAMMA_REAL_BASE."""
    cached = _gamma_curve_cache.get(n_bands)
    if cached is not None:
        return cached
    frac = np.arange(n_bands, dtype=np.float32) / max(1, n_bands - 1)  # 0 = grave, 1 = agudo
    curve = GAMMA_REAL_BASE + GAMMA_REAL_BASS_BOOST * np.exp(-frac / GAMMA_REAL_BASS_TAPER)
    _gamma_curve_cache[n_bands] = curve
    return curve

# -- geometría (§2.2) ---------------------------------------------------------
_ROWS_UP = 7
_ROWS_DOWN = 6
_ROWS_TOTAL = _ROWS_UP + 1 + _ROWS_DOWN  # 14: 7 arriba + eje + 6 abajo
_REFLECT_SCALE = 0.62  # altura del reflejo relativa a la barra real
_BAR_STRIDE = 2  # 1 columna de barra + 1 de separación, para que no se amalgamen

# -- paleta exacta (§2.1) ------------------------------------------------------
_BG_PANEL = "#0e0d0c"
_COLD = "#433d3b"
_HOT = "#ec3013"
_AXIS_RAIL = "#2a2523"
_PROGRESS_DONE = "#ec3013"
_PROGRESS_HEAD = "#ffd0c3"
_REFLECT_COLD = "#2f2b29"
_REFLECT_HOT = "#7d2a1c"


def _hex_to_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    r, g, b = (max(0, min(255, round(c))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def _lerp_rgb(c1: tuple[float, float, float], c2: tuple[float, float, float], t: float) -> tuple[float, float, float]:
    return (c1[0] + (c2[0] - c1[0]) * t, c1[1] + (c2[1] - c1[1]) * t, c1[2] + (c2[2] - c1[2]) * t)


def _blend_over(fg_hex: str, alpha: float, bg_hex: str) -> str:
    return _rgb_to_hex(_lerp_rgb(_hex_to_rgb(bg_hex), _hex_to_rgb(fg_hex), alpha))


_COLD_RGB = _hex_to_rgb(_COLD)
_HOT_RGB = _hex_to_rgb(_HOT)
_REFLECT_COLD_RGB = _hex_to_rgb(_REFLECT_COLD)
_REFLECT_HOT_RGB = _hex_to_rgb(_REFLECT_HOT)

# Punta de barra: mezcla #ec3013 → #ffe4dc al 55 %.
_TIP_COLOR = _rgb_to_hex(_lerp_rgb(_HOT_RGB, _hex_to_rgb("#ffe4dc"), 0.55))
# Cresta: #ffc4b2 al 70 % de alfa sobre el fondo del panel (sin alfa real en terminal).
_PEAK_COLOR = _blend_over("#ffc4b2", 0.70, _BG_PANEL)

_TIP_STYLE = Style(color=_TIP_COLOR)
_PEAK_STYLE = Style(color=_PEAK_COLOR)
_PROGRESS_DONE_STYLE = Style(color=_PROGRESS_DONE)
_PROGRESS_HEAD_STYLE = Style(color=_PROGRESS_HEAD)
_AXIS_RAIL_STYLE = Style(color=_AXIS_RAIL)

# Tabla de estilos precalculada por amplitud cuantizada: evita construir un
# Style() (y su string hex) por celda en cada frame, que es lo que hacía
# render() caro en terminales anchas.
_COLOR_LUT_STEPS = 40


def _amplitude_lut(cold_rgb: tuple[float, float, float], hot_rgb: tuple[float, float, float]) -> list[Style]:
    return [Style(color=_rgb_to_hex(_lerp_rgb(cold_rgb, hot_rgb, i / (_COLOR_LUT_STEPS - 1)))) for i in range(_COLOR_LUT_STEPS)]


_UP_STYLE_LUT = _amplitude_lut(_COLD_RGB, _HOT_RGB)
_DOWN_STYLE_LUT = _amplitude_lut(_REFLECT_COLD_RGB, _REFLECT_HOT_RGB)


def _lut_index(v: np.ndarray) -> np.ndarray:
    """§2.1: t = clamp(v * 1.25, 0, 1), cuantizado a la resolución de la LUT."""
    t = np.clip(v * 1.25, 0.0, 1.0)
    return np.minimum((t * (_COLOR_LUT_STEPS - 1) + 0.5).astype(np.int32), _COLOR_LUT_STEPS - 1)


class EqualizerWidget(Widget):
    """Espejo de barras a todo el ancho del frame, con el eje de progreso al medio.

    Arranca en modo simulado (señal sintética con sesgo por columna). En
    cuanto llega el primer `set_levels()` con niveles reales (calculados por
    `ytaudio/audiocap.py`) pasa a modo real de forma permanente para esa
    instancia. Al pausar, no se congela: se sigue animando la caída hasta
    cero en ambos modos.
    """

    DEFAULT_CSS = f"""
    EqualizerWidget {{
        height: {_ROWS_TOTAL};
        width: 1fr;
        background: {_BG_PANEL};
    }}
    """

    playing: reactive[bool] = reactive(False)
    volume: reactive[int] = reactive(100)
    position: reactive[float] = reactive(0.0)
    duration: reactive[Optional[float]] = reactive(None)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._n_cols = 0
        self._n_bars = 0
        self._bar_of_col = np.zeros(0, dtype=np.int32)
        self._is_bar_col = np.zeros(0, dtype=bool)
        self._cur = np.zeros(0, dtype=np.float32)
        self._peak = np.zeros(0, dtype=np.float32)
        self._roll = np.zeros(0, dtype=np.float32)
        self._bias = np.zeros(0, dtype=np.float32)
        self._real_mode = False
        self._last_tick: Optional[float] = None
        self._sim_t = 0.0
        self._rng = np.random.default_rng()
        self._levels_skip = 0

    def on_mount(self) -> None:
        self._alloc(max(1, self.size.width or 24))
        self.set_interval(1 / 20, self._tick)

    def on_resize(self, event) -> None:
        self._alloc(max(1, event.size.width))

    def _alloc(self, n: int) -> None:
        """Reservar los buffers de estado. Se llama al cambiar n_cols (§3.1).

        Una barra cada `_BAR_STRIDE` columnas (el resto son separación) —
        de lo contrario, con barras vecinas del mismo color/altura, el
        ecualizador se ve como una curva amalgamada en vez de barras.
        """
        if n == self._n_cols:
            return
        self._n_cols = n
        n_bars = max(1, (n + _BAR_STRIDE - 1) // _BAR_STRIDE)
        self._is_bar_col = np.zeros(n, dtype=bool)
        self._is_bar_col[0 : n_bars * _BAR_STRIDE : _BAR_STRIDE] = True
        self._bar_of_col = np.zeros(n, dtype=np.int32)
        self._bar_of_col[0 : n_bars * _BAR_STRIDE : _BAR_STRIDE] = np.arange(n_bars, dtype=np.int32)
        if n_bars == self._n_bars:
            return
        self._n_bars = n_bars
        self._cur = np.zeros(n_bars, dtype=np.float32)
        self._peak = np.zeros(n_bars, dtype=np.float32)
        self._roll = np.full(n_bars, 0.35, dtype=np.float32)
        self._bias = self._rng.uniform(0.65, 1.35, size=n_bars).astype(np.float32)

    def watch_playing(self) -> None:
        self.refresh()

    def watch_volume(self) -> None:
        self.refresh()

    def watch_position(self) -> None:
        self.refresh()

    def watch_duration(self) -> None:
        self.refresh()

    def _dt(self) -> float:
        now = time.monotonic()
        dt = 1 / 20 if self._last_tick is None else max(1e-3, now - self._last_tick)
        self._last_tick = now
        return dt

    def _attack_release(self, v: np.ndarray, dt: float) -> np.ndarray:
        a = 1.0 - (1.0 - ATTACK) ** (dt * 60.0)
        r = 1.0 - (1.0 - RELEASE) ** (dt * 60.0)
        rising = v > self._cur
        self._cur = self._cur + (v - self._cur) * np.where(rising, a, r)

        hit = self._cur >= self._peak
        self._peak = np.where(hit, self._cur, np.maximum(self._cur, self._peak - PEAK_DECAY * dt * 60.0))
        return self._cur

    def _shape_synthetic(self, mags: np.ndarray, dt: float) -> np.ndarray:
        """Señal cruda (sin normalizar): necesita la normalización por banda
        con memoria corta para que no todo viva a la sombra del pico global."""
        v = np.where(mags > NOISE_GATE, (mags - NOISE_GATE) / (1.0 - NOISE_GATE), 0.0)
        self._roll = np.maximum(v, self._roll * (1.0 - BAND_MEMORY * dt))
        v = np.clip(v / np.maximum(self._roll, 0.22), 0.0, 1.0) ** GAMMA
        return self._attack_release(v, dt)

    def set_levels(self, levels: list[float]) -> None:
        """Aplica niveles reales por banda (0.0-1.0) y fija el modo real.

        El gate y la gamma (más agresiva en graves, ver `_real_gamma_curve`)
        se aplican en la resolución nativa de `audiocap.py` — ahí es donde
        "banda 0" significa realmente grave — y recién después se interpola
        al número real de barras en vez de repetir cada banda en varias
        columnas (eso se veía como bloques planos gigantes en vez de textura
        por barra).

        Se procesa 1 de cada 2 llamados: `audiocap.py` entrega ~21
        frames/seg, más de lo que hace falta ver y a un costo de CPU
        innecesario; el resto de la dinámica sigue escalando por `dt` real,
        así que saltear frames no cambia el look, solo el gasto.
        """
        if self._n_bars == 0 or not levels:
            return
        self._real_mode = True
        self._levels_skip = (self._levels_skip + 1) % 2
        if self._levels_skip != 0:
            return

        raw = np.asarray(levels, dtype=np.float32)
        gated = np.where(raw > NOISE_GATE_REAL, (raw - NOISE_GATE_REAL) / (1.0 - NOISE_GATE_REAL), 0.0)
        shaped = gated ** _real_gamma_curve(len(raw))

        n_src = len(shaped)
        if n_src == self._n_bars:
            v = shaped
        else:
            src_x = np.linspace(0.0, 1.0, n_src, dtype=np.float32)
            dst_x = np.linspace(0.0, 1.0, self._n_bars, dtype=np.float32)
            v = np.interp(dst_x, src_x, shaped).astype(np.float32)
        self._attack_release(v, self._dt())
        self.refresh()

    def _synthetic_mags(self, dt: float) -> np.ndarray:
        self._sim_t += dt
        n = self._n_bars
        vol = max(0.0, min(1.0, self.volume / 130.0))
        phase = self._sim_t * 0.9 + np.arange(n, dtype=np.float32) * 0.17
        base = 0.18 + 0.16 * np.sin(phase)
        spikes = (self._rng.uniform(0.0, 1.0, size=n) < 0.16).astype(np.float32)
        spikes *= self._rng.uniform(0.35, 1.0, size=n).astype(np.float32)
        mags = (base + spikes) * vol * self._bias
        return np.clip(mags, 0.0, 1.0).astype(np.float32)

    def _tick(self) -> None:
        if self._n_bars == 0:
            return
        dt = self._dt()
        if not self.playing:
            # No se congela al pausar: la caída hasta cero sigue animándose.
            # Con nivel 0 el gate/gamma no cambian nada, así que en ambos
            # modos alcanza con pasar directo por ataque/caída hacia cero.
            self._attack_release(np.zeros(self._n_bars, dtype=np.float32), dt)
            self.refresh()
            return
        if self._real_mode:
            return  # los niveles reales llegan vía set_levels(), no por acá
        self._shape_synthetic(self._synthetic_mags(dt), dt)
        self.refresh()

    def render(self) -> Text:
        width = self.size.width or self._n_cols
        if width and width != self._n_cols:
            self._alloc(width)
        n = self._n_cols
        if n == 0 or self._n_bars == 0:
            return Text("")

        cur = self._cur
        peak = self._peak
        is_bar_col = self._is_bar_col
        bar_of_col = self._bar_of_col

        silent = cur < 0.02
        up_eighths = np.minimum(np.round(cur * (_ROWS_UP * 8)).astype(np.int32), _ROWS_UP * 8)
        up_full, up_rest = np.divmod(up_eighths, 8)
        up_full = np.where(silent, 0, up_full)
        up_rest = np.where(silent, 0, up_rest)
        top_row_up = np.where(up_rest > 0, up_full, up_full - 1)

        down_v = cur * _REFLECT_SCALE
        down_eighths = np.minimum(np.round(down_v * (_ROWS_DOWN * 8)).astype(np.int32), _ROWS_DOWN * 8)
        down_full, down_rest = np.divmod(down_eighths, 8)
        down_silent = down_v < 0.02
        down_full = np.where(down_silent, 0, down_full)
        down_rest = np.where(down_silent, 0, down_rest)

        peak_eighths = np.minimum(np.round(peak * (_ROWS_UP * 8)).astype(np.int32), _ROWS_UP * 8)
        peak_rows = peak_eighths // 8

        style_idx = _lut_index(cur)

        # eje de progreso: la propia fila del medio (§2.4)
        frac = 0.0
        if self.duration and self.duration > 0:
            frac = max(0.0, min(1.0, self.position / self.duration))
        has_progress = bool(self.duration and self.duration > 0)
        filled = int(round(frac * n))
        head_col = min(filled, n - 1) if has_progress else None

        text = Text(no_wrap=True, overflow="crop")
        for r in range(_ROWS_UP):
            row_from_axis = _ROWS_UP - 1 - r
            full_here = up_full > row_from_axis
            partial_here = (up_full == row_from_axis) & (up_rest > 0)
            top_here = top_row_up == row_from_axis
            for c in range(n):
                if not is_bar_col[c]:
                    text.append(" ")
                    continue
                b = bar_of_col[c]
                if full_here[b]:
                    ch = "█"
                elif partial_here[b]:
                    ch = _BLOCK_LEVELS[up_rest[b]]
                else:
                    ch = None
                if ch is None:
                    if peak_rows[b] == row_from_axis:
                        text.append("▔", style=_PEAK_STYLE)
                    else:
                        text.append(" ")
                elif top_here[b]:
                    text.append(ch, style=_TIP_STYLE)
                else:
                    text.append(ch, style=_UP_STYLE_LUT[style_idx[b]])
            text.append("\n")

        for c in range(n):
            if c == head_col:
                text.append("▔", style=_PROGRESS_HEAD_STYLE)
            elif c < filled:
                text.append("▔", style=_PROGRESS_DONE_STYLE)
            else:
                text.append("▔", style=_AXIS_RAIL_STYLE)
        text.append("\n")

        for row_from_axis in range(_ROWS_DOWN):
            full_here = down_full > row_from_axis
            partial_here = (down_full == row_from_axis) & (down_rest > 0)
            for c in range(n):
                if not is_bar_col[c]:
                    text.append(" ")
                    continue
                b = bar_of_col[c]
                if full_here[b]:
                    text.append("█", style=_DOWN_STYLE_LUT[style_idx[b]])
                elif partial_here[b]:
                    text.append(_BLOCK_LEVELS[down_rest[b]], style=_DOWN_STYLE_LUT[style_idx[b]])
                else:
                    text.append(" ")
            if row_from_axis != _ROWS_DOWN - 1:
                text.append("\n")
        return text
