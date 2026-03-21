"""Tests for R language handler (RHandlerMixin)."""
from __future__ import annotations

import pytest

from tempograph.parser import FileParser
from tempograph.types import Language, SymbolKind


def _parse(code: str, filename: str = "src/analysis.r"):
    p = FileParser(filename, Language.R, code.encode())
    return p.parse()


# ── Functions ─────────────────────────────────────────────────────────────────

class TestFunction:
    def test_arrow_assignment_extracted(self):
        syms, _, _ = _parse("add <- function(x, y) { x + y }\n")
        assert any(s.name == "add" for s in syms)

    def test_function_kind(self):
        syms, _, _ = _parse("add <- function(x, y) { x + y }\n")
        fn = next(s for s in syms if s.name == "add")
        assert fn.kind == SymbolKind.FUNCTION

    def test_function_exported(self):
        syms, _, _ = _parse("add <- function(x, y) { x + y }\n")
        fn = next(s for s in syms if s.name == "add")
        assert fn.exported is True

    def test_equals_assignment_extracted(self):
        syms, _, _ = _parse("greet = function(name) { paste('Hello', name) }\n")
        assert any(s.name == "greet" for s in syms)

    def test_multiple_functions(self):
        syms, _, _ = _parse(
            "add <- function(x, y) x + y\n"
            "sub <- function(x, y) x - y\n"
            "mul <- function(x, y) x * y\n"
        )
        names = {s.name for s in syms}
        assert {"add", "sub", "mul"}.issubset(names)

    def test_function_line_number(self):
        syms, _, _ = _parse("\nadd <- function(x, y) {\n  x + y\n}\n")
        fn = next(s for s in syms if s.name == "add")
        assert fn.line_start == 2


# ── Imports ───────────────────────────────────────────────────────────────────

class TestImports:
    def test_library_extracted(self):
        _, _, imports = _parse("library(ggplot2)\n")
        assert any("ggplot2" in i for i in imports)

    def test_require_extracted(self):
        _, _, imports = _parse("require(dplyr)\n")
        assert any("dplyr" in i for i in imports)

    def test_source_extracted(self):
        _, _, imports = _parse('source("utils.R")\n')
        assert any("utils.R" in i for i in imports)

    def test_multiple_imports(self):
        _, _, imports = _parse(
            "library(ggplot2)\n"
            "require(dplyr)\n"
            "library(tidyr)\n"
        )
        assert len(imports) == 3

    def test_import_does_not_create_symbol(self):
        syms, _, _ = _parse("library(ggplot2)\n")
        assert not any(s.name == "ggplot2" for s in syms)

    def test_non_import_call_ignored(self):
        _, _, imports = _parse("print('hello')\n")
        assert len(imports) == 0
