"""Tests for build_graph: file discovery, caching, exclude logic, graph structure."""
from __future__ import annotations

from pathlib import Path

import pytest

from tempograph.builder import build_graph, load_from_snapshot
from tempograph.types import Language, SymbolKind, EdgeKind


def _build(tmp_path: Path, files: dict[str, str], **kwargs) -> object:
    for name, content in files.items():
        (tmp_path / name).write_text(content)
    return build_graph(str(tmp_path), use_cache=False, use_config=False, **kwargs)


# ── Basic symbol extraction ───────────────────────────────────────────────────

class TestBuildGraph:
    def test_python_function_extracted(self, tmp_path):
        g = _build(tmp_path, {"utils.py": "def greet(): pass\n"})
        assert any(s.name == "greet" for s in g.symbols.values())

    def test_multiple_files_extracted(self, tmp_path):
        g = _build(tmp_path, {
            "a.py": "def fn_a(): pass\n",
            "b.py": "def fn_b(): pass\n",
        })
        names = {s.name for s in g.symbols.values()}
        assert "fn_a" in names
        assert "fn_b" in names

    def test_file_info_registered(self, tmp_path):
        g = _build(tmp_path, {"mod.py": "def f(): pass\n"})
        assert any("mod.py" in fp for fp in g.files)

    def test_language_detected_from_extension(self, tmp_path):
        g = _build(tmp_path, {"server.ts": "function start(): void {}\n"})
        ts_files = [fi for fp, fi in g.files.items() if "server.ts" in fp]
        assert ts_files and ts_files[0].language == Language.TYPESCRIPT

    def test_empty_dir_returns_empty_graph(self, tmp_path):
        g = build_graph(str(tmp_path), use_cache=False, use_config=False)
        assert len(g.symbols) == 0
        assert len(g.files) == 0


# ── Import resolution ─────────────────────────────────────────────────────────

class TestImportResolution:
    def test_import_edge_created_between_files(self, tmp_path):
        g = _build(tmp_path, {
            "main.py": "from utils import helper\ndef run(): helper()\n",
            "utils.py": "def helper(): pass\n",
        })
        import_edges = [e for e in g.edges if e.kind == EdgeKind.IMPORTS]
        assert len(import_edges) > 0

    def test_import_edge_source_is_importer(self, tmp_path):
        g = _build(tmp_path, {
            "app.py": "from lib import do_thing\n",
            "lib.py": "def do_thing(): pass\n",
        })
        import_edges = [e for e in g.edges if e.kind == EdgeKind.IMPORTS]
        assert any("app.py" in e.source_id for e in import_edges)

    def test_call_edge_resolved_across_files(self, tmp_path):
        g = _build(tmp_path, {
            "caller.py": "from callee import target\ndef run():\n    target()\n",
            "callee.py": "def target(): pass\n",
        })
        call_edges = [e for e in g.edges if e.kind == EdgeKind.CALLS]
        assert len(call_edges) > 0


# ── Exclude logic ─────────────────────────────────────────────────────────────

class TestExcludeDirs:
    def test_exclude_dirs_filters_directory(self, tmp_path):
        (tmp_path / "vendor").mkdir()
        (tmp_path / "vendor" / "lib.py").write_text("def vendored(): pass\n")
        (tmp_path / "app.py").write_text("def app(): pass\n")
        g = build_graph(str(tmp_path), use_cache=False, use_config=False, exclude_dirs=["vendor"])
        names = {s.name for s in g.symbols.values()}
        assert "vendored" not in names
        assert "app" in names

    def test_exclude_dirs_comma_string_normalized(self, tmp_path):
        (tmp_path / "dist").mkdir()
        (tmp_path / "dist" / "bundle.py").write_text("def bundled(): pass\n")
        (tmp_path / "src.py").write_text("def src_fn(): pass\n")
        g = build_graph(str(tmp_path), use_cache=False, use_config=False, exclude_dirs="dist")
        names = {s.name for s in g.symbols.values()}
        assert "bundled" not in names

    def test_multiple_exclude_dirs(self, tmp_path):
        for d in ["node_modules", "dist"]:
            (tmp_path / d).mkdir()
            (tmp_path / d / "f.py").write_text(f"def fn_{d}(): pass\n")
        (tmp_path / "src.py").write_text("def real(): pass\n")
        g = build_graph(str(tmp_path), use_cache=False, use_config=False,
                        exclude_dirs=["node_modules", "dist"])
        names = {s.name for s in g.symbols.values()}
        assert "fn_node_modules" not in names
        assert "fn_dist" not in names
        assert "real" in names


