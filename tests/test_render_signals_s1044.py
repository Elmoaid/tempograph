"""Tests for S1044: Call cycle signal in render_focused.

S1044 fires when a focus seed participates in an indirect 3-hop call cycle
(A→B→C→A) that is NOT detected by the existing inline mutual recursion
annotation (which only catches 2-hop A→B→A at the BFS node level).

Conditions to fire:
- kind in {function, method}
- not a test file
- seed calls B (B ≠ seed, B not test file)
- B calls C (C ≠ seed, C ≠ B, direct mutual B→A already skipped)
- C calls back to seed (C→A)

Distinct from:
- [recursive] annotation: self-recursion (A calls A directly), inline per-node
- [recursive: mutual with X]: direct 2-hop (A→B→A), inline per-node
This signal: indirect 3-hop cycle, PRE-BFS placement, invisible to inline annotations.
"""
from __future__ import annotations

import types
import pytest
from tempograph.types import Symbol, SymbolKind, Language


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sym(
    name: str,
    file_path: str = "src/utils.py",
    kind: str = "function",
    language: str = "python",
    line_start: int = 1,
    line_end: int = 10,
) -> Symbol:
    kmap = {
        "function": SymbolKind.FUNCTION,
        "method": SymbolKind.METHOD,
        "class": SymbolKind.CLASS,
    }
    lmap = {
        "python": Language.PYTHON,
        "typescript": Language.TYPESCRIPT,
        "tsx": Language.TSX,
    }
    return Symbol(
        id=f"{file_path}::{name}",
        name=name,
        qualified_name=name,
        kind=kmap.get(kind, SymbolKind.FUNCTION),
        language=lmap.get(language, Language.PYTHON),
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        signature=f"def {name}():",
        complexity=5,
    )


def _make_graph(symbols: list[Symbol], callees: dict[str, list[str]]) -> types.SimpleNamespace:
    """Build minimal mock graph with callees_of support.

    callees: {sym_id: [callee_sym_id, ...]}
    """
    sym_map = {s.id: s for s in symbols}
    g = types.SimpleNamespace()
    g.symbols = sym_map
    g.root = "/tmp/test_repo"
    g.hot_files = set()
    g.edges = []
    g._callers: dict[str, list[str]] = {}

    def callees_of(sym_id: str) -> list[Symbol]:
        return [sym_map[cid] for cid in callees.get(sym_id, []) if cid in sym_map]

    def callers_of(sym_id: str) -> list[Symbol]:
        return [sym_map[cid] for cid in g._callers.get(sym_id, []) if cid in sym_map]

    g.callees_of = callees_of
    g.callers_of = callers_of
    return g


from tempograph.render.focused import _compute_call_cycle


# ---------------------------------------------------------------------------
# Unit tests: _compute_call_cycle — FIRES
# ---------------------------------------------------------------------------


