"""Tests for Erlang language handler (ErlangHandlerMixin)."""
from __future__ import annotations

import pytest

from tempograph.parser import FileParser
from tempograph.types import Language, SymbolKind


def _parse(code: str, filename: str = "src/math_utils.erl"):
    p = FileParser(filename, Language.ERLANG, code.encode())
    return p.parse()


# ── Functions ─────────────────────────────────────────────────────────────────

class TestFunction:
    def test_function_extracted(self):
        syms, _, _ = _parse(
            "-module(math).\n\nadd(A, B) -> A + B.\n"
        )
        assert any(s.name == "add" for s in syms)

    def test_function_kind(self):
        syms, _, _ = _parse(
            "-module(math).\n\nadd(A, B) -> A + B.\n"
        )
        fn = next(s for s in syms if s.name == "add")
        assert fn.kind == SymbolKind.FUNCTION

    def test_exported_function(self):
        syms, _, _ = _parse(
            "-module(math).\n"
            "-export([add/2]).\n\n"
            "add(A, B) -> A + B.\n"
        )
        fn = next(s for s in syms if s.name == "add")
        assert fn.exported is True

    def test_unexported_function(self):
        syms, _, _ = _parse(
            "-module(math).\n"
            "-export([add/2]).\n\n"
            "add(A, B) -> A + B.\n"
            "helper(X) -> X * 2.\n"
        )
        fn = next(s for s in syms if s.name == "helper")
        assert fn.exported is False

    def test_multiple_functions(self):
        syms, _, _ = _parse(
            "-module(math).\n\n"
            "add(A, B) -> A + B.\n"
            "sub(A, B) -> A - B.\n"
            "mul(A, B) -> A * B.\n"
        )
        names = {s.name for s in syms}
        assert {"add", "sub", "mul"}.issubset(names)

    def test_multi_clause_function_deduped(self):
        """Multi-clause functions (factorial(0) / factorial(N)) emit one symbol."""
        syms, _, _ = _parse(
            "-module(math).\n\n"
            "factorial(0) -> 1;\n"
            "factorial(N) when N > 0 -> N * factorial(N-1).\n"
        )
        assert len([s for s in syms if s.name == "factorial"]) == 1

    def test_function_line_number(self):
        syms, _, _ = _parse(
            "-module(math).\n\n"
            "add(A, B) -> A + B.\n"
        )
        fn = next(s for s in syms if s.name == "add")
        assert fn.line_start == 3


# ── Records ───────────────────────────────────────────────────────────────────

class TestRecord:
    def test_record_extracted(self):
        syms, _, _ = _parse(
            "-module(shapes).\n\n"
            "-record(point, {x, y}).\n"
        )
        assert any(s.name == "point" for s in syms)

    def test_record_kind(self):
        syms, _, _ = _parse(
            "-module(shapes).\n\n"
            "-record(point, {x, y}).\n"
        )
        rec = next(s for s in syms if s.name == "point")
        assert rec.kind == SymbolKind.TYPE_ALIAS

    def test_multiple_records(self):
        syms, _, _ = _parse(
            "-module(shapes).\n\n"
            "-record(point, {x, y}).\n"
            "-record(rect, {top_left, bottom_right}).\n"
        )
        names = {s.name for s in syms}
        assert {"point", "rect"}.issubset(names)


# ── Imports ───────────────────────────────────────────────────────────────────

class TestImports:
    def test_import_extracted(self):
        _, _, imports = _parse(
            "-module(main).\n\n"
            "-import(lists, [map/2, filter/2]).\n"
        )
        assert any("lists" in i for i in imports)

    def test_multiple_imports(self):
        _, _, imports = _parse(
            "-module(main).\n\n"
            "-import(lists, [map/2]).\n"
            "-import(string, [join/2]).\n"
        )
        assert len(imports) == 2

    def test_import_does_not_create_symbol(self):
        syms, _, _ = _parse(
            "-module(main).\n\n"
            "-import(lists, [map/2]).\n"
        )
        assert not any(s.name == "lists" for s in syms)
