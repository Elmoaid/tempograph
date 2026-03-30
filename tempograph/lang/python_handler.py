"""Python language handler mixin for FileParser."""
from __future__ import annotations

from tree_sitter import Node

from ..types import Edge, EdgeKind, Symbol, SymbolKind
from ._utils import _node_text, _first_comment_above, _extract_signature


class PythonHandlerMixin:
    """Mixin providing Python-specific parsing methods for FileParser."""

    # ── Python ──────────────────────────────────────────────

    def _handle_python(self, node: Node) -> None:
        for child in node.children:
            if child.type == "import_statement":
                self.imports.append(_node_text(child, self.source).strip())
            elif child.type == "import_from_statement":
                self.imports.append(_node_text(child, self.source).strip())
            elif child.type == "class_definition":
                self._handle_python_class(child)
            elif child.type == "function_definition":
                self._handle_python_function(child, is_method=False)
            elif child.type == "decorated_definition":
                decorators = self._extract_python_decorators(child)
                inner = child.children[-1] if child.children else None
                if inner:
                    if inner.type == "class_definition":
                        self._handle_python_class(inner, decorators=decorators)
                    elif inner.type == "function_definition":
                        self._handle_python_function(inner, is_method=bool(self._symbol_stack), decorators=decorators)
            elif child.type in ("expression_statement",):
                self._scan_python_assignments(child)
            elif child.type == "if_statement":
                self._handle_python(child)  # recurse into if __name__ blocks etc
            else:
                pass

    def _handle_python_class(self, node: Node, *, decorators: list[str] | None = None) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        doc = _first_comment_above(node, self.source)
        # Check for docstring inside class body
        if not doc:
            body = node.child_by_field_name("body")
            if body and body.children:
                first = body.children[0]
                if first.type == "expression_statement" and first.children:
                    expr = first.children[0]
                    if expr.type == "string":
                        doc = _node_text(expr, self.source).strip("'\"").split("\n")[0].strip()[:200]

        # Python convention: _prefixed top-level names are private
        is_top_level = not self._symbol_stack
        exported = not name.startswith("_") if is_top_level else True

        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.CLASS, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=_extract_signature(node, self.source, self.language),
            doc=doc,
            parent_id=self._current_parent_id(),
            exported=exported,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)
        if self._current_parent_id():
            self.edges.append(Edge(EdgeKind.CONTAINS, self._current_parent_id(), sym_id))

        # Decorator dispatch edges for classes
        _SKIP_CLASS_DECS = ("dataclass", "dataclasses.dataclass", "typing.final", "final")
        for dec_name in (decorators or []):
            if dec_name in _SKIP_CLASS_DECS or dec_name.startswith("_"):
                continue
            self.edges.append(Edge(
                EdgeKind.CALLS, dec_name, sym_id, node.start_point[0],
            ))

        # Parse superclasses
        superclasses = node.child_by_field_name("superclasses")
        if superclasses:
            for arg in superclasses.children:
                if arg.type == "identifier":
                    target = _node_text(arg, self.source)
                    self.edges.append(Edge(EdgeKind.INHERITS, sym_id, target, node.start_point[0] + 1))

        # Recurse into class body
        body = node.child_by_field_name("body")
        if body:
            self._symbol_stack.append(sym_id)
            for child in body.children:
                if child.type == "function_definition":
                    self._handle_python_function(child, is_method=True)
                elif child.type == "decorated_definition":
                    decorators = self._extract_python_decorators(child)
                    inner = child.children[-1] if child.children else None
                    if inner and inner.type == "function_definition":
                        self._handle_python_function(inner, is_method=True, decorators=decorators)
                elif child.type == "class_definition":
                    self._handle_python_class(child)
            self._symbol_stack.pop()

    @staticmethod
    def _extract_python_decorators(decorated_node: Node) -> list[str]:
        """Extract decorator names from a decorated_definition node."""
        decorators = []
        for child in decorated_node.children:
            if child.type == "decorator":
                # decorator children: "@", identifier or call
                for sub in child.children:
                    if sub.type == "identifier":
                        decorators.append(sub.text.decode("utf-8", errors="replace"))
                    elif sub.type == "call":
                        fn = sub.child_by_field_name("function")
                        if fn:
                            decorators.append(fn.text.decode("utf-8", errors="replace"))
                    elif sub.type == "attribute":
                        decorators.append(sub.text.decode("utf-8", errors="replace"))
        return decorators

    def _handle_python_function(self, node: Node, *, is_method: bool, decorators: list[str] | None = None) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        # Determine kind from decorators
        decs = set(decorators or [])
        if "property" in decs:
            kind = SymbolKind.PROPERTY
        elif any(d for d in decs if ".route" in d or d == "route"):
            kind = SymbolKind.ROUTE
        elif name.startswith("test_") or any("pytest.mark" in d for d in decs):
            kind = SymbolKind.TEST
        elif "staticmethod" in decs or "classmethod" in decs:
            kind = SymbolKind.FUNCTION
        elif is_method:
            kind = SymbolKind.METHOD
        else:
            kind = SymbolKind.FUNCTION
        doc = _first_comment_above(node, self.source)
        if not doc:
            body = node.child_by_field_name("body")
            if body and body.children:
                first = body.children[0]
                if first.type == "expression_statement" and first.children:
                    expr = first.children[0]
                    if expr.type == "string":
                        doc = _node_text(expr, self.source).strip("'\"").split("\n")[0].strip()[:200]

        # Python convention: _prefixed top-level names are private
        is_top_level = not self._symbol_stack
        exported = not name.startswith("_") if is_top_level else True

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
            exported=exported,
            byte_size=node.end_byte - node.start_byte,
            complexity=self._compute_complexity(node),
        )
        self.symbols.append(sym)
        if self._current_parent_id():
            self.edges.append(Edge(EdgeKind.CONTAINS, self._current_parent_id(), sym_id))

        # Decorator dispatch edges — makes framework-dispatched functions visible in the graph
        _SKIP_FN_DECS = (
            "property", "staticmethod", "classmethod", "abstractmethod",
            "override", "dataclass", "dataclasses.dataclass",
            "typing.overload", "overload",
            "functools.wraps", "wraps",
            "functools.lru_cache", "lru_cache",
            "functools.cache", "cache",
            "functools.cached_property", "cached_property",
        )
        for dec_name in (decorators or []):
            if dec_name in _SKIP_FN_DECS or dec_name.startswith("_"):
                continue
            self.edges.append(Edge(
                EdgeKind.CALLS, dec_name, sym_id, node.start_point[0],
            ))

        # Scan function body for calls
        body = node.child_by_field_name("body")
        if body:
            self._scan_calls(body, sym_id)

        # Scan type annotations for USES_TYPE edges
        self._scan_type_annotations(node, sym_id)

    def _scan_python_assignments(self, node: Node) -> None:
        for child in node.children:
            if child.type == "assignment":
                left = child.child_by_field_name("left")
                if left and left.type == "identifier":
                    name = _node_text(left, self.source)
                    # Detect __all__ = ["name1", "name2", ...]
                    if name == "__all__" and not self._symbol_stack:
                        right = child.child_by_field_name("right")
                        if right and right.type == "list":
                            names = []
                            for elem in right.children:
                                if elem.type == "string":
                                    names.append(_node_text(elem, self.source).strip("'\""))
                            if names:
                                self._dunder_all = names
                    elif name.isupper() or name.startswith("_") and name[1:].isupper():
                        sym_id = self._make_id(name)
                        is_top_level = not self._symbol_stack
                        exported = not name.startswith("_") if is_top_level else True
                        sym = Symbol(
                            id=sym_id, name=name, qualified_name=name,
                            kind=SymbolKind.CONSTANT, language=self.language,
                            file_path=self.file_path,
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            parent_id=self._current_parent_id(),
                            exported=exported,
                        )
                        self.symbols.append(sym)
