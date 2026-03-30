"""Tests for S1041: Decomposition candidate advisory in render_focused.

S1041 fires when the focus seed is a function with F-grade complexity (cx >= 26)
and at most 5 unique cross-file non-test caller files.

The signal gives agents an actionable hint: the function is complex and can be
safely decomposed (0 cross-file callers) or with limited coordination (1-5 callers).

Distinct from:
- S1035 (orchestrator): triggers on callees/callers ratio, not complexity
- S65 (change_exposure): quantifies caller count as blast radius risk
- hotspots mode: identifies complex files/functions globally (not per-focus)
"""
from __future__ import annotations

import types
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sym(
    name: str,
    file_path: str = "src/utils.py",
    kind: str = "function",
    complexity: int = 1,
    exported: bool = True,
):
    from tempograph.types import Symbol, SymbolKind, Language

    kmap = {
        "function": SymbolKind.FUNCTION,
        "method": SymbolKind.METHOD,
        "class": SymbolKind.CLASS,
    }
    return Symbol(
        id=f"{file_path}::{name}",
        name=name,
        qualified_name=name,
        kind=kmap.get(kind, SymbolKind.FUNCTION),
        language=Language.PYTHON,
        file_path=file_path,
        line_start=1,
        line_end=10,
        exported=exported,
        complexity=complexity,
    )


def _make_graph(seed, cross_file_callers=None):
    """Build a minimal mock graph with _callers wired up."""
    g = types.SimpleNamespace()
    g.symbols = {seed.id: seed}
    g._callers = {seed.id: set()}

    if cross_file_callers:
        for c in cross_file_callers:
            g.symbols[c.id] = c
            g._callers.setdefault(seed.id, set()).add(c.id)

    # Stubs for other BFS methods (not used by decomp signal)
    g.callers_of = lambda sid: []
    g.callees_of = lambda sid: []
    g.children_of = lambda sid: []
    g.renderers_of = lambda sid: []
    g.subtypes_of = lambda name: []
    g.importers_of = lambda sid: []
    g.find_symbol = lambda name: [s for s in g.symbols.values() if s.name == name]
    g.hot_files = set()
    g.root = None

    return g


# ---------------------------------------------------------------------------
# Unit tests for _compute_decomp_candidate
# ---------------------------------------------------------------------------

from tempograph.render.focused import _compute_decomp_candidate


