# ytaudio

Reproductor de audio de YouTube para la terminal. Busca o pega una URL, y
reproduce solo el audio (sin video) directamente en tu terminal, con una
interfaz interactiva (TUI), biblioteca persistente, cola, caché offline y
soporte de teclas multimedia del sistema.

## Características

- **Interfaz interactiva (Textual)**: controles de reproducción, biblioteca,
  cola y ecualizador animado, todo desde el teclado.
- **Biblioteca automática**: cada pista reproducida se guarda con su título,
  autor y contador de reproducciones — se puede volver a elegir después.
- **Cola con shuffle y repeat** (apagado / una pista / toda la cola).
- **Historial** de reproducciones, separado de la biblioteca curada.
- **Caché offline**: la primera vez se transmite en streaming; en segundo
  plano se descarga el audio para que la próxima reproducción sea instantánea
  y sin red.
- **Teclas multimedia / MPRIS**: integración best-effort con el bus de
  sesión D-Bus del escritorio (play/pausa/siguiente desde el teclado del
  sistema). Si D-Bus no está disponible, se desactiva en silencio sin afectar
  el resto de la app.
- **Modo clásico (`--legacy`)**: el comportamiento original de una sola
  pieza (mpv controla la terminal directamente), por si preferís ese flujo o
  lo necesitás en un script.

## Requisitos

- Python 3.10+
- [`mpv`](https://mpv.io/) y `ffmpeg` instalados en el sistema
- Linux con D-Bus de sesión disponible (opcional, solo para MPRIS)

No hace falta `sudo` para nada de lo anterior más allá de `mpv`/`ffmpeg`, que
seguramente ya tenés instalados. Todas las dependencias de Python (Textual,
`dbus-next`, `yt-dlp`) se instalan en un entorno virtual propio del proyecto.

## Instalación

```bash
git clone <url-de-este-repo>
cd ytaudio  # o el nombre que le hayas dado al repo

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install textual dbus-next yt-dlp

./install.sh
```

`install.sh` crea el comando `ytaudio` en `~/.local/bin` (agregalo a tu
`PATH` si el script te lo indica). Internamente el comando usa siempre el
Python del `.venv` del proyecto, así que no hace falta activarlo a mano.

Para actualizar `yt-dlp` cuando YouTube cambie algo y deje de funcionar:

```bash
.venv/bin/pip install --upgrade yt-dlp
```

## Uso

```bash
ytaudio                      # abre la TUI vacía (biblioteca/cola/búsqueda)
ytaudio "lofi hip hop"       # abre la TUI y reproduce esa búsqueda al instante
ytaudio "https://youtube.com/watch?v=..."   # también acepta URLs directas
ytaudio --volume 80 "consulta"              # volumen inicial
ytaudio --legacy "consulta o URL"           # modo clásico, sin TUI
```

### Controles (TUI)

| Tecla       | Acción                                   |
|-------------|-------------------------------------------|
| `espacio`   | Pausar / reanudar                          |
| `]` / `[`   | Pista siguiente / anterior                 |
| `+` / `-`   | Subir / bajar volumen                      |
| `s`         | Shuffle on/off                             |
| `e`         | Ciclar repeat (apagado → una → toda)       |
| `f`         | Buscar en YouTube                          |
| `1` / `2` / `3` | Biblioteca / Cola / Historial           |
| `q`         | Salir                                      |

Dentro de cada pestaña:

| Pestaña     | Tecla | Acción                          |
|-------------|-------|----------------------------------|
| Biblioteca  | `a`   | Encolar pista seleccionada       |
| Biblioteca  | `d`   | Borrar de la biblioteca           |
| Biblioteca  | `r`   | Renombrar                        |
| Cola        | `d`   | Quitar de la cola                |
| Cola        | `c`   | Vaciar la cola                   |
| Búsqueda (`f`) | `a` | Encolar sin reproducir de inmediato |

`Enter` reproduce la pista seleccionada en cualquiera de las listas
(biblioteca, cola, historial, resultados de búsqueda).

## Dónde guarda sus datos

- Biblioteca, historial y configuración: `~/.config/ytaudio/`
- Audio descargado (caché offline): `~/.cache/ytaudio/`

Ambas carpetas son propias del usuario, no viven dentro del repo.

## Estructura del proyecto

```
ytaudio.py        # script original de una sola pieza (usado por --legacy)
install.sh        # instala el comando `ytaudio` en ~/.local/bin
ytaudio/
  models.py        # tipos compartidos (Track, PlayerStatus, etc.)
  config.py        # rutas XDG y configuración persistente
  resolver.py       # búsqueda/resolución/descarga vía yt-dlp
  library.py        # biblioteca curada (JSON)
  history.py        # historial de reproducción (JSON)
  cache.py          # caché offline de audio
  player.py         # motor de reproducción (mpv headless + socket JSON-IPC)
  mpris.py          # integración con teclas multimedia (D-Bus)
  visualizer.py      # ecualizador animado (widget Textual)
  tui.py            # interfaz interactiva (Textual App)
  cli.py            # punto de entrada / argparse
```
