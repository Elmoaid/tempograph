"""Tests for JavaScript language handler (JSHandlerMixin with Language.JAVASCRIPT)."""
from __future__ import annotations

import pytest

from tempograph.parser import FileParser
from tempograph.types import EdgeKind, Language, SymbolKind


def _parse(code: str, filename: str = "src/service.js"):
    p = FileParser(filename, Language.JAVASCRIPT, code.encode())
    return p.parse()


# ── Functions ─────────────────────────────────────────────────────────────────

class TestFunction:
    def test_named_function_extracted(self):
        syms, _, _ = _parse("function greet(name) { return name; }\n")
        assert any(s.name == "greet" for s in syms)

    def test_function_kind(self):
        syms, _, _ = _parse("function compute(x) { return x * 2; }\n")
        fn = next(s for s in syms if s.name == "compute")
        assert fn.kind == SymbolKind.FUNCTION

    def test_top_level_function_not_exported_without_export(self):
        syms, _, _ = _parse("function helper() {}\n")
        fn = next(s for s in syms if s.name == "helper")
        assert fn.exported is False

    def test_exported_function(self):
        syms, _, _ = _parse("export function greet(name) { return name; }\n")
        fn = next(s for s in syms if s.name == "greet")
        assert fn.exported is True

    def test_multiple_functions(self):
        code = "function foo() {}\nfunction bar() {}\nfunction baz() {}\n"
        syms, _, _ = _parse(code)
        names = {s.name for s in syms}
        assert {"foo", "bar", "baz"}.issubset(names)

    def test_arrow_function_in_const(self):
        syms, _, _ = _parse("const add = (x, y) => x + y;\n")
        assert any(s.name == "add" for s in syms)

    def test_exported_arrow_function(self):
        syms, _, _ = _parse("export const greet = (name) => name;\n")
        fn = next(s for s in syms if s.name == "greet")
        assert fn.exported is True

    def test_function_expression_in_const(self):
        syms, _, _ = _parse("const process = function(data) { return data; };\n")
        assert any(s.name == "process" for s in syms)


# ── Classes ───────────────────────────────────────────────────────────────────

class TestClass:
    def test_class_extracted(self):
        syms, _, _ = _parse("class Animal {}\n")
        assert any(s.name == "Animal" for s in syms)

    def test_class_kind(self):
        syms, _, _ = _parse("class Animal {}\n")
        cls = next(s for s in syms if s.name == "Animal")
        assert cls.kind == SymbolKind.CLASS

    def test_exported_class(self):
        syms, _, _ = _parse("export class Animal {}\n")
        cls = next(s for s in syms if s.name == "Animal")
        assert cls.exported is True

    def test_class_with_method(self):
        code = "class Dog {\n    bark() { return 'woof'; }\n}\n"
        syms, _, _ = _parse(code)
        assert any(s.name == "Dog" for s in syms)
        assert any(s.name == "bark" for s in syms)

    def test_method_kind(self):
        code = "class Cat {\n    meow() { return 'meow'; }\n}\n"
        syms, _, _ = _parse(code)
        m = next(s for s in syms if s.name == "meow")
        assert m.kind == SymbolKind.METHOD

    def test_method_parent_id_set(self):
        code = "class Bird {\n    sing() {}\n}\n"
        syms, edges, _ = _parse(code)
        bird = next(s for s in syms if s.name == "Bird")
        sing = next(s for s in syms if s.name == "sing")
        assert sing.parent_id == bird.id

    def test_contains_edge_for_method(self):
        code = "class Tree {\n    grow() {}\n}\n"
        _, edges, _ = _parse(code)
        assert any(e.kind == EdgeKind.CONTAINS for e in edges)

    def test_class_inheritance(self):
        code = "class Poodle extends Dog {}\n"
        _, edges, _ = _parse(code)
        assert any(e.kind == EdgeKind.INHERITS for e in edges)


# ── Imports ───────────────────────────────────────────────────────────────────

class TestImports:
    def test_esm_import_extracted(self):
        _, _, imports = _parse("import path from 'path';\n")
        assert len(imports) >= 1

    def test_named_esm_import_extracted(self):
        _, _, imports = _parse("import { readFile } from 'fs';\n")
        assert len(imports) >= 1

    def test_multiple_imports(self):
        code = "import path from 'path';\nimport fs from 'fs';\nimport os from 'os';\n"
        _, _, imports = _parse(code)
        assert len(imports) >= 3


# ── CommonJS (require) ────────────────────────────────────────────────────────

class TestCommonJS:
    def test_cjs_module_exports_function(self):
        code = "module.exports = function handler(req, res) {\n    res.send('ok');\n};\n"
        syms, _, _ = _parse(code)
        assert any(s.exported for s in syms)

    def test_cjs_exports_property_function(self):
        code = "exports.normalize = function(type) { return type.toLowerCase(); };\n"
        syms, _, _ = _parse(code)
        fn = next((s for s in syms if s.name == "normalize"), None)
        assert fn is not None
        assert fn.exported is True


# ── Call edges ────────────────────────────────────────────────────────────────

class TestCallEdges:
    def test_function_calls_tracked(self):
        code = (
            "function helper(x) { return x; }\n"
            "function main() { helper(42); }\n"
        )
        _, edges, _ = _parse(code)
        calls = [e for e in edges if e.kind == EdgeKind.CALLS]
        assert len(calls) >= 1
