"""Universal tree-sitter based code parser.

Extracts symbols (functions, classes, methods, etc.) and edges (calls, imports,
contains) from source files in any supported language.
"""
from __future__ import annotations

import re
from pathlib import Path

import tree_sitter_python as tspython
import tree_sitter_typescript as tstypescript
import tree_sitter_javascript as tsjavascript
import tree_sitter_rust as tsrust
import tree_sitter_go as tsgo
import tree_sitter_java as tsjava
import tree_sitter_c_sharp as tscsharp
import tree_sitter_ruby as tsruby
from tree_sitter import Language as TSLanguage, Parser, Node

from .types import (
    Tempo, Edge, EdgeKind, FileInfo, Language, Symbol, SymbolKind,
    EXTENSION_TO_LANGUAGE,
)
from .lang._utils import _node_text, _first_comment_above, _extract_signature
from .lang import PythonHandlerMixin, JSHandlerMixin, GoHandlerMixin, JavaHandlerMixin, CsharpHandlerMixin, RubyHandlerMixin, ZigHandlerMixin, CHandlerMixin, RustHandlerMixin, SwiftHandlerMixin, PHPHandlerMixin, KotlinHandlerMixin, DartHandlerMixin, ElixirHandlerMixin, ScalaHandlerMixin, OCamlHandlerMixin, FSharpHandlerMixin, HaskellHandlerMixin, LuaHandlerMixin, ClojureHandlerMixin, ErlangHandlerMixin, RHandlerMixin, JuliaHandlerMixin, BashHandlerMixin

# Build tree-sitter languages
_LANGUAGES: dict[Language, TSLanguage] = {}


def _get_ts_language(lang: Language) -> TSLanguage | None:
    if lang in _LANGUAGES:
        return _LANGUAGES[lang]
    mapping = {
        Language.PYTHON: tspython.language,
        Language.TYPESCRIPT: lambda: tstypescript.language_typescript(),
        Language.TSX: lambda: tstypescript.language_tsx(),
        Language.JAVASCRIPT: tsjavascript.language,
        Language.JSX: tsjavascript.language,
        Language.RUST: tsrust.language,
        Language.GO: tsgo.language,
        Language.JAVA: tsjava.language,
        Language.CSHARP: tscsharp.language,
        Language.RUBY: tsruby.language,
    }
    factory = mapping.get(lang)
    if factory is None:
        # Fallback: try tree-sitter-language-pack for 170+ languages
        try:
            from tree_sitter_language_pack import get_language as get_pack_language
            pack_name = _LANGUAGE_PACK_NAMES.get(lang, lang.value.lower())
            ts_lang = get_pack_language(pack_name)  # already returns TSLanguage
            _LANGUAGES[lang] = ts_lang
            return ts_lang
        except (ImportError, Exception):
            return None
    ts_lang = TSLanguage(factory())
    _LANGUAGES[lang] = ts_lang
    return ts_lang


# Mapping from Language enum to tree-sitter-language-pack names
_LANGUAGE_PACK_NAMES: dict[Language, str] = {
    Language.CSHARP: "c_sharp",
    Language.CPP: "cpp",
}


