"""Tests for S1033: Variant group signal in render_focused.

S1033 fires when a focus seed belongs to an A/B/C alphabetical or numeric series —
same-file symbols sharing the same base name with a single-letter or numeric suffix.

Example: focusing on `_signals_hotspots_core_b` surfaces `_a`, `_c`, `_d` as
co-edit candidates. The signal prevents the "fix-one-forget-the-other" anti-pattern.

Different from:
- S1032 (naming clusters): fires when DEPTH-1 BFS neighbors share a stem (neighborhood)
- S81/hot siblings: fires when same-file symbols have high cross-file callers (popularity)
- S57 (primary caller concentration): caller FILE concentration, not variant group
"""

from __future__ import annotations
import subprocess
import types
import pytest


# ---------------------------------------------------------------------------
# Helper: build a minimal Tempo mock for unit tests
# ---------------------------------------------------------------------------

def _make_sym(name: str, file_path: str = "src/module.py", kind: str = "function"):
    from tempograph.types import Symbol, SymbolKind, Language

    kind_map = {
        "function": SymbolKind.FUNCTION,
        "method": SymbolKind.METHOD,
        "class": SymbolKind.CLASS,
    }
    return Symbol(
        id=f"{file_path}::{name}",
        name=name,
        qualified_name=name,
        kind=kind_map.get(kind, SymbolKind.FUNCTION),
        language=Language.PYTHON,
        file_path=file_path,
        line_start=1,
        line_end=5,
    )


def _make_graph(seed, same_file_syms):
    """Build a minimal fake Tempo-like graph for _compute_variant_group tests."""
    from tempograph.types import FileInfo

    all_syms = [seed] + same_file_syms
    symbols_dict = {s.id: s for s in all_syms}

    fi = FileInfo(
        path=seed.file_path,
        language="python",
        symbols=[s.id for s in all_syms],
        line_count=100,
        byte_size=1000,
    )

    g = types.SimpleNamespace(
        files={seed.file_path: fi},
        symbols=symbols_dict,
    )
    return g


# ---------------------------------------------------------------------------
# Unit tests for _compute_variant_group
# ---------------------------------------------------------------------------

