"""Tests for Dart language handler (DartHandlerMixin)."""
from __future__ import annotations

import pytest

from tempograph.parser import FileParser
from tempograph.types import Language, EdgeKind, SymbolKind


def _parse(code: str, filename: str = "lib/main.dart"):
    p = FileParser(filename, Language.DART, code.encode())
    return p.parse()


# ── Top-level functions ──────────────────────────────────────────────────────

class TestTopLevelFunction:
    def test_simple_function(self):
        syms, _, _ = _parse("String greet(String name) => 'Hello, $name!';")
        assert any(s.name == "greet" and s.kind == SymbolKind.FUNCTION for s in syms)

    def test_async_function(self):
        syms, _, _ = _parse("""
Future<void> fetchData(String url) async {
  final result = await http.get(url);
  print(result);
}
""")
        assert any(s.name == "fetchData" and s.kind == SymbolKind.FUNCTION for s in syms)

    def test_function_exported(self):
        syms, _, _ = _parse("void doStuff() {}")
        fn = next(s for s in syms if s.name == "doStuff")
        assert fn.exported is True


# ── Classes ──────────────────────────────────────────────────────────────────

class TestClassBasic:
    def test_class_extraction(self):
        syms, _, _ = _parse("""
class Dog {
  void bark() {}
}
""")
        names = {s.name for s in syms}
        assert "Dog" in names
        assert "bark" in names
        dog = next(s for s in syms if s.name == "Dog")
        assert dog.kind == SymbolKind.CLASS

    def test_class_contains_method(self):
        _, edges, _ = _parse("""
class Dog {
  void bark() {}
}
""")
        contains = [e for e in edges if e.kind == EdgeKind.CONTAINS]
        assert len(contains) >= 1
        assert any("Dog" in e.source_id and "bark" in e.target_id for e in contains)


class TestAbstractClass:
    def test_abstract_class(self):
        syms, _, _ = _parse("""
abstract class Animal {
  void speak();
}
""")
        animal = next(s for s in syms if s.name == "Animal")
        assert animal.kind == SymbolKind.CLASS


class TestClassInheritance:
    def test_extends(self):
        syms, edges, _ = _parse("""
class Dog extends Animal {
  void bark() {}
}
""")
        inherits = [e for e in edges if e.kind == EdgeKind.INHERITS]
        assert len(inherits) >= 1
        assert any("Dog" in e.source_id and "Animal" in e.target_id for e in inherits)

    def test_implements(self):
        syms, edges, _ = _parse("""
class Dog implements Comparable<Dog>, Serializable {
  void bark() {}
}
""")
        impl_edges = [e for e in edges if e.kind == EdgeKind.IMPLEMENTS]
        assert len(impl_edges) >= 1
        targets = {e.target_id for e in impl_edges}
        assert any("Comparable" in t for t in targets)


# ── Mixin ────────────────────────────────────────────────────────────────────

class TestMixin:
    def test_mixin_extraction(self):
        syms, _, _ = _parse("""
mixin Flyable {
  void fly() => print('Flying!');
}
""")
        flyable = next(s for s in syms if s.name == "Flyable")
        assert flyable.kind == SymbolKind.CLASS

    def test_mixin_methods(self):
        syms, edges, _ = _parse("""
mixin Flyable {
  void fly() => print('Flying!');
}
""")
        assert any(s.name == "fly" for s in syms)
        contains = [e for e in edges if e.kind == EdgeKind.CONTAINS]
        assert any("Flyable" in e.source_id and "fly" in e.target_id for e in contains)


# ── Extension ────────────────────────────────────────────────────────────────

class TestExtension:
    def test_named_extension(self):
        syms, _, _ = _parse("""
extension StringExt on String {
  String capitalize() => this[0].toUpperCase() + substring(1);
}
""")
        ext = next(s for s in syms if s.name == "StringExt")
        assert ext.kind == SymbolKind.CLASS

    def test_extension_methods(self):
        syms, edges, _ = _parse("""
extension StringExt on String {
  String capitalize() => this[0].toUpperCase() + substring(1);
}
""")
        assert any(s.name == "capitalize" for s in syms)


# ── Enum ─────────────────────────────────────────────────────────────────────

class TestEnum:
    def test_enum_extraction(self):
        syms, _, _ = _parse("""
enum Status { active, inactive, pending }
""")
        status = next(s for s in syms if s.name == "Status")
        assert status.kind == SymbolKind.ENUM


# ── Top-level variables ─────────────────────────────────────────────────────

