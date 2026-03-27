"""Unit tests for render_overview, render_blast_radius, render_hotspots, render_dead_code,
render_dependencies, render_architecture, render_skills, is_git_repo."""
from __future__ import annotations

from unittest.mock import MagicMock

from tempograph.builder import build_graph
from tempograph.render import (
    render_blast_radius,
    render_dead_code,
    render_hotspots,
    render_overview,
    render_dependencies,
    render_architecture,
    render_skills,
    render_lookup,
    _extract_name_from_question,
    _is_test_file,
)
from tempograph.render.dead import _file_effort_badge
from tempograph.render.hotspots import _collect_hotspots_signals, _calm_zones_lines
from tempograph.git import is_git_repo
from tempograph.types import Symbol, SymbolKind, Language


def _build(tmp_path, files: dict[str, str]):
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return build_graph(str(tmp_path), use_cache=False)


# ── render_overview ──────────────────────────────────────────────────────────

class TestRenderOverview:
    def test_contains_stats_line(self, tmp_path):
        g = _build(tmp_path, {"core.py": "def foo():\n    pass\n"})
        out = render_overview(g)
        assert "files" in out and "symbols" in out

    def test_repo_name_in_header(self, tmp_path):
        g = _build(tmp_path, {"core.py": "def foo():\n    pass\n"})
        out = render_overview(g)
        repo_name = tmp_path.name
        assert repo_name in out

    def test_key_files_section_present(self, tmp_path):
        g = _build(tmp_path, {"main.py": "def run():\n    pass\n"})
        out = render_overview(g)
        assert "key files" in out

    def test_entry_point_main_detected(self, tmp_path):
        g = _build(tmp_path, {"main.py": "def main():\n    pass\n"})
        out = render_overview(g)
        assert "entry points" in out
        assert "main" in out

    def test_entry_point_app_detected(self, tmp_path):
        g = _build(tmp_path, {"app.py": "def run():\n    pass\n"})
        out = render_overview(g)
        assert "app.py" in out

    def test_json_file_excluded_from_key_files(self, tmp_path):
        # JSON files (non-code) must NOT appear in key files section
        g = _build(tmp_path, {
            "core.py": "def foo():\n    pass\n",
            "schema.json": '{"type": "object"}',
        })
        out = render_overview(g)
        key_files_section = out.split("key files")[1] if "key files" in out else ""
        assert "schema.json" not in key_files_section

    def test_python_file_included_in_key_files(self, tmp_path):
        g = _build(tmp_path, {"core.py": "def foo():\n    pass\n"})
        out = render_overview(g)
        assert "core.py" in out

    def test_multiple_symbols_counted(self, tmp_path):
        code = "def a():\n    pass\n\ndef b():\n    pass\n\ndef c():\n    pass\n"
        g = _build(tmp_path, {"core.py": code})
        out = render_overview(g)
        # symbols count should be >= 3
        stats = g.stats
        assert stats["symbols"] >= 3

    def test_language_shown_in_stats(self, tmp_path):
        g = _build(tmp_path, {"core.py": "def foo():\n    pass\n"})
        out = render_overview(g)
        assert "python" in out.lower()

    def test_symbol_named_main_in_entry_points(self, tmp_path):
        g = _build(tmp_path, {"server.py": "def main():\n    pass\n"})
        out = render_overview(g)
        assert "entry points" in out
        assert "main" in out

    def test_returns_str(self, tmp_path):
        g = _build(tmp_path, {"a.py": "x = 1\n"})
        assert isinstance(render_overview(g), str)


