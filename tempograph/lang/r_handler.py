"""R language handler mixin for FileParser."""
from __future__ import annotations

from ..types import Symbol, SymbolKind

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


class RHandlerMixin:
    """R parser: extracts function definitions and library/require/source imports."""

    def _handle_r(self, node: "Node") -> None:
        """Entry point — process the program (root) node."""
        for child in node.children:
            t = child.type
            if t == "binary_operator":
                self._r_handle_assignment(child, is_top_level=True)
            elif t == "call":
                self._r_handle_call(child)

    def _r_handle_assignment(self, node: "Node", is_top_level: bool) -> None:
        """Handle a binary_operator assignment. If RHS is a function_definition,
        record a function symbol. Only top-level assignments are exported."""
        children = node.children
        if len(children) < 3:
            return
        lhs, op, rhs = children[0], children[1], children[2]

        op_text = _node_text(op, self.source)
        if op_text not in ("<-", "=", "<<-"):
            return
        if lhs.type != "identifier":
            return
        if rhs.type != "function_definition":
            return

        name = _node_text(lhs, self.source)
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
            exported=is_top_level,
            parent_id=None,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    def _r_handle_call(self, node: "Node") -> None:
        """Handle top-level call expressions for library/require/source imports."""
        fn_node = _first_child_of_type(node, "identifier", "return")
        if fn_node is None or fn_node.type != "identifier":
            return
        fn_name = _node_text(fn_node, self.source)
        if fn_name not in ("library", "require", "source"):
            return
        args = _first_child_of_type(node, "arguments")
        if not args:
            return
        # First argument: either bare identifier (library(ggplot2)) or string ("utils.R")
        arg = _first_child_of_type(args, "argument")
        if not arg:
            return
        inner = arg.children[0] if arg.children else None
        if inner is None:
            return
        if inner.type == "identifier":
            self.imports.append(_node_text(inner, self.source))
        elif inner.type == "string":
            content = _first_child_of_type(inner, "string_content")
            if content:
                self.imports.append(_node_text(content, self.source))
