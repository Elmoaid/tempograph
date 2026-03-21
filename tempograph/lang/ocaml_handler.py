"""OCaml language handler mixin for FileParser."""
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


class OCamlHandlerMixin:
    """OCaml parser: extracts modules, functions, types, and opens."""

    def _handle_ocaml(self, node: "Node") -> None:
        """Entry point — process the compilation_unit root node."""
        self._ocaml_walk(node, module_prefix="")

    def _ocaml_walk(self, node: "Node", module_prefix: str) -> None:
        """Walk children of a compilation_unit or structure node."""
        for child in node.children:
            t = child.type
            if t == "module_definition":
                self._ocaml_handle_module(child, module_prefix)
            elif t == "value_definition":
                self._ocaml_handle_value(child, module_prefix)
            elif t == "type_definition":
                self._ocaml_handle_type(child, module_prefix)
            elif t == "open_module":
                self._ocaml_handle_open(child)
            elif t == "structure":
                # Bare structure (shouldn't appear at top level, but handle defensively)
                self._ocaml_walk(child, module_prefix)

    def _ocaml_handle_module(self, node: "Node", parent_prefix: str) -> None:
        """Process `module Name = struct ... end` declarations."""
        binding = _first_child_of_type(node, "module_binding")
        if not binding:
            return
        name_node = _first_child_of_type(binding, "module_name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        qualified = f"{parent_prefix}.{name}" if parent_prefix else name
        sym_id = f"{self.file_path}::{qualified}"

        sym = Symbol(
            id=sym_id, name=name, qualified_name=qualified,
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

        # Recurse into the structure body
        structure = _first_child_of_type(binding, "structure")
        if structure:
            self._symbol_stack.append(sym_id)
            self._ocaml_walk(structure, qualified)
            self._symbol_stack.pop()

    def _ocaml_handle_value(self, node: "Node", module_prefix: str) -> None:
        """Process `let name ... = ...` value/function definitions."""
        binding = _first_child_of_type(node, "let_binding")
        if not binding:
            return
        name_node = _first_child_of_type(binding, "value_name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)

        parent_id = self._current_parent_id()
        if parent_id:
            qualified = f"{module_prefix}.{name}"
            sym_id = f"{self.file_path}::{qualified}"
        else:
            sym_id = f"{self.file_path}::{name}"
            qualified = name

        # Functions have parameters, values do not
        has_params = any(c.type == "parameter" for c in binding.children)
        kind = SymbolKind.FUNCTION if has_params or not parent_id else SymbolKind.FUNCTION
        if parent_id:
            kind = SymbolKind.METHOD

        sym = Symbol(
            id=sym_id, name=name, qualified_name=qualified,
            kind=kind, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=not name.startswith("_"),
            parent_id=parent_id,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)
        if parent_id:
            self.edges.append(Edge(EdgeKind.CONTAINS, parent_id, sym_id, node.start_point[0] + 1))

    def _ocaml_handle_type(self, node: "Node", module_prefix: str) -> None:
        """Process `type name = ...` declarations."""
        binding = _first_child_of_type(node, "type_binding")
        if not binding:
            return
        name_node = _first_child_of_type(binding, "type_constructor")
        if not name_node:
            return
        name = _node_text(name_node, self.source)

        parent_id = self._current_parent_id()
        if parent_id:
            qualified = f"{module_prefix}.{name}"
            sym_id = f"{self.file_path}::{qualified}"
        else:
            sym_id = f"{self.file_path}::{name}"
            qualified = name

        sym = Symbol(
            id=sym_id, name=name, qualified_name=qualified,
            kind=SymbolKind.TYPE_ALIAS, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=not name.startswith("_"),
            parent_id=parent_id,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)
        if parent_id:
            self.edges.append(Edge(EdgeKind.CONTAINS, parent_id, sym_id, node.start_point[0] + 1))

    def _ocaml_handle_open(self, node: "Node") -> None:
        """Process `open Module` imports."""
        path_node = _first_child_of_type(node, "module_path")
        if not path_node:
            return
        name_node = _first_child_of_type(path_node, "module_name")
        if name_node:
            self.imports.append(_node_text(name_node, self.source))
