"""Tests for tempo/kernel/registry.py: load, status, enable, disable."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from tempo.kernel.registry import Registry, PluginInfo


def _make_plugin_module(name: str, provides: list[str] | None = None, depends: list[str] | None = None) -> MagicMock:
    """Create a mock module with a PLUGIN dict."""
    mod = MagicMock()
    mod.PLUGIN = {
        "name": name,
        "provides": provides or [],
        "depends": depends or [],
        "default": False,  # don't auto-enable in tests
        "description": f"Mock plugin {name}",
    }
    mod.run = MagicMock(return_value="mock output")
    return mod


# ── Registry.load ─────────────────────────────────────────────────────────────

class TestRegistryLoad:
    def test_load_valid_module(self, monkeypatch):
        r = Registry()
        mock_mod = _make_plugin_module("myplugin", provides=["mymode"])
        import importlib
        monkeypatch.setattr(importlib, "import_module", lambda path: mock_mod)
        info = r.load("tempo.plugins.myplugin")
        assert info is not None
        assert info.name == "myplugin"

    def test_load_returns_none_on_import_error(self, monkeypatch):
        r = Registry()
        import importlib
        monkeypatch.setattr(importlib, "import_module", lambda path: (_ for _ in ()).throw(ImportError()))
        info = r.load("tempo.plugins.nonexistent")
        assert info is None

    def test_load_stores_plugin_info(self, monkeypatch):
        r = Registry()
        mock_mod = _make_plugin_module("pluginA")
        import importlib
        monkeypatch.setattr(importlib, "import_module", lambda path: mock_mod)
        r.load("tempo.plugins.pluginA")
        assert "pluginA" in r.plugins

    def test_load_registers_mode_mapping(self, monkeypatch):
        r = Registry()
        mock_mod = _make_plugin_module("myplug", provides=["somemode"])
        import importlib
        monkeypatch.setattr(importlib, "import_module", lambda path: mock_mod)
        r.load("tempo.plugins.myplug")
        runner = r.get_runner("somemode")
        # Plugin is not enabled by default=False, so runner should be None
        assert runner is None

    def test_load_returns_none_when_no_plugin_meta(self, monkeypatch):
        r = Registry()
        mod = MagicMock()
        del mod.PLUGIN  # no PLUGIN attribute
        mod.PLUGIN = None
        import importlib
        monkeypatch.setattr(importlib, "import_module", lambda path: mod)
        info = r.load("tempo.plugins.empty")
        assert info is None


# ── Registry.status ───────────────────────────────────────────────────────────

class TestRegistryStatus:
    def test_empty_registry_status(self):
        r = Registry()
        s = r.status()
        assert s["plugins"] == {}
        assert s["modes"] == {}
        assert s["enabled_count"] == 0
        assert s["total_count"] == 0

    def test_status_has_expected_keys(self):
        r = Registry()
        s = r.status()
        assert "plugins" in s
        assert "modes" in s
        assert "enabled_count" in s
        assert "total_count" in s

    def test_status_after_load(self, monkeypatch):
        r = Registry()
        mock_mod = _make_plugin_module("p1", provides=["mode1"])
        import importlib
        monkeypatch.setattr(importlib, "import_module", lambda path: mock_mod)
        r.load("tempo.plugins.p1")
        s = r.status()
        assert s["total_count"] == 1
        assert "p1" in s["plugins"]
        assert s["plugins"]["p1"]["enabled"] is False  # default=False

    def test_status_shows_enabled(self, monkeypatch):
        r = Registry()
        mock_mod = _make_plugin_module("p2")
        import importlib
        monkeypatch.setattr(importlib, "import_module", lambda path: mock_mod)
        r.load("tempo.plugins.p2")
        r.enable("p2")
        s = r.status()
        assert s["plugins"]["p2"]["enabled"] is True
        assert s["enabled_count"] == 1


# ── Registry enable/disable ───────────────────────────────────────────────────

class TestRegistryEnableDisable:
    def test_enable_unknown_returns_empty(self):
        r = Registry()
        result = r.enable("unknown")
        assert result == []

    def test_enable_returns_plugin_name(self, monkeypatch):
        r = Registry()
        mock_mod = _make_plugin_module("px")
        import importlib
        monkeypatch.setattr(importlib, "import_module", lambda path: mock_mod)
        r.load("tempo.plugins.px")
        result = r.enable("px")
        assert "px" in result

    def test_disable_enabled_plugin(self, monkeypatch):
        r = Registry()
        mock_mod = _make_plugin_module("py")
        import importlib
        monkeypatch.setattr(importlib, "import_module", lambda path: mock_mod)
        r.load("tempo.plugins.py")
        r.enable("py")
        success, blocked = r.disable("py")
        assert success
        assert blocked == []
