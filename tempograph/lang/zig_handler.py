"""Zig language handler mixin for FileParser."""
from __future__ import annotations

from tree_sitter import Node

from ..types import Edge, EdgeKind, Symbol, SymbolKind
from ._utils import _node_text


class ZigHandlerMixin:
    """Mixin providing Zig-specific parsing methods for FileParser."""

    # ── Zig ─────────────────────────────────────────────────

    def _handle_zig(self, node: Node) -> None:
        """Walk source_file, tracking 'pub' sibling for exported flag."""
        prev_pub = False
        for child in node.children:
            if child.type == "pub":
                prev_pub = True
                continue
            if child.type == "Decl":
                self._handle_zig_decl(child, exported=prev_pub)
            prev_pub = False

    def _handle_zig_decl(self, node: Node, *, exported: bool = False) -> None:
        """Dispatch a top-level Decl node to fn or var handler."""
        fn_proto: Node | None = None
        block: Node | None = None
        var_decl: Node | None = None

        for child in node.children:
            if child.type == "FnProto":
                fn_proto = child
            elif child.type == "Block":
                block = child
            elif child.type == "VarDecl":
                var_decl = child

        if fn_proto is not None:
            self._handle_zig_fn(fn_proto, block, exported=exported)
        elif var_decl is not None:
            self._handle_zig_var(var_decl, exported=exported)

    def _handle_zig_fn(
        self, fn_proto: Node, block: Node | None, *, exported: bool = False,
        parent_id: str | None = None, parent_name: str | None = None,
    ) -> None:
        """Extract a Zig function or method from FnProto + optional Block."""
        name_node = self._zig_find_identifier(fn_proto)
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if not name:
            return

        if parent_name:
            qualified = f"{parent_name}.{name}"
            sym_id = f"{self.file_path}::{parent_name}.{name}"
        else:
            qualified = name
            sym_id = self._make_id(name)

        sig = _node_text(fn_proto, self.source).split("\n")[0][:200]
        end_node = block if block is not None else fn_proto
        complexity = self._compute_complexity(block) if block is not None else 1

        sym = Symbol(
            id=sym_id, name=name, qualified_name=qualified,
            kind=SymbolKind.METHOD if parent_id else SymbolKind.FUNCTION,
            language=self.language,
            file_path=self.file_path,
            line_start=fn_proto.start_point[0] + 1,
            line_end=end_node.end_point[0] + 1,
            signature=sig,
            exported=exported,
            parent_id=parent_id,
            byte_size=end_node.end_byte - fn_proto.start_byte,
            complexity=complexity,
        )
        self.symbols.append(sym)
        if parent_id:
            self.edges.append(Edge(EdgeKind.CONTAINS, parent_id, sym_id))
        if block is not None:
            self._scan_calls(block, sym_id)

    def _handle_zig_var(self, node: Node, *, exported: bool = False) -> None:
        """Extract a Zig struct/enum/union from a VarDecl containing a ContainerDecl."""
        name_node = self._zig_find_identifier(node)
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if not name:
            return

        container = self._zig_find_node(node, "ContainerDecl")
        if container is None:
            return

        kind = self._zig_container_kind(container)
        sym_id = self._make_id(name)

        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=kind, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=name, exported=exported,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)
        self._handle_zig_container_members(container, sym_id, name)

    def _handle_zig_container_members(
        self, container: Node, parent_id: str, parent_name: str,
    ) -> None:
        """Extract methods (Decl with FnProto) from a Zig container body."""
        prev_pub = False
        for child in container.children:
            if child.type == "pub":
                prev_pub = True
                continue
            if child.type == "Decl":
                fn_proto: Node | None = None
                block: Node | None = None
                for sub in child.children:
                    if sub.type == "FnProto":
                        fn_proto = sub
                    elif sub.type == "Block":
                        block = sub
                if fn_proto is not None:
                    self._handle_zig_fn(
                        fn_proto, block, exported=prev_pub,
                        parent_id=parent_id, parent_name=parent_name,
                    )
            prev_pub = False

    # ── Zig helpers ─────────────────────────────────────────

    @staticmethod
    def _zig_find_identifier(node: Node) -> Node | None:
        """Return first IDENTIFIER child of node (Zig uses ALL-CAPS token type)."""
        for child in node.children:
            if child.type == "IDENTIFIER":
                return child
        return None

    @staticmethod
    def _zig_find_node(node: Node, target_type: str) -> Node | None:
        """DFS search for the first node of target_type."""
        if node.type == target_type:
            return node
        for child in node.children:
            result = ZigHandlerMixin._zig_find_node(child, target_type)
            if result is not None:
                return result
        return None

    @staticmethod
    def _zig_container_kind(container: Node) -> SymbolKind:
        """Determine SymbolKind from ContainerDeclType child."""
        for child in container.children:
            if child.type == "ContainerDeclType":
                for kw in child.children:
                    if kw.type == "enum":
                        return SymbolKind.ENUM
                    if kw.type in ("union", "opaque"):
                        return SymbolKind.STRUCT
                # default: struct keyword or packed/extern prefix
                return SymbolKind.STRUCT
        return SymbolKind.STRUCT
