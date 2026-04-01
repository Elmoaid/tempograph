"""Tests for S1047: Narrow test coverage signal.

Fires when a widely-called function (≥8 production callers) is validated by
exactly 1 test file. One test file gives false confidence while the majority
of real-world calling patterns go unvalidated.
"""
import pytest
from tempograph.types import Symbol, SymbolKind, Language


def _sym(sym_id, name, file_path, kind=SymbolKind.FUNCTION):
    return Symbol(
        id=sym_id, name=name, qualified_name=name,
        kind=kind, language=Language.PYTHON,
        file_path=file_path, line_start=1, line_end=10,
    )


def _test_sym(sym_id, name, file_path):
    return _sym(sym_id, name, file_path)


class _Graph:
    def __init__(self, caller_map=None):
        self._caller_map = caller_map or {}

    def callers_of(self, sid):
        return self._caller_map.get(sid, [])


def _make_callers(prod_files, test_files, seed_file="src/lib.py"):
    """Build a list of caller symbols from file lists."""
    callers = []
    for i, fp in enumerate(prod_files):
        callers.append(_sym(f"{fp}::caller_{i}", f"caller_{i}", fp))
    for i, fp in enumerate(test_files):
        callers.append(_sym(f"{fp}::test_{i}", f"test_{i}", fp))
    return callers


# ---------------------------------------------------------------------------
# FIRE cases
# ---------------------------------------------------------------------------

class TestNarrowTestCoverageFires:
    def test_fires_minimum_threshold(self):
        """Exactly 8 prod callers + 1 test file → fires."""
        from tempograph.render.focused import _compute_narrow_test_coverage
        seed = _sym("src/lib.py::parse", "parse", "src/lib.py")
        callers = _make_callers(
            [f"src/mod_{i}.py" for i in range(8)],
            ["tests/test_parse.py"],
        )
        g = _Graph({"src/lib.py::parse": callers})
        result = _compute_narrow_test_coverage([seed], g)
        assert "narrow test" in result
        assert "8" in result
        assert "test_parse.py" in result

    def test_fires_many_callers_one_test_file(self):
        """81 prod callers + 1 test file → fires (mirrors _node_text pattern)."""
        from tempograph.render.focused import _compute_narrow_test_coverage
        seed = _sym("src/utils.py::_node_text", "_node_text", "src/utils.py")
        callers = _make_callers(
            [f"src/handlers/h_{i}.py" for i in range(81)],
            ["tests/test_parser_internals.py"],
        )
        g = _Graph({"src/utils.py::_node_text": callers})
        result = _compute_narrow_test_coverage([seed], g)
        assert "narrow test" in result
        assert "81" in result
        assert "test_parser_internals.py" in result
        assert "safety net is thin" in result

    def test_fires_for_method_kind(self):
        """Method kind (not just function) fires."""
        from tempograph.render.focused import _compute_narrow_test_coverage
        seed = _sym("src/parser.py::Parser.parse", "parse", "src/parser.py",
                    kind=SymbolKind.METHOD)
        callers = _make_callers(
            [f"src/client_{i}.py" for i in range(9)],
            ["tests/test_parser.py"],
        )
        g = _Graph({"src/parser.py::Parser.parse": callers})
        result = _compute_narrow_test_coverage([seed], g)
        assert "narrow test" in result

    def test_message_format_includes_caller_count(self):
        """Output includes production caller count."""
        from tempograph.render.focused import _compute_narrow_test_coverage
        seed = _sym("lib.py::render", "render", "lib.py")
        callers = _make_callers(
            [f"caller_{i}.py" for i in range(12)],
            ["tests/test_render.py"],
        )
        g = _Graph({"lib.py::render": callers})
        result = _compute_narrow_test_coverage([seed], g)
        assert "12" in result

    def test_same_file_callers_excluded_from_threshold(self):
        """Same-file callers do NOT count toward the 8-caller threshold."""
        from tempograph.render.focused import _compute_narrow_test_coverage
        seed = _sym("src/lib.py::helper", "helper", "src/lib.py")
        # 5 cross-file prod + 6 same-file = 11 total callers, but only 5 count
        cross_callers = [_sym(f"src/mod_{i}.py::fn", f"fn_{i}", f"src/mod_{i}.py") for i in range(5)]
        same_file_callers = [_sym(f"src/lib.py::fn_{i}", f"fn_{i}", "src/lib.py") for i in range(6)]
        test_callers = [_sym("tests/test_lib.py::t", "t", "tests/test_lib.py")]
        g = _Graph({"src/lib.py::helper": cross_callers + same_file_callers + test_callers})
        result = _compute_narrow_test_coverage([seed], g)
        # Only 5 cross-file prod callers → below threshold → SILENT
        assert result == ""

    def test_fires_shows_test_file_basename(self):
        """Test file name shows as basename only (no path)."""
        from tempograph.render.focused import _compute_narrow_test_coverage
        seed = _sym("src/core.py::process", "process", "src/core.py")
        callers = _make_callers(
            [f"src/sub/mod_{i}.py" for i in range(10)],
            ["tests/unit/test_core_unit.py"],
        )
        g = _Graph({"src/core.py::process": callers})
        result = _compute_narrow_test_coverage([seed], g)
        assert "test_core_unit.py" in result
        assert "tests/unit/" not in result  # No path, just basename


# ---------------------------------------------------------------------------
# SILENT cases
# ---------------------------------------------------------------------------

