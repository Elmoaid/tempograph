"""Clojure language handler mixin for FileParser."""
from __future__ import annotations

from ..types import Edge, EdgeKind, Symbol, SymbolKind

try:
    from tree_sitter import Node
except ImportError:
    Node = object  # type: ignore[assignment,misc]


def _node_text(node: "Node", source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


_DEF_FORMS = {"defn", "defn-", "defmacro", "def"}
_CLASS_FORMS = {"defrecord", "deftype"}
_IMPORT_KEYWORDS = {"require", "use", "import"}


class ClojureHandlerMixin:
    """Clojure parser: extracts ns, defn, def, defmacro, defprotocol, defrecord/deftype, imports."""

    def _handle_clojure(self, node: "Node") -> None:
        for child in node.children:
            if child.type == "list_lit":
                self._clojure_handle_list(child)

    def _clojure_handle_list(self, node: "Node") -> None:
        children = [c for c in node.children if c.type not in ("(", ")", "meta_lit")]
        if not children:
            return
        head = children[0]
        if head.type != "sym_lit":
            return
        form = _node_text(head, self.source)
        if form == "ns":
            self._clojure_handle_ns(node, children)
        elif form in _DEF_FORMS:
            self._clojure_handle_def(node, children, form)
        elif form == "defprotocol":
            self._clojure_handle_named(node, children, SymbolKind.INTERFACE)
        elif form in _CLASS_FORMS:
            self._clojure_handle_named(node, children, SymbolKind.CLASS)

    def _clojure_handle_ns(self, node: "Node", children: list) -> None:
        if len(children) < 2 or children[1].type != "sym_lit":
            return
        name = _node_text(children[1], self.source)
        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.MODULE, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=True,
            parent_id=None,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)
        # Extract imports from :require / :use / :import clauses
        for child in node.children:
            if child.type == "list_lit":
                self._clojure_handle_import_clause(child)

    def _clojure_handle_import_clause(self, node: "Node") -> None:
        children = [c for c in node.children if c.type not in ("(", ")")]
        if not children or children[0].type != "kwd_lit":
            return
        kwd_text = _node_text(children[0], self.source)
        # Strip leading colon
        kwd = kwd_text.lstrip(":")
        if kwd not in _IMPORT_KEYWORDS:
            return
        for child in children[1:]:
            if child.type == "vec_lit":
                # [namespace.name ...] — first sym_lit is the namespace
                for vc in child.children:
                    if vc.type == "sym_lit":
                        self.imports.append(_node_text(vc, self.source))
                        break
            elif child.type == "sym_lit":
                # Bare symbol: (:require clojure.string)
                self.imports.append(_node_text(child, self.source))

    def _clojure_handle_def(self, node: "Node", children: list, form: str) -> None:
        if len(children) < 2 or children[1].type != "sym_lit":
            return
        name = _node_text(children[1], self.source)
        exported = form != "defn-" and not name.startswith("_")
        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=SymbolKind.FUNCTION, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=exported,
            parent_id=None,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)

    def _clojure_handle_named(self, node: "Node", children: list, kind: SymbolKind) -> None:
        if len(children) < 2 or children[1].type != "sym_lit":
            return
        name = _node_text(children[1], self.source)
        sym_id = self._make_id(name)
        sym = Symbol(
            id=sym_id, name=name, qualified_name=name,
            kind=kind, language=self.language,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            exported=not name.startswith("_"),
            parent_id=None,
            byte_size=node.end_byte - node.start_byte,
        )
        self.symbols.append(sym)
