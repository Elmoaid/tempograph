"""C/C++ language handler mixin for FileParser."""
from __future__ import annotations

from tree_sitter import Node

from ..types import Edge, EdgeKind, Symbol, SymbolKind
from ._utils import _node_text, _first_comment_above


class CHandlerMixin:
    """Mixin providing C and C++ parsing methods for FileParser."""

    # ── C entry point ────────────────────────────────────────

    def _handle_c(self, node: Node) -> None:
        """Walk C translation_unit."""
        self._c_walk_body(node, is_cpp=False)

    # ── C++ entry point ──────────────────────────────────────

    def _handle_cpp(self, node: Node) -> None:
        """Walk C++ translation_unit."""
        self._c_walk_body(node, is_cpp=True)

    # ── Shared body walker ───────────────────────────────────

    def _c_walk_body(
        self,
        node: Node,
        *,
        is_cpp: bool,
        parent_id: str | None = None,
        parent_name: str | None = None,
    ) -> None:
        """Walk direct children of a C/C++ body node."""
        for child in node.children:
            t = child.type
            if t == "function_definition":
                self._handle_c_function(
                    child, is_cpp=is_cpp,
                    parent_id=parent_id, parent_name=parent_name,
                )
            elif t in ("struct_specifier", "union_specifier"):
                self._handle_c_struct(child, is_cpp=is_cpp)
            elif t == "class_specifier" and is_cpp:
                self._handle_cpp_class(child)
            elif t == "enum_specifier":
                self._handle_c_enum(child)
            elif t == "type_definition":
                self._handle_c_typedef(child, is_cpp=is_cpp)
            elif t == "namespace_definition" and is_cpp:
                self._handle_cpp_namespace(child)
            # skip: declaration (forward decl), access_specifier, preproc_*, comments

    # ── C/C++ function ───────────────────────────────────────

    def _handle_c_function(
        self,
        node: Node,
        *,
        is_cpp: bool = False,
        parent_id: str | None = None,
        parent_name: str | None = None,
    ) -> None:
        """Extract a C/C++ function or method from function_definition."""
        decl = self._c_find_child(node, "function_declarator")
        if decl is None:
            return
        name_node = self._c_fn_name_node(decl)
        if name_node is None:
            return
        name = _node_text(name_node, self.source)
        if not name:
            return

        qualified = f"{parent_name}.{name}" if parent_name else name
        sym_id = f"{self.file_path}::{qualified}"
        body = self._c_find_child(node, "compound_statement")

        # exported = not static (static = file-local linkage)
        exported = not any(
            child.type == "storage_class_specifier"
            and _node_text(child, self.source).strip() == "static"
            for child in node.children
        )

        sym = Symbol(
            id=sym_id, name=name, qualified_name=qualified,
            kind=SymbolKind.METHOD if parent_id else SymbolKind.FUNCTION,
            language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=_node_text(node, self.source).split("\n")[0].rstrip("{").strip()[:200],
            doc=_first_comment_above(node, self.source),
            parent_id=parent_id,
            exported=exported,
            byte_size=node.end_byte - node.start_byte,
            complexity=self._compute_complexity(body) if body else 1,
        )
        self.symbols.append(sym)
        if parent_id:
            self.edges.append(Edge(EdgeKind.CONTAINS, parent_id, sym_id))
        if body:
            self._scan_calls(body, sym_id)

    # ── C struct / union ─────────────────────────────────────

    def _handle_c_struct(self, node: Node, *, is_cpp: bool = False) -> None:
        """Extract a named C struct or union."""
        name_node = self._c_find_child(node, "type_identifier")
        if not name_node:
            return  # anonymous struct without typedef — skip
        name = _node_text(name_node, self.source)
        if not name:
            return

        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.STRUCT, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=name,
            doc=_first_comment_above(node, self.source),
            exported=True,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    # ── C enum ───────────────────────────────────────────────

    def _handle_c_enum(self, node: Node) -> None:
        """Extract a named C/C++ enum."""
        name_node = self._c_find_child(node, "type_identifier")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if not name:
            return

        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.ENUM, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=name,
            exported=True,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    # ── C typedef ────────────────────────────────────────────

    def _handle_c_typedef(self, node: Node, *, is_cpp: bool = False) -> None:
        """Handle typedef declarations, using the typedef alias as the symbol name."""
        inner_struct: Node | None = None
        inner_enum: Node | None = None
        typedef_name: str | None = None

        for child in node.children:
            t = child.type
            if t in ("struct_specifier", "union_specifier"):
                inner_struct = child
            elif t == "enum_specifier":
                inner_enum = child
            elif t == "type_identifier":
                # The last type_identifier in a type_definition is the typedef alias
                typedef_name = _node_text(child, self.source)

        if inner_struct is not None and typedef_name:
            sym_id = self._make_id(typedef_name)
            sym = Symbol(
                id=sym_id, name=typedef_name, qualified_name=typedef_name,
                kind=SymbolKind.STRUCT, language=self.language,
                file_path=self.file_path,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                signature=typedef_name,
                doc=_first_comment_above(node, self.source),
                exported=True,
                byte_size=node.end_byte - node.start_byte,
            )
            self.symbols.append(sym)
        elif inner_enum is not None and typedef_name:
            sym_id = self._make_id(typedef_name)
            sym = Symbol(
                id=sym_id, name=typedef_name, qualified_name=typedef_name,
                kind=SymbolKind.ENUM, language=self.language,
                file_path=self.file_path,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                signature=typedef_name,
                exported=True,
                byte_size=node.end_byte - node.start_byte,
            )
            self.symbols.append(sym)
        # typedef to existing type (no inner struct/enum): skip

    # ── C++ class ────────────────────────────────────────────

    def _handle_cpp_class(self, node: Node) -> None:
        """Extract a C++ class_specifier and its inline method definitions."""
        name_node = self._c_find_child(node, "type_identifier")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if not name:
            return

        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.CLASS, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=name,
            doc=_first_comment_above(node, self.source),
            exported=True,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

        # Extract inline method definitions from field_declaration_list
        body = self._c_find_child(node, "field_declaration_list")
        if body:
            for child in body.children:
                if child.type == "function_definition":
                    self._handle_c_function(
                        child, is_cpp=True,
                        parent_id=sym_id, parent_name=name,
                    )

    # ── C++ namespace ────────────────────────────────────────

    def _handle_cpp_namespace(self, node: Node) -> None:
        """Recurse into a C++ namespace body (transparent — no symbol emitted)."""
        decl_list = self._c_find_child(node, "declaration_list")
        if decl_list:
            self._c_walk_body(decl_list, is_cpp=True)

    # ── Helpers ──────────────────────────────────────────────

    @staticmethod
    def _c_find_child(node: Node, child_type: str) -> Node | None:
        """Return first direct child of the given type."""
        for child in node.children:
            if child.type == child_type:
                return child
        return None

    @staticmethod
    def _c_fn_name_node(declarator: Node) -> Node | None:
        """Return the name node from a function_declarator.

        Handles three forms:
        - ``identifier``: top-level C/C++ function
        - ``field_identifier``: inline C++ method inside a class body
        - ``qualified_identifier`` → ``identifier``: out-of-line C++ method (``Cls::fn``)
        """
        for child in declarator.children:
            if child.type in ("identifier", "field_identifier"):
                return child
            if child.type == "qualified_identifier":
                # Walk backward to find the final identifier (method name after ::)
                for sub in reversed(child.children):
                    if sub.type == "identifier":
                        return sub
        return None
