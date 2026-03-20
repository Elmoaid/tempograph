"""JavaScript/TypeScript language handler mixin for FileParser."""
from __future__ import annotations

from tree_sitter import Node

from ..types import Edge, EdgeKind, Language, Symbol, SymbolKind
from ._utils import _node_text, _first_comment_above, _extract_signature


class JSHandlerMixin:
    """Mixin providing JS/TS-specific parsing methods for FileParser."""

    # ── TypeScript / JavaScript / TSX / JSX ─────────────────

    def _handle_typescript(self, node: Node) -> None:
        self._handle_js_ts(node)

    def _handle_tsx(self, node: Node) -> None:
        self._handle_js_ts(node)

    def _handle_javascript(self, node: Node) -> None:
        self._handle_js_ts(node)

    def _handle_jsx(self, node: Node) -> None:
        self._handle_js_ts(node)

    def _handle_js_ts(self, node: Node) -> None:
        for child in node.children:
            t = child.type
            if t in ("import_statement", "import"):
                text = _node_text(child, self.source).strip()
                # Skip type-only imports — they have no runtime impact
                if text.startswith("import type "):
                    continue
                self.imports.append(text)
            elif t == "export_statement":
                self._handle_js_export(child)
            elif t in ("function_declaration", "generator_function_declaration"):
                self._handle_js_function(child)
            elif t == "class_declaration":
                self._handle_js_class(child)
            elif t in ("lexical_declaration", "variable_declaration"):
                self._handle_js_variable_declaration(child)
            elif t == "interface_declaration":
                self._handle_js_interface(child)
            elif t == "type_alias_declaration":
                self._handle_js_type_alias(child)
            elif t == "enum_declaration":
                self._handle_js_enum(child)
            elif t == "expression_statement":
                self._handle_js_ts(child)
            elif t == "assignment_expression":
                # Handle CommonJS exports: `module.exports = X` and `exports.X = Y`
                left = child.child_by_field_name("left")
                right = child.child_by_field_name("right")
                if left is not None and _node_text(left, self.source).startswith(("module.exports", "exports.")):
                    if right and right.type in ("class", "class_declaration"):
                        self._handle_js_class(right, exported=True)
                    elif right and right.type in ("function_expression", "generator_function_expression"):
                        # `module.exports = function override() {}` — named, or
                        # `exports.normalizeType = function(type) {}` — anonymous: use prop name
                        prop_name = None
                        left_text = _node_text(left, self.source)
                        if left_text.startswith("exports.") and "." not in left_text[8:]:
                            prop = left_text[len("exports."):]
                            if prop and not right.child_by_field_name("name"):
                                prop_name = prop
                        self._handle_js_function(right, exported=True, name_override=prop_name)
                    elif right and right.type == "identifier":
                        # `module.exports = fastify` — mark the named symbol as exported
                        self._cjs_exports.add(_node_text(right, self.source))
                    elif right and right.type == "object":
                        # `module.exports = { buildRouting, foo, bar }` — shorthand props
                        # `module.exports = { get header() {...}, redirect() {...} }` — method defs
                        for prop in right.children:
                            if prop.type == "shorthand_property_identifier":
                                self._cjs_exports.add(_node_text(prop, self.source))
                            elif prop.type == "method_definition":
                                # Inline method — treat as exported top-level function
                                self._handle_js_function(prop, exported=True)

    def _handle_js_export(self, node: Node) -> None:
        # Check if this is a re-export from another module: `export * from '...'` or `export { X } from '...'`
        has_source = any(c.type == "string" for c in node.children)
        if has_source:
            # Re-export: treat as an import edge so importers graph stays correct
            self.imports.append(_node_text(node, self.source).strip())
            return
        for child in node.children:
            t = child.type
            if t in ("function_declaration", "generator_function_declaration"):
                self._handle_js_function(child, exported=True)
            elif t == "class_declaration":
                self._handle_js_class(child, exported=True)
            elif t in ("lexical_declaration", "variable_declaration"):
                self._handle_js_variable_declaration(child, exported=True)
            elif t == "interface_declaration":
                self._handle_js_interface(child, exported=True)
            elif t == "type_alias_declaration":
                self._handle_js_type_alias(child, exported=True)
            elif t == "enum_declaration":
                self._handle_js_enum(child, exported=True)
            elif t == "identifier":
                # `export default settle` — mark pre-defined symbol as exported
                self._cjs_exports.add(_node_text(child, self.source))
            elif t == "export_clause":
                # `export { foo, bar }` — mark pre-defined symbols as exported
                for spec in child.children:
                    if spec.type == "export_specifier":
                        # `foo` or `foo as bar` — export the local name (first identifier)
                        local = spec.child_by_field_name("name") or (spec.children[0] if spec.children else None)
                        if local and local.type == "identifier":
                            self._cjs_exports.add(_node_text(local, self.source))

    def _handle_js_function(self, node: Node, *, exported: bool = False, name_override: str | None = None) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node and not name_override:
            return
        name = name_override if name_override else _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        kind = SymbolKind.FUNCTION
        # Detect React components (PascalCase + returns JSX)
        if name and name[0].isupper() and self.language in (Language.TSX, Language.JSX):
            kind = SymbolKind.COMPONENT
        # Detect hooks
        if name.startswith("use") and name[3:4].isupper():
            kind = SymbolKind.HOOK
        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=kind, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=_extract_signature(node, self.source, self.language),
            doc=doc, exported=exported,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
            complexity=self._compute_complexity(node),
        )
        self.symbols.append(sym)
        if self._current_parent_id():
            self.edges.append(Edge(EdgeKind.CONTAINS, self._current_parent_id(), sym_id))
        body = node.child_by_field_name("body")
        if body:
            self._symbol_stack.append(sym_id)
            self._scan_nested_js_declarations(body)
            self._symbol_stack.pop()
            self._scan_calls(body, sym_id)
            if kind == SymbolKind.COMPONENT:
                self._scan_jsx_renders(body, sym_id)

    def _handle_js_variable_declaration(self, node: Node, *, exported: bool = False) -> None:
        for child in node.children:
            if child.type != "variable_declarator":
                continue
            name_node = child.child_by_field_name("name")
            value_node = child.child_by_field_name("value")
            if not name_node:
                continue
            name = _node_text(name_node, self.source)
            if not name:
                continue
            sym_id = self._make_id(name)

            # const proto = module.exports = { get header() {...}, ... }
            if value_node and value_node.type == "assignment_expression":
                val_left = value_node.child_by_field_name("left")
                val_right = value_node.child_by_field_name("right")
                if (val_left is not None
                        and _node_text(val_left, self.source).startswith(("module.exports", "exports."))
                        and val_right and val_right.type == "object"):
                    for prop in val_right.children:
                        if prop.type == "method_definition":
                            self._handle_js_function(prop, exported=True)
                        elif prop.type == "shorthand_property_identifier":
                            self._cjs_exports.add(_node_text(prop, self.source))
                    continue  # skip creating a variable symbol for proto

            # Arrow function or function expression
            if value_node and value_node.type in ("arrow_function", "function_expression", "function"):
                kind = SymbolKind.FUNCTION
                if name[0].isupper() and self.language in (Language.TSX, Language.JSX):
                    kind = SymbolKind.COMPONENT
                if name.startswith("use") and name[3:4].isupper():
                    kind = SymbolKind.HOOK
                doc = _first_comment_above(node, self.source)
                sym = Symbol(
                    id=sym_id, name=name, qualified_name=name,
                    kind=kind, language=self.language,
                    file_path=self.file_path,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    signature=_extract_signature(node, self.source, self.language),
                    doc=doc, exported=exported,
                    parent_id=self._current_parent_id(),
                    byte_size=node.end_byte - node.start_byte,
                    complexity=self._compute_complexity(value_node),
                )
                self.symbols.append(sym)
                body = value_node.child_by_field_name("body")
                if body:
                    self._symbol_stack.append(sym_id)
                    self._scan_nested_js_declarations(body)
                    self._symbol_stack.pop()
                    self._scan_calls(body, sym_id)
                    if kind == SymbolKind.COMPONENT:
                        self._scan_jsx_renders(body, sym_id)
            elif name.isupper() or (value_node and value_node.type in ("string", "number", "true", "false")):
                sym = Symbol(
                    id=sym_id, name=name, qualified_name=name,
                    kind=SymbolKind.CONSTANT, language=self.language,
                    file_path=self.file_path,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    exported=exported,
                    parent_id=self._current_parent_id(),
                )
                self.symbols.append(sym)
            else:
                sym = Symbol(
                    id=sym_id, name=name, qualified_name=name,
                    kind=SymbolKind.VARIABLE, language=self.language,
                    file_path=self.file_path,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    exported=exported,
                    parent_id=self._current_parent_id(),
                    byte_size=node.end_byte - node.start_byte,
                )
                self.symbols.append(sym)

    def _scan_nested_js_declarations(self, node: Node, *, depth: int = 0) -> None:
        """Scan a function body for nested function/const declarations.
        Catches React patterns: const handleFoo = useCallback(...),
        const handleBar = () => {...}, but skips array destructuring
        like const [show, setShow] = useState(false)."""
        if depth > 3:
            return
        for child in node.children:
            t = child.type
            if t in ("lexical_declaration", "variable_declaration"):
                for decl in child.children:
                    if decl.type != "variable_declarator":
                        continue
                    name_node = decl.child_by_field_name("name")
                    value_node = decl.child_by_field_name("value")
                    if not name_node:
                        continue
                    # Skip destructuring patterns — these are state/context vars, not functions
                    if name_node.type in ("array_pattern", "object_pattern"):
                        continue
                    name = _node_text(name_node, self.source)
                    # Only extract named functions, hooks, and handlers
                    is_func = value_node and value_node.type in (
                        "arrow_function", "function_expression", "function",
                        "call_expression",  # useCallback, useMemo wrapping arrows
                    )
                    if not is_func:
                        continue
                    sym_id = self._make_id(name)
                    kind = SymbolKind.FUNCTION
                    if name.startswith("use") and name[3:4].isupper():
                        kind = SymbolKind.HOOK
                    doc = _first_comment_above(child, self.source)
                    pqname = self._parent_qualified_name()
                    sym = Symbol(
                        id=sym_id, name=name,
                        qualified_name=f"{pqname}.{name}" if pqname else name,
                        kind=kind, language=self.language,
                        file_path=self.file_path,
                        line_start=child.start_point[0] + 1,
                        line_end=child.end_point[0] + 1,
                        signature=_extract_signature(child, self.source, self.language),
                        doc=doc,
                        parent_id=self._current_parent_id(),
                        byte_size=child.end_byte - child.start_byte,
                    )
                    self.symbols.append(sym)
                    self.edges.append(Edge(EdgeKind.CONTAINS, self._current_parent_id(), sym_id))
                    # Scan nested function body for calls and renders
                    body = self._find_arrow_body(value_node)
                    if body:
                        self._symbol_stack.append(sym_id)
                        self._scan_calls(body, sym_id)
                        if self.language in (Language.TSX, Language.JSX):
                            self._scan_jsx_renders(body, sym_id)
                        self._scan_nested_js_declarations(body, depth=depth + 1)
                        self._symbol_stack.pop()
            elif t in ("function_declaration",):
                self._handle_js_function(child)
            elif t in ("if_statement", "for_statement", "while_statement",
                        "try_statement", "switch_statement"):
                self._scan_nested_js_declarations(child, depth=depth + 1)
            elif t == "statement_block":
                self._scan_nested_js_declarations(child, depth=depth + 1)

    def _find_arrow_body(self, node: Node) -> Node | None:
        """Find the body of an arrow function, including through useCallback wrappers."""
        if node is None:
            return None
        if node.type in ("arrow_function", "function_expression", "function"):
            return node.child_by_field_name("body")
        # useCallback((...) => {...}, [...]) — unwrap the call
        if node.type == "call_expression":
            args = node.child_by_field_name("arguments")
            if args:
                for arg in args.children:
                    if arg.type in ("arrow_function", "function_expression"):
                        return arg.child_by_field_name("body")
        return None

    def _handle_js_class(self, node: Node, *, exported: bool = False) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        doc = _first_comment_above(node, self.source)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.CLASS, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=_extract_signature(node, self.source, self.language),
            doc=doc, exported=exported,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

        # Scan for extends/implements heritage clauses
        for child in node.children:
            if child.type in ("class_heritage", "extends_clause"):
                for sub in child.children:
                    if sub.type == "extends_clause":
                        for ext_child in sub.children:
                            if ext_child.type in ("identifier", "member_expression"):
                                target = _node_text(ext_child, self.source)
                                if target and target[0].isupper():
                                    self.edges.append(Edge(EdgeKind.INHERITS, sym_id, target, node.start_point[0] + 1))
                                break
                    elif sub.type == "implements_clause":
                        for impl_child in sub.children:
                            if impl_child.type in ("identifier", "type_identifier", "generic_type"):
                                target = _node_text(impl_child, self.source)
                                if "<" in target:
                                    target = target.split("<")[0]
                                if target and target[0].isupper():
                                    self.edges.append(Edge(EdgeKind.IMPLEMENTS, sym_id, target, node.start_point[0] + 1))
                    elif sub.type in ("identifier", "member_expression"):
                        target = _node_text(sub, self.source)
                        if target and target[0].isupper():
                            self.edges.append(Edge(EdgeKind.INHERITS, sym_id, target, node.start_point[0] + 1))

        body = node.child_by_field_name("body")
        if body:
            self._symbol_stack.append(sym_id)
            for child in body.children:
                if child.type in ("method_definition", "public_field_definition"):
                    self._handle_js_method(child)
            self._symbol_stack.pop()

    def _handle_js_method(self, node: Node) -> None:
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
            complexity=self._compute_complexity(node),
        )
        self.symbols.append(sym)
        self.edges.append(Edge(EdgeKind.CONTAINS, self._current_parent_id(), sym_id))
        body = node.child_by_field_name("body")
        if body:
            self._scan_calls(body, sym_id)

    def _handle_js_interface(self, node: Node, *, exported: bool = False) -> None:
        name_node = node.child_by_field_name("name")
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
            exported=exported,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

        # Scan for extends heritage on interfaces
        for child in node.children:
            if child.type in ("extends_type_clause", "extends_clause"):
                for sub in child.children:
                    if sub.type in ("identifier", "type_identifier", "generic_type"):
                        target = _node_text(sub, self.source)
                        if "<" in target:
                            target = target.split("<")[0]
                        if target and target[0].isupper():
                            self.edges.append(Edge(EdgeKind.INHERITS, sym_id, target, node.start_point[0] + 1))

    def _handle_js_type_alias(self, node: Node, *, exported: bool = False) -> None:
        name_node = node.child_by_field_name("name")
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
            exported=exported,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    def _handle_js_enum(self, node: Node, *, exported: bool = False) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.source)
        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.ENUM, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=exported,
            parent_id=self._current_parent_id(),
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    def _scan_jsx_renders(self, node: Node, from_id: str) -> None:
        """Scan for JSX component references like <FooBar />."""
        if node.type in ("jsx_element", "jsx_self_closing_element"):
            opening = node.child_by_field_name("name") or (
                node.children[0] if node.children else None
            )
            if opening:
                # For jsx_element, look inside jsx_opening_element
                if opening.type == "jsx_opening_element":
                    name_node = opening.child_by_field_name("name")
                    if name_node:
                        tag = _node_text(name_node, self.source)
                        if tag[0:1].isupper():  # Component, not html tag
                            self.edges.append(Edge(EdgeKind.RENDERS, from_id, tag, node.start_point[0] + 1))
                elif opening.type == "identifier":
                    tag = _node_text(opening, self.source)
                    if tag[0:1].isupper():
                        self.edges.append(Edge(EdgeKind.RENDERS, from_id, tag, node.start_point[0] + 1))
        for child in node.children:
            self._scan_jsx_renders(child, from_id)
