# Handoff: barra de reproducción de playZim (ecualizador + progreso)

Repo destino: `vhal-Zim/playZim` · Python 3.10+ · TUI con **Textual**
Archivos a tocar: `ytaudio/visualizer.py` (principal) y `ytaudio/tui.py` (layout/CSS)

---

## 0. Cómo usar este paquete

`playZim Barra.dc.html` es una **referencia de diseño hecha en HTML/Canvas**: un
prototipo del aspecto y de la *dinámica* deseada, no código para copiar. Lo que
hay que implementar es el equivalente en el widget Textual existente.

Es un rediseño **hi-fi**: los colores, la geometría y las constantes de dinámica
de este documento son los valores finales. Respetarlos al pie de la letra.

Para abrir el prototipo hace falta también la carpeta `_ds/` del proyecto de
diseño (hoja de estilos del sistema Modernist). Si no está, el prototipo se ve
sin estilos de página, pero el canvas del ecualizador — que es lo que importa —
funciona igual.

---

## 1. Qué está mal hoy y qué se busca

Problemas del estado actual:

1. El ecualizador es un bloque de ~20 barras **centrado** en la pantalla; deja
   dos tercios del frame vacíos.
2. La animación es **sosa**: las barras viven casi siempre en el tercio superior
   de la escala ("arriba de los amarillos") y apenas se mueven. Causa: una
   escala de magnitud **común a todas las bandas** — el bombo satura el máximo y
   aplana todo lo demás — más un suavizado simétrico y lento.
3. La rampa de color verde→amarillo→rojo por altura es ruidosa y genérica.
4. La línea de progreso es un segmento corto bajo el bloque, desconectada.

Objetivo:

- Ecualizador **a todo el ancho del frame**, una banda por columna de terminal.
- Dinámica **exagerada**: ataque de un frame, caída larga, silencio real.
- Espejo: barras hacia arriba + reflejo tenue hacia abajo, y **el eje del espejo
  es la propia barra de progreso**.
- Paleta mono: ceniza → rojo por intensidad. Nada de arcoíris por altura.

---

## 2. Especificación visual

### 2.1 Paleta (exacta)

| Rol | Hex | Uso |
| --- | --- | --- |
| Fondo del panel | `#0e0d0c` | fondo del bloque de reproducción |
| Barra fría (v→0) | `#433d3b` | color de una banda en reposo |
| Barra caliente (v→1) | `#ec3013` | color de una banda a tope |
| Punta de barra | mezcla `#ec3013`→`#ffe4dc` al 55 % | celda superior de cada barra |
| Cresta (peak hold) | `#ffc4b2` al 70 % de alfa | marca que cae con gravedad |
| Eje / riel | `#2a2523` | línea del espejo sin progreso |
| Progreso recorrido | `#ec3013` | parte ya reproducida del eje |
| Cabezal de progreso | `#ffd0c3` | bloque de 1 celda en la posición actual |
| Texto primario | `#fbf9f8` | título, tiempo transcurrido, volumen |
| Texto secundario | `#a19a97` | artista, etiquetas |
| Texto terciario | `#6d6663` | atajos, separadores, duración total |
| Regla interna | `#262220` | separador sobre la fila de atajos |

**El color de cada barra depende SOLO de su amplitud**, interpolando lineal en
RGB de `#433d3b` a `#ec3013` con `t = clamp(v * 1.25, 0, 1)`. No hay gradiente
por frecuencia ni por altura dentro de la barra.

### 2.2 Geometría del ecualizador

- Alto total: **14 filas** de terminal. El eje del espejo va en la fila 8
  (56 % del alto): 7 filas arriba, 6 abajo.
- Una banda por **columna**, con 1 celda de separación efectiva: en el
  prototipo, ancho de celda 8 px con 2 px de gap. En terminal esto es
  simplemente `n_cols = size.width` y una barra por columna, sin separador
  (la propia forma de bloque ya lo da).
- Reflejo inferior: misma barra, **62 % de la altura** y **38 % de opacidad**.
  En terminal sin alfa, el equivalente es usar los caracteres de sombreado
  `░ ▒` o un color intermedio fijo (`#2f2b29` → `#7d2a1c`).
- **Resolución de 1/8 de celda**: usar bloques parciales
  `▁ ▂ ▃ ▄ ▅ ▆ ▇ █` para la celda superior de cada barra (y `▔` para la cresta).
  Sin esto la animación se ve escalonada aunque la dinámica esté bien.
- Umbral: si `v < 0.02` la barra no dibuja nada (silencio = negro).

### 2.3 Fila de now-playing (una línea)

```
▶  Pa' Que Retozen — Wisin, Yandel, Farruko        SHUFFLE · REPEAT ALL · VOL 100
```

