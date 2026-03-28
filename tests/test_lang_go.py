"""Tests for Go language handler (GoHandlerMixin)."""
from __future__ import annotations

import pytest

from tempograph.parser import FileParser
from tempograph.types import EdgeKind, Language, SymbolKind


def _parse(code: str, filename: str = "pkg/service.go"):
    p = FileParser(filename, Language.GO, code.encode())
    return p.parse()


# ── Functions ─────────────────────────────────────────────────────────────────

class TestFunction:
    def test_top_level_function_extracted(self):
        syms, _, _ = _parse("package main\n\nfunc Greet(name string) string {\n\treturn name\n}\n")
        assert any(s.name == "Greet" for s in syms)

    def test_function_kind(self):
        syms, _, _ = _parse("package main\n\nfunc Compute(x int) int {\n\treturn x\n}\n")
        fn = next(s for s in syms if s.name == "Compute")
        assert fn.kind == SymbolKind.FUNCTION

    def test_exported_uppercase(self):
        syms, _, _ = _parse("package main\n\nfunc PublicFunc() {}\n")
        fn = next(s for s in syms if s.name == "PublicFunc")
        assert fn.exported is True

    def test_unexported_lowercase(self):
        syms, _, _ = _parse("package main\n\nfunc internalHelper() {}\n")
        fn = next(s for s in syms if s.name == "internalHelper")
        assert fn.exported is False

    def test_function_line_number(self):
        code = "package main\n\nfunc Process(data string) {}\n"
        syms, _, _ = _parse(code)
        fn = next(s for s in syms if s.name == "Process")
        assert fn.line_start == 3

    def test_multiple_functions(self):
        code = "package main\n\nfunc Foo() {}\nfunc Bar() {}\nfunc Baz() {}\n"
        syms, _, _ = _parse(code)
        names = {s.name for s in syms}
        assert {"Foo", "Bar", "Baz"}.issubset(names)


# ── Methods ───────────────────────────────────────────────────────────────────

class TestMethod:
    def test_method_extracted(self):
        code = (
            "package main\n\n"
            "type Dog struct{}\n\n"
            "func (d *Dog) Bark() string {\n\treturn \"woof\"\n}\n"
        )
        syms, _, _ = _parse(code)
        assert any(s.name == "Bark" for s in syms)

    def test_method_kind(self):
        code = (
            "package main\n\n"
            "type Cat struct{}\n\n"
            "func (c Cat) Meow() string {\n\treturn \"meow\"\n}\n"
        )
        syms, _, _ = _parse(code)
        m = next(s for s in syms if s.name == "Meow")
        assert m.kind == SymbolKind.METHOD

    def test_method_exported(self):
        code = (
            "package main\n\n"
            "type Svc struct{}\n\n"
            "func (s *Svc) Run() {}\n"
        )
        syms, _, _ = _parse(code)
        m = next(s for s in syms if s.name == "Run")
        assert m.exported is True

    def test_method_unexported(self):
        code = (
            "package main\n\n"
            "type Svc struct{}\n\n"
            "func (s *Svc) cleanup() {}\n"
        )
        syms, _, _ = _parse(code)
        m = next(s for s in syms if s.name == "cleanup")
        assert m.exported is False

    def test_method_parent_id_set(self):
        code = (
            "package main\n\n"
            "type Server struct{}\n\n"
            "func (s *Server) Listen() {}\n"
        )
        syms, edges, _ = _parse(code)
        server = next(s for s in syms if s.name == "Server")
        listen = next(s for s in syms if s.name == "Listen")
        assert listen.parent_id == server.id

    def test_contains_edge_for_method(self):
        code = (
            "package main\n\n"
            "type Node struct{}\n\n"
            "func (n *Node) Value() int { return 0 }\n"
        )
        _, edges, _ = _parse(code)
        assert any(e.kind == EdgeKind.CONTAINS for e in edges)


# ── Structs and Interfaces ─────────────────────────────────────────────────────

class TestTypes:
    def test_struct_extracted(self):
        syms, _, _ = _parse("package main\n\ntype User struct {\n\tName string\n}\n")
        assert any(s.name == "User" for s in syms)

    def test_struct_kind(self):
        syms, _, _ = _parse("package main\n\ntype User struct {\n\tName string\n}\n")
        s = next(sym for sym in syms if sym.name == "User")
        assert s.kind == SymbolKind.STRUCT

    def test_struct_exported_uppercase(self):
        syms, _, _ = _parse("package main\n\ntype Response struct{}\n")
        s = next(sym for sym in syms if sym.name == "Response")
        assert s.exported is True

    def test_struct_unexported_lowercase(self):
        syms, _, _ = _parse("package main\n\ntype internalState struct{}\n")
        s = next(sym for sym in syms if sym.name == "internalState")
        assert s.exported is False

    def test_interface_extracted(self):
        syms, _, _ = _parse("package main\n\ntype Reader interface {\n\tRead() string\n}\n")
        assert any(s.name == "Reader" for s in syms)

    def test_interface_kind(self):
        syms, _, _ = _parse("package main\n\ntype Writer interface {\n\tWrite(s string)\n}\n")
        iface = next(s for s in syms if s.name == "Writer")
        assert iface.kind == SymbolKind.INTERFACE

    def test_struct_embedding_produces_inherits_edge(self):
        code = (
            "package main\n\n"
            "type Base struct{}\n\n"
            "type Child struct {\n\tBase\n}\n"
        )
        _, edges, _ = _parse(code)
        assert any(e.kind == EdgeKind.INHERITS for e in edges)

    def test_struct_embedding_target(self):
        code = (
            "package main\n\n"
            "type Base struct{}\n\n"
            "type Child struct {\n\tBase\n}\n"
        )
        syms, edges, _ = _parse(code)
        child = next(s for s in syms if s.name == "Child")
        inh = next(e for e in edges if e.kind == EdgeKind.INHERITS and e.source_id == child.id)
        assert "Base" in inh.target_id


