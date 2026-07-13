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


def download_audio(track: Track, dest_dir: str) -> str:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "format": "bestaudio/best",
        "outtmpl": f"{dest_dir}/%(id)s.%(ext)s",
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(track.url, download=True)
        if "requested_downloads" in info and info["requested_downloads"]:
            return info["requested_downloads"][0]["filepath"]
        return ydl.prepare_filename(info)
