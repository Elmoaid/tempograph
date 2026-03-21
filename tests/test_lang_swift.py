"""Tests for Swift language handler (SwiftHandlerMixin)."""
from __future__ import annotations

import pytest

from tempograph.parser import FileParser
from tempograph.types import EdgeKind, Language, SymbolKind


def _parse(code: str, filename: str = "Sources/Service.swift"):
    p = FileParser(filename, Language.SWIFT, code.encode())
    return p.parse()


# ── Functions ─────────────────────────────────────────────────────────────────

class TestFunction:
    def test_top_level_function_extracted(self):
        syms, _, _ = _parse("public func greet(name: String) -> String {\n    return name\n}\n")
        assert any(s.name == "greet" for s in syms)

    def test_function_kind(self):
        syms, _, _ = _parse("public func compute(x: Int) -> Int {\n    return x * 2\n}\n")
        fn = next(s for s in syms if s.name == "compute")
        assert fn.kind == SymbolKind.FUNCTION

    def test_public_function_exported(self):
        syms, _, _ = _parse("public func process() {}\n")
        fn = next(s for s in syms if s.name == "process")
        assert fn.exported is True

    def test_internal_function_not_exported(self):
        """Functions without public/open are internal (not exported)."""
        syms, _, _ = _parse("func internalHelper() {}\n")
        fn = next(s for s in syms if s.name == "internalHelper")
        assert fn.exported is False

    def test_multiple_functions(self):
        code = "public func foo() {}\npublic func bar() {}\npublic func baz() {}\n"
        syms, _, _ = _parse(code)
        names = {s.name for s in syms}
        assert {"foo", "bar", "baz"}.issubset(names)


# ── Classes ───────────────────────────────────────────────────────────────────

class TestClass:
    def test_class_extracted(self):
        syms, _, _ = _parse("public class Animal {\n}\n")
        assert any(s.name == "Animal" for s in syms)

    def test_class_kind(self):
        syms, _, _ = _parse("public class Vehicle {\n}\n")
        cls = next(s for s in syms if s.name == "Vehicle")
        assert cls.kind == SymbolKind.CLASS

    def test_public_class_exported(self):
        syms, _, _ = _parse("public class Animal {\n}\n")
        cls = next(s for s in syms if s.name == "Animal")
        assert cls.exported is True

    def test_class_with_method(self):
        code = "public class Dog {\n    public func bark() -> String {\n        return \"woof\"\n    }\n}\n"
        syms, _, _ = _parse(code)
        assert any(s.name == "Dog" for s in syms)
        assert any(s.name == "bark" for s in syms)

    def test_method_kind_in_class(self):
        code = "public class Cat {\n    public func meow() {}\n}\n"
        syms, _, _ = _parse(code)
        m = next(s for s in syms if s.name == "meow")
        assert m.kind == SymbolKind.METHOD

    def test_method_parent_id_set(self):
        code = "public class Bird {\n    public func sing() {}\n}\n"
        syms, edges, _ = _parse(code)
        bird = next(s for s in syms if s.name == "Bird")
        sing = next(s for s in syms if s.name == "sing")
        assert sing.parent_id == bird.id

    def test_contains_edge_for_method(self):
        code = "public class Tree {\n    public func grow() {}\n}\n"
        _, edges, _ = _parse(code)
        assert any(e.kind == EdgeKind.CONTAINS for e in edges)


# ── Structs ───────────────────────────────────────────────────────────────────

class TestStruct:
    def test_struct_extracted(self):
        syms, _, _ = _parse("public struct Point {\n    var x: Double\n    var y: Double\n}\n")
        assert any(s.name == "Point" for s in syms)

    def test_struct_kind(self):
        syms, _, _ = _parse("public struct Vector {\n    var dx: Float\n}\n")
        s = next(sym for sym in syms if sym.name == "Vector")
        assert s.kind == SymbolKind.STRUCT

    def test_struct_method_extracted(self):
        code = "public struct Circle {\n    public func area() -> Double { return 3.14 }\n}\n"
        syms, _, _ = _parse(code)
        assert any(s.name == "area" for s in syms)


# ── Enums ─────────────────────────────────────────────────────────────────────

class TestEnum:
    def test_enum_extracted(self):
        syms, _, _ = _parse("public enum Color {\n    case red, green, blue\n}\n")
        assert any(s.name == "Color" for s in syms)

    def test_enum_kind(self):
        syms, _, _ = _parse("public enum Direction {\n    case north, south\n}\n")
        e = next(s for s in syms if s.name == "Direction")
        assert e.kind == SymbolKind.ENUM


# ── Protocols ─────────────────────────────────────────────────────────────────

class TestProtocol:
    def test_protocol_extracted(self):
        syms, _, _ = _parse("public protocol Drawable {\n    func draw()\n}\n")
        assert any(s.name == "Drawable" for s in syms)

    def test_protocol_kind(self):
        syms, _, _ = _parse("public protocol Configurable {\n    func configure()\n}\n")
        p = next(s for s in syms if s.name == "Configurable")
        assert p.kind == SymbolKind.INTERFACE


# ── Extensions ────────────────────────────────────────────────────────────────

class TestExtension:
    def test_extension_method_extracted(self):
        """Extension methods on an existing type should be extracted."""
        code = (
            "public class Dog {\n}\n\n"
            "extension Dog {\n"
            "    public func fetch() {}\n"
            "}\n"
        )
        syms, _, _ = _parse(code)
        assert any(s.name == "fetch" for s in syms)

    def test_extension_contains_edge(self):
        code = (
            "public class Cat {\n}\n\n"
            "extension Cat {\n"
            "    public func purr() {}\n"
            "}\n"
        )
        _, edges, _ = _parse(code)
        assert any(e.kind == EdgeKind.CONTAINS for e in edges)
