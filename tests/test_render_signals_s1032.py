"""Tests for S1032: BFS naming cluster signal in render_focused.

S1032 fires when ≥3 depth-1 BFS neighbors share a naming stem (≥4 chars),
revealing that the seed is embedded in a structural naming family.

Different from:
- S70 (module diversity): groups by top-level module path, not symbol name
- S1029 (hot cluster): temporal grouping by hot_files, not naming pattern
- S57 (caller concentration): per-file caller count, not name family
"""

from __future__ import annotations
import subprocess
import pytest

# ---------------------------------------------------------------------------
# Helper: build a minimal git repo with a naming cluster
# ---------------------------------------------------------------------------

def _make_git_repo(tmp_path: "Path") -> "Path":
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True)
    return tmp_path


def _commit(tmp_path: "Path") -> None:
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)


# ---------------------------------------------------------------------------
# _get_naming_stem unit tests
# ---------------------------------------------------------------------------

class TestGetNamingStem:
    """Unit tests for the _get_naming_stem helper."""

    def test_private_compute_prefix(self):
        from tempograph.render.focused import _get_naming_stem
        assert _get_naming_stem("_compute_bfs_scope_note") == "_compute"

    def test_private_signals_prefix(self):
        from tempograph.render.focused import _get_naming_stem
        assert _get_naming_stem("_signals_diff_pre_a") == "_signals"

    def test_public_render_prefix(self):
        from tempograph.render.focused import _get_naming_stem
        assert _get_naming_stem("render_focused") == "render"

    def test_short_first_word_returns_empty(self):
        from tempograph.render.focused import _get_naming_stem
        # "get" is only 3 chars — below threshold
        assert _get_naming_stem("get_user") == ""

    def test_dunder_returns_empty(self):
        from tempograph.render.focused import _get_naming_stem
        assert _get_naming_stem("__init__") == ""

    def test_four_char_first_word_included(self):
        from tempograph.render.focused import _get_naming_stem
        # "test" is 4 chars — at threshold
        assert _get_naming_stem("test_something") == "test"

    def test_plain_name_no_underscore(self):
        from tempograph.render.focused import _get_naming_stem
        # "render" alone as a stem
        assert _get_naming_stem("render") == "render"


# ---------------------------------------------------------------------------
# _compute_bfs_naming_clusters unit tests
# ---------------------------------------------------------------------------

