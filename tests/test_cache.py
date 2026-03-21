"""Tests for tempograph/cache.py: JSON file-based parse result cache."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tempograph.cache import load_cache, save_cache, make_cache_entry, check_cache


# ── load_cache ────────────────────────────────────────────────────────────────

class TestLoadCache:
    def test_missing_dir_returns_empty(self, tmp_path):
        result = load_cache(tmp_path)
        assert result == {}

    def test_missing_file_returns_empty(self, tmp_path):
        (tmp_path / ".tempograph").mkdir()
        result = load_cache(tmp_path)
        assert result == {}

    def test_valid_cache_returns_files(self, tmp_path):
        cache_dir = tmp_path / ".tempograph"
        cache_dir.mkdir()
        data = {"version": 2, "files": {"mod.py": {"hash": "abc"}}}
        (cache_dir / "cache.json").write_text(json.dumps(data))
        result = load_cache(tmp_path)
        assert "mod.py" in result

    def test_invalid_json_returns_empty(self, tmp_path):
        cache_dir = tmp_path / ".tempograph"
        cache_dir.mkdir()
        (cache_dir / "cache.json").write_text("not json {{")
        result = load_cache(tmp_path)
        assert result == {}

    def test_wrong_version_returns_empty(self, tmp_path):
        cache_dir = tmp_path / ".tempograph"
        cache_dir.mkdir()
        data = {"version": 1, "files": {"mod.py": {}}}
        (cache_dir / "cache.json").write_text(json.dumps(data))
        result = load_cache(tmp_path)
        assert result == {}


# ── save_cache ────────────────────────────────────────────────────────────────

class TestSaveCache:
    def test_creates_cache_file(self, tmp_path):
        save_cache(tmp_path, {"mod.py": {"hash": "abc"}})
        cache_file = tmp_path / ".tempograph" / "cache.json"
        assert cache_file.exists()

    def test_saved_data_is_loadable(self, tmp_path):
        original = {"mod.py": {"hash": "xyz", "symbols": [], "edges": [], "imports": []}}
        save_cache(tmp_path, original)
        result = load_cache(tmp_path)
        assert result == original

    def test_overwrites_previous_cache(self, tmp_path):
        save_cache(tmp_path, {"a.py": {"hash": "v1"}})
        save_cache(tmp_path, {"b.py": {"hash": "v2"}})
        result = load_cache(tmp_path)
        assert "b.py" in result
        assert "a.py" not in result

    def test_creates_dir_if_missing(self, tmp_path):
        save_cache(tmp_path, {})
        assert (tmp_path / ".tempograph").exists()


# ── make_cache_entry ──────────────────────────────────────────────────────────

class TestMakeCacheEntry:
    def test_returns_dict_with_required_keys(self):
        entry = make_cache_entry(b"source code", [], [], [])
        assert "hash" in entry
        assert "symbols" in entry
        assert "edges" in entry
        assert "imports" in entry

    def test_hash_is_string(self):
        entry = make_cache_entry(b"code", [], [], [])
        assert isinstance(entry["hash"], str)

    def test_same_source_same_hash(self):
        e1 = make_cache_entry(b"def fn(): pass", [], [], [])
        e2 = make_cache_entry(b"def fn(): pass", [], [], [])
        assert e1["hash"] == e2["hash"]

    def test_different_source_different_hash(self):
        e1 = make_cache_entry(b"def fn(): pass", [], [], [])
        e2 = make_cache_entry(b"def other(): pass", [], [], [])
        assert e1["hash"] != e2["hash"]

    def test_symbols_stored(self):
        syms = [{"id": "mod.py::fn", "name": "fn"}]
        entry = make_cache_entry(b"code", syms, [], [])
        assert entry["symbols"] == syms


# ── check_cache ───────────────────────────────────────────────────────────────

class TestCheckCache:
    def test_hit_returns_entry(self):
        source = b"def fn(): pass"
        entry = make_cache_entry(source, [], [], [])
        cache = {"mod.py": entry}
        result = check_cache(cache, "mod.py", source)
        assert result is entry

    def test_miss_on_stale_content(self):
        entry = make_cache_entry(b"old content", [], [], [])
        cache = {"mod.py": entry}
        result = check_cache(cache, "mod.py", b"new content")
        assert result is None

    def test_miss_on_unknown_path(self):
        cache = {}
        result = check_cache(cache, "nonexistent.py", b"code")
        assert result is None

    def test_empty_source_is_valid_hit(self):
        entry = make_cache_entry(b"", [], [], [])
        cache = {"empty.py": entry}
        result = check_cache(cache, "empty.py", b"")
        assert result is not None
