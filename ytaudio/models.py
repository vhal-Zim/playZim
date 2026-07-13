"""Contrato compartido: tipos usados por todos los demás módulos.

Este archivo se congela antes de repartir trabajo en paralelo — ningún otro
módulo debe redefinir estos tipos, solo importarlos de aquí.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional, Protocol


class RepeatMode(str, Enum):
    OFF = "off"
    ONE = "one"
    ALL = "all"


class PlaybackState(str, Enum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"


@dataclass
class Track:
    id: str
    title: str
    url: str
    uploader: Optional[str] = None
    duration: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "uploader": self.uploader,
            "duration": self.duration,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Track":
        return cls(
            id=d["id"],
            title=d["title"],
            url=d["url"],
            uploader=d.get("uploader"),
            duration=d.get("duration"),
        )


@dataclass
class PlayerStatus:
    track: Optional[Track]
    state: PlaybackState
    position: float = 0.0
    duration: Optional[float] = None
    volume: int = 100
    shuffle: bool = False
    repeat: RepeatMode = RepeatMode.OFF


class PlayerObserver(Protocol):
    def on_status_changed(self, status: PlayerStatus) -> None: ...

    def on_track_started(self, track: Track) -> None: ...


@dataclass
class PlayableSource:
    """Lo que cache.py devuelve y player.py consume para reproducir."""

    path: str
    is_local: bool


# Firma que resolver.py debe exponer para descargar audio a un directorio;
# cache.py depende únicamente de esta firma (puede stubearse sin importar resolver.py).
DownloadAudioFn = Callable[[Track, str], str]
