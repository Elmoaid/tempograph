"""Tests for parser.py internals: _make_id, _compute_complexity, _node_text, and helpers."""
from __future__ import annotations

import pytest

from tempograph.parser import FileParser
from tempograph.types import Language, SymbolKind, EdgeKind
from tempograph.lang._utils import _node_text, _first_comment_above, _extract_signature


def _parser(code: str, filename: str = "test.py", lang: Language = Language.PYTHON) -> FileParser:
    return FileParser(filename, lang, code.encode())


# ── _make_id ─────────────────────────────────────────────────────────────────

class TestMakeId:
    def test_top_level_id_is_filepath_plus_name(self):
        p = _parser("def foo(): pass")
        assert p._make_id("foo") == "test.py::foo"

    def test_nested_id_includes_parent_qualified_name(self):
        p = _parser("class A:\n  def method(self): pass")
        p.parse()
        a_sym = next(s for s in p.symbols if s.name == "A")
        # Push the parent onto the stack and verify nested id
        p._symbol_stack = [a_sym.id]
        result = p._make_id("method")
        assert "A.method" in result
        assert p.file_path in result

    def test_empty_stack_gives_top_level_id(self):
        p = _parser("")
        p._symbol_stack = []
        assert p._make_id("standalone") == "test.py::standalone"

    def test_id_includes_file_path(self):
        p = _parser("def fn(): pass", filename="src/utils.py")
        assert p._make_id("fn") == "src/utils.py::fn"


# ── _node_text ────────────────────────────────────────────────────────────────

class TestNodeText:
    def _get_first_node(self, code: str, lang: Language = Language.PYTHON):
        """Parse code and return root node's first named child."""
        import tree_sitter_python as tspython
        from tree_sitter import Language as TSLanguage, Parser
        ts_lang = TSLanguage(tspython.language())
        parser = Parser(ts_lang)
        source = code.encode()
        tree = parser.parse(source)
        return tree.root_node.children[0], source

    def test_extracts_exact_text(self):
        node, source = self._get_first_node("x = 42\n")
        text = _node_text(node, source)
        assert text == "x = 42"

    def test_unicode_roundtrip(self):
        code = "x = '日本語'\n"
        node, source = self._get_first_node(code)
        text = _node_text(node, source)
        assert "日本語" in text

    def test_multiline_node(self):
        code = "def foo():\n    return 1\n"
        node, source = self._get_first_node(code)
        text = _node_text(node, source)
        assert "def foo" in text
        assert "return 1" in text


# ── _compute_complexity ───────────────────────────────────────────────────────

class TestComputeComplexity:
    def _complexity(self, code: str, lang: Language = Language.PYTHON) -> int:
        p = _parser(code, lang=lang)
        syms, _, _ = p.parse()
        fn = next((s for s in syms if s.kind == SymbolKind.FUNCTION), None)
        return fn.complexity if fn else 1

    def test_simple_function_complexity_one(self):
        code = "def trivial():\n    return 1\n"
        assert self._complexity(code) == 1

    def test_if_increases_complexity(self):
        code = "def check(x):\n    if x > 0:\n        return 1\n    return 0\n"
        assert self._complexity(code) >= 2

    def test_for_loop_increases_complexity(self):
        code = "def loop(xs):\n    for x in xs:\n        pass\n"
        assert self._complexity(code) >= 2

    def test_nested_branches_add_up(self):
        code = (
            "def complex(x, xs):\n"
            "    if x:\n"
            "        for i in xs:\n"
            "            if i > 0:\n"
            "                pass\n"
            "    return x\n"
        )
        assert self._complexity(code) >= 3

    def test_try_except_increases_complexity(self):
        code = "def safe():\n    try:\n        pass\n    except Exception:\n        pass\n"
        assert self._complexity(code) >= 2

    def test_typescript_complexity(self):
        code = "function check(x: number): boolean { if (x > 0) { return true; } return false; }\n"
        p = _parser(code, filename="test.ts", lang=Language.TYPESCRIPT)
        syms, _, _ = p.parse()
        fn = next((s for s in syms if s.name == "check"), None)
        assert fn is not None
        assert fn.complexity >= 2


# ── FileParser.parse() integration ───────────────────────────────────────────

class TestFileParserParse:
    def test_empty_file_returns_empty(self):
        syms, edges, imports = _parser("").parse()
        assert syms == []
        assert edges == []

    def test_returns_three_tuple(self):
        result = _parser("def foo(): pass").parse()
        assert len(result) == 3

    def test_unsupported_language_returns_empty(self):
        p = FileParser("test.xyz", Language.UNKNOWN, b"whatever")
        syms, edges, imports = p.parse()
        assert syms == []
        assert edges == []

    def test_symbol_ids_are_unique(self):
        code = "def foo(): pass\ndef bar(): pass\n"
        syms, _, _ = _parser(code).parse()
        ids = [s.id for s in syms]
        assert len(ids) == len(set(ids))

    def test_symbol_file_path_matches(self):
        code = "def fn(): pass\n"
        syms, _, _ = _parser(code, filename="pkg/utils.py").parse()
        assert all(s.file_path == "pkg/utils.py" for s in syms)

    def test_line_numbers_are_positive(self):
        code = "def fn(): pass\n"
        syms, _, _ = _parser(code).parse()
        assert all(s.line_start >= 1 for s in syms)

    def test_line_end_gte_line_start(self):
        code = "def fn():\n    x = 1\n    return x\n"
        syms, _, _ = _parser(code).parse()
        fn = next(s for s in syms if s.name == "fn")
        assert fn.line_end >= fn.line_start


