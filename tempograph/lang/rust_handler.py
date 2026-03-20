"""Rust language handler mixin for FileParser."""
from __future__ import annotations

from tree_sitter import Node

from ..types import Edge, EdgeKind, Symbol, SymbolKind
from ._utils import _node_text, _first_comment_above, _extract_signature


class RustHandlerMixin:
    """Mixin providing Rust-specific parsing methods for FileParser."""

    # ── Rust entry point ─────────────────────────────────────

    def _handle_rust(self, node: Node) -> None:
        for child in node.children:
            t = child.type
            if t == "use_declaration":
                self.imports.append(_node_text(child, self.source).strip())
            elif t == "function_item":
                self._handle_rust_function(child)
            elif t == "struct_item":
                self._handle_rust_struct(child)
            elif t == "enum_item":
                self._handle_rust_enum(child)
            elif t == "trait_item":
                self._handle_rust_trait(child)
            elif t == "impl_item":
                self._handle_rust_impl(child)
            elif t in ("const_item", "static_item"):
                self._handle_rust_const(child)
            elif t == "mod_item":
                self._handle_rust_mod(child)
            elif t == "macro_definition":
                self._handle_rust_macro(child)
            elif t == "attribute_item":
                pass  # skip attributes, they decorate the next item

    # ── Rust function / method ────────────────────────────────

    def _handle_rust_function(self, node: Node, *, is_method: bool = False) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        kind = SymbolKind.METHOD if is_method else SymbolKind.FUNCTION
        # Detect test functions and Tauri commands via preceding attribute
        prev = node.prev_named_sibling
        if prev and prev.type == "attribute_item":
            attr_text = _node_text(prev, self.source)
            if "test" in attr_text:
                kind = SymbolKind.TEST
            elif "tauri::command" in attr_text:
                kind = SymbolKind.COMMAND

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
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
            complexity=self._compute_complexity(node),
        )
        self.symbols.append(sym)
        if self._current_parent_id():
            self.edges.append(Edge(EdgeKind.CONTAINS, self._current_parent_id(), sym_id))
        body = node.child_by_field_name("body")
        if body:
            self._scan_calls(body, sym_id)

    # ── Rust function signature (trait declaration, no body) ──

    def _handle_rust_function_sig(self, node: Node) -> None:
        """Extract a trait method declaration (function_signature_item — no body)."""
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
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

    # ── Rust struct ───────────────────────────────────────────

    def _handle_rust_struct(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.STRUCT, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=_extract_signature(node, self.source, self.language),
            doc=doc,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    # ── Rust enum ─────────────────────────────────────────────

    def _handle_rust_enum(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.ENUM, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            doc=doc,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    # ── Rust trait ────────────────────────────────────────────

    def _handle_rust_trait(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.TRAIT, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            doc=doc,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)
        body = node.child_by_field_name("body")
        if body:
            self._symbol_stack.append(sym_id)
            for child in body.children:
                if child.type == "function_item":
                    self._handle_rust_function(child, is_method=True)
                elif child.type == "function_signature_item":
                    self._handle_rust_function_sig(child)
            self._symbol_stack.pop()

    # ── Rust impl block ───────────────────────────────────────

    def _handle_rust_impl(self, node: Node) -> None:
        type_node = node.child_by_field_name("type")
        if not type_node:
            return
        type_name = _node_text(type_node, self.source)

        # Detect "impl Trait for Type" — the trait field holds the trait name
        trait_node = node.child_by_field_name("trait")
        trait_name = _node_text(trait_node, self.source) if trait_node else None

        # Find the matching struct/enum symbol in the current file
        target_id = None
        for sym in self.symbols:
            if sym.name == type_name and sym.file_path == self.file_path:
                target_id = sym.id
                break

        # Create IMPLEMENTS edge: Type → Trait
        if trait_name and target_id:
            self.edges.append(Edge(
                EdgeKind.IMPLEMENTS, target_id, trait_name,
                node.start_point[0] + 1,
            ))

        body = node.child_by_field_name("body")
        if not body:
            return
        parent = target_id or self._make_id(f"impl_{type_name}")
        if not target_id:
            # Create a synthetic impl symbol when the type isn't defined in this file
            impl_sym = Symbol(
                id=parent, name=f"impl {type_name}", qualified_name=f"impl {type_name}",
                kind=SymbolKind.IMPL, language=self.language,
                file_path=self.file_path,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                parent_id=self._current_parent_id(),
            )
            self.symbols.append(impl_sym)
        self._symbol_stack.append(parent)
        for child in body.children:
            if child.type == "function_item":
                self._handle_rust_function(child, is_method=True)
        self._symbol_stack.pop()

    # ── Rust const / static ───────────────────────────────────

    def _handle_rust_const(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.CONSTANT, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            parent_id=self._current_parent_id(),
        )
        self.symbols.append(sym)

    # ── Rust mod ──────────────────────────────────────────────

    def _handle_rust_mod(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.MODULE, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            parent_id=self._current_parent_id(),
        )
        self.symbols.append(sym)
        body = node.child_by_field_name("body")
        if body:
            self._symbol_stack.append(sym_id)
            self._handle_rust(body)
            self._symbol_stack.pop()

    # ── Rust macro ────────────────────────────────────────────

    def _handle_rust_macro(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.FUNCTION, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            doc="macro",
            parent_id=self._current_parent_id(),
        )
        self.symbols.append(sym)
