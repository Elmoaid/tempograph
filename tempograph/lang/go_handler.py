"""Go language handler mixin for FileParser."""
from __future__ import annotations

from tree_sitter import Node

from ..types import Edge, EdgeKind, Symbol, SymbolKind
from ._utils import _node_text, _first_comment_above, _extract_signature


class GoHandlerMixin:
    """Mixin providing Go-specific parsing methods for FileParser."""

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
            elif t == "const_declaration":
                self._handle_go_const(child)
            elif t == "var_declaration":
                self._handle_go_var(child)

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

    def _handle_go_const(self, node: Node) -> None:
        # const_declaration contains one or more const_spec children.
        # Each const_spec may have multiple identifier children (e.g. const a, b = 1, 2).
        for child in node.children:
            if child.type != "const_spec":
                continue
            for c in child.children:
                if c.type == "identifier":
                    name = _node_text(c, self.source)
                    sym_id = self._make_id(name)
                    sym = Symbol(
                        id=sym_id, name=name, qualified_name=name,
                        kind=SymbolKind.CONSTANT, language=self.language,
                        file_path=self.file_path,
                        line_start=child.start_point[0] + 1,
                        line_end=child.end_point[0] + 1,
                        exported=name[0:1].isupper(),
                        byte_size=child.end_byte - child.start_byte,
                    )
                    self.symbols.append(sym)

    def _handle_go_var(self, node: Node) -> None:
        # var_declaration contains var_spec children (single) or a var_spec_list wrapper.
        for child in node.children:
            if child.type == "var_spec":
                self._emit_go_var_spec(child)
            elif child.type == "var_spec_list":
                for sub in child.children:
                    if sub.type == "var_spec":
                        self._emit_go_var_spec(sub)

    def _emit_go_var_spec(self, node: Node) -> None:
        # var_spec may have multiple identifier children (e.g. var x, y int).
        for c in node.children:
            if c.type == "identifier":
                name = _node_text(c, self.source)
                sym_id = self._make_id(name)
                sym = Symbol(
                    id=sym_id, name=name, qualified_name=name,
                    kind=SymbolKind.VARIABLE, language=self.language,
                    file_path=self.file_path,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    exported=name[0:1].isupper(),
                    byte_size=node.end_byte - node.start_byte,
                )
                self.symbols.append(sym)
