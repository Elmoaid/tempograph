"""Tests for S1034: Cross-file sibling family signal in render_focused.

S1034 fires when the focus seed belongs to a naming family (same prefix, same parent
directory) that spans multiple files not shown in BFS. When you edit render_focused
to add a new signal, you should know that render_dead_code, render_diff_context, etc.
may need the same change. BFS only shows callers/callees — not parallel siblings.

Different from:
- S1032 (naming cluster): fires on depth-1 BFS NEIGHBORS sharing a stem (what you see)
- S1033 (variant group): same-FILE A/B/C/D suffix variants of the seed
- S1034 (this): seed belongs to a cross-FILE naming family in the same directory
"""

from __future__ import annotations
import os
import types
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sym(name: str, file_path: str = "src/render/module.py", kind: str = "function"):
    from tempograph.types import Symbol, SymbolKind, Language

    return Symbol(
        id=f"{file_path}::{name}",
        name=name,
        qualified_name=name,
        kind=SymbolKind.FUNCTION,
        language=Language.PYTHON,
        file_path=file_path,
        line_start=1,
        line_end=5,
    )


def _make_graph(all_syms: list):
    """Build a minimal fake Tempo-like graph for unit tests."""
    symbols_dict = {s.id: s for s in all_syms}
    g = types.SimpleNamespace(symbols=symbols_dict)
    return g


# ---------------------------------------------------------------------------
# Unit tests for _compute_cross_file_siblings
# ---------------------------------------------------------------------------

