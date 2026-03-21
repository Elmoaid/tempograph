"""Tests for F# language handler (FSharpHandlerMixin)."""
from __future__ import annotations

import pytest

from tempograph.parser import FileParser
from tempograph.types import Language, EdgeKind, SymbolKind


def _parse(code: str, filename: str = "src/Lib.fs"):
    p = FileParser(filename, Language.FSHARP, code.encode())
    return p.parse()


# ── Top-level functions ───────────────────────────────────────────────────────

class TestFunction:
    def test_function_extracted(self):
        syms, _, _ = _parse("module M\nlet add x y = x + y")
        assert any(s.name == "add" for s in syms)

    def test_function_kind(self):
        syms, _, _ = _parse("module M\nlet add x y = x + y")
        fn = next(s for s in syms if s.name == "add")
        assert fn.kind == SymbolKind.FUNCTION

    def test_function_exported(self):
        syms, _, _ = _parse("module M\nlet add x y = x + y")
        fn = next(s for s in syms if s.name == "add")
        assert fn.exported is True

    def test_private_function_not_exported(self):
        syms, _, _ = _parse("module M\nlet private helper x = x + 1")
        fn = next(s for s in syms if s.name == "helper")
        assert fn.exported is False

    def test_value_binding_extracted(self):
        syms, _, _ = _parse("module M\nlet pi = 3.14159")
        assert any(s.name == "pi" for s in syms)

    def test_multiple_functions(self):
        syms, _, _ = _parse("""
module M
let add x y = x + y
let sub x y = x - y
let mul x y = x * y
""")
        names = {s.name for s in syms}
        assert {"add", "sub", "mul"}.issubset(names)

    def test_function_line_numbers(self):
        syms, _, _ = _parse("\nmodule M\nlet add x y = x + y\n")
        fn = next(s for s in syms if s.name == "add")
        assert fn.line_start == 3


# ── Types ─────────────────────────────────────────────────────────────────────

class TestType:
    def test_union_type_extracted(self):
        syms, _, _ = _parse("module M\ntype Color = Red | Green | Blue")
        assert any(s.name == "Color" for s in syms)

    def test_union_type_kind(self):
        syms, _, _ = _parse("module M\ntype Color = Red | Green | Blue")
        t = next(s for s in syms if s.name == "Color")
        assert t.kind == SymbolKind.TYPE_ALIAS

    def test_record_type_extracted(self):
        syms, _, _ = _parse("module M\ntype Person = { name: string; age: int }")
        assert any(s.name == "Person" for s in syms)

    def test_record_type_kind(self):
        syms, _, _ = _parse("module M\ntype Person = { name: string; age: int }")
        t = next(s for s in syms if s.name == "Person")
        assert t.kind == SymbolKind.TYPE_ALIAS

    def test_class_type_extracted(self):
        syms, _, _ = _parse("""
module M
type Dog(name: string) =
    member _.Name = name
""")
        assert any(s.name == "Dog" for s in syms)

    def test_class_type_kind(self):
        syms, _, _ = _parse("""
module M
type Dog(name: string) =
    member _.Name = name
""")
        t = next(s for s in syms if s.name == "Dog")
        assert t.kind == SymbolKind.CLASS

    def test_type_exported(self):
        syms, _, _ = _parse("module M\ntype Color = Red | Blue")
        t = next(s for s in syms if s.name == "Color")
        assert t.exported is True


# ── Nested modules ────────────────────────────────────────────────────────────

class TestModule:
    def test_nested_module_extracted(self):
        syms, _, _ = _parse("""
namespace MyApp

module Utils =
    let add x y = x + y
""")
        assert any(s.name == "Utils" for s in syms)

    def test_nested_module_kind(self):
        syms, _, _ = _parse("""
namespace MyApp

module Utils =
    let add x y = x + y
""")
        m = next(s for s in syms if s.name == "Utils")
        assert m.kind == SymbolKind.MODULE

    def test_module_members_extracted(self):
        syms, _, _ = _parse("""
namespace MyApp

module Math =
    let add x y = x + y
    type Status = Active | Inactive
""")
        names = {s.name for s in syms}
        assert "Math" in names
        assert "add" in names
        assert "Status" in names

    def test_module_contains_function(self):
        _, edges, _ = _parse("""
namespace MyApp

module Math =
    let add x y = x + y
""")
        contains = [e for e in edges if e.kind == EdgeKind.CONTAINS]
        assert any("Math" in e.source_id and "add" in e.target_id for e in contains)

    def test_function_inside_module_is_method(self):
        syms, _, _ = _parse("""
namespace MyApp

module Math =
    let add x y = x + y
""")
        fn = next(s for s in syms if s.name == "add")
        assert fn.kind == SymbolKind.METHOD


# ── Imports (open) ────────────────────────────────────────────────────────────

class TestOpen:
    def test_open_extracted(self):
        _, _, imports = _parse("module M\nopen System")
        assert "System" in imports

    def test_qualified_open_extracted(self):
        _, _, imports = _parse("module M\nopen System.IO")
        assert any("System" in i for i in imports)

    def test_multiple_opens(self):
        _, _, imports = _parse("module M\nopen System\nopen System.IO\nopen Microsoft.FSharp")
        assert any("System" in i for i in imports)

    def test_open_does_not_create_symbol(self):
        syms, _, _ = _parse("module M\nopen System")
        assert not any(s.name == "System" for s in syms)


# ── .fsx script files ─────────────────────────────────────────────────────────

class TestFsxFile:
    def test_fsx_file_parsed(self):
        p = FileParser("script.fsx", Language.FSHARP, b"let x = 42\n")
        syms, _, _ = p.parse()
        assert any(s.name == "x" for s in syms)