class TestCallCycleUnit:

    def test_fires_simple_3hop_cycle(self):
        """A→B→C→A: 3-hop cycle, all functions — fires."""
        a = _make_sym("func_a", "src/mod.py")
        b = _make_sym("func_b", "src/mod.py")
        c = _make_sym("func_c", "src/mod.py")
        g = _make_graph([a, b, c], {
            a.id: [b.id],        # A calls B
            b.id: [c.id],        # B calls C
            c.id: [a.id],        # C calls back to A
        })
        result = _compute_call_cycle([a], g)
        assert "call cycle" in result
        assert "func_a" in result
        assert "func_b" in result
        assert "func_c" in result
        assert "3-hop" in result

    def test_fires_method_kind(self):
        """3-hop cycle with method kind — fires."""
        a = _make_sym("method_a", "src/cls.py", kind="method")
        b = _make_sym("method_b", "src/cls.py", kind="method")
        c = _make_sym("method_c", "src/cls.py", kind="method")
        g = _make_graph([a, b, c], {
            a.id: [b.id],
            b.id: [c.id],
            c.id: [a.id],
        })
        result = _compute_call_cycle([a], g)
        assert "call cycle" in result
        assert "method_a" in result

    def test_fires_cross_file_3hop(self):
        """3-hop cycle spanning different files — fires."""
        a = _make_sym("parse_entry", "src/parser.py")
        b = _make_sym("compute_score", "src/scorer.py")
        c = _make_sym("walk_tree", "src/walker.py")
        g = _make_graph([a, b, c], {
            a.id: [b.id],
            b.id: [c.id],
            c.id: [a.id],
        })
        result = _compute_call_cycle([a], g)
        assert "call cycle" in result
        assert "parse_entry" in result
        assert "compute_score" in result
        assert "walk_tree" in result

    def test_fires_cycle_detected_from_any_node(self):
        """All 3 nodes in the cycle should trigger the warning when focused."""
        a = _make_sym("node_a", "src/chain.py")
        b = _make_sym("node_b", "src/chain.py")
        c = _make_sym("node_c", "src/chain.py")
        g = _make_graph([a, b, c], {
            a.id: [b.id],
            b.id: [c.id],
            c.id: [a.id],
        })
        # Each node, when focused, should reveal the cycle
        for seed in [a, b, c]:
            result = _compute_call_cycle([seed], g)
            assert "call cycle" in result, f"Expected cycle warning for seed={seed.name}"

    def test_fires_with_extra_callees(self):
        """Cycle node also has non-cycle callees — still fires."""
        a = _make_sym("func_a", "src/mod.py")
        b = _make_sym("func_b", "src/mod.py")
        c = _make_sym("func_c", "src/mod.py")
        noise1 = _make_sym("helper_x", "src/mod.py")
        noise2 = _make_sym("helper_y", "src/mod.py")
        g = _make_graph([a, b, c, noise1, noise2], {
            a.id: [noise1.id, b.id, noise2.id],  # A has extra callees too
            b.id: [c.id],
            c.id: [a.id],
        })
        result = _compute_call_cycle([a], g)
        assert "call cycle" in result
        assert "func_a" in result

    def test_fires_multi_seed_one_in_cycle(self):
        """Two seeds: one in cycle, one not — cycle seed reported."""
        a = _make_sym("in_cycle", "src/mod.py")
        b = _make_sym("mid_fn", "src/mod.py")
        c = _make_sym("far_fn", "src/mod.py")
        clean = _make_sym("no_cycle", "src/mod.py")
        g = _make_graph([a, b, c, clean], {
            a.id: [b.id],
            b.id: [c.id],
            c.id: [a.id],
            clean.id: [],
        })
        result = _compute_call_cycle([a, clean], g)
        assert "call cycle" in result
        assert "in_cycle" in result

    def test_shows_arrow_path_format(self):
        """Output uses A → B → C → A format."""
        a = _make_sym("alpha", "src/mod.py")
        b = _make_sym("beta", "src/mod.py")
        c = _make_sym("gamma", "src/mod.py")
        g = _make_graph([a, b, c], {
            a.id: [b.id],
            b.id: [c.id],
            c.id: [a.id],
        })
        result = _compute_call_cycle([a], g)
        assert "→" in result
        assert "alpha" in result


# ---------------------------------------------------------------------------
# Unit tests: _compute_call_cycle — SILENT
# ---------------------------------------------------------------------------


class TestCallCycleSilent:

    def test_silent_no_cycle(self):
        """Simple linear call chain A→B→C (no cycle back) — silent."""
        a = _make_sym("func_a", "src/mod.py")
        b = _make_sym("func_b", "src/mod.py")
        c = _make_sym("func_c", "src/mod.py")
        g = _make_graph([a, b, c], {
            a.id: [b.id],
            b.id: [c.id],
            c.id: [],
        })
        result = _compute_call_cycle([a], g)
        assert result == ""

    def test_silent_direct_2hop_mutual(self):
        """A→B→A (2-hop direct mutual): already handled by inline annotation — silent."""
        a = _make_sym("func_a", "src/mod.py")
        b = _make_sym("func_b", "src/mod.py")
        g = _make_graph([a, b], {
            a.id: [b.id],
            b.id: [a.id],  # direct mutual — this is the A→B→A case
        })
        result = _compute_call_cycle([a], g)
        assert result == ""

    def test_silent_self_recursive(self):
        """A calls itself (self-recursion): [recursive] handles this — silent."""
        a = _make_sym("func_a", "src/mod.py")
        g = _make_graph([a], {a.id: [a.id]})
        result = _compute_call_cycle([a], g)
        assert result == ""

    def test_silent_test_file_seed(self):
        """Seed in a test file — signal skips test files."""
        a = _make_sym("test_func_a", "tests/test_mod.py")
        b = _make_sym("func_b", "tests/test_mod.py")
        c = _make_sym("func_c", "tests/test_mod.py")
        g = _make_graph([a, b, c], {
            a.id: [b.id],
            b.id: [c.id],
            c.id: [a.id],
        })
        result = _compute_call_cycle([a], g)
        assert result == ""

    def test_silent_test_file_mid_node(self):
        """Middle node in a cycle is a test file — mid excluded, cycle broken."""
        a = _make_sym("func_a", "src/mod.py")
        b = _make_sym("helper_b", "tests/test_utils.py")  # test file
        c = _make_sym("func_c", "src/mod.py")
        g = _make_graph([a, b, c], {
            a.id: [b.id],
            b.id: [c.id],
            c.id: [a.id],
        })
        result = _compute_call_cycle([a], g)
        assert result == ""

    def test_silent_class_kind(self):
        """Class seed (not function/method) — signal only fires for function/method."""
        a = _make_sym("ClassA", "src/mod.py", kind="class")
        b = _make_sym("func_b", "src/mod.py")
        c = _make_sym("func_c", "src/mod.py")
        g = _make_graph([a, b, c], {
            a.id: [b.id],
            b.id: [c.id],
            c.id: [a.id],
        })
        result = _compute_call_cycle([a], g)
        assert result == ""

    def test_silent_no_callees(self):
        """Seed with no callees — trivially no cycle."""
        a = _make_sym("leaf_fn", "src/mod.py")
        g = _make_graph([a], {a.id: []})
        result = _compute_call_cycle([a], g)
        assert result == ""

    def test_silent_empty_seeds(self):
        """Empty seed list — trivially no cycle."""
        g = _make_graph([], {})
        result = _compute_call_cycle([], g)
        assert result == ""

    def test_silent_multi_seed_none_in_cycle(self):
        """Multiple seeds, none in a cycle — silent."""
        a = _make_sym("func_a", "src/mod.py")
        b = _make_sym("func_b", "src/mod.py")
        c = _make_sym("func_c", "src/mod.py")
        g = _make_graph([a, b, c], {
            a.id: [b.id],
            b.id: [c.id],
            c.id: [],
        })
        result = _compute_call_cycle([a, b, c], g)
        assert result == ""


