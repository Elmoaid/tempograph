"""Julia language handler mixin for FileParser."""
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


def _sig_name(sig_node: "Node", source: bytes) -> str | None:
    """Extract function/macro name from a signature node."""
    call = _first_child_of_type(sig_node, "call_expression")
    if call:
        for child in call.children:
            if child.type == "identifier":
                return _node_text(child, source)
    return None


class JuliaHandlerMixin:
    """Julia parser: extracts modules, functions, macros, structs, and imports."""

    def _handle_julia(self, node: "Node") -> None:
        """Entry point — process the source_file root node."""
        self._julia_exported: set[str] = set()
        # First pass: collect exported names from export_statement nodes
        self._julia_collect_exports(node)
        # Second pass: extract symbols from top-level children
        for child in node.children:
            self._julia_visit(child, parent_id=None)

    def _julia_collect_exports(self, root: "Node") -> None:
        """Walk all nodes collecting names from export_statement."""
        for child in root.children:
            if child.type == "export_statement":
                for n in child.children:
                    if n.type == "identifier":
                        self._julia_exported.add(_node_text(n, self.source))
            elif child.type in ("module_definition",):
                block = _first_child_of_type(child, "block")
                if block:
                    self._julia_collect_exports(block)

    def _julia_visit(self, node: "Node", parent_id: str | None) -> None:
        t = node.type
        if t == "module_definition":
            self._julia_module(node, parent_id)
        elif t == "function_definition":
            self._julia_function(node, parent_id)
        elif t == "macro_definition":
            self._julia_macro(node, parent_id)
        elif t == "struct_definition":
            self._julia_struct(node, parent_id)
        elif t == "abstract_definition":
            self._julia_abstract(node, parent_id)
        elif t == "assignment":
            self._julia_short_fn(node, parent_id)
        elif t == "const_statement":
            self._julia_const(node, parent_id)
        elif t in ("using_statement", "import_statement"):
            self._julia_import(node)

    def _make_sym(self, name: str, qualified: str, kind: SymbolKind,
                  node: "Node", parent_id: str | None, exported: bool) -> Symbol:
        sym_id = f"{self.file_path}::{qualified}"
        return Symbol(
            id=sym_id,
            name=name,
            qualified_name=qualified,
            kind=kind,
            language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=exported,
            parent_id=parent_id,
        )

    def _julia_module(self, node: "Node", parent_id: str | None) -> None:
        name_node = _first_child_of_type(node, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym = self._make_sym(name, name, SymbolKind.MODULE, node, parent_id, exported=True)
        self.symbols.append(sym)
        block = _first_child_of_type(node, "block")
        if block:
            for child in block.children:
                self._julia_visit(child, sym.id)

    def _julia_function(self, node: "Node", parent_id: str | None) -> None:
        sig = _first_child_of_type(node, "signature")
        if not sig:
            return
        name = _sig_name(sig, self.source)
        if not name:
            return
        sym = self._make_sym(name, name, SymbolKind.FUNCTION, node, parent_id,
                             exported=name in self._julia_exported)
        self.symbols.append(sym)

    def _julia_macro(self, node: "Node", parent_id: str | None) -> None:
        sig = _first_child_of_type(node, "signature")
        if not sig:
            return
        name = _sig_name(sig, self.source)
        if not name:
            return
        display = f"@{name}"
        sym = self._make_sym(display, display, SymbolKind.FUNCTION, node, parent_id,
                             exported=name in self._julia_exported)
        self.symbols.append(sym)

    def _julia_struct(self, node: "Node", parent_id: str | None) -> None:
        type_head = _first_child_of_type(node, "type_head")
        if not type_head:
            return
        name_node = _first_child_of_type(type_head, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym = self._make_sym(name, name, SymbolKind.CLASS, node, parent_id,
                             exported=name in self._julia_exported)
        self.symbols.append(sym)

    def _julia_abstract(self, node: "Node", parent_id: str | None) -> None:
        type_head = _first_child_of_type(node, "type_head")
        if not type_head:
            return
        name_node = _first_child_of_type(type_head, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym = self._make_sym(name, name, SymbolKind.INTERFACE, node, parent_id,
                             exported=name in self._julia_exported)
        self.symbols.append(sym)

    def _julia_short_fn(self, node: "Node", parent_id: str | None) -> None:
        """Short-form function: double(x) = x * 2"""
        lhs = node.children[0] if node.children else None
        if not lhs or lhs.type != "call_expression":
            return
        for child in lhs.children:
            if child.type == "identifier":
                name = _node_text(child, self.source)
                # Only treat as function if name starts with lowercase
                if name and name[0].islower():
                    sym = self._make_sym(name, name, SymbolKind.FUNCTION, node, parent_id,
                                         exported=name in self._julia_exported)
                    self.symbols.append(sym)
                break

    def _julia_const(self, node: "Node", parent_id: str | None) -> None:
        assign = _first_child_of_type(node, "assignment")
        if not assign:
            return
        name_node = _first_child_of_type(assign, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym = self._make_sym(name, name, SymbolKind.CONSTANT, node, parent_id,
                             exported=name in self._julia_exported)
        self.symbols.append(sym)

    def _julia_import(self, node: "Node") -> None:
        for child in node.children:
            if child.type == "identifier":
                self.imports.append(f"using {_node_text(child, self.source)}")
                return
            if child.type == "selected_import":
                mod = _first_child_of_type(child, "identifier")
                if mod:
                    self.imports.append(f"import {_node_text(mod, self.source)}")
                    return
