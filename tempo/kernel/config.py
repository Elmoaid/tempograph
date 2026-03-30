"""User configuration — feature flags, prefs, persisted to .tempo/config.json."""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_CONFIG = {
    "enabled_plugins": [],  # empty = use plugin defaults
    "disabled_plugins": [],
    "max_tokens": 4000,
    "token_budget": "auto",
    "ui_theme": "dark",
    "telemetry": True,
    "learning": True,
    "exclude_dirs": [],  # directory prefixes excluded from graph (e.g. ["archive", "bench"])
}


class Config:
    """Manages user configuration with file persistence."""

    def __init__(self, repo_path: str | None = None):
        self._data = dict(DEFAULT_CONFIG)
        self._path: Path | None = None
        if repo_path:
            self._path = Path(repo_path) / ".tempo" / "config.json"
            self._load()

    def _load(self) -> None:
        if self._path and self._path.exists():
            try:
                with open(self._path) as f:
                    saved = json.loads(f.read())
                self._data.update(saved)
            except (json.JSONDecodeError, OSError):
                pass

    def save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(self._data, f, indent=2)

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value