class TestDecompCandidateUnit:

    # ---- Fires correctly ----

    def test_fires_f_grade_no_callers(self):
        """F-grade function with no cross-file callers → fires with 'safe' note."""
        seed = _make_sym("heavy_fn", complexity=30)
        g = _make_graph(seed)
        result = _compute_decomp_candidate([seed], g)
        assert "complexity:" in result
        assert "cx=30" in result
        assert "F-grade" in result
        assert "safe to extract helpers" in result

    def test_fires_f_grade_few_callers(self):
        """F-grade function with 2 cross-file callers → fires with 'coordinate' note."""
        seed = _make_sym("big_fn", file_path="src/core.py", complexity=35)
        c1 = _make_sym("caller_a", file_path="src/api.py")
        c2 = _make_sym("caller_b", file_path="src/cli.py")
        g = _make_graph(seed, cross_file_callers=[c1, c2])
        result = _compute_decomp_candidate([seed], g)
        assert "cx=35" in result
        assert "F-grade" in result
        assert "2 caller files" in result
        assert "coordinate" in result

    def test_fires_at_exact_boundary_26(self):
        """cx=26 is F-grade boundary — should fire."""
        seed = _make_sym("boundary_fn", complexity=26)
        g = _make_graph(seed)
        result = _compute_decomp_candidate([seed], g)
        assert "cx=26" in result
        assert "F-grade" in result

    def test_fires_method(self):
        """Method kind triggers just like function."""
        seed = _make_sym("process", kind="method", complexity=28)
        g = _make_graph(seed)
        result = _compute_decomp_candidate([seed], g)
        assert "complexity:" in result

    def test_fires_with_same_file_callers_not_counted(self):
        """Same-file callers don't count toward cross-file threshold."""
        seed = _make_sym("big_fn", file_path="src/core.py", complexity=30)
        # All callers are in the SAME file — should still show "no cross-file callers"
        same_file_caller = _make_sym("internal", file_path="src/core.py")
        g = _make_graph(seed, cross_file_callers=[same_file_caller])
        # Manually add same-file caller
        g._callers[seed.id].add(same_file_caller.id)
        result = _compute_decomp_candidate([seed], g)
        assert "no cross-file callers" in result

    def test_output_is_arrow_format(self):
        """Signal output starts with ↳ prefix."""
        seed = _make_sym("fn", complexity=30)
        g = _make_graph(seed)
        result = _compute_decomp_candidate([seed], g)
        assert result.startswith("↳ complexity:")

    # ---- Silent correctly ----

    def test_silent_cx_below_threshold(self):
        """cx=25 (D-grade) — does not fire."""
        seed = _make_sym("ok_fn", complexity=25)
        g = _make_graph(seed)
        result = _compute_decomp_candidate([seed], g)
        assert result == ""

    def test_silent_cx_zero(self):
        """cx=0 — trivially silent."""
        seed = _make_sym("trivial", complexity=0)
        g = _make_graph(seed)
        result = _compute_decomp_candidate([seed], g)
        assert result == ""

    def test_silent_when_too_many_callers(self):
        """6 cross-file callers exceeds threshold — does not fire (too risky)."""
        seed = _make_sym("popular_fn", file_path="src/lib.py", complexity=40)
        callers = [
            _make_sym(f"c{i}", file_path=f"src/mod{i}.py") for i in range(6)
        ]
        g = _make_graph(seed, cross_file_callers=callers)
        result = _compute_decomp_candidate([seed], g)
        assert result == ""

    def test_silent_class_seed(self):
        """Class kind — does not fire (only for functions/methods)."""
        seed = _make_sym("BigClass", kind="class", complexity=40)
        g = _make_graph(seed)
        result = _compute_decomp_candidate([seed], g)
        assert result == ""

    def test_silent_test_file_seed(self):
        """Seed in test file — does not fire."""
        seed = _make_sym("test_big", file_path="tests/test_utils.py", complexity=30)
        g = _make_graph(seed)
        result = _compute_decomp_candidate([seed], g)
        assert result == ""

    def test_silent_empty_seeds(self):
        """Empty seeds list — does not fire."""
        g = _make_graph(_make_sym("x"))
        result = _compute_decomp_candidate([], g)
        assert result == ""

    def test_silent_cx_exactly_25(self):
        """cx=25 is E-grade boundary — does not trigger F-grade signal."""
        seed = _make_sym("e_grade", complexity=25)
        g = _make_graph(seed)
        assert _compute_decomp_candidate([seed], g) == ""

    def test_silent_test_callers_excluded(self):
        """Test file callers don't count toward cross-file caller limit."""
        seed = _make_sym("fn", file_path="src/core.py", complexity=30)
        # 6 test callers — should NOT exceed threshold (test files excluded)
        test_callers = [
            _make_sym(f"test_fn{i}", file_path=f"tests/test_mod{i}.py") for i in range(6)
        ]
        g = _make_graph(seed, cross_file_callers=test_callers)
        result = _compute_decomp_candidate([seed], g)
        # Should fire (0 non-test cross-file callers) not be silent
        assert "safe to extract helpers" in result

    # ---- Output format ----

    def test_shows_correct_single_caller_grammar(self):
        """1 caller file → singular form."""
        seed = _make_sym("fn", file_path="src/core.py", complexity=28)
        caller = _make_sym("user", file_path="src/api.py")
        g = _make_graph(seed, cross_file_callers=[caller])
        result = _compute_decomp_candidate([seed], g)
        assert "1 caller file;" in result

    def test_shows_correct_multiple_callers_grammar(self):
        """3 caller files → plural form."""
        seed = _make_sym("fn", file_path="src/core.py", complexity=28)
        callers = [_make_sym(f"c{i}", file_path=f"src/m{i}.py") for i in range(3)]
        g = _make_graph(seed, cross_file_callers=callers)
        result = _compute_decomp_candidate([seed], g)
        assert "3 caller files;" in result

    def test_boundary_exactly_5_callers_fires(self):
        """Exactly 5 unique caller files — just below threshold; fires."""
        seed = _make_sym("fn", file_path="src/core.py", complexity=28)
        callers = [_make_sym(f"c{i}", file_path=f"src/m{i}.py") for i in range(5)]
        g = _make_graph(seed, cross_file_callers=callers)
        result = _compute_decomp_candidate([seed], g)
        assert result != ""
        assert "5 caller files" in result

    def test_boundary_exactly_6_callers_silent(self):
        """Exactly 6 unique caller files — at threshold; does NOT fire."""
        seed = _make_sym("fn", file_path="src/core.py", complexity=28)
        callers = [_make_sym(f"c{i}", file_path=f"src/m{i}.py") for i in range(6)]
        g = _make_graph(seed, cross_file_callers=callers)
        result = _compute_decomp_candidate([seed], g)
        assert result == ""

    def test_deduplicates_caller_files(self):
        """Multiple callers from the same file count as ONE caller file."""
        seed = _make_sym("fn", file_path="src/core.py", complexity=30)
        # 3 symbols, all from same file "src/api.py"
        callers = [_make_sym(f"caller_{i}", file_path="src/api.py") for i in range(3)]
        g = _make_graph(seed, cross_file_callers=callers)
        result = _compute_decomp_candidate([seed], g)
        # Only 1 unique caller file → should show "1 caller file"
        assert "1 caller file;" in result


