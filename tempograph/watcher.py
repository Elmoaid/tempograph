"""File watcher for incremental graph updates.

Uses watchfiles (Rust-backed) to detect file changes and incrementally
update the SQLite graph database without full rebuild.

Usage:
    from tempograph.watcher import GraphWatcher
    watcher = GraphWatcher("/path/to/repo")
    watcher.start()  # background thread
    watcher.stop()
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from .builder import (
    DEFAULT_IGNORE_DIRS,
    DEFAULT_IGNORE_FILES,
    MAX_FILE_SIZE,
    _parse_file,
)
from .storage import GraphDB, content_hash
from .types import EXTENSION_TO_LANGUAGE, Language

# Non-code extensions that don't need reparsing
_SKIP_EXTENSIONS = frozenset({
    ".json", ".toml", ".yaml", ".yml", ".css", ".html",
    ".sh", ".bash", ".md", ".txt", ".lock", ".log",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".woff", ".woff2", ".ttf", ".eot",
})

# Debounce window in seconds
_DEBOUNCE_SECS = 2.0


class GraphWatcher:
    """Watches a repository for file changes and incrementally updates the graph DB."""

    def __init__(
        self,
        root: str | Path,
        *,
        exclude_dirs: list[str] | None = None,
        on_update: Callable[[list[str]], None] | None = None,
    ):
        self.root = Path(root).resolve()
        self.exclude_dirs = set(exclude_dirs or []) | set(DEFAULT_IGNORE_DIRS)
        self.on_update = on_update
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._db: GraphDB | None = None

    def start(self) -> None:
        """Start watching in a background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the watcher."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        if self._db:
            self._db.close()
            self._db = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _watch_loop(self) -> None:
        try:
            from watchfiles import watch, Change
        except ImportError:
            return  # watchfiles not installed, silently skip

        self._db = GraphDB(self.root)

        # Build filter for ignored directories
        ignore_paths = {str(self.root / d) for d in self.exclude_dirs}

        def should_watch(change: Change, path: str) -> bool:
            p = Path(path)
            # Skip ignored directories
            for ignore in ignore_paths:
                if path.startswith(ignore):
                    return False
            # Skip ignored files
            if p.name in DEFAULT_IGNORE_FILES:
                return False
            # Skip non-code files
            if p.suffix.lower() in _SKIP_EXTENSIONS:
                return False
            # Skip large files
            try:
                if p.stat().st_size > MAX_FILE_SIZE:
                    return False
            except OSError:
                return False
            return True

        for changes in watch(
            str(self.root),
            watch_filter=should_watch,
            stop_event=self._stop_event,
            debounce=int(_DEBOUNCE_SECS * 1000),
            recursive=True,
        ):
            if self._stop_event.is_set():
                break

            updated_files: list[str] = []

            for change_type, path_str in changes:
                path = Path(path_str)
                rel_path = str(path.relative_to(self.root))
                ext = path.suffix.lower()
                language = EXTENSION_TO_LANGUAGE.get(ext, Language.UNKNOWN)

                if change_type == Change.deleted:
                    # File deleted — remove from DB
                    self._db.remove_stale_files(
                        self._db._conn.execute("SELECT path FROM files").fetchall()
                        and set()  # force removal check
                    )
                    updated_files.append(rel_path)
                    continue

                # File added or modified
                try:
                    source = path.read_bytes()
                except (OSError, PermissionError):
                    continue

                file_hash = content_hash(source)

                # Skip if unchanged
                if self._db.file_hash_matches(rel_path, file_hash):
                    continue

                line_count = source.count(b"\n") + (1 if source and not source.endswith(b"\n") else 0)

                # Parse the file
                from .builder import _is_parseable
                if _is_parseable(language):
                    symbols, edges, imports = _parse_file(rel_path, language, source, is_tauri=False)
                else:
                    symbols, edges, imports = [], [], []

                # Update DB
                self._db.update_file(
                    rel_path, file_hash, language.value,
                    line_count, len(source), symbols, edges, imports,
                )
                updated_files.append(rel_path)

            if updated_files and self.on_update:
                self.on_update(updated_files)

        if self._db:
            self._db.close()
            self._db = None
