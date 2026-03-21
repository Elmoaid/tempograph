"""Lua language handler mixin for FileParser."""
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


def _extract_fn_name(name_node: "Node", source: bytes) -> tuple[str, str]:
    """Return (bare_name, qualified_name) from a function name node.

    Handles:
    - identifier         → ("add", "add")
    - dot_index_expression   M.func  → ("func", "M.func")
    - method_index_expression  Animal:speak → ("speak", "Animal:speak")
    """
    t = name_node.type
    if t == "identifier":
        n = _node_text(name_node, source)
        return n, n
    if t in ("dot_index_expression", "method_index_expression"):
        # last identifier child is the method name
        last_id = None
        for child in name_node.children:
            if child.type == "identifier":
                last_id = child
        if last_id:
            return _node_text(last_id, source), _node_text(name_node, source)
    return _node_text(name_node, source), _node_text(name_node, source)


class LuaHandlerMixin:
    """Lua parser: extracts functions and require() imports."""

    def _handle_lua(self, node: "Node") -> None:
        """Entry point — process the chunk (root) node."""
        self._lua_walk(node)

    def _lua_walk(self, node: "Node") -> None:
        """Walk a block/chunk and extract top-level declarations."""
        for child in node.children:
            t = child.type
            if t == "function_declaration":
                self._lua_handle_function(child)
            elif t in ("variable_declaration", "assignment_statement"):
                self._lua_handle_var_decl(child)
            elif t == "function_call":
                self._lua_handle_call(child)

    def _lua_handle_function(self, node: "Node") -> None:
        """Process a function_declaration node."""
        is_local = node.children and node.children[0].type == "local"

        # Find the name node: identifier | dot_index_expression | method_index_expression
        name_node = None
        for child in node.children:
            if child.type in ("identifier", "dot_index_expression", "method_index_expression"):
                name_node = child
                break
        if not name_node:
            return

        bare_name, qualified_name = _extract_fn_name(name_node, self.source)
        is_method = name_node.type == "method_index_expression"
        kind = SymbolKind.METHOD if is_method else SymbolKind.FUNCTION

        sym_id = self._make_id(qualified_name)
        sym = Symbol(
            id=sym_id,
            name=bare_name,
            qualified_name=qualified_name,
            kind=kind,
            language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=not is_local,
            parent_id=None,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    def _lua_handle_var_decl(self, node: "Node") -> None:
        """Scan variable declarations for require() calls."""
        self._lua_scan_for_require(node)

    def _lua_handle_call(self, node: "Node") -> None:
        """Handle top-level function_call (e.g. standalone require())."""
        self._lua_maybe_require(node)

    def _lua_scan_for_require(self, node: "Node") -> None:
        """Recursively look for function_call nodes that are require() calls."""
        for child in node.children:
            if child.type == "function_call":
                self._lua_maybe_require(child)
            else:
                self._lua_scan_for_require(child)

    def _lua_maybe_require(self, node: "Node") -> None:
        """If this function_call is require('module'), record the import."""
        fn_node = node.children[0] if node.children else None
        if fn_node is None or fn_node.type != "identifier":
            return
        if _node_text(fn_node, self.source) != "require":
            return
        # Arguments node — find the string content
        args = _first_child_of_type(node, "arguments")
        target = args if args else node  # bare require 'x' has string directly
        module_name = self._lua_extract_string(target)
        if module_name:
            self.imports.append(module_name)

    def _lua_extract_string(self, node: "Node") -> str | None:
        """Extract string_content from a string node or any descendant."""
        for child in node.children:
            if child.type == "string_content":
                return _node_text(child, self.source)
            if child.type == "string":
                result = self._lua_extract_string(child)
                if result:
                    return result
        return None
