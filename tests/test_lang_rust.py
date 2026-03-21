"""Tests for Rust language handler (RustHandlerMixin)."""
from __future__ import annotations

import pytest

from tempograph.parser import FileParser
from tempograph.types import EdgeKind, Language, SymbolKind


def _parse(code: str, filename: str = "src/lib.rs"):
    p = FileParser(filename, Language.RUST, code.encode())
    return p.parse()


# ── Functions ─────────────────────────────────────────────────────────────────

class TestFunction:
    def test_top_level_function_extracted(self):
        syms, _, _ = _parse("fn greet(name: &str) -> String {\n    name.to_string()\n}\n")
        assert any(s.name == "greet" for s in syms)

    def test_function_kind(self):
        syms, _, _ = _parse("fn compute(x: i32) -> i32 {\n    x * 2\n}\n")
        fn = next(s for s in syms if s.name == "compute")
        assert fn.kind == SymbolKind.FUNCTION

    def test_function_line_number(self):
        syms, _, _ = _parse("\nfn process(data: &str) {}\n")
        fn = next(s for s in syms if s.name == "process")
        assert fn.line_start == 2

    def test_multiple_functions(self):
        code = "fn foo() {}\nfn bar() {}\nfn baz() {}\n"
        syms, _, _ = _parse(code)
        names = {s.name for s in syms}
        assert {"foo", "bar", "baz"}.issubset(names)

    def test_test_attribute_yields_test_kind(self):
        code = "#[test]\nfn test_addition() {\n    assert_eq!(1 + 1, 2);\n}\n"
        syms, _, _ = _parse(code)
        fn = next(s for s in syms if s.name == "test_addition")
        assert fn.kind == SymbolKind.TEST


# ── Structs ───────────────────────────────────────────────────────────────────

class TestStruct:
    def test_struct_extracted(self):
        syms, _, _ = _parse("struct User {\n    name: String,\n}\n")
        assert any(s.name == "User" for s in syms)

    def test_struct_kind(self):
        syms, _, _ = _parse("struct Config {\n    debug: bool,\n}\n")
        s = next(sym for sym in syms if sym.name == "Config")
        assert s.kind == SymbolKind.STRUCT

    def test_impl_method_extracted(self):
        code = (
            "struct Dog;\n\n"
            "impl Dog {\n"
            "    fn bark(&self) -> &str {\n"
            "        \"woof\"\n"
            "    }\n"
            "}\n"
        )
        syms, _, _ = _parse(code)
        assert any(s.name == "bark" for s in syms)

    def test_impl_method_kind(self):
        code = (
            "struct Cat;\n\n"
            "impl Cat {\n"
            "    fn meow(&self) {}\n"
            "}\n"
        )
        syms, _, _ = _parse(code)
        m = next(s for s in syms if s.name == "meow")
        assert m.kind == SymbolKind.METHOD

    def test_impl_method_parent_id(self):
        code = (
            "struct Bird;\n\n"
            "impl Bird {\n"
            "    fn sing(&self) {}\n"
            "}\n"
        )
        syms, _, _ = _parse(code)
        bird = next(s for s in syms if s.name == "Bird")
        sing = next(s for s in syms if s.name == "sing")
        assert sing.parent_id == bird.id

    def test_contains_edge_for_impl_method(self):
        code = (
            "struct Node;\n\n"
            "impl Node {\n"
            "    fn value(&self) -> i32 { 0 }\n"
            "}\n"
        )
        _, edges, _ = _parse(code)
        assert any(e.kind == EdgeKind.CONTAINS for e in edges)


# ── Enums ─────────────────────────────────────────────────────────────────────

class TestEnum:
    def test_enum_extracted(self):
        syms, _, _ = _parse("enum Color {\n    Red,\n    Green,\n    Blue,\n}\n")
        assert any(s.name == "Color" for s in syms)

    def test_enum_kind(self):
        syms, _, _ = _parse("enum Status {\n    Active,\n    Inactive,\n}\n")
        e = next(s for s in syms if s.name == "Status")
        assert e.kind == SymbolKind.ENUM


# ── Traits ────────────────────────────────────────────────────────────────────

class TestTrait:
    def test_trait_extracted(self):
        syms, _, _ = _parse("trait Greetable {\n    fn greet(&self) -> String;\n}\n")
        assert any(s.name == "Greetable" for s in syms)

    def test_trait_kind(self):
        syms, _, _ = _parse("trait Drawable {\n    fn draw(&self);\n}\n")
        t = next(s for s in syms if s.name == "Drawable")
        assert t.kind == SymbolKind.TRAIT

    def test_trait_method_signatures_extracted(self):
        code = "trait Shape {\n    fn area(&self) -> f64;\n    fn perimeter(&self) -> f64;\n}\n"
        syms, _, _ = _parse(code)
        names = {s.name for s in syms}
        assert {"area", "perimeter"}.issubset(names)

    def test_impl_trait_produces_implements_edge(self):
        code = (
            "trait Greetable {\n    fn greet(&self) -> String;\n}\n\n"
            "struct Robot;\n\n"
            "impl Greetable for Robot {\n"
            "    fn greet(&self) -> String { String::from(\"beep\") }\n"
            "}\n"
        )
        _, edges, _ = _parse(code)
        assert any(e.kind == EdgeKind.IMPLEMENTS for e in edges)


# ── Imports ───────────────────────────────────────────────────────────────────

class TestImports:
    def test_use_declaration_extracted(self):
        _, _, imports = _parse("use std::collections::HashMap;\n")
        assert len(imports) >= 1

    def test_multiple_use_declarations(self):
        code = "use std::io;\nuse std::fs;\nuse std::path::Path;\n"
        _, _, imports = _parse(code)
        assert len(imports) >= 3


# ── Call edges ────────────────────────────────────────────────────────────────

class TestCallEdges:
    def test_function_calls_tracked(self):
        code = (
            "fn helper() -> i32 { 42 }\n\n"
            "fn main() {\n    helper();\n}\n"
        )
        _, edges, _ = _parse(code)
        calls = [e for e in edges if e.kind == EdgeKind.CALLS]
        assert len(calls) >= 1