class TestTopLevelVariable:
    def test_const_variable(self):
        syms, _, _ = _parse("const pi = 3.14159;")
        pi = next(s for s in syms if s.name == "pi")
        assert pi.kind == SymbolKind.CONSTANT

    def test_final_variable(self):
        syms, _, _ = _parse("final appName = 'MyApp';")
        app = next(s for s in syms if s.name == "appName")
        assert app.kind == SymbolKind.VARIABLE

    def test_var_variable(self):
        syms, _, _ = _parse("var count = 0;")
        count = next(s for s in syms if s.name == "count")
        assert count.kind == SymbolKind.VARIABLE


# ── Constructors ─────────────────────────────────────────────────────────────

class TestConstructor:
    def test_default_constructor(self):
        syms, _, _ = _parse("""
class MyClass {
  int value;
  MyClass(this.value);
}
""")
        assert any(s.name == "MyClass" and s.kind == SymbolKind.METHOD for s in syms)

    def test_named_constructor(self):
        syms, _, _ = _parse("""
class MyClass {
  int value;
  MyClass.named(int v) : value = v;
}
""")
        assert any(s.name == "MyClass.named" and s.kind == SymbolKind.METHOD for s in syms)

    def test_factory_constructor(self):
        syms, _, _ = _parse("""
class MyClass {
  factory MyClass.create(int v) {
    return MyClass(v);
  }
}
""")
        assert any(s.name == "MyClass.create" and s.kind == SymbolKind.METHOD for s in syms)


# ── Private symbols ─────────────────────────────────────────────────────────

class TestPrivateSymbol:
    def test_private_function(self):
        syms, _, _ = _parse("void _privateFunc() {}")
        fn = next(s for s in syms if s.name == "_privateFunc")
        assert fn.exported is False

    def test_private_class(self):
        syms, _, _ = _parse("""
class _InternalClass {
  void doStuff() {}
}
""")
        cls = next(s for s in syms if s.name == "_InternalClass")
        assert cls.exported is False

    def test_public_function_exported(self):
        syms, _, _ = _parse("void publicFunc() {}")
        fn = next(s for s in syms if s.name == "publicFunc")
        assert fn.exported is True


# ── Imports ──────────────────────────────────────────────────────────────────

class TestImports:
    def test_import_dart_core(self):
        _, _, imports = _parse("import 'dart:io';")
        assert any("dart:io" in imp for imp in imports)

    def test_import_package(self):
        _, _, imports = _parse("import 'package:flutter/material.dart';")
        assert any("flutter/material.dart" in imp for imp in imports)

    def test_multiple_imports(self):
        _, _, imports = _parse("""
import 'dart:io';
import 'dart:async';
import 'package:http/http.dart';
""")
        assert len(imports) == 3


# ── Type alias ───────────────────────────────────────────────────────────────

class TestTypeAlias:
    def test_typedef(self):
        syms, _, _ = _parse("typedef StringList = List<String>;")
        ta = next(s for s in syms if s.name == "StringList")
        assert ta.kind == SymbolKind.TYPE_ALIAS


# ── Integration ──────────────────────────────────────────────────────────────

class TestIntegration:
    def test_full_file(self):
        code = """
import 'dart:io';
import 'package:flutter/material.dart';

const pi = 3.14159;

enum Status { active, inactive, pending }

abstract class Animal {
  String name;
  Animal(this.name);
  void speak();
}

class Dog extends Animal implements Comparable<Dog> {
  Dog(String name) : super(name);

  @override
  void speak() => print('Woof!');

  @override
  int compareTo(Dog other) => name.compareTo(other.name);
}

mixin Flyable {
  void fly() => print('Flying!');
}

extension StringExt on String {
  String capitalize() => this[0].toUpperCase() + substring(1);
}

String greet(String name) => 'Hello, $name!';

void _internalHelper() {}
"""
        syms, edges, imports = _parse(code)
        names = {s.name for s in syms}
        assert "pi" in names
        assert "Status" in names
        assert "Animal" in names
        assert "Dog" in names
        assert "Flyable" in names
        assert "StringExt" in names
        assert "greet" in names
        assert "_internalHelper" in names
        assert len(imports) == 2

        # Check private
        helper = next(s for s in syms if s.name == "_internalHelper")
        assert helper.exported is False

        # Check inheritance
        inherits = [e for e in edges if e.kind == EdgeKind.INHERITS]
        assert any("Dog" in e.source_id for e in inherits)
