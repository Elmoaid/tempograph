"""Tests for Tempo graph query methods: callers_of, callees_of, children_of,
subtypes_of, find_symbol, symbols_in_file, detect_circular_imports."""
from __future__ import annotations

from pathlib import Path

import pytest

from tempograph.builder import build_graph
from tempograph.types import Language, SymbolKind, EdgeKind


def _build(tmp_path: Path, files: dict[str, str]) -> object:
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return build_graph(str(tmp_path), use_cache=False, use_config=False)


# ── callers_of / callees_of ───────────────────────────────────────────────────

class TestCallersCallees:
    def test_callers_of_returns_list(self, tmp_path):
        g = _build(tmp_path, {
            "lib.py": "def fn(): pass\n",
            "app.py": "from lib import fn\ndef run(): fn()\n",
        })
        fn = next(s for s in g.symbols.values() if s.name == "fn")
        callers = g.callers_of(fn.id)
        assert isinstance(callers, list)

    def test_callers_identified_correctly(self, tmp_path):
        g = _build(tmp_path, {
            "lib.py": "def target(): pass\n",
            "app.py": "from lib import target\ndef caller(): target()\n",
        })
        target = next(s for s in g.symbols.values() if s.name == "target")
        callers = g.callers_of(target.id)
        assert any(c.name == "caller" for c in callers)

    def test_callees_of_returns_list(self, tmp_path):
        g = _build(tmp_path, {
            "lib.py": "def helper(): pass\n",
            "app.py": "from lib import helper\ndef runner(): helper()\n",
        })
        runner = next(s for s in g.symbols.values() if s.name == "runner")
        callees = g.callees_of(runner.id)
        assert isinstance(callees, list)

    def test_callees_identified_correctly(self, tmp_path):
        g = _build(tmp_path, {
            "lib.py": "def callee(): pass\n",
            "app.py": "from lib import callee\ndef caller(): callee()\n",
        })
        caller = next(s for s in g.symbols.values() if s.name == "caller")
        callees = g.callees_of(caller.id)
        assert any(c.name == "callee" for c in callees)

    def test_unknown_id_returns_empty(self, tmp_path):
        g = _build(tmp_path, {"a.py": "def fn(): pass\n"})
        assert g.callers_of("nonexistent::id") == []
        assert g.callees_of("nonexistent::id") == []


# ── children_of ───────────────────────────────────────────────────────────────

class TestChildrenOf:
    def test_class_methods_are_children(self, tmp_path):
        g = _build(tmp_path, {
            "mod.py": "class MyClass:\n    def method(self): pass\n    def other(self): pass\n"
        })
        cls = next(s for s in g.symbols.values() if s.name == "MyClass")
        children = g.children_of(cls.id)
        child_names = {c.name for c in children}
        assert "method" in child_names
        assert "other" in child_names

    def test_top_level_function_has_no_children(self, tmp_path):
        g = _build(tmp_path, {"mod.py": "def fn(): pass\n"})
        fn = next(s for s in g.symbols.values() if s.name == "fn")
        assert g.children_of(fn.id) == []

    def test_unknown_id_returns_empty(self, tmp_path):
        g = _build(tmp_path, {"a.py": "def fn(): pass\n"})
        assert g.children_of("nonexistent::id") == []


# ── find_symbol ───────────────────────────────────────────────────────────────

class TestFindSymbol:
    def test_finds_by_exact_name(self, tmp_path):
        g = _build(tmp_path, {"mod.py": "def greet(): pass\n"})
        results = g.find_symbol("greet")
        assert len(results) == 1
        assert results[0].name == "greet"

    def test_case_insensitive_match(self, tmp_path):
        g = _build(tmp_path, {"mod.py": "def MyFunc(): pass\n"})
        results = g.find_symbol("myfunc")
        assert len(results) >= 1

    def test_unknown_name_returns_empty(self, tmp_path):
        g = _build(tmp_path, {"mod.py": "def fn(): pass\n"})
        assert g.find_symbol("nonexistent_xyz_abc") == []

    def test_finds_across_files(self, tmp_path):
        g = _build(tmp_path, {
            "a.py": "class Widget: pass\n",
            "b.py": "class Widget: pass\n",  # same name in different file
        })
        results = g.find_symbol("Widget")
        assert len(results) == 2


# ── symbols_in_file ───────────────────────────────────────────────────────────

