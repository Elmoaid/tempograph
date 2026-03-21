"""Tests for SQLite persistent graph storage."""
import json
import pytest
from pathlib import Path

from tempograph.storage import GraphDB, content_hash
from tempograph.types import Edge, EdgeKind, Language, Symbol, SymbolKind


@pytest.fixture
def db(tmp_path):
    """Create a fresh GraphDB in a temp directory."""
    d = tmp_path / "repo"
    d.mkdir()
    db = GraphDB(d)
    yield db
    db.close()


def _make_symbol(file_path: str, name: str, kind=SymbolKind.FUNCTION, line=1) -> Symbol:
    return Symbol(
        id=f"{file_path}::{name}", name=name, qualified_name=name,
        kind=kind, language=Language.PYTHON, file_path=file_path,
        line_start=line, line_end=line + 5, signature=f"def {name}()",
        exported=True, complexity=3, byte_size=100,
    )


def _make_edge(source: str, target: str, kind=EdgeKind.CALLS) -> Edge:
    return Edge(kind=kind, source_id=source, target_id=target, line=10)


class TestGraphDB:
    def test_create_db(self, db):
        assert db.db_path.exists()
        assert db.symbol_count() == 0
        assert db.file_count() == 0

    def test_update_file_and_load(self, db):
        sym = _make_symbol("main.py", "main")
        edge = _make_edge("main.py::main", "utils.py::helper")
        db.update_file("main.py", "abc123", "python", 50, 1000, [sym], [edge], ["import utils"])

        assert db.file_count() == 1
        assert db.symbol_count() == 1

        files, symbols, edges = db.load_all()
        assert "main.py" in files
        assert files["main.py"].line_count == 50
        assert "main.py::main" in symbols
        assert symbols["main.py::main"].name == "main"
        assert len(edges) == 1
        assert edges[0].kind == EdgeKind.CALLS

    def test_load_all_lazy_edges(self, db):
        """lazy_edges=True returns files+symbols but empty edges list."""
        sym = _make_symbol("main.py", "main")
        edge = _make_edge("main.py::main", "utils.py::helper")
        db.update_file("main.py", "abc123", "python", 50, 1000, [sym], [edge], ["import utils"])

        files, symbols, edges = db.load_all(lazy_edges=True)
        assert "main.py" in files
        assert "main.py::main" in symbols
        assert len(edges) == 0  # edges skipped

        # Normal load still works after lazy load
        _, _, edges_full = db.load_all(lazy_edges=False)
        assert len(edges_full) == 1

    def test_hash_check(self, db):
        db.update_file("main.py", "hash1", "python", 10, 100, [], [], [])
        assert db.file_hash_matches("main.py", "hash1")
        assert not db.file_hash_matches("main.py", "hash2")
        assert not db.file_hash_matches("other.py", "hash1")

    def test_incremental_update(self, db):
        sym1 = _make_symbol("main.py", "old_func")
        db.update_file("main.py", "hash1", "python", 10, 100, [sym1], [], [])
        assert db.symbol_count() == 1

        # Update same file with different content
        sym2 = _make_symbol("main.py", "new_func")
        sym3 = _make_symbol("main.py", "another_func")
        db.update_file("main.py", "hash2", "python", 20, 200, [sym2, sym3], [], [])
        assert db.symbol_count() == 2  # old_func replaced by new_func + another_func

        _, symbols, _ = db.load_all()
        assert "main.py::old_func" not in symbols
        assert "main.py::new_func" in symbols
        assert "main.py::another_func" in symbols

    def test_remove_stale_files(self, db):
        db.update_file("keep.py", "h1", "python", 10, 100, [_make_symbol("keep.py", "f1")], [], [])
        db.update_file("stale.py", "h2", "python", 10, 100, [_make_symbol("stale.py", "f2")], [], [])
        assert db.file_count() == 2
        assert db.symbol_count() == 2

        removed = db.remove_stale_files({"keep.py"})
        assert removed == 1
        assert db.file_count() == 1
        assert db.symbol_count() == 1

    def test_fts_search(self, db):
        db.update_file("main.py", "h1", "python", 10, 100,
                       [_make_symbol("main.py", "build_graph"),
                        _make_symbol("main.py", "render_output")], [], [])
        results = db.search_fts("build_graph")
        assert len(results) > 0
        assert any("build_graph" in sid for _, sid in results)

    def test_fts_no_match(self, db):
        db.update_file("main.py", "h1", "python", 10, 100,
                       [_make_symbol("main.py", "process")], [], [])
        results = db.search_fts("nonexistent_xyz")
        assert len(results) == 0

    def test_multiple_files(self, db):
        for i in range(5):
            db.update_file(f"mod{i}.py", f"h{i}", "python", 10, 100,
                           [_make_symbol(f"mod{i}.py", f"func{i}")], [], [])
        assert db.file_count() == 5
        assert db.symbol_count() == 5

    def test_content_hash(self):
        h1 = content_hash(b"hello world")
        h2 = content_hash(b"hello world")
        h3 = content_hash(b"different")
        assert h1 == h2
        assert h1 != h3

    def test_imports_preserved(self, db):
        db.update_file("main.py", "h1", "python", 10, 100, [],
                       [], ["import os", "from pathlib import Path"])
        files, _, _ = db.load_all()
        assert files["main.py"].imports == ["import os", "from pathlib import Path"]

    def test_symbol_kinds(self, db):
        syms = [
            _make_symbol("main.py", "MyClass", SymbolKind.CLASS),
            _make_symbol("main.py", "my_func", SymbolKind.FUNCTION),
            _make_symbol("main.py", "my_method", SymbolKind.METHOD),
        ]
        db.update_file("main.py", "h1", "python", 30, 300, syms, [], [])
        _, symbols, _ = db.load_all()
        assert symbols["main.py::MyClass"].kind == SymbolKind.CLASS
        assert symbols["main.py::my_func"].kind == SymbolKind.FUNCTION
        assert symbols["main.py::my_method"].kind == SymbolKind.METHOD

    def test_save_and_load_indexes_roundtrip(self, db):
        indexes = {
            'callers': {'a::fn': ['b::caller']},
            'callees': {'b::caller': ['a::fn']},
            'children': {},
            'importers': {},
            'renderers': {},
            'subtypes': {},
        }
        db.save_indexes(indexes, edge_count=5)
        result = db.load_indexes(edge_count=5)
        assert result is not None
        assert result['callers'] == {'a::fn': ['b::caller']}
        assert result['callees'] == {'b::caller': ['a::fn']}

    def test_load_indexes_returns_none_on_stale_edge_count(self, db):
        indexes = {'callers': {}, 'callees': {}, 'children': {},
                   'importers': {}, 'renderers': {}, 'subtypes': {}}
        db.save_indexes(indexes, edge_count=10)
        result = db.load_indexes(edge_count=99)  # different count
        assert result is None

    def test_load_indexes_returns_none_when_missing(self, db):
        result = db.load_indexes(edge_count=0)
        assert result is None

    def test_save_indexes_overwrites_previous(self, db):
        indexes_v1 = {'callers': {'x': ['y']}, 'callees': {}, 'children': {},
                      'importers': {}, 'renderers': {}, 'subtypes': {}}
        indexes_v2 = {'callers': {'a': ['b']}, 'callees': {}, 'children': {},
                      'importers': {}, 'renderers': {}, 'subtypes': {}}
        db.save_indexes(indexes_v1, edge_count=1)
        db.save_indexes(indexes_v2, edge_count=1)
        result = db.load_indexes(edge_count=1)
        assert result['callers'] == {'a': ['b']}


