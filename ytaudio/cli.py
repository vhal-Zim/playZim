"""Punto de entrada: TUI por defecto, --legacy preserva el modo clásico de mpv bloqueante."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys

from ytaudio import resolver


def _check_mpv() -> None:
    if shutil.which("mpv") is None:
        print("Falta instalar: mpv", file=sys.stderr)
        sys.exit(1)


def run_legacy(query_or_url: str, volume: int | None = None) -> None:
    _check_mpv()
    target = query_or_url
    if not resolver.is_url(target):
        track = resolver.resolve_single(target)
        print(f"Reproduciendo: {track.title}")
        target = track.url

    print("Controles mpv: espacio=pausa, 9/0=volumen, </> pista anterior/siguiente, q=salir")
    cmd = ["mpv", "--no-video", "--ytdl-format=bestaudio/best"]
    if volume is not None:
        cmd.append(f"--volume={volume}")
    cmd.append(target)
    subprocess.run(cmd)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="ytaudio", description="Reproductor de audio de YouTube para terminal."
    )
    parser.add_argument("query", nargs="?", help="URL de YouTube o texto de búsqueda a reproducir al iniciar")
    parser.add_argument(
        "--legacy", action="store_true", help="Modo clásico: mpv bloqueante sin TUI (comportamiento original)"
    )
    parser.add_argument("--volume", type=int, help="Volumen inicial, 0-130")
    args = parser.parse_args(argv)

    if args.legacy:
        if not args.query:
            parser.error("--legacy requiere una URL o texto de búsqueda")
        run_legacy(args.query, args.volume)
        return

    _check_mpv()
    from ytaudio.tui import YtAudioApp

    app = YtAudioApp(initial_query=args.query, initial_volume=args.volume)
    app.run()


if __name__ == "__main__":
    main()