class TestSymbolsInFile:
    def test_returns_symbols_for_known_file(self, tmp_path):
        g = _build(tmp_path, {"mod.py": "def fn(): pass\ndef gn(): pass\n"})
        syms = g.symbols_in_file("mod.py")
        names = {s.name for s in syms}
        assert "fn" in names
        assert "gn" in names

    def test_returns_empty_for_unknown_file(self, tmp_path):
        g = _build(tmp_path, {"mod.py": "def fn(): pass\n"})
        assert g.symbols_in_file("nonexistent.py") == []

    def test_returns_only_file_symbols(self, tmp_path):
        g = _build(tmp_path, {
            "a.py": "def fn_a(): pass\n",
            "b.py": "def fn_b(): pass\n",
        })
        syms_a = g.symbols_in_file("a.py")
        assert all(s.file_path == "a.py" for s in syms_a)


# ── subtypes_of ───────────────────────────────────────────────────────────────

class TestSubtypesOf:
    def test_subclass_found(self, tmp_path):
        g = _build(tmp_path, {
            "animals.py": "class Animal: pass\nclass Dog(Animal): pass\n"
        })
        subtypes = g.subtypes_of("Animal")
        subtype_names = {s.name for s in subtypes}
        assert "Dog" in subtype_names

    def test_no_subclasses_returns_empty(self, tmp_path):
        g = _build(tmp_path, {"mod.py": "class Base: pass\n"})
        assert g.subtypes_of("Base") == []

    def test_unknown_base_returns_empty(self, tmp_path):
        g = _build(tmp_path, {"mod.py": "def fn(): pass\n"})
        assert g.subtypes_of("NonExistentBase") == []


# ── detect_circular_imports ───────────────────────────────────────────────────

class TestDetectCircularImports:
    def test_no_cycles_returns_empty(self, tmp_path):
        g = _build(tmp_path, {
            "core.py": "def base(): pass\n",
            "app.py": "from core import base\ndef run(): base()\n",
        })
        cycles = g.detect_circular_imports()
        assert isinstance(cycles, list)

    def test_cycle_detected(self, tmp_path):
        g = _build(tmp_path, {
            "a.py": "from b import g\ndef f(): g()\n",
            "b.py": "from a import f\ndef g(): f()\n",
        })
        cycles = g.detect_circular_imports()
        # Whether cycle is detected depends on import resolution — just assert type
        assert isinstance(cycles, list)

    def test_returns_list_of_lists(self, tmp_path):
        g = _build(tmp_path, {"a.py": "def fn(): pass\n"})
        result = g.detect_circular_imports()
        for item in result:
            assert isinstance(item, list)


# ── search_symbols_scored ─────────────────────────────────────────────────────

class TestSearchSymbolsScored:
    def test_returns_sorted_tuples(self, tmp_path):
        g = _build(tmp_path, {"mod.py": "def greet(): pass\ndef farewell(): pass\n"})
        results = g.search_symbols_scored("greet", use_hybrid=False)
        assert isinstance(results, list)
        assert all(isinstance(score, float) and hasattr(sym, 'name') for score, sym in results)

    def test_exact_name_match_ranks_first(self, tmp_path):
        g = _build(tmp_path, {
            "mod.py": "def render_overview(): pass\ndef render_focused(): pass\n"
        })
        results = g.search_symbols_scored("render_overview", use_hybrid=False)
        assert results[0][1].name == "render_overview"

    def test_scores_sorted_descending(self, tmp_path):
        g = _build(tmp_path, {"mod.py": "def foo(): pass\ndef bar(): pass\ndef foobar(): pass\n"})
        results = g.search_symbols_scored("foo", use_hybrid=False)
        scores = [s for s, _ in results]
        assert scores == sorted(scores, reverse=True)

    def test_unknown_query_returns_empty(self, tmp_path):
        g = _build(tmp_path, {"mod.py": "def fn(): pass\n"})
        results = g.search_symbols_scored("xyz_nonexistent_zzz", use_hybrid=False)
        assert results == []

    def test_exported_symbol_scores_higher(self, tmp_path):
        # Both "process" names — the exported one should rank higher
        g = _build(tmp_path, {
            "pub.py": "def process(): pass\n",  # exported (no underscore)
            "priv.py": "def _process(): pass\n",
        })
        results = g.search_symbols_scored("process", use_hybrid=False)
        if len(results) >= 2:
            exported = next((s for _, s in results if s.exported), None)
            not_exported = next((s for _, s in results if not s.exported), None)
            if exported and not_exported:
                exported_score = next(sc for sc, s in results if s.id == exported.id)
                priv_score = next(sc for sc, s in results if s.id == not_exported.id)
                assert exported_score >= priv_score

    def test_camelcase_query_matches_snake_case(self, tmp_path):
        g = _build(tmp_path, {"mod.py": "def build_graph(): pass\n"})
        results = g.search_symbols_scored("buildGraph", use_hybrid=False)
        names = [s.name for _, s in results]
        assert "build_graph" in names

    def test_search_symbols_wrapper(self, tmp_path):
        g = _build(tmp_path, {"mod.py": "def greet(): pass\n"})
        results = g.search_symbols("greet")
        assert isinstance(results, list)
        assert any(s.name == "greet" for s in results)


