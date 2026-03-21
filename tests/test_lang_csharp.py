"""Tests for C# language handler (CsharpHandlerMixin)."""
from __future__ import annotations

import pytest

from tempograph.parser import FileParser
from tempograph.types import EdgeKind, Language, SymbolKind


def _parse(code: str, filename: str = "src/Service.cs"):
    p = FileParser(filename, Language.CSHARP, code.encode())
    return p.parse()


# ── Classes ───────────────────────────────────────────────────────────────────

class TestClass:
    def test_class_extracted(self):
        syms, _, _ = _parse("public class Animal {}\n")
        assert any(s.name == "Animal" for s in syms)

    def test_class_kind(self):
        syms, _, _ = _parse("public class Animal {}\n")
        cls = next(s for s in syms if s.name == "Animal")
        assert cls.kind == SymbolKind.CLASS

    def test_public_class_exported(self):
        syms, _, _ = _parse("public class Animal {}\n")
        cls = next(s for s in syms if s.name == "Animal")
        assert cls.exported is True

    def test_internal_class_not_exported(self):
        syms, _, _ = _parse("internal class Helper {}\n")
        cls = next(s for s in syms if s.name == "Helper")
        assert cls.exported is False

    def test_class_with_method(self):
        code = "public class Dog {\n    public void Bark() {}\n}\n"
        syms, _, _ = _parse(code)
        assert any(s.name == "Dog" for s in syms)
        assert any(s.name == "Bark" for s in syms)

    def test_method_kind(self):
        code = "public class Cat {\n    public void Meow() {}\n}\n"
        syms, _, _ = _parse(code)
        m = next(s for s in syms if s.name == "Meow")
        assert m.kind == SymbolKind.METHOD

    def test_public_method_exported(self):
        code = "public class Svc {\n    public void Run() {}\n}\n"
        syms, _, _ = _parse(code)
        m = next(s for s in syms if s.name == "Run")
        assert m.exported is True

    def test_private_method_not_exported(self):
        code = "public class Svc {\n    private void Cleanup() {}\n}\n"
        syms, _, _ = _parse(code)
        m = next(s for s in syms if s.name == "Cleanup")
        assert m.exported is False

    def test_method_parent_id_set(self):
        code = "public class Bird {\n    public void Sing() {}\n}\n"
        syms, edges, _ = _parse(code)
        bird = next(s for s in syms if s.name == "Bird")
        sing = next(s for s in syms if s.name == "Sing")
        assert sing.parent_id == bird.id

    def test_contains_edge_for_method(self):
        code = "public class Tree {\n    public void Grow() {}\n}\n"
        _, edges, _ = _parse(code)
        assert any(e.kind == EdgeKind.CONTAINS for e in edges)

    def test_class_inheritance(self):
        code = "public class Poodle : Dog {}\n"
        _, edges, _ = _parse(code)
        assert any(e.kind == EdgeKind.INHERITS for e in edges)

    def test_inherits_edge_target(self):
        code = "public class Poodle : Dog {}\n"
        syms, edges, _ = _parse(code)
        inh = next(e for e in edges if e.kind == EdgeKind.INHERITS)
        assert "Dog" in inh.target_id

    def test_implements_interface(self):
        """C# interfaces detected by I + uppercase convention."""
        code = "public class MyList : IEnumerable {}\n"
        _, edges, _ = _parse(code)
        assert any(e.kind == EdgeKind.IMPLEMENTS for e in edges)

    def test_multiple_methods(self):
        code = (
            "public class Robot {\n"
            "    public void Walk() {}\n"
            "    public void Talk() {}\n"
            "    public void Stop() {}\n"
            "}\n"
        )
        syms, _, _ = _parse(code)
        names = {s.name for s in syms}
        assert {"Walk", "Talk", "Stop"}.issubset(names)


# ── Interfaces ─────────────────────────────────────────────────────────────────

class TestInterface:
    def test_interface_extracted(self):
        syms, _, _ = _parse("public interface IReadable {\n    string Read();\n}\n")
        assert any(s.name == "IReadable" for s in syms)

    def test_interface_kind(self):
        syms, _, _ = _parse("public interface IWritable {\n    void Write(string s);\n}\n")
        iface = next(s for s in syms if s.name == "IWritable")
        assert iface.kind == SymbolKind.INTERFACE


# ── Structs ───────────────────────────────────────────────────────────────────

class TestStruct:
    def test_struct_extracted(self):
        syms, _, _ = _parse("public struct Point {\n    public int X;\n    public int Y;\n}\n")
        assert any(s.name == "Point" for s in syms)

    def test_struct_kind(self):
        syms, _, _ = _parse("public struct Vector {\n    public float X;\n}\n")
        s = next(sym for sym in syms if sym.name == "Vector")
        assert s.kind == SymbolKind.STRUCT


# ── Enums ─────────────────────────────────────────────────────────────────────

class TestEnum:
    def test_enum_extracted(self):
        syms, _, _ = _parse("public enum Color { Red, Green, Blue }\n")
        assert any(s.name == "Color" for s in syms)

    def test_enum_kind(self):
        syms, _, _ = _parse("public enum Status { Active, Inactive }\n")
        e = next(s for s in syms if s.name == "Status")
        assert e.kind == SymbolKind.ENUM


# ── Properties ────────────────────────────────────────────────────────────────

class TestProperty:
    def test_property_extracted(self):
        code = "public class User {\n    public string Name { get; set; }\n}\n"
        syms, _, _ = _parse(code)
        assert any(s.name == "Name" for s in syms)

    def test_property_kind(self):
        code = "public class Config {\n    public int Port { get; set; }\n}\n"
        syms, _, _ = _parse(code)
        prop = next(s for s in syms if s.name == "Port")
        assert prop.kind == SymbolKind.PROPERTY


# ── Imports ───────────────────────────────────────────────────────────────────

class TestImports:
    def test_using_directive_extracted(self):
        code = "using System;\npublic class Foo {}\n"
        _, _, imports = _parse(code)
        assert len(imports) >= 1

    def test_multiple_usings(self):
        code = "using System;\nusing System.IO;\nusing System.Collections.Generic;\npublic class Foo {}\n"
        _, _, imports = _parse(code)
        assert len(imports) >= 3


# ── Namespace ─────────────────────────────────────────────────────────────────

class TestNamespace:
    def test_class_in_namespace_extracted(self):
        code = "namespace MyApp {\n    public class Service {}\n}\n"
        syms, _, _ = _parse(code)
        assert any(s.name == "Service" for s in syms)


# ── Call edges ────────────────────────────────────────────────────────────────

class TestCallEdges:
    def test_method_calls_tracked(self):
        code = (
            "public class Main {\n"
            "    public void Helper() {}\n"
            "    public void Run() {\n"
            "        Helper();\n"
            "    }\n"
            "}\n"
        )
        _, edges, _ = _parse(code)
        calls = [e for e in edges if e.kind == EdgeKind.CALLS]
        assert len(calls) >= 1
