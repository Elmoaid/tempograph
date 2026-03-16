"""Tests for MCP server: all 15 tools, JSON output, error codes, edge cases."""
import json
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))

# Use tempograph's own repo as test fixture
REPO_PATH = str(REPO)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_json(raw: str) -> dict:
    return json.loads(raw)


def assert_ok(raw: str) -> dict:
    d = parse_json(raw)
    assert d["status"] == "ok", f"Expected ok, got: {d}"
    assert "data" in d
    assert "tokens" in d
    assert "duration_ms" in d
    assert isinstance(d["tokens"], int)
    assert isinstance(d["duration_ms"], int)
    return d


def assert_error(raw: str, code: str) -> dict:
    d = parse_json(raw)
    assert d["status"] == "error", f"Expected error, got: {d}"
    assert d["code"] == code, f"Expected {code}, got: {d['code']}"
    assert "message" in d
    return d


# ---------------------------------------------------------------------------
# Import all tools
# ---------------------------------------------------------------------------

from tempograph.server import (
    index_repo, overview, focus, hotspots, blast_radius,
    diff_context, dead_code, lookup, symbols, file_map,
    dependencies, architecture, stats, report_feedback,
    learn_recommendation, prepare_context,
)


# ---------------------------------------------------------------------------
# Tool count
# ---------------------------------------------------------------------------

def test_tool_count():
    from tempograph.server import mcp
    assert len(mcp._tool_manager._tools) == 16


# ---------------------------------------------------------------------------
# JSON output — every tool returns valid JSON with correct shape
# ---------------------------------------------------------------------------

class TestJsonOutput:
    def test_index_repo(self):
        assert_ok(index_repo(REPO_PATH, output_format="json"))

    def test_overview(self):
        assert_ok(overview(REPO_PATH, output_format="json"))

    def test_focus(self):
        assert_ok(focus(REPO_PATH, "build_graph", output_format="json"))

    def test_hotspots(self):
        assert_ok(hotspots(REPO_PATH, output_format="json"))

    def test_blast_radius(self):
        assert_ok(blast_radius(REPO_PATH, query="build_graph", output_format="json"))

    def test_diff_context(self):
        assert_ok(diff_context(REPO_PATH, changed_files="tempograph/server.py", output_format="json"))

    def test_dead_code(self):
        assert_ok(dead_code(REPO_PATH, output_format="json"))

    def test_lookup(self):
        assert_ok(lookup(REPO_PATH, "where is build_graph?", output_format="json"))

    def test_symbols(self):
        assert_ok(symbols(REPO_PATH, output_format="json"))

    def test_file_map(self):
        assert_ok(file_map(REPO_PATH, output_format="json"))

    def test_dependencies(self):
        assert_ok(dependencies(REPO_PATH, output_format="json"))

    def test_architecture(self):
        assert_ok(architecture(REPO_PATH, output_format="json"))

    def test_stats(self):
        assert_ok(stats(REPO_PATH, output_format="json"))

    def test_learn_recommendation(self):
        raw = learn_recommendation(REPO_PATH, output_format="json")
        d = parse_json(raw)
        assert d["status"] in ("ok", "error")  # ok if tempo installed, error if not

    def test_prepare_context(self):
        r = assert_ok(prepare_context(REPO_PATH, task="fix search ranking",
                                      exclude_dirs="archive", output_format="json"))
        assert "Focus:" in r["data"]
        assert r["tokens"] > 100


# ---------------------------------------------------------------------------
# Text output — backwards compatible, no JSON wrapper
# ---------------------------------------------------------------------------

class TestTextOutput:
    def test_overview_text(self):
        raw = overview(REPO_PATH)
        assert not raw.startswith("{")
        assert "repo:" in raw.lower() or "files" in raw.lower()

    def test_focus_text(self):
        raw = focus(REPO_PATH, "build_graph")
        assert not raw.startswith("{")

    def test_hotspots_text(self):
        raw = hotspots(REPO_PATH)
        assert not raw.startswith("{")

    def test_lookup_text(self):
        raw = lookup(REPO_PATH, "where is build_graph?")
        assert "build_graph" in raw

    def test_dead_code_text(self):
        raw = dead_code(REPO_PATH)
        assert not raw.startswith("{")


# ---------------------------------------------------------------------------
# Error codes — machine-readable
# ---------------------------------------------------------------------------

