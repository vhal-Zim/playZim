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
- PipeWire with `pw-record` on `PATH` (optional, only needed for the real
  audio-reactive equalizer — without it, the equalizer just runs a
  simulated animation instead)
- [`deno`](https://deno.com/) on `PATH` (optional, recommended): yt-dlp uses
  it to solve YouTube's JavaScript signature challenges. Without it, some
  videos may fail to resolve at all.

No `sudo` is required beyond the above, which you likely already have.
All Python dependencies (Textual, `dbus-next`, `yt-dlp`, `numpy`) are
installed into the project's own virtual environment.

### Playback reliability fallbacks

YouTube increasingly blocks automated audio/video extraction for certain
content (mainly official/licensed music) unless the request looks like it
comes from a real browser. To keep the tool working out-of-the-box for
anyone who clones this repo — desktop machine or bare headless server alike
— the offline-cache download path (`ytaudio/resolver.py`) tries
progressively harder on failure, entirely automatically:

1. Plain download, no cookies, default client (fast, works for the large
   majority of videos).
2. If that's rejected, it retries with a couple of alternative YouTube
   "player clients" that yt-dlp supports (`tv`, `android`, `ios`, `web`).
   These need no browser and no cookies at all, so they're what kicks in on
   a machine with no browser installed (a server, a container, etc.).
3. If that's still not enough, it retries using the session cookies of any
   browser it finds actually installed on your machine (Chrome, Firefox,
   Edge, Chromium, Brave, Opera, Vivaldi, Safari, Whale — tried in that
   order). Browsers that aren't installed, or aren't logged into YouTube,
   fail that check almost instantly and it just moves on to the next one.
4. Whichever strategy works gets remembered for the rest of the session, so
   later downloads don't re-pay the cost of retrying the ones that failed.

This never sends your data anywhere beyond the normal yt-dlp/YouTube
request, and step 3 only ever reads your own local browser's existing
session. It's all best-effort — some official/licensed content is currently
blocked by YouTube's anti-bot measures hard enough that none of this gets
through. If you hit that often and don't mind running one extra local
service, the most reliable fix right now is a PO-token provider such as
[bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)
(run via Docker) — install its yt-dlp plugin
(`pip install bgutil-ytdlp-pot-provider`) and start its server; yt-dlp will
pick it up automatically with no other configuration.

## Installation

```bash
git clone git@github.com:vhal-Zim/playZim.git
cd playZim

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install textual dbus-next yt-dlp numpy

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
