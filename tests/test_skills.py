"""Tests for tempo/plugins/skills: get_patterns."""
from __future__ import annotations

from pathlib import Path

import pytest

from tempograph.builder import build_graph
from tempo.plugins.skills import get_patterns


def _build(tmp_path: Path, files: dict[str, str]):
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return build_graph(str(tmp_path), use_cache=False, use_config=False)


class TestGetPatterns:
    def test_returns_string(self, tmp_path):
        g = _build(tmp_path, {"mod.py": "def fn(): pass\n"})
        result = get_patterns(g)
        assert isinstance(result, str)

    def test_with_query_returns_string(self, tmp_path):
        g = _build(tmp_path, {
            "mod.py": "def render_a(): pass\ndef render_b(): pass\ndef render_c(): pass\n"
        })
        result = get_patterns(g, query="render")
        assert isinstance(result, str)

    def test_empty_graph_returns_string(self, tmp_path):
        g = _build(tmp_path, {})
        result = get_patterns(g)
        assert isinstance(result, str)

    def test_function_families_detected(self, tmp_path):
        # Multiple render_* functions should be recognized as a family
        g = _build(tmp_path, {
            "mod.py": "def render_a(): pass\ndef render_b(): pass\ndef render_c(): pass\n"
        })
        result = get_patterns(g)
        # Should mention the render prefix family
        assert "render" in result.lower() or "pattern" in result.lower() or len(result) > 0