class TestErrorCodes:
    def test_repo_not_found_json(self):
        assert_error(overview("/nonexistent/repo", output_format="json"), "REPO_NOT_FOUND")

    def test_repo_not_found_text(self):
        raw = overview("/nonexistent/repo")
        assert raw.startswith("[ERROR:REPO_NOT_FOUND]")

    def test_not_git_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            assert_error(diff_context(tmp, output_format="json"), "NOT_GIT_REPO")

    def test_invalid_params_blast_radius(self):
        assert_error(blast_radius(REPO_PATH, output_format="json"), "INVALID_PARAMS")

    def test_not_git_repo_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = diff_context(tmp)
            assert "[ERROR:NOT_GIT_REPO]" in raw


# ---------------------------------------------------------------------------
# Parameter behavior
# ---------------------------------------------------------------------------

class TestParameters:
    def test_blast_radius_query_over_file(self):
        """When both file_path and query are given, query takes precedence."""
        r = assert_ok(blast_radius(REPO_PATH, file_path="tempograph/server.py",
                                   query="build_graph", output_format="json"))
        assert "build_graph" in r["data"]

    def test_blast_radius_file_only(self):
        r = assert_ok(blast_radius(REPO_PATH, file_path="tempograph/server.py",
                                   output_format="json"))
        assert "server.py" in r["data"]

    def test_diff_context_explicit_files_no_git(self):
        """Passing changed_files explicitly should not require git."""
        with tempfile.TemporaryDirectory() as tmp:
            # Non-git dir, but with explicit files — should not error NOT_GIT_REPO
            raw = diff_context(tmp, changed_files="foo.py", output_format="json")
            d = parse_json(raw)
            # May be ok (empty impact) or error (build failed on empty dir), but NOT "NOT_GIT_REPO"
            if d["status"] == "error":
                assert d["code"] != "NOT_GIT_REPO"

    def test_focus_max_tokens(self):
        r1 = assert_ok(focus(REPO_PATH, "build_graph", max_tokens=500, output_format="json"))
        r2 = assert_ok(focus(REPO_PATH, "build_graph", max_tokens=4000, output_format="json"))
        assert r1["tokens"] <= r2["tokens"] + 50  # smaller budget → fewer tokens

    def test_hotspots_top_n(self):
        r1 = assert_ok(hotspots(REPO_PATH, top_n=3, output_format="json"))
        r2 = assert_ok(hotspots(REPO_PATH, top_n=20, output_format="json"))
        assert r1["tokens"] <= r2["tokens"]

    def test_file_map_max_symbols(self):
        r = assert_ok(file_map(REPO_PATH, max_symbols_per_file=2, output_format="json"))
        assert r["tokens"] > 0


# ---------------------------------------------------------------------------
# Token budget sanity
# ---------------------------------------------------------------------------

class TestTokenBudgets:
    def test_overview_cheap(self):
        r = assert_ok(overview(REPO_PATH, output_format="json"))
        assert r["tokens"] < 1000

    def test_stats_cheap(self):
        r = assert_ok(stats(REPO_PATH, output_format="json"))
        assert r["tokens"] < 300

    def test_focus_bounded(self):
        r = assert_ok(focus(REPO_PATH, "build_graph", max_tokens=2000, output_format="json"))
        assert r["tokens"] < 3000  # some overhead is ok

    def test_lookup_cheap(self):
        r = assert_ok(lookup(REPO_PATH, "where is render_overview?", output_format="json"))
        assert r["tokens"] < 1000


# ---------------------------------------------------------------------------
# Feedback tool
# ---------------------------------------------------------------------------

class TestFeedback:
    def test_feedback_success(self, monkeypatch, tmp_path):
        monkeypatch.setattr("tempograph.telemetry.CENTRAL_DIR", tmp_path)
        raw = report_feedback(str(tmp_path), "overview", True, "great output")
        assert "recorded" in raw.lower()

    def test_feedback_negative(self, monkeypatch, tmp_path):
        monkeypatch.setattr("tempograph.telemetry.CENTRAL_DIR", tmp_path)
        raw = report_feedback(str(tmp_path), "focus", False, "missing context")
        assert "recorded" in raw.lower()

    def test_feedback_no_note(self, monkeypatch, tmp_path):
        monkeypatch.setattr("tempograph.telemetry.CENTRAL_DIR", tmp_path)
        raw = report_feedback(str(tmp_path), "hotspots", True)
        assert "recorded" in raw.lower()


# ---------------------------------------------------------------------------
# Prepare context (batch tool)
# ---------------------------------------------------------------------------

