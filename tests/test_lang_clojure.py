"""Tests for Clojure language handler (ClojureHandlerMixin)."""
from __future__ import annotations

import pytest

from tempograph.parser import FileParser
from tempograph.types import Language, SymbolKind


def _parse(code: str, filename: str = "src/core.clj"):
    p = FileParser(filename, Language.CLOJURE, code.encode())
    return p.parse()


# -- Namespace ----------------------------------------------------------------

class TestNamespace:
    def test_ns_extracted(self):
        syms, _, _ = _parse("(ns myapp.core)")
        assert any(s.name == "myapp.core" for s in syms)

    def test_ns_kind_is_module(self):
        syms, _, _ = _parse("(ns myapp.core)")
        ns = next(s for s in syms if s.name == "myapp.core")
        assert ns.kind == SymbolKind.MODULE

    def test_ns_exported(self):
        syms, _, _ = _parse("(ns myapp.core)")
        ns = next(s for s in syms if s.name == "myapp.core")
        assert ns.exported is True

    def test_ns_line_number(self):
        syms, _, _ = _parse("\n(ns myapp.core)")
        ns = next(s for s in syms if s.name == "myapp.core")
        assert ns.line_start == 2


# -- Functions (defn) --------------------------------------------------------

class TestDefn:
    def test_defn_extracted(self):
        syms, _, _ = _parse("(defn add [x y] (+ x y))")
        assert any(s.name == "add" for s in syms)

    def test_defn_kind(self):
        syms, _, _ = _parse("(defn add [x y] (+ x y))")
        fn = next(s for s in syms if s.name == "add")
        assert fn.kind == SymbolKind.FUNCTION

    def test_defn_exported(self):
        syms, _, _ = _parse("(defn add [x y] (+ x y))")
        fn = next(s for s in syms if s.name == "add")
        assert fn.exported is True

    def test_defn_line_number(self):
        syms, _, _ = _parse("\n\n(defn add [x y] (+ x y))")
        fn = next(s for s in syms if s.name == "add")
        assert fn.line_start == 3

    def test_multiple_defns(self):
        syms, _, _ = _parse("""
(defn add [x y] (+ x y))
(defn sub [x y] (- x y))
(defn mul [x y] (* x y))
""")
        names = {s.name for s in syms}
        assert {"add", "sub", "mul"}.issubset(names)


# -- Private functions (defn-) -----------------------------------------------

class TestDefnPrivate:
    def test_defn_private_extracted(self):
        syms, _, _ = _parse("(defn- helper [x] (* x 2))")
        assert any(s.name == "helper" for s in syms)

    def test_defn_private_not_exported(self):
        syms, _, _ = _parse("(defn- helper [x] (* x 2))")
        fn = next(s for s in syms if s.name == "helper")
        assert fn.exported is False

    def test_defn_private_kind(self):
        syms, _, _ = _parse("(defn- helper [x] (* x 2))")
        fn = next(s for s in syms if s.name == "helper")
        assert fn.kind == SymbolKind.FUNCTION


# -- Underscore-prefix name --------------------------------------------------

class TestUnderscorePrefix:
    def test_underscore_prefix_not_exported(self):
        syms, _, _ = _parse("(defn _internal [x] x)")
        fn = next(s for s in syms if s.name == "_internal")
        assert fn.exported is False


# -- Macros (defmacro) -------------------------------------------------------

class TestDefmacro:
    def test_defmacro_extracted(self):
        syms, _, _ = _parse("(defmacro when-let [binding & body] nil)")
        assert any(s.name == "when-let" for s in syms)

    def test_defmacro_kind(self):
        syms, _, _ = _parse("(defmacro when-let [binding & body] nil)")
        m = next(s for s in syms if s.name == "when-let")
        assert m.kind == SymbolKind.FUNCTION


# -- Vars (def) --------------------------------------------------------------

class TestDef:
    def test_def_extracted(self):
        syms, _, _ = _parse("(def my-var 42)")
        assert any(s.name == "my-var" for s in syms)

    def test_def_kind(self):
        syms, _, _ = _parse("(def my-var 42)")
        v = next(s for s in syms if s.name == "my-var")
        assert v.kind == SymbolKind.FUNCTION

    def test_def_exported(self):
        syms, _, _ = _parse("(def my-var 42)")
        v = next(s for s in syms if s.name == "my-var")
        assert v.exported is True


