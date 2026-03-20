"""Shared tree-sitter utility functions used by language handler mixins."""
from __future__ import annotations

from tree_sitter import Node

from ..types import Language


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _first_comment_above(node: Node, source: bytes) -> str:
    """Extract the first doc comment line above a node."""
    prev = node.prev_named_sibling
    if prev and prev.type in ("comment", "line_comment", "block_comment", "string"):
        text = _node_text(prev, source).strip()
        for prefix in ("///", "//!", "//", "#", "/*", "*/", '"""', "'''"):
            text = text.removeprefix(prefix)
        for suffix in ("*/", '"""', "'''"):
            text = text.removesuffix(suffix)
        text = text.strip()
        first_line = text.split("\n")[0].strip()
        if first_line:
            return first_line[:200]
    return ""


def _extract_signature(node: Node, source: bytes, lang: Language) -> str:
    """Extract a compact function/method signature."""
    text = _node_text(node, source)
    first_line = text.split("\n")[0].strip()
    first_line = first_line.rstrip("{").rstrip()
    if len(first_line) > 200:
        first_line = first_line[:200] + "..."
    return first_line