# ---------------------------------------------------------------------------
# Integration test: real tempograph graph
# ---------------------------------------------------------------------------


class TestCallCycleIntegration:

    @pytest.fixture(scope="class")
    def graph(self, tmp_path_factory):
        from tempograph import build_graph
        import os
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return build_graph(repo_root)

    def test_fires_for_walk_parser(self, graph):
        """_walk in parser.py: participates in _walk→_handle_generic→_compute_complexity→_walk.

        This 3-hop cycle is invisible to the existing [recursive] annotation because:
        - _walk doesn't directly call _compute_complexity
        - The cycle spans 3 hops via _handle_generic

        Before S1044: focusing on _walk shows no cycle warning in pre-BFS section.
        After S1044: call cycle warning fires upfront.
        """
        seeds = [
            s for s in graph.symbols.values()
            if s.name == "_walk" and "parser.py" in s.file_path
        ]
        assert seeds, "_walk not found in parser.py"
        result = _compute_call_cycle(seeds, graph)
        assert "call cycle" in result, (
            f"Expected call cycle warning for _walk. Got: {result!r}"
        )
        assert "_walk" in result
        assert "→" in result

    def test_fires_for_handle_generic_parser(self, graph):
        """_handle_generic in parser.py: same cycle, different entry node."""
        seeds = [
            s for s in graph.symbols.values()
            if s.name == "_handle_generic" and "parser.py" in s.file_path
        ]
        assert seeds, "_handle_generic not found in parser.py"
        result = _compute_call_cycle(seeds, graph)
        # _handle_generic is self-recursive AND in the 3-hop cycle
        # The 3-hop cycle: _handle_generic → _compute_complexity → _walk → _handle_generic
        assert "call cycle" in result

    def test_silent_for_direct_mutual_search_hybrid(self, graph):
        """_search_hybrid in types.py: 2-hop direct mutual with search_symbols_scored.

        This is ALREADY annotated inline as [recursive: mutual with search_symbols_scored].
        S1044 should NOT fire for 2-hop mutual recursion — avoid double-reporting.
        """
        seeds = [
            s for s in graph.symbols.values()
            if s.name == "_search_hybrid" and "types.py" in s.file_path
        ]
        assert seeds, "_search_hybrid not found in types.py"
        result = _compute_call_cycle(seeds, graph)
        assert result == "", (
            f"Expected no call cycle warning for _search_hybrid (2-hop, already inline). Got: {result!r}"
        )

    def test_silent_for_linear_function(self, graph):
        """A simple non-recursive function should not fire."""
        seeds = [
            s for s in graph.symbols.values()
            if s.name == "build_graph" and "builder.py" in s.file_path
        ]
        assert seeds, "build_graph not found in builder.py"
        result = _compute_call_cycle(seeds, graph)
        assert result == "", (
            f"Expected no call cycle warning for build_graph. Got: {result!r}"
        )
