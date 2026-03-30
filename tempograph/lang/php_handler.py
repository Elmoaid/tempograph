"""PHP language handler mixin for FileParser."""
from __future__ import annotations

from tree_sitter import Node

from ..types import Edge, EdgeKind, Symbol, SymbolKind
from ._utils import _node_text, _first_comment_above, _extract_signature


class PHPHandlerMixin:
    """Mixin providing PHP-specific parsing methods for FileParser."""

    # ── PHP entry point ─────────────────────────────────────

    def _handle_php(self, node: Node) -> None:
        for child in node.children:
            t = child.type
            if t == "php_tag":
                continue
            elif t == "namespace_use_declaration":
                self.imports.append(_node_text(child, self.source).strip())
            elif t == "function_definition":
                self._handle_php_function(child)
            elif t == "class_declaration":
                self._handle_php_class(child)
            elif t == "interface_declaration":
                self._handle_php_interface(child)
            elif t == "trait_declaration":
                self._handle_php_trait(child)
            elif t == "namespace_definition":
                pass  # transparent — namespace doesn't create a symbol

    # ── PHP free function ──────────────────────────────────

    def _handle_php_function(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
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
            kind=SymbolKind.FUNCTION, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=_extract_signature(node, self.source, self.language),
            doc=doc,
            exported=True,  # all top-level PHP functions are public
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
            complexity=self._compute_complexity(node),
        )
        self.symbols.append(sym)
        if self._current_parent_id():
            self.edges.append(Edge(EdgeKind.CONTAINS, self._current_parent_id(), sym_id))
        body = node.child_by_field_name("body")
        if body:
            self._php_scan_calls(body, sym_id)

    # ── PHP method ─────────────────────────────────────────

    def _handle_php_method(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if not name:
            return
        sym_id = self._make_id(name)

        # Determine visibility from modifier children
        exported = True  # default (no modifier = public in PHP)
        for child in node.children:
            if child.type == "visibility_modifier":
                vis = _node_text(child, self.source)
                if vis in ("private", "protected"):
                    exported = False
                break

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
            exported=exported,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
            complexity=self._compute_complexity(node),
        )
        self.symbols.append(sym)
        if self._current_parent_id():
            self.edges.append(Edge(EdgeKind.CONTAINS, self._current_parent_id(), sym_id))
        body = node.child_by_field_name("body")
        if body:
            self._php_scan_calls(body, sym_id)

    # ── PHP class ──────────────────────────────────────────

    def _handle_php_class(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if not name:
            return
        sym_id = self._make_id(name)
        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.CLASS, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=name,
            doc=doc,
            exported=True,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
            complexity=self._compute_complexity(node),
        )
        self.symbols.append(sym)

        # Extract extends (INHERITS) and implements (IMPLEMENTS) edges
        for child in node.children:
            if child.type == "base_clause":
                for c in child.children:
                    if c.type == "name":
                        self.edges.append(Edge(
                            EdgeKind.INHERITS, sym_id, _node_text(c, self.source),
                            node.start_point[0] + 1,
                        ))
            elif child.type == "class_interface_clause":
                for c in child.children:
                    if c.type == "name":
                        self.edges.append(Edge(
                            EdgeKind.IMPLEMENTS, sym_id, _node_text(c, self.source),
                            node.start_point[0] + 1,
                        ))

        # Walk body for properties and methods
        body = node.child_by_field_name("body")
        if body:
            self._symbol_stack.append(sym_id)
            for child in body.children:
                if child.type == "method_declaration":
                    self._handle_php_method(child)
                elif child.type == "property_declaration":
                    self._handle_php_property(child, sym_id)
            self._symbol_stack.pop()

    # ── PHP interface ──────────────────────────────────────

    def _handle_php_interface(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if not name:
            return
        sym_id = self._make_id(name)
        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.INTERFACE, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=name,
            doc=doc,
            exported=True,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

        body = node.child_by_field_name("body")
        if body:
            self._symbol_stack.append(sym_id)
            for child in body.children:
                if child.type == "method_declaration":
                    self._handle_php_method(child)
            self._symbol_stack.pop()

    # ── PHP trait ──────────────────────────────────────────

    def _handle_php_trait(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        if not name:
            return
        sym_id = self._make_id(name)
        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.TRAIT, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=name,
            doc=doc,
            exported=True,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

        body = node.child_by_field_name("body")
        if body:
            self._symbol_stack.append(sym_id)
            for child in body.children:
                if child.type == "method_declaration":
                    self._handle_php_method(child)
                elif child.type == "property_declaration":
                    self._handle_php_property(child, sym_id)
            self._symbol_stack.pop()

    # ── PHP property ───────────────────────────────────────

    def _handle_php_property(self, node: Node, parent_id: str) -> None:
        """Extract a class/trait property_declaration node as a VARIABLE symbol."""
        # Collect modifier/type info from children before property_element
        exported = True  # default: public
        type_name: str | None = None
        prop_elem: Node | None = None

        _TYPE_NODES = {
            "primitive_type", "named_type", "union_type",
            "intersection_type", "nullable_type",
        }

        for child in node.children:
            t = child.type
            if t == "visibility_modifier":
                vis = _node_text(child, self.source)
                if vis in ("private", "protected"):
                    exported = False
            elif t in _TYPE_NODES:
                type_name = _node_text(child, self.source)
            elif t == "property_element":
                prop_elem = child

        if prop_elem is None:
            return

        # property_element → variable_name → name
        var_node = None
        for child in prop_elem.children:
            if child.type == "variable_name":
                var_node = child
                break
        if var_node is None:
            return

        # Strip leading $
        raw = _node_text(var_node, self.source)
        name = raw.lstrip("$")
        if not name:
            return

        sym_id = self._make_id(name)
        sig = f"${name}" + (f": {type_name}" if type_name else "")
        sym = Symbol(
            id=sym_id, name=name,
            qualified_name=f"{self._parent_qualified_name()}.{name}" if self._symbol_stack else name,
            kind=SymbolKind.VARIABLE, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=sig,
            exported=exported,
            parent_id=parent_id,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)
        self.edges.append(Edge(EdgeKind.CONTAINS, parent_id, sym_id))

    # ── PHP call scanning ──────────────────────────────────

    def _php_scan_calls(self, node: Node, from_id: str, *, depth: int = 0) -> None:
        """Scan for PHP call expressions: function_call_expression, member_call_expression, scoped_call_expression."""
        if depth > 20:
            return
        t = node.type
        if t == "function_call_expression":
            # greet('world') → name node is first child (field "function")
            func_node = node.child_by_field_name("function")
            if not func_node:
                func_node = node.children[0] if node.children else None
            if func_node:
                raw = _node_text(func_node, self.source)
                if raw and raw not in self._ignore_set:
                    self.edges.append(Edge(
                        EdgeKind.CALLS, from_id, raw,
                        node.start_point[0] + 1,
                    ))
            # Recurse into arguments
            args = node.child_by_field_name("arguments")
            if args:
                for child in args.children:
                    self._php_scan_calls(child, from_id, depth=depth + 1)
            return
        elif t == "member_call_expression":
            # $this->method() or $obj->method()
            name_node = node.child_by_field_name("name")
            if name_node:
                raw = _node_text(name_node, self.source)
                if raw and raw not in self._ignore_set:
                    self.edges.append(Edge(
                        EdgeKind.CALLS, from_id, raw,
                        node.start_point[0] + 1,
                    ))
            args = node.child_by_field_name("arguments")
            if args:
                for child in args.children:
                    self._php_scan_calls(child, from_id, depth=depth + 1)
            return
        elif t == "scoped_call_expression":
            # ClassName::staticMethod()
            name_node = node.child_by_field_name("name")
            scope_node = node.child_by_field_name("scope")
            if name_node:
                method_name = _node_text(name_node, self.source)
                if scope_node:
                    scope_name = _node_text(scope_node, self.source)
                    qualified = f"{scope_name}.{method_name}"
                    self.edges.append(Edge(
                        EdgeKind.CALLS, from_id, qualified,
                        node.start_point[0] + 1,
                    ))
                elif method_name and method_name not in self._ignore_set:
                    self.edges.append(Edge(
                        EdgeKind.CALLS, from_id, method_name,
                        node.start_point[0] + 1,
                    ))
            args = node.child_by_field_name("arguments")
            if args:
                for child in args.children:
                    self._php_scan_calls(child, from_id, depth=depth + 1)
            return
        # Default: recurse into children
        for child in node.children:
            self._php_scan_calls(child, from_id, depth=depth + 1)
