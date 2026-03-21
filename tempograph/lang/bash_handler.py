"""Bash/shell language handler mixin for FileParser."""
from __future__ import annotations

from ..types import Symbol, SymbolKind

try:
    from tree_sitter import Node
except ImportError:
    Node = object  # type: ignore[assignment,misc]


def _node_text(node: "Node", source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


class BashHandlerMixin:
    """Bash/sh parser: extracts functions, constants, and source imports."""

    def _handle_bash(self, node: "Node") -> None:
        """Entry point — process the program (root) node."""
        for child in node.children:
            self._bash_dispatch(child)

    def _bash_dispatch(self, node: "Node") -> None:
        t = node.type
        if t == "function_definition":
            self._bash_handle_function(node)
        elif t == "declaration_command":
            self._bash_handle_declaration(node)
        elif t == "variable_assignment":
            self._bash_handle_var_assignment(node)
        elif t == "command":
            self._bash_handle_command(node)

    def _bash_handle_function(self, node: "Node") -> None:
        """Process a function_definition node.

        Both forms are supported:
          function name() { ... }
          name() { ... }
        """
        # Name is in first 'word' child
        name_node = next((c for c in node.children if c.type == "word"), None)
        if not name_node:
            return

        name = _node_text(name_node, self.source)
        if not name:
            return

        sym = Symbol(
            id=self._make_id(name),
            name=name,
            qualified_name=name,
            kind=SymbolKind.FUNCTION,
            language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=not name.startswith("_"),
            parent_id=None,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    def _bash_handle_declaration(self, node: "Node") -> None:
        """Handle readonly/declare commands.

        readonly FOO=bar  → constant
        declare -r BAR=1  → constant (when -r flag present)
        """
        has_readonly = any(
            c.type in ("readonly",) or (c.type == "word" and _node_text(c, self.source) in ("-r",))
            for c in node.children
        )

        for child in node.children:
            if child.type == "variable_assignment":
                self._bash_extract_constant(child, is_readonly=has_readonly)
            elif child.type == "variable_name":
                # declare -A name (no assignment) — skip
                pass

    def _bash_handle_var_assignment(self, node: "Node") -> None:
        """Top-level variable assignment: FOO=bar.

        Uppercase names with no lowercase letters are treated as constants.
        """
        var_node = next((c for c in node.children if c.type == "variable_name"), None)
        if not var_node:
            return
        name = _node_text(var_node, self.source)
        if name and name.upper() == name and any(c.isalpha() for c in name):
            self._bash_emit_constant(node, name)

    def _bash_extract_constant(self, assign_node: "Node", *, is_readonly: bool) -> None:
        """Extract a constant from a variable_assignment node inside declare/readonly."""
        var_node = next((c for c in assign_node.children if c.type == "variable_name"), None)
        if not var_node:
            return
        name = _node_text(var_node, self.source)
        if not name:
            return
        # Emit if readonly OR uppercase convention
        if is_readonly or (name.upper() == name and any(c.isalpha() for c in name)):
            self._bash_emit_constant(assign_node, name)

    def _bash_emit_constant(self, node: "Node", name: str) -> None:
        sym = Symbol(
            id=self._make_id(name),
            name=name,
            qualified_name=name,
            kind=SymbolKind.CONSTANT,
            language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=True,
            parent_id=None,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    def _bash_handle_command(self, node: "Node") -> None:
        """Handle 'source ./file.sh' and '. ./file.sh' imports."""
        children = node.children
        if not children:
            return
        cmd_name = _node_text(children[0], self.source) if children[0].type == "command_name" else ""
        if cmd_name not in ("source", "."):
            return
        if len(children) < 2:
            return
        target = _node_text(children[1], self.source).strip()
        if target:
            self.imports.append(target)
