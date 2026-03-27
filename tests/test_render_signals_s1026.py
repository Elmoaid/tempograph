"""Tests for S1026: semantic drift — read-named hotspot calls write-named callees."""
from __future__ import annotations

from unittest.mock import MagicMock

from tempograph.render.hotspots import _collect_hotspots_signals
from tempograph.types import Language, Symbol, SymbolKind


def _make_sym(sym_id: str, file_path: str, name: str | None = None) -> Symbol:
    sname = name or sym_id.split("::")[-1]
    return Symbol(
        id=sym_id,
        name=sname,
        qualified_name=sname,
        kind=SymbolKind.FUNCTION,
        language=Language.PYTHON,
        file_path=file_path,
        line_start=1,
        line_end=20,
    )


def _safe_graph(symbols: list[Symbol], callees_map: dict[str, list[Symbol]] | None = None) -> MagicMock:
    graph = MagicMock()
    graph.hot_files = set()
    graph.importers_of.return_value = []
    graph.files.get.return_value = None
    graph.symbols = {s.id: s for s in symbols}
    graph.callers_of.return_value = []
    graph.root = None

    def _callees_of(sym_id: str) -> list[Symbol]:
        return (callees_map or {}).get(sym_id, [])

    graph.callees_of.side_effect = _callees_of
    return graph


class TestSemanticDriftS1026:
    """S1026: semantic drift fires when a read-named hotspot calls write-named callees."""

    def test_fires_for_get_function_calling_two_write_ops(self):
        """get_config calling save_config + update_state triggers semantic drift."""
        get_fn = _make_sym("api.py::get_config", "api.py")
        save_fn = _make_sym("db.py::save_config", "db.py")
        update_fn = _make_sym("db.py::update_state", "db.py")
        scores = [(200.0, get_fn)]
        graph = _safe_graph(
            [get_fn, save_fn, update_fn],
            callees_map={"api.py::get_config": [save_fn, update_fn]},
        )
        result = _collect_hotspots_signals(graph, scores, {}, {}, set(), 20)
        combined = "\n".join(result)
        assert "semantic drift" in combined
        assert "get_config" in combined

    def test_fires_for_fetch_prefix_calling_two_write_ops(self):
        """fetch_user calling delete_session + update_last_seen triggers drift."""
        fetch_fn = _make_sym("users.py::fetch_user", "users.py")
        delete_fn = _make_sym("session.py::delete_session", "session.py")
        update_fn = _make_sym("session.py::update_last_seen", "session.py")
        scores = [(180.0, fetch_fn)]
        graph = _safe_graph(
            [fetch_fn, delete_fn, update_fn],
            callees_map={"users.py::fetch_user": [delete_fn, update_fn]},
        )
        result = _collect_hotspots_signals(graph, scores, {}, {}, set(), 20)
        combined = "\n".join(result)
        assert "semantic drift" in combined

    def test_suppressed_when_only_one_write_callee(self):
        """A read-named function with only ONE write callee does not trigger (threshold=2)."""
        get_fn = _make_sym("api.py::get_data", "api.py")
        save_fn = _make_sym("db.py::save_record", "db.py")
        read_fn = _make_sym("db.py::read_cache", "db.py")
        scores = [(200.0, get_fn)]
        graph = _safe_graph(
            [get_fn, save_fn, read_fn],
            callees_map={"api.py::get_data": [save_fn, read_fn]},
        )
        result = _collect_hotspots_signals(graph, scores, {}, {}, set(), 20)
        combined = "\n".join(result)
        assert "semantic drift" not in combined

    def test_suppressed_for_write_named_function(self):
        """A write-named function (update_*, save_*) calling write ops is not drift."""
        update_fn = _make_sym("api.py::update_user", "api.py")
        save_fn = _make_sym("db.py::save_record", "db.py")
        persist_fn = _make_sym("db.py::persist_changes", "db.py")
        scores = [(200.0, update_fn)]
        graph = _safe_graph(
            [update_fn, save_fn, persist_fn],
            callees_map={"api.py::update_user": [save_fn, persist_fn]},
        )
        result = _collect_hotspots_signals(graph, scores, {}, {}, set(), 20)
        combined = "\n".join(result)
        assert "semantic drift" not in combined

    def test_suppressed_for_test_file_symbol(self):
        """A symbol in a test file does not trigger semantic drift even if naming matches."""
        get_fn = _make_sym("tests/test_api.py::get_fixture", "tests/test_api.py")
        save_fn = _make_sym("tests/test_api.py::save_mock", "tests/test_api.py")
        delete_fn = _make_sym("tests/test_api.py::delete_fixture", "tests/test_api.py")
        scores = [(200.0, get_fn)]
        graph = _safe_graph(
            [get_fn, save_fn, delete_fn],
            callees_map={"tests/test_api.py::get_fixture": [save_fn, delete_fn]},
        )
        result = _collect_hotspots_signals(graph, scores, {}, {}, set(), 20)
        combined = "\n".join(result)
        assert "semantic drift" not in combined

    def test_fires_for_is_prefix_calling_write_ops(self):
        """is_* prefix is read-named — calling two write ops triggers drift."""
        is_fn = _make_sym("auth.py::is_valid", "auth.py")
        insert_fn = _make_sym("db.py::insert_token", "db.py")
        update_fn = _make_sym("db.py::update_expiry", "db.py")
        scores = [(150.0, is_fn)]
        graph = _safe_graph(
            [is_fn, insert_fn, update_fn],
            callees_map={"auth.py::is_valid": [insert_fn, update_fn]},
        )
        result = _collect_hotspots_signals(graph, scores, {}, {}, set(), 20)
        combined = "\n".join(result)
        assert "semantic drift" in combined

    def test_empty_scores_no_crash(self):
        """Empty scores list does not crash."""
        graph = _safe_graph([])
        result = _collect_hotspots_signals(graph, [], {}, {}, set(), 20)
        assert isinstance(result, list)

    def test_no_drift_when_all_callees_are_read_ops(self):
        """A read-named function calling only read callees has no semantic drift."""
        get_fn = _make_sym("api.py::get_user", "api.py")
        find_fn = _make_sym("db.py::find_record", "db.py")
        load_fn = _make_sym("db.py::load_profile", "db.py")
        scores = [(200.0, get_fn)]
        graph = _safe_graph(
            [get_fn, find_fn, load_fn],
            callees_map={"api.py::get_user": [find_fn, load_fn]},
        )
        result = _collect_hotspots_signals(graph, scores, {}, {}, set(), 20)
        combined = "\n".join(result)
        assert "semantic drift" not in combined
