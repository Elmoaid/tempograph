"""Tests for render/_utils.py: _dead_code_confidence, _is_test_file, count_tokens."""
from __future__ import annotations

import pytest

from tempograph.types import Symbol, SymbolKind, EdgeKind, Language, Edge, Tempo
from tempograph.render._utils import _dead_code_confidence, _is_test_file, count_tokens


# ── helpers ──────────────────────────────────────────────────────────────────

def _sym(
    name: str,
    file_path: str = "mod.py",
    kind: SymbolKind = SymbolKind.FUNCTION,
    line_start: int = 1,
    line_end: int = 5,
    exported: bool = True,
    doc: str = "",
    parent_id: str | None = None,
) -> Symbol:
    return Symbol(
        id=f"{file_path}::{name}",
        name=name,
        qualified_name=name,
        kind=kind,
        language=Language.PYTHON,
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        exported=exported,
        doc=doc,
        parent_id=parent_id,
    )


def _empty_graph() -> Tempo:
    g = Tempo(root="/tmp/repo")
    g.build_indexes()
    return g


def _graph_with(*symbols: Symbol, edges: list[Edge] | None = None) -> Tempo:
    g = Tempo(root="/tmp/repo")
    for sym in symbols:
        g.symbols[sym.id] = sym
    g.edges = edges or []
    g.build_indexes()
    return g


# ── _is_test_file ─────────────────────────────────────────────────────────────

class TestIsTestFile:
    def test_test_prefix_py(self):
        assert _is_test_file("tests/test_foo.py")

    def test_test_suffix_py(self):
        assert _is_test_file("src/foo_test.py")

    def test_test_ts(self):
        assert _is_test_file("src/foo.test.ts")

    def test_spec_tsx(self):
        assert _is_test_file("src/Bar.spec.tsx")

    def test_regular_py(self):
        assert not _is_test_file("mod.py")

    def test_regular_ts(self):
        assert not _is_test_file("src/utils.ts")

    def test_empty_string(self):
        assert not _is_test_file("")


# ── count_tokens ──────────────────────────────────────────────────────────────

class TestCountTokens:
    def test_empty_string(self):
        assert count_tokens("") == 0

    def test_single_word(self):
        assert count_tokens("hello") > 0

    def test_longer_is_more(self):
        short = count_tokens("hi")
        long = count_tokens("hi there, how are you doing today?")
        assert long > short

    def test_returns_int(self):
        assert isinstance(count_tokens("test"), int)


# ── _dead_code_confidence ─────────────────────────────────────────────────────

class TestDeadCodeConfidence:
    def test_no_callers_raises_score(self):
        sym = _sym("orphan")
        g = _graph_with(sym)
        score = _dead_code_confidence(sym, g)
        assert score > 0

    def test_test_file_symbol_scores_low(self):
        sym = _sym("helper", file_path="tests/test_mod.py")
        g = _graph_with(sym)
        score = _dead_code_confidence(sym, g)
        assert score < 30

    def test_large_symbol_scores_higher(self):
        small = _sym("tiny", line_start=1, line_end=3)
        large = _sym("big", line_start=1, line_end=100)
        g_small = _graph_with(small)
        g_large = _graph_with(large)
        assert _dead_code_confidence(large, g_large) > _dead_code_confidence(small, g_small)

    def test_dispatch_pattern_reduces_score(self):
        normal = _sym("process")
        handler = _sym("handle_request")
        g = _graph_with(normal, handler)
        assert _dead_code_confidence(normal, g) >= _dead_code_confidence(handler, g)

    def test_plugin_run_scores_lower_than_normal_run(self):
        # "run" in /plugins/ path gets extra -30 penalty vs "run" elsewhere
        plugin_run = _sym("run", file_path="tempo/plugins/myplugin/__init__.py")
        normal_run = _sym("run", file_path="tempograph/utils.py")
        g = _graph_with(plugin_run, normal_run)
        assert _dead_code_confidence(plugin_run, g) < _dead_code_confidence(normal_run, g)

    def test_doc_reduces_score(self):
        no_doc = _sym("fn")
        with_doc = _sym("fn_doc", doc="Does something important.")
        g = _graph_with(no_doc, with_doc)
        assert _dead_code_confidence(no_doc, g) >= _dead_code_confidence(with_doc, g)

    def test_score_clamped_to_zero(self):
        # Test file symbol with docstring + dispatch name — should not go negative
        sym = _sym("handle_event", file_path="tests/test_x.py", doc="desc")
        g = _graph_with(sym)
        assert _dead_code_confidence(sym, g) >= 0

    def test_score_clamped_to_hundred(self):
        # Large, no callers, no importers, no doc
        sym = _sym("big_orphan", line_start=1, line_end=200, doc="")
        g = _graph_with(sym)
        assert _dead_code_confidence(sym, g) <= 100

    def test_tauri_command_scores_low(self):
        sym = _sym("open_file", kind=SymbolKind.COMMAND)
        g = _graph_with(sym)
        assert _dead_code_confidence(sym, g) < 30

    def test_caller_reduces_score(self):
        sym = _sym("fn")
        caller = _sym("caller")
        # CALLS edge: caller → sym
        edge = Edge(kind=EdgeKind.CALLS, source_id=caller.id, target_id=sym.id)
        g = _graph_with(sym, caller, edges=[edge])
        score_with_caller = _dead_code_confidence(sym, g)

        g2 = _graph_with(sym)
        score_without_caller = _dead_code_confidence(sym, g2)
        # Having a caller should not hurt the score (no-caller adds +30)
        assert score_with_caller <= score_without_caller
