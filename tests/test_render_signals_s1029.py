"""S1029: Hot cluster at BFS depth 1 in focus mode.

When ≥2 depth-1 BFS neighbors of the focused symbol are in actively-modified
(hot) files, surface a synthesis note naming those files. The BFS sort key
also promotes hot-file symbols within each depth tier — making temporal
relevance first-class in the focus output.

Distinct from per-symbol [hot] tags (which were already present) — this gives
a cluster-level synthesis and explains the ordering change.
"""

from tempograph.builder import build_graph
from tempograph.render import render_focused


def _build(tmp_path, files: dict, hot_files: set = None):
    for name, content in files.items():
        (tmp_path / name).write_text(content)
    g = build_graph(str(tmp_path), use_cache=False)
    if hot_files is not None:
        # Inject hot_files for testing temporal signals without needing a git repo
        g.hot_files = hot_files
    return g


class TestHotClusterNote:
    """S1029: hot cluster note fires when ≥2 depth-1 neighbors are in hot files."""

    def test_fires_with_two_hot_depth1_neighbors(self, tmp_path):
        """Note appears when two callers of the seed are in hot files."""
        (tmp_path / "core.py").write_text("def seed():\n    pass\n")
        (tmp_path / "hot_a.py").write_text("from core import seed\ndef a():\n    seed()\n")
        (tmp_path / "hot_b.py").write_text("from core import seed\ndef b():\n    seed()\n")
        (tmp_path / "cold.py").write_text("from core import seed\ndef c():\n    seed()\n")
        g = _build(tmp_path, {}, hot_files={"hot_a.py", "hot_b.py"})
        out = render_focused(g, "seed")
        assert "hot cluster at depth 1" in out, f"Expected hot cluster note; got:\n{out}"

    def test_silent_with_only_one_hot_neighbor(self, tmp_path):
        """Note is suppressed when only one depth-1 neighbor is in a hot file."""
        (tmp_path / "core.py").write_text("def seed():\n    pass\n")
        (tmp_path / "hot_a.py").write_text("from core import seed\ndef a():\n    seed()\n")
        (tmp_path / "cold_b.py").write_text("from core import seed\ndef b():\n    seed()\n")
        g = _build(tmp_path, {}, hot_files={"hot_a.py"})
        out = render_focused(g, "seed")
        assert "hot cluster at depth 1" not in out, f"Unexpected hot cluster note; got:\n{out}"

    def test_silent_when_hot_files_empty(self, tmp_path):
        """Note is suppressed when hot_files is empty (non-git or no recent activity)."""
        (tmp_path / "core.py").write_text("def seed():\n    pass\n")
        (tmp_path / "a.py").write_text("from core import seed\ndef a():\n    seed()\n")
        (tmp_path / "b.py").write_text("from core import seed\ndef b():\n    seed()\n")
        g = _build(tmp_path, {}, hot_files=set())
        out = render_focused(g, "seed")
        assert "hot cluster at depth 1" not in out, f"Unexpected hot cluster note; got:\n{out}"

    def test_output_includes_hot_file_names(self, tmp_path):
        """Hot cluster note names the hot files for quick identification."""
        (tmp_path / "core.py").write_text("def seed():\n    pass\n")
        (tmp_path / "renderer.py").write_text("from core import seed\ndef render():\n    seed()\n")
        (tmp_path / "builder.py").write_text("from core import seed\ndef build():\n    seed()\n")
        g = _build(tmp_path, {}, hot_files={"renderer.py", "builder.py"})
        out = render_focused(g, "seed")
        assert "hot cluster at depth 1" in out
        # At least one of the hot file names should appear
        assert "renderer.py" in out or "builder.py" in out, \
            f"Expected hot file names in cluster note; got:\n{out}"

    def test_neighbor_count_in_output(self, tmp_path):
        """Output includes the count of hot depth-1 neighbors."""
        (tmp_path / "core.py").write_text("def seed():\n    pass\n")
        for i in range(3):
            (tmp_path / f"hot_{i}.py").write_text(
                f"from core import seed\ndef fn_{i}():\n    seed()\n"
            )
        (tmp_path / "cold.py").write_text("from core import seed\ndef cold_fn():\n    seed()\n")
        g = _build(tmp_path, {}, hot_files={f"hot_{i}.py" for i in range(3)})
        out = render_focused(g, "seed")
        assert "hot cluster at depth 1" in out
        assert "3 neighbors" in out, f"Expected count '3 neighbors'; got:\n{out}"

    def test_prioritized_label_in_output(self, tmp_path):
        """Note includes 'prioritized in BFS below' to explain ordering change."""
        (tmp_path / "core.py").write_text("def seed():\n    pass\n")
        (tmp_path / "hot_x.py").write_text("from core import seed\ndef x():\n    seed()\n")
        (tmp_path / "hot_y.py").write_text("from core import seed\ndef y():\n    seed()\n")
        g = _build(tmp_path, {}, hot_files={"hot_x.py", "hot_y.py"})
        out = render_focused(g, "seed")
        assert "prioritized in BFS below" in out, \
            f"Expected 'prioritized in BFS below' phrase; got:\n{out}"


