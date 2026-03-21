"""Unit tests for prepare.py utility functions."""
from __future__ import annotations

import pytest

from tempograph.prepare import (
    _is_change_localization,
    _extract_focus_ranges,
    _cl_path_fallback,
)
from tempograph.types import Symbol, SymbolKind, Language, Tempo


# ── _is_change_localization ────────────────────────────────────────────────────

class TestIsChangeLocalization:
    def test_explicit_task_type_changelocal(self):
        assert _is_change_localization("anything", "changelocal") is True

    def test_explicit_task_type_debug(self):
        assert _is_change_localization("anything", "debug") is True

    def test_explicit_task_type_bugfix(self):
        assert _is_change_localization("anything", "bugfix") is True

    def test_merge_pull_request(self):
        assert _is_change_localization(
            "Merge pull request #1234 from user/fix-login", ""
        ) is True

    def test_merge_branch(self):
        assert _is_change_localization("Merge branch 'fix/auth'", "") is True

    def test_conventional_commit_fix(self):
        assert _is_change_localization("fix: prevent crash on empty input", "") is True

    def test_conventional_commit_feat(self):
        assert _is_change_localization("feat: add dark mode toggle", "") is True

    def test_conventional_commit_with_scope(self):
        assert _is_change_localization("feat(auth): add OAuth2 support", "") is True

    def test_conventional_commit_refactor(self):
        assert _is_change_localization("refactor(parser): simplify token handling", "") is True

    def test_issue_ref_at_end(self):
        assert _is_change_localization("Fix login crash (#5928)", "") is True

    def test_issue_ref_inline(self):
        assert _is_change_localization("Fix #1234: null pointer exception", "") is True

    def test_general_task_not_cl(self):
        assert _is_change_localization("Add a login page for users", "") is False

    def test_general_task_feature_request(self):
        assert _is_change_localization("Implement user authentication with JWT", "") is False

    def test_empty_task_not_cl(self):
        assert _is_change_localization("", "") is False

    def test_case_insensitive_merge(self):
        assert _is_change_localization("MERGE PULL REQUEST #99 from org/branch", "") is True


# ── _extract_focus_ranges ──────────────────────────────────────────────────────

class TestExtractFocusRanges:
    def test_extracts_file_range(self):
        focus = "  — render.py:100-200\n  some content"
        result = _extract_focus_ranges(focus, ["render.py"])
        assert result == {"render.py": "100-200"}

    def test_first_occurrence_wins(self):
        """Depth 0 (first occurrence) is most relevant."""
        focus = "  — parser.py:50-100\n  — parser.py:200-300"
        result = _extract_focus_ranges(focus, ["parser.py"])
        assert result == {"parser.py": "50-100"}

    def test_multiple_files(self):
        focus = "  — builder.py:1-50\n  — cache.py:100-200"
        result = _extract_focus_ranges(focus, ["builder.py", "cache.py"])
        assert result == {"builder.py": "1-50", "cache.py": "100-200"}

    def test_key_files_filter(self):
        """Only files in key_files are returned."""
        focus = "  — render.py:10-20\n  — types.py:5-15"
        result = _extract_focus_ranges(focus, ["render.py"])
        assert "types.py" not in result
        assert "render.py" in result

    def test_missing_key_file_not_in_result(self):
        focus = "  — builder.py:1-50"
        result = _extract_focus_ranges(focus, ["missing.py"])
        assert result == {}

    def test_empty_focus_output(self):
        result = _extract_focus_ranges("", ["render.py"])
        assert result == {}

    def test_empty_key_files(self):
        focus = "  — render.py:1-100"
        result = _extract_focus_ranges(focus, [])
        assert result == {}


# ── _cl_path_fallback ─────────────────────────────────────────────────────────

def _make_graph_with_files(*file_paths: str) -> Tempo:
    """Create a minimal Tempo with symbols at the given file paths."""
    graph = Tempo(root="/repo")
    for fp in file_paths:
        sym = Symbol(
            id=f"{fp}::fn",
            name="fn",
            qualified_name="fn",
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_path=fp,
            line_start=1,
            line_end=5,
        )
        graph.symbols[sym.id] = sym
    return graph


class TestClPathFallback:
    def test_plain_keyword_match(self):
        graph = _make_graph_with_files("src/hashers.py", "src/models.py")
        result = _cl_path_fallback(graph, "hasher")
        assert "src/hashers.py" in result
        assert "src/models.py" not in result

    def test_returns_empty_for_broad_keyword(self):
        """Keywords matching >5 files return empty."""
        files = [f"src/handler_{i}.py" for i in range(7)]
        graph = _make_graph_with_files(*files)
        result = _cl_path_fallback(graph, "handler")
        assert result == []

    def test_snake_case_decomposition(self):
        """config_from_object → tries "config", finds config.py."""
        graph = _make_graph_with_files("src/config.py", "src/utils.py")
        result = _cl_path_fallback(graph, "config_from_object")
        assert "src/config.py" in result

    def test_snake_case_skips_generic_parts(self):
        """Parts in _PATH_SNAKE_SKIP are ignored."""
        graph = _make_graph_with_files("src/router.py", "src/auth.py")
        # "router" is in the skip list, so it should skip it
        result = _cl_path_fallback(graph, "router_auth")
        # "auth" (not in skip list) should be tried → finds auth.py
        assert "src/auth.py" in result

    def test_camelcase_decomposition(self):
        """RequestAuthentication → tries "Request", "Authentication" etc."""
        graph = _make_graph_with_files("src/authentication.py", "src/utils.py")
        result = _cl_path_fallback(graph, "RequestAuthentication")
        assert "src/authentication.py" in result

    def test_test_files_excluded(self):
        """Files matching test markers are excluded from results."""
        graph = _make_graph_with_files("src/hasher.py", "tests/test_hasher.py")
        result = _cl_path_fallback(graph, "hasher")
        assert "src/hasher.py" in result
        assert "tests/test_hasher.py" not in result

    def test_no_match_returns_empty(self):
        graph = _make_graph_with_files("src/models.py")
        result = _cl_path_fallback(graph, "xyznonexistent")
        assert result == []

    def test_exact_5_files_ok(self):
        """Exactly 5 files should be returned (<=5 threshold)."""
        files = [f"src/auth_{i}.py" for i in range(5)]
        graph = _make_graph_with_files(*files)
        result = _cl_path_fallback(graph, "auth")
        assert len(result) == 5

    def test_6_files_returns_empty(self):
        """6 files exceeds threshold → falls through to other strategies."""
        files = [f"src/auth_{i}.py" for i in range(6)]
        graph = _make_graph_with_files(*files)
        result = _cl_path_fallback(graph, "auth")
        # No snake/camel fallbacks for "auth" (no _ or camel), so returns []
        assert result == []
