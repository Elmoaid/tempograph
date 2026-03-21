"""Tests for Lua language handler (LuaHandlerMixin)."""
from __future__ import annotations

import pytest

from tempograph.parser import FileParser
from tempograph.types import Language, SymbolKind


def _parse(code: str, filename: str = "src/lib.lua"):
    p = FileParser(filename, Language.LUA, code.encode())
    return p.parse()


# ── Functions ─────────────────────────────────────────────────────────────────

class TestFunction:
    def test_global_function_extracted(self):
        syms, _, _ = _parse("function add(a, b)\n  return a + b\nend\n")
        assert any(s.name == "add" for s in syms)

    def test_global_function_kind(self):
        syms, _, _ = _parse("function add(a, b)\n  return a + b\nend\n")
        fn = next(s for s in syms if s.name == "add")
        assert fn.kind == SymbolKind.FUNCTION

    def test_global_function_exported(self):
        syms, _, _ = _parse("function add(a, b)\n  return a + b\nend\n")
        fn = next(s for s in syms if s.name == "add")
        assert fn.exported is True

    def test_local_function_extracted(self):
        syms, _, _ = _parse("local function helper(x)\n  return x * 2\nend\n")
        assert any(s.name == "helper" for s in syms)

    def test_local_function_not_exported(self):
        syms, _, _ = _parse("local function helper(x)\n  return x * 2\nend\n")
        fn = next(s for s in syms if s.name == "helper")
        assert fn.exported is False

    def test_multiple_functions(self):
        syms, _, _ = _parse(
            "function add(a, b) return a + b end\n"
            "function sub(a, b) return a - b end\n"
        )
        names = {s.name for s in syms}
        assert {"add", "sub"}.issubset(names)

    def test_function_line_number(self):
        syms, _, _ = _parse("\nfunction greet(name)\n  return name\nend\n")
        fn = next(s for s in syms if s.name == "greet")
        assert fn.line_start == 2


# ── Module-namespaced functions ───────────────────────────────────────────────

class TestModuleFunction:
    def test_dotted_function_extracted(self):
        syms, _, _ = _parse(
            "local M = {}\n"
            "function M.greet(name)\n  return name\nend\n"
        )
        assert any(s.name == "greet" for s in syms)

    def test_dotted_function_qualified_name(self):
        syms, _, _ = _parse(
            "local M = {}\n"
            "function M.greet(name)\n  return name\nend\n"
        )
        fn = next(s for s in syms if s.name == "greet")
        assert fn.qualified_name == "M.greet"

    def test_dotted_function_kind(self):
        syms, _, _ = _parse(
            "local M = {}\n"
            "function M.greet(name)\n  return name\nend\n"
        )
        fn = next(s for s in syms if s.name == "greet")
        assert fn.kind == SymbolKind.FUNCTION


# ── Methods (colon syntax) ────────────────────────────────────────────────────

class TestMethod:
    def test_method_extracted(self):
        syms, _, _ = _parse(
            "local Animal = {}\n"
            "function Animal:speak()\n  print(self.name)\nend\n"
        )
        assert any(s.name == "speak" for s in syms)

    def test_method_kind(self):
        syms, _, _ = _parse(
            "local Animal = {}\n"
            "function Animal:speak()\n  print(self.name)\nend\n"
        )
        fn = next(s for s in syms if s.name == "speak")
        assert fn.kind == SymbolKind.METHOD

    def test_method_qualified_name(self):
        syms, _, _ = _parse(
            "local Animal = {}\n"
            "function Animal:speak()\n  print(self.name)\nend\n"
        )
        fn = next(s for s in syms if s.name == "speak")
        assert fn.qualified_name == "Animal:speak"


# ── Imports (require) ─────────────────────────────────────────────────────────

class TestImports:
    def test_require_extracted(self):
        _, _, imports = _parse("local utils = require('utils')\n")
        assert any("utils" in i for i in imports)

    def test_require_without_parens(self):
        _, _, imports = _parse("local M = require 'mymodule'\n")
        assert any("mymodule" in i for i in imports)

    def test_multiple_requires(self):
        _, _, imports = _parse(
            "local a = require('mod_a')\n"
            "local b = require('mod_b')\n"
            "local c = require('mod_c')\n"
        )
        assert len(imports) == 3

    def test_require_does_not_create_symbol(self):
        syms, _, _ = _parse("local utils = require('utils')\n")
        assert not any(s.name == "utils" and s.kind != SymbolKind.FUNCTION for s in syms)
        assert not any("utils" == s.name for s in syms if s.kind == SymbolKind.FUNCTION)
