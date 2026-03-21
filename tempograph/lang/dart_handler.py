"""Dart language handler mixin for FileParser."""
from __future__ import annotations

from tree_sitter import Node

from ..types import Edge, EdgeKind, Symbol, SymbolKind
from ._utils import _node_text, _first_comment_above, _extract_signature


class DartHandlerMixin:
    """Mixin providing Dart-specific parsing methods for FileParser."""

    # ── Dart entry point ────────────────────────────────────────

    def _handle_dart(self, node: Node) -> None:
        """Walk the top-level program node."""
        children = list(node.children)
        i = 0
        while i < len(children):
            child = children[i]
            t = child.type
            if t == "import_or_export":
                self._dart_handle_import(child)
            elif t == "function_signature":
                # Top-level function: signature followed by body
                body = children[i + 1] if i + 1 < len(children) and children[i + 1].type == "function_body" else None
                self._handle_dart_function(child, body, is_method=False)
                if body:
                    i += 1  # skip body on next iteration
            elif t in ("class_definition", "mixin_declaration"):
                self._handle_dart_class(child)
            elif t == "enum_declaration":
                self._handle_dart_enum(child)
            i += 1

    # ── Dart imports ────────────────────────────────────────────

    def _dart_handle_import(self, node: Node) -> None:
        """Extract import URI from import_or_export node."""
        for lib in node.children:
            if lib.type != "library_import":
                continue
            for spec in lib.children:
                if spec.type != "import_specification":
                    continue
                for part in spec.children:
                    if part.type == "configurable_uri":
                        for uri_node in part.children:
                            if uri_node.type == "uri":
                                text = _node_text(uri_node, self.source).strip("'\"")
                                if text:
                                    self.imports.append(text)

    # ── Dart visibility ─────────────────────────────────────────

    def _dart_is_private(self, name: str) -> bool:
        """In Dart, names starting with '_' are library-private."""
        return name.startswith("_")

    # ── Dart top-level / method function ────────────────────────

    def _handle_dart_function(
        self,
        sig_node: Node,
        body_node: Node | None,
        *,
        is_method: bool,
    ) -> None:
        """Create a Symbol from a Dart function_signature + optional function_body."""
        # Name: identifier child of function_signature
        name_node = self._dart_first_child_of_type(sig_node, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if not name:
            return

        exported = not self._dart_is_private(name)
        qualified_name = name
        if self._symbol_stack:
            parent_qname = self._parent_qualified_name()
            if parent_qname:
                qualified_name = f"{parent_qname}.{name}"

        kind = SymbolKind.METHOD if is_method else SymbolKind.FUNCTION
        sym_id = self._make_id(name)
        doc = _first_comment_above(sig_node, self.source)

        line_start = sig_node.start_point[0] + 1
        line_end = (body_node.end_point[0] + 1) if body_node else (sig_node.end_point[0] + 1)

        sym = Symbol(
            id=sym_id, name=name,
            qualified_name=qualified_name,
            kind=kind, language=self.language,
            file_path=self.file_path,
            line_start=line_start,
            line_end=line_end,
            signature=_extract_signature(sig_node, self.source, self.language),
            doc=doc,
            exported=exported,
            parent_id=self._current_parent_id(),
            byte_size=(body_node.end_byte if body_node else sig_node.end_byte) - sig_node.start_byte,
            complexity=self._compute_complexity(body_node or sig_node),
        )
        self.symbols.append(sym)
        if self._current_parent_id():
            self.edges.append(Edge(EdgeKind.CONTAINS, self._current_parent_id(), sym_id))
        if body_node:
            self._scan_calls(body_node, sym_id)

    # ── Dart class / mixin ──────────────────────────────────────

    def _handle_dart_class(self, node: Node) -> None:
        """Handle class_definition or mixin_declaration."""
        is_mixin = node.type == "mixin_declaration"
        is_abstract = any(
            c.type == "abstract" for c in node.children
        )

        # Name node
        name_node = self._dart_first_child_of_type(node, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if not name:
            return

        exported = not self._dart_is_private(name)
        kind = SymbolKind.CLASS
        sym_id = self._make_id(name)
        doc = _first_comment_above(node, self.source)

        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=kind, language=self.language,
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

        # Superclass: class_definition → superclass → type_identifier
        for child in node.children:
            if child.type == "superclass":
                type_id = self._dart_first_child_of_type(child, "type_identifier")
                if type_id:
                    self.edges.append(Edge(
                        EdgeKind.INHERITS, sym_id,
                        _node_text(type_id, self.source),
                        node.start_point[0] + 1,
                    ))
            elif child.type == "interfaces":
                for c in child.children:
                    if c.type == "type_identifier":
                        self.edges.append(Edge(
                            EdgeKind.IMPLEMENTS, sym_id,
                            _node_text(c, self.source),
                            node.start_point[0] + 1,
                        ))

        # Walk class_body for methods
        body = self._dart_first_child_of_type(node, "class_body")
        if body:
            self._symbol_stack.append(sym_id)
            self._dart_walk_class_body(body)
            self._symbol_stack.pop()

    def _dart_walk_class_body(self, body: Node) -> None:
        """Walk a class_body, extracting methods."""
        children = list(body.children)
        i = 0
        while i < len(children):
            child = children[i]
            if child.type == "method_signature":
                # method_signature → function_signature → identifier
                func_sig = self._dart_first_child_of_type(child, "function_signature")
                if func_sig:
                    body_node = (
                        children[i + 1]
                        if i + 1 < len(children) and children[i + 1].type == "function_body"
                        else None
                    )
                    self._handle_dart_function(func_sig, body_node, is_method=True)
                    if body_node:
                        i += 1
            i += 1

    # ── Dart enum ───────────────────────────────────────────────

    def _handle_dart_enum(self, node: Node) -> None:
        """Handle enum_declaration."""
        name_node = self._dart_first_child_of_type(node, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if not name:
            return

        exported = not self._dart_is_private(name)
        sym_id = self._make_id(name)
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
            complexity=0,
        )
        self.symbols.append(sym)

    # ── Dart utility ────────────────────────────────────────────

    def _dart_first_child_of_type(self, node: Node, type_name: str) -> Node | None:
        """Return the first direct child with the given type, or None."""
        for child in node.children:
            if child.type == type_name:
                return child
        return None