# ── Python __all__ export narrowing ──────────────────────────────────────────

class TestPythonDunderAll:
    def test_dunder_all_marks_listed_symbols_exported(self):
        code = '__all__ = ["pub"]\ndef pub(): pass\ndef _priv(): pass\n'
        syms, _, _ = _parser(code).parse()
        pub = next(s for s in syms if s.name == "pub")
        priv = next(s for s in syms if s.name == "_priv")
        assert pub.exported is True
        assert priv.exported is False

    def test_without_dunder_all_export_follows_underscore_convention(self):
        code = "def visible(): pass\ndef _hidden(): pass\n"
        syms, _, _ = _parser(code).parse()
        visible = next(s for s in syms if s.name == "visible")
        hidden = next(s for s in syms if s.name == "_hidden")
        assert visible.exported is True
        assert hidden.exported is False


# ── CJS module.exports ────────────────────────────────────────────────────────

class TestCJSExports:
    def test_module_exports_assignment_marks_exported(self):
        code = "function handler() {}\nmodule.exports = handler;\n"
        p = FileParser("server.js", Language.JAVASCRIPT, code.encode())
        syms, _, _ = p.parse()
        h = next((s for s in syms if s.name == "handler"), None)
        assert h is not None
        assert h.exported is True


# ── Dynamic imports ───────────────────────────────────────────────────────────

class TestDynamicImports:
    def test_dynamic_import_is_captured(self):
        code = "const Comp = React.lazy(() => import('./Foo'));\n"
        p = FileParser("app.tsx", Language.TSX, code.encode())
        _, _, imports = p.parse()
        assert any("Foo" in imp for imp in imports)

    def test_static_import_also_captured(self):
        code = "import { foo } from './utils';\n"
        p = FileParser("app.ts", Language.TYPESCRIPT, code.encode())
        _, _, imports = p.parse()
        assert any("utils" in imp for imp in imports)


# ── _first_comment_above ──────────────────────────────────────────────────────

class TestFirstCommentAbove:
    def _get_func_node(self, code: str):
        import tree_sitter_python as tspython
        from tree_sitter import Language as TSLanguage, Parser
        ts_lang = TSLanguage(tspython.language())
        parser = Parser(ts_lang)
        source = code.encode()
        tree = parser.parse(source)
        for child in tree.root_node.children:
            if child.type == "function_definition":
                return child, source
        return None, source

    def test_extracts_comment_above_function(self):
        code = "# helper function\ndef fn(): pass\n"
        node, source = self._get_func_node(code)
        assert node is not None
        text = _first_comment_above(node, source)
        assert "helper function" in text

    def test_returns_empty_when_no_comment(self):
        code = "def fn(): pass\n"
        node, source = self._get_func_node(code)
        assert node is not None
        text = _first_comment_above(node, source)
        assert text == ""

    def test_strips_comment_prefixes(self):
        code = "# this is a doc\ndef fn(): pass\n"
        node, source = self._get_func_node(code)
        text = _first_comment_above(node, source)
        assert not text.startswith("#")


# ── _scan_calls ───────────────────────────────────────────────────────────────

class TestScanCalls:
    def test_function_call_creates_call_edge(self):
        code = "from b import target\ndef caller():\n    target()\n"
        syms, edges, _ = _parser(code).parse()
        call_edges = [e for e in edges if e.kind == EdgeKind.CALLS]
        assert len(call_edges) > 0

    def test_call_target_name_captured(self):
        code = "def caller():\n    some_fn()\n"
        syms, edges, _ = _parser(code).parse()
        call_edges = [e for e in edges if e.kind == EdgeKind.CALLS]
        assert any("some_fn" in e.target_id for e in call_edges)

    def test_method_call_captured(self):
        code = "class A:\n    def method(self):\n        self.helper()\n"
        syms, edges, _ = _parser(code).parse()
        call_edges = [e for e in edges if e.kind == EdgeKind.CALLS]
        assert len(call_edges) > 0

    def test_call_in_typescript(self):
        code = "function run(): void { myHelper(); }\n"
        p = FileParser("api.ts", Language.TYPESCRIPT, code.encode())
        _, edges, _ = p.parse()
        call_edges = [e for e in edges if e.kind == EdgeKind.CALLS]
        assert any("myHelper" in e.target_id for e in call_edges)
