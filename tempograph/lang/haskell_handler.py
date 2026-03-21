"""Haskell language handler mixin for FileParser."""
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


class HaskellHandlerMixin:
    """Haskell parser: extracts functions, types, classes, newtypes, and imports."""

    def _handle_haskell(self, node: "Node") -> None:
        """Entry point — process the haskell root node."""
        self._haskell_walk(node)

    def _haskell_walk(self, node: "Node") -> None:
        """Walk the root haskell node."""
        for child in node.children:
            t = child.type
            if t == "imports":
                self._haskell_handle_imports(child)
            elif t == "declarations":
                self._haskell_walk_decls(child)

    def _haskell_walk_decls(self, node: "Node") -> None:
        """Walk a declarations node and extract top-level definitions."""
        seen_fns: set[str] = set()  # deduplicate multi-equation functions
        for child in node.children:
            t = child.type
            if t == "function":
                self._haskell_handle_function(child, seen_fns)
            elif t == "data_type":
                self._haskell_handle_data(child)
            elif t == "type_synomym":
                self._haskell_handle_type_synonym(child)
            elif t in ("class", "class_declaration"):
                self._haskell_handle_class(child)
            elif t == "newtype":
                self._haskell_handle_newtype(child)
            # Skip: signature (type signatures don't define symbols)

    def _haskell_handle_function(self, node: "Node", seen: set[str]) -> None:
        """Process a function definition — `name patterns = body`."""
        name_node = _first_child_of_type(node, "variable")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if name in seen:
            return  # multi-equation function — only emit the first occurrence
        seen.add(name)

        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.FUNCTION, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=not name.startswith("_"),
            parent_id=None,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    def _haskell_handle_data(self, node: "Node") -> None:
        """Process `data Name = ...` declarations."""
        name_node = _first_child_of_type(node, "name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.TYPE_ALIAS, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=not name.startswith("_"),
            parent_id=None,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    def _haskell_handle_type_synonym(self, node: "Node") -> None:
        """Process `type Name = ...` type synonyms."""
        name_node = _first_child_of_type(node, "name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.TYPE_ALIAS, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=not name.startswith("_"),
            parent_id=None,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    def _haskell_handle_class(self, node: "Node") -> None:
        """Process `class Name a where ...` type class declarations."""
        name_node = _first_child_of_type(node, "name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.INTERFACE, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=not name.startswith("_"),
            parent_id=None,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    def _haskell_handle_newtype(self, node: "Node") -> None:
        """Process `newtype Name = ...` declarations."""
        name_node = _first_child_of_type(node, "name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.CLASS, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=not name.startswith("_"),
            parent_id=None,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    def _haskell_handle_imports(self, node: "Node") -> None:
        """Process `import Module.Path` import declarations."""
        for child in node.children:
            if child.type == "import":
                self._haskell_handle_one_import(child)

    def _haskell_handle_one_import(self, node: "Node") -> None:
        """Extract module name from a single import declaration."""
        mod_node = _first_child_of_type(node, "module")
        if mod_node:
            # Full dotted path (e.g. Data.List)
            text = _node_text(mod_node, self.source).strip()
            if text:
                self.imports.append(text)