class FileParser(PythonHandlerMixin, JSHandlerMixin, GoHandlerMixin, JavaHandlerMixin, CsharpHandlerMixin, RubyHandlerMixin, ZigHandlerMixin, CHandlerMixin, RustHandlerMixin, SwiftHandlerMixin, PHPHandlerMixin, KotlinHandlerMixin, DartHandlerMixin, ElixirHandlerMixin, ScalaHandlerMixin, OCamlHandlerMixin, FSharpHandlerMixin, HaskellHandlerMixin, LuaHandlerMixin, ClojureHandlerMixin, ErlangHandlerMixin, RHandlerMixin, JuliaHandlerMixin, BashHandlerMixin):
    """Parse a single source file and extract symbols + edges."""

    def __init__(self, file_path: str, language: Language, source: bytes, *, is_tauri: bool = False):
        self.file_path = file_path
        self.language = language
        self.source = source
        self.is_tauri = is_tauri
        self.symbols: list[Symbol] = []
        self.edges: list[Edge] = []
        self.imports: list[str] = []
        self._symbol_stack: list[str] = []  # parent tracking
        self._dunder_all: list[str] | None = None  # Python __all__ names
        self._cjs_exports: set[str] = set()  # names exported via module.exports = X
        self._ignore_set = self._get_ignore_set(language)

    def parse(self) -> tuple[list[Symbol], list[Edge], list[str]]:
        ts_lang = _get_ts_language(self.language)
        if ts_lang is None:
            return [], [], []

        parser = Parser(ts_lang)
        tree = parser.parse(self.source)

        self._walk(tree.root_node)

        # Detect dynamic import() expressions — e.g. lazy(() => import('./Foo'))
        # Runs once per file via regex since tree-sitter treats import() as call_expression
        if self.language in (Language.TYPESCRIPT, Language.TSX, Language.JAVASCRIPT, Language.JSX):
            import re
            source_str = self.source.decode("utf-8", errors="replace") if isinstance(self.source, bytes) else self.source
            for m in re.finditer(r'''import\(\s*['"]([^'"]+)['"]\s*\)''', source_str):
                self.imports.append(f"import '{m.group(1)}'")

        # Apply Python __all__ export narrowing
        if self._dunder_all is not None:
            all_set = set(self._dunder_all)
            self.symbols = [
                Symbol(
                    id=s.id, name=s.name, qualified_name=s.qualified_name,
                    kind=s.kind, language=s.language, file_path=s.file_path,
                    line_start=s.line_start, line_end=s.line_end,
                    signature=s.signature, doc=s.doc, parent_id=s.parent_id,
                    exported=(s.name in all_set),
                    complexity=s.complexity, byte_size=s.byte_size,
                ) if s.parent_id is None else s  # only affect top-level symbols
                for s in self.symbols
            ]

        # Apply CommonJS module.exports = identifier export marking
        if self._cjs_exports:
            self.symbols = [
                Symbol(
                    id=s.id, name=s.name, qualified_name=s.qualified_name,
                    kind=s.kind, language=s.language, file_path=s.file_path,
                    line_start=s.line_start, line_end=s.line_end,
                    signature=s.signature, doc=s.doc, parent_id=s.parent_id,
                    exported=True,
                    complexity=s.complexity, byte_size=s.byte_size,
                ) if s.parent_id is None and s.name in self._cjs_exports else s
                for s in self.symbols
            ]

        return self.symbols, self.edges, self.imports

    def _make_id(self, name: str) -> str:
        if self._symbol_stack:
            parent_qname = self._parent_qualified_name()
            if parent_qname:
                return f"{self.file_path}::{parent_qname}.{name}"
        return f"{self.file_path}::{name}"

    def _current_parent_id(self) -> str | None:
        return self._symbol_stack[-1] if self._symbol_stack else None

    def _parent_qualified_name(self) -> str | None:
        """Get the qualified name of the current parent symbol."""
        if not self._symbol_stack:
            return None
        pid = self._symbol_stack[-1]
        for s in reversed(self.symbols):
            if s.id == pid:
                return s.qualified_name
        return None

    def _walk(self, node: Node) -> None:
        handler = getattr(self, f"_handle_{self.language.value}", None)
        if handler:
            handler(node)
        else:
            self._handle_generic(node)

    # ── Generic fallback ────────────────────────────────────

    # Node types that represent symbol definitions across languages.
    # Not universal — each grammar uses its own names. This set covers the most common patterns.
    _GENERIC_FUNCTION_TYPES = frozenset({
        "function_definition", "function_declaration", "function_item",
        "method_definition", "method_declaration",
        "arrow_function", "lambda",
        "procedure_declaration", "subroutine",
        # Less common but valid across grammars
        "func_literal", "anonymous_function",
    })
    _GENERIC_CLASS_TYPES = frozenset({
        "class_definition", "class_declaration",
        "struct_definition", "struct_item", "struct_declaration",
        "interface_declaration", "trait_item",
        "enum_definition", "enum_declaration", "enum_item",
        "module_definition", "module_declaration",
        "protocol_declaration", "extension_declaration",
        "object_declaration",  # Kotlin
        "companion_object",  # Kotlin companion objects
        "object_definition", "trait_definition",  # Scala
    })
    # Field names that different grammars use for the symbol name
    _GENERIC_NAME_FIELDS = ("name", "type_identifier", "simple_identifier", "identifier")

    def _generic_find_name(self, node: Node) -> Node | None:
        """Try multiple field names to find the name node (grammars differ)."""
        for field in self._GENERIC_NAME_FIELDS:
            name_node = node.child_by_field_name(field)
            if name_node:
                return name_node
        # Some grammars put the name as a direct named child without a field
        for child in node.children:
            if child.type in ("name", "identifier", "simple_identifier", "type_identifier"):
                return child
        return None

    def _handle_generic(self, node: Node) -> None:
        """Fallback handler for languages without a custom _handle_X.

        Extracts functions, classes, and structs using common tree-sitter node type patterns.
        Less precise than custom handlers but covers 170+ languages via tree-sitter-language-pack.
        """
        if node.type in self._GENERIC_FUNCTION_TYPES:
            name_node = self._generic_find_name(node)
            if name_node:
                name = _node_text(name_node, self.source)
                if name and not name.startswith("_"):
                    sym_id = f"{self.file_path}::{name}"
                    sig = _node_text(node, self.source).split("\n")[0][:200]
                    sym = Symbol(
                        id=sym_id, name=name, qualified_name=name,
                        kind=SymbolKind.FUNCTION, language=self.language,
                        file_path=self.file_path,
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        signature=sig, exported=True,
                        complexity=self._compute_complexity(node),
                        byte_size=node.end_byte - node.start_byte,
                    )
                    self.symbols.append(sym)
                    self._scan_decorators(node, sym_id)
                    self._scan_calls(node, sym_id)
                    self._scan_type_annotations(node, sym_id)
        elif node.type in self._GENERIC_CLASS_TYPES:
            name_node = self._generic_find_name(node)
            if name_node:
                name = _node_text(name_node, self.source)
                if name:
                    kind = SymbolKind.CLASS
                    if "struct" in node.type:
                        kind = SymbolKind.STRUCT
                    elif "interface" in node.type or "trait" in node.type:
                        kind = SymbolKind.INTERFACE
                    elif "enum" in node.type:
                        kind = SymbolKind.ENUM
                    sym_id = f"{self.file_path}::{name}"
                    sym = Symbol(
                        id=sym_id, name=name, qualified_name=name,
                        kind=kind, language=self.language,
                        file_path=self.file_path,
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        signature=name, exported=True,
                        complexity=self._compute_complexity(node),
                        byte_size=node.end_byte - node.start_byte,
                    )
                    self.symbols.append(sym)
                    self._scan_decorators(node, sym_id)
                    # Scan body for methods/calls
                    # Try field names first, then look for body-like child nodes
                    body = node.child_by_field_name("body") or node.child_by_field_name("class_body")
                    if body is None:
                        body = node.child_by_field_name("declaration_list")
                    if body is None:
                        # Fallback: find body-like child node by type name
                        for child in node.children:
                            if child.type in ("class_body", "declaration_list", "body",
                                              "block", "compound_statement", "suite"):
                                body = child
                                break
                    if body:
                        for child in body.children:
                            if child.type in self._GENERIC_FUNCTION_TYPES or child.type in ("method_declaration",):
                                method_name_node = self._generic_find_name(child)
                                if method_name_node:
                                    method_name = _node_text(method_name_node, self.source)
                                    method_id = f"{self.file_path}::{name}.{method_name}"
                                    method_sig = _node_text(child, self.source).split("\n")[0][:200]
                                    # Detect private/protected visibility modifiers
                                    _exported = True
                                    for mc in child.children:
                                        if mc.type in ("visibility_modifier", "access_modifier"):
                                            if _node_text(mc, self.source).lower() in ("private", "protected"):
                                                _exported = False
                                            break
                                    method_sym = Symbol(
                                        id=method_id, name=method_name,
                                        qualified_name=f"{name}.{method_name}",
                                        kind=SymbolKind.METHOD, language=self.language,
                                        file_path=self.file_path,
                                        line_start=child.start_point[0] + 1,
                                        line_end=child.end_point[0] + 1,
                                        signature=method_sig, parent_id=sym_id,
                                        exported=_exported,
                                        complexity=self._compute_complexity(child),
                                        byte_size=child.end_byte - child.start_byte,
                                    )
                                    self.symbols.append(method_sym)
                                    self.edges.append(Edge(EdgeKind.CONTAINS, sym_id, method_id))
                                    self._scan_decorators(child, method_id)
                                    self._scan_calls(child, method_id)
                                    self._scan_type_annotations(child, method_id)

        # Recurse into children — but skip body of classes (already handled methods above)
        if node.type in self._GENERIC_CLASS_TYPES:
            return  # class body already processed, don't double-count methods
        for child in node.children:
            self._handle_generic(child)

    # ── Shared helpers ──────────────────────────────────────

    # ── Language-aware built-in ignore sets ──────────────────
    # Names that should never resolve to user-defined symbols.
    # Split per-language so Rust `collect()` doesn't suppress a Python
    # function named collect(), and Python `open()` doesn't suppress a
    # JS function named open().

    _BUILTIN_IGNORE_UNIVERSAL = frozenset({
        # Console / logging — every language's stdlib has these as noise
        "log", "warn", "error", "info", "debug", "trace", "dir", "table",
        # JSON-like serialisation
        "stringify",
        # Object introspection (cross-language noise)
        "keys", "values", "entries", "assign", "freeze",
        "from", "of", "isArray",
        # String primitives (too generic to be user functions)
        "split", "trim", "trimStart", "trimEnd",
        "startsWith", "endsWith", "padStart", "padEnd", "repeat", "charAt",
        "toLowerCase", "toUpperCase", "toFixed", "parseInt", "parseFloat",
        # Timer / scheduling
        "setTimeout", "setInterval", "clearTimeout", "clearInterval",
        "requestAnimationFrame", "cancelAnimationFrame",
        # DOM event plumbing (no non-browser codebase defines these)
        "addEventListener", "removeEventListener", "preventDefault", "stopPropagation",
        "querySelector", "querySelectorAll", "getElementById", "getAttribute",
        "setAttribute", "removeAttribute", "appendChild", "removeChild",
        "insertBefore", "cloneNode",
        # React internals
        "createElement", "createRef", "createContext", "forwardRef", "memo",
        # Encoding
        "encode", "decode", "atob", "btoa",
        # Generic collection noise (has/clear/size are too ambiguous as bare calls)
        "has", "clear", "size",
        # valueOf / toString — universal noise
        "toString", "valueOf",
    })

    _BUILTIN_IGNORE_JS = _BUILTIN_IGNORE_UNIVERSAL | frozenset({
        # JSON.parse, Date.now
        "parse", "now",
        # Array methods
        "map", "filter", "reduce", "forEach", "find", "findIndex",
        "some", "every", "includes", "indexOf", "join", "slice", "splice",
        "push", "pop", "shift", "unshift", "sort", "reverse",
        "flat", "flatMap", "fill", "concat",
        # Math
        "min", "max", "floor", "ceil", "round", "abs", "sqrt", "pow", "random",
        # Promise
        "resolve", "reject", "all", "allSettled", "race", "any",
        "then", "catch", "finally",
        # Map / Set
        "get", "set", "delete", "add",
        # String
        "match", "replace", "replaceAll",
        # DOM interaction
        "focus", "blur", "click", "scroll", "scrollTo", "scrollIntoView",
        "submit",
        # Fetch API
        "fetch", "json", "text", "blob", "arrayBuffer", "formData",
        # I/O keywords that are DOM / Node built-ins
        "open", "close", "write", "read", "abort",
        # Collection helpers
        "contains",
    })

    _BUILTIN_IGNORE_PYTHON = _BUILTIN_IGNORE_UNIVERSAL | frozenset({
        # Builtins
        "print", "len", "range", "enumerate", "zip", "isinstance", "type",
        "str", "int", "float", "bool", "list", "dict", "tuple", "set",
        "sorted", "reversed", "any", "all", "sum", "min", "max",
        "open",
        # Attribute introspection
        "getattr", "setattr", "hasattr", "delattr",
        # OOP decorators / helpers
        "super", "property", "staticmethod", "classmethod", "abstractmethod",
        # Collection methods
        "append", "extend", "update", "copy", "deepcopy", "items",
    })

    _BUILTIN_IGNORE_RUST = _BUILTIN_IGNORE_UNIVERSAL | frozenset({
        # Result / Option
        "unwrap", "expect", "ok", "err", "and_then", "or_else",
        # Iterator
        "collect", "iter", "into_iter", "chain", "enumerate", "zip",
        "map", "filter", "find",
        # Conversion / clone
        "clone", "copy", "to_string", "to_owned", "as_ref", "as_mut",
        # Collections
        "push", "pop", "insert", "remove", "contains", "len", "is_empty",
        # I/O
        "read", "write", "flush", "close", "open",
        # Macros / formatting
        "format", "display", "println", "eprintln", "dbg", "vec",
        "todo", "unimplemented",
    })

    _BUILTIN_IGNORE_GO = _BUILTIN_IGNORE_UNIVERSAL | frozenset({
        # Builtins
        "len", "cap", "make", "new", "append", "copy", "delete",
        "close", "panic", "recover", "print", "println",
    })

    _BUILTIN_IGNORE_JAVA = _BUILTIN_IGNORE_UNIVERSAL | frozenset({
        # Collection interface
        "get", "set", "put", "add", "remove", "contains", "size",
        # Object methods
        "toString", "equals", "hashCode", "compareTo",
        # I/O
        "println", "printf", "format", "print",
    })

    _LANGUAGE_IGNORE_MAP: dict[Language, frozenset[str]] = {
        Language.JAVASCRIPT: _BUILTIN_IGNORE_JS,
        Language.JSX:        _BUILTIN_IGNORE_JS,
        Language.TYPESCRIPT:  _BUILTIN_IGNORE_JS,
        Language.TSX:         _BUILTIN_IGNORE_JS,
        Language.PYTHON:      _BUILTIN_IGNORE_PYTHON,
        Language.RUST:        _BUILTIN_IGNORE_RUST,
        Language.GO:          _BUILTIN_IGNORE_GO,
        Language.JAVA:        _BUILTIN_IGNORE_JAVA,
        Language.CSHARP:      _BUILTIN_IGNORE_JAVA,  # close enough
    }

    @classmethod
    def _get_ignore_set(cls, language: Language) -> frozenset[str]:
        """Return the built-in ignore set appropriate for *language*."""
        return cls._LANGUAGE_IGNORE_MAP.get(language, cls._BUILTIN_IGNORE_UNIVERSAL)

    _BRANCH_TYPES = frozenset({
        "if_statement", "elif_clause", "else_clause",
        "for_statement", "for_in_statement", "while_statement",
        "switch_case", "catch_clause", "ternary_expression",
        "conditional_expression",
        "match_arm", "if_expression", "if_let_expression",
        "logical_and", "logical_or", "&&", "||",
        "try_statement", "except_clause",
    })

    def _compute_complexity(self, node: Node) -> int:
        """Count branching nodes for cyclomatic complexity estimate."""
        count = 1  # base complexity
        def _walk(n: Node) -> None:
            nonlocal count
            if n.type in self._BRANCH_TYPES:
                count += 1
            if n.type == "binary_expression":
                op = n.child_by_field_name("operator")
                if op:
                    op_text = _node_text(op, self.source)
                    if op_text in ("&&", "||", "and", "or"):
                        count += 1
            for child in n.children:
                _walk(child)
        _walk(node)
        return count

    # Decorators that are language features, not framework dispatch
    _BUILTIN_DECORATORS = frozenset({
        "property", "staticmethod", "classmethod", "abstractmethod",
        "override", "dataclass", "dataclasses.dataclass",
        "typing.overload", "overload",
        "functools.wraps", "wraps",
        "functools.lru_cache", "lru_cache",
        "functools.cache", "cache",
        "functools.cached_property", "cached_property",
        "typing.final", "final",
    })

    def _scan_decorators(self, node: Node, sym_id: str) -> None:
        """Create CALLS edges from decorators to the decorated symbol.

        Handles tree-sitter grammars where decorator nodes are children of
        the function/class node or its parent (TS, Kotlin, etc.).
        Python decorators are handled in python_handler via the decorators list.
        """
        for child in node.children:
            if child.type == "decorator":
                dec_text = _node_text(child, self.source).lstrip("@").strip()
                dec_name = dec_text.split("(")[0].strip()
                if dec_name and dec_name not in self._BUILTIN_DECORATORS and not dec_name.startswith("_"):
                    self.edges.append(Edge(
                        EdgeKind.CALLS, dec_name, sym_id,
                        child.start_point[0] + 1,
                    ))
        parent = node.parent
        if parent and parent.type in ("decorated_definition", "decorated"):
            for child in parent.children:
                if child.type == "decorator":
                    dec_text = _node_text(child, self.source).lstrip("@").strip()
                    dec_name = dec_text.split("(")[0].strip()
                    if dec_name and dec_name not in self._BUILTIN_DECORATORS and not dec_name.startswith("_"):
                        self.edges.append(Edge(
                            EdgeKind.CALLS, dec_name, sym_id,
                            child.start_point[0] + 1,
                        ))

    def _scan_calls(self, node: Node, from_id: str, *, depth: int = 0) -> None:
        """Recursively scan a node for function call expressions."""
        if depth > 20:
            return
        if node.type in ("call_expression", "call", "method_invocation", "invocation_expression"):
            func_node = node.child_by_field_name("function") or (
                node.children[0] if node.children else None
            )
            # Java method_invocation: has "object" and "name" fields instead of "function"
            java_name = node.child_by_field_name("name")
            java_obj = node.child_by_field_name("object")
            if java_name and not func_node:
                func_node = java_name
            if func_node:
                raw = _node_text(func_node, self.source)
                # For Java/C# method_invocation with object, build qualified name
                # Only use simple identifiers as object — skip chained calls like repo.findAll()
                if java_obj and java_name and node.type == "method_invocation":
                    obj_text = _node_text(java_obj, self.source)
                    # If object is a simple identifier, use it. Otherwise skip the qualifier.
                    if java_obj.type == "identifier":
                        raw = f"{obj_text}.{_node_text(java_name, self.source)}"
                    else:
                        raw = _node_text(java_name, self.source)
                # Detect Tauri invoke("command_name") — cross-language bridge (only in Tauri projects)
                if self.is_tauri and (raw == "invoke" or raw.endswith(".invoke")):
                    args = node.child_by_field_name("arguments")
                    if args and args.children:
                        for arg in args.children:
                            if arg.type == "string" or arg.type == "template_string":
                                cmd = _node_text(arg, self.source).strip("'\"` ")
                                if cmd and cmd.isidentifier():
                                    self.edges.append(Edge(
                                        EdgeKind.CALLS, from_id, cmd,
                                        node.start_point[0] + 1,
                                    ))
                                break
                # Clean up chained call artifacts — remove anything with parens/brackets
                # e.g. "findAll().stream" → "stream", "items.Where(x => x.Active).ToList" → "ToList"
                if "(" in raw or ")" in raw:
                    # Take only the last clean segment after the last )
                    after_paren = raw.rsplit(")", 1)[-1].lstrip(".")
                    if after_paren and after_paren.isidentifier():
                        raw = after_paren
                    elif "." in raw:
                        # Try to extract the last two clean identifiers
                        clean_parts = [p for p in raw.replace("(", ".").replace(")", ".").split(".") if p.isidentifier()]
                        if clean_parts:
                            raw = ".".join(clean_parts[-2:]) if len(clean_parts) >= 2 else clean_parts[-1]
                        else:
                            raw = ""  # no valid identifiers found
                    else:
                        raw = ""  # only parens, no identifier

                if not raw:
                    # No valid call target — skip to children
                    for child in node.children:
                        self._scan_calls(child, from_id, depth=depth + 1)
                    return

                # For member expressions (obj.method), keep both qualified and bare name
                # so edge resolution can match Type.method first, then fall back to method
                if "." in raw:
                    # Keep last two segments: Type.method or obj.method
                    parts = raw.rsplit(".", 2)
                    qualified = ".".join(parts[-2:]) if len(parts) >= 2 else raw
                    bare = parts[-1]
                else:
                    qualified = None
                    bare = raw
                # Skip built-ins — but only for bare calls (no qualifier).
                # obj.parse() is a real call even though bare "parse" is in ignore list.
                is_qualified = qualified is not None
                if bare and not bare.startswith("("):
                    if is_qualified or bare not in self._ignore_set:
                        target = qualified if is_qualified else bare
                        self.edges.append(Edge(
                            EdgeKind.CALLS, from_id, target,
                            node.start_point[0] + 1,
                        ))
            # Recurse into arguments only — skip the receiver chain so that
            # a.get().min().filter() registers as 1 edge, not 3.
            args_node = node.child_by_field_name("arguments")
            if args_node:
                for child in args_node.children:
                    self._scan_calls(child, from_id, depth=depth + 1)
            return
        # Traverse spread elements — e.g. ...createSlice(...args) inside object literals
        if node.type == "spread_element":
            for child in node.children:
                self._scan_calls(child, from_id, depth=depth + 1)
            return
        for child in node.children:
            self._scan_calls(child, from_id, depth=depth + 1)

    # Type names that are containers/builtins — never create USES_TYPE edges for these.
    _TYPE_IGNORE = frozenset({
        "Optional", "List", "Dict", "Tuple", "Set", "FrozenSet", "Union",
        "Sequence", "Iterable", "Iterator", "Generator", "Coroutine",
        "Awaitable", "AsyncIterator", "AsyncGenerator", "Mapping",
        "MutableMapping", "MutableSequence", "MutableSet", "Callable",
        "Type", "ClassVar", "Final", "Literal", "Annotated",
        "Any", "NoReturn", "Never",
    })

    def _scan_type_annotations(self, node: Node, from_id: str) -> None:
        """Scan function parameters and return type for type references.

        Creates USES_TYPE edges from the function to referenced user-defined types.
        Skips lowercase builtins (int, str, etc.) and container types (Optional, List, etc.).
        """
        # Parameter type annotations
        params = node.child_by_field_name("parameters")
        if params:
            for child in params.children:
                type_node = child.child_by_field_name("type")
                if type_node:
                    self._extract_type_refs(type_node, from_id)

        # Return type annotation
        return_type = node.child_by_field_name("return_type")
        if return_type:
            self._extract_type_refs(return_type, from_id)

    def _extract_type_refs(self, type_node: Node, from_id: str) -> None:
        """Extract user-defined type names from a type annotation node."""
        text = _node_text(type_node, self.source).strip().lstrip("->").strip()
        if not text:
            return
        # Split on common type combinators: [], |, ,
        # e.g. "Optional[User]" -> ["Optional", "User"]
        # e.g. "User | None" -> ["User", "None"]
        parts = re.split(r'[\[\]|,\s]+', text)
        for part in parts:
            part = part.strip()
            if (
                part
                and len(part) >= 2
                and part[0].isupper()
                and part not in self._TYPE_IGNORE
            ):
                self.edges.append(Edge(EdgeKind.USES_TYPE, from_id, part))
