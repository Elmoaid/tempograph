"""C# language handler mixin for FileParser."""
from __future__ import annotations

from tree_sitter import Node

from ..types import Edge, EdgeKind, Symbol, SymbolKind
from ._utils import _node_text, _first_comment_above, _extract_signature


class CsharpHandlerMixin:
    """Mixin providing C#-specific parsing methods for FileParser."""

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
