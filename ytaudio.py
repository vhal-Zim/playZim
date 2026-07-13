#!/usr/bin/env python3
"""Reproduce solo el audio de vídeos/playlists de YouTube en la terminal."""

import argparse
import shutil
import subprocess
import sys

REQUIRED_BINS = ("mpv",)


def check_deps():
    missing = [b for b in REQUIRED_BINS if shutil.which(b) is None]
    if missing:
        print(f"Falta instalar: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)


def is_url(s):
    return s.startswith("http://") or s.startswith("https://")


def search_results(query, count):
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{count}:{query}", download=False)
    return info.get("entries", []) or []


def format_duration(seconds):
    if not seconds:
        return "?"
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def entry_url(entry):
    return entry.get("url") or f"https://www.youtube.com/watch?v={entry['id']}"


def choose_from_results(query, count):
    entries = search_results(query, count)
    if not entries:
        print("Sin resultados.", file=sys.stderr)
        sys.exit(1)

    for i, e in enumerate(entries, 1):
        title = e.get("title", "?")
        dur = format_duration(e.get("duration"))
        author = e.get("channel") or e.get("uploader") or "?"
        print(f"[{i}] {title} ({dur}) - {author}")

    choice = input("Elige número (Enter=1, q=salir): ").strip().lower()
    if choice == "q":
        sys.exit(0)
    idx = int(choice) - 1 if choice else 0
    if idx < 0 or idx >= len(entries):
        print("Selección inválida.", file=sys.stderr)
        sys.exit(1)

    return entry_url(entries[idx])


def resolve_first_match(query):
    entries = search_results(query, 1)
    if not entries:
        print("Sin resultados.", file=sys.stderr)
        sys.exit(1)
    print(f"Reproduciendo: {entries[0].get('title', '?')}")
    return entry_url(entries[0])


def play(target, volume=None):
    cmd = ["mpv", "--no-video", "--ytdl-format=bestaudio/best"]
    if volume is not None:
        cmd.append(f"--volume={volume}")
    cmd.append(target)
    subprocess.run(cmd)


def main():
    parser = argparse.ArgumentParser(
        description="Reproduce solo el audio de YouTube en la terminal."
    )
    parser.add_argument("query", help="URL de YouTube (vídeo o playlist) o texto de búsqueda")
    parser.add_argument(
        "-p", "--pick", action="store_true", help="Mostrar varios resultados y elegir uno"
    )
    parser.add_argument(
        "-n", "--num", type=int, default=5, help="Resultados a mostrar con --pick (por defecto 5)"
    )
    parser.add_argument("--volume", type=int, help="Volumen inicial, 0-130")
    args = parser.parse_args()

    check_deps()

    target = args.query
    if not is_url(target):
        target = (
            choose_from_results(target, args.num)
            if args.pick
            else resolve_first_match(target)
        )

    print("Controles: espacio=pausa, 9/0=volumen, </> pista anterior/siguiente, q=salir")
    play(target, volume=args.volume)


if __name__ == "__main__":
    main()
