"""Tests for Ruby language handler (RubyHandlerMixin)."""
from __future__ import annotations

import pytest

from tempograph.parser import FileParser
from tempograph.types import EdgeKind, Language, SymbolKind


def _parse(code: str, filename: str = "app/service.rb"):
    p = FileParser(filename, Language.RUBY, code.encode())
    return p.parse()


# ── Functions ─────────────────────────────────────────────────────────────────

class TestFunction:
    def test_top_level_function_extracted(self):
        syms, _, _ = _parse("def greet(name)\n  \"Hello #{name}\"\nend\n")
        assert any(s.name == "greet" for s in syms)

    def test_function_kind(self):
        syms, _, _ = _parse("def greet(name)\n  \"Hello\"\nend\n")
        fn = next(s for s in syms if s.name == "greet")
        assert fn.kind == SymbolKind.FUNCTION

    def test_function_exported(self):
        syms, _, _ = _parse("def compute(x)\n  x * 2\nend\n")
        fn = next(s for s in syms if s.name == "compute")
        assert fn.exported is True

    def test_function_line_number(self):
        syms, _, _ = _parse("\ndef process(data)\n  data\nend\n")
        fn = next(s for s in syms if s.name == "process")
        assert fn.line_start == 2

    def test_multiple_top_level_functions(self):
        code = "def foo; end\ndef bar; end\ndef baz; end\n"
        syms, _, _ = _parse(code)
        names = {s.name for s in syms}
        assert {"foo", "bar", "baz"}.issubset(names)


# ── Classes ───────────────────────────────────────────────────────────────────

class TestClass:
    def test_class_extracted(self):
        syms, _, _ = _parse("class Animal\nend\n")
        assert any(s.name == "Animal" for s in syms)

    def test_class_kind(self):
        syms, _, _ = _parse("class Animal\nend\n")
        cls = next(s for s in syms if s.name == "Animal")
        assert cls.kind == SymbolKind.CLASS

    def test_class_exported(self):
        syms, _, _ = _parse("class Animal\nend\n")
        cls = next(s for s in syms if s.name == "Animal")
        assert cls.exported is True

    def test_class_with_method(self):
        code = "class Dog\n  def bark\n    'woof'\n  end\nend\n"
        syms, _, _ = _parse(code)
        assert any(s.name == "Dog" for s in syms)
        assert any(s.name == "bark" for s in syms)

    def test_method_kind_in_class(self):
        code = "class Cat\n  def meow\n    'meow'\n  end\nend\n"
        syms, _, _ = _parse(code)
        method = next(s for s in syms if s.name == "meow")
        assert method.kind == SymbolKind.METHOD

    def test_method_parent_id_set(self):
        code = "class Bird\n  def sing\n    'tweet'\n  end\nend\n"
        syms, edges, _ = _parse(code)
        bird = next(s for s in syms if s.name == "Bird")
        sing = next(s for s in syms if s.name == "sing")
        assert sing.parent_id == bird.id

    def test_contains_edge_for_method(self):
        code = "class Tree\n  def grow\n    'growing'\n  end\nend\n"
        syms, edges, _ = _parse(code)
        assert any(e.kind == EdgeKind.CONTAINS for e in edges)

    def test_class_inheritance(self):
        code = "class Poodle < Dog\nend\n"
        syms, edges, _ = _parse(code)
        assert any(e.kind == EdgeKind.INHERITS for e in edges)

    def test_inherits_edge_has_correct_superclass(self):
        code = "class Poodle < Dog\nend\n"
        syms, edges, _ = _parse(code)
        inh = next(e for e in edges if e.kind == EdgeKind.INHERITS)
        assert "Dog" in inh.target_id

    def test_multiple_methods_in_class(self):
        code = (
            "class Robot\n"
            "  def walk; end\n"
            "  def talk; end\n"
            "  def stop; end\n"
            "end\n"
        )
        syms, _, _ = _parse(code)
        names = {s.name for s in syms}
        assert {"walk", "talk", "stop"}.issubset(names)


# ── Modules ───────────────────────────────────────────────────────────────────

class TestModule:
    def test_module_extracted(self):
        syms, _, _ = _parse("module Greetable\nend\n")
        assert any(s.name == "Greetable" for s in syms)

    def test_module_kind(self):
        syms, _, _ = _parse("module Greetable\nend\n")
        mod = next(s for s in syms if s.name == "Greetable")
        assert mod.kind == SymbolKind.MODULE

    def test_module_methods_extracted(self):
        code = "module Formatter\n  def format(x)\n    x.to_s\n  end\nend\n"
        syms, _, _ = _parse(code)
        assert any(s.name == "format" for s in syms)

    def test_nested_class_in_module(self):
        code = "module Services\n  class UserService\n  end\nend\n"
        syms, _, _ = _parse(code)
        assert any(s.name == "UserService" for s in syms)


# ── Imports ───────────────────────────────────────────────────────────────────

class TestImports:
    def test_require_extracted(self):
        _, _, imports = _parse("require 'json'\n")
        assert any("json" in i for i in imports)

    def test_require_relative_extracted(self):
        _, _, imports = _parse("require_relative './helper'\n")
        assert any("helper" in i for i in imports)

    def test_multiple_requires(self):
        code = "require 'json'\nrequire 'yaml'\nrequire 'net/http'\n"
        _, _, imports = _parse(code)
        assert len(imports) >= 2

    def test_non_require_call_not_imported(self):
        _, _, imports = _parse("puts 'hello'\n")
        assert len(imports) == 0


# ── Method visibility ─────────────────────────────────────────────────────────

class TestMethodVisibility:
    def test_public_method_exported(self):
        """Methods without leading _ are exported."""
        code = "class Svc\n  def call(x)\n    x\n  end\nend\n"
        syms, _, _ = _parse(code)
        method = next(s for s in syms if s.name == "call")
        assert method.exported is True

    def test_private_method_not_exported(self):
        """Methods with _ prefix are marked unexported."""
        code = "class Svc\n  def _internal(x)\n    x\n  end\nend\n"
        syms, _, _ = _parse(code)
        method = next(s for s in syms if s.name == "_internal")
        assert method.exported is False


# ── Call edges ────────────────────────────────────────────────────────────────

class TestCallEdges:
    def test_method_calls_tracked(self):
        """Method body that calls another function should produce CALLS edges."""
        code = (
            "def helper(x)\n  x\nend\n\n"
            "def main()\n  helper(42)\nend\n"
        )
        syms, edges, _ = _parse(code)
        calls = [e for e in edges if e.kind == EdgeKind.CALLS]
        assert len(calls) >= 1
