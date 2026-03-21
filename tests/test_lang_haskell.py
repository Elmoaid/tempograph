"""Tests for Haskell language handler (HaskellHandlerMixin)."""
from __future__ import annotations

import pytest

from tempograph.parser import FileParser
from tempograph.types import Language, SymbolKind


def _parse(code: str, filename: str = "src/Lib.hs"):
    p = FileParser(filename, Language.HASKELL, code.encode())
    return p.parse()


# ── Functions ─────────────────────────────────────────────────────────────────

class TestFunction:
    def test_function_extracted(self):
        syms, _, _ = _parse("module Lib where\n\nadd x y = x + y")
        assert any(s.name == "add" for s in syms)

    def test_function_kind(self):
        syms, _, _ = _parse("module Lib where\n\nadd x y = x + y")
        fn = next(s for s in syms if s.name == "add")
        assert fn.kind == SymbolKind.FUNCTION

    def test_function_exported(self):
        syms, _, _ = _parse("module Lib where\n\nadd x y = x + y")
        fn = next(s for s in syms if s.name == "add")
        assert fn.exported is True

    def test_underscore_function_not_exported(self):
        syms, _, _ = _parse("module Lib where\n\n_helper x = x + 1")
        fn = next(s for s in syms if s.name == "_helper")
        assert fn.exported is False

    def test_multiple_functions(self):
        syms, _, _ = _parse("""
module Lib where

add x y = x + y
sub x y = x - y
mul x y = x * y
""")
        names = {s.name for s in syms}
        assert {"add", "sub", "mul"}.issubset(names)

    def test_multi_equation_function_deduped(self):
        """Multi-equation functions (e.g. factorial 0 = 1; factorial n = ...) emit one symbol."""
        syms, _, _ = _parse("""
module Lib where

factorial 0 = 1
factorial n = n * factorial (n - 1)
""")
        assert len([s for s in syms if s.name == "factorial"]) == 1

    def test_function_line_number(self):
        syms, _, _ = _parse("\nmodule Lib where\n\nadd x y = x + y\n")
        fn = next(s for s in syms if s.name == "add")
        assert fn.line_start == 4


# ── Data types ────────────────────────────────────────────────────────────────

class TestDataType:
    def test_data_type_extracted(self):
        syms, _, _ = _parse("module Lib where\n\ndata Color = Red | Green | Blue")
        assert any(s.name == "Color" for s in syms)

    def test_data_type_kind(self):
        syms, _, _ = _parse("module Lib where\n\ndata Color = Red | Green | Blue")
        t = next(s for s in syms if s.name == "Color")
        assert t.kind == SymbolKind.TYPE_ALIAS

    def test_data_type_exported(self):
        syms, _, _ = _parse("module Lib where\n\ndata Color = Red | Green | Blue")
        t = next(s for s in syms if s.name == "Color")
        assert t.exported is True

    def test_record_type_extracted(self):
        syms, _, _ = _parse("""
module Lib where

data Person = Person { name :: String, age :: Int }
""")
        assert any(s.name == "Person" for s in syms)


# ── Type synonyms ─────────────────────────────────────────────────────────────

class TestTypeSynonym:
    def test_type_synonym_extracted(self):
        syms, _, _ = _parse("module Lib where\n\ntype Name = String")
        assert any(s.name == "Name" for s in syms)

    def test_type_synonym_kind(self):
        syms, _, _ = _parse("module Lib where\n\ntype Name = String")
        t = next(s for s in syms if s.name == "Name")
        assert t.kind == SymbolKind.TYPE_ALIAS


# ── Type classes ──────────────────────────────────────────────────────────────

class TestTypeClass:
    def test_class_extracted(self):
        syms, _, _ = _parse("""
module Lib where

class Animal a where
  sound :: a -> String
""")
        assert any(s.name == "Animal" for s in syms)

    def test_class_kind(self):
        syms, _, _ = _parse("""
module Lib where

class Animal a where
  sound :: a -> String
""")
        c = next(s for s in syms if s.name == "Animal")
        assert c.kind == SymbolKind.INTERFACE


# ── Newtypes ──────────────────────────────────────────────────────────────────

class TestNewtype:
    def test_newtype_extracted(self):
        syms, _, _ = _parse("module Lib where\n\nnewtype Wrapper a = Wrapper { unwrap :: a }")
        assert any(s.name == "Wrapper" for s in syms)

    def test_newtype_kind(self):
        syms, _, _ = _parse("module Lib where\n\nnewtype Wrapper a = Wrapper { unwrap :: a }")
        t = next(s for s in syms if s.name == "Wrapper")
        assert t.kind == SymbolKind.CLASS


# ── Imports ───────────────────────────────────────────────────────────────────

class TestImports:
    def test_import_extracted(self):
        _, _, imports = _parse("module Lib where\n\nimport Data.List")
        assert any("Data.List" in i for i in imports)

    def test_multiple_imports(self):
        _, _, imports = _parse("""
module Lib where

import Data.List
import qualified Data.Map as Map
import System.IO
""")
        assert len(imports) == 3

    def test_import_does_not_create_symbol(self):
        syms, _, _ = _parse("module Lib where\n\nimport Data.List")
        assert not any("Data.List" == s.name for s in syms)
