"""F# language handler mixin for FileParser."""
from __future__ import annotations

from ..types import Edge, EdgeKind, Symbol, SymbolKind

try:
    from tree_sitter import Node
except ImportError:
    Node = object  # type: ignore[assignment,misc]


def _node_text(node: "Node", source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _first_child_of_type(node: "Node", *types: str) -> "Node | None":
    for child in node.children:
        if child.type in types:
            return child
    return None


def _is_private(node: "Node", source: bytes) -> bool:
    """Return True if the declaration has a private/internal access modifier."""
    decl_left = _first_child_of_type(node, "function_declaration_left", "value_declaration_left")
    if not decl_left:
        return False
    for child in decl_left.children:
        if child.type == "access_modifier":
            text = _node_text(child, source).strip()
            if text in ("private", "internal"):
                return True
    return False


def _get_fn_name(node: "Node", source: bytes) -> str | None:
    """Return the identifier from function_declaration_left or value_declaration_left."""
    decl_left = _first_child_of_type(node, "function_declaration_left", "value_declaration_left")
    if not decl_left:
        return None
    # function_declaration_left: direct identifier child
    for child in decl_left.children:
        if child.type == "identifier":
            return _node_text(child, source)
    # value_declaration_left: identifier_pattern → long_identifier_or_op → identifier
    for child in decl_left.children:
        if child.type == "identifier_pattern":
            for sub in child.children:
                if sub.type == "long_identifier_or_op":
                    for inner in sub.children:
                        if inner.type == "identifier":
                            return _node_text(inner, source)
                elif sub.type == "identifier":
                    return _node_text(sub, source)
    return None


def _has_params(node: "Node") -> bool:
    """True if function_declaration_left has argument_patterns (real function, not a value)."""
    decl_left = _first_child_of_type(node, "function_declaration_left")
    if not decl_left:
        return False
    return any(c.type == "argument_patterns" for c in decl_left.children)


class FSharpHandlerMixin:
    """F# parser: extracts modules, functions/values, types, and opens."""

    def _handle_fsharp(self, node: "Node") -> None:
        """Entry point — process the file root node."""
        self._fsharp_walk(node)

    def _fsharp_walk(self, node: "Node") -> None:
        """Walk children of a file, named_module, namespace, or module_defn."""
        for child in node.children:
            t = child.type
            if t in ("named_module", "namespace"):
                # Recurse into top-level container (namespace/named_module body)
                self._fsharp_walk(child)
            elif t == "module_defn":
                self._fsharp_handle_module(child)
            elif t == "declaration_expression":
                self._fsharp_handle_declaration(child)
            elif t == "type_definition":
                self._fsharp_handle_type(child)
            elif t == "import_decl":
                self._fsharp_handle_open(child)
            elif t == "ERROR":
                # The F# grammar produces ERROR nodes for file-level let/type/open decls
                # in the "file module" style (module M\nlet ...). Extract them gracefully.
                self._fsharp_walk_error(child)

    def _fsharp_walk_error(self, node: "Node") -> None:
        """Extract declarations from ERROR nodes (file-level module style)."""
        # Check if this ERROR node itself looks like a function/value decl
        decl_left = _first_child_of_type(node, "function_declaration_left", "value_declaration_left")
        if decl_left:
            self._fsharp_handle_fn(node)
            return
        # Otherwise recurse into children looking for declaration_expression / type_definition
        for child in node.children:
            if child.type == "declaration_expression":
                self._fsharp_handle_declaration(child)
            elif child.type == "type_definition":
                self._fsharp_handle_type(child)
            elif child.type == "import_decl":
                self._fsharp_handle_open(child)
            elif child.type == "function_or_value_defn":
                self._fsharp_handle_fn(child)

    def _fsharp_handle_module(self, node: "Node") -> None:
        """Process `module Name = ...` nested module declarations."""
        name_node = _first_child_of_type(node, "identifier")
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
            exported=not name.startswith("_"),
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

        if self._current_parent_id():
            self.edges.append(Edge(EdgeKind.CONTAINS, self._current_parent_id(), sym_id, node.start_point[0] + 1))

        # Recurse into module body
        self._symbol_stack.append(sym_id)
        self._fsharp_walk(node)
        self._symbol_stack.pop()

    def _fsharp_handle_declaration(self, node: "Node") -> None:
        """Process declaration_expression — may contain function/value or other decls."""
        fn_node = _first_child_of_type(node, "function_or_value_defn")
        if fn_node:
            self._fsharp_handle_fn(fn_node)

    def _fsharp_handle_fn(self, node: "Node") -> None:
        """Process `let [private] name [params] = body` definitions."""
        name = _get_fn_name(node, self.source)
        if not name:
            return
        parent_id = self._current_parent_id()
        sym_id = self._make_id(name)
        private = _is_private(node, self.source)
        kind = SymbolKind.METHOD if parent_id else SymbolKind.FUNCTION

        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=kind, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=not private and not name.startswith("_"),
            parent_id=parent_id,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)
        if parent_id:
            self.edges.append(Edge(EdgeKind.CONTAINS, parent_id, sym_id, node.start_point[0] + 1))

    def _fsharp_handle_type(self, node: "Node") -> None:
        """Process `type Name = ...` declarations (union, record, alias, interface)."""
        # type_definition children: type_name comes from union_type_defn, record_type_defn, anon_type_defn
        for child in node.children:
            if child.type in ("union_type_defn", "record_type_defn", "anon_type_defn",
                              "abbrev_type_defn", "type_extension"):
                name_node = _first_child_of_type(child, "type_name")
                if name_node:
                    ident = _first_child_of_type(name_node, "identifier")
                    if ident:
                        name = _node_text(ident, self.source)
                        sym_id = self._make_id(name)
                        kind = SymbolKind.INTERFACE if child.type == "anon_type_defn" else SymbolKind.TYPE_ALIAS
                        # Check for class-like (has primary_constr_args) → treat as CLASS
                        if child.type == "anon_type_defn":
                            if _first_child_of_type(child, "primary_constr_args"):
                                kind = SymbolKind.CLASS
                        sym = Symbol(
                            id=sym_id, name=name, qualified_name=name,
                            kind=kind, language=self.language,
                            file_path=self.file_path,
                            line_start=node.start_point[0] + 1,
                            line_end=node.end_point[0] + 1,
                            exported=not name.startswith("_"),
                            parent_id=self._current_parent_id(),
                            byte_size=node.end_byte - node.start_byte,
                        )
                        self.symbols.append(sym)
                        if self._current_parent_id():
                            self.edges.append(Edge(
                                EdgeKind.CONTAINS, self._current_parent_id(), sym_id, node.start_point[0] + 1,
                            ))
                        return
        # Fallback: no recognized type structure found

    def _fsharp_handle_open(self, node: "Node") -> None:
        """Process `open Module.Path` imports."""
        path_node = _first_child_of_type(node, "long_identifier")
        if path_node:
            # Take the whole text (e.g. "System.IO") or just the first identifier
            text = _node_text(path_node, self.source).strip()
            if text:
                self.imports.append(text)
