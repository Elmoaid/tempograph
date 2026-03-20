"""Swift language handler mixin for FileParser."""
from __future__ import annotations

from tree_sitter import Node

from ..types import Edge, EdgeKind, Symbol, SymbolKind
from ._utils import _node_text, _first_comment_above, _extract_signature


class SwiftHandlerMixin:
    """Mixin providing Swift-specific parsing methods for FileParser."""

    # ── Swift entry point ─────────────────────────────────────

    def _handle_swift(self, node: Node) -> None:
        for child in node.children:
            t = child.type
            if t == "import_declaration":
                pass  # skip — imports don't create symbols
            elif t == "function_declaration":
                self._handle_swift_function(child)
            elif t == "class_declaration":
                self._handle_swift_class_like(child)
            elif t == "protocol_declaration":
                self._handle_swift_protocol(child)

    # ── Swift function ────────────────────────────────────────

    def _handle_swift_function(self, node: Node, *, is_method: bool = False) -> None:
        name_node = self._swift_first_child_of_type(node, "simple_identifier")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if not name:
            return
        sym_id = self._make_id(name)
        kind = SymbolKind.METHOD if is_method else SymbolKind.FUNCTION
        exported = self._swift_is_exported(node)
        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name,
            qualified_name=f"{self._parent_qualified_name()}.{name}" if self._symbol_stack else name,
            kind=kind, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=_extract_signature(node, self.source, self.language),
            doc=doc,
            exported=exported,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
            complexity=self._compute_complexity(node),
        )
        self.symbols.append(sym)
        if self._current_parent_id():
            self.edges.append(Edge(EdgeKind.CONTAINS, self._current_parent_id(), sym_id))
        body = self._swift_first_child_of_type(node, "function_body")
        if body:
            self._scan_calls(body, sym_id)

    # ── Swift init ────────────────────────────────────────────

    def _handle_swift_init(self, node: Node) -> None:
        """Extract an initializer declaration — name = 'init', kind = METHOD."""
        name = "init"
        sym_id = self._make_id(name)
        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name,
            qualified_name=f"{self._parent_qualified_name()}.{name}" if self._symbol_stack else name,
            kind=SymbolKind.METHOD, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=_extract_signature(node, self.source, self.language),
            doc=doc,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)
        if self._current_parent_id():
            self.edges.append(Edge(EdgeKind.CONTAINS, self._current_parent_id(), sym_id))
        body = self._swift_first_child_of_type(node, "function_body")
        if body:
            self._scan_calls(body, sym_id)

    # ── Swift class / struct / enum / extension ───────────────

    def _handle_swift_class_like(self, node: Node) -> None:
        """Handle class_declaration, which covers class, struct, enum, and extension."""
        # Determine keyword to classify the construct
        keyword = None
        for child in node.children:
            if child.type in ("class", "struct", "enum", "extension") and not child.is_named:
                keyword = child.type
                break
            # Named keyword nodes
            if child.type in ("class", "struct", "enum", "extension"):
                keyword = child.type
                break

        if keyword is None:
            return

        if keyword == "extension":
            self._handle_swift_extension(node)
            return

        # class / struct / enum — find type_identifier for name
        name_node = self._swift_first_child_of_type(node, "type_identifier")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if not name:
            return

        kind_map = {
            "class": SymbolKind.CLASS,
            "struct": SymbolKind.STRUCT,
            "enum": SymbolKind.ENUM,
        }
        kind = kind_map[keyword]
        sym_id = self._make_id(name)
        exported = self._swift_is_exported(node)
        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=kind, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=name,
            doc=doc,
            exported=exported,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
            complexity=self._compute_complexity(node),
        )
        self.symbols.append(sym)

        # Walk body for methods and inits
        body = (
            self._swift_first_child_of_type(node, "class_body")
            or self._swift_first_child_of_type(node, "enum_class_body")
        )
        if body:
            self._symbol_stack.append(sym_id)
            for child in body.children:
                if child.type == "function_declaration":
                    self._handle_swift_function(child, is_method=True)
                elif child.type == "init_declaration":
                    self._handle_swift_init(child)
            self._symbol_stack.pop()

    # ── Swift extension (transparent container, like Rust impl) ──

    def _handle_swift_extension(self, node: Node) -> None:
        """Extension blocks add methods to an existing type.

        The extended type name is found in a user_type → type_identifier child.
        We look up the existing symbol and emit CONTAINS edges to methods.
        """
        # Find extended type name from user_type child
        user_type_node = self._swift_first_child_of_type(node, "user_type")
        if not user_type_node:
            return
        type_name_node = self._swift_first_child_of_type(user_type_node, "type_identifier")
        if not type_name_node:
            return
        type_name = _node_text(type_name_node, self.source)
        if not type_name:
            return

        # Find existing symbol or create synthetic one
        target_id = None
        for sym in self.symbols:
            if sym.name == type_name and sym.file_path == self.file_path:
                target_id = sym.id
                break

        parent = target_id or self._make_id(f"extension_{type_name}")
        if not target_id:
            ext_sym = Symbol(
                id=parent, name=f"extension {type_name}", qualified_name=f"extension {type_name}",
                kind=SymbolKind.CLASS, language=self.language,
                file_path=self.file_path,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                parent_id=self._current_parent_id(),
            )
            self.symbols.append(ext_sym)

        body = self._swift_first_child_of_type(node, "class_body")
        if body:
            self._symbol_stack.append(parent)
            for child in body.children:
                if child.type == "function_declaration":
                    self._handle_swift_function(child, is_method=True)
                elif child.type == "init_declaration":
                    self._handle_swift_init(child)
            self._symbol_stack.pop()

    # ── Swift protocol ────────────────────────────────────────

    def _handle_swift_protocol(self, node: Node) -> None:
        name_node = self._swift_first_child_of_type(node, "type_identifier")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if not name:
            return
        sym_id = self._make_id(name)
        exported = self._swift_is_exported(node)
        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.INTERFACE, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=name,
            doc=doc,
            exported=exported,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

        body = self._swift_first_child_of_type(node, "protocol_body")
        if body:
            self._symbol_stack.append(sym_id)
            for child in body.children:
                if child.type == "protocol_function_declaration":
                    self._handle_swift_protocol_method(child)
            self._symbol_stack.pop()

    # ── Swift protocol method declaration (no body) ───────────

    def _handle_swift_protocol_method(self, node: Node) -> None:
        name_node = self._swift_first_child_of_type(node, "simple_identifier")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if not name:
            return
        sym_id = self._make_id(name)
        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name,
            qualified_name=f"{self._parent_qualified_name()}.{name}" if self._symbol_stack else name,
            kind=SymbolKind.METHOD, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=_extract_signature(node, self.source, self.language),
            doc=doc,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)
        if self._current_parent_id():
            self.edges.append(Edge(EdgeKind.CONTAINS, self._current_parent_id(), sym_id))

    # ── Helpers ───────────────────────────────────────────────

    def _swift_first_child_of_type(self, node: Node, node_type: str) -> Node | None:
        """Return the first child with the given type (named or unnamed)."""
        for child in node.children:
            if child.type == node_type:
                return child
        return None

    def _swift_is_exported(self, node: Node) -> bool:
        """Return True if the node has public or open visibility modifier."""
        mods = self._swift_first_child_of_type(node, "modifiers")
        if not mods:
            return False
        for child in mods.children:
            if child.type == "visibility_modifier":
                text = _node_text(child, self.source)
                return text in ("public", "open")
        return False
