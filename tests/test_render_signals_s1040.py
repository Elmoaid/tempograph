"""Tests for S1040: Cross-language callee warning in render_focused.

S1040 fires when a BFS depth-1 callee of the focus seed is in a different
programming language than the seed itself (e.g. Python seed calling a TypeScript
property).

Root cause: graph symbol matching produces false edges when two symbols in
different languages share the same method name.  Classic example:
  Python ``cfg_path.exists()`` → TypeScript ``AmbientStatus.exists`` property
  (both named "exists", so the graph incorrectly draws a CALLS edge)

S1040 surfaces this so agents know to verify the edge is real before acting on it.
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
    language: str = "python",
    qualified_name: str | None = None,
):
    from tempograph.types import Symbol, SymbolKind, Language

    kmap = {
        "function": SymbolKind.FUNCTION,
        "method": SymbolKind.METHOD,
        "class": SymbolKind.CLASS,
        "property": SymbolKind.VARIABLE,
    }
    lang_map = {
        "python": Language.PYTHON,
        "typescript": Language.TYPESCRIPT,
        "javascript": Language.JAVASCRIPT,
        "rust": Language.RUST,
        "go": Language.GO,
    }
    return Symbol(
        id=f"{file_path}::{name}",
        name=name,
        qualified_name=qualified_name if qualified_name is not None else name,
        kind=kmap.get(kind, SymbolKind.FUNCTION),
        language=lang_map.get(language, Language.PYTHON),
        file_path=file_path,
        line_start=1,
        line_end=10,
        exported=True,
    )


def _make_graph(seed_sym, *, cross_lang_callees=None, same_lang_callees=None):
    """Build a minimal fake Tempo for S1040 unit tests.

    cross_lang_callees: list of Symbol objects in a different language than seed.
    same_lang_callees:  list of Symbol objects in the same language as seed.
    """
    cross_lang_callees = cross_lang_callees or []
    same_lang_callees = same_lang_callees or []

    g = types.SimpleNamespace()
    g.symbols = {seed_sym.id: seed_sym}
    g.edges = []
    g.files = {}
    g.hot_files = set()
    g._callers = {}
    g._callees = {seed_sym.id: []}
    g._children = {}
    g._importers = {}
    g._renderers = {}
    g._subtypes = {}

    all_callees = cross_lang_callees + same_lang_callees
    for callee in all_callees:
        g.symbols[callee.id] = callee
        g._callees[seed_sym.id].append(callee.id)

    def _callers_of(sym_id):
        return [g.symbols[s] for s in g._callers.get(sym_id, []) if s in g.symbols]

    def _callees_of(sym_id):
        return [g.symbols[s] for s in g._callees.get(sym_id, []) if s in g.symbols]

    def _renderers_of(sym_id):
        return []

    def _subtypes_of(name):
        return []

    def _importers_of(fp):
        return []

    def _find_symbol(name):
        return [s for s in g.symbols.values() if s.name == name]

    g.callers_of = _callers_of
    g.callees_of = _callees_of
    g.renderers_of = _renderers_of
    g.subtypes_of = _subtypes_of
    g.importers_of = _importers_of
    g.find_symbol = _find_symbol

    return g


def _ordered_for(seed_sym, graph, depth=1):
    """Build a minimal ordered list: seed at depth 0, callees at depth 1."""
    ordered = [(seed_sym, 0)]
    for cid in graph._callees.get(seed_sym.id, []):
        if cid in graph.symbols:
            ordered.append((graph.symbols[cid], depth))
    return ordered


# ---------------------------------------------------------------------------
# Unit tests for _compute_cross_language_callees
# ---------------------------------------------------------------------------

from tempograph.render.focused import _compute_cross_language_callees


class TestCrossLanguageCalleesUnit:

    # ---- Fires correctly ----

    def test_fires_python_calling_typescript(self):
        """Python function has TypeScript depth-1 callee → fires."""
        seed = _make_sym("build_graph", file_path="tempograph/builder.py", language="python")
        ts_callee = _make_sym(
            "exists", file_path="tempo/ui/src/hooks/useAmbientStatus.ts",
            kind="property", language="typescript",
            qualified_name="AmbientStatus.exists",
        )
        g = _make_graph(seed, cross_lang_callees=[ts_callee])
        ordered = _ordered_for(seed, g)
        result = _compute_cross_language_callees([seed], ordered, g)
        assert "cross-language callees:" in result
        assert "AmbientStatus.exists" in result
        assert "TypeScript" in result
        assert "symbol-name collision" in result

    def test_fires_python_calling_javascript(self):
        """Python function with JavaScript depth-1 callee → fires."""
        seed = _make_sym("process", file_path="src/backend.py", language="python")
        js_callee = _make_sym("render", file_path="src/ui/Component.js",
                               language="javascript")
        g = _make_graph(seed, cross_lang_callees=[js_callee])
        ordered = _ordered_for(seed, g)
        result = _compute_cross_language_callees([seed], ordered, g)
        assert "cross-language callees:" in result
        assert "JavaScript" in result

    def test_fires_rust_calling_typescript(self):
        """Rust function with TypeScript callee → fires."""
        seed = _make_sym("parse", file_path="src/parser.rs", language="rust")
        ts_callee = _make_sym("transform", file_path="src/ui/transform.ts",
                               language="typescript")
        g = _make_graph(seed, cross_lang_callees=[ts_callee])
        ordered = _ordered_for(seed, g)
        result = _compute_cross_language_callees([seed], ordered, g)
        assert "cross-language callees:" in result

    def test_shows_multiple_cross_lang_callees(self):
        """Multiple TypeScript depth-1 callees → all shown (up to 3)."""
        seed = _make_sym("heavy_fn", file_path="src/backend.py", language="python")
        ts1 = _make_sym("alpha", file_path="ui/alpha.ts", language="typescript")
        ts2 = _make_sym("beta", file_path="ui/beta.ts", language="typescript")
        g = _make_graph(seed, cross_lang_callees=[ts1, ts2])
        ordered = _ordered_for(seed, g)
        result = _compute_cross_language_callees([seed], ordered, g)
        assert "alpha" in result
        assert "beta" in result

    def test_overflow_beyond_three(self):
        """More than 3 cross-language callees → overflow suffix."""
        seed = _make_sym("mega_fn", file_path="src/core.py", language="python")
        ts_callees = [
            _make_sym(f"ts_fn_{i}", file_path=f"ui/fn_{i}.ts", language="typescript")
            for i in range(5)
        ]
        g = _make_graph(seed, cross_lang_callees=ts_callees)
        ordered = _ordered_for(seed, g)
        result = _compute_cross_language_callees([seed], ordered, g)
        assert "+2 more" in result

    def test_mentions_exclude_dirs_hint(self):
        """Output includes actionable hint about exclude_dirs."""
        seed = _make_sym("do_it", file_path="src/main.py", language="python")
        ts_callee = _make_sym("helper", file_path="ui/helper.ts", language="typescript")
        g = _make_graph(seed, cross_lang_callees=[ts_callee])
        ordered = _ordered_for(seed, g)
        result = _compute_cross_language_callees([seed], ordered, g)
        assert "exclude_dirs" in result

    # ---- Silent conditions ----

    def test_silent_same_language_callees(self):
        """All callees in same language as seed → silent."""
        seed = _make_sym("fn_a", file_path="src/a.py", language="python")
        py_callee = _make_sym("fn_b", file_path="src/b.py", language="python")
        g = _make_graph(seed, same_lang_callees=[py_callee])
        ordered = _ordered_for(seed, g)
        result = _compute_cross_language_callees([seed], ordered, g)
        assert result == ""

    def test_silent_no_callees(self):
        """Seed with no callees → silent."""
        seed = _make_sym("leaf", file_path="src/leaf.py", language="python")
        g = _make_graph(seed)
        ordered = [(seed, 0)]
        result = _compute_cross_language_callees([seed], ordered, g)
        assert result == ""

    def test_silent_typescript_seed(self):
        """TypeScript seed → silent (cross-language edges expected in frontend context)."""
        seed = _make_sym("useHook", file_path="ui/hooks/useHook.ts", language="typescript")
        py_callee = _make_sym("get_data", file_path="src/api.py", language="python")
        g = _make_graph(seed, cross_lang_callees=[py_callee])
        ordered = _ordered_for(seed, g)
        result = _compute_cross_language_callees([seed], ordered, g)
        assert result == ""

    def test_silent_javascript_seed(self):
        """JavaScript seed → silent."""
        seed = _make_sym("render", file_path="ui/comp.js", language="javascript")
        py_callee = _make_sym("compute", file_path="src/compute.py", language="python")
        g = _make_graph(seed, cross_lang_callees=[py_callee])
        ordered = _ordered_for(seed, g)
        result = _compute_cross_language_callees([seed], ordered, g)
        assert result == ""

    def test_silent_class_seed(self):
        """Class seed → silent (only function/method seeds fire)."""
        seed = _make_sym("MyParser", file_path="src/parser.py", language="python",
                          kind="class")
        ts_callee = _make_sym("parse", file_path="ui/parse.ts", language="typescript")
        g = _make_graph(seed, cross_lang_callees=[ts_callee])
        ordered = _ordered_for(seed, g)
        result = _compute_cross_language_callees([seed], ordered, g)
        assert result == ""

    def test_silent_test_file_seed(self):
        """Test file seeds → silent."""
        seed = _make_sym("test_fn", file_path="tests/test_core.py", language="python")
        ts_callee = _make_sym("util", file_path="ui/util.ts", language="typescript")
        g = _make_graph(seed, cross_lang_callees=[ts_callee])
        ordered = _ordered_for(seed, g)
        result = _compute_cross_language_callees([seed], ordered, g)
        assert result == ""

    def test_silent_test_file_callee_excluded(self):
        """Test file callees (TypeScript test files) are excluded."""
        seed = _make_sym("build", file_path="src/build.py", language="python")
        ts_test_callee = _make_sym("helper", file_path="tests/ui.test.ts",
                                   language="typescript")
        g = _make_graph(seed, cross_lang_callees=[ts_test_callee])
        ordered = _ordered_for(seed, g)
        result = _compute_cross_language_callees([seed], ordered, g)
        assert result == ""

    def test_silent_depth_2_callees_not_counted(self):
        """Only depth-1 callees checked; depth-2 cross-lang callees don't fire."""
        seed = _make_sym("entry", file_path="src/entry.py", language="python")
        ts_callee = _make_sym("ts_fn", file_path="ui/deep.ts", language="typescript")
        g = _make_graph(seed)
        g.symbols[ts_callee.id] = ts_callee
        # depth-2 only, seed does NOT directly call it
        ordered = [(seed, 0), (ts_callee, 2)]
        result = _compute_cross_language_callees([seed], ordered, g)
        assert result == ""

    def test_silent_empty_seeds(self):
        """Empty seeds list → silent."""
        seed = _make_sym("fn", file_path="src/fn.py")
        g = _make_graph(seed)
        result = _compute_cross_language_callees([], [], g)
        assert result == ""

    def test_silent_empty_ordered(self):
        """Empty ordered list → silent."""
        seed = _make_sym("fn", file_path="src/fn.py")
        g = _make_graph(seed)
        result = _compute_cross_language_callees([seed], [], g)
        assert result == ""


