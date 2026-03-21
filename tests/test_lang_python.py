"""Tests for Python language handler (PythonHandlerMixin)."""
from __future__ import annotations

import pytest

from tempograph.parser import FileParser
from tempograph.types import EdgeKind, Language, SymbolKind


def _parse(code: str, filename: str = "src/service.py"):
    p = FileParser(filename, Language.PYTHON, code.encode())
    return p.parse()


# ── Functions ─────────────────────────────────────────────────────────────────

class TestFunction:
    def test_top_level_function_extracted(self):
        syms, _, _ = _parse("def greet(name):\n    return name\n")
        assert any(s.name == "greet" for s in syms)

    def test_function_kind(self):
        syms, _, _ = _parse("def compute(x):\n    return x * 2\n")
        fn = next(s for s in syms if s.name == "compute")
        assert fn.kind == SymbolKind.FUNCTION

    def test_function_exported(self):
        syms, _, _ = _parse("def public_fn():\n    pass\n")
        fn = next(s for s in syms if s.name == "public_fn")
        assert fn.exported is True

    def test_private_function_not_exported(self):
        """Functions with _ prefix at top level are unexported."""
        syms, _, _ = _parse("def _internal():\n    pass\n")
        fn = next(s for s in syms if s.name == "_internal")
        assert fn.exported is False

    def test_function_line_number(self):
        syms, _, _ = _parse("\ndef process(data):\n    return data\n")
        fn = next(s for s in syms if s.name == "process")
        assert fn.line_start == 2

    def test_multiple_functions(self):
        code = "def foo():\n    pass\ndef bar():\n    pass\ndef baz():\n    pass\n"
        syms, _, _ = _parse(code)
        names = {s.name for s in syms}
        assert {"foo", "bar", "baz"}.issubset(names)

    def test_async_function_extracted(self):
        syms, _, _ = _parse("async def fetch(url):\n    pass\n")
        assert any(s.name == "fetch" for s in syms)


# ── Classes ───────────────────────────────────────────────────────────────────

class TestClass:
    def test_class_extracted(self):
        syms, _, _ = _parse("class Animal:\n    pass\n")
        assert any(s.name == "Animal" for s in syms)

    def test_class_kind(self):
        syms, _, _ = _parse("class Animal:\n    pass\n")
        cls = next(s for s in syms if s.name == "Animal")
        assert cls.kind == SymbolKind.CLASS

    def test_class_exported(self):
        syms, _, _ = _parse("class Animal:\n    pass\n")
        cls = next(s for s in syms if s.name == "Animal")
        assert cls.exported is True

    def test_private_class_not_exported(self):
        syms, _, _ = _parse("class _Internal:\n    pass\n")
        cls = next(s for s in syms if s.name == "_Internal")
        assert cls.exported is False

    def test_class_with_method(self):
        code = "class Dog:\n    def bark(self):\n        return 'woof'\n"
        syms, _, _ = _parse(code)
        assert any(s.name == "Dog" for s in syms)
        assert any(s.name == "bark" for s in syms)

    def test_method_kind_in_class(self):
        code = "class Cat:\n    def meow(self):\n        return 'meow'\n"
        syms, _, _ = _parse(code)
        m = next(s for s in syms if s.name == "meow")
        assert m.kind == SymbolKind.METHOD

    def test_method_parent_id_set(self):
        code = "class Bird:\n    def sing(self):\n        pass\n"
        syms, edges, _ = _parse(code)
        bird = next(s for s in syms if s.name == "Bird")
        sing = next(s for s in syms if s.name == "sing")
        assert sing.parent_id == bird.id

    def test_contains_edge_for_method(self):
        code = "class Tree:\n    def grow(self):\n        pass\n"
        _, edges, _ = _parse(code)
        assert any(e.kind == EdgeKind.CONTAINS for e in edges)

    def test_class_inheritance(self):
        code = "class Poodle(Dog):\n    pass\n"
        _, edges, _ = _parse(code)
        assert any(e.kind == EdgeKind.INHERITS for e in edges)

    def test_inherits_edge_target(self):
        code = "class Poodle(Dog):\n    pass\n"
        syms, edges, _ = _parse(code)
        inh = next(e for e in edges if e.kind == EdgeKind.INHERITS)
        assert "Dog" in inh.target_id

    def test_multiple_methods_in_class(self):
        code = (
            "class Robot:\n"
            "    def walk(self): pass\n"
            "    def talk(self): pass\n"
            "    def stop(self): pass\n"
        )
        syms, _, _ = _parse(code)
        names = {s.name for s in syms}
        assert {"walk", "talk", "stop"}.issubset(names)

    def test_nested_class(self):
        code = "class Outer:\n    class Inner:\n        pass\n"
        syms, _, _ = _parse(code)
        assert any(s.name == "Inner" for s in syms)


# ── Decorators ─────────────────────────────────────────────────────────────────

class TestDecorators:
    def test_property_decorator_yields_property_kind(self):
        code = "class Svc:\n    @property\n    def name(self):\n        return self._name\n"
        syms, _, _ = _parse(code)
        prop = next(s for s in syms if s.name == "name")
        assert prop.kind == SymbolKind.PROPERTY

    def test_staticmethod_yields_function_kind(self):
        code = "class Util:\n    @staticmethod\n    def parse(x):\n        return x\n"
        syms, _, _ = _parse(code)
        fn = next(s for s in syms if s.name == "parse")
        assert fn.kind == SymbolKind.FUNCTION

    def test_test_prefixed_function_is_test_kind(self):
        code = "def test_addition():\n    assert 1 + 1 == 2\n"
        syms, _, _ = _parse(code)
        fn = next(s for s in syms if s.name == "test_addition")
        assert fn.kind == SymbolKind.TEST


# ── Imports ───────────────────────────────────────────────────────────────────

class TestImports:
    def test_import_extracted(self):
        _, _, imports = _parse("import os\n")
        assert any("os" in i for i in imports)

    def test_from_import_extracted(self):
        _, _, imports = _parse("from pathlib import Path\n")
        assert any("pathlib" in i for i in imports)

    def test_multiple_imports(self):
        code = "import os\nimport sys\nfrom pathlib import Path\n"
        _, _, imports = _parse(code)
        assert len(imports) >= 3

    def test_non_import_not_collected(self):
        _, _, imports = _parse("x = 1\n")
        assert len(imports) == 0


# ── Call edges ────────────────────────────────────────────────────────────────

class TestCallEdges:
    def test_function_calls_tracked(self):
        code = (
            "def helper(x):\n    return x\n\n"
            "def main():\n    helper(42)\n"
        )
        _, edges, _ = _parse(code)
        calls = [e for e in edges if e.kind == EdgeKind.CALLS]
        assert len(calls) >= 1
