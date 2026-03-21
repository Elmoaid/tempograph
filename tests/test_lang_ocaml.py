"""Tests for OCaml language handler (OCamlHandlerMixin)."""
from __future__ import annotations

import pytest

from tempograph.parser import FileParser
from tempograph.types import Language, EdgeKind, SymbolKind


def _parse(code: str, filename: str = "src/lib.ml"):
    p = FileParser(filename, Language.OCAML, code.encode())
    return p.parse()


# ── Top-level functions ───────────────────────────────────────────────────────

class TestFunction:
    def test_function_extracted(self):
        syms, _, _ = _parse("let add x y = x + y")
        assert any(s.name == "add" for s in syms)

    def test_function_kind(self):
        syms, _, _ = _parse("let add x y = x + y")
        fn = next(s for s in syms if s.name == "add")
        assert fn.kind == SymbolKind.FUNCTION

    def test_function_exported(self):
        syms, _, _ = _parse("let add x y = x + y")
        fn = next(s for s in syms if s.name == "add")
        assert fn.exported is True

    def test_value_without_params_extracted(self):
        syms, _, _ = _parse("let pi = 3.14159")
        assert any(s.name == "pi" for s in syms)

    def test_private_function_not_exported(self):
        syms, _, _ = _parse("let _helper x = x + 1")
        fn = next(s for s in syms if s.name == "_helper")
        assert fn.exported is False

    def test_multiple_functions(self):
        syms, _, _ = _parse("""
let add x y = x + y
let sub x y = x - y
let mul x y = x * y
""")
        names = {s.name for s in syms}
        assert {"add", "sub", "mul"}.issubset(names)

    def test_function_line_numbers(self):
        syms, _, _ = _parse("\nlet add x y = x + y\n")
        fn = next(s for s in syms if s.name == "add")
        assert fn.line_start == 2


# ── Types ─────────────────────────────────────────────────────────────────────

class TestType:
    def test_type_alias_extracted(self):
        syms, _, _ = _parse("type name = string")
        assert any(s.name == "name" for s in syms)

    def test_type_kind(self):
        syms, _, _ = _parse("type id = int")
        t = next(s for s in syms if s.name == "id")
        assert t.kind == SymbolKind.TYPE_ALIAS

    def test_variant_type_extracted(self):
        syms, _, _ = _parse("type color = Red | Green | Blue")
        t = next(s for s in syms if s.name == "color")
        assert t.kind == SymbolKind.TYPE_ALIAS

    def test_type_exported(self):
        syms, _, _ = _parse("type point = { x: int; y: int }")
        t = next(s for s in syms if s.name == "point")
        assert t.exported is True

    def test_private_type_not_exported(self):
        syms, _, _ = _parse("type _internal = int")
        t = next(s for s in syms if s.name == "_internal")
        assert t.exported is False


# ── Modules ───────────────────────────────────────────────────────────────────

class TestModule:
    def test_module_extracted(self):
        syms, _, _ = _parse("""
module Utils = struct
  let helper x = x + 1
end
""")
        assert any(s.name == "Utils" for s in syms)

    def test_module_kind(self):
        syms, _, _ = _parse("module M = struct end")
        m = next(s for s in syms if s.name == "M")
        assert m.kind == SymbolKind.MODULE

    def test_module_exported(self):
        syms, _, _ = _parse("module Utils = struct end")
        m = next(s for s in syms if s.name == "Utils")
        assert m.exported is True

    def test_module_members_extracted(self):
        syms, _, _ = _parse("""
module Math = struct
  let add x y = x + y
  type t = int
end
""")
        names = {s.name for s in syms}
        assert "Math" in names
        assert "add" in names
        assert "t" in names

    def test_module_contains_function(self):
        _, edges, _ = _parse("""
module Math = struct
  let add x y = x + y
end
""")
        contains = [e for e in edges if e.kind == EdgeKind.CONTAINS]
        assert any("Math" in e.source_id and "add" in e.target_id for e in contains)

    def test_function_inside_module_is_method(self):
        syms, _, _ = _parse("""
module Math = struct
  let add x y = x + y
end
""")
        fn = next(s for s in syms if s.name == "add")
        assert fn.kind == SymbolKind.METHOD

    def test_nested_module(self):
        syms, _, _ = _parse("""
module Outer = struct
  module Inner = struct
    let f x = x
  end
end
""")
        names = {s.name for s in syms}
        assert "Outer" in names
        assert "Inner" in names
        assert "f" in names


# ── Imports (open) ────────────────────────────────────────────────────────────

class TestOpen:
    def test_open_extracted(self):
        _, _, imports = _parse("open List")
        assert "List" in imports

    def test_multiple_opens(self):
        _, _, imports = _parse("open List\nopen Printf\nopen Stdlib")
        assert {"List", "Printf", "Stdlib"}.issubset(set(imports))

    def test_open_does_not_create_symbol(self):
        syms, _, _ = _parse("open List")
        assert not any(s.name == "List" for s in syms)


# ── Interface files (.mli) ────────────────────────────────────────────────────

class TestInterface:
    def test_mli_file_parsed(self):
        p = FileParser("src/lib.mli", Language.OCAML, b"val add : int -> int -> int")
        # .mli files use 'ocaml_interface' grammar — if it doesn't blow up, we're fine
        syms, _, _ = p.parse()
        # No symbols expected for val declarations (not handled), but no crash
        assert isinstance(syms, list)