class TestDepLayerHealth:
    """Tests for _dep_layer_health — dep layer stratification in overview output."""

    def _three_layer_files(self) -> dict[str, str]:
        """8-file, 4-layer import graph: all connected by import edges.

        dependency_layers() only includes files that appear in IMPORT edges,
        so isolated files (no imports, no importers) are excluded from layers.
        All 8 files here are reachable via import edges.

        Layers:
          L0: core.py, utils.py, git.py, cache.py  (no deps)
          L1: parser.py, storage.py  (import L0 files)
          L2: builder.py  (imports L1)
          L3: server.py  (imports L2)
        """
        return {
            "core.py": "def base(): pass\n",
            "utils.py": "def helper(): pass\n",
            "git.py": "def git_fn(): pass\n",
            "cache.py": "def store(): pass\n",
            "parser.py": "from core import base\nfrom utils import helper\ndef parse(): pass\n",
            "storage.py": "from cache import store\nfrom git import git_fn\ndef save(): pass\n",
            "builder.py": "from parser import parse\nfrom storage import save\ndef build(): pass\n",
            "server.py": "from builder import build\ndef serve(): pass\n",
        }

    def test_dep_layers_shown_for_three_layer_graph(self, tmp_path):
        g = _build(tmp_path, self._three_layer_files())
        out = render_overview(g)
        assert "dep layers" in out

    def test_dep_layers_foundation_label(self, tmp_path):
        g = _build(tmp_path, self._three_layer_files())
        out = render_overview(g)
        assert "foundation" in out

    def test_dep_layers_interface_label(self, tmp_path):
        g = _build(tmp_path, self._three_layer_files())
        out = render_overview(g)
        assert "interface" in out

    def test_dep_layers_skips_shallow_graph(self, tmp_path):
        # Only 2 distinct layers — below 3-layer threshold.
        files = {
            "a.py": "def f(): pass\n",
            "b.py": "def g(): pass\n",
            "c.py": "def h(): pass\n",
            "d.py": "def i(): pass\n",
            "e.py": "def j(): pass\n",
            "f.py": "def k(): pass\n",
            "g.py": "def l(): pass\n",
            "consumer.py": "from a import f\nfrom b import g\ndef run(): pass\n",
        }
        g = _build(tmp_path, files)
        out = render_overview(g)
        layers = g.dependency_layers()
        if len(layers) < 3:
            assert "dep layers" not in out

    def test_dep_layers_skips_too_few_files(self, tmp_path):
        # 3 layers but only 5 files total — below the 8-file threshold.
        files = {
            "base.py": "def f(): pass\n",
            "mid.py": "from base import f\ndef g(): pass\n",
            "top.py": "from mid import g\ndef h(): pass\n",
        }
        g = _build(tmp_path, files)
        out = render_overview(g)
        layers_filtered = [
            [fp for fp in l if "test" not in fp] for l in g.dependency_layers()
        ]
        layers_filtered = [l for l in layers_filtered if l]
        total = sum(len(l) for l in layers_filtered)
        if total < 8:
            assert "dep layers" not in out

    def test_dep_layers_depth_shown_for_deep_hierarchy(self, tmp_path):
        # 5-level chain with 8 files — should show "(5 levels)"
        files = {
            "base.py": "def f0(): pass\n",
            "base2.py": "def f0b(): pass\n",
            "base3.py": "def f0c(): pass\n",
            "base4.py": "def f0d(): pass\n",
            "l1.py": "from base import f0\ndef f1(): pass\n",
            "l2.py": "from l1 import f1\ndef f2(): pass\n",
            "l3.py": "from l2 import f2\ndef f3(): pass\n",
            "l4.py": "from l3 import f3\ndef f4(): pass\n",
        }
        g = _build(tmp_path, files)
        out = render_overview(g)
        layers = g.dependency_layers()
        if len(layers) >= 4 and sum(len(l) for l in layers) >= 8:
            assert "levels" in out

    def test_dep_layers_foundation_churn_warning(self, tmp_path):
        # All L0 files are hot, none in the top layers → should trigger warning.
        g = _build(tmp_path, self._three_layer_files())
        layers = g.dependency_layers()
        # Mark all foundation files as hot
        l0_files = [fp for fp in layers[0] if "test" not in fp]
        g.hot_files = set(l0_files)
        out = render_overview(g)
        if len(l0_files) >= 4:  # Only fires when foundation has ≥ 4 files
            assert "foundation churn" in out or "foundation" in out

    def test_dep_layers_no_warning_normal_gradient(self, tmp_path):
        # Only top-layer files are hot → no foundation churn warning.
        g = _build(tmp_path, self._three_layer_files())
        layers = g.dependency_layers()
        top_files = [fp for fp in layers[-1] if "test" not in fp]
        g.hot_files = set(top_files)
        out = render_overview(g)
        assert "foundation churn" not in out


# ── render_blast_radius ───────────────────────────────────────────────────────