# ── Graph structure ───────────────────────────────────────────────────────────

class TestGraphStructure:
    def test_build_indexes_populates_callers(self, tmp_path):
        g = _build(tmp_path, {
            "a.py": "from b import fn\ndef caller():\n    fn()\n",
            "b.py": "def fn(): pass\n",
        })
        fn_sym = next((s for s in g.symbols.values() if s.name == "fn"), None)
        assert fn_sym is not None
        callers = g.callers_of(fn_sym.id)
        assert len(callers) > 0

    def test_build_indexes_populates_callees(self, tmp_path):
        g = _build(tmp_path, {
            "a.py": "from b import fn\ndef caller():\n    fn()\n",
            "b.py": "def fn(): pass\n",
        })
        caller_sym = next((s for s in g.symbols.values() if s.name == "caller"), None)
        assert caller_sym is not None
        callees = g.callees_of(caller_sym.id)
        assert len(callees) > 0

    def test_root_attribute_set_correctly(self, tmp_path):
        g = _build(tmp_path, {"mod.py": "x = 1\n"})
        assert g.root == str(tmp_path)

    def test_symbol_ids_are_unique_across_files(self, tmp_path):
        g = _build(tmp_path, {
            "a.py": "def foo(): pass\n",
            "b.py": "def foo(): pass\n",  # same name, different file
        })
        ids = list(g.symbols.keys())
        assert len(ids) == len(set(ids))


# ── Large file exclusion ──────────────────────────────────────────────────────

class TestLargeFileExclusion:
    def test_normal_file_included(self, tmp_path):
        (tmp_path / "small.py").write_text("def small(): pass\n")
        g = build_graph(str(tmp_path), use_cache=False, use_config=False)
        assert any("small.py" in fp for fp in g.files)


# ── use_db=False path ─────────────────────────────────────────────────────────

class TestNoDB:
    def test_no_db_still_extracts_symbols(self, tmp_path):
        g = build_graph(str(tmp_path / "repo"),
                        use_cache=False, use_config=False, use_db=False) \
            if False else None  # skip (needs existing dir)
        # Alternative: use tmp_path directly with use_db=False
        (tmp_path / "fn.py").write_text("def no_db_fn(): pass\n")
        g = build_graph(str(tmp_path), use_cache=False, use_config=False, use_db=False)
        assert any(s.name == "no_db_fn" for s in g.symbols.values())

    def test_no_db_creates_no_sqlite_file(self, tmp_path):
        (tmp_path / "mod.py").write_text("def x(): pass\n")
        build_graph(str(tmp_path), use_cache=False, use_config=False, use_db=False)
        db_path = tmp_path / ".tempograph" / "graph.db"
        assert not db_path.exists()


# ── Tempo config exclude_dirs ─────────────────────────────────────────────────

class TestTempoConfig:
    def test_config_exclude_dirs_respected(self, tmp_path):
        (tmp_path / ".tempo").mkdir()
        (tmp_path / ".tempo" / "config.json").write_text('{"exclude_dirs": ["hidden"]}')
        (tmp_path / "hidden").mkdir()
        (tmp_path / "hidden" / "secret.py").write_text("def secret(): pass\n")
        (tmp_path / "visible.py").write_text("def visible(): pass\n")
        g = build_graph(str(tmp_path), use_cache=False, use_config=True)
        names = {s.name for s in g.symbols.values()}
        assert "secret" not in names
        assert "visible" in names


# ── load_from_snapshot ────────────────────────────────────────────────────────

class TestLoadFromSnapshot:
    def test_missing_snapshot_raises_file_not_found(self):
        import pytest
        with pytest.raises(FileNotFoundError):
            load_from_snapshot("nonexistent/repo-xyz-abc-123")
