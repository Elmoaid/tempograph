"""Incremental build cache — stores per-file parse results keyed by content hash.

On rebuild, unchanged files skip parsing and load from cache.
Cache is stored as a single JSON file in .tempograph/cache.json inside the repo.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


CACHE_DIR = ".tempograph"
CACHE_FILE = "cache.json"


def _content_hash(source: bytes) -> str:
    return hashlib.md5(source).hexdigest()


def load_cache(root: str | Path) -> dict[str, Any]:
    """Load the parse cache for a repo. Returns {rel_path: {hash, symbols, edges, imports}}."""
    cache_path = Path(root) / CACHE_DIR / CACHE_FILE
    if not cache_path.exists():
        return {}
    try:
        data = json.loads(cache_path.read_text())
        if isinstance(data, dict) and data.get("version") == 2:
            return data.get("files", {})
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def save_cache(root: str | Path, file_cache: dict[str, Any]) -> None:
    """Save the parse cache."""
    cache_dir = Path(root) / CACHE_DIR
    cache_dir.mkdir(exist_ok=True)
    cache_path = cache_dir / CACHE_FILE
    data = {"version": 2, "files": file_cache}
    try:
        cache_path.write_text(json.dumps(data))
    except OSError:
        pass  # non-fatal


def make_cache_entry(source: bytes, symbols_data: list[dict], edges_data: list[dict], imports: list[str]) -> dict:
    """Create a cache entry for a parsed file."""
    return {
        "hash": _content_hash(source),
        "symbols": symbols_data,
        "edges": edges_data,
        "imports": imports,
    }


def check_cache(cache: dict[str, Any], rel_path: str, source: bytes) -> dict | None:
    """Check if a file's parse results are cached and still valid.
    Returns the cache entry if valid, None if stale/missing."""
    entry = cache.get(rel_path)
    if entry and entry.get("hash") == _content_hash(source):
        return entry
    return None
