"""Historial append-only de reproducciones. Ver models.py y config.py."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ytaudio.config import HISTORY_FILE, atomic_write_json, read_json
from ytaudio.models import Track


@dataclass
class HistoryEntry:
    track: Track
    played_at: str

    def to_dict(self) -> dict:
        return {
            "track": self.track.to_dict(),
            "played_at": self.played_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "HistoryEntry":
        return cls(
            track=Track.from_dict(d["track"]),
            played_at=d["played_at"],
        )


class History:
    def __init__(self, path: Path = HISTORY_FILE, max_entries: int | None = 2000):
        self._path = path
        self._max_entries = max_entries
        data = read_json(path, [])
        self._entries: list[HistoryEntry] = [HistoryEntry.from_dict(d) for d in data]

    def _persist(self) -> None:
        atomic_write_json(self._path, [e.to_dict() for e in self._entries])

    def record(self, track: Track) -> None:
        self._entries.append(HistoryEntry(track=track, played_at=datetime.now().isoformat()))
        if self._max_entries is not None and len(self._entries) > self._max_entries:
            self._entries = self._entries[-self._max_entries :]
        self._persist()

    def recent(self, limit: int = 50) -> list[HistoryEntry]:
        return list(reversed(self._entries[-limit:]))

    def clear(self) -> None:
        self._entries = []
        self._persist()
