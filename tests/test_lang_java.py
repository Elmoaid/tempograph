"""Tests for Java language handler (JavaHandlerMixin)."""
from __future__ import annotations

import pytest

from tempograph.parser import FileParser
from tempograph.types import EdgeKind, Language, SymbolKind


def _parse(code: str, filename: str = "src/main/Service.java"):
    p = FileParser(filename, Language.JAVA, code.encode())
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

    def test_package_private_class_not_exported(self):
        syms, _, _ = _parse("class InternalHelper {}\n")
        cls = next(s for s in syms if s.name == "InternalHelper")
        assert cls.exported is False

    def test_class_with_method(self):
        code = "public class Dog {\n    public void bark() {}\n}\n"
        syms, _, _ = _parse(code)
        assert any(s.name == "Dog" for s in syms)
        assert any(s.name == "bark" for s in syms)

    def test_method_kind(self):
        code = "public class Cat {\n    public void meow() {}\n}\n"
        syms, _, _ = _parse(code)
        m = next(s for s in syms if s.name == "meow")
        assert m.kind == SymbolKind.METHOD

    def test_public_method_exported(self):
        code = "public class Svc {\n    public void run() {}\n}\n"
        syms, _, _ = _parse(code)
        m = next(s for s in syms if s.name == "run")
        assert m.exported is True

    def test_private_method_not_exported(self):
        code = "public class Svc {\n    private void cleanup() {}\n}\n"
        syms, _, _ = _parse(code)
        m = next(s for s in syms if s.name == "cleanup")
        assert m.exported is False

    def test_method_parent_id_set(self):
        code = "public class Bird {\n    public void sing() {}\n}\n"
        syms, _, _ = _parse(code)
        bird = next(s for s in syms if s.name == "Bird")
        sing = next(s for s in syms if s.name == "sing")
        assert sing.parent_id == bird.id

    def test_contains_edge_for_method(self):
        code = "public class Tree {\n    public void grow() {}\n}\n"
        _, edges, _ = _parse(code)
        assert any(e.kind == EdgeKind.CONTAINS for e in edges)

    def test_class_inheritance(self):
        code = "public class Poodle extends Dog {}\n"
        _, edges, _ = _parse(code)
        assert any(e.kind == EdgeKind.INHERITS for e in edges)

    def test_inherits_edge_target(self):
        code = "public class Poodle extends Dog {}\n"
        syms, edges, _ = _parse(code)
        inh = next(e for e in edges if e.kind == EdgeKind.INHERITS)
        assert "Dog" in inh.target_id

    def test_implements_interface(self):
        code = "public class MyList implements Iterable {}\n"
        _, edges, _ = _parse(code)
        assert any(e.kind == EdgeKind.IMPLEMENTS for e in edges)

    def test_multiple_methods(self):
        code = (
            "public class Robot {\n"
            "    public void walk() {}\n"
            "    public void talk() {}\n"
            "    public void stop() {}\n"
            "}\n"
        )
        syms, _, _ = _parse(code)
        names = {s.name for s in syms}
        assert {"walk", "talk", "stop"}.issubset(names)


# ── Interfaces ─────────────────────────────────────────────────────────────────

class TestInterface:
    def test_interface_extracted(self):
        syms, _, _ = _parse("public interface Readable {\n    String read();\n}\n")
        assert any(s.name == "Readable" for s in syms)

    def test_interface_kind(self):
        syms, _, _ = _parse("public interface Writable {\n    void write(String s);\n}\n")
        iface = next(s for s in syms if s.name == "Writable")
        assert iface.kind == SymbolKind.INTERFACE

    def test_interface_method_extracted(self):
        syms, _, _ = _parse("public interface Readable {\n    String read();\n}\n")
        assert any(s.name == "read" for s in syms)


# ── Enums ─────────────────────────────────────────────────────────────────────

class TestEnum:
    def test_enum_extracted(self):
        syms, _, _ = _parse("public enum Color { RED, GREEN, BLUE }\n")
        assert any(s.name == "Color" for s in syms)

    def test_enum_kind(self):
        syms, _, _ = _parse("public enum Status { ACTIVE, INACTIVE }\n")
        e = next(s for s in syms if s.name == "Status")
        assert e.kind == SymbolKind.ENUM


# ── Imports ───────────────────────────────────────────────────────────────────

class TestImports:
    def test_import_extracted(self):
        code = "import java.util.List;\npublic class Foo {}\n"
        _, _, imports = _parse(code)
        assert len(imports) >= 1

    def test_multiple_imports(self):
        code = (
            "import java.util.List;\n"
            "import java.util.Map;\n"
            "import java.io.File;\n"
            "public class Foo {}\n"
        )
        _, _, imports = _parse(code)
        assert len(imports) >= 3


# ── Call edges ────────────────────────────────────────────────────────────────

class TestCallEdges:
    def test_method_calls_tracked(self):
        code = (
            "public class Main {\n"
            "    public void helper() {}\n"
            "    public void run() {\n"
            "        helper();\n"
            "    }\n"
            "}\n"
        )
        _, edges, _ = _parse(code)
        calls = [e for e in edges if e.kind == EdgeKind.CALLS]
        assert len(calls) >= 1