# ── begin_batch / end_batch ───────────────────────────────────────────────────

class TestBatchOperations:
    def test_begin_batch_sets_flag(self, db):
        assert not db._batching
        db.begin_batch()
        assert db._batching

    def test_end_batch_clears_flag(self, db):
        db.begin_batch()
        db.end_batch()
        assert not db._batching

    def test_batch_writes_commit_on_end(self, db):
        db.begin_batch()
        db.update_file("a.py", "hash1", "python", 10, 100, [], [], [])
        # Before end_batch, check the data is available (within same connection)
        db.end_batch()
        stored = db.get_stored_files()
        assert "a.py" in stored


# ── get_stored_files ──────────────────────────────────────────────────────────

class TestGetStoredFiles:
    def test_empty_db_returns_empty_dict(self, db):
        result = db.get_stored_files()
        assert result == {}

    def test_stored_file_appears_in_result(self, db):
        db.update_file("mod.py", "abc123", "python", 10, 200, [], [], [])
        result = db.get_stored_files()
        assert "mod.py" in result

    def test_returns_hash_and_mtime(self, db):
        db.update_file("mod.py", "myhash", "python", 10, 200, [], [], [])
        result = db.get_stored_files()
        file_hash, mtime_ns = result["mod.py"]
        assert file_hash == "myhash"
        assert isinstance(mtime_ns, int)

    def test_multiple_files_returned(self, db):
        db.update_file("a.py", "h1", "python", 10, 100, [], [], [])
        db.update_file("b.py", "h2", "python", 5, 50, [], [], [])
        result = db.get_stored_files()
        assert len(result) == 2