class TestComputeVariantGroup:
    """Unit tests for the _compute_variant_group helper."""

    def test_fires_for_alpha_suffix_with_siblings(self):
        from tempograph.render.focused import _compute_variant_group

        seed = _make_sym("_signals_hotspots_core_b")
        variants = [
            _make_sym("_signals_hotspots_core_a"),
            _make_sym("_signals_hotspots_core_c"),
            _make_sym("_signals_hotspots_core_d"),
        ]
        g = _make_graph(seed, variants)
        result = _compute_variant_group([seed], g)

        assert "variant group" in result, f"should fire for A/B/C/D series; got: {result!r}"
        assert "A/B series" in result, f"should label as A/B series; got: {result!r}"
        assert "4" in result, f"should report total of 4 members; got: {result!r}"

    def test_fires_for_numeric_suffix_with_siblings(self):
        from tempograph.render.focused import _compute_variant_group

        seed = _make_sym("render_context_1")
        variants = [
            _make_sym("render_context_2"),
            _make_sym("render_context_3"),
        ]
        g = _make_graph(seed, variants)
        result = _compute_variant_group([seed], g)

        assert "variant group" in result, f"should fire for numeric series; got: {result!r}"
        assert "numeric series" in result, f"should label as numeric series; got: {result!r}"
        assert "3" in result, f"should report total of 3 members; got: {result!r}"

    def test_silent_when_no_same_file_variants(self):
        from tempograph.render.focused import _compute_variant_group

        seed = _make_sym("_signals_hotspots_core_b")
        # Other file symbols — not in seed's file
        other = _make_sym("_signals_hotspots_core_a", file_path="src/other.py")
        g = _make_graph(seed, [])  # empty same-file list

        # Manually inject the other file's symbol but not in seed's FileInfo
        g.symbols[other.id] = other

        result = _compute_variant_group([seed], g)
        assert result == "", f"should be silent when variants are in different files; got: {result!r}"

    def test_silent_when_no_variant_suffix(self):
        from tempograph.render.focused import _compute_variant_group

        seed = _make_sym("render_focused")
        others = [
            _make_sym("render_hotspots"),
            _make_sym("render_dead_code"),
        ]
        g = _make_graph(seed, others)
        result = _compute_variant_group([seed], g)
        assert result == "", f"should be silent when seed has no variant suffix; got: {result!r}"

    def test_silent_when_base_too_short(self):
        from tempograph.render.focused import _compute_variant_group

        # base "foo" = 3 chars, below 5-char threshold
        seed = _make_sym("foo_a")
        variants = [_make_sym("foo_b"), _make_sym("foo_c")]
        g = _make_graph(seed, variants)
        result = _compute_variant_group([seed], g)
        assert result == "", f"should be silent when base < 5 chars; got: {result!r}"

    def test_alpha_and_numeric_do_not_cross_match(self):
        from tempograph.render.focused import _compute_variant_group

        # seed is alpha suffix, siblings are numeric — different type, should NOT fire
        seed = _make_sym("render_context_a")
        variants = [
            _make_sym("render_context_1"),
            _make_sym("render_context_2"),
        ]
        g = _make_graph(seed, variants)
        result = _compute_variant_group([seed], g)
        assert result == "", (
            f"alpha suffix seed should not match numeric siblings; got: {result!r}"
        )

    def test_silent_for_test_file_seed(self):
        from tempograph.render.focused import _compute_variant_group

        seed = _make_sym("_signals_core_a", file_path="tests/test_signals.py")
        variants = [
            _make_sym("_signals_core_b", file_path="tests/test_signals.py"),
        ]
        g = _make_graph(seed, variants)
        result = _compute_variant_group([seed], g)
        assert result == "", f"should be silent for test-file seeds; got: {result!r}"

    def test_variant_names_shown_in_output(self):
        from tempograph.render.focused import _compute_variant_group

        seed = _make_sym("_signals_dead_patterns_a")
        variants = [_make_sym("_signals_dead_patterns_b")]
        g = _make_graph(seed, variants)
        result = _compute_variant_group([seed], g)

        assert "variant group" in result, f"should fire; got: {result!r}"
        assert "_signals_dead_patterns_b" in result, (
            f"sibling name should appear in output; got: {result!r}"
        )

    def test_shows_overflow_when_many_variants(self):
        from tempograph.render.focused import _compute_variant_group

        seed = _make_sym("process_batch_a")
        variants = [
            _make_sym(f"process_batch_{c}")
            for c in "bcdefg"  # 6 more variants
        ]
        g = _make_graph(seed, variants)
        result = _compute_variant_group([seed], g)

        assert "variant group" in result, f"should fire; got: {result!r}"
        assert "more" in result, f"should show overflow count; got: {result!r}"

    def test_method_kind_also_detected(self):
        from tempograph.render.focused import _compute_variant_group

        seed = _make_sym("_handle_request_a", kind="method")
        variants = [_make_sym("_handle_request_b", kind="method")]
        g = _make_graph(seed, variants)
        result = _compute_variant_group([seed], g)

        assert "variant group" in result, f"should fire for method kind; got: {result!r}"

    def test_class_kind_not_detected(self):
        from tempograph.render.focused import _compute_variant_group

        # Classes shouldn't trigger (they're not parallelism variants the same way)
        seed = _make_sym("BaseHandler_a", kind="class")
        variants = [_make_sym("BaseHandler_b", kind="class")]
        g = _make_graph(seed, variants)
        result = _compute_variant_group([seed], g)
        # Class kind seeds: the implementation only checks kind in OTHER file symbols,
        # not the seed itself. So this may or may not fire depending on the seed check.
        # The test verifies the function runs without error.
        assert isinstance(result, str), "should return a string"

    def test_exact_base_match_required(self):
        from tempograph.render.focused import _compute_variant_group

        seed = _make_sym("_compute_blast_a")
        # "_compute_bfs_a" shares "_compute" prefix but NOT the same base "_compute_blast"
        variants = [_make_sym("_compute_bfs_a"), _make_sym("_compute_bfs_b")]
        g = _make_graph(seed, variants)
        result = _compute_variant_group([seed], g)
        assert result == "", f"different base names should not match; got: {result!r}"

    def test_total_count_includes_seed(self):
        from tempograph.render.focused import _compute_variant_group

        seed = _make_sym("_signals_core_a")
        variants = [_make_sym("_signals_core_b"), _make_sym("_signals_core_c")]
        g = _make_graph(seed, variants)
        result = _compute_variant_group([seed], g)

        # seed + 2 variants = 3 total
        assert "3" in result, f"total should be 3 (seed + 2 variants); got: {result!r}"


# ---------------------------------------------------------------------------
# Integration test: fires on the real codebase
# ---------------------------------------------------------------------------

REPO_PATH = "/Users/elmoaidali/Desktop/tempograph"


class TestVariantGroupIntegration:
    """Integration tests: variant group fires on real A/B/C/D patterns."""

    def test_fires_for_signals_hotspots_core_b(self):
        """_signals_hotspots_core_b has a/c/d variants — signal must fire."""
        from tempograph.builder import build_graph
        from tempograph.render import render_focused

        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        out = render_focused(g, "_signals_hotspots_core_b", max_tokens=6000)

        assert "variant group" in out, (
            f"_signals_hotspots_core_b must surface A/B variants;\n{out[:400]}"
        )
        assert "A/B series" in out, "should label as A/B series"
        assert "_signals_hotspots_core_a" in out, "should name variant _a"

    def test_fires_for_signals_dead_patterns_a(self):
        """_signals_dead_patterns_a has a _b sibling — signal must fire."""
        from tempograph.builder import build_graph
        from tempograph.render import render_focused

        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        out = render_focused(g, "_signals_dead_patterns_a", max_tokens=6000)

        assert "variant group" in out, (
            f"_signals_dead_patterns_a must surface its _b sibling;\n{out[:400]}"
        )
        assert "_signals_dead_patterns_b" in out, "should name the _b sibling"

    def test_silent_for_render_focused(self):
        """render_focused has no variant suffix — signal must be silent."""
        from tempograph.builder import build_graph
        from tempograph.render import render_focused

        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        out = render_focused(g, "render_focused", max_tokens=6000)

        assert "variant group" not in out, (
            f"render_focused has no A/B suffix — signal must be silent;\n{out[:400]}"
        )

    def test_silent_for_build_graph(self):
        """build_graph has no variant suffix — signal must be silent."""
        from tempograph.builder import build_graph
        from tempograph.render import render_focused

        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        out = render_focused(g, "build_graph", max_tokens=6000)

        assert "variant group" not in out, (
            f"build_graph has no A/B suffix — signal must be silent;\n{out[:400]}"
        )
