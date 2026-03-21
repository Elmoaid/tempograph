"""Tests for callsite line annotations in focus mode callers section (S20)."""

from unittest.mock import patch

import pytest

from tempograph.types import Edge, EdgeKind, FileInfo, Language, Symbol, SymbolKind, Tempo


def _make_graph(tmp_path, edges, symbols=None):
    """Build a minimal Tempo graph with explicit symbols and edges."""
    root = str(tmp_path)
    if symbols is None:
        symbols = []

    graph = Tempo(root=root)
    for sym in symbols:
        graph.symbols[sym.id] = sym
        graph.files.setdefault(
            sym.file_path,
            FileInfo(path=sym.file_path, language=sym.language, line_count=100, byte_size=5000, symbols=[]),
        ).symbols.append(sym.id)
    graph.edges = list(edges)
    graph.build_indexes()
    return graph


def _target_sym():
    return Symbol(
        id="target.py::process_data",
        name="process_data",
        qualified_name="process_data",
        kind=SymbolKind.FUNCTION,
        language=Language.PYTHON,
        file_path="target.py",
        line_start=10,
        line_end=30,
        exported=True,
    )


def _caller_sym(name="do_work", file_path="caller.py", line_start=1, line_end=50):
    return Symbol(
        id=f"{file_path}::{name}",
        name=name,
        qualified_name=name,
        kind=SymbolKind.FUNCTION,
        language=Language.PYTHON,
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        exported=True,
    )


class TestFocusCallsiteLines:
    """Verify callsite line annotations in focus mode callers section."""

    def test_single_callsite_shows_line(self, tmp_path):
        """One caller with one callsite shows [line N]."""
        from tempograph.render import render_focused

        target = _target_sym()
        caller = _caller_sym()
        edges = [Edge(EdgeKind.CALLS, caller.id, target.id, line=45)]
        graph = _make_graph(tmp_path, edges, [target, caller])

        with patch("tempograph.git.file_last_modified_days", return_value=5):
            output = render_focused(graph, "process_data")

        assert "[line 45]" in output

    def test_multiple_callsites_shows_two_lines(self, tmp_path):
        """One caller with multiple callsites shows [lines N, M] (lowest two)."""
        from tempograph.render import render_focused

        target = _target_sym()
        caller = _caller_sym()
        edges = [
            Edge(EdgeKind.CALLS, caller.id, target.id, line=23),
            Edge(EdgeKind.CALLS, caller.id, target.id, line=67),
            Edge(EdgeKind.CALLS, caller.id, target.id, line=99),
        ]
        graph = _make_graph(tmp_path, edges, [target, caller])

        with patch("tempograph.git.file_last_modified_days", return_value=5):
            output = render_focused(graph, "process_data")

        assert "[lines 23, 67]" in output

    def test_zero_line_not_shown(self, tmp_path):
        """When edge.line == 0, no bracket annotation appears."""
        from tempograph.render import render_focused

        target = _target_sym()
        caller = _caller_sym()
        edges = [Edge(EdgeKind.CALLS, caller.id, target.id, line=0)]
        graph = _make_graph(tmp_path, edges, [target, caller])

        with patch("tempograph.git.file_last_modified_days", return_value=5):
            output = render_focused(graph, "process_data")

        assert "[line" not in output
        # Caller should still appear
        assert "do_work" in output

    def test_callsite_lines_in_output(self, tmp_path):
        """Integration: build graph, run render_focused, assert [line appears."""
        from tempograph.render import render_focused

        target = _target_sym()
        caller_a = _caller_sym("validate_user", "auth.py")
        caller_b = _caller_sym("handle_request", "api.py")
        edges = [
            Edge(EdgeKind.CALLS, caller_a.id, target.id, line=45),
            Edge(EdgeKind.CALLS, caller_b.id, target.id, line=23),
            Edge(EdgeKind.CALLS, caller_b.id, target.id, line=67),
        ]
        graph = _make_graph(tmp_path, edges, [target, caller_a, caller_b])

        with patch("tempograph.git.file_last_modified_days", return_value=5):
            output = render_focused(graph, "process_data")

        assert "[line 45]" in output
        assert "[lines 23, 67]" in output