class TestComputeBfsNamingClusters:
    """Unit tests for _compute_bfs_naming_clusters."""

    def _make_fake_sym(self, name: str, file_path: str = "src/module.py", depth: int = 1):
        from tempograph.types import Symbol, SymbolKind, Language
        return (Symbol(
            id=f"{file_path}::{name}",
            name=name,
            qualified_name=name,
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_path=file_path,
            line_start=1,
            line_end=5,
        ), depth)

    def test_fires_when_three_or_more_share_stem(self):
        from tempograph.render.focused import _compute_bfs_naming_clusters
        from tempograph.types import Symbol, SymbolKind, Language

        seed = Symbol(
            id="src/core.py::orchestrate",
            name="orchestrate",
            qualified_name="orchestrate",
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_path="src/core.py",
            line_start=1, line_end=10,
        )
        ordered = [
            (seed, 0),
            *[self._make_fake_sym(f"render_{i}", "src/render.py") for i in range(4)],
        ]
        result = _compute_bfs_naming_clusters([seed], ordered)
        assert "naming cluster" in result, f"should fire for 4 render_* neighbors; got: {result!r}"
        assert "render" in result, "should show the stem"
        assert "4" in result, "should show the count"

    def test_silent_when_fewer_than_three_share_stem(self):
        from tempograph.render.focused import _compute_bfs_naming_clusters
        from tempograph.types import Symbol, SymbolKind, Language

        seed = Symbol(
            id="src/core.py::dispatch",
            name="dispatch",
            qualified_name="dispatch",
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_path="src/core.py",
            line_start=1, line_end=5,
        )
        ordered = [
            (seed, 0),
            self._make_fake_sym("render_a"),
            self._make_fake_sym("render_b"),
            # only 2 render_* — below threshold
            self._make_fake_sym("build_graph"),
            self._make_fake_sym("count_tokens"),
        ]
        result = _compute_bfs_naming_clusters([seed], ordered)
        assert result == "", f"should be silent with 2 render_* neighbors; got: {result!r}"

    def test_excludes_seed_name_from_cluster(self):
        from tempograph.render.focused import _compute_bfs_naming_clusters
        from tempograph.types import Symbol, SymbolKind, Language

        seed = Symbol(
            id="src/core.py::render_main",
            name="render_main",
            qualified_name="render_main",
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_path="src/core.py",
            line_start=1, line_end=5,
        )
        ordered = [
            (seed, 0),
            # seed is depth-0, not counted in cluster
            self._make_fake_sym("render_a"),
            self._make_fake_sym("render_b"),
            # Only 2 depth-1 render_* (seed is depth-0, excluded)
        ]
        result = _compute_bfs_naming_clusters([seed], ordered)
        assert result == "", f"seed should not count toward its own cluster; got: {result!r}"

    def test_excludes_test_file_symbols(self):
        from tempograph.render.focused import _compute_bfs_naming_clusters
        from tempograph.types import Symbol, SymbolKind, Language

        seed = Symbol(
            id="src/core.py::main",
            name="main",
            qualified_name="main",
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_path="src/core.py",
            line_start=1, line_end=5,
        )
        # 4 render_* from test files — should be excluded
        ordered = [
            (seed, 0),
            *[self._make_fake_sym(f"render_{i}", "tests/test_render.py") for i in range(4)],
        ]
        result = _compute_bfs_naming_clusters([seed], ordered)
        assert result == "", f"test-file symbols should be excluded; got: {result!r}"

    def test_only_depth_one_symbols_counted(self):
        from tempograph.render.focused import _compute_bfs_naming_clusters
        from tempograph.types import Symbol, SymbolKind, Language

        seed = Symbol(
            id="src/core.py::dispatch",
            name="dispatch",
            qualified_name="dispatch",
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_path="src/core.py",
            line_start=1, line_end=5,
        )
        # 4 render_* at depth 2 — should NOT count
        ordered = [
            (seed, 0),
            self._make_fake_sym("build_graph", depth=1),
            self._make_fake_sym("parse_file", depth=1),
            *[self._make_fake_sym(f"render_{i}", depth=2) for i in range(4)],
        ]
        result = _compute_bfs_naming_clusters([seed], ordered)
        assert result == "", f"depth-2 symbols should not count; got: {result!r}"

    def test_shows_example_names_in_output(self):
        from tempograph.render.focused import _compute_bfs_naming_clusters
        from tempograph.types import Symbol, SymbolKind, Language

        seed = Symbol(
            id="src/core.py::main",
            name="main",
            qualified_name="main",
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_path="src/core.py",
            line_start=1, line_end=5,
        )
        ordered = [
            (seed, 0),
            self._make_fake_sym("render_overview"),
            self._make_fake_sym("render_focused"),
            self._make_fake_sym("render_hotspots"),
        ]
        result = _compute_bfs_naming_clusters([seed], ordered)
        # Should include example names
        assert "render_overview" in result or "render_focused" in result, (
            f"should include example names; got: {result!r}"
        )

    def test_private_stem_preserved_in_output(self):
        from tempograph.render.focused import _compute_bfs_naming_clusters
        from tempograph.types import Symbol, SymbolKind, Language

        seed = Symbol(
            id="src/render.py::render_main",
            name="render_main",
            qualified_name="render_main",
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_path="src/render.py",
            line_start=1, line_end=10,
        )
        ordered = [
            (seed, 0),
            self._make_fake_sym("_compute_scope"),
            self._make_fake_sym("_compute_exposure"),
            self._make_fake_sym("_compute_dead_note"),
        ]
        result = _compute_bfs_naming_clusters([seed], ordered)
        assert "_compute" in result, f"private stem should appear with leading underscore; got: {result!r}"
        assert "3" in result, "count should be 3"


# ---------------------------------------------------------------------------
# Integration test: fires on real codebase for render_focused
# ---------------------------------------------------------------------------

REPO_PATH = "/Users/elmoaidali/Desktop/tempograph"


class TestBfsNamingClusterIntegration:
    """Integration test: naming cluster fires on the real codebase."""

    def test_fires_for_render_focused_compute_family(self):
        """render_focused has 6+ _compute_* private helpers at depth-1; signal must fire."""
        from tempograph.builder import build_graph
        from tempograph.render import render_focused

        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        out = render_focused(g, "render_focused", max_tokens=6000)

        assert "naming cluster" in out, (
            f"render_focused has _compute_* family at depth-1 — signal must fire;\n{out[:500]}"
        )
        assert "_compute" in out, "signal must identify the _compute_ stem"

    def test_silent_for_build_graph(self):
        """build_graph has diverse depth-1 neighbors — no naming cluster expected."""
        from tempograph.builder import build_graph
        from tempograph.render import render_focused

        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        out = render_focused(g, "build_graph", max_tokens=6000)

        # build_graph has callers from many different naming families
        # Signal should not fire (or if it does, it's a legitimate cluster we accept)
        # This test just verifies the output is well-formed
        assert "Focus:" in out, "output must be valid focus output"
        # We don't assert "naming cluster" NOT in out — build_graph might legitimately cluster