# ── update_file_mtime ─────────────────────────────────────────────────────────

class TestUpdateFileMtime:
    def test_mtime_updated_correctly(self, db):
        db.update_file("mod.py", "h1", "python", 10, 100, [], [], [])
        db.update_file_mtime("mod.py", 1234567890)
        result = db.get_stored_files()
        _, mtime_ns = result["mod.py"]
        assert mtime_ns == 1234567890

    def test_unknown_file_does_not_crash(self, db):
        # Updating mtime of a file that doesn't exist should not raise
        db.update_file_mtime("nonexistent.py", 999)


# ── upsert_vectors_batch ──────────────────────────────────────────────────────

class TestUpsertVectorsBatch:
    def test_empty_batch_does_not_crash(self, db):
        db.upsert_vectors_batch([])

    def test_vectors_stored(self, db):
        # This may silently skip if sqlite-vec is not available
        items = [("a.py::fn", [0.1] * 384)]
        db.upsert_vectors_batch(items)  # should not raise


# ── graph_stats ───────────────────────────────────────────────────────────────

class TestGraphStats:
    def test_returns_dict(self, db):
        result = db.graph_stats()
        assert isinstance(result, dict)

    def test_has_expected_keys(self, db):
        result = db.graph_stats()
        assert "files" in result
        assert "symbols" in result
        assert "edges" in result
        assert "languages" in result

    def test_counts_match_stored(self, db):
        db.update_file("a.py", "h1", "python", 10, 100,
                       [_make_symbol("a.py", "fn")], [], [])
        result = db.graph_stats()
        assert result["files"] == 1
        assert result["symbols"] == 1

    def test_languages_breakdown(self, db):
        db.update_file("a.py", "h1", "python", 10, 100, [], [], [])
        result = db.graph_stats()
        assert "python" in result["languages"]


class TestVectorSearch:
    def test_init_vectors(self, db):
        result = db.init_vectors(dimensions=384)
        # May or may not work depending on sqlite-vec availability
        assert isinstance(result, bool)

    def test_hybrid_search_fts_only(self, db):
        """Hybrid search works with FTS only (no vectors)."""
        db.update_file("main.py", "h1", "python", 10, 100,
                       [_make_symbol("main.py", "process_data")], [], [])
        results = db.search_hybrid("process_data", query_embedding=None)
        assert len(results) > 0
        assert any("process_data" in sid for _, sid in results)