# -- Protocols (defprotocol) -------------------------------------------------

class TestDefprotocol:
    def test_defprotocol_extracted(self):
        syms, _, _ = _parse("(defprotocol MyProtocol (my-method [this]))")
        assert any(s.name == "MyProtocol" for s in syms)

    def test_defprotocol_kind(self):
        syms, _, _ = _parse("(defprotocol MyProtocol (my-method [this]))")
        p = next(s for s in syms if s.name == "MyProtocol")
        assert p.kind == SymbolKind.INTERFACE


# -- Records (defrecord) / Types (deftype) -----------------------------------

class TestDefrecord:
    def test_defrecord_extracted(self):
        syms, _, _ = _parse("(defrecord MyRecord [field1 field2])")
        assert any(s.name == "MyRecord" for s in syms)

    def test_defrecord_kind(self):
        syms, _, _ = _parse("(defrecord MyRecord [field1 field2])")
        r = next(s for s in syms if s.name == "MyRecord")
        assert r.kind == SymbolKind.CLASS

    def test_deftype_extracted(self):
        syms, _, _ = _parse("(deftype MyType [x y])")
        assert any(s.name == "MyType" for s in syms)

    def test_deftype_kind(self):
        syms, _, _ = _parse("(deftype MyType [x y])")
        t = next(s for s in syms if s.name == "MyType")
        assert t.kind == SymbolKind.CLASS


# -- Imports ------------------------------------------------------------------

class TestImports:
    def test_require_import(self):
        _, _, imports = _parse("""
(ns myapp.core
  (:require [clojure.string :as str]))
""")
        assert "clojure.string" in imports

    def test_multiple_require_imports(self):
        _, _, imports = _parse("""
(ns myapp.core
  (:require [clojure.string :as str]
            [clojure.set]))
""")
        assert "clojure.string" in imports
        assert "clojure.set" in imports

    def test_use_import(self):
        _, _, imports = _parse("""
(ns myapp.core
  (:use [clojure.pprint]))
""")
        assert "clojure.pprint" in imports

    def test_java_import(self):
        _, _, imports = _parse("""
(ns myapp.core
  (:import [java.util Date]))
""")
        assert "java.util" in imports

    def test_import_does_not_create_symbol(self):
        syms, _, imports = _parse("""
(ns myapp.core
  (:require [clojure.string :as str]))
""")
        assert not any(s.name == "clojure.string" for s in syms)
        assert "clojure.string" in imports


# -- Edge cases ---------------------------------------------------------------

class TestEdgeCases:
    def test_empty_file(self):
        syms, edges, imports = _parse("")
        assert syms == []
        assert edges == []
        assert imports == []

    def test_comment_only_file(self):
        syms, _, _ = _parse(";; just a comment\n;; another comment")
        assert syms == []

    def test_full_file(self):
        syms, _, imports = _parse("""
(ns myapp.core
  (:require [clojure.string :as str]))

(defn greet [name]
  (str "Hello, " name))

(defn- internal-helper [x]
  (* x 2))

(def version "1.0.0")

(defprotocol Greeter
  (say-hello [this]))

(defrecord Person [name age])
""")
        names = {s.name for s in syms}
        assert "myapp.core" in names
        assert "greet" in names
        assert "internal-helper" in names
        assert "version" in names
        assert "Greeter" in names
        assert "Person" in names
        assert "clojure.string" in imports
        # Check kinds
        ns = next(s for s in syms if s.name == "myapp.core")
        assert ns.kind == SymbolKind.MODULE
        greet = next(s for s in syms if s.name == "greet")
        assert greet.kind == SymbolKind.FUNCTION
        assert greet.exported is True
        helper = next(s for s in syms if s.name == "internal-helper")
        assert helper.exported is False
        greeter = next(s for s in syms if s.name == "Greeter")
        assert greeter.kind == SymbolKind.INTERFACE
        person = next(s for s in syms if s.name == "Person")
        assert person.kind == SymbolKind.CLASS
