"""Biblioteca curada de tracks (dedup por id). Ver models.py y config.py."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ytaudio.config import LIBRARY_FILE, atomic_write_json, read_json
from ytaudio.models import Track


@dataclass
class LibraryEntry:
    track: Track
    added_at: str
    last_played_at: str
    play_count: int

    def to_dict(self) -> dict:
        return {
            "track": self.track.to_dict(),
            "added_at": self.added_at,
            "last_played_at": self.last_played_at,
            "play_count": self.play_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LibraryEntry":
        return cls(
            track=Track.from_dict(d["track"]),
            added_at=d["added_at"],
            last_played_at=d["last_played_at"],
            play_count=d["play_count"],
        )


class Library:
    def __init__(self, path: Path = LIBRARY_FILE):
        self._path = path
        data = read_json(path, [])
        entries = [LibraryEntry.from_dict(d) for d in data]
        self._entries: dict[str, LibraryEntry] = {e.track.id: e for e in entries}

    def _persist(self) -> None:
        atomic_write_json(self._path, [e.to_dict() for e in self._entries.values()])

    def all(self) -> list[LibraryEntry]:
        return list(self._entries.values())

    def upsert(self, track: Track) -> LibraryEntry:
        now = datetime.now().isoformat()
        existing = self._entries.get(track.id)
        if existing is not None:
            existing.track = track
            existing.play_count += 1
            existing.last_played_at = now
        else:
            existing = LibraryEntry(
                track=track,
                added_at=now,
                last_played_at=now,
                play_count=1,
            )
            self._entries[track.id] = existing
        self._persist()
        return existing

    def remove(self, track_id: str) -> None:
        if track_id in self._entries:
            del self._entries[track_id]
            self._persist()

    def rename(self, track_id: str, new_title: str) -> None:
        entry = self._entries.get(track_id)
        if entry is None:
            return
        entry.track.title = new_title
        self._persist()

    def search(self, query: str) -> list[LibraryEntry]:
        q = query.lower()
        return [
            e
            for e in self._entries.values()
            if q in e.track.title.lower() or q in (e.track.uploader or "").lower()
        ]

    def get(self, track_id: str) -> LibraryEntry | None:
        return self._entries.get(track_id)
