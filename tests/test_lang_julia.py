"""Tests for Julia language handler (JuliaHandlerMixin)."""
import pytest
from tempograph.builder import build_graph


def _build(tmp_path, filename: str, content: str):
    (tmp_path / filename).write_text(content)
    return build_graph(str(tmp_path), use_cache=False)


class TestJuliaFunctions:
    def test_function_extracted(self, tmp_path):
        g = _build(tmp_path, "math.jl", "function add(x, y)\n    return x + y\nend\n")
        names = {s.name for s in g.symbols.values()}
        assert "add" in names

    def test_function_kind(self, tmp_path):
        g = _build(tmp_path, "math.jl", "function compute(x)\n    return x * 2\nend\n")
        syms = [s for s in g.symbols.values() if s.name == "compute"]
        assert syms, "compute not found"
        assert syms[0].kind.value == "function"

    def test_short_form_function(self, tmp_path):
        g = _build(tmp_path, "util.jl", "double(x) = x * 2\n")
        names = {s.name for s in g.symbols.values()}
        assert "double" in names

    def test_multiple_functions(self, tmp_path):
        g = _build(tmp_path, "ops.jl",
            "function foo(x)\n    return x\nend\n"
            "function bar(y)\n    return y\nend\n"
        )
        names = {s.name for s in g.symbols.values()}
        assert "foo" in names
        assert "bar" in names


class TestJuliaStructs:
    def test_struct_extracted(self, tmp_path):
        g = _build(tmp_path, "types.jl", "struct Point\n    x\n    y\nend\n")
        names = {s.name for s in g.symbols.values()}
        assert "Point" in names

    def test_struct_kind(self, tmp_path):
        g = _build(tmp_path, "types.jl", "struct Circle\n    r\nend\n")
        syms = [s for s in g.symbols.values() if s.name == "Circle"]
        assert syms, "Circle not found"
        assert syms[0].kind.value == "class"

    def test_mutable_struct_extracted(self, tmp_path):
        g = _build(tmp_path, "types.jl", "mutable struct Counter\n    n::Int\nend\n")
        names = {s.name for s in g.symbols.values()}
        assert "Counter" in names

    def test_abstract_type(self, tmp_path):
        g = _build(tmp_path, "shapes.jl", "abstract type Shape end\n")
        names = {s.name for s in g.symbols.values()}
        assert "Shape" in names

    def test_abstract_type_kind_interface(self, tmp_path):
        g = _build(tmp_path, "shapes.jl", "abstract type Animal end\n")
        syms = [s for s in g.symbols.values() if s.name == "Animal"]
        assert syms, "Animal not found"
        assert syms[0].kind.value == "interface"


class TestJuliaModules:
    def test_module_extracted(self, tmp_path):
        g = _build(tmp_path, "mod.jl",
            "module MyMath\n"
            "function add(x, y)\n    return x + y\nend\n"
            "end\n"
        )
        names = {s.name for s in g.symbols.values()}
        assert "MyMath" in names

    def test_module_kind(self, tmp_path):
        g = _build(tmp_path, "mod.jl", "module Foo\nend\n")
        syms = [s for s in g.symbols.values() if s.name == "Foo"]
        assert syms, "Foo not found"
        assert syms[0].kind.value == "module"

    def test_function_inside_module(self, tmp_path):
        g = _build(tmp_path, "mod.jl",
            "module Utils\n"
            "function helper(x)\n    return x\nend\n"
            "end\n"
        )
        names = {s.name for s in g.symbols.values()}
        assert "helper" in names


class TestJuliaMacros:
    def test_macro_extracted(self, tmp_path):
        g = _build(tmp_path, "macros.jl",
            "macro assert_gt(a, b)\n    return :( $a > $b )\nend\n"
        )
        names = {s.name for s in g.symbols.values()}
        assert "@assert_gt" in names


class TestJuliaExports:
    def test_exported_function(self, tmp_path):
        g = _build(tmp_path, "api.jl",
            "export compute\n"
            "function compute(x)\n    return x * 2\nend\n"
        )
        syms = [s for s in g.symbols.values() if s.name == "compute"]
        assert syms, "compute not found"
        assert syms[0].exported is True

    def test_unexported_function(self, tmp_path):
        g = _build(tmp_path, "api.jl",
            "export compute\n"
            "function compute(x)\n    return x\nend\n"
            "function _private(x)\n    return x\nend\n"
        )
        priv = [s for s in g.symbols.values() if s.name == "_private"]
        assert priv, "_private not found"
        assert priv[0].exported is False


class TestJuliaConstants:
    def test_const_extracted(self, tmp_path):
        g = _build(tmp_path, "consts.jl", "const MAX_SIZE = 100\n")
        names = {s.name for s in g.symbols.values()}
        assert "MAX_SIZE" in names

    def test_const_kind(self, tmp_path):
        g = _build(tmp_path, "consts.jl", "const PI = 3.14\n")
        syms = [s for s in g.symbols.values() if s.name == "PI"]
        assert syms, "PI not found"
        assert syms[0].kind.value == "constant"


class TestJuliaImports:
    def test_using_creates_import(self, tmp_path):
        (tmp_path / "mathlib.jl").write_text("function sqrt(x)\n    return x\nend\n")
        (tmp_path / "main.jl").write_text(
            "using LinearAlgebra\n"
            "function run(x)\n    return x\nend\n"
        )
        g = build_graph(str(tmp_path), use_cache=False)
        # Just check it doesn't crash and main.jl is indexed
        assert any("main.jl" in fp for fp in g.files), f"main.jl not in graph files: {list(g.files.keys())}"
