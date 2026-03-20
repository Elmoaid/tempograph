"""Java language handler mixin for FileParser."""
from __future__ import annotations

from tree_sitter import Node

from ..types import Edge, EdgeKind, Symbol, SymbolKind
from ._utils import _node_text, _first_comment_above, _extract_signature


class JavaHandlerMixin:
    """Mixin providing Java-specific parsing methods for FileParser."""

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