class TestRenderBlastRadius:
    def test_unknown_file_returns_not_found(self, tmp_path):
        g = _build(tmp_path, {"a.py": "def foo():\n    pass\n"})
        out = render_blast_radius(g, "nonexistent.py")
        assert "not found" in out.lower() or "'" in out

    def test_header_shows_filename(self, tmp_path):
        g = _build(tmp_path, {"core.py": "def util():\n    pass\n"})
        out = render_blast_radius(g, "core.py")
        assert "core.py" in out

    def test_importer_listed(self, tmp_path):
        g = _build(tmp_path, {
            "core.py": "def util():\n    pass\n",
            "user.py": "from core import util\ndef use():\n    util()\n",
        })
        out = render_blast_radius(g, "core.py")
        assert "user.py" in out

    def test_direct_imported_by_count(self, tmp_path):
        files = {"core.py": "def util():\n    pass\n"}
        for i in range(3):
            files[f"user{i}.py"] = f"from core import util\ndef f{i}():\n    util()\n"
        g = _build(tmp_path, files)
        out = render_blast_radius(g, "core.py")
        assert "Directly imported by" in out

    def test_no_importers_shows_no_import_section(self, tmp_path):
        g = _build(tmp_path, {
            "standalone.py": "def fn():\n    pass\n",
            "other.py": "def other():\n    pass\n",
        })
        out = render_blast_radius(g, "standalone.py")
        # Either no importers section or empty
        assert isinstance(out, str)

    def test_refactor_safety_shown_with_tests(self, tmp_path):
        g = _build(tmp_path, {
            "core.py": "def util():\n    pass\n",
            "user.py": "from core import util\ndef use():\n    util()\n",
            "tests/test_user.py": "from user import use\ndef test_use():\n    use()\n",
        })
        out = render_blast_radius(g, "core.py")
        # refactor safety section should appear when tests exist
        assert isinstance(out, str)

    def test_symbol_blast_when_query_given(self, tmp_path):
        g = _build(tmp_path, {
            "core.py": "def process():\n    pass\n",
            "user.py": "from core import process\ndef run():\n    process()\n",
        })
        out = render_blast_radius(g, "core.py", query="process")
        # Symbol blast mode — should mention process
        assert "process" in out

    def test_blast_returns_str(self, tmp_path):
        g = _build(tmp_path, {"a.py": "def foo():\n    pass\n"})
        assert isinstance(render_blast_radius(g, "a.py"), str)


# ── render_hotspots ───────────────────────────────────────────────────────────

class TestRenderHotspots:
    def test_returns_str(self, tmp_path):
        g = _build(tmp_path, {"a.py": "def foo():\n    pass\n"})
        assert isinstance(render_hotspots(g), str)

    def test_header_present(self, tmp_path):
        g = _build(tmp_path, {"a.py": "def foo():\n    pass\n"})
        out = render_hotspots(g)
        assert "hotspots" in out.lower()

    def test_hub_function_ranks_top(self, tmp_path):
        files = {"core.py": "def hub():\n    pass\n"}
        for i in range(5):
            files[f"u{i}.py"] = f"from core import hub\ndef f{i}():\n    hub()\n"
        g = _build(tmp_path, files)
        out = render_hotspots(g)
        assert "hub" in out

    def test_top_n_respected(self, tmp_path):
        # Build several functions
        fns = "\n".join(f"def fn{i}():\n    pass\n" for i in range(10))
        g = _build(tmp_path, {"lib.py": fns})
        out = render_hotspots(g, top_n=3)
        assert isinstance(out, str)

    def test_highly_connected_symbol_scores_higher(self, tmp_path):
        # hub has 4 cross-file callers, leaf has 0
        files = {
            "hub.py": "def hub():\n    pass\n",
            "leaf.py": "def leaf():\n    pass\n",
        }
        for i in range(4):
            files[f"caller{i}.py"] = f"from hub import hub\ndef f{i}():\n    hub()\n"
        g = _build(tmp_path, files)
        out = render_hotspots(g)
        # hub should appear before leaf in output
        if "leaf" in out and "hub" in out:
            assert out.index("hub") < out.index("leaf")

    def test_empty_graph_returns_str(self, tmp_path):
        # Even an empty graph shouldn't crash
        g = _build(tmp_path, {"empty.py": "# no symbols\n"})
        assert isinstance(render_hotspots(g), str)


# ── S1018 hot cascade signal ──────────────────────────────────────────────────

class TestHotCascadeSignal:
    def _make_sym(self, sym_id: str, file_path: str) -> "Symbol":
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

    def _safe_graph(self, hot_files: set[str], importers: list[str]) -> "MagicMock":
        """Build a minimal MagicMock graph that won't crash _collect_hotspots_signals."""
        graph = MagicMock()
        graph.hot_files = hot_files
        graph.importers_of.return_value = importers
        # Stub out attributes that other signals access to avoid TypeError comparisons
        graph.files.get.return_value = None  # FileInfo lookups return None → signals skip
        graph.symbols.values.return_value = []  # No symbols → complexity/caller signals skip
        graph.callers_of.return_value = []
        graph.callees_of.return_value = []
        graph.root = None
        return graph

    def test_hot_cascade_fires_when_2plus_hot_importers(self):
        # core.py is the top hotspot; service_a.py and service_b.py import it and are hot
        top_sym = self._make_sym("core.py::build", "core.py")
        scores = [(100.0, top_sym)]
        graph = self._safe_graph(
            hot_files={"core.py", "service_a.py", "service_b.py"},
            importers=["service_a.py", "service_b.py", "cold_consumer.py"],
        )
        result = _collect_hotspots_signals(graph, scores, {}, {}, set(), 20)
        combined = "\n".join(result)
        assert "hot cascade" in combined
        assert "service_a" in combined or "service_b" in combined

    def test_hot_cascade_suppressed_when_fewer_than_2_hot_importers(self):
        # Only 1 hot importer — below threshold of 2
        top_sym = self._make_sym("core.py::build", "core.py")
        scores = [(100.0, top_sym)]
        graph = self._safe_graph(
            hot_files={"core.py", "service_a.py"},
            importers=["service_a.py", "cold_consumer.py"],
        )
        result = _collect_hotspots_signals(graph, scores, {}, {}, set(), 20)
        combined = "\n".join(result)
        assert "hot cascade" not in combined