class TestNarrowTestCoverageSilent:
    def test_silent_empty_seeds(self):
        """Empty seeds → silent."""
        from tempograph.render.focused import _compute_narrow_test_coverage
        assert _compute_narrow_test_coverage([], _Graph()) == ""

    def test_silent_below_threshold(self):
        """7 prod callers (< 8) with 1 test file → silent."""
        from tempograph.render.focused import _compute_narrow_test_coverage
        seed = _sym("src/lib.py::fn", "fn", "src/lib.py")
        callers = _make_callers(
            [f"src/m_{i}.py" for i in range(7)],
            ["tests/test_fn.py"],
        )
        g = _Graph({"src/lib.py::fn": callers})
        result = _compute_narrow_test_coverage([seed], g)
        assert result == ""

    def test_silent_two_test_files(self):
        """8+ prod callers with 2 test files → silent (reasonable coverage spread)."""
        from tempograph.render.focused import _compute_narrow_test_coverage
        seed = _sym("src/lib.py::fn", "fn", "src/lib.py")
        callers = _make_callers(
            [f"src/m_{i}.py" for i in range(10)],
            ["tests/test_a.py", "tests/test_b.py"],
        )
        g = _Graph({"src/lib.py::fn": callers})
        result = _compute_narrow_test_coverage([seed], g)
        assert result == ""

    def test_silent_zero_test_files(self):
        """8+ prod callers but 0 test files → silent (handled by 'Tests: none' elsewhere)."""
        from tempograph.render.focused import _compute_narrow_test_coverage
        seed = _sym("src/lib.py::fn", "fn", "src/lib.py")
        callers = _make_callers(
            [f"src/m_{i}.py" for i in range(10)],
            [],
        )
        g = _Graph({"src/lib.py::fn": callers})
        result = _compute_narrow_test_coverage([seed], g)
        assert result == ""

    def test_silent_test_file_seed(self):
        """Seed is in a test file → silent."""
        from tempograph.render.focused import _compute_narrow_test_coverage
        seed = _sym("tests/test_lib.py::helper", "helper", "tests/test_lib.py")
        callers = _make_callers(
            [f"src/m_{i}.py" for i in range(10)],
            ["tests/test_b.py"],
        )
        g = _Graph({"tests/test_lib.py::helper": callers})
        result = _compute_narrow_test_coverage([seed], g)
        assert result == ""

    def test_silent_class_kind(self):
        """Class kind (not function/method) → silent."""
        from tempograph.render.focused import _compute_narrow_test_coverage
        seed = _sym("src/lib.py::Parser", "Parser", "src/lib.py",
                    kind=SymbolKind.CLASS)
        callers = _make_callers(
            [f"src/m_{i}.py" for i in range(10)],
            ["tests/test_lib.py"],
        )
        g = _Graph({"src/lib.py::Parser": callers})
        result = _compute_narrow_test_coverage([seed], g)
        assert result == ""

    def test_silent_test_callers_excluded_from_prod_count(self):
        """Test callers don't count toward the ≥8 production threshold."""
        from tempograph.render.focused import _compute_narrow_test_coverage
        seed = _sym("src/lib.py::fn", "fn", "src/lib.py")
        # 3 prod + 10 test callers = 13 total, but only 3 count
        prod_callers = [_sym(f"src/m_{i}.py::f", f"f{i}", f"src/m_{i}.py") for i in range(3)]
        test_callers = [_sym(f"tests/t_{i}.py::t", f"t{i}", f"tests/t_{i}.py") for i in range(10)]
        g = _Graph({"src/lib.py::fn": prod_callers + test_callers})
        result = _compute_narrow_test_coverage([seed], g)
        assert result == ""


# ---------------------------------------------------------------------------
# Integration tests — real codebase evidence
# ---------------------------------------------------------------------------

class TestNarrowTestCoverageIntegration:
    @pytest.fixture(scope="class")
    def graph(self):
        from tempograph import build_graph
        return build_graph(".")

    def test_fires_for_node_text(self, graph):
        """_node_text in lang/_utils.py: 81 prod callers, 1 test file → fires."""
        from tempograph.render.focused import _compute_narrow_test_coverage
        syms = [
            s for s in graph.symbols.values()
            if s.name == "_node_text" and "_utils" in s.file_path
        ]
        assert syms, "_node_text not found in graph"
        result = _compute_narrow_test_coverage(syms[:1], graph)
        assert "narrow test" in result, f"Expected narrow test signal, got: {result!r}"
        assert "test_parser_internals.py" in result

    def test_fires_for_extract_signature(self, graph):
        """_extract_signature in lang/_utils.py: 27 prod callers, 1 test file → fires."""
        from tempograph.render.focused import _compute_narrow_test_coverage
        syms = [
            s for s in graph.symbols.values()
            if s.name == "_extract_signature" and "_utils" in s.file_path
        ]
        assert syms, "_extract_signature not found in graph"
        result = _compute_narrow_test_coverage(syms[:1], graph)
        assert "narrow test" in result, f"Expected narrow test signal, got: {result!r}"

    def test_silent_for_build_graph(self, graph):
        """build_graph has 37 test files → silent."""
        from tempograph.render.focused import _compute_narrow_test_coverage
        syms = [
            s for s in graph.symbols.values()
            if s.name == "build_graph" and "builder" in s.file_path
        ]
        assert syms, "build_graph not found in graph"
        result = _compute_narrow_test_coverage(syms[:1], graph)
        assert result == "", f"Expected silent for build_graph (many test files), got: {result!r}"

    def test_silent_for_render_focused(self, graph):
        """render_focused has 16 test files → silent."""
        from tempograph.render.focused import _compute_narrow_test_coverage
        syms = [
            s for s in graph.symbols.values()
            if s.name == "render_focused" and "focused" in s.file_path
            and "signals" not in s.file_path
        ]
        assert syms, "render_focused not found in graph"
        result = _compute_narrow_test_coverage(syms[:1], graph)
        assert result == "", f"Expected silent for render_focused (many test files), got: {result!r}"