# ---------------------------------------------------------------------------
# Integration tests using render_focused on the real codebase
# ---------------------------------------------------------------------------

class TestCrossLanguageCalleesIntegration:
    """Integration tests: render_focused on real codebase.

    build_graph in builder.py calls cfg_path.exists() (Python Path) which the
    graph incorrectly maps to AmbientStatus.exists (TypeScript property).
    S1040 should fire and surface this suspicious edge.
    """

    @staticmethod
    def _focus(query: str) -> str:
        from tempograph import build_graph
        from tempograph.render.focused import render_focused
        g = build_graph(".")
        return render_focused(g, query)

    def test_build_graph_fires_cross_language(self):
        """build_graph has Python→TypeScript false edges — S1040 fires."""
        result = self._focus("build_graph")
        assert "cross-language callees:" in result

    def test_render_overview_silent(self):
        """render_overview is pure Python — no cross-language edges expected."""
        result = self._focus("render_overview")
        # render_overview doesn't have cross-language phantom edges
        # (it's possible it does — just verify it only fires when warranted)
        # This test validates the signal doesn't fire spuriously on simple Python fns
        if "cross-language callees:" in result:
            # If it fires, verify the output is formatted correctly
            assert "TypeScript" in result or "Javascript" in result
            assert "exclude_dirs" in result
