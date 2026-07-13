"""Rutas y configuración persistente (XDG). Contrato congelado, ver models.py."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from ytaudio.models import RepeatMode

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))).expanduser() / "ytaudio"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))).expanduser() / "ytaudio"

LIBRARY_FILE = CONFIG_DIR / "library.json"
HISTORY_FILE = CONFIG_DIR / "history.json"
SETTINGS_FILE = CONFIG_DIR / "settings.json"

CACHE_INDEX_FILE = CACHE_DIR / "index.json"
CACHE_AUDIO_DIR = CACHE_DIR / "audio"

RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
MPV_SOCKET = RUNTIME_DIR / "ytaudio-mpv.sock"


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_AUDIO_DIR.mkdir(parents=True, exist_ok=True)


def atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


@dataclass
class Settings:
    volume: int = 100
    shuffle: bool = False
    repeat: RepeatMode = RepeatMode.OFF
    auto_cache: bool = True
    max_cache_mb: int | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["repeat"] = self.repeat.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        d = dict(d)
        if "repeat" in d:
            d["repeat"] = RepeatMode(d["repeat"])
        return cls(**d)


def load_settings() -> Settings:
    data = read_json(SETTINGS_FILE, None)
    if data is None:
        return Settings()
    try:
        return Settings.from_dict(data)
    except (KeyError, ValueError, TypeError):
        return Settings()


def save_settings(settings: Settings) -> None:
    atomic_write_json(SETTINGS_FILE, settings.to_dict())