# ── Imports ───────────────────────────────────────────────────────────────────

class TestImports:
    def test_import_extracted(self):
        code = "package main\n\nimport \"fmt\"\n\nfunc main() {}\n"
        _, _, imports = _parse(code)
        assert len(imports) >= 1

    def test_multiple_imports_extracted(self):
        code = (
            "package main\n\n"
            "import (\n"
            "\t\"fmt\"\n"
            "\t\"os\"\n"
            "\t\"strings\"\n"
            ")\n"
        )
        _, _, imports = _parse(code)
        assert len(imports) >= 1  # import block is captured as one declaration


# ── Call edges ────────────────────────────────────────────────────────────────

class TestCallEdges:
    def test_function_calls_tracked(self):
        code = (
            "package main\n\n"
            "func helper() int { return 42 }\n\n"
            "func main() {\n\thelper()\n}\n"
        )
        _, edges, _ = _parse(code)
        calls = [e for e in edges if e.kind == EdgeKind.CALLS]
        assert len(calls) >= 1


# ── Constants ──────────────────────────────────────────────────────────────────

class TestConst:
    def test_single_const_extracted(self):
        syms, _, _ = _parse("package main\n\nconst MaxSize = 100\n")
        assert any(s.name == "MaxSize" for s in syms)

    def test_const_kind(self):
        syms, _, _ = _parse("package main\n\nconst MaxSize = 100\n")
        c = next(s for s in syms if s.name == "MaxSize")
        assert c.kind == SymbolKind.CONSTANT

    def test_const_exported_uppercase(self):
        syms, _, _ = _parse("package main\n\nconst MaxRetries = 3\n")
        c = next(s for s in syms if s.name == "MaxRetries")
        assert c.exported is True

    def test_const_unexported_lowercase(self):
        syms, _, _ = _parse("package main\n\nconst defaultTimeout = 30\n")
        c = next(s for s in syms if s.name == "defaultTimeout")
        assert c.exported is False

    def test_const_block_all_extracted(self):
        code = (
            "package main\n\n"
            "const (\n"
            "\tStatusOK = 0\n"
            "\tStatusFail = 1\n"
            "\tStatusError = 2\n"
            ")\n"
        )
        syms, _, _ = _parse(code)
        names = {s.name for s in syms if s.kind == SymbolKind.CONSTANT}
        assert {"StatusOK", "StatusFail", "StatusError"} == names

    def test_const_iota_block_extracted(self):
        code = (
            "package main\n\n"
            "const (\n"
            "\tTypeA = iota\n"
            "\tTypeB\n"
            "\tTypeC\n"
            ")\n"
        )
        syms, _, _ = _parse(code)
        names = {s.name for s in syms if s.kind == SymbolKind.CONSTANT}
        assert {"TypeA", "TypeB", "TypeC"} == names

    def test_const_multi_name_extracted(self):
        syms, _, _ = _parse("package main\n\nconst a, b = 1, 2\n")
        names = {s.name for s in syms if s.kind == SymbolKind.CONSTANT}
        assert {"a", "b"} == names


# ── Variables ──────────────────────────────────────────────────────────────────

class TestVar:
    def test_single_var_extracted(self):
        syms, _, _ = _parse("package main\n\nvar globalCount int = 0\n")
        assert any(s.name == "globalCount" for s in syms)

    def test_var_kind(self):
        syms, _, _ = _parse("package main\n\nvar globalCount int = 0\n")
        v = next(s for s in syms if s.name == "globalCount")
        assert v.kind == SymbolKind.VARIABLE

    def test_var_exported_uppercase(self):
        syms, _, _ = _parse("package main\n\nvar DefaultLogger = nil\n")
        v = next(s for s in syms if s.name == "DefaultLogger")
        assert v.exported is True

    def test_var_unexported_lowercase(self):
        syms, _, _ = _parse("package main\n\nvar cache map[string]int\n")
        v = next(s for s in syms if s.name == "cache")
        assert v.exported is False

    def test_var_block_all_extracted(self):
        code = (
            "package main\n\n"
            "var (\n"
            "\tdebug bool\n"
            "\tprefix string\n"
            "\tcounter int\n"
            ")\n"
        )
        syms, _, _ = _parse(code)
        names = {s.name for s in syms if s.kind == SymbolKind.VARIABLE}
        assert {"debug", "prefix", "counter"} == names

    def test_var_multi_name_extracted(self):
        syms, _, _ = _parse("package main\n\nvar x, y int\n")
        names = {s.name for s in syms if s.kind == SymbolKind.VARIABLE}
        assert {"x", "y"} == names