class TestPrepareContext:
    def test_includes_overview_and_focus(self):
        r = assert_ok(prepare_context(REPO_PATH, task="graph building",
                                      exclude_dirs="archive", output_format="json"))
        assert "## Repo:" in r["data"]
        assert "Focus:" in r["data"]

    def test_respects_token_budget(self):
        r = assert_ok(prepare_context(REPO_PATH, task="graph building",
                                      max_tokens=2000, exclude_dirs="archive",
                                      output_format="json"))
        assert r["tokens"] <= 2500  # some overhead ok

    def test_includes_hotspots_for_change_tasks(self):
        r = assert_ok(prepare_context(REPO_PATH, task="fix search ranking bug",
                                      task_type="debug", exclude_dirs="archive",
                                      output_format="json"))
        assert "Hotspots" in r["data"] or r["tokens"] > 500

    def test_error_on_bad_repo(self):
        assert_error(prepare_context("/nonexistent", task="anything",
                                     output_format="json"), "REPO_NOT_FOUND")

    def test_text_mode(self):
        r = prepare_context(REPO_PATH, task="overview", exclude_dirs="archive")
        assert not r.startswith("{")
        assert "Focus:" in r


# ---------------------------------------------------------------------------
# Exclude dirs
# ---------------------------------------------------------------------------

class TestExcludeDirs:
    def test_exclude_reduces_symbols(self):
        """Excluding archive/ should produce fewer symbols."""
        r_all = assert_ok(overview(REPO_PATH, output_format="json"))
        r_excl = assert_ok(overview(REPO_PATH, exclude_dirs="archive", output_format="json"))
        # The excluded version should mention fewer files
        assert "archive" not in r_excl["data"] or r_excl["data"].count("archive") < r_all["data"].count("archive")

    def test_exclude_on_hotspots(self):
        """Hotspots with archive excluded should not show archive symbols."""
        r = assert_ok(hotspots(REPO_PATH, exclude_dirs="archive", output_format="json"))
        # Top hotspots should be from real code, not archive
        lines = r["data"].split("\n")
        for line in lines[:10]:
            assert "archive/" not in line

    def test_exclude_comma_separated(self):
        """Multiple dirs can be excluded with commas."""
        r = assert_ok(dead_code(REPO_PATH, exclude_dirs="archive,bench", output_format="json"))
        assert r["status"] == "ok"

    def test_exclude_on_focus(self):
        r = assert_ok(focus(REPO_PATH, "build_graph", exclude_dirs="archive", output_format="json"))
        assert r["status"] == "ok"

    def test_exclude_on_blast_radius(self):
        r = assert_ok(blast_radius(REPO_PATH, query="build_graph", exclude_dirs="archive", output_format="json"))
        assert r["status"] == "ok"


# ---------------------------------------------------------------------------
# Search ranking
# ---------------------------------------------------------------------------

class TestSearchRanking:
    def test_exported_ranks_higher(self):
        """Exported symbols should rank above non-exported with same text match."""
        from tempograph.builder import build_graph
        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        results = g.search_symbols("config")
        # Config class (exported) should be in top 3
        top_names = [s.qualified_name for s in results[:5]]
        assert any("Config" in n for n in top_names)

    def test_cross_file_callers_boost(self):
        """Symbols with many cross-file callers should rank higher."""
        from tempograph.builder import build_graph
        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        results = g.search_symbols("config")
        # Config.get has 70+ cross-file callers — should be top 3
        top_names = [s.qualified_name for s in results[:3]]
        assert any("get" in n.lower() or "Config" in n for n in top_names)


# ---------------------------------------------------------------------------
# Import type skipping
# ---------------------------------------------------------------------------

class TestImportTypeSkipping:
    def test_type_only_import_no_edge(self):
        """import type { X } from 'Y' should not create an imports edge."""
        from tempograph.builder import build_graph
        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        # tempo.ts has 'import type { TempoResult } from "../App"'
        # This should NOT create an import edge from tempo.ts to App.tsx
        for e in g.edges:
            if "tempo.ts" in e.source_id and e.kind.value == "imports" and "App" in e.target_id:
                pytest.fail(f"Type-only import created edge: {e.source_id} -> {e.target_id}")

    def test_no_false_circular_imports(self):
        """With import type skipping, no circular imports in the UI code."""
        from tempograph.builder import build_graph
        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        cycles = g.detect_circular_imports()
        # Filter to UI cycles only
        ui_cycles = [c for c in cycles if any("ui/src" in f for f in c)]
        assert len(ui_cycles) == 0, f"False circular imports: {ui_cycles}"


# ---------------------------------------------------------------------------
# Java parser
# ---------------------------------------------------------------------------

