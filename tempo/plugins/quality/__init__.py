"""Quality scorer — rate code output against "right, once, small" standard.

Analyzes code changes and scores them on:
- Correctness signals (test coverage, type safety)
- Minimality (LOC added vs problem complexity)
- Maintainability (coupling, complexity of new code)
- Convention adherence (matches existing patterns)
"""
from __future__ import annotations

PLUGIN = {
    "name": "quality",
    "depends": ["hotspots", "dead_code"],
    "provides": ["quality"],
    "default": True,
    "description": "Output quality scoring — rate code against 'right, once, small'",
}


def run(graph, *, file: str = "", **kwargs) -> str:
    return score_quality(graph, file)


def score_quality(graph, target_file: str = "") -> str:
    """Score codebase or specific file quality."""
    from tempograph.render import render_hotspots, render_dead_code

    scores = {}

    # Minimality: dead code ratio (exclude archive/ and vendor dirs)
    _skip = ("archive/", "bench/", "node_modules/", ".git/", "dist/", "build/")
    dead = [s for s in graph.find_dead_code()
            if not any(s.file_path.startswith(p) for p in _skip)]
    total_exported = sum(1 for s in graph.symbols.values()
                         if s.exported and not any(s.file_path.startswith(p) for p in _skip))
    dead_ratio = len(dead) / max(total_exported, 1)
    scores["minimality"] = max(0, 100 - int(dead_ratio * 200))

    # Complexity: average symbol complexity
    complexities = [s.complexity for s in graph.symbols.values() if s.complexity > 0]
    avg_cx = sum(complexities) / max(len(complexities), 1)
    scores["simplicity"] = max(0, 100 - int(avg_cx * 3))

    # Coupling: files with >20 cross-file dependencies
    high_coupling = 0
    for fpath, finfo in graph.files.items():
        importers = len(graph.importers_of(fpath))
        if importers > 20:
            high_coupling += 1
    coupling_ratio = high_coupling / max(len(graph.files), 1)
    scores["independence"] = max(0, 100 - int(coupling_ratio * 500))

    # Convention: consistency of naming patterns
    scores["convention"] = _score_naming_consistency(graph)

    overall = sum(scores.values()) // len(scores)

    lines = [
        f"Quality Score: {overall}/100",
        "",
        f"  Minimality:    {scores['minimality']:>3}/100  (dead code ratio: {dead_ratio:.1%})",
        f"  Simplicity:    {scores['simplicity']:>3}/100  (avg complexity: {avg_cx:.1f})",
        f"  Independence:  {scores['independence']:>3}/100  ({high_coupling} high-coupling files)",
        f"  Convention:    {scores['convention']:>3}/100  (naming consistency)",
        "",
        f"Standard: \"Right, once, small.\"",
    ]

    if target_file and target_file in graph.files:
        file_syms = [s for s in graph.symbols.values() if s.file_path == target_file]
        file_cx = [s.complexity for s in file_syms if s.complexity > 0]
        file_avg_cx = sum(file_cx) / max(len(file_cx), 1)
        importers = len(graph.importers_of(target_file))
        lines.extend([
            "",
            f"  File: {target_file}",
            f"    Symbols: {len(file_syms)}, Avg complexity: {file_avg_cx:.1f}, Importers: {importers}",
        ])

    return "\n".join(lines)


def _score_naming_consistency(graph) -> int:
    """Score how consistent naming conventions are across the codebase."""
    from tempograph.types import SymbolKind
    funcs = [s.name for s in graph.symbols.values() if s.kind == SymbolKind.FUNCTION]
    if len(funcs) < 5:
        return 80  # too few to judge

    snake = sum(1 for n in funcs if "_" in n and n == n.lower())
    camel = sum(1 for n in funcs if n[0].islower() and any(c.isupper() for c in n[1:]))
    total = max(len(funcs), 1)

    # Dominant style should be >80% for good score
    dominant = max(snake, camel) / total
    return min(100, int(dominant * 120))
