"""Dart language handler mixin for FileParser."""
from __future__ import annotations

from tree_sitter import Node

from ..types import Edge, EdgeKind, Symbol, SymbolKind
from ._utils import _node_text, _first_comment_above, _extract_signature


class DartHandlerMixin:
    """Mixin providing Dart-specific parsing methods for FileParser."""

    # ── Dart entry point ─────────────────────────────────────

    def _handle_dart(self, node: Node) -> None:
        children = node.children
        i = 0
        while i < len(children):
            child = children[i]
            t = child.type

            if t == "import_or_export":
                self.imports.append(_node_text(child, self.source).strip())
            elif t == "class_definition":
                self._handle_dart_class(child)
            elif t == "enum_declaration":
                self._handle_dart_enum(child)
            elif t == "mixin_declaration":
                self._handle_dart_mixin(child)
            elif t == "extension_declaration":
                self._handle_dart_extension(child)
            elif t == "type_alias":
                self._handle_dart_type_alias(child)
            elif t == "function_signature":
                # Top-level function: signature node followed by function_body
                body = children[i + 1] if i + 1 < len(children) and children[i + 1].type == "function_body" else None
                self._handle_dart_top_function(child, body)
                if body:
                    i += 1  # skip body node
            elif t in ("const_builtin", "final_builtin"):
                # const/final top-level variable: keyword followed by static_final_declaration_list
                kind_kw = t
                if i + 1 < len(children) and children[i + 1].type == "static_final_declaration_list":
                    i += 1
                    self._handle_dart_top_var(children[i], const=(kind_kw == "const_builtin"))
            elif t == "static_final_declaration_list":
                # Standalone (shouldn't happen normally but handle defensively)
                self._handle_dart_top_var(child, const=False)
            elif t in ("inferred_type", "type_identifier"):
                # var/typed top-level variable: type node followed by initialized_identifier_list
                if i + 1 < len(children) and children[i + 1].type == "initialized_identifier_list":
                    i += 1
                    self._handle_dart_top_var_initialized(children[i])

            i += 1

    # ── Top-level function ───────────────────────────────────

    def _handle_dart_top_function(self, sig_node: Node, body_node: Node | None) -> None:
        name_node = self._dart_find_child(sig_node, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if not name:
            return
        sym_id = self._make_id(name)
        exported = not name.startswith("_")
        doc = _first_comment_above(sig_node, self.source)
        end_node = body_node if body_node else sig_node
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.FUNCTION, language=self.language,
            file_path=self.file_path,
            line_start=sig_node.start_point[0] + 1,
            line_end=end_node.end_point[0] + 1,
            signature=_extract_signature(sig_node, self.source, self.language),
            doc=doc,
            exported=exported,
            parent_id=self._current_parent_id(),
            byte_size=end_node.end_byte - sig_node.start_byte,
            complexity=self._compute_complexity(body_node) if body_node else 1,
        )
        self.symbols.append(sym)
        if body_node:
            self._scan_calls(body_node, sym_id)

    # ── Class ────────────────────────────────────────────────

    def _handle_dart_class(self, node: Node) -> None:
        name_node = self._dart_find_child(node, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if not name:
            return

        sym_id = self._make_id(name)
        exported = not name.startswith("_")
        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.CLASS, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=name,
            doc=doc,
            exported=exported,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
            complexity=self._compute_complexity(node),
        )
        self.symbols.append(sym)

        # Inheritance edges
        for child in node.children:
            if child.type == "superclass":
                type_id = self._dart_find_child(child, "type_identifier")
                if type_id:
                    parent_name = _node_text(type_id, self.source)
                    if parent_name:
                        self.edges.append(Edge(EdgeKind.INHERITS, sym_id, f"_ext_::{parent_name}"))
            elif child.type == "interfaces":
                for sub in child.children:
                    if sub.type == "type_identifier":
                        iface_name = _node_text(sub, self.source)
                        if iface_name:
                            self.edges.append(Edge(EdgeKind.IMPLEMENTS, sym_id, f"_ext_::{iface_name}"))

        # Walk class body
        body = self._dart_find_child(node, "class_body")
        if body:
            self._dart_walk_class_body(body, sym_id)

    # ── Enum ─────────────────────────────────────────────────

    def _handle_dart_enum(self, node: Node) -> None:
        name_node = self._dart_find_child(node, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if not name:
            return
        sym_id = self._make_id(name)
        exported = not name.startswith("_")
        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.ENUM, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=name,
            doc=doc,
            exported=exported,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    # ── Mixin ────────────────────────────────────────────────

    def _handle_dart_mixin(self, node: Node) -> None:
        name_node = self._dart_find_child(node, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if not name:
            return
        sym_id = self._make_id(name)
        exported = not name.startswith("_")
        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.CLASS, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=name,
            doc=doc,
            exported=exported,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

        body = self._dart_find_child(node, "class_body")
        if body:
            self._dart_walk_class_body(body, sym_id)

    # ── Extension ────────────────────────────────────────────

    def _handle_dart_extension(self, node: Node) -> None:
        name_node = self._dart_find_child(node, "identifier")
        if name_node:
            name = _node_text(name_node, self.source)
        else:
            # Anonymous extension — use the extended type
            type_node = self._dart_find_child(node, "type_identifier")
            name = f"extension on {_node_text(type_node, self.source)}" if type_node else "extension"

        if not name:
            return
        sym_id = self._make_id(name)
        exported = not name.startswith("_")
        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.CLASS, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=name,
            doc=doc,
            exported=exported,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

        body = self._dart_find_child(node, "extension_body")
        if body:
            self._dart_walk_class_body(body, sym_id)

    # ── Type alias ───────────────────────────────────────────

    def _handle_dart_type_alias(self, node: Node) -> None:
        name_node = self._dart_find_child(node, "type_identifier")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if not name:
            return
        sym_id = self._make_id(name)
        exported = not name.startswith("_")
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.TYPE_ALIAS, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=_node_text(node, self.source).strip().rstrip(";"),
            exported=exported,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    # ── Top-level variables ──────────────────────────────────

    def _handle_dart_top_var(self, decl_list: Node, *, const: bool) -> None:
        for child in decl_list.children:
            if child.type == "static_final_declaration":
                name_node = self._dart_find_child(child, "identifier")
                if not name_node:
                    continue
                name = _node_text(name_node, self.source)
                if not name:
                    continue
                sym_id = self._make_id(name)
                exported = not name.startswith("_")
                kind = SymbolKind.CONSTANT if const else SymbolKind.VARIABLE
                doc = _first_comment_above(decl_list, self.source)
                sym = Symbol(
                    id=sym_id, name=name, qualified_name=name,
                    kind=kind, language=self.language,
                    file_path=self.file_path,
                    line_start=child.start_point[0] + 1,
                    line_end=child.end_point[0] + 1,
                    signature=name,
                    doc=doc,
                    exported=exported,
                    parent_id=self._current_parent_id(),
                    byte_size=child.end_byte - child.start_byte,
                )
                self.symbols.append(sym)

    def _handle_dart_top_var_initialized(self, init_list: Node) -> None:
        for child in init_list.children:
            if child.type == "initialized_identifier":
                name_node = self._dart_find_child(child, "identifier")
                if not name_node:
                    continue
                name = _node_text(name_node, self.source)
                if not name:
                    continue
                sym_id = self._make_id(name)
                exported = not name.startswith("_")
                doc = _first_comment_above(init_list, self.source)
                sym = Symbol(
                    id=sym_id, name=name, qualified_name=name,
                    kind=SymbolKind.VARIABLE, language=self.language,
                    file_path=self.file_path,
                    line_start=child.start_point[0] + 1,
                    line_end=child.end_point[0] + 1,
                    signature=name,
                    doc=doc,
                    exported=exported,
                    parent_id=self._current_parent_id(),
                    byte_size=child.end_byte - child.start_byte,
                )
                self.symbols.append(sym)

    # ── Class body walking ───────────────────────────────────

    def _dart_walk_class_body(self, body: Node, parent_id: str) -> None:
        self._symbol_stack.append(parent_id)
        children = body.children
        i = 0
        while i < len(children):
            child = children[i]
            t = child.type

            if t == "method_signature":
                # Method: signature followed by function_body
                body_node = children[i + 1] if i + 1 < len(children) and children[i + 1].type == "function_body" else None
                self._handle_dart_method(child, body_node)
                if body_node:
                    i += 1
            elif t == "declaration":
                # Could be a constructor_signature inside
                self._handle_dart_declaration(child)

            i += 1
        self._symbol_stack.pop()

    def _handle_dart_method(self, sig_node: Node, body_node: Node | None) -> None:
        name = self._dart_extract_method_name(sig_node)
        if not name:
            return
        sym_id = self._make_id(name)
        exported = not name.startswith("_")
        doc = _first_comment_above(sig_node, self.source)
        end_node = body_node if body_node else sig_node
        sym = Symbol(
            id=sym_id, name=name,
            qualified_name=f"{self._parent_qualified_name()}.{name}" if self._symbol_stack else name,
            kind=SymbolKind.METHOD, language=self.language,
            file_path=self.file_path,
            line_start=sig_node.start_point[0] + 1,
            line_end=end_node.end_point[0] + 1,
            signature=_extract_signature(sig_node, self.source, self.language),
            doc=doc,
            exported=exported,
            parent_id=self._current_parent_id(),
            byte_size=end_node.end_byte - sig_node.start_byte,
            complexity=self._compute_complexity(body_node) if body_node else 1,
        )
        self.symbols.append(sym)
        if self._current_parent_id():
            self.edges.append(Edge(EdgeKind.CONTAINS, self._current_parent_id(), sym_id))
        if body_node:
            self._scan_calls(body_node, sym_id)

    def _handle_dart_declaration(self, node: Node) -> None:
        for child in node.children:
            if child.type == "constructor_signature":
                self._handle_dart_constructor(child)
                return

    def _handle_dart_constructor(self, node: Node) -> None:
        text = _node_text(node, self.source)
        # Constructor name is the class name, possibly with .namedPart
        # e.g. "MyClass(this._value)" or "MyClass.named(int v)"
        # Extract from first identifier child
        parts = []
        for child in node.children:
            if child.type == "identifier":
                parts.append(_node_text(child, self.source))
            elif child.type == ".":
                pass  # separator between ClassName.namedPart
        if not parts:
            return
        name = ".".join(parts) if len(parts) > 1 else parts[0]
        sym_id = self._make_id(name)
        exported = not name.startswith("_")
        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name,
            qualified_name=f"{self._parent_qualified_name()}.{name}" if self._symbol_stack else name,
            kind=SymbolKind.METHOD, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=_extract_signature(node, self.source, self.language),
            doc=doc,
            exported=exported,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)
        if self._current_parent_id():
            self.edges.append(Edge(EdgeKind.CONTAINS, self._current_parent_id(), sym_id))

    # ── Helpers ──────────────────────────────────────────────

    def _dart_extract_method_name(self, sig_node: Node) -> str | None:
        for child in sig_node.children:
            if child.type == "function_signature":
                name_node = self._dart_find_child(child, "identifier")
                if name_node:
                    return _node_text(name_node, self.source)
            elif child.type == "factory_constructor_signature":
                # factory ClassName.name(...) — extract identifier after '.'
                parts = []
                for sub in child.children:
                    if sub.type == "identifier":
                        parts.append(_node_text(sub, self.source))
                if parts:
                    return ".".join(parts) if len(parts) > 1 else parts[0]
            elif child.type == "getter_signature":
                name_node = self._dart_find_child(child, "identifier")
                if name_node:
                    return _node_text(name_node, self.source)
            elif child.type == "setter_signature":
                name_node = self._dart_find_child(child, "identifier")
                if name_node:
                    return _node_text(name_node, self.source)
        return None

    @staticmethod
    def _dart_find_child(node: Node, node_type: str) -> Node | None:
        for child in node.children:
            if child.type == node_type:
                return child
        return None