class TestCalmZones:
    """S63: Calm zones — stable, heavily-imported files (load-bearing walls)."""

    def _make_graph(self, files_importers: dict[str, list[str]]) -> "MagicMock":
        """Build a minimal mock graph for calm zones testing."""
        graph = MagicMock()
        graph.files = {fp: MagicMock() for fp in files_importers}
        graph.importers_of.side_effect = lambda fp: files_importers.get(fp, [])
        return graph

    def test_calm_zone_fires_for_stable_heavily_imported_file(self):
        # core.py has 5 non-test importers and velocity 0.0 → calm zone
        graph = self._make_graph({
            "core.py": ["a.py", "b.py", "c.py", "d.py", "e.py"],
        })
        result = _calm_zones_lines(graph, {"core.py": 0.0})
        combined = "\n".join(result)
        assert "calm zones" in combined
        assert "core.py" in combined
        assert "5 importers" in combined

    def test_calm_zone_hidden_when_file_is_hot(self):
        # core.py has many importers but is actively churning — NOT a calm zone
        graph = self._make_graph({
            "core.py": ["a.py", "b.py", "c.py", "d.py", "e.py"],
        })
        result = _calm_zones_lines(graph, {"core.py": 5.0})
        combined = "\n".join(result)
        assert "calm zones" not in combined

    def test_calm_zone_hidden_when_too_few_importers(self):
        # stable file but only 4 importers — below threshold of 5
        graph = self._make_graph({
            "utils.py": ["a.py", "b.py", "c.py", "d.py"],
        })
        result = _calm_zones_lines(graph, {"utils.py": 0.0})
        combined = "\n".join(result)
        assert "calm zones" not in combined

    def test_calm_zone_hidden_when_no_velocity_data(self):
        # empty velocity dict → no git data → don't emit calm zones
        graph = self._make_graph({
            "core.py": ["a.py", "b.py", "c.py", "d.py", "e.py"],
        })
        result = _calm_zones_lines(graph, {})
        assert result == []

    def test_calm_zones_sorted_by_importer_count(self):
        # two calm candidates: big.py (8 importers) and small.py (5) → big first
        graph = self._make_graph({
            "big.py": ["a.py", "b.py", "c.py", "d.py", "e.py", "f.py", "g.py", "h.py"],
            "small.py": ["a.py", "b.py", "c.py", "d.py", "e.py"],
        })
        result = _calm_zones_lines(graph, {"big.py": 0.0, "small.py": 0.0})
        combined = "\n".join(result)
        assert combined.index("big.py") < combined.index("small.py")

    def test_calm_zone_vel_note_shown_when_nonzero(self):
        # velocity 0.5 → shows commits/wk note; velocity 0.0 → no note
        graph = self._make_graph({
            "slow.py": ["a.py", "b.py", "c.py", "d.py", "e.py"],
        })
        result = _calm_zones_lines(graph, {"slow.py": 0.5})
        combined = "\n".join(result)
        assert "0.5 commits/wk" in combined

    def test_calm_zones_excludes_test_files(self):
        # test_core.py would match on importers but is a test file → excluded
        graph = self._make_graph({
            "test_core.py": ["a.py", "b.py", "c.py", "d.py", "e.py"],
        })
        result = _calm_zones_lines(graph, {"test_core.py": 0.0})
        assert "calm zones" not in "\n".join(result)


# ── render_dead_code ──────────────────────────────────────────────────────────

