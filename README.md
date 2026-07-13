# ytaudio

A terminal YouTube audio player. Search or paste a URL and play just the
audio (no video) right in your terminal, with an interactive TUI, a
persistent library, a queue, offline caching, and system media-key support.

## Features

- **Interactive TUI (Textual)**: playback controls, library, queue, and an
  animated equalizer, all from the keyboard.
- **Automatic library**: every track you play is saved with its title,
  uploader, and play count — pick it again later.
- **Queue with shuffle and repeat** (off / one track / whole queue).
- **History** of played tracks, kept separate from the curated library.
- **Offline cache**: the first playback streams live; a background download
  caches the audio so the next playback is instant and network-free.
- **Media keys / MPRIS**: best-effort integration with the desktop session
  D-Bus (play/pause/next from your system's media keys). If D-Bus isn't
  available, it silently disables itself without affecting anything else.
- **Classic mode (`--legacy`)**: the original single-file behavior (mpv
  takes over the terminal directly), in case you prefer that flow or need it
  in a script.

## Requirements

- Python 3.10+
- [`mpv`](https://mpv.io/) and `ffmpeg` installed on the system
- Linux with a session D-Bus available (optional, only needed for MPRIS)

No `sudo` is required beyond `mpv`/`ffmpeg`, which you likely already have.
All Python dependencies (Textual, `dbus-next`, `yt-dlp`) are installed into
the project's own virtual environment.

## Installation

```bash
git clone git@github.com:vhal-Zim/playZim.git
cd playZim

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install textual dbus-next yt-dlp

./install.sh
```

`install.sh` creates the `ytaudio` command in `~/.local/bin` (add it to your
`PATH` if the script tells you to). The command always runs through the
project's `.venv` Python internally, so there's no need to activate it
manually.

To update `yt-dlp` whenever YouTube changes something and playback breaks:

```bash
.venv/bin/pip install --upgrade yt-dlp
```

## Usage

```bash
ytaudio                      # opens the empty TUI (library/queue/search)
ytaudio "lofi hip hop"       # opens the TUI and plays that search right away
ytaudio "https://youtube.com/watch?v=..."   # direct URLs work too
ytaudio --volume 80 "query"                 # initial volume
ytaudio --legacy "query or URL"             # classic mode, no TUI
```

### Controls (TUI)

| Key         | Action                                   |
|-------------|-------------------------------------------|
| `space`     | Play / pause                               |
| `]` / `[`   | Next / previous track                      |
| `+` / `-`   | Volume up / down                           |
| `s`         | Toggle shuffle                             |
| `e`         | Cycle repeat (off → one → all)             |
| `f`         | Search YouTube                             |
| `1` / `2` / `3` | Library / Queue / History               |
| `q`         | Quit                                       |

Within each tab:

| Tab          | Key | Action                            |
|--------------|-----|-------------------------------------|
| Library      | `a` | Enqueue selected track              |
| Library      | `d` | Remove from library                 |
| Library      | `r` | Rename                              |
| Queue        | `d` | Remove from queue                   |
| Queue        | `c` | Clear the queue                     |
| Search (`f`) | `a` | Enqueue without playing immediately |

`Enter` plays the selected track in any of the lists (library, queue,
history, search results).

## Where it stores data

- Library, history, and settings: `~/.config/ytaudio/`
- Downloaded audio (offline cache): `~/.cache/ytaudio/`

Both folders are user-specific and don't live inside the repo.

## Project structure

```
ytaudio.py        # original single-file script (used by --legacy)
install.sh        # installs the `ytaudio` command into ~/.local/bin
ytaudio/
  models.py        # shared types (Track, PlayerStatus, etc.)
  config.py        # XDG paths and persistent settings
  resolver.py       # search/resolve/download via yt-dlp
  library.py        # curated library (JSON)
  history.py        # playback history (JSON)
  cache.py          # offline audio cache
  player.py         # playback engine (headless mpv + JSON-IPC socket)
  mpris.py          # media-key integration (D-Bus)
  visualizer.py      # animated equalizer (Textual widget)
  tui.py            # interactive interface (Textual App)
  cli.py            # entry point / argparse
```
