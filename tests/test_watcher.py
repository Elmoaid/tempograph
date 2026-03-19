"""Tests for file watcher module."""
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from tempograph.watcher import GraphWatcher, _SKIP_EXTENSIONS, _DEBOUNCE_SECS


class TestGraphWatcher:
    def test_init(self, tmp_path):
        watcher = GraphWatcher(tmp_path)
        assert watcher.root == tmp_path.resolve()
        assert not watcher.is_running

    def test_skip_extensions(self):
        assert ".json" in _SKIP_EXTENSIONS
        assert ".md" in _SKIP_EXTENSIONS
        assert ".png" in _SKIP_EXTENSIONS
        assert ".py" not in _SKIP_EXTENSIONS
        assert ".ts" not in _SKIP_EXTENSIONS
        assert ".rs" not in _SKIP_EXTENSIONS

    def test_debounce_is_reasonable(self):
        assert 1.0 <= _DEBOUNCE_SECS <= 5.0

    def test_exclude_dirs_merged_with_defaults(self, tmp_path):
        watcher = GraphWatcher(tmp_path, exclude_dirs=["custom_dir"])
        assert "custom_dir" in watcher.exclude_dirs
        assert "node_modules" in watcher.exclude_dirs
        assert ".git" in watcher.exclude_dirs

    def test_stop_when_not_running(self, tmp_path):
        watcher = GraphWatcher(tmp_path)
        watcher.stop()  # should not raise
        assert not watcher.is_running

    def test_on_update_callback_stored(self, tmp_path):
        cb = MagicMock()
        watcher = GraphWatcher(tmp_path, on_update=cb)
        assert watcher.on_update is cb

    def test_start_stop_lifecycle(self, tmp_path):
        """Start and stop without errors (watchfiles may or may not be installed)."""
        watcher = GraphWatcher(tmp_path)
        watcher.start()
        assert watcher.is_running
        watcher.stop()
        assert not watcher.is_running