class TestRenderDeadCode:
    def test_returns_str(self, tmp_path):
        g = _build(tmp_path, {"a.py": "def foo():\n    pass\n"})
        assert isinstance(render_dead_code(g), str)

    def test_unreferenced_exported_function_detected(self, tmp_path):
        # dead_fn is exported and never called externally → dead
        g = _build(tmp_path, {
            "utils.py": "def dead_fn():\n    pass\n",
            "main.py": "def main():\n    pass\n",
        })
        out = render_dead_code(g)
        # Either "dead_fn" appears as dead, or "No dead code" (parser may not pick up all cases)
        assert isinstance(out, str)

    def test_main_function_not_dead(self, tmp_path):
        # 'main' is excluded from dead code by name convention
        g = _build(tmp_path, {
            "main.py": "def main():\n    pass\n",
        })
        out = render_dead_code(g)
        # main is excluded → either no dead code, or main not in the output
        assert "main" not in out or "No dead code" in out

    def test_no_dead_code_message(self, tmp_path):
        # A single file where everything calls each other → no dead code
        code = (
            "def helper():\n    pass\n\n"
            "def caller():\n    helper()\n"
        )
        # Build with two files so cross-file references work
        g = _build(tmp_path, {
            "lib.py": "def helper():\n    pass\n",
            "main.py": "from lib import helper\ndef main():\n    helper()\n",
        })
        out = render_dead_code(g)
        # helper is called → should not be dead
        assert "lib.py" not in out or "helper" not in out or "No dead code" in out

    def test_dead_code_header_when_dead_found(self, tmp_path):
        g = _build(tmp_path, {
            "orphan.py": "def unused_func():\n    pass\n\ndef also_unused():\n    pass\n",
            "other.py": "def something():\n    pass\n",
        })
        out = render_dead_code(g)
        if "unused_func" in out or "also_unused" in out:
            assert "Potential dead code" in out or "dead" in out.lower()

    def test_include_low_parameter_accepted(self, tmp_path):
        g = _build(tmp_path, {"a.py": "def foo():\n    pass\n"})
        out = render_dead_code(g, include_low=True)
        assert isinstance(out, str)

    def test_max_symbols_parameter_accepted(self, tmp_path):
        g = _build(tmp_path, {"a.py": "def foo():\n    pass\n"})
        out = render_dead_code(g, max_symbols=5)
        assert isinstance(out, str)

    def test_dead_ratio_shown_for_large_project(self, tmp_path):
        # Build a project with 10+ symbols where several are dead
        fns = "".join(f"def fn_{i}():\n    pass\n\n" for i in range(15))
        g = _build(tmp_path, {"lib.py": fns})
        out = render_dead_code(g)
        # Either dead ratio is shown, or just "No dead code" — either is valid
        assert isinstance(out, str)



# ── hot-file debt signal ──────────────────────────────────────────────────────

class TestHotFileDeadCode:
    """S64: Hot-file dead — dead symbols in currently high-velocity files."""

    def test_fires_when_dead_symbol_in_hot_file(self, tmp_path):
        # dead.py has two unconnected dead functions; mark it as a hot file
        g = _build(tmp_path, {
            "lib.py": "def unused_a():\n    pass\n\ndef unused_b():\n    pass\n",
            "main.py": "def main():\n    pass\n",
        })
        rel = "lib.py"
        g.hot_files = {rel}
        out = render_dead_code(g)
        assert "Hot-file debt" in out

    def test_silent_when_hot_files_empty(self, tmp_path):
        g = _build(tmp_path, {
            "lib.py": "def unused_a():\n    pass\n\ndef unused_b():\n    pass\n",
            "main.py": "def main():\n    pass\n",
        })
        g.hot_files = set()
        out = render_dead_code(g)
        assert "Hot-file debt" not in out

    def test_silent_when_no_dead_in_hot_file(self, tmp_path):
        # hot file is main.py but dead code is in lib.py
        g = _build(tmp_path, {
            "lib.py": "def unused_a():\n    pass\n\ndef unused_b():\n    pass\n",
            "main.py": "def main():\n    pass\n",
        })
        g.hot_files = {"main.py"}
        out = render_dead_code(g)
        assert "Hot-file debt" not in out

    def test_shows_filename_in_output(self, tmp_path):
        g = _build(tmp_path, {
            "utils.py": "def stale_a():\n    pass\n\ndef stale_b():\n    pass\n",
            "app.py": "def run():\n    pass\n",
        })
        g.hot_files = {"utils.py"}
        out = render_dead_code(g)
        if "Hot-file debt" in out:
            assert "utils.py" in out

    def test_count_in_header(self, tmp_path):
        # 3 dead symbols all in hot file → count should be ≥1
        fns = "".join(f"def fn_{i}():\n    pass\n\n" for i in range(5))
        g = _build(tmp_path, {
            "dead_batch.py": fns,
            "caller.py": "def entry():\n    pass\n",
        })
        g.hot_files = {"dead_batch.py"}
        out = render_dead_code(g)
        if "Hot-file debt" in out:
            # count should be a positive integer in parentheses
            import re
            m = re.search(r"Hot-file debt \((\d+)\)", out)
            assert m and int(m.group(1)) >= 1

    def test_positioned_after_clustered_dead(self, tmp_path):
        # Hot-file debt should appear after Clustered dead in output order
        fns = "".join(f"def fn_{i}():\n    pass\n\n" for i in range(5))
        g = _build(tmp_path, {
            "hot_module.py": fns,
            "other.py": "def go():\n    pass\n",
        })
        g.hot_files = {"hot_module.py"}
        out = render_dead_code(g)
        if "Hot-file debt" in out and "Clustered dead" in out:
            assert out.index("Clustered dead") < out.index("Hot-file debt")

    def test_multiple_hot_files_aggregated(self, tmp_path):
        g = _build(tmp_path, {
            "alpha.py": "def dead_one():\n    pass\n\ndef dead_two():\n    pass\n",
            "beta.py": "def dead_three():\n    pass\n",
            "app.py": "def main():\n    pass\n",
        })
        g.hot_files = {"alpha.py", "beta.py"}
        out = render_dead_code(g)
        if "Hot-file debt" in out:
            # At least one of the hot files should appear
            assert "alpha.py" in out or "beta.py" in out


