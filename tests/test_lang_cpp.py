"""Tests for C++ language handler (CHandlerMixin in C++ mode)."""
from __future__ import annotations

import pytest

from tempograph.parser import FileParser
from tempograph.types import EdgeKind, Language, SymbolKind


def _parse(code: str, filename: str = "src/service.cpp"):
    p = FileParser(filename, Language.CPP, code.encode())
    return p.parse()


# ── Free Functions ────────────────────────────────────────────────────────────

class TestFunction:
    def test_function_extracted(self):
        syms, _, _ = _parse("int greet(const std::string& name) {\n    return 0;\n}\n")
        assert any(s.name == "greet" for s in syms)

    def test_function_kind(self):
        syms, _, _ = _parse("int compute(int x) {\n    return x * 2;\n}\n")
        fn = next(s for s in syms if s.name == "compute")
        assert fn.kind == SymbolKind.FUNCTION

    def test_non_static_function_exported(self):
        syms, _, _ = _parse("void process() {}\n")
        fn = next(s for s in syms if s.name == "process")
        assert fn.exported is True

    def test_static_function_not_exported(self):
        syms, _, _ = _parse("static void helper() {}\n")
        fn = next(s for s in syms if s.name == "helper")
        assert fn.exported is False

    def test_multiple_functions(self):
        code = "void foo() {}\nvoid bar() {}\nvoid baz() {}\n"
        syms, _, _ = _parse(code)
        names = {s.name for s in syms}
        assert {"foo", "bar", "baz"}.issubset(names)


# ── Classes ───────────────────────────────────────────────────────────────────

class TestClass:
    def test_class_extracted(self):
        syms, _, _ = _parse("class Animal {};\n")
        assert any(s.name == "Animal" for s in syms)

    def test_class_kind(self):
        syms, _, _ = _parse("class Vehicle {};\n")
        cls = next(s for s in syms if s.name == "Vehicle")
        assert cls.kind == SymbolKind.CLASS

    def test_class_exported(self):
        syms, _, _ = _parse("class Config {};\n")
        cls = next(s for s in syms if s.name == "Config")
        assert cls.exported is True

    def test_class_inline_method_extracted(self):
        code = "class Dog {\npublic:\n    void bark() {}\n};\n"
        syms, _, _ = _parse(code)
        assert any(s.name == "Dog" for s in syms)
        assert any(s.name == "bark" for s in syms)

    def test_inline_method_kind(self):
        code = "class Cat {\npublic:\n    void meow() {}\n};\n"
        syms, _, _ = _parse(code)
        m = next(s for s in syms if s.name == "meow")
        assert m.kind == SymbolKind.METHOD

    def test_inline_method_parent_id(self):
        code = "class Bird {\npublic:\n    void sing() {}\n};\n"
        syms, edges, _ = _parse(code)
        bird = next(s for s in syms if s.name == "Bird")
        sing = next(s for s in syms if s.name == "sing")
        assert sing.parent_id == bird.id

    def test_contains_edge_for_method(self):
        code = "class Tree {\npublic:\n    void grow() {}\n};\n"
        _, edges, _ = _parse(code)
        assert any(e.kind == EdgeKind.CONTAINS for e in edges)


# ── Namespace ─────────────────────────────────────────────────────────────────

class TestNamespace:
    def test_function_in_namespace_extracted(self):
        code = "namespace utils {\n    void helper() {}\n}\n"
        syms, _, _ = _parse(code)
        assert any(s.name == "helper" for s in syms)

    def test_class_in_namespace_extracted(self):
        code = "namespace app {\n    class Service {};\n}\n"
        syms, _, _ = _parse(code)
        assert any(s.name == "Service" for s in syms)


# ── Structs and Enums ─────────────────────────────────────────────────────────

class TestStructEnum:
    def test_struct_extracted(self):
        syms, _, _ = _parse("struct Point {\n    int x;\n    int y;\n};\n")
        assert any(s.name == "Point" for s in syms)

    def test_enum_extracted(self):
        syms, _, _ = _parse("enum Color { RED, GREEN, BLUE };\n")
        assert any(s.name == "Color" for s in syms)

    def test_enum_kind(self):
        syms, _, _ = _parse("enum Status { ACTIVE, INACTIVE };\n")
        e = next(s for s in syms if s.name == "Status")
        assert e.kind == SymbolKind.ENUM


# ── Typedef ───────────────────────────────────────────────────────────────────

class TestTypedef:
    def test_typedef_struct_extracted(self):
        code = "typedef struct {\n    int value;\n} Node;\n"
        syms, _, _ = _parse(code)
        assert any(s.name == "Node" for s in syms)

    def test_typedef_struct_kind(self):
        code = "typedef struct {\n    float x;\n    float y;\n} Vec2;\n"
        syms, _, _ = _parse(code)
        s = next(sym for sym in syms if sym.name == "Vec2")
        assert s.kind == SymbolKind.STRUCT
