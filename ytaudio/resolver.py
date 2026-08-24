"""Resolución de búsquedas/URLs de YouTube y descarga de audio via yt_dlp.

Usa siempre la API de Python de yt_dlp (nunca el binario por subprocess):
la versión instalada en el venv del proyecto es reciente y resuelve streams
sin el bug de anti-bot que afecta a versiones viejas empaquetadas por el
sistema.
"""

from __future__ import annotations

import re

import yt_dlp

from ytaudio.models import Track

_VIDEO_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)


def is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def video_id_from_url(url: str) -> str | None:
    match = _VIDEO_ID_RE.search(url)
    return match.group(1) if match else None


def _entry_to_track(entry: dict) -> Track:
    video_id = entry.get("id")
    url = entry.get("url") or entry.get("webpage_url")
    if url and not is_url(url) and video_id:
        url = f"https://www.youtube.com/watch?v={video_id}"
    elif not url and video_id:
        url = f"https://www.youtube.com/watch?v={video_id}"
    return Track(
        id=video_id,
        title=entry.get("title") or video_id,
        url=url,
        uploader=entry.get("uploader") or entry.get("channel"),
        duration=entry.get("duration"),
    )


def search(query: str, count: int = 5) -> list[Track]:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "default_search": "ytsearch",
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{count}:{query}", download=False)
    entries = (info or {}).get("entries") or []
    return [_entry_to_track(e) for e in entries if e]


def resolve_single(query_or_url: str) -> Track:
    if is_url(query_or_url):
        opts = {"quiet": True, "no_warnings": True, "skip_download": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query_or_url, download=False)
        video_id = video_id_from_url(query_or_url) or info.get("id")
        return Track(
            id=video_id,
            title=info.get("title") or video_id,
            url=query_or_url,
            uploader=info.get("uploader") or info.get("channel"),
            duration=info.get("duration"),
        )

    results = search(query_or_url, count=1)
    if not results:
        raise ValueError(f"Sin resultados de búsqueda para: {query_or_url!r}")
    return results[0]


_COOKIE_BROWSERS = ["chrome", "firefox", "edge", "chromium", "brave", "opera", "vivaldi", "safari", "whale"]

# Cada estrategia es (nombre, overrides de opciones de yt_dlp). Se prueban en
# este orden — de la más liviana/menos invasiva a la más costosa — hasta que
# alguna funcione:
#   1. "default": tal cual, sin nada especial (sirve para la gran mayoría).
#   2. clientes alternativos de YouTube: no necesitan navegador ni cookies,
#      solo cambian cómo yt_dlp le pide la info a YouTube — cubre la máquina
#      que no tiene NINGÚN navegador instalado (servidor, contenedor, etc.).
#   3. cookies de cualquier navegador instalado — el intento más "fuerte",
#      pero también el único que depende de que haya un navegador con
#      sesión iniciada en YouTube.
_FALLBACK_STRATEGIES: list[tuple[str, dict]] = [
    ("default", {}),
    *[(f"client:{c}", {"extractor_args": {"youtube": {"player_client": [c]}}}) for c in ("tv", "android", "ios", "web")],
    *[(f"cookies:{b}", {"cookiesfrombrowser": (b,)}) for b in _COOKIE_BROWSERS],
]
_STRATEGY_BY_NAME = dict(_FALLBACK_STRATEGIES)

# Recuerda qué estrategia funcionó la última vez en este proceso ("default"
# al empezar) para probarla primero en la próxima descarga y no repetir todo
# el barrido en cada pista de una sesión donde YouTube ya nos está pidiendo
# algo especial.
_last_working_strategy: str = "default"


def _yt_dlp_download(track: Track, dest_dir: str, overrides: dict) -> str:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "format": "bestaudio/best",
        "outtmpl": f"{dest_dir}/%(id)s.%(ext)s",
        "noplaylist": True,
        **overrides,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(track.url, download=True)
        if "requested_downloads" in info and info["requested_downloads"]:
            return info["requested_downloads"][0]["filepath"]
        return ydl.prepare_filename(info)


def download_audio(track: Track, dest_dir: str) -> str:
    """Descarga el mejor audio disponible para `track`.

    Estrategia en capas para que funcione "de fábrica" en cualquier máquina,
    sin pedirle configuración a nadie — desde un servidor headless sin
    ningún navegador hasta una máquina de escritorio normal:

    1. Descarga simple, sin nada especial (rápido, sirve para la mayoría).
    2. Si YouTube la rechaza (protección anti-bot / PO-token, común en
       contenido con licencia), reintenta con un par de "clientes"
       alternativos de YouTube que yt_dlp soporta — no necesitan navegador
       ni cookies, así que también sirven en máquinas sin ninguno instalado.
    3. Si eso tampoco alcanza, reintenta con las cookies de sesión de
       cualquier navegador que esté instalado en esta máquina, probando uno
       por uno hasta que alguno funcione.

    Cualquier estrategia que no aplique (navegador no instalado, cliente sin
    ese formato, etc.) falla rápido y sigue con la siguiente — nunca se le
    pide nada al usuario. Si NINGUNA funciona, se propaga el último error.
    """
    global _last_working_strategy

    names = [_last_working_strategy, *(name for name, _ in _FALLBACK_STRATEGIES)]
    seen: set = set()
    ordered = [n for n in names if not (n in seen or seen.add(n))]

    last_exc: Exception | None = None
    for name in ordered:
        try:
            path = _yt_dlp_download(track, dest_dir, _STRATEGY_BY_NAME[name])
        except Exception as exc:  # noqa: BLE001 - se prueba la siguiente estrategia
            last_exc = exc
            continue
        _last_working_strategy = name
        return path

    assert last_exc is not None
    raise last_exc