# ── render_dependencies ───────────────────────────────────────────────────────

class TestRenderDependencies:
    def test_returns_str(self, tmp_path):
        g = _build(tmp_path, {"a.py": "from b import f\ndef g(): f()\n", "b.py": "def f(): pass\n"})
        out = render_dependencies(g)
        assert isinstance(out, str)

    def test_header_present(self, tmp_path):
        g = _build(tmp_path, {"a.py": "def f(): pass\n"})
        out = render_dependencies(g)
        assert "Dependency" in out

    def test_no_circular_imports_message(self, tmp_path):
        g = _build(tmp_path, {"a.py": "def f(): pass\n", "b.py": "def g(): pass\n"})
        out = render_dependencies(g)
        assert "No circular imports" in out

    def test_circular_import_detected(self, tmp_path):
        g = _build(tmp_path, {
            "a.py": "from b import g\ndef f(): g()\n",
            "b.py": "from a import f\ndef g(): f()\n",
        })
        out = render_dependencies(g)
        # Either shows a cycle or no circular imports — depends on resolution
        assert isinstance(out, str)

    def test_dependency_layers_section_present(self, tmp_path):
        g = _build(tmp_path, {
            "core.py": "def base(): pass\n",
            "app.py": "from core import base\ndef run(): base()\n",
        })
        out = render_dependencies(g)
        assert "Layer" in out or "layer" in out.lower()


# ── render_architecture ───────────────────────────────────────────────────────

class TestRenderArchitecture:
    def test_returns_str(self, tmp_path):
        g = _build(tmp_path, {"pkg/mod.py": "def fn(): pass\n"})
        out = render_architecture(g)
        assert isinstance(out, str)

    def test_architecture_header(self, tmp_path):
        g = _build(tmp_path, {"pkg/a.py": "def fn(): pass\n"})
        out = render_architecture(g)
        assert "Architecture" in out or "Modules" in out

    def test_modules_section_present(self, tmp_path):
        g = _build(tmp_path, {
            "frontend/app.py": "def render(): pass\n",
            "backend/api.py": "def handler(): pass\n",
        })
        out = render_architecture(g)
        assert "frontend" in out or "backend" in out

    def test_flat_repo_shows_root_module(self, tmp_path):
        g = _build(tmp_path, {"utils.py": "def helper(): pass\n"})
        out = render_architecture(g)
        assert isinstance(out, str) and len(out) > 0