class TestHotClusterBFSOrdering:
    """S1029: BFS sort key promotes hot-file neighbors before cold ones within same depth."""

    def test_hot_neighbor_before_cold_at_same_depth(self, tmp_path):
        """Hot-file depth-1 neighbor appears before cold depth-1 neighbor in output."""
        # Setup: seed called by both hot_caller and cold_caller
        # cold_caller has more cross-file callers (higher structural importance)
        # hot_caller is in a hot file (lower structural importance but temporal priority)
        (tmp_path / "core.py").write_text("def seed():\n    pass\n")
        (tmp_path / "hot_caller.py").write_text(
            "from core import seed\ndef hot_fn():\n    seed()\n"
        )
        (tmp_path / "cold_caller.py").write_text(
            "from core import seed\ndef cold_fn():\n    seed()\n"
        )
        # Give cold_caller more structural weight by adding callers to it
        for i in range(5):
            (tmp_path / f"extra_{i}.py").write_text(
                f"from cold_caller import cold_fn\ndef use_{i}():\n    cold_fn()\n"
            )
        g = _build(tmp_path, {}, hot_files={"hot_caller.py"})
        out = render_focused(g, "seed")
        # hot_caller should appear before cold_caller in the output
        hot_pos = out.find("hot_fn")
        cold_pos = out.find("cold_fn")
        assert hot_pos != -1 and cold_pos != -1, \
            f"Both symbols should appear in focus output; got:\n{out}"
        assert hot_pos < cold_pos, (
            f"hot_fn (in hot file) should appear before cold_fn "
            f"(hot_pos={hot_pos}, cold_pos={cold_pos})"
        )

    def test_cold_repo_ordering_unchanged(self, tmp_path):
        """When hot_files is empty, sort falls back to structural importance (no change)."""
        (tmp_path / "core.py").write_text("def seed():\n    pass\n")
        (tmp_path / "important.py").write_text(
            "from core import seed\ndef imp():\n    seed()\n"
        )
        (tmp_path / "minor.py").write_text(
            "from core import seed\ndef minor_fn():\n    seed()\n"
        )
        # important has more callers → higher structural importance
        for i in range(5):
            (tmp_path / f"ext_{i}.py").write_text(
                f"from important import imp\ndef use_{i}():\n    imp()\n"
            )
        g = _build(tmp_path, {}, hot_files=set())
        out = render_focused(g, "seed")
        imp_pos = out.find("imp")
        minor_pos = out.find("minor_fn")
        assert imp_pos != -1 and minor_pos != -1, \
            f"Both symbols should appear; got:\n{out}"
        assert imp_pos < minor_pos, (
            "Without hot_files, structural importance should order imp before minor_fn "
            f"(imp_pos={imp_pos}, minor_pos={minor_pos})"
        )
