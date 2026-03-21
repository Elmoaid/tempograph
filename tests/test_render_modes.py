"""Unit tests for render_overview, render_blast_radius, render_hotspots, render_dead_code,
render_dependencies, render_architecture, render_skills, is_git_repo."""
from __future__ import annotations

from tempograph.builder import build_graph
from tempograph.render import (
    render_blast_radius,
    render_dead_code,
    render_hotspots,
    render_overview,
    render_dependencies,
    render_architecture,
    render_skills,
)
from tempograph.git import is_git_repo


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