- Glifo de estado: `▶` reproduciendo / `❚❚` en pausa, en `#ec3013`, negrita.
- Título en `#fbf9f8` negrita; guión largo en `#6d6663`; artista en `#a19a97`.
- Bloque derecho alineado a la derecha: flags en `#6d6663` mayúsculas con
  espaciado, `VOL` en `#a19a97` y el número en `#fbf9f8` negrita.
- Padding: 1 fila en blanco entre el ecualizador y esta línea.

### 2.4 Barra de progreso

Es el **eje del espejo**, no un elemento aparte. En la vista de terminal se
dibuja como una fila completa:

```
2:01 ████████████████████████▌······································· 2:31
```

- `2:01` a la izquierda en `#fbf9f8`, `2:31` a la derecha en `#6d6663`, ambos con
  cifras tabulares.
- El riel ocupa **todo el ancho restante** (nunca un número fijo de columnas).
- Recorrido en `#ec3013`, resto en `#26221f`, cabezal de 1 celda en `#ffd0c3`.

### 2.5 Fila de pestañas y atajos

Debajo, separada por una regla de 1 px `#262220`:

- `1 Biblioteca` activa: `#fbf9f8` negrita con subrayado de 2 px `#ec3013`.
- `2 Cola`, `3 Historial` inactivas en `#6d6663`.
- Atajos alineados a la derecha en `#6d6663`, minúsculas: `space pausa`,
  `] siguiente`, `f buscar`, `q salir`.

---

## 3. Dinámica — el corazón del cambio

Cinco constantes. Los valores por defecto son los del prototipo y son los
recomendados.

| Constante | Valor | Qué hace |
| --- | --- | --- |
| `ATTACK` | `0.62` | fracción del salto hacia arriba por frame a 60 fps |
| `RELEASE` | `0.10` | fracción del descenso por frame — cola visible |
| `GAMMA` | `0.55` | < 1 expande la zona baja; el frame deja de estar lleno |
| `NOISE_GATE` | `0.07` | recorta el piso de ruido; el silencio se ve vacío |
| `PEAK_DECAY` | `0.022` | gravedad de las marcas de cresta |
| `BAND_MEMORY` | `0.55` | olvido del máximo por banda, en 1/s |

Las tres ideas, en orden de impacto:

1. **Normalización por banda con memoria corta.** Cada banda se divide por su
   propio máximo reciente en vez de por un máximo global. Es lo que rompe la
   dictadura del bombo y hace que medios y agudos por fin se muevan.
2. **Suavizado asimétrico.** Sube con `ATTACK`, baja con `RELEASE`. El
   transitorio entra en un frame y tarda diez en caer.
3. **Gate + gamma.** El gate baja el suelo a cero de verdad; la gamma < 1
   expande el rango útil hacia arriba, así el movimiento ocupa toda la altura
   en vez de vibrar en el tercio superior.

### 3.1 Código drop-in (`ytaudio/visualizer.py`)

```python
import numpy as np

ATTACK      = 0.62    # subida: golpe casi instantáneo
RELEASE     = 0.10    # bajada: cola visible
GAMMA       = 0.55    # <1 expande la zona baja
NOISE_GATE  = 0.07    # silencio = suelo real
PEAK_DECAY  = 0.022   # gravedad de las crestas
BAND_MEMORY = 0.55    # olvido del máximo por banda (1/s)


def _alloc(self, n: int) -> None:
    """Reservar los buffers de estado. Llamar al cambiar n_cols."""
    self.n_cols = n
    self._cur  = np.zeros(n, dtype=np.float32)
    self._peak = np.zeros(n, dtype=np.float32)
    self._vel  = np.zeros(n, dtype=np.float32)
    self._roll = np.full(n, 0.35, dtype=np.float32)


def _shape(self, mags: np.ndarray, dt: float) -> np.ndarray:
    """mags: magnitudes FFT ya agrupadas en n_cols bandas logarítmicas,
    normalizadas aprox. a [0, 1]. dt: segundos desde el frame anterior."""
    a = 1.0 - (1.0 - ATTACK)  ** (dt * 60.0)
    r = 1.0 - (1.0 - RELEASE) ** (dt * 60.0)

    v = np.where(mags > NOISE_GATE,
                 (mags - NOISE_GATE) / (1.0 - NOISE_GATE), 0.0)

    # máximo con memoria, por banda: nadie vive a la sombra del bombo
    self._roll = np.maximum(v, self._roll * (1.0 - BAND_MEMORY * dt))
    v = np.clip(v / np.maximum(self._roll, 0.22), 0.0, 1.0) ** GAMMA

    rising = v > self._cur
    self._cur += (v - self._cur) * np.where(rising, a, r)

    hit = self._cur >= self._peak
    self._peak = np.where(hit, self._cur,
                          np.maximum(self._cur,
                                     self._peak - PEAK_DECAY * dt * 60.0))
    self._vel = np.where(hit, 0.0, self._vel + PEAK_DECAY)
    return self._cur


def on_resize(self, event) -> None:
    self._alloc(max(24, event.size.width))   # ← todo el frame
```

