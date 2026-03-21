"""Tests for Dart language handler."""
from tempograph.parser import FileParser
from tempograph.types import Language, EdgeKind, SymbolKind


class TestDartParser:
    def _parse(self, code: str):
        p = FileParser("test.dart", Language.DART, code.encode())
        symbols, edges, imports = p.parse()
        return symbols, edges, imports

    def test_basic_function(self):
        symbols, _, _ = self._parse(
            "String greet(String name) {\n  return 'Hello $name';\n}"
        )
        funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
        assert len(funcs) >= 1
        assert any(f.name == "greet" for f in funcs)
        func = next(f for f in funcs if f.name == "greet")
        assert func.exported is True

    def test_private_function(self):
        symbols, _, _ = self._parse(
            "void _helper() {\n  print('private');\n}"
        )
        funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
        assert len(funcs) >= 1
        func = next((f for f in funcs if f.name == "_helper"), None)
        assert func is not None
        assert func.exported is False

    def test_class_detection(self):
        symbols, edges, _ = self._parse(
            "class Animal {\n  String name;\n  void speak() {}\n}"
        )
        classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
        assert len(classes) == 1
        assert classes[0].name == "Animal"
        assert classes[0].exported is True

        methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
        assert any(m.name == "speak" for m in methods)

    def test_abstract_class(self):
        symbols, _, _ = self._parse(
            "abstract class Shape {\n  double area();\n}"
        )
        classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
        assert len(classes) >= 1
        assert any(c.name == "Shape" for c in classes)

    def test_mixin_declaration(self):
        symbols, _, _ = self._parse(
            "mixin Flyable {\n  void fly() {\n    print('flying');\n  }\n}"
        )
        classes = [s for s in symbols if s.kind in (SymbolKind.CLASS, SymbolKind.INTERFACE)]
        assert len(classes) >= 1
        assert any(c.name == "Flyable" for c in classes)

    def test_class_methods(self):
        symbols, edges, _ = self._parse(
            "class Calculator {\n  int add(int a, int b) => a + b;\n  int subtract(int a, int b) => a - b;\n}"
        )
        methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
        names = {m.name for m in methods}
        assert "add" in names
        assert "subtract" in names

        contains = [e for e in edges if e.kind == EdgeKind.CONTAINS]
        assert len(contains) >= 2

    def test_contains_edge(self):
        symbols, edges, _ = self._parse(
            "class Foo {\n  void bar() {}\n}"
        )
        contains = [e for e in edges if e.kind == EdgeKind.CONTAINS]
        assert len(contains) >= 1
        assert any("bar" in e.target_id for e in contains)

    def test_enum_detection(self):
        symbols, _, _ = self._parse(
            "enum Color { red, green, blue }"
        )
        # Enum should be detected as a class or similar top-level symbol
        top_level = [s for s in symbols if s.name == "Color"]
        assert len(top_level) >= 1

    def test_private_class(self):
        symbols, _, _ = self._parse(
            "class _InternalState {\n  int count = 0;\n}"
        )
        classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
        assert len(classes) >= 1
        cls = next((c for c in classes if c.name == "_InternalState"), None)
        assert cls is not None
        assert cls.exported is False

    def test_import_extraction(self):
        _, _, imports = self._parse(
            "import 'dart:async';\nimport 'package:flutter/material.dart';\n"
        )
        assert any("async" in imp for imp in imports)
        assert any("flutter" in imp or "material" in imp for imp in imports)

    def test_multiple_classes(self):
        symbols, _, _ = self._parse(
            "class Dog {}\nclass Cat {}\nclass Bird {}"
        )
        classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
        names = {c.name for c in classes}
        assert "Dog" in names
        assert "Cat" in names
        assert "Bird" in names

    def test_void_function(self):
        symbols, _, _ = self._parse(
            "void main() {\n  print('hello');\n}"
        )
        funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
        assert any(f.name == "main" for f in funcs)

    def test_class_with_private_method(self):
        symbols, _, _ = self._parse(
            "class MyService {\n  void process() {}\n  void _internalProcess() {}\n}"
        )
        methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
        public = [m for m in methods if m.exported]
        private = [m for m in methods if not m.exported]
        assert any(m.name == "process" for m in public)
        assert any(m.name == "_internalProcess" for m in private)