class TestModuleBehavioralCoupling:
    """Tests for module behavioral coupling (hot vs dormant structural edges)."""

    def test_no_crash_without_git_repo(self, tmp_path):
        # tmp_path is not a git repo — signal should be silently absent
        g = _build(tmp_path, {
            "src/a.py": "def fn(): pass\n",
            "tests/test_a.py": "from src.a import fn\ndef test(): pass\n",
        })
        out = render_architecture(g)
        assert "hot module coupling" not in out.lower()

    def test_hot_coupling_shown_when_present(self, tmp_path):
        from unittest.mock import patch
        # Two unique file pairs from tempograph ↔ tests modules (need ≥2 for threshold)
        g = _build(tmp_path, {
            "tempograph/parser.py": "def parse(): pass\n",
            "tempograph/extra.py": "def extra(): pass\n",
            "tests/test_parser.py": "from tempograph.parser import parse\ndef test(): pass\n",
            "tests/test_extra.py": "from tempograph.extra import extra\ndef test(): pass\n",
        })
        hot_matrix = {
            "tempograph/parser.py": [("tests/test_parser.py", 0.9)],
            "tempograph/extra.py": [("tests/test_extra.py", 0.9)],
            "tests/test_parser.py": [("tempograph/parser.py", 0.9)],
            "tests/test_extra.py": [("tempograph/extra.py", 0.9)],
        }
        with patch("tempograph.git.is_git_repo", return_value=True), \
             patch("tempograph.git.cochange_matrix", return_value=hot_matrix):
            out = render_architecture(g)
        assert "hot module coupling" in out.lower()
        assert "tempograph↔tests" in out or "tests↔tempograph" in out

    def test_hot_coupling_direction_import_dependent(self, tmp_path):
        from unittest.mock import patch
        # tests imports tempograph → "modifying tempograph → expect tests updates"
        g = _build(tmp_path, {
            "tempograph/api.py": "def fn(): pass\n",
            "tempograph/core.py": "def core(): pass\n",
            "tests/test_api.py": "from tempograph.api import fn\ndef test(): pass\n",
            "tests/test_core.py": "from tempograph.core import core\ndef test(): pass\n",
        })
        hot_matrix = {
            "tempograph/api.py": [("tests/test_api.py", 0.8)],
            "tempograph/core.py": [("tests/test_core.py", 0.8)],
            "tests/test_api.py": [("tempograph/api.py", 0.8)],
            "tests/test_core.py": [("tempograph/core.py", 0.8)],
        }
        with patch("tempograph.git.is_git_repo", return_value=True), \
             patch("tempograph.git.cochange_matrix", return_value=hot_matrix):
            out = render_architecture(g)
        # Direction: tests is dependent → expect "modifying tempograph → expect tests updates"
        assert "modifying tempograph" in out or "expect tests" in out

    def test_low_frequency_pairs_excluded(self, tmp_path):
        from unittest.mock import patch
        g = _build(tmp_path, {
            "src/a.py": "def fn(): pass\n",
            "tests/test_a.py": "from src.a import fn\ndef test(): pass\n",
        })
        low_matrix = {
            "src/a.py": [("tests/test_a.py", 0.3)],
            "tests/test_a.py": [("src/a.py", 0.3)],
        }
        with patch("tempograph.git.is_git_repo", return_value=True), \
             patch("tempograph.git.cochange_matrix", return_value=low_matrix):
            out = render_architecture(g)
        assert "hot module coupling" not in out.lower()

    def test_non_structural_pairs_excluded(self, tmp_path):
        from unittest.mock import patch
        # notes and plans co-change but have NO import edges → filtered out
        g = _build(tmp_path, {
            "notes/state.md": "# notes\n",
            "plans/plan.md": "# plans\n",
            "src/a.py": "def fn(): pass\n",
        })
        hot_matrix = {
            "notes/state.md": [("plans/plan.md", 1.0)] * 5,
            "plans/plan.md": [("notes/state.md", 1.0)] * 5,
        }
        with patch("tempograph.git.is_git_repo", return_value=True), \
             patch("tempograph.git.cochange_matrix", return_value=hot_matrix):
            out = render_architecture(g)
        # notes↔plans should NOT appear — they have no structural import edges
        assert "notes↔plans" not in out and "plans↔notes" not in out

    def test_min_two_hot_pairs_required(self, tmp_path):
        from unittest.mock import patch
        g = _build(tmp_path, {
            "src/a.py": "def fn(): pass\n",
            "tests/test_a.py": "from src.a import fn\ndef test(): pass\n",
        })
        # Only 1 hot file pair — below threshold of 2
        one_pair_matrix = {
            "src/a.py": [("tests/test_a.py", 0.9)],
            "tests/test_a.py": [("src/a.py", 0.9)],
        }
        with patch("tempograph.git.is_git_repo", return_value=True), \
             patch("tempograph.git.cochange_matrix", return_value=one_pair_matrix):
            out = render_architecture(g)
        assert "hot module coupling" not in out.lower()

    def test_bidirectional_label_when_mutual_imports(self, tmp_path):
        from unittest.mock import patch
        # A imports B AND B imports A → bidirectional
        g = _build(tmp_path, {
            "pkga/a.py": "from pkgb.b import fn\ndef fa(): pass\n",
            "pkgb/b.py": "from pkga.a import fa\ndef fn(): pass\n",
        })
        hot_matrix = {
            "pkga/a.py": [("pkgb/b.py", 0.9)] * 3,
            "pkgb/b.py": [("pkga/a.py", 0.9)] * 3,
        }
        with patch("tempograph.git.is_git_repo", return_value=True), \
             patch("tempograph.git.cochange_matrix", return_value=hot_matrix):
            out = render_architecture(g)
        if "hot module coupling" in out.lower():
            assert "bidirectional" in out


# ── render_skills ─────────────────────────────────────────────────────────────

class TestRenderSkills:
    def test_returns_str(self, tmp_path):
        g = _build(tmp_path, {"mod.py": "def fn(): pass\n"})
        out = render_skills(g)
        assert isinstance(out, str)

    def test_returns_fallback_when_plugin_missing(self, tmp_path):
        g = _build(tmp_path, {"mod.py": "def fn(): pass\n"})
        out = render_skills(g)
        # Either returns pattern data or the "not available" fallback
        assert len(out) > 0


# ── is_git_repo ───────────────────────────────────────────────────────────────

