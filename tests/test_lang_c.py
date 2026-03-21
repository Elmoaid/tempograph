"""Tests for C language handler (CHandlerMixin)."""
from __future__ import annotations

import pytest

from tempograph.parser import FileParser
from tempograph.types import EdgeKind, Language, SymbolKind


def _parse(code: str, filename: str = "src/service.c"):
    p = FileParser(filename, Language.C, code.encode())
    return p.parse()


# ── Functions ─────────────────────────────────────────────────────────────────

class TestFunction:
    def test_function_extracted(self):
        syms, _, _ = _parse("int greet(char *name) {\n    return 0;\n}\n")
        assert any(s.name == "greet" for s in syms)

    def test_function_kind(self):
        syms, _, _ = _parse("int compute(int x) {\n    return x * 2;\n}\n")
        fn = next(s for s in syms if s.name == "compute")
        assert fn.kind == SymbolKind.FUNCTION

    def test_non_static_function_exported(self):
        """Non-static C functions have external linkage = exported."""
        syms, _, _ = _parse("int process(void) { return 0; }\n")
        fn = next(s for s in syms if s.name == "process")
        assert fn.exported is True

    def test_static_function_not_exported(self):
        """Static functions have file-local linkage = not exported."""
        syms, _, _ = _parse("static int helper(void) { return 0; }\n")
        fn = next(s for s in syms if s.name == "helper")
        assert fn.exported is False

    def test_function_line_number(self):
        syms, _, _ = _parse("\nint run(void) { return 0; }\n")
        fn = next(s for s in syms if s.name == "run")
        assert fn.line_start == 2

    def test_multiple_functions(self):
        code = "void foo(void) {}\nvoid bar(void) {}\nvoid baz(void) {}\n"
        syms, _, _ = _parse(code)
        names = {s.name for s in syms}
        assert {"foo", "bar", "baz"}.issubset(names)

    def test_void_function(self):
        syms, _, _ = _parse("void init(void) {}\n")
        assert any(s.name == "init" for s in syms)


# ── Structs ───────────────────────────────────────────────────────────────────

class TestStruct:
    def test_struct_extracted(self):
        syms, _, _ = _parse("struct Point {\n    int x;\n    int y;\n};\n")
        assert any(s.name == "Point" for s in syms)

    def test_struct_kind(self):
        syms, _, _ = _parse("struct Config {\n    int debug;\n};\n")
        s = next(sym for sym in syms if sym.name == "Config")
        assert s.kind == SymbolKind.STRUCT

    def test_struct_exported(self):
        syms, _, _ = _parse("struct Node {\n    int value;\n};\n")
        s = next(sym for sym in syms if sym.name == "Node")
        assert s.exported is True


# ── Enums ─────────────────────────────────────────────────────────────────────

class TestEnum:
    def test_enum_extracted(self):
        syms, _, _ = _parse("enum Color { RED, GREEN, BLUE };\n")
        assert any(s.name == "Color" for s in syms)

    def test_enum_kind(self):
        syms, _, _ = _parse("enum Status { ACTIVE, INACTIVE };\n")
        e = next(s for s in syms if s.name == "Status")
        assert e.kind == SymbolKind.ENUM


# ── Call edges ────────────────────────────────────────────────────────────────

class TestCallEdges:
    def test_function_calls_tracked(self):
        code = (
            "int helper(void) { return 42; }\n"
            "int main(void) {\n    helper();\n    return 0;\n}\n"
        )
        _, edges, _ = _parse(code)
        calls = [e for e in edges if e.kind == EdgeKind.CALLS]
        assert len(calls) >= 1