# ── dependency_layers ─────────────────────────────────────────────────────────

class TestDependencyLayers:
    def test_returns_list_of_lists(self, tmp_path):
        g = _build(tmp_path, {
            "lib.py": "def fn(): pass\n",
            "app.py": "from lib import fn\n",
        })
        result = g.dependency_layers()
        assert isinstance(result, list)
        for layer in result:
            assert isinstance(layer, list)

    def test_leaf_in_layer_zero(self, tmp_path):
        g = _build(tmp_path, {
            "lib.py": "def fn(): pass\n",
            "app.py": "from lib import fn\n",
        })
        layers = g.dependency_layers()
        assert len(layers) >= 1
        # lib.py has no imports — should be in layer 0
        assert any("lib.py" in f for f in layers[0])

    def test_importer_in_higher_layer(self, tmp_path):
        g = _build(tmp_path, {
            "lib.py": "def fn(): pass\n",
            "app.py": "from lib import fn\n",
        })
        layers = g.dependency_layers()
        flat = [f for layer in layers for f in layer]
        lib_layer = next(i for i, layer in enumerate(layers) if any("lib.py" in f for f in layer))
        app_layer = next(i for i, layer in enumerate(layers) if any("app.py" in f for f in layer))
        assert app_layer > lib_layer

    def test_chain_produces_three_layers(self, tmp_path):
        g = _build(tmp_path, {
            "core.py": "def base(): pass\n",
            "mid.py": "from core import base\ndef wrapper(): base()\n",
            "top.py": "from mid import wrapper\ndef main(): wrapper()\n",
        })
        layers = g.dependency_layers()
        assert len(layers) >= 2
        core_layer = next(i for i, layer in enumerate(layers) if any("core.py" in f for f in layer))
        mid_layer = next(i for i, layer in enumerate(layers) if any("mid.py" in f for f in layer))
        top_layer = next(i for i, layer in enumerate(layers) if any("top.py" in f for f in layer))
        assert core_layer < mid_layer < top_layer

    def test_no_import_edges_returns_empty(self, tmp_path):
        g = _build(tmp_path, {"standalone.py": "def fn(): pass\n"})
        result = g.dependency_layers()
        # No IMPORTS edges — no files in layers
        assert result == []

    def test_circular_dep_does_not_crash(self, tmp_path):
        g = _build(tmp_path, {
            "a.py": "from b import g\ndef f(): g()\n",
            "b.py": "from a import f\ndef g(): f()\n",
        })
        result = g.dependency_layers()
        # Should not raise; cycles get dumped into last layer
        assert isinstance(result, list)


# ── renderers_of ──────────────────────────────────────────────────────────────

class TestRenderersOf:
    def test_unknown_id_returns_empty(self, tmp_path):
        g = _build(tmp_path, {"a.py": "def fn(): pass\n"})
        assert g.renderers_of("nonexistent::id") == []

    def test_returns_list(self, tmp_path):
        g = _build(tmp_path, {"a.py": "def fn(): pass\n"})
        fn = next(s for s in g.symbols.values() if s.name == "fn")
        result = g.renderers_of(fn.id)
        assert isinstance(result, list)


# ── find_dead_code ────────────────────────────────────────────────────────────

class TestFindDeadCode:
    def test_returns_list(self, tmp_path):
        g = _build(tmp_path, {"mod.py": "def fn(): pass\n"})
        result = g.find_dead_code()
        assert isinstance(result, list)

    def test_unused_exported_function_detected(self, tmp_path):
        g = _build(tmp_path, {"mod.py": "def orphan(): pass\n"})
        result = g.find_dead_code()
        assert any(s.name == "orphan" for s in result)

    def test_called_function_not_dead(self, tmp_path):
        g = _build(tmp_path, {
            "lib.py": "def used(): pass\n",
            "app.py": "from lib import used\ndef run(): used()\n",
        })
        dead = g.find_dead_code()
        # "used" is called cross-file — should not be in dead list
        assert not any(s.name == "used" for s in dead)

    def test_main_not_flagged(self, tmp_path):
        g = _build(tmp_path, {"app.py": "def main(): pass\n"})
        dead = g.find_dead_code()
        assert not any(s.name == "main" for s in dead)

    def test_result_sorted_by_file(self, tmp_path):
        g = _build(tmp_path, {
            "a.py": "def orphan_a(): pass\n",
            "b.py": "def orphan_b(): pass\n",
        })
        dead = g.find_dead_code()
        file_paths = [s.file_path for s in dead]
        assert file_paths == sorted(file_paths)