Notas de integración:

- `n_cols` **se toma de `self.size.width`**, nunca de una constante. Eso es lo
  que lleva el ecualizador a todo el frame y lo que hay que reflejar también en
  el agrupado log de bins del FFT (`np.logspace` de ~40 Hz a ~14 kHz en
  `n_cols + 1` bordes, y `np.add.reduceat` sobre las magnitudes).
- `dt` real entre frames, no un valor asumido: las constantes están expresadas
  a 60 fps y se reescalan solas con `** (dt * 60)`, así que el look no cambia si
  el refresco del widget baja a 30 fps.
- Cuando no hay `pw-record` y el equalizador corre en modo simulado, el mismo
  `_shape()` debe aplicarse a la señal sintética — la dinámica es del widget, no
  de la fuente.

### 3.2 Render de una banda a 1/8 de celda

```python
BLOCKS = " ▁▂▃▄▅▆▇█"

def _column(v: float, rows: int) -> list[str]:
    """De abajo hacia arriba: celdas llenas + una parcial."""
    if v < 0.02:
        return [" "] * rows
    eighths = int(round(v * rows * 8))
    full, rest = divmod(eighths, 8)
    col = ["█"] * min(full, rows)
    if full < rows and rest:
        col.append(BLOCKS[rest])
    return col + [" "] * (rows - len(col))
```

El color se aplica por celda con markup de Rich/Textual:
`f"[{hex}]{ch}[/]"`, donde `hex` sale de la interpolación de §2.1. La celda
superior de cada barra usa la mezcla de punta; la cresta se dibuja con `▔` en
`#ffc4b2`.

---

## 4. Estado necesario

| Variable | Tipo | Nota |
| --- | --- | --- |
| `n_cols` | int | = ancho del widget; se recalcula en `on_resize` |
| `_cur` | `float32[n]` | altura suavizada actual por banda |
| `_peak` | `float32[n]` | posición de la cresta |
| `_vel` | `float32[n]` | velocidad de caída de la cresta |
| `_roll` | `float32[n]` | máximo con memoria por banda |
| `position`, `duration` | float | segundos; alimentan el eje de progreso |
| `playing` | bool | `▶` / `❚❚`; con `False` las barras caen a cero por `RELEASE` |

---

## 5. Layout en Textual (`tui.py`)

El bloque superior deja de ser un contenedor centrado:

- El widget del ecualizador: `width: 100%`, `height: 14`, sin `align: center`,
  sin `max-width`, sin padding lateral.
- Debajo, en el mismo contenedor: la fila de now-playing (`height: 1`, con una
  fila de aire encima), la fila de progreso (`height: 1`) y la fila de
  pestañas/atajos (`height: 1`) separada por un `border-top` de `#262220`.
- Fondo del contenedor `#0e0d0c` a todo el ancho.
- Al pausar no se congela el widget: se sigue animando la caída hasta cero.

---

## 6. Criterios de aceptación

1. Redimensionar la terminal cambia el número de barras al instante, sin huecos
   a izquierda ni derecha.
2. Con música con bombo marcado, medios y agudos se mueven de forma claramente
   independiente del bombo.
3. En silencio el ecualizador queda **vacío**, no en una meseta.
4. Un golpe sube en ≤ 1 frame y tarda ~10 frames en caer.
5. Ninguna barra usa verde ni amarillo.
6. La barra de progreso llega de un extremo al otro del panel.

---

## 7. Capturas de referencia

En `screenshots/`:

- `01-barra-completa.png` — el bloque de reproducción entero: ecualizador en
  espejo a todo el ancho, crestas colgadas, eje de progreso, fila de now-playing
  y fila de pestañas/atajos. **Es la referencia principal de color y geometría.**
- `02-barra-frame-b.png` — otro frame de la misma animación, para leer cuánta
  diferencia hay entre bandas vecinas y cómo quedan las crestas rezagadas.
- `03-documento-completo.png` — el documento de diseño completo, con los
  deslizadores de dinámica y el código drop-in en contexto.

Notas al leer las capturas: el prototipo dibuja con rectángulos sobre canvas,
así que las barras se ven con bordes perfectos; en terminal el equivalente son
bloques `█` con la celda superior parcial (§2.2). El reflejo inferior es el
mismo espectro al 62 % de altura y 38 % de opacidad.

---

## 8. Archivos de este paquete

- `playZim Barra.dc.html` — prototipo interactivo. Los deslizadores mueven las
  cinco constantes en vivo y el bloque de código de la derecha refleja los
  valores elegidos; si se quiere afinar el look antes de implementar, hacerlo
  ahí y copiar los números.
- `screenshots/` — capturas de referencia (§7).
- `README.md` — este documento.