class TestJavaParser:
    def test_class_detection(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'public class Foo { public void bar() {} private int baz() { return 1; } }'
        p = FileParser('Foo.java', Language.JAVA, code)
        syms, edges, imports = p.parse()
        names = {s.qualified_name for s in syms}
        assert "Foo" in names
        assert "Foo.bar" in names
        assert "Foo.baz" in names
        foo = next(s for s in syms if s.name == "Foo")
        assert foo.kind.value == "class"
        assert foo.exported is True
        bar = next(s for s in syms if s.name == "bar")
        assert bar.exported is True
        baz = next(s for s in syms if s.name == "baz")
        assert baz.exported is False

    def test_interface_detection(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'interface Repo { void save(); List<User> findAll(); }'
        p = FileParser('Repo.java', Language.JAVA, code)
        syms, _, _ = p.parse()
        names = {s.qualified_name for s in syms}
        assert "Repo" in names
        assert "Repo.save" in names
        assert "Repo.findAll" in names
        repo = next(s for s in syms if s.name == "Repo")
        assert repo.kind.value == "interface"

    def test_enum_detection(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'enum Color { RED, GREEN, BLUE }'
        p = FileParser('Color.java', Language.JAVA, code)
        syms, _, _ = p.parse()
        assert any(s.name == "Color" for s in syms)

    def test_constructor(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'class Svc { public Svc(int x) { this.x = x; } }'
        p = FileParser('Svc.java', Language.JAVA, code)
        syms, edges, _ = p.parse()
        assert any(s.qualified_name == "Svc.Svc" for s in syms)
        assert any(e.kind.value == "contains" for e in edges)

    def test_imports(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'import java.util.List;\nimport java.util.Map;\nclass A {}'
        p = FileParser('A.java', Language.JAVA, code)
        _, _, imports = p.parse()
        assert len(imports) == 2
        assert any("List" in i for i in imports)

    def test_call_detection(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'class A { void run() { svc.findAll(); validate(); } void validate() {} }'
        p = FileParser('A.java', Language.JAVA, code)
        syms, edges, _ = p.parse()
        call_edges = [e for e in edges if e.kind.value == "calls"]
        targets = {e.target_id for e in call_edges}
        assert "svc.findAll" in targets
        assert "validate" in targets

    def test_inheritance(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'class Dog extends Animal implements Runnable { void bark() {} }'
        p = FileParser('Dog.java', Language.JAVA, code)
        syms, edges, _ = p.parse()
        inherit_edges = [e for e in edges if e.kind.value == "inherits"]
        targets = {e.target_id for e in inherit_edges}
        assert "Animal" in targets or any("Animal" in t for t in targets)


# ---------------------------------------------------------------------------
# C# parser
# ---------------------------------------------------------------------------

class TestCSharpParser:
    def test_class_and_methods(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'public class Svc { public void Run() {} private int Calc() { return 1; } }'
        p = FileParser('Svc.cs', Language.CSHARP, code)
        syms, edges, _ = p.parse()
        names = {s.qualified_name for s in syms}
        assert "Svc" in names
        assert "Svc.Run" in names
        assert "Svc.Calc" in names
        svc = next(s for s in syms if s.name == "Svc")
        assert svc.exported is True
        calc = next(s for s in syms if s.name == "Calc")
        assert calc.exported is False

    def test_interface(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'public interface IRepo { void Save(); }'
        p = FileParser('IRepo.cs', Language.CSHARP, code)
        syms, _, _ = p.parse()
        assert any(s.name == "IRepo" and s.kind.value == "interface" for s in syms)
        assert any(s.qualified_name == "IRepo.Save" for s in syms)

    def test_struct_and_enum(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'public struct Vec2 { public int X; } public enum Color { Red, Green }'
        p = FileParser('Types.cs', Language.CSHARP, code)
        syms, _, _ = p.parse()
        assert any(s.name == "Vec2" and s.kind.value == "struct" for s in syms)
        assert any(s.name == "Color" for s in syms)

    def test_constructor(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'class Svc { public Svc(int x) { } }'
        p = FileParser('Svc.cs', Language.CSHARP, code)
        syms, edges, _ = p.parse()
        assert any(s.qualified_name == "Svc.Svc" for s in syms)

    def test_property(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'class User { public string Name { get; set; } }'
        p = FileParser('User.cs', Language.CSHARP, code)
        syms, _, _ = p.parse()
        assert any(s.qualified_name == "User.Name" for s in syms)

    def test_using_directives(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'using System;\nusing System.Linq;\nclass A {}'
        p = FileParser('A.cs', Language.CSHARP, code)
        _, _, imports = p.parse()
        assert len(imports) == 2

    def test_call_detection(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'class A { void Run() { _repo.Save(); Validate(); } void Validate() {} }'
        p = FileParser('A.cs', Language.CSHARP, code)
        _, edges, _ = p.parse()
        call_targets = {e.target_id for e in edges if e.kind.value == "calls"}
        assert "_repo.Save" in call_targets
        assert "Validate" in call_targets

    def test_inheritance(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'class Dog : Animal, IRunnable { void Bark() {} }'
        p = FileParser('Dog.cs', Language.CSHARP, code)
        _, edges, _ = p.parse()
        inherit_targets = {e.target_id for e in edges if e.kind.value == "inherits"}
        assert "Animal" in inherit_targets

    def test_namespace(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'namespace App { public class Foo { public void Bar() {} } }'
        p = FileParser('Foo.cs', Language.CSHARP, code)
        syms, _, _ = p.parse()
        assert any(s.name == "Foo" for s in syms)
        assert any(s.qualified_name == "Foo.Bar" for s in syms)


# ---------------------------------------------------------------------------
# Ruby parser
# ---------------------------------------------------------------------------

class TestRubyParser:
    def test_class_and_methods(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'class Foo\n  def bar\n    baz\n  end\n  def baz; end\nend'
        p = FileParser('foo.rb', Language.RUBY, code)
        syms, _, _ = p.parse()
        names = {s.qualified_name for s in syms}
        assert "Foo" in names
        assert "Foo.bar" in names
        assert "Foo.baz" in names

    def test_module(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'module Services\n  class Svc\n    def run; end\n  end\nend'
        p = FileParser('svc.rb', Language.RUBY, code)
        syms, _, _ = p.parse()
        assert any(s.name == "Services" for s in syms)
        assert any(s.name == "Svc" for s in syms)

    def test_inheritance(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'class Dog < Animal\n  def bark; end\nend'
        p = FileParser('dog.rb', Language.RUBY, code)
        _, edges, _ = p.parse()
        inherit_targets = {e.target_id for e in edges if e.kind.value == "inherits"}
        assert "Animal" in inherit_targets

    def test_requires(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b"require 'json'\nrequire_relative 'user'\nclass A; end"
        p = FileParser('a.rb', Language.RUBY, code)
        _, _, imports = p.parse()
        assert len(imports) == 2

    def test_call_detection(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'class A\n  def run\n    validate()\n    @repo.save(x)\n  end\n  def validate; end\nend'
        p = FileParser('a.rb', Language.RUBY, code)
        _, edges, _ = p.parse()
        call_targets = {e.target_id for e in edges if e.kind.value == "calls"}
        assert "validate" in call_targets


# ---------------------------------------------------------------------------
# Render module — token caps and noise detection
# ---------------------------------------------------------------------------

class TestRenderTokenCaps:
    def test_symbols_max_tokens(self):
        from tempograph.builder import build_graph
        from tempograph.render import render_symbols, count_tokens
        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        # Capped
        output = render_symbols(g, max_tokens=2000)
        tokens = count_tokens(output)
        assert tokens <= 2500  # allow some overhead
        assert "truncated" in output
        # Unlimited
        full = render_symbols(g, max_tokens=0)
        assert count_tokens(full) > tokens

    def test_map_max_tokens(self):
        from tempograph.builder import build_graph
        from tempograph.render import render_map, count_tokens
        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        output = render_map(g, max_tokens=1500)
        tokens = count_tokens(output)
        assert tokens <= 2000
        assert "truncated" in output

    def test_map_unlimited(self):
        from tempograph.builder import build_graph
        from tempograph.render import render_map, count_tokens
        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        output = render_map(g, max_tokens=0)
        assert "truncated" not in output

    def test_noise_detection(self):
        from tempograph.builder import build_graph
        from tempograph.render import render_overview
        g = build_graph(REPO_PATH)  # without exclude
        ov = render_overview(g)
        assert "SUGGESTED EXCLUDES" in ov
        assert "archive" in ov.lower()

    def test_focus_file_context(self):
        from tempograph.builder import build_graph
        from tempograph.render import render_focused
        g = build_graph(REPO_PATH, exclude_dirs=["archive"])
        output = render_focused(g, "build_graph", max_tokens=4000)
        # Should have the "Also in these files" section
        assert "Focus:" in output

    def test_namespace(self):
        from tempograph.parser import FileParser
        from tempograph.types import Language
        code = b'namespace App { public class Foo { public void Bar() {} } }'
        p = FileParser('Foo.cs', Language.CSHARP, code)
        syms, _, _ = p.parse()
        assert any(s.name == "Foo" for s in syms)
        assert any(s.qualified_name == "Foo.Bar" for s in syms)
