"""Elixir language handler mixin for FileParser."""
from __future__ import annotations

from ..types import Edge, EdgeKind, Symbol, SymbolKind

try:
    from tree_sitter import Node
except ImportError:
    Node = object  # type: ignore[assignment,misc]

_IMPORT_MACROS = frozenset({"alias", "use", "import", "require"})
_DEF_MACROS = frozenset({"def", "defp", "defmacro", "defmacrop", "defguard", "defguardp"})
_DEF_PRIVATE = frozenset({"defp", "defmacrop", "defguardp"})


def _node_text(node: "Node", source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _get_call_name(node: "Node", source: bytes) -> str | None:
    """Return the identifier name of a `call` node (first child must be identifier)."""
    if not node.children:
        return None
    first = node.children[0]
    if first.type == "identifier":
        return _node_text(first, source)
    return None


def _get_module_name(call_node: "Node", source: bytes) -> str | None:
    """Extract module name from a defmodule call node."""
    args = next((c for c in call_node.children if c.type == "arguments"), None)
    if not args:
        return None
    for child in args.children:
        if child.type in ("alias", "identifier"):
            return _node_text(child, source)
    return None


def _get_func_name(call_node: "Node", source: bytes) -> str | None:
    """Extract function name from a def/defp call node.

    The function signature is the first argument of the def macro:
      def find(id) do ...  → arguments[0] = call{find, (id)}
      def find(id), do: ... → same
      def find, do: ...    → arguments[0] = identifier{find}  (no-arg form)
    """
    args = next((c for c in call_node.children if c.type == "arguments"), None)
    if not args:
        return None
    for child in args.children:
        if child.type == "call":
            # def func(args) form — func name is the call's first identifier child
            return _get_call_name(child, source)
        if child.type == "identifier":
            # def func, do: ... (no args)
            return _node_text(child, source)
    return None


class ElixirHandlerMixin:
    """Elixir parser: extracts modules, functions, macros, and imports."""

    def _handle_elixir(self, node: "Node") -> None:
        """Entry point — process the source root node."""
        self._elixir_walk(node, module_prefix="")

    def _elixir_walk(self, node: "Node", module_prefix: str) -> None:
        """Walk children of node, dispatching on Elixir macro calls."""
        children = node.children if node.type == "source" else (
            node.children if node.type == "do_block" else []
        )
        for child in children:
            if child.type != "call":
                continue
            macro = _get_call_name(child, self.source)
            if not macro:
                continue

            if macro == "defmodule":
                self._elixir_handle_module(child, module_prefix)
            elif macro in _DEF_MACROS:
                self._elixir_handle_func(child, macro, module_prefix)
            elif macro in _IMPORT_MACROS:
                self._elixir_handle_import(child)

    def _elixir_handle_module(self, node: "Node", parent_prefix: str) -> None:
        """Process a defmodule declaration."""
        mod_name = _get_module_name(node, self.source)
        if not mod_name:
            return

        # Qualified name: nested modules get their parent prefix
        qualified = f"{parent_prefix}.{mod_name}" if parent_prefix else mod_name
        sym_id = f"{self.file_path}::{qualified}"
        exported = not mod_name.startswith("_")

        sym = Symbol(
            id=sym_id, name=mod_name, qualified_name=qualified,
            kind=SymbolKind.CLASS,  # modules are the primary structural unit in Elixir
            language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=exported,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

        if self._current_parent_id():
            self.edges.append(Edge(EdgeKind.CONTAINS, self._current_parent_id(), sym_id, node.start_point[0] + 1))

        # Recurse into module body
        do_block = next((c for c in node.children if c.type == "do_block"), None)
        if do_block:
            self._symbol_stack.append(sym_id)
            self._elixir_walk(do_block, qualified)
            self._symbol_stack.pop()

    def _elixir_handle_func(self, node: "Node", macro: str, module_prefix: str) -> None:
        """Process a def/defp/defmacro declaration."""
        func_name = _get_func_name(node, self.source)
        if not func_name:
            return

        parent_id = self._current_parent_id()
        if parent_id:
            qualified = f"{module_prefix}.{func_name}"
            sym_id = f"{self.file_path}::{qualified}"
        else:
            sym_id = f"{self.file_path}::{func_name}"
            qualified = func_name

        exported = macro not in _DEF_PRIVATE and not func_name.startswith("_")
        kind = SymbolKind.FUNCTION if not parent_id else SymbolKind.METHOD

        sym = Symbol(
            id=sym_id, name=func_name, qualified_name=qualified,
            kind=kind, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=exported,
            parent_id=parent_id,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

        if parent_id:
            self.edges.append(Edge(EdgeKind.CONTAINS, parent_id, sym_id, node.start_point[0] + 1))

    def _elixir_handle_import(self, node: "Node") -> None:
        """Process alias/use/import/require statements."""
        args = next((c for c in node.children if c.type == "arguments"), None)
        if not args:
            return
        for child in args.children:
            if child.type in ("alias", "identifier"):
                self.imports.append(_node_text(child, self.source))
                return
