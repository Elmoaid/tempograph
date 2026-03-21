"""Kotlin language handler mixin for FileParser."""
from __future__ import annotations

from tree_sitter import Node

from ..types import Edge, EdgeKind, Symbol, SymbolKind
from ._utils import _node_text, _first_comment_above, _extract_signature


class KotlinHandlerMixin:
    """Mixin providing Kotlin-specific parsing methods for FileParser."""

    # ── Kotlin entry point ─────────────────────────────────────

    def _handle_kotlin(self, node: Node) -> None:
        for child in node.children:
            t = child.type
            if t == "import_list":
                self._kotlin_handle_imports(child)
            elif t == "function_declaration":
                self._handle_kotlin_function(child)
            elif t == "class_declaration":
                self._handle_kotlin_class(child)
            elif t == "object_declaration":
                self._handle_kotlin_object(child)
            elif t == "property_declaration":
                self._handle_kotlin_property(child)

    # ── Kotlin imports ─────────────────────────────────────────

    def _kotlin_handle_imports(self, node: Node) -> None:
        for child in node.children:
            if child.type == "import_header":
                self.imports.append(_node_text(child, self.source).strip())

    # ── Kotlin visibility helper ───────────────────────────────

    def _kotlin_is_exported(self, node: Node) -> bool:
        """Return True if the node is public (default) or explicitly public.

        Private, protected, and internal symbols are NOT exported.
        """
        mods = None
        for child in node.children:
            if child.type == "modifiers":
                mods = child
                break
        if not mods:
            return True  # Kotlin default is public
        for child in mods.children:
            if child.type == "visibility_modifier":
                text = _node_text(child, self.source)
                return text == "public"
        return True  # no visibility modifier found → public

    # ── Kotlin class modifier helper ───────────────────────────

    def _kotlin_class_modifier(self, node: Node) -> str | None:
        """Return 'data', 'enum', or 'sealed' if present, else None.

        These can appear either inside a modifiers node (data, sealed)
        or as unnamed keyword children directly on class_declaration (enum).
        """
        for child in node.children:
            if child.type == "modifiers":
                for mod in child.children:
                    if mod.type == "class_modifier":
                        text = _node_text(mod, self.source)
                        if text in ("data", "enum", "sealed"):
                            return text
            # enum appears as unnamed keyword child directly on class_declaration
            if not child.is_named and child.type in ("enum", "sealed"):
                return child.type
        return None

    # ── Kotlin function ────────────────────────────────────────

    def _handle_kotlin_function(self, node: Node, *, is_method: bool = False) -> None:
        name_node = self._kotlin_first_child_of_type(node, "simple_identifier")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if not name:
            return

        exported = self._kotlin_is_exported(node)

        # Extension function: fun ReceiverType.name()
        receiver_node = self._kotlin_first_child_of_type(node, "receiver_type")
        qualified_name = name
        if receiver_node:
            receiver_text = _node_text(receiver_node, self.source).strip()
            qualified_name = f"{receiver_text}.{name}"
        elif self._symbol_stack:
            parent_qname = self._parent_qualified_name()
            if parent_qname:
                qualified_name = f"{parent_qname}.{name}"

        kind = SymbolKind.METHOD if is_method else SymbolKind.FUNCTION
        sym_id = self._make_id(name)
        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name,
            qualified_name=qualified_name,
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
        body = self._kotlin_first_child_of_type(node, "function_body")
        if body:
            self._scan_calls(body, sym_id)

    # ── Kotlin class (class, interface, data class, enum class, sealed class) ──

    def _handle_kotlin_class(self, node: Node) -> None:
        # Determine if this is a class or interface
        is_interface = False
        for child in node.children:
            if not child.is_named and child.type == "interface":
                is_interface = True
                break

        name_node = self._kotlin_first_child_of_type(node, "type_identifier")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if not name:
            return

        exported = self._kotlin_is_exported(node)
        class_mod = self._kotlin_class_modifier(node)

        if is_interface:
            kind = SymbolKind.INTERFACE
        elif class_mod == "enum":
            kind = SymbolKind.ENUM
        else:
            kind = SymbolKind.CLASS

        sym_id = self._make_id(name)
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

        # Extract inheritance (delegation_specifier)
        for child in node.children:
            if child.type == "delegation_specifier":
                for c in child.children:
                    if c.type == "constructor_invocation":
                        user_type = self._kotlin_first_child_of_type(c, "user_type")
                        if user_type:
                            type_id = self._kotlin_first_child_of_type(user_type, "type_identifier")
                            if type_id:
                                self.edges.append(Edge(
                                    EdgeKind.INHERITS, sym_id,
                                    _node_text(type_id, self.source),
                                    node.start_point[0] + 1,
                                ))
                    elif c.type == "user_type":
                        type_id = self._kotlin_first_child_of_type(c, "type_identifier")
                        if type_id:
                            self.edges.append(Edge(
                                EdgeKind.IMPLEMENTS, sym_id,
                                _node_text(type_id, self.source),
                                node.start_point[0] + 1,
                            ))

        # Walk body for methods, companion objects, nested classes, secondary constructors
        body = (
            self._kotlin_first_child_of_type(node, "class_body")
            or self._kotlin_first_child_of_type(node, "enum_class_body")
        )
        if body:
            self._symbol_stack.append(sym_id)
            for child in body.children:
                if child.type == "function_declaration":
                    self._handle_kotlin_function(child, is_method=True)
                elif child.type == "companion_object":
                    self._handle_kotlin_companion_object(child, sym_id)
                elif child.type == "class_declaration":
                    self._handle_kotlin_class(child)
                elif child.type == "secondary_constructor":
                    self._handle_kotlin_secondary_constructor(child)
            self._symbol_stack.pop()

    # ── Kotlin object declaration (singleton) ──────────────────

    def _handle_kotlin_object(self, node: Node) -> None:
        name_node = self._kotlin_first_child_of_type(node, "type_identifier")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if not name:
            return
        sym_id = self._make_id(name)
        exported = self._kotlin_is_exported(node)
        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.CLASS, language=self.language,
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

        body = self._kotlin_first_child_of_type(node, "class_body")
        if body:
            self._symbol_stack.append(sym_id)
            for child in body.children:
                if child.type == "function_declaration":
                    self._handle_kotlin_function(child, is_method=True)
            self._symbol_stack.pop()

    # ── Kotlin companion object ────────────────────────────────

    def _handle_kotlin_companion_object(self, node: Node, parent_id: str) -> None:
        """Handle companion object — methods are attached to the parent class."""
        body = self._kotlin_first_child_of_type(node, "class_body")
        if body:
            for child in body.children:
                if child.type == "function_declaration":
                    self._handle_kotlin_function(child, is_method=True)

    # ── Kotlin secondary constructor ───────────────────────────

    def _handle_kotlin_secondary_constructor(self, node: Node) -> None:
        name = "constructor"
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

    # ── Kotlin top-level property ──────────────────────────────

    def _handle_kotlin_property(self, node: Node) -> None:
        """Handle top-level val/var declarations as VARIABLE symbols."""
        if self._symbol_stack:
            return  # skip class-level properties (already part of class)
        var_decl = self._kotlin_first_child_of_type(node, "variable_declaration")
        if not var_decl:
            return
        name_node = self._kotlin_first_child_of_type(var_decl, "simple_identifier")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if not name:
            return
        sym_id = self._make_id(name)
        exported = self._kotlin_is_exported(node)
        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.VARIABLE, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=_extract_signature(node, self.source, self.language),
            doc=doc,
            exported=exported,
            parent_id=None,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    # ── Helpers ───────────────────────────────────────────────

    def _kotlin_first_child_of_type(self, node: Node, node_type: str) -> Node | None:
        for child in node.children:
            if child.type == node_type:
                return child
        return None
