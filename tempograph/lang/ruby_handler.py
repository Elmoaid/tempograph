"""Ruby language handler mixin for FileParser."""
from __future__ import annotations

from tree_sitter import Node

from ..types import Edge, EdgeKind, Symbol, SymbolKind
from ._utils import _node_text, _extract_signature


class RubyHandlerMixin:
    """Mixin providing Ruby-specific parsing methods for FileParser."""

    # ── Ruby ──────────────────────────────────────────────

    def _handle_ruby(self, node: Node) -> None:
        for child in node.children:
            t = child.type
            if t == "call" and child.children:
                first = child.children[0]
                if first.type == "identifier":
                    name = _node_text(first, self.source)
                    if name in ("require", "require_relative"):
                        self.imports.append(_node_text(child, self.source).strip())
                        continue
            if t == "class":
                self._handle_ruby_class(child)
            elif t == "module":
                self._handle_ruby_module(child)
            elif t in ("method", "singleton_method"):
                self._handle_ruby_method(child, None, "")
            elif t == "body_statement":
                self._handle_ruby(child)

    def _handle_ruby_class(self, node: Node) -> None:
        name_node = None
        superclass = None
        for child in node.children:
            if child.type == "constant":
                name_node = child
            elif child.type == "superclass":
                for sc in child.children:
                    if sc.type in ("constant", "scope_resolution"):
                        superclass = _node_text(sc, self.source)
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
            exported=True,
            byte_size=node.end_byte - node.start_byte,
            complexity=self._compute_complexity(node),
        )
        self.symbols.append(sym)
        if superclass:
            self.edges.append(Edge(EdgeKind.INHERITS, sym_id, superclass, node.start_point[0] + 1))

        # Process body
        self._symbol_stack.append(sym_id)
        for child in node.children:
            if child.type == "body_statement":
                for sub in child.children:
                    if sub.type == "method":
                        self._handle_ruby_method(sub, sym_id, name)
                    elif sub.type == "singleton_method":
                        self._handle_ruby_method(sub, sym_id, name)
                    elif sub.type == "class":
                        self._handle_ruby_class(sub)
                    elif sub.type == "module":
                        self._handle_ruby_module(sub)
        self._symbol_stack.pop()

    def _handle_ruby_module(self, node: Node) -> None:
        name_node = None
        for child in node.children:
            if child.type == "constant":
                name_node = child
                break
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
            exported=True,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

        self._symbol_stack.append(sym_id)
        for child in node.children:
            if child.type == "body_statement":
                for sub in child.children:
                    if sub.type in ("method", "singleton_method"):
                        self._handle_ruby_method(sub, sym_id, name)
                    elif sub.type == "class":
                        self._handle_ruby_class(sub)
                    elif sub.type == "module":
                        self._handle_ruby_module(sub)
        self._symbol_stack.pop()

    def _handle_ruby_method(self, node: Node, class_id: str | None, class_name: str) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            for c in node.children:
                if c.type == "identifier":
                    name_node = c
                    break
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if class_name:
            qualified = f"{class_name}.{name}"
            sym_id = f"{self.file_path}::{qualified}"
        else:
            qualified = name
            sym_id = self._make_id(name)

        sym = Symbol(
            id=sym_id, name=name, qualified_name=qualified,
            kind=SymbolKind.METHOD if class_id else SymbolKind.FUNCTION,
            language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=_extract_signature(node, self.source, self.language),
            parent_id=class_id,
            exported=not name.startswith("_"),
            byte_size=node.end_byte - node.start_byte,
            complexity=self._compute_complexity(node),
        )
        self.symbols.append(sym)
        if class_id:
            self.edges.append(Edge(EdgeKind.CONTAINS, class_id, sym_id))
        body = node.child_by_field_name("body")
        if body:
            self._scan_calls(body, sym_id)