# ---------------------------------------------------------------------------
# Integration tests — on the real codebase
# ---------------------------------------------------------------------------

class TestDecompCandidateIntegration:
    """Integration tests: _compute_decomp_candidate on the real tempograph codebase."""

    @staticmethod
    def _focus(query: str) -> str:
        from tempograph import build_graph
        from tempograph.render.focused import render_focused
        g = build_graph(".")
        return render_focused(g, query)

    def test_fires_for_high_cx_hotspot_helper(self):
        """_signals_structure_b (cx=43, 0 cross callers) — fires."""
        result = self._focus("_signals_structure_b")
        assert "complexity:" in result
        assert "F-grade" in result
        assert "safe to extract helpers" in result

    def test_silent_for_decomposed_dispatcher(self):
        """Decomposed dispatchers (cx=1) — do not fire F-grade signal."""
        for fn in (
            "_signals_hotspots_core_b_structure",
            "_signals_hotspots_core_b_type",
            "_signals_hotspots_core_b_concentration",
        ):
            result = self._focus(fn)
            assert "F-grade" not in result, f"{fn} should not fire F-grade after decomposition"

    def test_fires_for_dead_insights_helper(self):
        """_render_dead_insights_b (dead.py, cx=29, 1 caller file ≤5) — fires with safe-to-extract note."""
        result = self._focus("_render_dead_insights_b")
        assert "complexity:" in result
        assert "F-grade" in result
        assert "safe to extract helpers" in result

    def test_silent_for_simple_function(self):
        """A simple utility function (cx << 26) — does not fire."""
        result = self._focus("_is_test_file")
        assert "complexity:" not in result or "F-grade" not in result

    def test_silent_for_render_focused_high_callers(self):
        """render_focused has cx=33 (F-grade) but 6 non-test caller files (>5 threshold) — does NOT fire."""
        result = self._focus("render_focused")
        # render_focused: cx=33 >= 26 (would qualify), but 6 non-test cross-file callers > _MAX_CALLERS=5
        # Signal must remain silent — high blast radius suppresses the advisory
        assert "F-grade" not in result
