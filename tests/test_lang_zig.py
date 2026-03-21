"""Tests for Zig language handler (ZigHandlerMixin)."""
from __future__ import annotations

import pytest

from tempograph.parser import FileParser
from tempograph.types import EdgeKind, Language, SymbolKind


def _parse(code: str, filename: str = "src/main.zig"):
    p = FileParser(filename, Language.ZIG, code.encode())
    return p.parse()


# ── Functions ─────────────────────────────────────────────────────────────────

class TestFunction:
    def test_top_level_function_extracted(self):
        syms, _, _ = _parse("fn greet(name: []const u8) void {}\n")
        assert any(s.name == "greet" for s in syms)

    def test_function_kind(self):
        syms, _, _ = _parse("fn compute(x: i32) i32 {\n    return x * 2;\n}\n")
        fn = next(s for s in syms if s.name == "compute")
        assert fn.kind == SymbolKind.FUNCTION

    def test_pub_function_exported(self):
        syms, _, _ = _parse("pub fn greet() void {}\n")
        fn = next(s for s in syms if s.name == "greet")
        assert fn.exported is True

    def test_private_function_not_exported(self):
        syms, _, _ = _parse("fn helper() void {}\n")
        fn = next(s for s in syms if s.name == "helper")
        assert fn.exported is False

    def test_multiple_functions(self):
        code = "pub fn foo() void {}\npub fn bar() void {}\npub fn baz() void {}\n"
        syms, _, _ = _parse(code)
        names = {s.name for s in syms}
        assert {"foo", "bar", "baz"}.issubset(names)


# ── Structs ───────────────────────────────────────────────────────────────────

class TestStruct:
    def test_struct_extracted(self):
        syms, _, _ = _parse("const Point = struct {\n    x: f64,\n    y: f64,\n};\n")
        assert any(s.name == "Point" for s in syms)

    def test_struct_kind(self):
        syms, _, _ = _parse("const Config = struct {\n    debug: bool,\n};\n")
        s = next(sym for sym in syms if sym.name == "Config")
        assert s.kind == SymbolKind.STRUCT

    def test_pub_struct_exported(self):
        syms, _, _ = _parse("pub const User = struct {\n    name: []const u8,\n};\n")
        s = next(sym for sym in syms if sym.name == "User")
        assert s.exported is True

    def test_struct_method_extracted(self):
        code = (
            "pub const Dog = struct {\n"
            "    pub fn bark(self: Dog) void {}\n"
            "};\n"
        )
        syms, _, _ = _parse(code)
        assert any(s.name == "bark" for s in syms)

    def test_struct_method_kind(self):
        code = (
            "pub const Cat = struct {\n"
            "    pub fn meow(self: Cat) void {}\n"
            "};\n"
        )
        syms, _, _ = _parse(code)
        m = next(s for s in syms if s.name == "meow")
        assert m.kind == SymbolKind.METHOD

    def test_struct_method_parent_id(self):
        code = (
            "pub const Bird = struct {\n"
            "    pub fn sing(self: Bird) void {}\n"
            "};\n"
        )
        syms, edges, _ = _parse(code)
        bird = next(s for s in syms if s.name == "Bird")
        sing = next(s for s in syms if s.name == "sing")
        assert sing.parent_id == bird.id

    def test_contains_edge_for_method(self):
        code = (
            "pub const Tree = struct {\n"
            "    pub fn grow(self: Tree) void {}\n"
            "};\n"
        )
        _, edges, _ = _parse(code)
        assert any(e.kind == EdgeKind.CONTAINS for e in edges)


# ── Enums ─────────────────────────────────────────────────────────────────────

class TestEnum:
    def test_enum_extracted(self):
        syms, _, _ = _parse("const Color = enum {\n    red,\n    green,\n    blue,\n};\n")
        assert any(s.name == "Color" for s in syms)

    def test_enum_kind(self):
        syms, _, _ = _parse("const Direction = enum {\n    north,\n    south,\n};\n")
        e = next(s for s in syms if s.name == "Direction")
        assert e.kind == SymbolKind.ENUM
