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
from .lang import PythonHandlerMixin, JSHandlerMixin

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


class FileParser(PythonHandlerMixin, JSHandlerMixin):
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

    # ── Rust ────────────────────────────────────────────────

    def _handle_rust(self, node: Node) -> None:
        for child in node.children:
            t = child.type
            if t == "use_declaration":
                self.imports.append(_node_text(child, self.source).strip())
            elif t == "function_item":
                self._handle_rust_function(child)
            elif t == "struct_item":
                self._handle_rust_struct(child)
            elif t == "enum_item":
                self._handle_rust_enum(child)
            elif t == "trait_item":
                self._handle_rust_trait(child)
            elif t == "impl_item":
                self._handle_rust_impl(child)
            elif t == "const_item" or t == "static_item":
                self._handle_rust_const(child)
            elif t == "mod_item":
                self._handle_rust_mod(child)
            elif t == "macro_definition":
                self._handle_rust_macro(child)
            elif t == "attribute_item":
                pass  # skip attributes, they decorate the next item

    def _handle_rust_function(self, node: Node, *, is_method: bool = False) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        kind = SymbolKind.METHOD if is_method else SymbolKind.FUNCTION
        # Detect test functions and Tauri commands
        prev = node.prev_named_sibling
        if prev and prev.type == "attribute_item":
            attr_text = _node_text(prev, self.source)
            if "test" in attr_text:
                kind = SymbolKind.TEST
            elif "tauri::command" in attr_text:
                kind = SymbolKind.COMMAND

        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name,
            qualified_name=f"{self._parent_qualified_name()}.{name}" if self._symbol_stack else name,
            kind=kind, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=_extract_signature(node, self.source, self.language),
            doc=doc,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
            complexity=self._compute_complexity(node),
        )
        self.symbols.append(sym)
        if self._current_parent_id():
            self.edges.append(Edge(EdgeKind.CONTAINS, self._current_parent_id(), sym_id))
        body = node.child_by_field_name("body")
        if body:
            self._scan_calls(body, sym_id)

    def _handle_rust_struct(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.STRUCT, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=_extract_signature(node, self.source, self.language),
            doc=doc,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    def _handle_rust_enum(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.ENUM, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            doc=doc,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    def _handle_rust_trait(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.TRAIT, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            doc=doc,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)
        body = node.child_by_field_name("body")
        if body:
            self._symbol_stack.append(sym_id)
            for child in body.children:
                if child.type == "function_item":
                    self._handle_rust_function(child, is_method=True)
            self._symbol_stack.pop()

    def _handle_rust_impl(self, node: Node) -> None:
        type_node = node.child_by_field_name("type")
        if not type_node:
            return
        type_name = _node_text(type_node, self.source)

        # Detect "impl Trait for Type" — the trait field holds the trait name
        trait_node = node.child_by_field_name("trait")
        trait_name = _node_text(trait_node, self.source) if trait_node else None

        # Find the matching struct/enum symbol
        target_id = None
        for sym in self.symbols:
            if sym.name == type_name and sym.file_path == self.file_path:
                target_id = sym.id
                break

        # Create IMPLEMENTS edge: Type → Trait
        if trait_name and target_id:
            self.edges.append(Edge(
                EdgeKind.IMPLEMENTS, target_id, trait_name,
                node.start_point[0] + 1,
            ))

        body = node.child_by_field_name("body")
        if not body:
            return
        parent = target_id or self._make_id(f"impl_{type_name}")
        if not target_id:
            # Create a synthetic impl symbol
            impl_sym = Symbol(
                id=parent, name=f"impl {type_name}", qualified_name=f"impl {type_name}",
                kind=SymbolKind.IMPL, language=self.language,
                file_path=self.file_path,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                parent_id=self._current_parent_id(),
            )
            self.symbols.append(impl_sym)
        self._symbol_stack.append(parent)
        for child in body.children:
            if child.type == "function_item":
                self._handle_rust_function(child, is_method=True)
        self._symbol_stack.pop()

    def _handle_rust_const(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.CONSTANT, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            parent_id=self._current_parent_id(),
        )
        self.symbols.append(sym)

    def _handle_rust_mod(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.MODULE, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            parent_id=self._current_parent_id(),
        )
        self.symbols.append(sym)
        body = node.child_by_field_name("body")
        if body:
            self._symbol_stack.append(sym_id)
            self._handle_rust(body)
            self._symbol_stack.pop()

    def _handle_rust_macro(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.FUNCTION, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            doc="macro",
            parent_id=self._current_parent_id(),
        )
        self.symbols.append(sym)

    # ── Go ──────────────────────────────────────────────────

    def _handle_go(self, node: Node) -> None:
        for child in node.children:
            t = child.type
            if t == "import_declaration":
                self.imports.append(_node_text(child, self.source).strip())
            elif t == "function_declaration":
                self._handle_go_function(child)
            elif t == "method_declaration":
                self._handle_go_function(child, is_method=True)
            elif t == "type_declaration":
                self._handle_go_type(child)

    def _handle_go_function(self, node: Node, *, is_method: bool = False) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)

        # Extract receiver type for methods: func (r *ReceiverType) MethodName()
        receiver_type = None
        if is_method:
            receiver = node.child_by_field_name("receiver")
            if receiver:
                # parameter_list → parameter_declaration → type
                for param in receiver.children:
                    if param.type == "parameter_declaration":
                        type_node = param.child_by_field_name("type")
                        if type_node:
                            rt = _node_text(type_node, self.source).lstrip("*")
                            if rt:
                                receiver_type = rt

        qualified = f"{receiver_type}.{name}" if receiver_type else name
        # For Go methods, make the ID include the receiver type
        if receiver_type:
            sym_id = f"{self.file_path}::{receiver_type}.{name}"
        else:
            sym_id = self._make_id(name)

        # Find the receiver struct symbol and create CONTAINS edge
        parent_id = None
        if receiver_type:
            candidate = f"{self.file_path}::{receiver_type}"
            for s in self.symbols:
                if s.id == candidate:
                    parent_id = s.id
                    break

        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=qualified,
            kind=SymbolKind.METHOD if is_method else SymbolKind.FUNCTION,
            language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=_extract_signature(node, self.source, self.language),
            doc=doc,
            parent_id=parent_id,
            exported=name[0:1].isupper(),
            byte_size=node.end_byte - node.start_byte,
            complexity=self._compute_complexity(node),
        )
        self.symbols.append(sym)
        if parent_id:
            self.edges.append(Edge(EdgeKind.CONTAINS, parent_id, sym_id))
        body = node.child_by_field_name("body")
        if body:
            self._scan_calls(body, sym_id)

    def _handle_go_type(self, node: Node) -> None:
        for child in node.children:
            if child.type == "type_spec":
                name_node = child.child_by_field_name("name")
                type_node = child.child_by_field_name("type")
                if not name_node:
                    continue
                name = _node_text(name_node, self.source)
                sym_id = self._make_id(name)
                kind = SymbolKind.STRUCT
                if type_node and type_node.type == "interface_type":
                    kind = SymbolKind.INTERFACE
                sym = Symbol(
                    id=sym_id, name=name, qualified_name=name,
                    kind=kind, language=self.language,
                    file_path=self.file_path,
                    line_start=child.start_point[0] + 1,
                    line_end=child.end_point[0] + 1,
                    exported=name[0:1].isupper(),
                    byte_size=child.end_byte - child.start_byte,
                )
                self.symbols.append(sym)

                # Detect struct embedding (field_declaration with type but no field_identifier)
                if type_node and type_node.type == "struct_type":
                    for sub in type_node.children:
                        if sub.type == "field_declaration_list":
                            for field in sub.children:
                                if field.type == "field_declaration":
                                    has_name = any(c.type == "field_identifier" for c in field.children)
                                    if not has_name:
                                        # Embedded field — the type_identifier IS the embedded type
                                        for c in field.children:
                                            if c.type in ("type_identifier", "qualified_type", "pointer_type"):
                                                embedded = _node_text(c, self.source).lstrip("*")
                                                if embedded:
                                                    self.edges.append(Edge(
                                                        EdgeKind.INHERITS, sym_id, embedded,
                                                        field.start_point[0] + 1,
                                                    ))
                # Detect interface embedding (type_elem children)
                elif type_node and type_node.type == "interface_type":
                    for member in type_node.children:
                        if member.type == "type_elem":
                            for c in member.children:
                                if c.type in ("type_identifier", "qualified_type"):
                                    embedded = _node_text(c, self.source)
                                    if embedded:
                                        self.edges.append(Edge(
                                            EdgeKind.INHERITS, sym_id, embedded,
                                            member.start_point[0] + 1,
                                        ))

    # ── Java ───────────────────────────────────────────────

    def _handle_java(self, node: Node) -> None:
        for child in node.children:
            t = child.type
            if t == "import_declaration":
                self.imports.append(_node_text(child, self.source).strip())
            elif t == "class_declaration":
                self._handle_java_class(child)
            elif t == "interface_declaration":
                self._handle_java_interface(child)
            elif t == "enum_declaration":
                self._handle_java_enum(child)

    def _handle_java_class(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)

        # Check modifiers for public/export
        mods = node.child_by_field_name("modifiers") or node.children[0] if node.children and node.children[0].type == "modifiers" else None
        mod_text = _node_text(mods, self.source) if mods and mods.type == "modifiers" else ""
        exported = "public" in mod_text

        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.CLASS, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            doc=doc,
            exported=exported,
            byte_size=node.end_byte - node.start_byte,
            complexity=self._compute_complexity(node),
        )
        self.symbols.append(sym)

        # Check for extends/implements
        superclass = node.child_by_field_name("superclass")
        if superclass:
            sc_name = _node_text(superclass, self.source).replace("extends ", "").strip()
            if sc_name:
                self.edges.append(Edge(EdgeKind.INHERITS, sym_id, sc_name, node.start_point[0] + 1))
        interfaces = node.child_by_field_name("interfaces")
        if interfaces:
            # super_interfaces → type_list → type_identifier/generic_type
            for child in interfaces.children:
                if child.type == "type_list":
                    for tc in child.children:
                        if tc.type in ("type_identifier", "generic_type"):
                            iface = _node_text(tc, self.source).split("<")[0].strip()
                            if iface:
                                self.edges.append(Edge(EdgeKind.IMPLEMENTS, sym_id, iface, node.start_point[0] + 1))

        # Process class body
        body = node.child_by_field_name("body")
        if body:
            self._symbol_stack.append(sym_id)
            for child in body.children:
                if child.type == "method_declaration":
                    self._handle_java_method(child, sym_id, name)
                elif child.type == "constructor_declaration":
                    self._handle_java_constructor(child, sym_id, name)
                elif child.type == "class_declaration":
                    self._handle_java_class(child)  # inner class
                elif child.type == "interface_declaration":
                    self._handle_java_interface(child)
                elif child.type == "enum_declaration":
                    self._handle_java_enum(child)
            self._symbol_stack.pop()

    def _handle_java_method(self, node: Node, class_id: str, class_name: str) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        qualified = f"{class_name}.{name}"
        sym_id = f"{self.file_path}::{qualified}"

        mods = node.child_by_field_name("modifiers") or (node.children[0] if node.children and node.children[0].type == "modifiers" else None)
        mod_text = _node_text(mods, self.source) if mods and mods.type == "modifiers" else ""
        exported = "public" in mod_text

        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=qualified,
            kind=SymbolKind.METHOD, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=_extract_signature(node, self.source, self.language),
            doc=doc,
            parent_id=class_id,
            exported=exported,
            byte_size=node.end_byte - node.start_byte,
            complexity=self._compute_complexity(node),
        )
        self.symbols.append(sym)
        self.edges.append(Edge(EdgeKind.CONTAINS, class_id, sym_id))
        body = node.child_by_field_name("body")
        if body:
            self._scan_calls(body, sym_id)

    def _handle_java_constructor(self, node: Node, class_id: str, class_name: str) -> None:
        line = node.start_point[0] + 1
        params = node.child_by_field_name("parameters")
        nparams = len([c for c in (params.children if params else []) if c.type == "formal_parameter"]) if params else 0
        qualified = f"{class_name}.{class_name}"
        sym_id = f"{self.file_path}::{qualified}/{nparams}@{line}"
        sym = Symbol(
            id=sym_id, name=class_name, qualified_name=qualified,
            kind=SymbolKind.METHOD, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=_extract_signature(node, self.source, self.language),
            parent_id=class_id,
            exported=True,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)
        self.edges.append(Edge(EdgeKind.CONTAINS, class_id, sym_id))
        body = node.child_by_field_name("body")
        if body:
            self._scan_calls(body, sym_id)

    def _handle_java_interface(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.INTERFACE, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=True,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

        body = node.child_by_field_name("body")
        if body:
            self._symbol_stack.append(sym_id)
            for child in body.children:
                if child.type == "method_declaration":
                    self._handle_java_method(child, sym_id, name)
            self._symbol_stack.pop()

    def _handle_java_enum(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.ENUM, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=True,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    # ── C# ────────────────────────────────────────────────

    def _handle_csharp(self, node: Node) -> None:
        for child in node.children:
            t = child.type
            if t == "using_directive":
                self.imports.append(_node_text(child, self.source).strip())
            elif t == "namespace_declaration":
                # Recurse into namespace body
                body = child.child_by_field_name("body")
                if body:
                    self._handle_csharp(body)
            elif t == "file_scoped_namespace_declaration":
                self._handle_csharp(child)
            elif t == "class_declaration":
                self._handle_csharp_class(child)
            elif t == "interface_declaration":
                self._handle_csharp_interface(child)
            elif t == "enum_declaration":
                self._handle_csharp_enum(child)
            elif t == "struct_declaration":
                self._handle_csharp_struct(child)
            elif t in ("declaration_list",):
                self._handle_csharp(child)

    def _has_modifier(self, node: Node, mod: str) -> bool:
        for child in node.children:
            if child.type == "modifier":
                if _node_text(child, self.source).strip() == mod:
                    return True
        return False

    def _handle_csharp_class(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            # Try identifier child
            for c in node.children:
                if c.type == "identifier":
                    name_node = c
                    break
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        exported = self._has_modifier(node, "public")

        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.CLASS, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            doc=doc, exported=exported,
            byte_size=node.end_byte - node.start_byte,
            complexity=self._compute_complexity(node),
        )
        self.symbols.append(sym)

        # base_list for inheritance/implements
        # C# convention: interfaces start with I + uppercase (IDisposable, IComparable)
        for child in node.children:
            if child.type == "base_list":
                for bc in child.children:
                    if bc.type in ("identifier", "generic_name", "qualified_name"):
                        base = _node_text(bc, self.source).split("<")[0].strip()
                        if base:
                            is_interface = len(base) > 1 and base[0] == "I" and base[1].isupper()
                            kind = EdgeKind.IMPLEMENTS if is_interface else EdgeKind.INHERITS
                            self.edges.append(Edge(kind, sym_id, base, node.start_point[0] + 1))

        # Process body
        body = None
        for child in node.children:
            if child.type == "declaration_list":
                body = child
                break
        if body:
            self._symbol_stack.append(sym_id)
            for child in body.children:
                if child.type == "method_declaration":
                    self._handle_csharp_method(child, sym_id, name)
                elif child.type == "constructor_declaration":
                    self._handle_csharp_constructor(child, sym_id, name)
                elif child.type == "property_declaration":
                    self._handle_csharp_property(child, sym_id, name)
                elif child.type == "class_declaration":
                    self._handle_csharp_class(child)
                elif child.type == "interface_declaration":
                    self._handle_csharp_interface(child)
                elif child.type == "struct_declaration":
                    self._handle_csharp_struct(child)
            self._symbol_stack.pop()

    def _handle_csharp_method(self, node: Node, class_id: str, class_name: str) -> None:
        name_node = None
        for c in node.children:
            if c.type == "identifier":
                name_node = c
                break
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        qualified = f"{class_name}.{name}"
        sym_id = f"{self.file_path}::{qualified}"
        exported = self._has_modifier(node, "public")

        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=qualified,
            kind=SymbolKind.METHOD, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=_extract_signature(node, self.source, self.language),
            doc=doc, parent_id=class_id, exported=exported,
            byte_size=node.end_byte - node.start_byte,
            complexity=self._compute_complexity(node),
        )
        self.symbols.append(sym)
        self.edges.append(Edge(EdgeKind.CONTAINS, class_id, sym_id))
        body = node.child_by_field_name("body")
        if body:
            self._scan_calls(body, sym_id)

    def _handle_csharp_constructor(self, node: Node, class_id: str, class_name: str) -> None:
        line = node.start_point[0] + 1
        params = node.child_by_field_name("parameters")
        nparams = len([c for c in (params.children if params else []) if c.type == "parameter"]) if params else 0
        qualified = f"{class_name}.{class_name}"
        sym_id = f"{self.file_path}::{qualified}/{nparams}@{line}"
        sym = Symbol(
            id=sym_id, name=class_name, qualified_name=qualified,
            kind=SymbolKind.METHOD, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=_extract_signature(node, self.source, self.language),
            parent_id=class_id, exported=True,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)
        self.edges.append(Edge(EdgeKind.CONTAINS, class_id, sym_id))
        body = node.child_by_field_name("body")
        if not body:
            for c in node.children:
                if c.type == "block":
                    body = c
                    break
        if body:
            self._scan_calls(body, sym_id)

    def _handle_csharp_property(self, node: Node, class_id: str, class_name: str) -> None:
        name_node = None
        for c in node.children:
            if c.type == "identifier":
                name_node = c
                break
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        qualified = f"{class_name}.{name}"
        sym_id = f"{self.file_path}::{qualified}"
        sym = Symbol(
            id=sym_id, name=name, qualified_name=qualified,
            kind=SymbolKind.PROPERTY, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            parent_id=class_id,
            exported=self._has_modifier(node, "public"),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)
        self.edges.append(Edge(EdgeKind.CONTAINS, class_id, sym_id))
        # Scan accessor bodies for calls (e.g. get { return cache.Load(); })
        for child in node.children:
            if child.type == "accessor_list":
                self._scan_calls(child, sym_id)

    def _handle_csharp_interface(self, node: Node) -> None:
        name_node = None
        for c in node.children:
            if c.type == "identifier":
                name_node = c
                break
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.INTERFACE, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=self._has_modifier(node, "public"),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)
        body = None
        for c in node.children:
            if c.type == "declaration_list":
                body = c
                break
        if body:
            self._symbol_stack.append(sym_id)
            for child in body.children:
                if child.type == "method_declaration":
                    self._handle_csharp_method(child, sym_id, name)
            self._symbol_stack.pop()

    def _handle_csharp_enum(self, node: Node) -> None:
        name_node = None
        for c in node.children:
            if c.type == "identifier":
                name_node = c
                break
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.ENUM, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=self._has_modifier(node, "public"),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    def _handle_csharp_struct(self, node: Node) -> None:
        name_node = None
        for c in node.children:
            if c.type == "identifier":
                name_node = c
                break
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.STRUCT, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=self._has_modifier(node, "public"),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    # ── Ruby ──────────────────────────────────────────────

    def _handle_ruby(self, node: Node) -> None:
        for child in node.children:
            t = child.type
            if t == "call" and child.children:
                first = child.children[0]
                if first.type == "identifier":
                    name = _node_text(first, self.source)
                    if name in ("require", "require_relative"):
                        self.imports.append(_node_text(child, self.source).strip())
                        continue
            if t == "class":
                self._handle_ruby_class(child)
            elif t == "module":
                self._handle_ruby_module(child)
            elif t in ("method", "singleton_method"):
                self._handle_ruby_method(child, None, "")
            elif t == "body_statement":
                self._handle_ruby(child)

    def _handle_ruby_class(self, node: Node) -> None:
        name_node = None
        superclass = None
        for child in node.children:
            if child.type == "constant":
                name_node = child
            elif child.type == "superclass":
                for sc in child.children:
                    if sc.type in ("constant", "scope_resolution"):
                        superclass = _node_text(sc, self.source)
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.CLASS, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=True,
            byte_size=node.end_byte - node.start_byte,
            complexity=self._compute_complexity(node),
        )
        self.symbols.append(sym)
        if superclass:
            self.edges.append(Edge(EdgeKind.INHERITS, sym_id, superclass, node.start_point[0] + 1))

        # Process body
        self._symbol_stack.append(sym_id)
        for child in node.children:
            if child.type == "body_statement":
                for sub in child.children:
                    if sub.type == "method":
                        self._handle_ruby_method(sub, sym_id, name)
                    elif sub.type == "singleton_method":
                        self._handle_ruby_method(sub, sym_id, name)
                    elif sub.type == "class":
                        self._handle_ruby_class(sub)
                    elif sub.type == "module":
                        self._handle_ruby_module(sub)
        self._symbol_stack.pop()

    def _handle_ruby_module(self, node: Node) -> None:
        name_node = None
        for child in node.children:
            if child.type == "constant":
                name_node = child
                break
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.CLASS, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=True,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

        self._symbol_stack.append(sym_id)
        for child in node.children:
            if child.type == "body_statement":
                for sub in child.children:
                    if sub.type in ("method", "singleton_method"):
                        self._handle_ruby_method(sub, sym_id, name)
                    elif sub.type == "class":
                        self._handle_ruby_class(sub)
                    elif sub.type == "module":
                        self._handle_ruby_module(sub)
        self._symbol_stack.pop()

    def _handle_ruby_method(self, node: Node, class_id: str | None, class_name: str) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            for c in node.children:
                if c.type == "identifier":
                    name_node = c
                    break
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if class_name:
            qualified = f"{class_name}.{name}"
            sym_id = f"{self.file_path}::{qualified}"
        else:
            qualified = name
            sym_id = self._make_id(name)

        sym = Symbol(
            id=sym_id, name=name, qualified_name=qualified,
            kind=SymbolKind.METHOD if class_id else SymbolKind.FUNCTION,
            language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=_extract_signature(node, self.source, self.language),
            parent_id=class_id,
            exported=not name.startswith("_"),
            byte_size=node.end_byte - node.start_byte,
            complexity=self._compute_complexity(node),
        )
        self.symbols.append(sym)
        if class_id:
            self.edges.append(Edge(EdgeKind.CONTAINS, class_id, sym_id))
        body = node.child_by_field_name("body")
        if body:
            self._scan_calls(body, sym_id)

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
                    self._scan_calls(node, sym_id)
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
                                    method_sym = Symbol(
                                        id=method_id, name=method_name,
                                        qualified_name=f"{name}.{method_name}",
                                        kind=SymbolKind.METHOD, language=self.language,
                                        file_path=self.file_path,
                                        line_start=child.start_point[0] + 1,
                                        line_end=child.end_point[0] + 1,
                                        signature=method_sig, parent_id=sym_id,
                                        exported=True,
                                        complexity=self._compute_complexity(child),
                                        byte_size=child.end_byte - child.start_byte,
                                    )
                                    self.symbols.append(method_sym)
                                    self.edges.append(Edge(EdgeKind.CONTAINS, sym_id, method_id))
                                    self._scan_calls(child, method_id)

        # Recurse into children — but skip body of classes (already handled methods above)
        if node.type in self._GENERIC_CLASS_TYPES:
            return  # class body already processed, don't double-count methods
        for child in node.children:
            self._handle_generic(child)

    # ── Shared helpers ──────────────────────────────────────

    # Built-in names that should never resolve to user-defined symbols
    _BUILTIN_IGNORE = frozenset({
        # JS/TS built-ins
        "log", "warn", "error", "info", "debug", "trace", "dir", "table",
        "stringify", "parse", "keys", "values", "entries", "assign", "freeze",
        "from", "of", "isArray", "map", "filter", "reduce", "forEach", "find",
        "findIndex", "some", "every", "includes", "indexOf", "join", "slice",
        "splice", "push", "pop", "shift", "unshift", "sort", "reverse", "flat",
        "flatMap", "fill", "concat", "toString", "valueOf",
        "min", "max", "floor", "ceil", "round", "abs", "sqrt", "pow", "random",
        "now", "resolve", "reject", "all", "allSettled", "race", "any",
        "then", "catch", "finally",
        "createElement", "createRef", "createContext", "forwardRef", "memo",
        "get", "set", "has", "delete", "clear", "add", "size",
        "match", "replace", "replaceAll", "split", "trim", "trimStart", "trimEnd",
        "startsWith", "endsWith", "padStart", "padEnd", "repeat", "charAt",
        "toLowerCase", "toUpperCase", "toFixed", "parseInt", "parseFloat",
        "setTimeout", "setInterval", "clearTimeout", "clearInterval",
        "requestAnimationFrame", "cancelAnimationFrame",
        "addEventListener", "removeEventListener", "preventDefault", "stopPropagation",
        "querySelector", "querySelectorAll", "getElementById", "getAttribute",
        "setAttribute", "removeAttribute", "appendChild", "removeChild",
        "insertBefore", "cloneNode", "contains",
        "focus", "blur", "click", "scroll", "scrollTo", "scrollIntoView",
        "open", "close", "write", "read", "abort",
        "fetch", "json", "text", "blob", "arrayBuffer", "formData",
        "encode", "decode", "atob", "btoa",
        # Python built-ins
        "print", "len", "range", "enumerate", "zip", "isinstance", "type",
        "str", "int", "float", "bool", "list", "dict", "tuple", "set",
        "sorted", "reversed", "any", "all", "sum", "min", "max",
        "getattr", "setattr", "hasattr", "delattr", "super", "property",
        "staticmethod", "classmethod", "abstractmethod",
        "append", "extend", "update", "copy", "deepcopy", "items",
        # Rust std
        "unwrap", "expect", "ok", "err", "map", "and_then", "or_else",
        "collect", "iter", "into_iter", "chain", "enumerate", "zip",
        "clone", "to_string", "to_owned", "as_ref", "as_mut",
        "push", "pop", "insert", "remove", "contains", "len", "is_empty",
        "format", "println", "eprintln", "dbg", "vec",
    })

    _BRANCH_TYPES = frozenset({
        "if_statement", "elif_clause", "else_clause",
        "for_statement", "for_in_statement", "while_statement",
        "switch_case", "catch_clause", "ternary_expression",
        "conditional_expression", "binary_expression",
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
                    if is_qualified or bare not in self._BUILTIN_IGNORE:
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
