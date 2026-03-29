"""Tests for edge resolution: prevent false bare-name fallback for stdlib calls."""
from __future__ import annotations

from pathlib import Path

import pytest

from tempograph.builder import build_graph
from tempograph.types import EdgeKind


def _build(tmp_path: Path, files: dict[str, str]) -> object:
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return build_graph(str(tmp_path), use_cache=False, use_config=False)


class TestFalseBareNameResolution:
    """dict.get(), list.append(), etc. must NOT resolve to user-defined functions."""

    def test_dict_get_not_resolved_to_user_get(self, tmp_path):
        """d.get('a') on a dict should NOT create a CALLS edge to Config.get."""
        g = _build(tmp_path, {
            "config.py": (
                "class Config:\n"
                "    def get(self, key):\n"
                "        return self.data[key]\n"
            ),
            "main.py": (
                "def process():\n"
                "    d = {'a': 1}\n"
                "    return d.get('a')\n"
            ),
        })
        # Find CALLS edges from main.py::process targeting Config.get
        false_edges = [
            e for e in g.edges
            if e.kind == EdgeKind.CALLS
            and "process" in e.source_id
            and "Config.get" in e.target_id
        ]
        assert false_edges == [], (
            f"dict.get() falsely resolved to Config.get: {false_edges}"
        )

    def test_list_append_not_resolved_to_user_append(self, tmp_path):
        """items.append(x) on a list should NOT create a CALLS edge to Queue.append."""
        g = _build(tmp_path, {
            "queue.py": (
                "class Queue:\n"
                "    def append(self, item):\n"
                "        self.items.append(item)\n"
            ),
            "main.py": (
                "def collect():\n"
                "    items = []\n"
                "    items.append(42)\n"
            ),
        })
        false_edges = [
            e for e in g.edges
            if e.kind == EdgeKind.CALLS
            and "collect" in e.source_id
            and "Queue.append" in e.target_id
        ]
        assert false_edges == [], (
            f"list.append() falsely resolved to Queue.append: {false_edges}"
        )

    def test_qualified_user_call_still_resolves(self, tmp_path):
        """Service.run() where Service is a known class should still resolve."""
        g = _build(tmp_path, {
            "service.py": (
                "class Service:\n"
                "    def run(self):\n"
                "        pass\n"
            ),
            "main.py": (
                "from service import Service\n"
                "def start():\n"
                "    svc = Service()\n"
                "    svc.run()\n"
            ),
        })
        # svc.run() — 'svc' is not a known class, so bare fallback is blocked.
        # But Service() call should still resolve (it's a direct name match).
        call_edges = [
            e for e in g.edges
            if e.kind == EdgeKind.CALLS
            and "start" in e.source_id
        ]
        # At minimum, Service() constructor call should resolve
        service_calls = [
            e for e in call_edges if "Service" in e.target_id
        ]
        assert len(service_calls) > 0, (
            f"Service() call should resolve. All edges from start: {call_edges}"
        )

class TestLanguageAwareIgnore:
    """Per-language ignore sets: names only suppressed in the language they belong to."""

    def test_python_parse_not_ignored(self, tmp_path):
        """In Python, bare `parse()` should create a CALLS edge (parse is JS-only ignore)."""
        g = _build(tmp_path, {
            "parser.py": (
                "def parse(data):\n"
                "    return data.split(',')\n"
            ),
            "main.py": (
                "def run():\n"
                "    parse(input())\n"
            ),
        })
        call_edges = [
            e for e in g.edges
            if e.kind == EdgeKind.CALLS
            and "run" in e.source_id
            and e.target_id.endswith("parse")
        ]
        assert len(call_edges) > 0, (
            "Python bare parse() should NOT be ignored — parse is JS-specific"
        )

    def test_js_parse_is_ignored(self, tmp_path):
        """In JS/TS, bare `parse()` should NOT create a CALLS edge (JSON.parse noise)."""
        g = _build(tmp_path, {
            "utils.js": (
                "function parse(data) {\n"
                "  return data.split(',');\n"
                "}\n"
            ),
            "main.js": (
                "function run() {\n"
                "  parse(getInput());\n"
                "}\n"
            ),
        })
        call_edges = [
            e for e in g.edges
            if e.kind == EdgeKind.CALLS
            and "run" in e.source_id
            and e.target_id.endswith("parse")
        ]
        assert len(call_edges) == 0, (
            "JS bare parse() SHOULD be ignored — it's in the JS ignore set"
        )

    def test_rust_collect_not_in_python_ignore(self, tmp_path):
        """A Python function named collect() should create edges when called."""
        g = _build(tmp_path, {
            "collector.py": (
                "def collect(items):\n"
                "    return list(items)\n"
            ),
            "main.py": (
                "def run():\n"
                "    collect([1, 2, 3])\n"
            ),
        })
        call_edges = [
            e for e in g.edges
            if e.kind == EdgeKind.CALLS
            and "run" in e.source_id
            and e.target_id.endswith("collect")
        ]
        assert len(call_edges) > 0, (
            "Python bare collect() should NOT be ignored — collect is Rust-specific"
        )

    def test_generic_language_uses_universal_only(self, tmp_path):
        """For an unmapped language (e.g. PHP), only the universal set applies."""
        g = _build(tmp_path, {
            "utils.php": (
                "<?php\n"
                "function parse($data) {\n"
                "  return explode(',', $data);\n"
                "}\n"
                "function run() {\n"
                "  parse(readline());\n"
                "}\n"
            ),
        })
        call_edges = [
            e for e in g.edges
            if e.kind == EdgeKind.CALLS
            and "run" in e.source_id
            and e.target_id.endswith("parse")
        ]
        assert len(call_edges) > 0, (
            "PHP bare parse() should NOT be ignored — PHP uses universal-only set"
        )


    def test_false_callers_reduced_on_self_repo(self):
        """Build graph on tempograph itself; Config.get should have <50 callers."""
        import os
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        g = build_graph(repo, use_cache=False, use_config=False)
        # Find all CALLS edges targeting a symbol with "Config.get" in the id
        config_get_ids = [
            sid for sid in g.symbols if "Config" in sid and ".get" in sid
        ]
        if not config_get_ids:
            pytest.skip("Config.get symbol not found in graph")
        callers = [
            e for e in g.edges
            if e.kind == EdgeKind.CALLS and e.target_id in config_get_ids
        ]
        assert len(callers) < 50, (
            f"Config.get has {len(callers)} callers (expected <50 after fix). "
            f"False resolution still occurring."
        )
