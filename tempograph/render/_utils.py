from __future__ import annotations

from pathlib import Path

import tiktoken

from ..types import Tempo, Symbol, SymbolKind

_ENC = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_ENC.encode(text))


_MONOLITH_THRESHOLD = 1000

_DISPATCH_PATTERNS = ("handle_", "on_", "test_", "route", "command", "hook", "middleware", "plugin")


_TEST_FILE_SUFFIXES = (".test.ts", ".test.tsx", ".test.js", ".spec.ts", ".spec.tsx", ".spec.js")


def _is_test_file(file_path: str) -> bool:
    """Return True if file_path looks like a test/spec file."""
    name = Path(file_path).name
    return (
        (name.startswith("test_") and name.endswith(".py"))
        or name.endswith("_test.py")
        or any(name.endswith(sfx) for sfx in _TEST_FILE_SUFFIXES)
    )

def _dead_code_confidence(sym: Symbol, graph: Tempo) -> int:
    """Score 0-100: how confident we are this symbol is truly dead."""
    score = 0

    # Test files: symbols are test infrastructure discovered by runners, not dead code
    if _is_test_file(sym.file_path):
        score -= 50

    # No callers at all (even same-file) — strong signal
    if not graph.callers_of(sym.id):
        score += 30

    # Parent file has no importers — nothing depends on this file
    if not graph.importers_of(sym.file_path):
        score += 25

    # No render relationships
    if not graph.renderers_of(sym.id):
        score += 10

    # Larger symbols are higher-value cleanup targets
    if sym.line_count > 50:
        score += 15

    # Name looks like a dispatch target — likely wired at runtime
    name_lower = sym.name.lower()
    if any(name_lower.startswith(p) or p in name_lower for p in _DISPATCH_PATTERNS):
        score -= 20

    # Plugin entrypoint: function named 'run' in a plugins/ directory (called via dynamic dispatch)
    if sym.name == "run" and "/plugins/" in sym.file_path:
        score -= 30

    # Tauri command — invoked via IPC from frontend, static analysis can't see callers
    if sym.kind == SymbolKind.COMMAND:
        score -= 40

    # Has docstring — suggests intentional public API
    if sym.doc:
        score -= 15

    # Parent is not cross-file referenced — parent already dead, this is redundant noise
    if sym.parent_id and not graph.callers_of(sym.parent_id):
        score -= 10

    # Single-component file — likely lazy-loaded, lower confidence
    if sym.kind == SymbolKind.COMPONENT and sym.exported:
        siblings = [
            s for s in graph.symbols.values()
            if s.file_path == sym.file_path and s.kind == SymbolKind.COMPONENT
        ]
        if len(siblings) == 1:
            score -= 20

    return max(0, min(100, score))