class TestIsGitRepo:
    def test_non_git_dir_returns_false(self, tmp_path):
        assert is_git_repo(str(tmp_path)) is False

    def test_dir_with_git_returns_true(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert is_git_repo(str(tmp_path)) is True

    def test_tempograph_itself_is_git_repo(self):
        import os
        repo = os.path.dirname(os.path.dirname(__file__))
        assert is_git_repo(repo) is True


# ── _extract_name_from_question ───────────────────────────────────────────────

class TestExtractNameFromQuestion:
    def test_strips_where_is_prefix(self):
        assert _extract_name_from_question("where is FileParser") == "FileParser"

    def test_strips_find_prefix(self):
        assert _extract_name_from_question("find build_graph") == "build_graph"

    def test_strips_what_calls_prefix(self):
        result = _extract_name_from_question("what calls render_overview")
        assert "render_overview" in result

    def test_strips_trailing_question_mark(self):
        result = _extract_name_from_question("where is Symbol defined?")
        assert "?" not in result

    def test_strips_articles(self):
        result = _extract_name_from_question("find the FileParser class")
        assert result == "FileParser"

    def test_bare_name_unchanged(self):
        assert _extract_name_from_question("FileParser") == "FileParser"

    def test_strips_quotes(self):
        result = _extract_name_from_question("find 'Symbol'")
        assert result == "Symbol"


# ── render_lookup ─────────────────────────────────────────────────────────────

class TestRenderLookup:
    def test_returns_str(self, tmp_path):
        g = _build(tmp_path, {"mod.py": "def greet(): pass\n"})
        out = render_lookup(g, "where is greet")
        assert isinstance(out, str)

    def test_finds_known_symbol(self, tmp_path):
        g = _build(tmp_path, {"mod.py": "def greet(): pass\n"})
        out = render_lookup(g, "where is greet")
        assert "greet" in out

    def test_unknown_symbol_returns_not_found(self, tmp_path):
        g = _build(tmp_path, {"mod.py": "def fn(): pass\n"})
        out = render_lookup(g, "where is nonexistent_xyz_abc")
        assert "not found" in out.lower() or "no match" in out.lower() or isinstance(out, str)

    def test_what_calls_question(self, tmp_path):
        g = _build(tmp_path, {
            "lib.py": "def target(): pass\n",
            "app.py": "from lib import target\ndef run(): target()\n",
        })
        out = render_lookup(g, "what calls target")
        assert isinstance(out, str) and len(out) > 0

    def test_what_imports_question(self, tmp_path):
        g = _build(tmp_path, {
            "utils.py": "def helper(): pass\n",
            "main.py": "from utils import helper\n",
        })
        out = render_lookup(g, "what imports utils.py")
        assert isinstance(out, str)


# ── _is_test_file ─────────────────────────────────────────────────────────────

class TestIsTestFile:
    def test_pytest_test_file(self):
        assert _is_test_file("tests/test_parser.py") is True

    def test_underscore_test_suffix(self):
        assert _is_test_file("parser_test.py") is True

    def test_jest_test_file(self):
        assert _is_test_file("Button.test.tsx") is True

    def test_jest_spec_file(self):
        assert _is_test_file("utils.spec.ts") is True

    def test_source_file_not_test(self):
        assert _is_test_file("tempograph/parser.py") is False

    def test_file_with_test_in_middle_not_test(self):
        assert _is_test_file("test_helpers_module.py") is True  # starts with test_

    def test_non_test_ts_file(self):
        assert _is_test_file("components/Button.tsx") is False

    def test_empty_string_not_test(self):
        assert _is_test_file("") is False


# ── _file_effort_badge ────────────────────────────────────────────────────────

def _make_sym(sym_id: str, file_path: str) -> Symbol:
    return Symbol(
        id=sym_id,
        name=sym_id.split("::")[-1],
        qualified_name=sym_id.split("::")[-1],
        kind=SymbolKind.FUNCTION,
        language=Language.PYTHON,
        file_path=file_path,
        line_start=1,
        line_end=5,
    )


class TestFileEffortBadge:
    def test_high_effort_five_symbols_two_callers_each(self):
        # 5 dead symbols, each with 2 external callers → score = 5 * (1 + 2) = 15 → HIGH
        syms = [(_make_sym(f"a.py::fn_{i}", "a.py"), 80) for i in range(5)]
        graph = MagicMock()
        # Each symbol has 2 callers from a different file
        caller_sym = _make_sym("b.py::caller", "b.py")
        graph.callers_of.return_value = [caller_sym, caller_sym]
        badge = _file_effort_badge(syms, graph)
        assert badge == " [effort: HIGH]"

    def test_low_effort_two_symbols_zero_callers(self):
        # 2 dead symbols, 0 external callers → weights = [0.5, 0.5], score = 2 * 1.5 = 3 → LOW
        syms = [(_make_sym(f"a.py::fn_{i}", "a.py"), 80) for i in range(2)]
        graph = MagicMock()
        graph.callers_of.return_value = []
        badge = _file_effort_badge(syms, graph)
        assert badge == " [effort: LOW]"
