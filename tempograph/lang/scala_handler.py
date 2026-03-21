"""Scala language handler mixin for FileParser."""
from __future__ import annotations

from ..types import Edge, EdgeKind, Symbol, SymbolKind

try:
    from tree_sitter import Node
except ImportError:
    Node = object  # type: ignore[assignment,misc]


def _node_text(node: "Node", source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _is_private(node: "Node", source: bytes) -> bool:
    """Return True if the node has a private/protected access modifier."""
    for child in node.children:
        if child.type == "modifiers":
            for mod in child.children:
                if mod.type == "access_modifier":
                    text = _node_text(mod, source)
                    if "private" in text or "protected" in text:
                        return True
    return False


def _get_identifier(node: "Node", source: bytes) -> str | None:
    """Return the first identifier or type_identifier child of a node."""
    for child in node.children:
        if child.type in ("identifier", "type_identifier"):
            return _node_text(child, source)
    return None


def _get_extends_type(node: "Node", source: bytes) -> str | None:
    """Return the base class/trait name from an extends_clause, if any."""
    for child in node.children:
        if child.type == "extends_clause":
            for sub in child.children:
                if sub.type in ("type_identifier", "generic_type"):
                    name = _node_text(sub, source)
                    # Strip generic args: Repository[User] → Repository
                    if "[" in name:
                        name = name.split("[")[0]
                    return name
    return None


class ScalaHandlerMixin:
    """Scala parser: extracts classes, traits, objects, functions, and imports."""

    def _handle_scala(self, node: "Node") -> None:
        """Entry point — process the source root node."""
        self._scala_walk_top(node)

    def _scala_walk_top(self, node: "Node") -> None:
        """Walk top-level declarations."""
        for child in node.children:
            t = child.type
            if t == "import_declaration":
                self._scala_handle_import(child)
            elif t == "package_clause":
                pass  # package name not tracked as symbol
            elif t == "class_definition":
                self._scala_handle_class(child)
            elif t == "trait_definition":
                self._scala_handle_trait(child)
            elif t == "object_definition":
                self._scala_handle_object(child)
            elif t == "function_definition":
                self._scala_handle_function(child)
            elif t == "enum_definition":
                self._scala_handle_enum(child)

    def _scala_handle_import(self, node: "Node") -> None:
        """Process import declarations."""
        text = _node_text(node, self.source).strip()
        # Remove 'import ' prefix and get the import path
        if text.startswith("import "):
            self.imports.append(text[7:].strip())

    def _scala_handle_class(self, node: "Node", *, exported: bool = True) -> None:
        """Process class declarations (includes case classes)."""
        name = _get_identifier(node, self.source)
        if not name:
            return
        sym_id = self._make_id(name)
        private = _is_private(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.CLASS, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=not private and exported and not name.startswith("_"),
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

        if self._current_parent_id():
            self.edges.append(Edge(EdgeKind.CONTAINS, self._current_parent_id(), sym_id, node.start_point[0] + 1))

        # Inheritance
        extends = _get_extends_type(node, self.source)
        if extends and extends[0].isupper():
            self.edges.append(Edge(EdgeKind.INHERITS, sym_id, extends, node.start_point[0] + 1))

        # Process body
        body = node.child_by_field_name("body") or next(
            (c for c in node.children if c.type == "template_body"), None
        )
        if body:
            self._symbol_stack.append(sym_id)
            self._scala_walk_body(body)
            self._symbol_stack.pop()

    def _scala_handle_trait(self, node: "Node") -> None:
        """Process trait declarations."""
        name = _get_identifier(node, self.source)
        if not name:
            return
        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.INTERFACE, language=self.language,
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

        body = next((c for c in node.children if c.type == "template_body"), None)
        if body:
            self._symbol_stack.append(sym_id)
            self._scala_walk_body(body)
            self._symbol_stack.pop()

    def _scala_handle_object(self, node: "Node") -> None:
        """Process object declarations (singleton objects)."""
        name = _get_identifier(node, self.source)
        if not name:
            return
        sym_id = self._make_id(name)
        private = _is_private(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.CLASS, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=not private and not name.startswith("_"),
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

        if self._current_parent_id():
            self.edges.append(Edge(EdgeKind.CONTAINS, self._current_parent_id(), sym_id, node.start_point[0] + 1))

        body = next((c for c in node.children if c.type == "template_body"), None)
        if body:
            self._symbol_stack.append(sym_id)
            self._scala_walk_body(body)
            self._symbol_stack.pop()

    def _scala_handle_enum(self, node: "Node") -> None:
        """Process enum declarations (Scala 3 enums)."""
        name = _get_identifier(node, self.source)
        if not name:
            return
        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.ENUM, language=self.language,
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

    def _scala_walk_body(self, body: "Node") -> None:
        """Process members inside a class/trait/object body."""
        for child in body.children:
            t = child.type
            if t in ("function_definition", "function_declaration"):
                self._scala_handle_function(child)
            elif t == "class_definition":
                self._scala_handle_class(child)
            elif t == "object_definition":
                self._scala_handle_object(child)
            elif t == "trait_definition":
                self._scala_handle_trait(child)
            elif t == "enum_definition":
                self._scala_handle_enum(child)

    def _scala_handle_function(self, node: "Node") -> None:
        """Process def declarations (function_definition and function_declaration)."""
        name = _get_identifier(node, self.source)
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
