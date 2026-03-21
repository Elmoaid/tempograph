"""Erlang language handler mixin for FileParser."""
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


class ErlangHandlerMixin:
    """Erlang parser: extracts functions, records, and imports."""

    def _handle_erlang(self, node: "Node") -> None:
        """Entry point — process the source_file root node."""
        # Pass 1: collect exported function names from -export([...]) attributes
        exported_names: set[str] = set()
        for child in node.children:
            if child.type == "export_attribute":
                self._erlang_collect_exports(child, exported_names)

        # Pass 2: extract symbols
        seen_fns: set[str] = set()  # deduplicate multi-clause functions
        for child in node.children:
            t = child.type
            if t == "fun_decl":
                self._erlang_handle_fun(child, seen_fns, exported_names)
            elif t == "record_decl":
                self._erlang_handle_record(child)
            elif t == "import_attribute":
                self._erlang_handle_import(child)

    def _erlang_collect_exports(self, node: "Node", exported_names: set[str]) -> None:
        """Collect function names from -export([name/arity, ...]) attribute."""
        for child in node.children:
            if child.type == "fa":
                name_node = _first_child_of_type(child, "atom")
                if name_node:
                    exported_names.add(_node_text(name_node, self.source))

    def _erlang_handle_fun(
        self, node: "Node", seen: set[str], exported_names: set[str]
    ) -> None:
        """Process a fun_decl — extract function name from first clause."""
        clause = _first_child_of_type(node, "function_clause")
        if not clause:
            return
        name_node = _first_child_of_type(clause, "atom")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if name in seen:
            return  # multi-clause function — only emit the first occurrence
        seen.add(name)

        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id,
            name=name,
            qualified_name=name,
            kind=SymbolKind.FUNCTION,
            language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=name in exported_names,
            parent_id=None,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    def _erlang_handle_record(self, node: "Node") -> None:
        """Process -record(Name, {...}) declarations → TYPE_ALIAS."""
        # Structure: '-' 'record' '(' atom ',' '{' fields '}' ')' '.'
        name_node = None
        found_record_kw = False
        for child in node.children:
            if child.type == "record" or (child.type in ("atom",) and _node_text(child, self.source) == "record"):
                found_record_kw = True
            elif found_record_kw and child.type == "atom" and name_node is None:
                name_node = child
        if not name_node:
            # Fallback: first atom that isn't 'record'
            for child in node.children:
                if child.type == "atom":
                    text = _node_text(child, self.source)
                    if text != "record":
                        name_node = child
                        break
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id,
            name=name,
            qualified_name=name,
            kind=SymbolKind.TYPE_ALIAS,
            language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=True,
            parent_id=None,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    def _erlang_handle_import(self, node: "Node") -> None:
        """Process -import(Module, [fn/arity, ...]) → record import of Module."""
        # First atom child is the module name
        module_node = None
        for child in node.children:
            if child.type == "atom":
                module_node = child
                break
        if module_node:
            module_name = _node_text(module_node, self.source)
            self.imports.append(module_name)
