"""Tests for tempo/kernel/config.py: Config.set, get, load, save."""
from __future__ import annotations

import json
import pytest

from tempo.kernel.config import Config


class TestConfigSet:
    def test_set_stores_value(self):
        c = Config()
        c.set("my_key", "my_value")
        assert c.get("my_key") == "my_value"

    def test_set_overwrites_existing(self):
        c = Config()
        c.set("max_tokens", 8000)
        assert c.get("max_tokens") == 8000

    def test_set_then_get_round_trips(self):
        c = Config()
        c.set("telemetry", False)
        assert c.get("telemetry") is False

    def test_set_any_type(self):
        c = Config()
        c.set("list_val", [1, 2, 3])
        assert c.get("list_val") == [1, 2, 3]


class TestConfigGetDefault:
    def test_get_default_max_tokens(self):
        c = Config()
        assert c.get("max_tokens") == 4000

    def test_get_missing_key_returns_none(self):
        c = Config()
        assert c.get("nonexistent_key") is None

    def test_get_missing_key_with_default(self):
        c = Config()
        assert c.get("nonexistent_key", "fallback") == "fallback"


class TestConfigLoadSave:
    def test_load_from_file(self, tmp_path):
        cfg_dir = tmp_path / ".tempo"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").write_text(json.dumps({"max_tokens": 9000}))
        c = Config(str(tmp_path))
        assert c.get("max_tokens") == 9000

    def test_save_creates_file(self, tmp_path):
        c = Config(str(tmp_path))
        c.set("telemetry", False)
        c.save()
        assert (tmp_path / ".tempo" / "config.json").exists()

    def test_save_then_load_round_trips(self, tmp_path):
        c = Config(str(tmp_path))
        c.set("ui_theme", "light")
        c.save()
        c2 = Config(str(tmp_path))
        assert c2.get("ui_theme") == "light"

    def test_invalid_json_file_uses_defaults(self, tmp_path):
        cfg_dir = tmp_path / ".tempo"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").write_text("not valid json {{")
        c = Config(str(tmp_path))
        assert c.get("max_tokens") == 4000