class TestComputeCrossFileSiblings:
    """Unit tests for the _compute_cross_file_siblings helper."""

    def test_fires_with_three_cross_file_siblings(self):
        from tempograph.render.focused import _compute_cross_file_siblings

        seed = _make_sym("render_focused", "src/render/focused.py")
        sibs = [
            _make_sym("render_dead_code", "src/render/dead.py"),
            _make_sym("render_blast_radius", "src/render/blast.py"),
            _make_sym("render_diff_context", "src/render/diff.py"),
        ]
        g = _make_graph([seed] + sibs)
        result = _compute_cross_file_siblings([seed], g, seen_ids=set())

        assert "sibling family" in result, f"should fire with 3+ siblings; got: {result!r}"
        assert "render_*" in result, f"should show stem pattern; got: {result!r}"
        assert "3" in result or "4" in result, f"should report sibling count; got: {result!r}"

    def test_fires_shows_correct_count(self):
        from tempograph.render.focused import _compute_cross_file_siblings

        seed = _make_sym("render_focused", "src/render/focused.py")
        sibs = [
            _make_sym("render_dead_code", "src/render/dead.py"),
            _make_sym("render_blast_radius", "src/render/blast.py"),
            _make_sym("render_diff_context", "src/render/diff.py"),
            _make_sym("render_overview", "src/render/overview.py"),
        ]
        g = _make_graph([seed] + sibs)
        result = _compute_cross_file_siblings([seed], g, seen_ids=set())

        assert "sibling family" in result
        assert "4" in result, f"should count 4 siblings; got: {result!r}"

    def test_silent_with_fewer_than_three_siblings(self):
        from tempograph.render.focused import _compute_cross_file_siblings

        seed = _make_sym("render_focused", "src/render/focused.py")
        sibs = [
            _make_sym("render_dead_code", "src/render/dead.py"),
            _make_sym("render_blast_radius", "src/render/blast.py"),
        ]
        g = _make_graph([seed] + sibs)
        result = _compute_cross_file_siblings([seed], g, seen_ids=set())

        assert result == "", f"should be silent with only 2 siblings; got: {result!r}"

    def test_silent_siblings_in_different_directory(self):
        from tempograph.render.focused import _compute_cross_file_siblings

        seed = _make_sym("render_focused", "src/render/focused.py")
        # siblings are in a completely different directory
        sibs = [
            _make_sym("render_dead_code", "src/other/dead.py"),
            _make_sym("render_blast_radius", "src/other/blast.py"),
            _make_sym("render_diff_context", "src/other/diff.py"),
        ]
        g = _make_graph([seed] + sibs)
        result = _compute_cross_file_siblings([seed], g, seen_ids=set())

        assert result == "", f"should be silent when siblings are in different dir; got: {result!r}"

    def test_excludes_siblings_already_in_seen_ids(self):
        from tempograph.render.focused import _compute_cross_file_siblings

        seed = _make_sym("render_focused", "src/render/focused.py")
        sibs = [
            _make_sym("render_dead_code", "src/render/dead.py"),
            _make_sym("render_blast_radius", "src/render/blast.py"),
            _make_sym("render_diff_context", "src/render/diff.py"),
            _make_sym("render_overview", "src/render/overview.py"),
        ]
        g = _make_graph([seed] + sibs)

        # Put 3 siblings into seen_ids — only 1 left outside BFS
        seen_ids = {sibs[0].id, sibs[1].id, sibs[2].id}
        result = _compute_cross_file_siblings([seed], g, seen_ids=seen_ids)

        # Only 1 sibling outside BFS → below threshold of 3 → silent
        assert result == "", f"should be silent when siblings are in seen_ids; got: {result!r}"

    def test_excludes_same_file_symbols(self):
        from tempograph.render.focused import _compute_cross_file_siblings

        seed = _make_sym("render_focused", "src/render/focused.py")
        # Same file siblings — should be variant group territory (S1033), not this signal
        same_file = [
            _make_sym("render_focused_v2", "src/render/focused.py"),
            _make_sym("render_focused_lite", "src/render/focused.py"),
            _make_sym("render_focused_debug", "src/render/focused.py"),
        ]
        g = _make_graph([seed] + same_file)
        result = _compute_cross_file_siblings([seed], g, seen_ids=set())

        assert result == "", f"should be silent for same-file siblings; got: {result!r}"

    def test_silent_when_seed_is_test_file(self):
        from tempograph.render.focused import _compute_cross_file_siblings

        seed = _make_sym("render_focused", "tests/test_render/test_focused.py")
        sibs = [
            _make_sym("render_dead_code", "tests/test_render/test_dead.py"),
            _make_sym("render_blast_radius", "tests/test_render/test_blast.py"),
            _make_sym("render_diff_context", "tests/test_render/test_diff.py"),
        ]
        g = _make_graph([seed] + sibs)
        result = _compute_cross_file_siblings([seed], g, seen_ids=set())

        assert result == "", f"should be silent for test-file seeds; got: {result!r}"

    def test_excludes_test_file_siblings(self):
        from tempograph.render.focused import _compute_cross_file_siblings

        seed = _make_sym("render_focused", "src/render/focused.py")
        # Mix: 2 real siblings + 3 test siblings
        real_sibs = [
            _make_sym("render_dead_code", "src/render/dead.py"),
            _make_sym("render_blast_radius", "src/render/blast.py"),
        ]
        test_sibs = [
            _make_sym("render_test_a", "tests/test_a.py"),
            _make_sym("render_test_b", "tests/test_b.py"),
            _make_sym("render_test_c", "tests/test_c.py"),
        ]
        g = _make_graph([seed] + real_sibs + test_sibs)
        result = _compute_cross_file_siblings([seed], g, seen_ids=set())

        # Only 2 real siblings → below threshold
        assert result == "", f"should exclude test siblings and be silent; got: {result!r}"

    def test_silent_stem_too_short(self):
        from tempograph.render.focused import _compute_cross_file_siblings

        # "run" is 3 chars → _get_naming_stem returns "" (first part < 4 chars)
        seed = _make_sym("run_something", "src/cmd/run.py")
        sibs = [
            _make_sym("run_other", "src/cmd/other.py"),
            _make_sym("run_third", "src/cmd/third.py"),
            _make_sym("run_fourth", "src/cmd/fourth.py"),
        ]
        g = _make_graph([seed] + sibs)
        result = _compute_cross_file_siblings([seed], g, seen_ids=set())

        assert result == "", f"should be silent for short stems; got: {result!r}"

    def test_silent_stem_four_chars_filtered_by_five_char_minimum(self):
        from tempograph.render.focused import _compute_cross_file_siblings

        # "emit" is 4 chars — _get_naming_stem returns "emit" but our 5-char guard blocks it
        seed = _make_sym("emit_event", "src/events/emitter.py")
        sibs = [
            _make_sym("emit_signal", "src/events/signal.py"),
            _make_sym("emit_message", "src/events/message.py"),
            _make_sym("emit_error", "src/events/error.py"),
        ]
        g = _make_graph([seed] + sibs)
        result = _compute_cross_file_siblings([seed], g, seen_ids=set())

        assert result == "", f"should be silent for 4-char stems; got: {result!r}"

    def test_fires_with_private_stem_five_plus_chars(self):
        from tempograph.render.focused import _compute_cross_file_siblings

        # "_compute" is 7 bare chars ("compute") — should fire
        seed = _make_sym("_compute_result", "src/module/a.py")
        sibs = [
            _make_sym("_compute_score", "src/module/b.py"),
            _make_sym("_compute_weight", "src/module/c.py"),
            _make_sym("_compute_delta", "src/module/d.py"),
        ]
        g = _make_graph([seed] + sibs)
        result = _compute_cross_file_siblings([seed], g, seen_ids=set())

        assert "sibling family" in result, f"should fire for private stem ≥5 chars; got: {result!r}"

    def test_output_contains_directory_and_examples(self):
        from tempograph.render.focused import _compute_cross_file_siblings

        seed = _make_sym("render_focused", "src/render/focused.py")
        sibs = [
            _make_sym("render_dead_code", "src/render/dead.py"),
            _make_sym("render_blast_radius", "src/render/blast.py"),
            _make_sym("render_diff_context", "src/render/diff.py"),
        ]
        g = _make_graph([seed] + sibs)
        result = _compute_cross_file_siblings([seed], g, seen_ids=set())

        assert "render/" in result or "src/render" in result, f"should mention directory; got: {result!r}"
        assert "not in BFS" in result, f"should say 'not in BFS'; got: {result!r}"
        # At least one sibling name should appear
        sibling_names = ["render_dead_code", "render_blast_radius", "render_diff_context"]
        assert any(n in result for n in sibling_names), f"should list sibling names; got: {result!r}"

    def test_overflow_suffix_for_many_siblings(self):
        from tempograph.render.focused import _compute_cross_file_siblings

        seed = _make_sym("render_focused", "src/render/focused.py")
        # 6 siblings — should show 3 + overflow
        sibs = [
            _make_sym(f"render_mode_{i}", f"src/render/mode_{i}.py")
            for i in range(6)
        ]
        g = _make_graph([seed] + sibs)
        result = _compute_cross_file_siblings([seed], g, seen_ids=set())

        assert "sibling family" in result
        assert "+3 more" in result, f"should show overflow for 6 siblings; got: {result!r}"

    def test_silent_when_no_matching_symbols(self):
        from tempograph.render.focused import _compute_cross_file_siblings

        seed = _make_sym("render_focused", "src/render/focused.py")
        # Completely unrelated symbols in same dir
        others = [
            _make_sym("build_graph", "src/render/builder.py"),
            _make_sym("parse_file", "src/render/parser.py"),
            _make_sym("load_config", "src/render/config.py"),
        ]
        g = _make_graph([seed] + others)
        result = _compute_cross_file_siblings([seed], g, seen_ids=set())

        assert result == "", f"should be silent when no name-matching siblings; got: {result!r}"

    def test_empty_seeds_list(self):
        from tempograph.render.focused import _compute_cross_file_siblings

        g = _make_graph([])
        result = _compute_cross_file_siblings([], g, seen_ids=set())
        assert result == "", f"should be silent for empty seeds; got: {result!r}"

    def test_deduplicates_same_name_in_multiple_files(self):
        """If the same function name appears in multiple files (due to overloads/re-exports),
        count it only once."""
        from tempograph.render.focused import _compute_cross_file_siblings

        seed = _make_sym("render_focused", "src/render/focused.py")
        # Same sibling name defined in 3 different files
        sibs = [
            _make_sym("render_dead_code", "src/render/dead_v1.py"),
            _make_sym("render_dead_code", "src/render/dead_v2.py"),  # duplicate name
            _make_sym("render_dead_code", "src/render/dead_v3.py"),  # duplicate name
            _make_sym("render_blast_radius", "src/render/blast.py"),
            _make_sym("render_diff_context", "src/render/diff.py"),
        ]
        g = _make_graph([seed] + sibs)
        result = _compute_cross_file_siblings([seed], g, seen_ids=set())

        # Should count render_dead_code once, total = 3 → fires
        assert "sibling family" in result, f"should fire with deduplicated count; got: {result!r}"
        assert "3" in result, f"should show deduplicated count of 3; got: {result!r}"
